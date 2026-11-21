"""网页的 TACC 会话：开一个 Qwen 服务作业（≤ 1 小时）常驻，单题求解与答疑每次作为小作业提交。

只走课程 SDK（submit / cat / download / cancel），不建任何转发。作业把网页需要的东西按行打进
Slurm 日志（flagpilot/remote.py：@@ev / @@delta / @@reply），这里每隔几秒 `tcloud cat` 一次解析。
客户端作业在平台上用固定的 WORKDIR，同一时间只能有一个——所以求解与答疑排他（busy）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
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
S: dict = {"phase": "idle", "serve": None, "node": None, "url": "", "t0": 0.0, "ready": 0.0, "log": [], "busy": "", "error": "",
          "queue": ""}


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


def _submit(mode: str, env_extra: dict, url: str = "") -> tuple[str, str]:
    """交一个作业。返回 (作业号, 失败原因)；成功时原因为空。

    原因取 submit.sh 输出的最后一行，网页直接显示（以前只有一句 "TACC submission failed"，
    看不出是题名不合法还是题目不存在）。含中文的原因不上页面，完整输出打进服务器日志。
    """
    env = {**os.environ, "LANG": os.environ.get("LANG") or "en_US.UTF-8", **env_extra}
    try:
        r = subprocess.run(["bash", str(_submit_path()), mode, *([url] if url else [])], capture_output=True,
                           timeout=600, cwd=str(ROOT), env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        return "", type(e).__name__
    out = _clean((r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace"))
    m = re.findall(r"Submitted batch job (\d+)", out)
    if m:
        return m[-1], ""
    print(f"[tacc] {mode} submission failed:\n{out[-1500:]}", flush=True)
    lines = [l.strip() for l in out.splitlines() if l.strip() and not l.startswith("[tcloud]")]
    why = lines[-1][:200] if lines else ""
    return "", ("" if re.search(r"[\u3000-\u9fff]", why) else why)


def _stage_upload(task: str, root: Path) -> str:
    """上传的题目（runs/custom/<名字>）不在课程平台的题库里：在本机按数据集格式包成一道题，换成 ASCII 别名。

    以前直接把名字交给 submit.sh：名字带中文（标题是中文时就会这样）过不了名字校验，纯英文的又报
    unknown challenge，网页上只看到 "TACC submission failed"——看起来像"发中文就出错"。
    只带 gold 的哈希，从不带明文答案。不是上传题就原样返回名字。
    """
    from flagpilot import custom
    t = custom.get(task) if task.startswith("custom-") else None
    if t is None:
        return task
    alias = "upload-" + hashlib.sha1(task.encode("utf-8")).hexdigest()[:10]
    src, dst = Path(t.assets_dir).resolve(), root / "tasks" / alias
    dst.mkdir(parents=True, exist_ok=True)
    assets = []
    for a in t.assets:
        f = (src / a).resolve()
        if f.is_file() and f.is_relative_to(src):
            (dst / a).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst / a)
            assets.append(a)
    row = {"task_id": t.task_id, "name": alias, "category": t.category, "title": t.brief_override or t.title,
           "query": t.query, "gold_sha256": t.gold_sha256, "assets": assets, "assets_dir": f"tasks/{alias}",
           "suggested_tools": [], "scope": "custom", "upload": task}
    (root / "index").mkdir(parents=True, exist_ok=True)
    (root / "index" / "tasks.jsonl").write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    return alias


def _queue_reason(jid: str) -> str:
    """作业还没开跑时问一次 Slurm 为什么：QOSMaxJobsPerUserLimit（个人并发名额满）、Resources（没空 GPU）等。

    开跑了、已结束或查不到都返回空串。每次是一趟 SSH（约 1–2 秒），只在排队时隔一阵问一次。
    """
    for line in _sdk("ps", timeout=60).splitlines():
        cols = line.split()
        if cols and cols[0] == jid and len(cols) >= 6:
            return cols[-1].strip("()") if cols[4] == "PD" else ""
    return ""


def _gone(jid: str) -> bool:
    return not any(l.split()[:1] == [jid] for l in _sdk("ps", timeout=60).splitlines())


def _cancel(jid: str) -> bool:
    """取消并核实。以前只发一次 cancel、不看结果：SDK 一抖，作业就在后台占着 GPU 和个人名额跑满 1 小时。"""
    for _ in range(3):
        _sdk("cancel", "-j", jid)
        for _ in range(3):
            if _gone(jid):
                return True
            time.sleep(float(os.environ.get("FLAGPILOT_TACC_POLL", "15")) / 5)
    _log(f"job {jid} may still be running; cancel it with: tcloud cancel -j {jid}")
    return False


STATE_FILE: Path | None = None       # serve() 设置：runs/tacc/session.json


def _save() -> None:
    """会话状态落盘（原子替换）。网页服务重启后靠它接回还在跑的服务作业。"""
    if STATE_FILE is None:
        return
    with _L:
        d = {k: S[k] for k in ("phase", "serve", "node", "url", "t0", "ready")}
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d), encoding="utf-8")
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass


def restore(path: Path) -> bool:
    """网页服务重启后接回还在跑的会话。

    服务作业不随网页进程退出：以前一重启就把它丢了，它照样在 TACC 上占着 GPU 和个人名额跑满 1 小时
    （之后的答疑作业就排在 QOSMaxJobsPerUserLimit 后面）。这里只接"就绪、没过 1 小时、TACC 上确实还在跑"的。
    """
    global STATE_FILE
    STATE_FILE = Path(path)
    try:
        d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    jid = str(d.get("serve") or "")
    if d.get("phase") == "starting" and jid and time.time() < float(d.get("t0") or 0) + 45 * 60 + SESSION_S:
        # 重启前还在启动（排队或开服）：作业还在就接着等，不再丢下一个孤儿作业白占 GPU 和名额
        state = next((c[4] for c in (l.split() for l in _sdk("ps", timeout=60).splitlines()) if c[:1] == [jid] and len(c) >= 5), "")
        if state not in ("R", "PD", "CF"):
            return False
        with _L:
            if S["phase"] != "idle":
                return False
            S.update(phase="starting", serve=jid, node=None, url="", t0=float(d["t0"]), ready=0.0, log=[], busy="", error="", queue="")
        threading.Thread(target=_serve_loop, kwargs={"adopt": (jid, float(d["t0"]))}, daemon=True).start()
        return True
    if d.get("phase") != "ready" or not jid or not d.get("url") or time.time() > float(d.get("t0") or 0) + SESSION_S:
        return False
    running = any(c[:1] == [jid] and len(c) >= 5 and c[4] == "R" for c in (l.split() for l in _sdk("ps", timeout=60).splitlines()))
    if not running:
        return False
    with _L:
        if S["phase"] != "idle":
            return False
        S.update(phase="ready", serve=jid, node=d.get("node"), url=d["url"], t0=float(d["t0"]),
                 ready=float(d.get("ready") or time.time()), log=[], busy="", error="", queue="")
    _log(f"reconnected to serve job {jid} on {d.get('node')} after a restart")
    threading.Thread(target=_watchdog, args=(jid,), daemon=True).start()
    return True


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
        S.update(phase="starting", serve=None, node=None, url="", t0=time.time(), ready=0.0, error="", log=[], queue="")
    threading.Thread(target=_serve_loop, daemon=True).start()
    return {"ok": True, "phase": "starting"}


def _fail(msg: str) -> None:
    _log(msg)
    with _L:
        S.update(phase="failed", error=msg, url="")
    _save()


def _serve_loop(adopt: tuple[str, float] | None = None) -> None:
    """起服务作业并等到就绪。adopt=(作业号, 提交时刻)：网页服务重启前已经在启动的作业，接着等，不重交。"""
    excl = os.environ.get("FLAGPILOT_TACC_EXCLUDE", BAD_NODES)
    poll = float(os.environ.get("FLAGPILOT_TACC_POLL", "15"))
    for attempt in range(1, 5):
        if adopt and attempt == 1:
            jid, t_sub = adopt
            _log(f"reconnected to serve job {jid}, which was starting before a restart")
        else:
            t_sub = time.time()
            jid, why = _submit("serve", {"FP_EXCLUDE": excl})
            if not jid:
                return _fail("serve submission failed" + (f": {why}" if why else ""))
            # 1 小时从作业真正开跑算起。看到第一行输出之前先按提交时刻算（只会偏早，安全）；
            # 看到 SDK_BOOTSTRAP 后改成"上一次还没输出的那次查询 − 激活余量"，排队多久都不吃会话时长。
            with _L:
                S.update(serve=jid, t0=t_sub)
            _save()                       # 提交了就落盘：此刻重启网页服务，也能把这个作业接回来，而不是留下孤儿作业
            _log(f"serve job {jid} submitted (attempt {attempt})")
        deadline, retry, quiet, running = time.time() + 45 * 60, False, t_sub, False
        while time.time() < deadline:
            if S["phase"] == "stopping":
                _cancel(jid)
                _log(f"serve job {jid} cancelled")
                with _L:
                    S.update(phase="idle", serve=None, queue="")
                _save()
                return
            time.sleep(poll)
            t_ask = time.time()
            L = _sdk("cat", f"slurm_log/qwen-public-serve-{jid}.log")
            if not running:
                if "SDK_BOOTSTRAP=" in L:
                    running = True
                    with _L:
                        S.update(t0=max(t_sub, quiet - BOOT_MARGIN_S), queue="")
                else:
                    quiet = t_ask
                    why = _queue_reason(jid)
                    with _L:
                        S["queue"] = why
            if S["phase"] == "stopping":                 # 睡眠期间点了断开：别再把它改回 ready
                continue
            m = re.search(r"SERVICE_BASE_URL=(http://([^:\s]+):\d+/v1)", L)
            if "PRECHECK_FAIL" in L:
                node = (re.search(r"PRECHECK_FAIL node=([^:\s]+)", L) or [None, ""])[1]
                _log(f"node {node} is busy; retrying elsewhere")
                excl = f"{excl},{node}" if node else excl
                _cancel(jid)                  # 换节点重交之前先停掉这一个：不然它照样占着名额
                retry = True
                break
            if "Application startup complete" in L and m:
                with _L:
                    if S["phase"] != "starting":
                        continue
                    S.update(phase="ready", url=m.group(1), node=m.group(2), ready=time.time(), queue="")
                _save()
                _log(f"Qwen 27B ready on {m.group(2)}")
                threading.Thread(target=_watchdog, args=(jid,), daemon=True).start()
                return
            if any(k in L for k in ("Traceback (most recent call last)", "srun: error", "CANCELLED")):
                if m and ("Free memory on device" in L or "Address already in use" in L):
                    excl = f"{excl},{m.group(2)}"
                    _log(f"node {m.group(2)} is busy; retrying elsewhere")
                    _cancel(jid)
                    retry = True
                    break
                _sdk("cancel", "-j", jid)
                return _fail(f"serve job {jid} failed")
            _log(f"waiting for serve {jid}" + (f" on {m.group(2)}" if m else " (starting)" if running
                                                 else f" (queued: {S['queue']})" if S["queue"] else " (queued)"))
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
        S.update(phase="idle", url="", serve=None, node=None, queue="")
    _save()
    ok = _cancel(jid)
    _log(f"serve job {jid} cancelled" if ok else f"serve job {jid}: cancel not confirmed")
    return {"ok": True, "cancelled": ok}


def _claim(what: str, on_wait: Callable[[str], None] | None = None, timeout: float = 20 * 60) -> str:
    """占用"客户端作业"这一个名额（平台上客户端作业共用一个工作目录，只能一个一个来）。

    被占着就排队等（以前直接报 "TACC is busy"，单题和答疑撞上就白白失败），on_wait 收到"在等谁"；
    会话结束或等太久才放弃。
    """
    t_end, told = time.time() + timeout, ""
    while True:
        with _L:
            if S["phase"] != "ready":
                return "Qwen on TACC is not connected"
            if not S["busy"]:
                S["busy"] = what
                return ""
            busy = S["busy"]
        if time.time() > t_end:
            return f"TACC stayed busy with {busy}"
        if on_wait and busy != told:
            on_wait(busy)
            told = busy
        time.sleep(min(1.0, float(os.environ.get("FLAGPILOT_TACC_TICK", "2"))))


def _release() -> None:
    with _L:
        S["busy"] = ""


REASON_EVERY_S = 15      # 排队时多久问一次 Slurm 原因


def _waiting(jid: str, started: bool, last: dict, emit: Callable[[str], None]) -> str:
    """作业等着开跑时的公共检查。返回放弃的原因（空串 = 继续等）。

    会话结束（到点或被断开）还在等的作业永远等不到服务，直接取消，免得它之后开跑、白占名额；
    还没开跑就隔一阵问一次排队原因，变了才通知。
    """
    if S["phase"] != "ready":
        _cancel(jid)
        return "the TACC session ended before the job " + ("finished" if started else "started")
    if not started and time.time() - last.get("t", 0) >= REASON_EVERY_S:
        last["t"] = time.time()
        why = _queue_reason(jid)
        if why != last.get("why"):
            last["why"] = why
            if why:
                emit(why)
    return ""


def run_task(task: str, tier: str, runs_root: Path, on_event: Callable[[dict], None]) -> Path | None:
    """单题：提交批次作业（只带这一道题），跟着日志把事件交给 on_event；结束后把运行目录下载回来。"""
    err = _claim(f"solving {task}", on_wait=lambda b: on_event({"type": "tacc", "stage": "hold", "busy": b}))
    if err:
        on_event({"type": "error", "message": err})
        return None
    try:
        batch = re.sub(r"[^a-z0-9-]", "-", f"web-{task}-{uuid.uuid4().hex[:5]}".lower())[:40]
        env = {"FP_TASKS": task, "FP_SPLIT": "all", "FP_LIMIT": "0", "FP_ABLATIONS": tier, "FP_EVENTS": "1",
               "FP_BATCH": batch, "FP_DEADLINE_EPOCH": str(int(S["t0"] + SESSION_S - 60)), "FP_EXCLUDE": S["node"] or "",
               "FP_TIME": "00:30:00"}
        on_event({"type": "tacc", "stage": "uploading"})
        with tempfile.TemporaryDirectory(prefix="fp-task-") as d:
            name = _stage_upload(task, Path(d))
            if name != task:                       # 上传的题：交它的数据集格式副本
                env.update(FP_TASKS=name, FLAGPILOT_TASKS=d)
            jid, why = _submit("batch", env, url=S["url"])
        if not jid:
            on_event({"type": "error", "message": "TACC submission failed" + (f": {why}" if why else "")})
            return None
        on_event({"type": "tacc", "stage": "queued", "job": jid})
        seen, t_end, started, last, finished = 0, time.time() + 30 * 60, False, {}, False
        while time.time() < t_end:
            time.sleep(float(os.environ.get("FLAGPILOT_TACC_TICK", "2")))
            gave_up = _waiting(jid, started, last, lambda why: on_event({"type": "tacc", "stage": "queued", "job": jid, "reason": why}))
            if gave_up:
                on_event({"type": "error", "message": gave_up})
                return None
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
                finished = True
                break
        if not finished:
            _cancel(jid)
            on_event({"type": "error", "message": "no result within 30 minutes; the job was cancelled"})
            return None
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
    err = _claim("a chat reply", on_wait=lambda b: stage({"stage": "hold", "busy": b}))
    if err:
        return {"error": err, "answer": ""}
    try:
        stage({"stage": "uploading"})
        with tempfile.TemporaryDirectory(prefix="fp-chat-") as d:
            Path(d, "msg.txt").write_text(message, encoding="utf-8")
            Path(d, "history.json").write_text(json.dumps(history[-12:], ensure_ascii=False), encoding="utf-8")
            name = _stage_upload(task, Path(d) / "tasks") if task else ""
            env = {"FP_CHAT_DIR": d, "FP_CHAT_TASK": name, "FP_CHAT_TOOLS": "1" if tools else "0",
                   "FP_EXCLUDE": S["node"] or "", "FP_TIME": "00:15:00", "FP_SPLIT": "all"}
            if task and name != task:              # 上传的题：交它的数据集格式副本
                env["FLAGPILOT_TASKS"] = str(Path(d) / "tasks")
            jid, why = _submit("chat", env, url=S["url"])
        if not jid:
            return {"error": "TACC submission failed" + (f": {why}" if why else ""), "answer": ""}
        stage({"stage": "queued", "job": jid})
        sent, seen_tools, running, t_end, last = 0, 0, False, time.time() + 15 * 60, {}
        while time.time() < t_end:
            time.sleep(float(os.environ.get("FLAGPILOT_TACC_TICK", "2")))
            gave_up = _waiting(jid, running, last, lambda why: stage({"stage": "queued", "job": jid, "reason": why}))
            if gave_up:
                return {"error": gave_up, "answer": ""}
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
        _cancel(jid)                                     # 别让它之后开跑、白占名额
        return {"error": "no reply within 15 minutes; the job was cancelled", "answer": ""}
    finally:
        _release()
