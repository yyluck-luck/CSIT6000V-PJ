"""演示服务：标准库 http.server + SSE，零依赖。

刻意不引 FastAPI：在 TACC 的 CPU 作业里，能跑起来比框架漂亮重要。
接口与 proposal 图里的 "Request API (FastAPI): submit run · SSE stream" 等价：

    GET  /                   单页前端
    GET  /api/tasks          内置题库 + 自建题目 + 消融档位
    POST /api/run            {"task": "...", "ablate": "B2"} 提交一次运行
    GET  /api/stream?id=...  逐条推送 trace 事件（SSE；答疑也走这条）
    GET  /api/result?id=...  取最终 summary
    POST /api/probe          {"task": "...", "mode": "brief"} 记忆探针（单轮，同步返回）
    POST /api/upload         {"title","query","category","gold","files":[{name,data_b64}]}
    POST /api/custom/delete  {"task": "custom-..."}
    POST /api/chat           {"message","task"?,"session_id"?,"tools"?} 答疑/讲解
"""

from __future__ import annotations

import json
import hashlib
import os
import queue
import re
import threading
import urllib.parse
import urllib.request
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from flagpilot import config, custom, probe, tagger, storage, writeup
from flagpilot.agent import run_attempt
from flagpilot.chat import ChatSession, read_log
from flagpilot.config import (ABLATIONS, GEMINI, Budget, Policy, RunConfig,
                              SandboxLimits, gemini_api_key, get_ablation, is_gemini, make_model_spec)
from flagpilot.memory import MemoryStore
from flagpilot.tasks import Task, load_index, select
from flagpilot.trace import read_jsonl

from app import catalog, httpcache, lab, runs_index, tacc, tacc_live
from flagpilot.control import OPS, RunControl

STATIC = Path(__file__).resolve().parent / "index.html"
_STATIC = httpcache.StaticFile(STATIC)
_MODELS = httpcache.TTLCache(30.0)     # /api/models：30 s 内复用，换模型 / 端点即失效
# SSE 空闲多久发一次心跳。模型一轮可能想很久；经 SSH 隧道或代理时，
# 长时间没有字节的连接会被掐断，前端只能等 150 s 看门狗再补救。
SSE_PING_S = 15.0
PARTIAL_NOTIFY_S = 0.05     # 逐字输出最多 20 次/秒推给浏览器：再密肉眼看不出，只是白占 CPU
PARTIAL_MAX_CHARS = 6000    # 一轮的动作 JSON 不会这么长；超出部分不再推（整段结果仍走 model 事件）

STATE: dict[str, dict] = {}
CHATS: dict[str, ChatSession] = {}
LOCK = threading.Lock()
SETTINGS = {"model": "mock", "base_url": "", "track": "L", "ablation": "B3"}

# 会话 id 是 uuid4().hex[:12]，只允许这个形状。
# 以前 /api/chat/history?id=../.. 会直接拼进 runs/chat/<sid>/chat.jsonl，
# 变成一个"只能读 chat.jsonl"的目录穿越；顺手也能把沙箱工作目录指到外面。
_SID_RE = re.compile(r"^[0-9a-f]{12}$")
MAX_CONCURRENT_RUNS = 3          # 单进程同时跑几个 agent；防本地网页刷爆
MAX_STATE_ENTRIES = 80           # STATE 只增不减会吃内存，完成后按时间淘汰
RUNNING = 0


def _base_url() -> str:
    """当前端点。gemini-* 模型没给端点时走 Google 的 OpenAI 兼容端点（与 make_model_spec 同一条规则）。"""
    if SETTINGS["base_url"]:
        return SETTINGS["base_url"]
    return GEMINI["base_url"] if str(SETTINGS["model"]).lower().startswith("gemini") else "http://127.0.0.1:8000/v1"


def _api_key(base: str | None = None) -> str:
    """服务端用的密钥：FLAGPILOT_API_KEY；对 Gemini 端点没设时用 GEMINI_API_KEY（环境变量或根目录 .env）。"""
    key = os.environ.get("FLAGPILOT_API_KEY", "")
    if (not key or key == "EMPTY") and is_gemini(base or _base_url()):
        key = gemini_api_key()
    return key or "EMPTY"


def _base_for(model: str) -> str:
    """某一个模型的端点（答疑每个线程可以用不同模型）：gemini-* / gemma-* 走 Google，其余走配置的端点。"""
    if model.lower().startswith(("gemini-", "gemma-")):
        return SETTINGS["base_url"] if is_gemini(SETTINGS["base_url"] or "") else GEMINI["base_url"]
    return SETTINGS["base_url"] or "http://127.0.0.1:8000/v1"


# 线程里选的模型名（来自浏览器）：只认这些字符，挡住拼进 URL / 日志的怪东西
_MODEL_RE = re.compile(r"^[A-Za-z0-9][\w.:/@-]{0,99}$")
TACC_MODEL = "qwen3.8-27b (TACC)"


# Gemini 的模型表里还有语音、图片、实时音频、电脑操控、深度研究等专用模型，答疑和解题用不上：
# 只留 gemini- / gemma- 开头的，再去掉这些专用的
_NOT_CHAT = ("tts", "image", "audio", "live", "embedding", "transcribe", "computer-use", "robotics",
             "lyria", "banana", "imagen", "veo", "aqa", "omni", "customtools")


def list_models() -> list[str]:
    """向 OpenAI 兼容端点要模型列表（Ollama 的 /v1/models 支持）。

    取不到就只回当前模型——界面不该因为列不出模型就崩。
    """
    base = _base_url().rstrip("/")
    try:
        request = urllib.request.Request(base + "/models", headers={
            "Authorization": "Bearer " + _api_key(),
            "User-Agent": "FlagPilot/0.1.0",
        })
        with urllib.request.urlopen(request, timeout=5) as r:
            data = json.loads(r.read().decode())
        ids = [str(m.get("id")) for m in (data.get("data") or []) if m.get("id")]
        if is_gemini(base):          # Google 给的是 "models/gemini-…"；只留能对话的
            ids = [i.removeprefix("models/") for i in ids]
            ids = [i for i in ids if i.startswith(("gemini-", "gemma-")) and not any(k in i for k in _NOT_CHAT)]
        if ids:
            return sorted(set(ids) | {SETTINGS["model"]})
    except Exception:
        pass
    return [SETTINGS["model"]]


def resolve_task(name: str) -> Task | None:
    """先查内置题库，再查自建题目。

    两条来源分开存、统一取：内置题只读、参与评测；自建题可删、不参与评测。
    混在一个列表里会让"分母"变得说不清。
    """
    if not name:
        return None
    hit = select(load_index(), names=[name])
    if hit:
        return hit[0]
    return custom.get(str(name))


def _make_cfg(task_batch: str, ablate: str | None = None, model: str | None = None) -> RunConfig:
    """model 给了就用它（答疑线程各自的模型），否则用全局设置的模型。"""
    name = model or SETTINGS["model"]
    base = _base_for(name) if model else _base_url()
    abl = get_ablation(ablate or SETTINGS["ablation"])
    policy = Policy()
    if not abl.truncate:
        # 和 CLI 的 _build_cfg 保持同一口径：B0 必须真的不截断
        policy.truncate_head_chars = None
        policy.truncate_tail_chars = None
    return RunConfig(
        batch=task_batch,
        track=SETTINGS["track"],
        # 必须和 CLI 走同一个构造函数：之前这里直接 new ModelSpec，
        # 漏掉 soft_no_think，Qwen3 的思维链把每轮预算吃光，表现为"空回复"。
        model=make_model_spec(name, base,
                              api_key=_api_key(base),
                              metrics_url=SETTINGS.get("metrics_url") or ""),
        budget=Budget(max_turns=20),
        policy=policy,
        limits=SandboxLimits(),
        ablation=abl,
        replay_timeout_s=300.0,
        # 交互式默认开记忆：同题跑过几次之后，prompt 里会带上"上次怎么失败的"。
        # 批跑（cli/runner）仍然默认关，保证 E1 四档只差一个变量。
        memory=True,
    )


def _evict_state() -> None:
    """STATE 只增不减：每个 run 最多留 4000 条事件，长期开着会吃内存。
    只淘汰已完成的求解记录，不动正在跑的、也不动答疑会话。"""
    if len(STATE) <= MAX_STATE_ENTRIES:
        return
    done = [(v.get("t0", 0), k) for k, v in STATE.items()
            if v.get("summary") is not None and v.get("kind") != "chat"]
    for _, k in sorted(done)[:len(STATE) - MAX_STATE_ENTRIES]:
        STATE.pop(k, None)


def _load_run_from_disk(rid: str) -> dict | None:
    """服务重启后把已完成的 run 从 trace.jsonl 读回来。

    以前 `?run=<id>` 只在 STATE 里找，进程一重启就 404——但 trace 明明已经落盘。
    这里按 run_id 找 `runs/web/<id>/*/trace.jsonl`，把事件和 summary 还原成
    与内存态同形的 dict，前端不需要区分来源。
    """
    if not _SID_RE.match(rid):
        return None
    base = config.RUNS_ROOT / "web" / rid
    for p in sorted(base.glob("*/trace.jsonl")):
        evs = read_jsonl(p)
        if not evs:
            continue
        done = next((e for e in reversed(evs) if e.get("type") == "done"), None)
        summary = next((e for e in reversed(evs) if e.get("type") == "summary"), None)
        if done or summary:
            return {"q": queue.Queue(), "summary": {**(done or summary), "web_run_id": rid},
                    "events": evs, "kind": "disk"}
    d = lab.find_run(config.RUNS_ROOT, rid)          # Lab / TACC 实验里的一次 attempt
    evs = read_jsonl(d / "trace.jsonl") if d is not None else []
    summary = next((e for e in reversed(evs) if e.get("type") == "summary"), None)
    if summary:
        return {"q": queue.Queue(), "summary": summary, "events": evs, "kind": "replay"}
    return None


def _new_control(payload: dict) -> RunControl:
    """开跑时就带上单步：否则第一条命令可能抢在"打开单步"的请求之前执行。"""
    ctl = RunControl()
    if payload.get("step"):
        ctl.set("step", True)
    return ctl


def _wake(cond: threading.Condition) -> None:
    """summary 落定后再叫醒一次：读端可能正等在"没有新事件"上。"""
    with cond:
        cond.notify_all()


def _worker(run_id: str, task_name: str, ablate: str | None) -> None:
    global RUNNING
    q: queue.Queue = STATE[run_id]["q"]
    events: list = STATE[run_id]["events"]
    cond: threading.Condition = STATE[run_id].setdefault("cond", threading.Condition())

    def hook(ev: dict) -> None:
        """把事件追加进历史。

        历史是唯一数据源：SSE 流按游标读它，所以"断开重连""刷新页面""多个窗口
        同时看"都能拿到完整步骤，且不会重复。队列只留给答疑（那边没有历史）。
        """
        with cond:
            events.append(ev)
            if len(events) > 4000:      # 单次运行不可能这么多，兜底防内存涨
                del events[:1000]
            cond.notify_all()

    # 模型逐字输出：只放一份"本轮已生成的文本"给 SSE 读端，不进事件历史（回放 / trace 都不需要它）。
    # 写端按 PARTIAL_NOTIFY_S 节流唤醒；本轮的 model 事件一到，读端就不再发这一轮的 partial。
    partial = STATE[run_id].setdefault("partial", {"turn": 0, "text": "", "seq": 0, "t": 0.0})

    def on_delta(turn: int, chunk: str) -> None:
        with cond:
            if partial["turn"] != turn:
                partial["turn"], partial["text"] = turn, ""
            if len(partial["text"]) < PARTIAL_MAX_CHARS:
                partial["text"] += chunk
            partial["seq"] += 1
            now = time.monotonic()
            if now - partial["t"] >= PARTIAL_NOTIFY_S:
                partial["t"] = now
                cond.notify_all()

    try:
        task = resolve_task(task_name)
        if task is None:
            raise ValueError(f"Unknown challenge {task_name}")
        cfg = _make_cfg("web", ablate)
        # 每次运行一个独立目录：以前 out_dir 固定是 runs/web，run_id 又只是
        # "题目-s0"，同一题跑第二遍会把上一遍的 trace 覆盖掉。四档连跑更糟，
        # B0→B3 只剩最后一档的痕迹，事后想核对"当时到底调了什么"就没了。
        summary = run_attempt(task, cfg, config.RUNS_ROOT / "web" / run_id,
                              seed=0, hook=hook, on_delta=on_delta, control=STATE[run_id].get("control"))
        # 归因按需重算（不写回 runs.jsonl），界面上直接显示"为什么没解出来"
        t_tag = time.time()
        try:
            f = tagger.classify_run(summary.get("run_dir"), summary)
            f["label"] = tagger.LABELS.get(f.get("primary", ""), f.get("primary", ""))
            f["evidence_text"] = (f.get("evidence") or {}).get(f.get("primary", ""), "")
            summary["failure"] = f
        except Exception:
            summary["failure"] = {"primary": "unknown", "tags": [], "evidence": {}}
        # 端到端：从浏览器点下去（POST 到达）到 done 发出。和 wall_s 的差额
        # 就是"沙箱准备 + 归因 + 落盘"这些看不到的开销，界面分开显示。
        summary["web_run_id"] = run_id
        summary["tag_s"] = round(time.time() - t_tag, 2)
        summary["e2e_s"] = round(time.time() - STATE[run_id].get("t0", t_tag), 2)
        # 先发 done 再置 summary：读端以 events 为准，看到 done 就收尾，
        # 反过来的话读端可能先看到 summary、补发一条兜底 done，造成重复。
        hook({"type": "done", **summary})
        STATE[run_id]["summary"] = summary
        _wake(cond)
    except Exception as e:
        hook({"type": "error", "message": f"{type(e).__name__}: {e}",
              "trace": traceback.format_exc()[-1500:]})
        STATE[run_id]["summary"] = {"verdict": "error",
                                    "reason": f"{type(e).__name__}: {e}"}
        _wake(cond)
    finally:
        q.put(None)
        with LOCK:
            RUNNING = max(0, RUNNING - 1)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # 慢速连接（本机恶意进程/网页挂住 socket）不该无限占一个线程
    timeout = 30

    def log_message(self, fmt, *args):
        pass

    # ---------- 防护 ----------

    def _json_content_type(self) -> bool:
        """只收 application/json。

        这不是洁癖：浏览器跨站表单/sendBeacon 只能发 urlencoded / text / multipart，
        卡住 Content-Type 就卡住了"任意网页悄悄 POST 到 127.0.0.1:8080"的主路径。
        """
        ct = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        return ct == "application/json"

    def _origin_ok(self) -> bool:
        """有 Origin 就必须同源；绑定回环时还要求 Host 也是回环（防 DNS rebinding）。"""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        try:
            o = urllib.parse.urlsplit(origin)
            h = urllib.parse.urlsplit("//" + (self.headers.get("Host") or "")).hostname
        except ValueError:
            return False
        if not o.hostname or not h or o.hostname != h:
            return False
        if str(SETTINGS.get("host") or "127.0.0.1") in ("127.0.0.1", "localhost", "::1"):
            return h in ("127.0.0.1", "localhost", "::1")
        return True

    def _sid(self) -> str:
        """会话 id 必须是 12 位十六进制；否则一律当空处理，绝不拼进路径。"""
        s = self._qid()
        return s if _SID_RE.match(s) else ""

    # ---------- 写 ----------

    def _json(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        body, gz = httpcache.maybe_gzip(body, self.headers.get("Accept-Encoding"))
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if gz:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self) -> None:
        if not STATIC.exists():
            self._json(500, {"error": "index.html is missing"})
            return
        page = _STATIC.load()
        # no-cache = 每次都向服务端确认；没改动只回 304，不重传 80KB。
        if self.headers.get("If-None-Match") == page.etag:
            self.send_response(304)
            self.send_header("ETag", page.etag)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        gz = httpcache.accepts_gzip(self.headers.get("Accept-Encoding"))
        body = page.gz if gz else page.raw
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("ETag", page.etag)
        self.send_header("Vary", "Accept-Encoding")
        if gz:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            self._get()
        except storage.StorageError as exc:
            self._json(409, {"error": str(exc)})

    def _get(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._html()
        if path == "/api/writeup":
            return self._writeup()
        if path == "/api/tasks":
            tasks = [{"name": t.name, "category": t.category, "scope": t.scope,
                      "title": t.title[:90], "custom": False, "has_gold": t.has_gold}
                     for t in load_index()]
            mine = [{"name": t.name, "category": t.category, "scope": "custom",
                     "title": t.title[:90], "custom": True, "has_gold": t.has_gold,
                     "n_assets": len(t.assets)} for t in custom.load()]
            return self._json(200, {
                "tasks": tasks,
                "custom_tasks": mine,
                "model": SETTINGS["model"],
                "ablation": SETTINGS["ablation"],
                "ablations": [{"name": k, "desc": v.desc} for k, v in ABLATIONS.items()],
            })
        if path == "/api/models":
            key = (SETTINGS.get("base_url"), SETTINGS.get("model"),
                   httpcache.secret_tag(_api_key()))
            return self._json(200, {"models": _MODELS.get(key, list_models),
                                    "current": SETTINGS["model"]})
        if path == "/api/stream":
            return self._stream()
        if path == "/api/runs":
            return self._runs()
        if path == "/api/result":
            rid = self._qid()
            st = STATE.get(rid)
            if st is None:
                # 进程重启过：STATE 没了，但 trace 还在盘上
                st = _load_run_from_disk(rid)
                if st is not None:
                    with LOCK:
                        STATE[rid] = st
            if not st:
                return self._json(404, {"error": "unknown id"})
            # 带回 events：SSE 断了之后前端靠这个把中间步骤补回来，
            # 否则只能拿到结论、看不到"到底调了什么工具"。
            return self._json(200, {"summary": st.get("summary"),
                                    "events": st.get("events") or [],
                                    "done": bool(st.get("summary"))})
        if path == "/api/tacc":
            return self._json(200, tacc.status(config.RUNS_ROOT))
        if path == "/api/tacc/session":
            return self._json(200, tacc_live.status())
        if path == "/api/chat/sessions":
            return self._json(200, {"sessions": runs_index.list_chats(config.RUNS_ROOT / "chat")})
        if path == "/api/chat/history":
            raw = self._qid()
            if raw and not _SID_RE.match(raw):
                return self._json(400, {"error": "bad session id"})
            sid = raw
            p = config.RUNS_ROOT / "chat" / sid / "chat.jsonl"
            rows = read_log(p) if sid and p.exists() else []
            return self._json(200, {"session_id": sid,
                                    "messages": [r for r in rows
                                                 if r.get("type") in ("user", "reply", "error")],
                                    "model": runs_index.thread_model(rows),
                                    "exists": bool(rows)})
        if path.startswith("/api/catalog/"):
            return self._catalog_get(path)
        if path == "/api/lab/experiments":
            return self._json(200, {"experiments": lab.experiments(config.RUNS_ROOT)})
        if path == "/api/lab/experiment":
            exp = lab.experiment(config.RUNS_ROOT, self._qparam("id"))
            return self._json(200, exp) if exp else self._json(404, {"error": "unknown experiment"})
        if path == "/api/lab/export":
            exp = lab.experiment(config.RUNS_ROOT, self._qparam("id"))
            if not exp:
                return self._json(404, {"error": "unknown experiment"})
            data = lab.csv_rows(exp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="flagpilot-{exp["id"].replace("/", "-")}.csv"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/memory":
            task = urllib.parse.parse_qs(self.path.split("?", 1)[-1]).get("task", [""])[0]
            store = MemoryStore()
            if task:
                return self._json(200, {**store.stats(task),
                                        "digest": store.digest(task)})
            return self._json(200, store.stats())
        self._json(404, {"error": "not found"})

    def _writeup(self):
        rid = self._qid()
        if not _SID_RE.fullmatch(rid or ""):
            return self._json(400, {"error": "Invalid run ID"})
        root = (config.RUNS_ROOT / "web").resolve()
        base = (root / rid).resolve()
        if not base.is_relative_to(root):
            return self._json(404, {"error": "Run not found"})
        for trace in sorted(base.glob("*/trace.jsonl")):
            if not trace.resolve().is_relative_to(root):
                continue
            path = trace.parent / "writeup.md"
            if not path.exists() and not path.with_name(path.name + ".enc").exists():
                try:
                    writeup.generate(trace.parent)
                except ValueError as exc:
                    return self._json(409, {"error": str(exc)})
            data = storage.read_artifact(path)
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="flagpilot-{rid}.md"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self._json(404, {"error": "Run not found"})

    def do_POST(self):
        global RUNNING
        path = self.path.split("?")[0]
        routes = ("/api/run", "/api/probe", "/api/upload", "/api/chat",
                  "/api/custom/delete", "/api/settings", "/api/tacc/start", "/api/tacc/stop",
                  "/api/tacc/session/start", "/api/tacc/session/stop", "/api/lab/start", "/api/lab/stop", "/api/lab/retry", "/api/run/control",
                  "/api/catalog/import")
        if path not in routes:
            return self._json(404, {"error": "not found"})
        if not self._json_content_type():
            return self._json(415, {"error": "Content-Type must be application/json"})
        if not self._origin_ok():
            return self._json(403, {"error": "Cross-origin request refused"})
        n = int(self.headers.get("Content-Length") or 0)
        if n > 200 * 1024 * 1024:
            return self._json(413, {"error": "Request body too large"})
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "bad json"})

        if path == "/api/tacc/session/start":
            return self._json(200, tacc_live.start())
        if path == "/api/tacc/session/stop":
            return self._json(200, tacc_live.stop())
        if path == "/api/upload":
            return self._upload(payload)
        if path == "/api/chat" and payload.get("backend") == "tacc":
            return self._chat_tacc(payload)
        if path == "/api/chat":
            return self._chat(payload)
        if path == "/api/custom/delete":
            return self._delete_custom(payload)
        if path == "/api/settings":
            return self._settings(payload)
        if path == "/api/lab/start":
            return self._lab_start(payload)
        if path == "/api/run/control":
            return self._run_control(payload)
        if path == "/api/catalog/import":
            return self._catalog_import(payload)
        if path == "/api/lab/retry":
            try:
                r = lab.retry_infra(config.RUNS_ROOT, str(payload.get("id") or ""), resolve_task,
                                    make_cfg=lambda tier, model: _make_cfg("lab", ablate=tier, model=model))
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            return self._json(200, r)
        if path == "/api/lab/stop":
            m = lab.exp_dir(config.RUNS_ROOT, str(payload.get("id") or ""))
            return self._json(200, {"ok": bool(m) and lab.stop(m.name)})
        if path == "/api/tacc/start":
            r = tacc.start(str(payload.get("batch") or ""), str(payload.get("tiers") or ""),
                           payload.get("limit", 0), config.RUNS_ROOT, _mount_tacc,
                           exclude=str(payload.get("exclude") or ""), tasks=str(payload.get("tasks") or ""))
            return self._json(400 if "error" in r else 200, r)
        if path == "/api/tacc/stop":
            r = tacc.stop()
            return self._json(400 if "error" in r else 200, r)

        task = payload.get("task")
        if not task:
            return self._json(400, {"error": "task required"})

        if path == "/api/probe":
            return self._probe(task, str(payload.get("mode") or "brief"))

        ablate = payload.get("ablate")
        if ablate and str(ablate).upper() not in ABLATIONS:
            return self._json(400, {"error": f"Unknown tier {ablate}"})
        rid = uuid.uuid4().hex[:12]
        with LOCK:
            if RUNNING >= MAX_CONCURRENT_RUNS:
                return self._json(429, {"error": f"{RUNNING} run(s) already in progress; try again shortly"})
            _evict_state()
            RUNNING += 1
            STATE[rid] = {"q": queue.Queue(), "summary": None, "task": task,
                          "events": [],          # 供断线重连回放
                          "cond": threading.Condition(),   # 新事件到达即唤醒 SSE
                          "partial": {"turn": 0, "text": "", "seq": 0, "t": 0.0},  # 本轮逐字输出
                          "t0": time.time(),     # 端到端计时起点（收到 POST 那一刻）
                          # 单步 / 暂停 / 插提示：只有本机运行能在命令之间停下来，TACC 作业做不到
                          "control": _new_control(payload) if payload.get("backend") != "tacc" else None,
                          "ablate": ablate or SETTINGS["ablation"]}
        try:
            target = _tacc_worker if payload.get("backend") == "tacc" else _worker
            threading.Thread(target=target, args=(rid, task, ablate), daemon=True).start()
        except Exception:
            with LOCK:
                RUNNING = max(0, RUNNING - 1)
            raise
        self._json(200, {"run_id": rid, "ablation": ablate or SETTINGS["ablation"]})

    # ---------- 运行时设置 ----------

    def _catalog_get(self, path: str) -> None:
        """在线题库：比赛列表、某个比赛的题、搜索、预览。网络或额度问题以 502 + 可读原因返回。"""
        try:
            if path == "/api/catalog/ctfs":
                return self._json(200, {"ctfs": [c["name"] for c in catalog.ctfs()], "token": bool(catalog._token())})
            if path == "/api/catalog/challenges":
                return self._json(200, {"rows": catalog.challenges(self._qparam("ctf"))})
            if path == "/api/catalog/search":
                return self._json(200, catalog.search(self._qparam("q")[:200]))
            if path == "/api/catalog/preview":
                return self._json(200, catalog.preview(self._qparam("path")[:600]))
        except catalog.CatalogError as e:
            return self._json(502, {"error": str(e)})
        return self._json(404, {"error": "not found"})

    def _catalog_import(self, payload: dict) -> None:
        try:
            if payload.get("path"):
                r = catalog.import_archive(str(payload["path"])[:600])
            elif payload.get("url"):
                r = catalog.import_url(str(payload["url"])[:2000])
            else:
                return self._json(400, {"error": "path or url required"})
        except catalog.CatalogError as e:
            return self._json(502, {"error": str(e)})
        except ValueError as e:              # custom.create 的输入校验
            return self._json(400, {"error": str(e)})
        t = r["task"]
        return self._json(200, {**r, "task": {"name": t.name, "title": t.title, "category": t.category,
                                              "has_gold": t.has_gold, "n_assets": len(t.assets)}})

    def _run_control(self, payload: dict) -> None:
        """看着一次本机运行的人：单步、暂停 / 继续、批准 / 修改 / 跳过当前命令、停止、插提示。"""
        rid, op = str(payload.get("id") or ""), str(payload.get("op") or "")
        st = STATE.get(rid) if _SID_RE.match(rid) else None
        ctl = st.get("control") if st else None
        if ctl is None or st.get("summary"):
            return self._json(404, {"error": "No run in progress with that id"})
        if op not in OPS:
            return self._json(400, {"error": f"Unknown control {op!r}"})
        turn = payload.get("turn")
        ok = ctl.set(op, payload.get("value"), turn=int(turn) if isinstance(turn, (int, float)) else None)
        return self._json(200 if ok else 409, {"ok": ok, **ctl.state()})

    def _qparam(self, name: str) -> str:
        return urllib.parse.parse_qs(self.path.split("?", 1)[-1]).get(name, [""])[0]

    def _lab_start(self, payload: dict) -> None:
        """Lab 的对比 / 多题实验：题 × 组（模型 × 档位）× 重复次数，后台并发跑。"""
        names = [str(x) for x in (payload.get("tasks") or [])]
        if not 1 <= len(names) <= lab.MAX_TASKS:
            return self._json(400, {"error": f"Pick 1 to {lab.MAX_TASKS} challenges"})
        tasks = []
        for n in dict.fromkeys(names):
            t = resolve_task(n)
            if t is None:
                return self._json(400, {"error": f"Unknown challenge {n}"})
            tasks.append(t)
        arms, seen = [], set()
        for a in payload.get("arms") or []:
            tier, model = str((a or {}).get("tier") or "").upper(), str((a or {}).get("model") or "").strip()
            if tier not in ABLATIONS:
                return self._json(400, {"error": f"Unknown tier {tier}"})
            if model and not _MODEL_RE.match(model):
                return self._json(400, {"error": "bad model name"})
            if (model, tier) not in seen:
                seen.add((model, tier))
                arms.append({"model": model, "tier": tier})
        if not 1 <= len(arms) <= lab.MAX_ARMS:
            return self._json(400, {"error": f"Pick 1 to {lab.MAX_ARMS} arms"})
        try:
            seeds = max(1, min(lab.MAX_SEEDS, int(payload.get("seeds") or 1)))
            workers = max(1, min(4, int(payload.get("workers") or 2)))
        except (TypeError, ValueError):
            return self._json(400, {"error": "seeds and workers must be numbers"})
        if len(tasks) * len(arms) * seeds > lab.MAX_JOBS:
            return self._json(400, {"error": f"At most {lab.MAX_JOBS} attempts per experiment"})
        r = lab.start(config.RUNS_ROOT, tasks, arms, seeds, workers,
                      make_cfg=lambda tier, model: _make_cfg("lab", ablate=tier, model=model),
                      label=str(payload.get("label") or "")[:120])
        return self._json(200, r)

    def _settings(self, payload: dict) -> None:
        """只允许改模型。改完清掉旧会话——它们还挂着上一个模型的客户端。"""
        model = str(payload.get("model") or "").strip()
        if not model:
            return self._json(400, {"error": "model required"})
        with LOCK:
            SETTINGS["model"] = model
            CHATS.clear()
        _MODELS.clear()
        return self._json(200, {"model": model,
                                "note": "Applies to new runs and chats; runs in progress are unaffected"})

    # ---------- 自建题目 ----------

    def _upload(self, payload: dict) -> None:
        """上传题目。文件用 base64 放在 JSON 里——避免自己写 multipart 解析。

        标准库 3.13 已经删掉 cgi 模块，手写 multipart 解析是纯粹的风险源；
        而前端本来就能读到 File，转一次 base64 成本可以忽略。
        """
        import base64 as _b64

        files: list[tuple[str, bytes]] = []
        total_decoded = 0
        try:
            for item in payload.get("files") or []:
                name = str(item.get("name") or "upload.bin")
                raw = item.get("data_b64") or ""
                if not isinstance(raw, str):
                    raise ValueError("data_b64 must be a string")
                if len(raw) > custom.MAX_FILE_BYTES * 2:
                    return self._json(413, {"error": f"{name} exceeds the per-file limit"})
                data = _b64.b64decode(raw, validate=False)
                total_decoded += len(data)
                # 先按解码后的真实字节卡总量，别等 20 个 32MB 全进内存才在 create 里拒绝
                if total_decoded > custom.MAX_TOTAL_BYTES:
                    return self._json(413, {"error": "Attachments exceed the total size limit"})
                files.append((name, data))
            task = custom.create(
                title=str(payload.get("title") or ""),
                query=str(payload.get("query") or ""),
                category=str(payload.get("category") or "custom"),
                gold=str(payload.get("gold") or ""),
                files=files,
            )
        except (ValueError, OSError) as e:
            return self._json(400, {"error": f"{type(e).__name__}: {e}"})
        return self._json(200, {
            "task": {"name": task.name, "title": task.title, "category": task.category,
                     "has_gold": task.has_gold, "n_assets": len(task.assets),
                     "custom": True},
            "warning": "" if task.has_gold else
                       "No gold flag: the best possible verdict is reproduced (the replay can run, but correctness cannot be judged).",
        })

    def _delete_custom(self, payload: dict) -> None:
        name = str(payload.get("task") or "")
        if not name.startswith("custom-"):
            return self._json(400, {"error": "Only uploaded challenges can be deleted"})
        ok = custom.delete(name)
        return self._json(200 if ok else 404, {"deleted": ok})

    # ---------- 答疑 ----------

    def _chat_tacc(self, payload: dict) -> None:
        """答疑一轮交给 TACC 上常驻的 Qwen：本机只记会话日志（左侧线程照常可用），回答由作业打回。"""
        message = str(payload.get("message") or "").strip()
        if not message:
            return self._json(400, {"error": "message required"})
        sid = str(payload.get("session_id") or "") or uuid.uuid4().hex[:12]
        if not _SID_RE.match(sid):
            return self._json(400, {"error": "bad session id"})
        task_name, use_tools = str(payload.get("task") or ""), bool(payload.get("tools", True))
        path = config.RUNS_ROOT / "chat" / sid / "chat.jsonl"
        rows = read_log(path) if path.exists() else []
        history, pending = [], None
        for r in rows:
            if r.get("type") == "user":
                pending = r.get("text") or ""
            elif r.get("type") == "reply" and pending:
                history.append({"user": pending, "answer": r.get("answer") or ""})
                pending = None
        q: queue.Queue = queue.Queue()
        with LOCK:
            STATE[sid] = {"q": q, "summary": None, "kind": "chat"}

        def work() -> None:
            stamp = lambda: time.strftime("%Y-%m-%dT%H:%M:%S")      # noqa: E731
            path.parent.mkdir(parents=True, exist_ok=True)
            if not rows:
                storage.append_record(path, {"ts": stamp(), "type": "meta", "task": task_name,
                                             "model": TACC_MODEL, "tools": use_tools})
            turn = sum(1 for r in rows if r.get("type") == "user") + 1
            storage.append_record(path, {"ts": stamp(), "type": "user", "turn": turn, "text": message[:4000]})
            rep = tacc_live.chat(message, history, task_name, use_tools,
                                 on_delta=lambda t: q.put({"type": "chat_delta", "text": t}),
                                 on_tool=lambda ev: q.put(ev if ev.get("type") == "chat_check" else {**ev, "type": "chat_tool"}),
                                 on_stage=lambda ev: q.put({**ev, "type": "chat_stage"}))
            if rep.get("error") and not rep.get("answer"):
                # 失败也落盘：重开线程时能看到这条消息为什么没有回复（不参与上面的问答配对）
                storage.append_record(path, {"ts": stamp(), "type": "error", "turn": turn, "error": str(rep["error"])[:400]})
                q.put({"type": "error", "message": rep["error"]})
            else:
                steps = rep.get("steps") or []
                storage.append_record(path, {"ts": stamp(), "type": "reply", "turn": turn,
                                             "answer": (rep.get("answer") or "")[:4000], "rounds": rep.get("rounds", 0),
                                             "steps": [[str(x.get("cmd", ""))[:200], x.get("exit_code"), bool(x.get("cached"))] for x in steps],
                                             "prompt_tokens": rep.get("prompt_tokens", 0), "completion_tokens": rep.get("completion_tokens", 0),
                                             "wall_s": rep.get("wall_s", 0), "error": rep.get("error", ""), "model": TACC_MODEL,
                                             "flag_checks": [[str(c.get("flag", ""))[:120], c.get("status")] for c in rep.get("flag_checks") or []]})
                q.put({"type": "chat_done", "answer": rep.get("answer") or "", "turns": turn, "session_id": sid,
                       "total_steps": len(steps), "flag_checks": rep.get("flag_checks") or [], "model": TACC_MODEL})
            q.put(None)

        threading.Thread(target=work, daemon=True).start()
        return self._json(200, {"session_id": sid, "backend": "tacc"})

    def _chat(self, payload: dict) -> None:
        message = str(payload.get("message") or "").strip()
        if not message:
            return self._json(400, {"error": "message required"})
        task_name = str(payload.get("task") or "")
        sid = str(payload.get("session_id") or "")
        if sid and not _SID_RE.match(sid):
            return self._json(400, {"error": "bad session id"})
        use_tools = bool(payload.get("tools", True))
        # 每个线程可以用自己的模型：浏览器把这个线程选的模型带过来；不带就用全局设置
        want = str(payload.get("model") or "").strip() or None
        if want and not _MODEL_RE.match(want):
            return self._json(400, {"error": "bad model name"})

        with LOCK:
            sess = CHATS.get(sid)
            # 换了题目就得换会话：沙箱里挂的附件不一样，历史也会串题
            if sess is not None and task_name and (
                    sess.task is None or sess.task.name != task_name):
                sess = None
            # 内存里没有，但磁盘上可能还有：服务重启过、或换了进程。
            # 这一步是"答疑会话不丢"的关键——之前 CHATS 只活在内存里，
            # 我热更新一次代码，用户的对话就全没了。
            if sess is None and sid:
                try:
                    prev = resolve_task(task_name) if task_name else None
                    sess = ChatSession.resume(
                        _make_cfg("web-chat", model=want), prev,
                        config.RUNS_ROOT / "chat" / sid, use_tools=use_tools)
                except Exception:
                    sess = None
                if sess is not None and task_name and (
                        sess.task is None or sess.task.name != task_name):
                    sess = None      # 盘上那份是另一道题的，别混着聊
                if sess is not None:
                    CHATS[sid] = sess
                    STATE[sid] = {"q": queue.Queue(), "summary": None, "kind": "chat"}
            if sess is None:
                try:
                    task = resolve_task(task_name) if task_name else None
                except Exception as e:
                    return self._json(500, {"error": f"{type(e).__name__}: {e}"})
                sid = uuid.uuid4().hex[:12]
                try:
                    sess = ChatSession(
                        _make_cfg("web-chat", model=want),
                        task=task,
                        workdir=config.RUNS_ROOT / "chat" / sid,
                        use_tools=use_tools,
                    )
                except Exception as e:
                    return self._json(500, {"error": f"{type(e).__name__}: {e}"})
                CHATS[sid] = sess
                STATE[sid] = {"q": queue.Queue(), "summary": None, "kind": "chat"}
            if want and sess.cfg.model.name != want:
                sess.set_model(_make_cfg("web-chat", model=want).model)     # 同一线程中途换模型
            q = STATE[sid]["q"]

        def worker() -> None:
            try:
                reply = sess.ask(
                    message,
                    on_delta=lambda t: q.put({"type": "chat_delta", "text": t}),
                    hook=q.put,
                )
                q.put({"type": "chat_done", **reply.as_dict(),
                       "session_id": sid, "turns": sess.turns, "model": sess.cfg.model.name,
                       "total_steps": sess.total_steps,
                       "task": sess.task.name if sess.task else ""})
            except Exception as e:
                sess.log_error(f"{type(e).__name__}: {e}")
                q.put({"type": "error", "message": f"{type(e).__name__}: {e}",
                       "trace": traceback.format_exc()[-1200:]})
            finally:
                q.put(None)

        threading.Thread(target=worker, daemon=True).start()
        return self._json(200, {"session_id": sid})

    def _probe(self, task_name: str, mode: str) -> None:
        """记忆探针：单轮、无工具，同步返回（一次调用，没必要走 SSE）。"""
        try:
            task = resolve_task(task_name)
            if task is None:
                return self._json(404, {"error": f"Unknown challenge {task_name}"})
            cfg = _make_cfg("web-probe")
            r = probe.run_probe(task, cfg, mode=mode)
            return self._json(200, r.as_dict())
        except Exception as e:
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})

    # ---------- SSE ----------

    def _qid(self) -> str:
        if "?" not in self.path:
            return ""
        for kv in self.path.split("?", 1)[1].split("&"):
            if kv.startswith("id="):
                return kv[3:]
        return ""

    def _runs(self) -> None:
        """历史页：内存里的运行（在跑的、--replay 挂载的）优先，其余从 runs/web 读摘要。"""
        with LOCK:
            mem = {rid: st for rid, st in STATE.items()
                   if "events" in st and st.get("kind") != "chat"}
        items = [runs_index.item_from_state(rid, st) for rid, st in mem.items()]
        items += [x for x in runs_index.list_runs(config.RUNS_ROOT) if x["id"] not in mem]
        items.sort(key=lambda x: x.get("ts") or 0, reverse=True)
        self._json(200, {"runs": items[:300]})

    def _stream(self) -> None:
        rid = self._qid()
        st = STATE.get(rid)
        if st is None:
            st = _load_run_from_disk(rid)
            if st is not None:
                with LOCK:
                    STATE[rid] = st
        if not st:
            return self._json(404, {"error": "unknown id"})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.wfile.flush()

        def send(ev: dict) -> bool:
            try:
                self.wfile.write(b"data: " + json.dumps(ev, ensure_ascii=False, default=str).encode() + b"\n\n")
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError):
                return False

        # ---- 求解：按历史列表游标读，重连/刷新都不会丢也不会重复 ----
        if "events" in st:
            i = 0
            cond = st.get("cond")
            partial = st.get("partial")
            sent_seq, model_turn = -1, 0
            deadline = time.monotonic() + 1800
            while time.monotonic() < deadline:
                evs = st["events"]
                while i < len(evs):
                    ev = evs[i]
                    i += 1
                    if ev.get("type") == "model":
                        model_turn = max(model_turn, int(ev.get("turn") or 0))
                    if not send(ev):
                        return
                    if ev.get("type") == "done":
                        return
                if st.get("summary"):
                    # 兜底：worker 异常结束时 summary 有值但 done 没进列表；回放与从盘上读回的 run 也走这里。
                    # type 放在最后：盘上的 summary 事件自带 "type": "summary"，放前面会被它盖掉。
                    send({**st["summary"], "type": "done"})
                    return
                if partial is not None and cond is not None:
                    with cond:
                        seq, turn, text = partial["seq"], partial["turn"], partial["text"]
                    # 同一份内容只看一次；只推"还没出 model 事件"的那一轮
                    if seq != sent_seq:
                        sent_seq = seq
                        if turn > model_turn and text and \
                                not send({"type": "model_partial", "turn": turn, "text": text}):
                            return
                if cond is None:
                    time.sleep(0.15)
                    continue
                # 以前固定睡 150 ms 再看，每个事件平均晚 75 ms 才推出去。
                # 现在在锁里复查再等：写端 append 后 notify，读端立即醒，不会漏唤醒。
                with cond:
                    idle = (i >= len(st["events"]) and not st.get("summary")
                            and (partial is None or partial["seq"] == sent_seq))
                    woke = cond.wait(timeout=SSE_PING_S) if idle else True
                if not woke and i >= len(st["events"]):
                    try:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return
            return

        # ---- 答疑：没有历史可回放，仍然走队列 ----
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            try:
                ev = st["q"].get(timeout=1.0)
            except queue.Empty:
                try:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break
                continue
            if ev is None:
                break
            if not send(ev):
                break


def _mount_replay(root: str) -> list[tuple[str, str, str]]:
    """把目录下已完成的 run 挂进 STATE，返回 [(id, 名字, 结论)]。

    只读回放：这些条目没有可写的 q，也不会被 _worker 碰到。前端走
    `/api/result?id=` 时与实时 run 完全同一条路径（events + summary 都在），
    所以界面不需要区分"真跑"和"回放"。

    存在的理由：给助教/评审复现时，不该要求他们先有集群访问、先起 vLLM。
    把 runs/ 拷过来、一条命令挂上去，就能在浏览器里点开每一次真实运行。
    """
    base = Path(root).expanduser()
    if not base.exists():
        print(f"⚠ 回放目录不存在：{base}", flush=True)
        return []
    # 一层（<batch>/<run>/trace.jsonl）或两层（<root>/<batch>/<run>/trace.jsonl，例如 runs/tacc 下按档位分的批次）
    dirs = [base] if (base / "trace.jsonl").exists() else \
        sorted({p.parent for pat in ("*/trace.jsonl", "*/*/trace.jsonl", "*/*/*/trace.jsonl") for p in base.glob(pat)})
    mounted: list[tuple[str, str, str]] = []
    for d in dirs:
        evs = read_jsonl(d / "trace.jsonl")
        if not evs:
            continue
        summary = next((e for e in reversed(evs) if e.get("type") == "summary"), None)
        if not summary:
            # 没跑完的 attempt 没有 summary，挂上去只会显示"进行中"，跳过
            continue
        # id 必须是 12 位十六进制（与 _SID_RE 一致），否则前端的参数校验会拒掉
        rid = hashlib.sha1(str(d.resolve()).encode()).hexdigest()[:12]
        STATE[rid] = {"q": queue.Queue(), "summary": summary,
                      "events": evs, "kind": "replay"}
        mounted.append((rid, d.name, f"{summary.get('verdict')}/{summary.get('reason')}"))
    return mounted


def _tacc_worker(run_id: str, task_name: str, ablate: str | None) -> None:
    """单题交给 TACC 上常驻的 Qwen：作业日志里的 @@ev 事件原样推进 SSE，跑完下载运行目录并挂成回放。"""
    global RUNNING
    st = STATE[run_id]
    cond, events = st["cond"], st["events"]
    tier = str(ablate or SETTINGS["ablation"] or "B3").upper()
    summary: dict = {}
    failed: list = []

    def push(ev: dict) -> None:
        with cond:
            events.append(ev)
            cond.notify_all()

    def on_event(ev: dict) -> None:
        if ev.get("type") == "summary":
            summary.update(ev)
        elif ev.get("type") == "error":
            failed.append(str(ev.get("message") or ""))
        push(ev)

    try:
        d = tacc_live.run_task(task_name, tier, config.RUNS_ROOT, on_event)
        res = dict(summary) or {"verdict": "error", "reason": (failed[-1] if failed else "") or "the TACC job returned no result",
                                "turns": 0}
        res.pop("type", None)
        done = {"type": "done", **res, "ablation": tier, "model": "qwen3.8-27b (TACC)", "task": task_name,
                "e2e_s": round(time.time() - st.get("t0", time.time()), 2)}
        if d is not None and d.exists():
            _mount_replay(str(d))
        if not any(e.get("type") == "error" for e in events[-3:]) or summary:
            push(done)
        st["summary"] = done
        _wake(cond)
    except Exception as e:
        push({"type": "error", "message": f"{type(e).__name__}: {e}"})
        st["summary"] = {"verdict": "error", "reason": f"{type(e).__name__}: {e}"}
        _wake(cond)
    finally:
        st["q"].put(None)
        with LOCK:
            RUNNING = max(0, RUNNING - 1)


def _mount_tacc(out: Path) -> int:
    """TACC 流水线跑完：把每一档（<batch>-B0..B3）挂成只读回放，Runs 页立刻能逐步复盘。"""
    return sum(len(_mount_replay(str(d))) for d in sorted(Path(out).glob("*-B[0-3]")))


def serve(host: str = "127.0.0.1", port: int = 8080, model: str = "mock",
          base_url: str = "", track: str = "L", metrics_url: str = "",
          replay_dir: str = "") -> None:
    SETTINGS.update({"model": model,
                     "base_url": base_url or os.environ.get("FLAGPILOT_BASE_URL", ""),
                     "track": track,
                     "host": host,
                     # TACC 的 env.sh 已经导出了 FP_METRICS_URL；没有显式参数时用它，
                     # 这样 vLLM 上的 prefix cache 在网页里也能看到真实数字。
                     "metrics_url": metrics_url or os.environ.get("FP_METRICS_URL", "")})
    # 运行产物里有 flag 明文和用户上传的附件。默认 umask 022 会让同机其他用户
    # 读到这些文件；web demo 是单用户场景，直接收紧到 600/700。
    # （只改这个进程，CLI 批跑在共享集群上的组权限不受影响。）
    os.umask(0o077)
    if host not in ("127.0.0.1", "localhost", "::1"):
        # 没有账号体系，绑到 0.0.0.0 等于把"提交 run / <run> 执行命令"开放给同网段。
        print(f"⚠ 监听 {host} 且无鉴权：仅限可信内网，勿暴露到公网", flush=True)
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"FlagPilot demo → http://{host}:{port}   model={model}", flush=True)
    # 重启前若有 Qwen 会话还在 TACC 上跑，接回来（不然它白占 GPU 和个人名额到 1 小时）
    state = config.RUNS_ROOT / "tacc" / "session.json"
    tacc_live.STATE_FILE = state
    threading.Thread(target=tacc_live.restore, args=(state,), daemon=True).start()
    if replay_dir:
        mounted = _mount_replay(replay_dir)
        print(f"回放模式：挂载 {len(mounted)} 个已完成的 run（只读，不需要模型）", flush=True)
        for rid, name, verdict in mounted:
            print(f"  {name:<30} http://{host}:{port}/?run={rid}   {verdict}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        srv.server_close()
