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
from flagpilot.config import (ABLATIONS, Budget, Policy, RunConfig,
                              SandboxLimits, get_ablation, make_model_spec)
from flagpilot.memory import MemoryStore
from flagpilot.tasks import Task, load_index, select
from flagpilot.trace import read_jsonl

STATIC = Path(__file__).resolve().parent / "index.html"

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


def list_models() -> list[str]:
    """向 OpenAI 兼容端点要模型列表（Ollama 的 /v1/models 支持）。

    取不到就只回当前模型——界面不该因为列不出模型就崩。
    """
    base = (SETTINGS["base_url"] or "http://127.0.0.1:8000/v1").rstrip("/")
    try:
        request = urllib.request.Request(base + "/models", headers={
            "Authorization": "Bearer " + os.environ.get("FLAGPILOT_API_KEY", "EMPTY"),
            "User-Agent": "FlagPilot/0.1.0",
        })
        with urllib.request.urlopen(request, timeout=5) as r:
            data = json.loads(r.read().decode())
        ids = [str(m.get("id")) for m in (data.get("data") or []) if m.get("id")]
        if ids:
            return sorted(set(ids))
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


def _make_cfg(task_batch: str, ablate: str | None = None) -> RunConfig:
    base = SETTINGS["base_url"] or "http://127.0.0.1:8000/v1"
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
        model=make_model_spec(SETTINGS["model"], base,
                              api_key=os.environ.get("FLAGPILOT_API_KEY", "EMPTY"),
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
    return None


def _worker(run_id: str, task_name: str, ablate: str | None) -> None:
    global RUNNING
    q: queue.Queue = STATE[run_id]["q"]
    events: list = STATE[run_id]["events"]

    def hook(ev: dict) -> None:
        """把事件追加进历史。

        历史是唯一数据源：SSE 流按游标读它，所以"断开重连""刷新页面""多个窗口
        同时看"都能拿到完整步骤，且不会重复。队列只留给答疑（那边没有历史）。
        """
        events.append(ev)
        if len(events) > 4000:      # 单次运行不可能这么多，兜底防内存涨
            del events[:1000]

    try:
        task = resolve_task(task_name)
        if task is None:
            raise ValueError(f"未知题目 {task_name}")
        cfg = _make_cfg("web", ablate)
        # 每次运行一个独立目录：以前 out_dir 固定是 runs/web，run_id 又只是
        # "题目-s0"，同一题跑第二遍会把上一遍的 trace 覆盖掉。四档连跑更糟，
        # B0→B3 只剩最后一档的痕迹，事后想核对"当时到底调了什么"就没了。
        summary = run_attempt(task, cfg, config.RUNS_ROOT / "web" / run_id,
                              seed=0, hook=hook)
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
    except Exception as e:
        hook({"type": "error", "message": f"{type(e).__name__}: {e}",
              "trace": traceback.format_exc()[-1500:]})
        STATE[run_id]["summary"] = {"verdict": "error",
                                    "reason": f"{type(e).__name__}: {e}"}
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
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self) -> None:
        if not STATIC.exists():
            self._json(500, {"error": "index.html 缺失"})
            return
        body = STATIC.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
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
            return self._json(200, {"models": list_models(), "current": SETTINGS["model"]})
        if path == "/api/stream":
            return self._stream()
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
        if path == "/api/chat/history":
            raw = self._qid()
            if raw and not _SID_RE.match(raw):
                return self._json(400, {"error": "bad session id"})
            sid = raw
            p = config.RUNS_ROOT / "chat" / sid / "chat.jsonl"
            rows = read_log(p) if sid and p.exists() else []
            return self._json(200, {"session_id": sid,
                                    "messages": [r for r in rows
                                                 if r.get("type") in ("user", "reply")],
                                    "exists": bool(rows)})
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
                  "/api/custom/delete", "/api/settings")
        if path not in routes:
            return self._json(404, {"error": "not found"})
        if not self._json_content_type():
            return self._json(415, {"error": "Content-Type 必须是 application/json"})
        if not self._origin_ok():
            return self._json(403, {"error": "跨源请求被拒绝"})
        n = int(self.headers.get("Content-Length") or 0)
        if n > 200 * 1024 * 1024:
            return self._json(413, {"error": "请求体过大"})
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "bad json"})

        if path == "/api/upload":
            return self._upload(payload)
        if path == "/api/chat":
            return self._chat(payload)
        if path == "/api/custom/delete":
            return self._delete_custom(payload)
        if path == "/api/settings":
            return self._settings(payload)

        task = payload.get("task")
        if not task:
            return self._json(400, {"error": "task required"})

        if path == "/api/probe":
            return self._probe(task, str(payload.get("mode") or "brief"))

        ablate = payload.get("ablate")
        if ablate and str(ablate).upper() not in ABLATIONS:
            return self._json(400, {"error": f"未知档位 {ablate}"})
        rid = uuid.uuid4().hex[:12]
        with LOCK:
            if RUNNING >= MAX_CONCURRENT_RUNS:
                return self._json(429, {"error": f"已有 {RUNNING} 个 run 在跑，稍后再试"})
            _evict_state()
            RUNNING += 1
            STATE[rid] = {"q": queue.Queue(), "summary": None, "task": task,
                          "events": [],          # 供断线重连回放
                          "t0": time.time(),     # 端到端计时起点（收到 POST 那一刻）
                          "ablate": ablate or SETTINGS["ablation"]}
        try:
            threading.Thread(target=_worker, args=(rid, task, ablate), daemon=True).start()
        except Exception:
            with LOCK:
                RUNNING = max(0, RUNNING - 1)
            raise
        self._json(200, {"run_id": rid, "ablation": ablate or SETTINGS["ablation"]})

    # ---------- 运行时设置 ----------

    def _settings(self, payload: dict) -> None:
        """只允许改模型。改完清掉旧会话——它们还挂着上一个模型的客户端。"""
        model = str(payload.get("model") or "").strip()
        if not model:
            return self._json(400, {"error": "model required"})
        with LOCK:
            SETTINGS["model"] = model
            CHATS.clear()
        return self._json(200, {"model": model,
                                "note": "新运行与新会话生效；已开始的运行不受影响"})

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
                    raise ValueError("data_b64 必须是字符串")
                if len(raw) > custom.MAX_FILE_BYTES * 2:
                    return self._json(413, {"error": f"{name} 超过单文件上限"})
                data = _b64.b64decode(raw, validate=False)
                total_decoded += len(data)
                # 先按解码后的真实字节卡总量，别等 20 个 32MB 全进内存才在 create 里拒绝
                if total_decoded > custom.MAX_TOTAL_BYTES:
                    return self._json(413, {"error": "附件合计超过上限"})
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
                       "没有填标准答案：结果最高只能判 reproduced（重放成功但不能判定对错）",
        })

    def _delete_custom(self, payload: dict) -> None:
        name = str(payload.get("task") or "")
        if not name.startswith("custom-"):
            return self._json(400, {"error": "只能删除自建题目"})
        ok = custom.delete(name)
        return self._json(200 if ok else 404, {"deleted": ok})

    # ---------- 答疑 ----------

    def _chat(self, payload: dict) -> None:
        message = str(payload.get("message") or "").strip()
        if not message:
            return self._json(400, {"error": "message required"})
        task_name = str(payload.get("task") or "")
        sid = str(payload.get("session_id") or "")
        if sid and not _SID_RE.match(sid):
            return self._json(400, {"error": "bad session id"})
        use_tools = bool(payload.get("tools", True))

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
                        _make_cfg("web-chat"), prev,
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
                        _make_cfg("web-chat"),
                        task=task,
                        workdir=config.RUNS_ROOT / "chat" / sid,
                        use_tools=use_tools,
                    )
                except Exception as e:
                    return self._json(500, {"error": f"{type(e).__name__}: {e}"})
                CHATS[sid] = sess
                STATE[sid] = {"q": queue.Queue(), "summary": None, "kind": "chat"}
            q = STATE[sid]["q"]

        def worker() -> None:
            try:
                reply = sess.ask(
                    message,
                    on_delta=lambda t: q.put({"type": "chat_delta", "text": t}),
                    hook=q.put,
                )
                q.put({"type": "chat_done", **reply.as_dict(),
                       "session_id": sid, "turns": sess.turns,
                       "total_steps": sess.total_steps,
                       "task": sess.task.name if sess.task else ""})
            except Exception as e:
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
                return self._json(404, {"error": f"未知题目 {task_name}"})
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
            deadline = time.monotonic() + 1800
            while time.monotonic() < deadline:
                evs = st["events"]
                while i < len(evs):
                    ev = evs[i]
                    i += 1
                    if not send(ev):
                        return
                    if ev.get("type") == "done":
                        return
                if st.get("summary"):
                    # 兜底：worker 异常结束时 summary 有值但 done 没进列表
                    send({"type": "done", **st["summary"]})
                    return
                time.sleep(0.15)
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


def serve(host: str = "127.0.0.1", port: int = 8080, model: str = "mock",
          base_url: str = "", track: str = "L", metrics_url: str = "") -> None:
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
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        srv.server_close()
