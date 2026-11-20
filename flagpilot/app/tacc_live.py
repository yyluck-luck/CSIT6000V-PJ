"""网页的 TACC 会话：开一个 Qwen 服务作业（≤ 1 小时）常驻，单题求解与答疑每次作为小作业提交。

只走课程 SDK（submit / cat / download / cancel），不建任何转发。作业把网页需要的东西按行打进
Slurm 日志（flagpilot/remote.py：@@ev / @@delta / @@reply），这里每隔几秒 `tcloud cat` 一次解析。
客户端作业在平台上用固定的 WORKDIR，同一时间只能有一个——所以求解与答疑排他（busy）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
BAD_NODES = "10-0-1-10,10-0-1-11,10-0-4-10"     # 2026-11 实测有 Slurm 管不到的进程占卡 / 占 8000 端口
SESSION_S = 3600 - 180                            # 分区上限 1 小时，留 3 分钟收尾
BOOT_MARGIN_S = 60                                # 分到节点后、作业第一行输出前的激活时间（偏大估计）
_L = threading.Lock()
S: dict = {"phase": "idle", "serve": None, "node": None, "url": "", "t0": 0.0, "ready": 0.0, "log": [], "busy": "", "error": ""}


def _tc() -> str:
    return os.environ.get("TCLOUD_BIN") or "tcloud"


def _submit_path() -> Path:
    return Path(os.environ.get("FLAGPILOT_TACC_SUBMIT") or ROOT / "deploy" / "tcloud" / "submit.sh")


def _clean(text: str) -> str:
    return "\n".join(l.rstrip("\r") for l in (text or "").splitlines()
                     if "post-quantum" not in l and "store now, decrypt later" not in l and "pq.html" not in l)


def _sdk(*args: str, timeout: float = 120) -> str:
    try:
        r = subprocess.run([_tc(), *args], capture_output=True, timeout=timeout, cwd=str(ROOT))
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return _clean((r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace"))


def _submit(mode: str, env_extra: dict, url: str = "") -> str:
    env = {**os.environ, "LANG": os.environ.get("LANG") or "en_US.UTF-8", **env_extra}
    try:
        r = subprocess.run(["bash", str(_submit_path()), mode, *([url] if url else [])], capture_output=True,
                           timeout=600, cwd=str(ROOT), env=env)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    out = _clean((r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace"))
    m = re.findall(r"Submitted batch job (\d+)", out)
    return m[-1] if m else ""


def _log(msg: str) -> None:
    with _L:
        S["log"].append(time.strftime("%H:%M:%S ") + msg)
        del S["log"][:-60]


def status() -> dict:
    with _L:
        d = {k: (list(v) if isinstance(v, list) else v) for k, v in S.items()}
    left = (d["t0"] + SESSION_S - time.time()) if d["phase"] == "ready" else 0
    return {**d, "remaining": max(0, round(left)), "elapsed": round(time.time() - d["t0"]) if d["t0"] else 0}


def start() -> dict:
    with _L:
        if S["phase"] in ("starting", "ready"):
            return {"ok": True, "phase": S["phase"]}
        S.update(phase="starting", serve=None, node=None, url="", t0=time.time(), ready=0.0, error="", log=[])
    threading.Thread(target=_serve_loop, daemon=True).start()
    return {"ok": True, "phase": "starting"}


def _fail(msg: str) -> None:
    _log(msg)
    with _L:
        S.update(phase="failed", error=msg, url="")


def _serve_loop() -> None:
    excl = os.environ.get("FLAGPILOT_TACC_EXCLUDE", BAD_NODES)
    poll = float(os.environ.get("FLAGPILOT_TACC_POLL", "15"))
    for attempt in range(1, 5):
        t_sub = time.time()
        jid = _submit("serve", {"FP_EXCLUDE": excl})
        if not jid:
            return _fail("serve submission failed")
        # 1 小时从作业真正开跑算起。看到第一行输出之前先按提交时刻算（只会偏早，安全）；
        # 看到 SDK_BOOTSTRAP 后改成"上一次还没输出的那次查询 − 激活余量"，排队多久都不吃会话时长。
        with _L:
            S.update(serve=jid, t0=t_sub)
        _log(f"serve job {jid} submitted (attempt {attempt})")
        deadline, retry, quiet, running = time.time() + 45 * 60, False, t_sub, False
        while time.time() < deadline:
            if S["phase"] == "stopping":
                _sdk("cancel", "-j", jid)
                _log(f"serve job {jid} cancelled")
                with _L:
                    S.update(phase="idle", serve=None)
                return
            time.sleep(poll)
            t_ask = time.time()
            L = _sdk("cat", f"slurm_log/qwen-public-serve-{jid}.log")
            if not running:
                if "SDK_BOOTSTRAP=" in L:
                    running = True
                    with _L:
                        S["t0"] = max(t_sub, quiet - BOOT_MARGIN_S)
                else:
                    quiet = t_ask
            m = re.search(r"SERVICE_BASE_URL=(http://([^:\s]+):\d+/v1)", L)
            if "PRECHECK_FAIL" in L:
                node = (re.search(r"PRECHECK_FAIL node=([^:\s]+)", L) or [None, ""])[1]
                _log(f"node {node} is busy; retrying elsewhere")
                excl = f"{excl},{node}" if node else excl
                retry = True
                break
            if "Application startup complete" in L and m:
                with _L:
                    S.update(phase="ready", url=m.group(1), node=m.group(2), ready=time.time())
                _log(f"Qwen 27B ready on {m.group(2)}")
                threading.Thread(target=_watchdog, args=(jid,), daemon=True).start()
                return
            if any(k in L for k in ("Traceback (most recent call last)", "srun: error", "CANCELLED")):
                if m and ("Free memory on device" in L or "Address already in use" in L):
                    excl = f"{excl},{m.group(2)}"
                    _log(f"node {m.group(2)} is busy; retrying elsewhere")
                    retry = True
                    break
                _sdk("cancel", "-j", jid)
                return _fail(f"serve job {jid} failed")
            _log(f"waiting for serve {jid}" + (f" on {m.group(2)}" if m else " (starting)" if running else " (queued)"))
        if not retry:
            _sdk("cancel", "-j", jid)
            return _fail(f"serve job {jid} not ready within 45 min (cluster busy)")
    _fail("no server after 4 attempts")


def _watchdog(jid: str) -> None:
    while True:
        time.sleep(20)
        with _L:
            if S["phase"] != "ready" or S["serve"] != jid:
                return
            over = time.time() > S["t0"] + SESSION_S
        if over:
            _log("session reached the 1-hour partition limit")
            stop()
            return


def stop() -> dict:
    with _L:
        ph, jid = S["phase"], S["serve"]
        if ph == "starting":
            S["phase"] = "stopping"
            return {"ok": True}
        if ph != "ready":
            return {"ok": True}
        S.update(phase="idle", url="", serve=None, node=None)
    _sdk("cancel", "-j", jid)
    _log(f"serve job {jid} cancelled")
    return {"ok": True}


def _claim(what: str) -> str:
    with _L:
        if S["phase"] != "ready":
            return "Qwen on TACC is not connected"
        if S["busy"]:
            return f"TACC is busy with {S['busy']}; one job at a time"
        S["busy"] = what
    return ""


def _release() -> None:
    with _L:
        S["busy"] = ""


def run_task(task: str, tier: str, runs_root: Path, on_event: Callable[[dict], None]) -> Path | None:
    """单题：提交批次作业（只带这一道题），跟着日志把事件交给 on_event；结束后把运行目录下载回来。"""
    err = _claim(f"solving {task}")
    if err:
        on_event({"type": "error", "message": err})
        return None
    try:
        batch = re.sub(r"[^a-z0-9-]", "-", f"web-{task}-{uuid.uuid4().hex[:5]}".lower())[:40]
        env = {"FP_TASKS": task, "FP_SPLIT": "all", "FP_LIMIT": "0", "FP_ABLATIONS": tier, "FP_EVENTS": "1",
               "FP_BATCH": batch, "FP_DEADLINE_EPOCH": str(int(S["t0"] + SESSION_S - 60)), "FP_EXCLUDE": S["node"] or "",
               "FP_TIME": "00:30:00"}
        on_event({"type": "tacc", "stage": "uploading"})
        jid = _submit("batch", env, url=S["url"])
        if not jid:
            on_event({"type": "error", "message": "TACC submission failed"})
            return None
        on_event({"type": "tacc", "stage": "queued", "job": jid})
        seen, t_end, started = 0, time.time() + 30 * 60, False
        while time.time() < t_end:
            time.sleep(float(os.environ.get("FLAGPILOT_TACC_TICK", "4")))
            L = _sdk("cat", f"slurm_log/flagpilot-client-{jid}.log")
            if not started and "[fp] job=" in L:
                started = True
                on_event({"type": "tacc", "stage": "running", "job": jid})
            evs = [l[5:] for l in L.splitlines() if l.startswith("@@ev ")]
            for raw in evs[seen:]:
                try:
                    on_event(json.loads(raw))
                except json.JSONDecodeError:
                    pass
            seen = len(evs)
            if "[fp] done:" in L or "srun: error" in L or "CANCELLED" in L:
                break
        out = Path(runs_root) / "tacc" / batch
        out.mkdir(parents=True, exist_ok=True)
        _sdk("download", "-r", f"flagpilot-runs/{batch}-{tier}", str(out) + "/", timeout=300)
        return out / f"{batch}-{tier}"
    finally:
        _release()


def chat(message: str, history: list[dict], task: str, tools: bool,
         on_delta: Callable[[str], None], on_tool: Callable[[dict], None],
         on_stage: Callable[[dict], None] | None = None) -> dict:
    """答疑一轮：小作业调用常驻服务；@@delta 逐段交给 on_delta，@@reply 是结果。

    on_stage 收到 uploading → queued（带作业号）→ running（带节点），网页在"三个点"展开后显示。
    """
    stage = on_stage or (lambda ev: None)
    err = _claim("a chat reply")
    if err:
        return {"error": err, "answer": ""}
    try:
        stage({"stage": "uploading"})
        with tempfile.TemporaryDirectory(prefix="fp-chat-") as d:
            Path(d, "msg.txt").write_text(message, encoding="utf-8")
            Path(d, "history.json").write_text(json.dumps(history[-12:], ensure_ascii=False), encoding="utf-8")
            env = {"FP_CHAT_DIR": d, "FP_CHAT_TASK": task or "", "FP_CHAT_TOOLS": "1" if tools else "0",
                   "FP_EXCLUDE": S["node"] or "", "FP_TIME": "00:15:00", "FP_SPLIT": "all"}
            jid = _submit("chat", env, url=S["url"])
        if not jid:
            return {"error": "TACC submission failed", "answer": ""}
        stage({"stage": "queued", "job": jid})
        sent, seen_tools, running, t_end = 0, 0, False, time.time() + 15 * 60
        while time.time() < t_end:
            time.sleep(float(os.environ.get("FLAGPILOT_TACC_TICK", "3")))
            L = _sdk("cat", f"slurm_log/flagpilot-client-{jid}.log")
            if not running and "[fp] job=" in L:
                running = True
                node = (re.search(r"\[fp\] job=\S+ node=(\S+)", L) or [None, ""])[1]
                stage({"stage": "running", "job": jid, "node": node})
            deltas = [json.loads(l[8:]) for l in L.splitlines() if l.startswith("@@delta ")]
            for t in deltas[sent:]:
                on_delta(str(t))
            sent = len(deltas)
            tools_ev = [json.loads(l[5:]) for l in L.splitlines() if l.startswith("@@ev ")]
            for ev in tools_ev[seen_tools:]:
                on_tool(ev)
            seen_tools = len(tools_ev)
            rep = [l for l in L.splitlines() if l.startswith("@@reply ")]
            if rep:
                return json.loads(rep[-1][8:])
            if "[fp] done: chat" in L or "srun: error" in L or "CANCELLED" in L:
                return {"error": "the TACC job ended without a reply", "answer": ""}
        return {"error": "no reply within 15 minutes", "answer": ""}
    finally:
        _release()
