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

from flagpilot import client as llm_client
from flagpilot import config, custom, probe, tagger, storage, writeup
from flagpilot.agent import run_attempt
from flagpilot.chat import (ChatSession, context_note, context_plan, context_summary, context_window, recalled_turns,
                            read_log)
from flagpilot.config import (ABLATIONS, GEMINI, Budget, Policy, RunConfig,
                              SandboxLimits, gemini_api_key, get_ablation, is_gemini, make_model_spec, extra_endpoints, dotenv_value)
from flagpilot.memory import MemoryStore
from flagpilot.tasks import Task, load_index, select
from flagpilot.trace import read_jsonl

from app import catalog, drafting, httpcache, lab, linkfix, runs_index, tacc, tacc_live
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


def _code_sig() -> str:
    """服务端 Python 代码的指纹（文件名 + 修改时间 + 大小）。网页总是读磁盘上最新的 index.html，
    进程却可能还是改代码之前起的：两者对不上时页面会请求不存在的接口。/api/tasks 带上 stale，页面据此提醒重启。"""
    h = hashlib.sha1()
    here = Path(__file__).resolve().parent
    for base in (here, here.parent / "flagpilot"):
        for f in sorted(base.glob("*.py")):
            try:
                st = f.stat()
            except OSError:
                continue
            h.update(f"{f.name}:{st.st_mtime_ns}:{st.st_size}".encode())
    return h.hexdigest()[:12]


BOOT_SIG = _code_sig()

# 会话 id 是 uuid4().hex[:12]，只允许这个形状。
# 以前 /api/chat/history?id=../.. 会直接拼进 runs/chat/<sid>/chat.jsonl，
# 变成一个"只能读 chat.jsonl"的目录穿越；顺手也能把沙箱工作目录指到外面。
_SID_RE = re.compile(r"^[0-9a-f]{12}$")
MAX_CONCURRENT_RUNS = 3          # 单进程同时跑几个 agent；防本地网页刷爆
# 网页单题的轮数上限（页面上可选）。20 是评测口径（Lab 实验、批跑都用它）；导入的难题 20 轮不够，单题默认 40。
TURN_CHOICES = (20, 40, 60, 100)
WEB_TURNS = 40
# "+20 turns" during a run can take it past the choices (80, 120 ...); "Run again" re-runs with what the run ended with
TURN_STEP, TURN_MAX = 20, 200


def _chat_context(rows: list[dict]) -> dict:
    """一个对话的上下文现状（页面显示用）：按它的模型的窗口算；TACC 上的对话按 Qwen 的窗口。"""
    meta = next((r for r in rows if r.get("type") == "meta"), {})
    model = runs_index.thread_model(rows) or str(meta.get("model") or "") or str(SETTINGS["model"])
    return context_summary(context_plan(rows, context_window(model)))


def _turns_ok(n) -> bool:
    return isinstance(n, int) and not isinstance(n, bool) and TURN_STEP <= n <= TURN_MAX and n % TURN_STEP == 0
MAX_STATE_ENTRIES = 80           # STATE 只增不减会吃内存，完成后按时间淘汰
RUNNING = 0


def _base_url() -> str:
    """当前端点。gemini-* 模型没给端点时走 Google 的 OpenAI 兼容端点（与 make_model_spec 同一条规则）；
    别的端点（.env 里的 NAME_BASE_URL）上的模型走那个端点。"""
    if SETTINGS["base_url"]:
        return SETTINGS["base_url"]
    r = _route(str(SETTINGS["model"]))
    if r:
        return r["base_url"]
    return GEMINI["base_url"] if str(SETTINGS["model"]).lower().startswith("gemini") else "http://127.0.0.1:8000/v1"


# ---- 别的 OpenAI 兼容端点（.env：NAME_BASE_URL + NAME_API_KEY）：每个端点的模型表决定一个模型走哪里 ----
_EXTRA: dict[str, dict] = {}
_ROUTES: dict[str, str] = {}          # 模型 → 端点名
_ROUTES_AT = [0.0]
_NOT_CHAT_EXTRA = ("image", "tts", "embed", "whisper", "audio", "moderation", "review", "dall-e", "rerank")


def _load_routes(force: bool = False) -> None:
    """读 .env 里的端点并向每个端点要模型表（缓存 5 分钟；列不出来时用 NAME_MODELS 声明的）。"""
    if not force and _ROUTES_AT[0] and time.time() - _ROUTES_AT[0] < 300:
        return
    _ROUTES_AT[0] = time.time()
    extra = extra_endpoints()
    routes: dict[str, str] = {}
    for name, e in extra.items():
        ids = list(e.get("models") or [])
        try:
            req = urllib.request.Request(e["base_url"] + "/models", headers={"Authorization": "Bearer " + e["api_key"], "User-Agent": "FlagPilot/0.1.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                ids += [str(m.get("id")) for m in (json.loads(r.read().decode()).get("data") or []) if m.get("id")]
        except Exception:
            pass
        for i in ids:
            if not any(k in i.lower() for k in _NOT_CHAT_EXTRA) and not i.lower().startswith(("gemini-", "gemma-")):
                routes.setdefault(i, name)
    _EXTRA.clear()
    _EXTRA.update(extra)
    _ROUTES.clear()
    _ROUTES.update(routes)


def _route(model: str) -> dict | None:
    if not _ROUTES_AT[0]:
        _load_routes()
    name = _ROUTES.get(model or "")
    return _EXTRA.get(name) if name else None


def _api_key(base: str | None = None) -> str:
    """服务端用的密钥：别的端点用它自己的 NAME_API_KEY；否则 FLAGPILOT_API_KEY；Gemini 端点没设时用 GEMINI_API_KEY。"""
    b = base or _base_url()
    for e in _EXTRA.values():
        if b == e["base_url"]:
            return e["api_key"]
    key = os.environ.get("FLAGPILOT_API_KEY", "")
    if (not key or key == "EMPTY") and is_gemini(b):
        key = gemini_api_key()
    return key or "EMPTY"


def _base_for(model: str) -> str:
    """某一个模型的端点（答疑每个线程可以用不同模型）：gemini-* / gemma-* 走 Google，别的端点列出的模型走那个端点，其余走配置的端点。"""
    if model.lower().startswith(("gemini-", "gemma-")):
        return SETTINGS["base_url"] if is_gemini(SETTINGS["base_url"] or "") else GEMINI["base_url"]
    r = _route(model)
    if r:
        return r["base_url"]
    return SETTINGS["base_url"] or "http://127.0.0.1:8000/v1"


# 线程里选的模型名（来自浏览器）：只认这些字符，挡住拼进 URL / 日志的怪东西
_MODEL_RE = re.compile(r"^[A-Za-z0-9][\w.:/@-]{0,99}$")
TACC_MODEL = "qwen3.8-27b (TACC)"


# Gemini 的模型表里还有语音、图片、实时音频、电脑操控、深度研究等专用模型，答疑和解题用不上：
# 只留 gemini- / gemma- 开头的，再去掉这些专用的
_NOT_CHAT = ("tts", "image", "audio", "live", "embedding", "transcribe", "computer-use", "robotics",
             "lyria", "banana", "imagen", "veo", "aqa", "omni", "customtools")


# 菜单里只列提供商；具体用哪个型号由这里决定：便宜的在前（按名字估：lite / flash 在前，pro / max 在后），
# 换不了就下一个，最后是 FLAGPILOT_FALLBACK_MODEL（默认 DeepSeek V4.1 Flash）。切换不在界面上显示，只记进轨迹。
PROVIDERS = (("gemini", "Gemini", r"^(gemini|gemma)-"), ("deepseek", "DeepSeek", r"^deepseek-"), ("glm", "GLM", r"^glm-"),
             ("qwen", "Qwen", r"^qwen"), ("gpt", "GPT", r"^gpt-"), ("claude", "Claude", r"^claude-"), ("mistral", "Mistral", r"^(mistral|codestral)"))
CHAIN_LEN = 3


def provider_of(model: str) -> str:
    m = str(model or "").removeprefix("provider:")
    if str(model or "").startswith("provider:"):
        return m
    return next((pid for pid, _, rx in PROVIDERS if re.match(rx, m, re.I)), "")


def _cost_rank(model: str) -> tuple:
    m = model.lower()
    words = set(re.split(r"[-_.:/]", m))             # 按整词比："gemini" 里有 "mini"，按子串比会把所有 Gemini 都当成最便宜的
    tier = (0 if words & {"lite", "nano", "mini", "air", "small", "tiny"} else 1 if words & {"flash", "turbo", "haiku"}
            else 3 if words & {"pro", "max", "plus", "ultra", "opus", "large"} else 2)
    if m.startswith("gemma-"):
        tier = 1.5                                   # 免费但慢：排在 Gemini 的 lite 之后
    shaky = 1 if re.search(r"preview|exp|beta", m) else 0
    alias = 0 if m.endswith("-latest") else 1
    ver = [-float(x) for x in re.findall(r"\d+(?:\.\d+)?", m)[:1]] or [0.0]
    return (tier, shaky, alias, ver[0], m)


def provider_chain(pid: str) -> list[str]:
    """一个提供商的型号，便宜的在前（最多 CHAIN_LEN 个），后面接全局备援。"""
    models = _MODELS.get(("all",), all_models)["models"]
    mine = sorted((m for m in models if provider_of(m) == pid), key=_cost_rank)[:CHAIN_LEN]
    fb = _fallback_model()
    return list(dict.fromkeys(mine + ([fb] if fb else [])))


def _fallback_model() -> str:
    """出错 / 没额度时最后换到的型号：.env / 环境变量 FLAGPILOT_FALLBACK_MODEL；没设就看有没有 DeepSeek V4.1 Flash。"""
    name = os.environ.get("FLAGPILOT_FALLBACK_MODEL") or dotenv_value("FLAGPILOT_FALLBACK_MODEL")
    if name:
        return name
    _load_routes()
    return "deepseek-v4.1-flash" if "deepseek-v4.1-flash" in _ROUTES else ""


def provider_list() -> list[dict]:
    models = _MODELS.get(("all",), all_models)["models"]
    have = {provider_of(m) for m in models}
    return [{"id": f"provider:{pid}", "label": label} for pid, label, _ in PROVIDERS if pid in have]


def all_models() -> dict:
    """界面的模型表：当前端点的 + Gemini 的（有密钥、当前又不是它时）+ .env 里别的端点的；每个模型注明来自哪个端点。"""
    models = list_models()
    if gemini_api_key() and not is_gemini(_base_url()):
        models += [m for m in _list_at(GEMINI["base_url"], gemini_api_key(), gemini=True) if m not in models]
    _load_routes()
    models += [m for m in sorted(_ROUTES) if m not in models]
    return {"models": models, "current": SETTINGS["model"], "providers": {m: n for m, n in _ROUTES.items() if m in models}}


def _list_at(base: str, key: str, gemini: bool = False) -> list[str]:
    try:
        req = urllib.request.Request(base.rstrip("/") + "/models", headers={"Authorization": "Bearer " + key, "User-Agent": "FlagPilot/0.1.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            ids = [str(m.get("id")) for m in (json.loads(r.read().decode()).get("data") or []) if m.get("id")]
    except Exception:
        return []
    if gemini:
        ids = [i.removeprefix("models/") for i in ids]
        ids = [i for i in ids if i.startswith(("gemini-", "gemma-")) and not any(k in i for k in _NOT_CHAT)]
    return ids


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
    hit = select(_index(), names=[name])
    if hit:
        return hit[0]
    return custom.get(str(name))


def _index() -> list[Task]:
    """内置题库；还没有（新克隆、题库没取下来）就是空的，页面照常可用（上传 / 导入 / 答疑 / 回放），不报 500。"""
    try:
        return load_index()
    except FileNotFoundError:
        return []


def _label(model: str) -> str:
    pid = provider_of(model)
    return next((label for p, label, _ in PROVIDERS if p == pid), "Demo model" if str(model).startswith("mock") else str(model))


def _resolve(model: str | None, chain: bool) -> tuple[str, str]:
    """菜单里选的（提供商或具体型号）→ (这次先用的型号, 后面的链)。chain=False（Lab 实验）只取一个型号、不切换。"""
    name = model or SETTINGS["model"]
    if str(name).startswith("provider:"):
        models = provider_chain(name.removeprefix("provider:"))
        up = [m for m in models if not (llm_client.DOWN.get(m) and llm_client.DOWN[m][0] > time.time())] or models
        first = up[0] if up else (_fallback_model() or "mock")
        rest = [m for m in models if m != first]
    else:
        first = name
        fb = _fallback_model()
        rest = [fb] if fb and fb != first and first != "mock" else []
    return first, (",".join(rest) if chain else "")


def _spec(name: str, base: str):
    spec = make_model_spec(name, base, api_key=_api_key(base), metrics_url=SETTINGS.get("metrics_url") or "")
    if any(base == e["base_url"] for e in _EXTRA.values()):
        spec.provider = "openai-compatible"           # 网关只认标准字段：不带 vLLM 的 chat_template_kwargs
    return spec


def _with_chain(spec, rest: str):
    spec.fallback = rest
    return spec


# 模型链里后面的型号由这里生成完整配置（端点、密钥、只发标准字段）
llm_client.FALLBACK_RESOLVER = lambda name: _spec(name, _base_for(name))


def _make_cfg(task_batch: str, ablate: str | None = None, model: str | None = None, turns: int = 20) -> RunConfig:
    """model 给了就用它（答疑线程各自的选择），否则用全局设置；提供商在这里换成具体型号和备援链。
    Lab 实验不带备援链：一个实验组只能是一个型号，否则结果没法比。"""
    name, rest = _resolve(model, chain=task_batch != "lab")
    base = _base_for(name)
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
        model=_with_chain(_spec(name, base), rest),
        budget=Budget.for_turns(turns),
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
    """开跑时就带上单步：否则第一条命令可能抢在"打开单步"的请求之前执行。
    hint：结果卡上"带提示重跑"——提示在第一轮调模型之前就进对话。"""
    ctl = RunControl()
    if payload.get("step"):
        ctl.set("step", True)
    hint = _hint_of(payload)
    if hint:
        ctl.set("hint", hint)
    return ctl


def _link_ask():
    """给 linkfix 的模型：链接里的路径或页面对不上时，从真实候选里挑一个（它的回答只有在候选里才算）。演示模型不用。"""
    cfg = _make_cfg("web-import")
    if cfg.model.name == "mock":
        return None
    client = llm_client.build_client(cfg.model)

    def ask(prompt: str) -> str:
        return client.complete([{"role": "user", "content": prompt}], max_tokens=200).text or ""
    return ask


def _hint_of(payload: dict) -> str:
    return " ".join(str(payload.get("hint") or "").split())[:500]


def _finish(rid: str) -> dict:
    """Finish：立刻结束这次运行，会话保留。页面马上收到 done（verdict "stopped"）；后台收拾干净：
    本机运行在下一个闸门处停下（不再执行新命令），TACC 作业在后台取消（名额等它真停了才放）。"""
    st = STATE[rid]
    cond = st.setdefault("cond", threading.Condition())
    with cond:
        if st.get("summary") or st.get("finished"):
            return {"ok": True, "already": True}
        st["finished"] = True
        turns = sum(1 for e in st.get("events") or [] if e.get("type") == "model")
    if st.get("control") is not None:
        st["control"].set("stop")
    if st.get("finish") is not None:
        st["finish"].set()
    done = {"type": "done", "verdict": "stopped", "reason": "finished by you", "turns": turns, "task": st.get("task"),
            "ablation": st.get("ablate"), "web_run_id": rid, "e2e_s": round(time.time() - st.get("t0", time.time()), 2)}
    with cond:
        st.setdefault("events", []).append(done)
        st["summary"] = done
        cond.notify_all()
    return {"ok": True}


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
        cfg = _make_cfg("web", ablate, model=STATE[run_id].get("model"), turns=STATE[run_id].get("turns") or WEB_TURNS)
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
        if STATE[run_id].get("finished"):         # Finish 已经给页面发过 done：轨迹照常落盘，这里不再发第二个
            return
        hook({"type": "done", **summary})
        STATE[run_id]["summary"] = summary
        _wake(cond)
    except Exception as e:
        if STATE[run_id].get("finished"):
            return
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
            # 304 也要带 Vary：同一个 ETag 对应 gzip 和原文两种表示，缺了它，
            # 中间缓存可能把压缩过的版本发给不支持 gzip 的客户端（RFC 7232 §4.1）。
            self.send_header("Vary", "Accept-Encoding")
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
        if path == "/api/task":
            # 站内预览：题面、附件、来源；不含答案（只说有没有）
            name = urllib.parse.parse_qs(self.path.partition("?")[2]).get("name", [""])[0]
            t = resolve_task(name) if name and len(name) < 200 else None
            if t is None:
                return self._json(404, {"error": "No such challenge"})
            files = [{"name": n, "size": sz} for n, sz in drafting.files_of(t)]
            return self._json(200, {"name": t.name, "title": t.title, "category": t.category, "statement": t.query or t.title,
                                    "files": files, "source": t.source, "custom": t.scope == "custom",
                                    "has_gold": t.has_gold, "service": t.service})
        if path == "/api/tasks":
            tasks = [{"name": t.name, "category": t.category, "scope": t.scope,
                      "title": t.title[:90], "custom": False, "has_gold": t.has_gold, "source": t.source}
                     for t in _index()]
            mine = [{"name": t.name, "category": t.category, "scope": "custom",
                     "title": t.title[:90], "custom": True, "has_gold": t.has_gold,
                     "n_assets": len(t.assets), "source": t.source,
                     # 原比赛里是网络服务的题：真 flag 从来不在这些文件里
                     "service": t.service} for t in custom.load()]
            return self._json(200, {
                "tasks": tasks,
                "custom_tasks": mine,
                "stale": _code_sig() != BOOT_SIG,      # 代码改过、服务没重启
                "model": SETTINGS["model"],
                "ablation": SETTINGS["ablation"],
                "ablations": [{"name": k, "desc": v.desc} for k, v in ABLATIONS.items()],
                "turns": WEB_TURNS, "turn_choices": list(TURN_CHOICES), "turn_max": TURN_MAX,
            })
        if path == "/api/models":
            return self._json(200, {"providers": provider_list(), "current": SETTINGS["model"],
                                    "current_label": _label(SETTINGS["model"]),
                                    # 具体型号表仍然给（CLI、脚本、旧调用方用）；界面只显示 providers
                                    "models": _MODELS.get(("all",), all_models)["models"]})
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
            return self._json(200, tacc_live.watch() if self._qparam("watch") == "1" else tacc_live.status())
        if path == "/api/chat/sessions":
            return self._json(200, {"sessions": runs_index.list_chats(config.RUNS_ROOT / "chat")})
        if path == "/api/chat/history":
            raw = self._qid()
            if raw and not _SID_RE.match(raw):
                return self._json(400, {"error": "bad session id"})
            sid = raw
            p = config.RUNS_ROOT / "chat" / sid / "chat.jsonl"
            rows = read_log(p) if sid and p.exists() else []
            gone = recalled_turns(rows)
            return self._json(200, {"session_id": sid,
                                    "messages": [r for r in rows
                                                 if r.get("type") in ("user", "reply", "error") and r.get("turn") not in gone],
                                    "model": runs_index.thread_model(rows),
                                    # 这个对话是关于哪道题的：页面打开旧对话时把 Ask 的题目换成它（不动 Solve 的）
                                    "task": next((str(r.get("task") or "") for r in rows if r.get("type") == "meta"), ""),
                                    "context": _chat_context(rows),
                                    "exists": bool(rows)})
        if path == "/api/chat/context":
            raw = self._qid()
            if not _SID_RE.match(raw or ""):
                return self._json(400, {"error": "bad session id"})
            p = config.RUNS_ROOT / "chat" / raw / "chat.jsonl"
            return self._json(200, _chat_context(read_log(p) if p.exists() else []))     # 还没落盘的对话：空的
        if path.startswith("/api/catalog/"):
            return self._catalog_get(path)
        if path == "/api/lab/experiments":
            return self._json(200, {"experiments": lab.experiments(config.RUNS_ROOT)})
        if path == "/api/lab/experiment":
            exp = lab.experiment(config.RUNS_ROOT, self._qparam("id"))
            return self._json(200, exp) if exp else self._json(404, {"error": "unknown experiment"})
        if path == "/api/lab/cross":
            try:
                return self._json(200, lab.cross(config.RUNS_ROOT, self._qparam("a"), self._qparam("b")))
            except ValueError as e:
                return self._json(400, {"error": str(e)})
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
                  "/api/tacc/session/start", "/api/tacc/session/stop", "/api/lab/start", "/api/lab/stop", "/api/lab/retry", "/api/run/control", "/api/custom/gold", "/api/chat/recall", "/api/chat/context", "/api/custom/edit", "/api/custom/draft",
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
            # Ask 只用托管模型回答（2026-11-22："the discussion section should not support TACC"）：每条消息一个 Slurm 作业，
            # 一问要等半分钟到几分钟、还占着账号的两个作业名额。Qwen on TACC 留给 Solve 跑题
            return self._json(400, {"error": "Ask answers with the hosted models; Qwen on TACC runs challenges from Solve"})
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
        if path == "/api/chat/recall":
            sid, turn = str(payload.get("session_id") or ""), payload.get("turn")
            if not _SID_RE.match(sid) or not isinstance(turn, int) or turn < 1:
                return self._json(400, {"error": "session_id and turn required"})
            path_ = config.RUNS_ROOT / "chat" / sid / "chat.jsonl"
            if not path_.exists():
                return self._json(404, {"error": "unknown conversation"})
            storage.append_record(path_, {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "type": "recall", "turn": turn})
            with LOCK:
                sess = CHATS.pop(sid, None)       # 下一条消息从盘上重建会话：撤回的那轮不再进模型的上下文
            if sess is not None:
                sess.cancel(turn)                 # 撤回的正是还在回答的那一轮：做完当前这步就停
            return self._json(200, {"ok": True, "turn": turn})
        if path == "/api/chat/context":
            # 上下文管理：某一轮不带 / 带回（exclude / include）、从某一轮重新开始（fresh）、全部恢复（reset）。日志只追加，下一问生效
            sid, op, turn = str(payload.get("session_id") or ""), str(payload.get("op") or ""), payload.get("turn")
            if not _SID_RE.match(sid) or op not in ("exclude", "include", "fresh", "reset"):
                return self._json(400, {"error": "session_id and op (exclude, include, fresh, reset) required"})
            if op != "reset" and (isinstance(turn, bool) or not isinstance(turn, int) or turn < 1):
                return self._json(400, {"error": "turn required"})
            path_ = config.RUNS_ROOT / "chat" / sid / "chat.jsonl"
            if not path_.exists():
                return self._json(404, {"error": "unknown conversation"})
            storage.append_record(path_, {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "type": "context", "op": op,
                                          **({"turn": turn} if op != "reset" else {})})
            return self._json(200, _chat_context(read_log(path_)))
        if path == "/api/custom/edit":
            q = payload.get("query")
            try:
                t = custom.update(str(payload.get("task") or ""), title=payload.get("title"),
                                  query=None if q is None else str(q), category=payload.get("category"))
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            return self._json(200, {"task": {"name": t.name, "title": t.title, "category": t.category, "service": t.service}})
        if path == "/api/custom/draft":
            t = custom.get(str(payload.get("task") or ""))
            if t is None:
                return self._json(404, {"error": "No uploaded challenge by that name"})
            cfg = _make_cfg("web-draft")
            if cfg.model.name == "mock":
                return self._json(200, drafting.rules_draft(t))
            return self._json(200, drafting.draft(t, llm_client.build_client(cfg.model), label=lab.pretty_model(SETTINGS["model"])))
        if path == "/api/custom/gold":
            name, flag = str(payload.get("task") or ""), str(payload.get("flag") or "").strip()   # flag：刚才那次运行交的，当场判对错（不落盘）
            try:
                if payload.get("clear") is True:
                    t = custom.clear_gold(name)
                    return self._json(200, {"task": t.name, "has_gold": False, "match": None})
                gold = str(payload.get("gold") or "").strip()
                # 要存的正是这次运行没能证明的那个 flag：多半是模型自己编的。存进去以后"判对"就成了自己证明自己，先问一句
                if flag and gold == flag and payload.get("unverified") is True and payload.get("confirm") is not True:
                    return self._json(409, {"needs_confirm": True, "error": "That is the flag this run could not back up, "
                                             "so it is probably made up. Save it only if you know it is the real flag."})
                t = custom.set_gold(name, gold)
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            match = (hashlib.sha256(flag.encode()).hexdigest() == t.gold_sha256) if flag else None
            return self._json(200, {"task": t.name, "has_gold": t.has_gold, "match": match})
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
        turns = payload.get("turns")
        turns = WEB_TURNS if turns is None else turns
        if not _turns_ok(turns):
            return self._json(400, {"error": f"turns must be a multiple of {TURN_STEP} from {TURN_STEP} to {TURN_MAX}"})
        # the run's own model (Solve's settings bar, or "Run again" with a replay's model); none = the server's setting
        model = str(payload.get("model") or "").strip() or None
        if model and not _MODEL_RE.match(model):
            return self._json(400, {"error": "bad model name"})
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
                          "finish": threading.Event() if payload.get("backend") == "tacc" else None,
                          "hint": _hint_of(payload),
                          "turns": turns, "model": model,
                          "ablate": ablate or SETTINGS["ablation"]}
        try:
            target = _tacc_worker if payload.get("backend") == "tacc" else _worker
            threading.Thread(target=target, args=(rid, task, ablate), daemon=True).start()
        except Exception:
            with LOCK:
                RUNNING = max(0, RUNNING - 1)
            raise
        self._json(200, {"run_id": rid, "ablation": ablate or SETTINGS["ablation"], "turns": turns, "model": model or SETTINGS["model"]})

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
                r = catalog.import_url(str(payload["url"])[:2000], ask=_link_ask())
            else:
                return self._json(400, {"error": "path or url required"})
        except linkfix.Choose as c:          # 好几道题都对得上：页面列出来让人挑
            return self._json(409, {"error": c.why, "choices": c.choices, "corrected": c.notes})
        except catalog.CatalogError as e:
            return self._json(502, {"error": str(e)})
        except ValueError as e:              # custom.create 的输入校验
            return self._json(400, {"error": str(e)})
        if r.get("local"):                   # 本机题库里已有这道题：不用导入
            return self._json(200, r)
        t = r["task"]
        return self._json(200, {**r, "task": {"name": t.name, "title": t.title, "category": t.category,
                                              "has_gold": t.has_gold, "n_assets": len(t.assets), "source": t.source}})

    def _run_control(self, payload: dict) -> None:
        """看着一次本机运行的人：单步、暂停 / 继续、批准 / 修改 / 跳过当前命令、停止、插提示。"""
        rid, op = str(payload.get("id") or ""), str(payload.get("op") or "")
        st = STATE.get(rid) if _SID_RE.match(rid) else None
        if op == "finish":                         # 本机和 TACC 运行都能立刻结束
            if not st or st.get("kind") == "chat" or (st.get("summary") and not st.get("finished")):
                return self._json(404, {"error": "No run in progress with that id"})
            return self._json(200, _finish(rid))
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
            if model.startswith("provider:"):
                model = _resolve(model, chain=False)[0]      # 实验只用一个型号：开跑时锁定这个提供商当下最便宜的可用型号
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
            if want and getattr(sess, "picked", None) != want and sess.cfg.model.name != want:
                sess.set_model(_make_cfg("web-chat", model=want).model)     # 同一线程中途换模型（或换提供商）
            if want:
                sess.picked = want
            q = STATE[sid]["q"]
            turn = sess.turns + 1                    # 这条消息的轮次号（撤回时用）
            sess.turns = turn                        # 现在就占上：同一会话紧接着再来一条也拿不到同一个号

        def worker() -> None:
            try:
                sess.load_context()                  # 这一问之前按日志重建上下文："不带" / "重新开始" / 按窗口省略随即生效
                reply = sess.ask(
                    message,
                    on_delta=lambda t: q.put({"type": "chat_delta", "text": t}),
                    hook=q.put,
                    turn=turn,
                )
                q.put({"type": "chat_done", **reply.as_dict(),
                       "session_id": sid, "turns": sess.turns, "model": getattr(sess, "picked", None) or SETTINGS["model"],
                       "total_steps": sess.total_steps,
                       "task": sess.task.name if sess.task else ""})
            except Exception as e:
                sess.log_error(f"{type(e).__name__}: {e}", turn=turn)
                q.put({"type": "error", "message": f"{type(e).__name__}: {e}",
                       "trace": traceback.format_exc()[-1200:]})
            finally:
                q.put(None)

        threading.Thread(target=worker, daemon=True).start()
        return self._json(200, {"session_id": sid, "turn": turn})

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
        # 这里的 300 必须和下面 items[:300] 一致。list_runs 自己的默认 limit 是 200
        # （按运行目录计），不传的话页面上限实际是 200：第 201 条以后的运行再怎么
        # 排在前面也拿不到，而代码看起来是允许 300 条的。
        items += [x for x in runs_index.list_runs(config.RUNS_ROOT, limit=300)
                  if x["id"] not in mem]
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
    skipped: list[tuple[str, str]] = []
    for d in dirs:
        try:
            evs = read_jsonl(d / "trace.jsonl")
        except Exception as exc:
            # 挂载是"有什么就回放什么"：加密过的 trace 没有密钥、拷贝传了一半、别的工具写的二进制，
            # 都只说明这一条读不了。以前异常直接抛穿，`main()` 在启动阶段就死了，整页起不来；
            # 一条坏 trace 不该让另外几百条也看不见。
            skipped.append((d.name, f"{type(exc).__name__}: {exc}"))
            continue
        if not evs:
            # 读取本身是容错的（坏字节换成 U+FFFD），坏行于是解析失败、整份文件返回空——
            # 和"还没开始写"的空文件长得一模一样。按大小分开：0 字节是没开始，非空却
            # 一行都读不出来就是坏了，得报出来，否则 Run 页又少了一条却没人知道。
            try:
                blank = (d / "trace.jsonl").stat().st_size == 0
            except OSError:
                blank = False
            if not blank:
                skipped.append((d.name, "文件非空，但没有一行能解析"))
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
    if skipped:
        # 跳过要说话：Run 页少了几条，得能分清是"坏了"还是"本来就没跑"
        print(f"⚠ 回放：{len(skipped)} 条 trace 读不了，已跳过；其余 {len(mounted)} 条照常挂载", flush=True)
        for name, why in skipped[:10]:
            print(f"    {name}: {why}", flush=True)
        if len(skipped) > 10:
            print(f"    …另有 {len(skipped) - 10} 条", flush=True)
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

    # 和本机运行一样：模型逐字输出放进 partial，SSE 读端推 model_partial（不用等整轮结束）
    partial = st.setdefault("partial", {"turn": 0, "text": "", "seq": 0, "t": 0.0})

    def on_delta(turn: int, chunk: str) -> None:
        with cond:
            if partial["turn"] != turn:
                partial["turn"], partial["text"] = turn, ""
            if len(partial["text"]) < PARTIAL_MAX_CHARS:
                partial["text"] += chunk
            partial["seq"] += 1
            cond.notify_all()

    try:
        fin = st.get("finish") or threading.Event()
        d = tacc_live.run_task(task_name, tier, config.RUNS_ROOT, on_event, on_delta=on_delta, cancelled=fin.is_set,
                               hint=st.get("hint") or "", turns=st.get("turns") or WEB_TURNS)
        if st.get("finished"):                     # Finish：页面已经收到 done，作业在后台取消
            return
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


def auto_model() -> str:
    """./start.sh 默认的 --model auto：有 Gemini 密钥就用 Gemini 这个提供商，否则 .env 里第一个配了端点、列得出型号的提供商，
    都没有就用离线演示模型 mock（不要任何密钥也能把整个界面走一遍）。"""
    if gemini_api_key():
        return "provider:gemini"
    try:
        _load_routes()
        have = {provider_of(m) for m in _ROUTES}
    except Exception:
        have = set()
    for pid, _label, _ in PROVIDERS:
        if pid in have:
            return f"provider:{pid}"
    return "mock"


class QuietServer(ThreadingHTTPServer):
    """浏览器关标签页、断网、刷新时连接被对方掐断，不是服务器错误：不往日志里打一整段 traceback。"""
    daemon_threads = True

    def handle_error(self, request, client_address):
        import sys
        if isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def serve(host: str = "127.0.0.1", port: int = 8080, model: str = "mock",
          base_url: str = "", track: str = "L", metrics_url: str = "",
          replay_dir: str = "") -> None:
    if model == "auto":
        model = auto_model()
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
    srv = QuietServer((host, port), Handler)
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
