"""网页的 TACC 会话：开一个 Qwen 服务作业（≤ 1 小时）常驻，单题求解与答疑每次作为小作业提交。

只走课程 SDK（submit / ps / cat / download / cancel），不建任何转发。作业把网页需要的东西按行打进
Slurm 日志（flagpilot/remote.py：@@ev / @@pd / @@delta / @@reply），这里隔几秒 `tcloud cat` 一次，
解析在 app/tacc_log.py。客户端作业在平台上用固定的 WORKDIR，同一时间只能有一个——所以求解与答疑排他（busy）。

2026-11-21 两条教训，这里每一处都照着写：
  * TACC 的登录网关忙时会直接断开 SSH 握手（"Failed to create remote session"）。提交耐心重试；
    `tcloud ps` 列不出来就是"不知道"，绝不当成"作业没了"；日志读失败就跳过这一轮，已读的事件不回退、不重放。
  * vLLM 每次启动都会打印一段带 "Traceback (most recent call last)" 的无害警告（deep_gemm 导入失败）。
    以前见到 Traceback 就当服务崩溃、取消作业——就绪前约 12 秒的窗口里看一眼，就把好好的服务杀掉
    （30270、30272、30281、30442、30446）。现在只认"作业真的结束了"：Slurm 的结束标记，或 ps 里确认没了。
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

from app import tacc_log

ROOT = Path(__file__).resolve().parent.parent
BAD_NODES = "10-0-1-10,10-0-1-11,10-0-4-10"     # 2026-11 实测有 Slurm 管不到的进程占卡 / 占 8000 端口
SESSION_S = 3600 - 180                            # 分区上限 1 小时，留 3 分钟收尾
BOOT_MARGIN_S = 60                                # 分到节点后、作业第一行输出前的激活时间（偏大估计）
MIN_RUN_S = 7 * 60       # 单题至少要剩这么多会话时间：上传 + 排队 + 激活约 2–3 分钟，作业开跑时 job.sh 还要 ≥ 240 s 才肯跑
MIN_CHAT_S = 3 * 60      # 答疑一轮：上传 + 排队 + 回答
REASON_EVERY_S = 15      # 作业等着或跑着时多久问一次 Slurm（排队原因、作业还在不在）。每次一趟 SSH，别太勤
FAST_POLL_S = 4          # 有人开着启动进度弹窗、或启动已到最后几步时，多久看一次服务日志
SUBMIT_TRIES = 6
SUBMIT_BACKOFF = (1, 2, 4, 6, 9)                  # × FLAGPILOT_TACC_RETRY_WAIT（默认 5 s）：5、10、20、30、45 s，约 2 分钟
GONE = "\x00gone"
CANCELLED = "\x00cancelled"
_L = threading.Lock()
S: dict = {"phase": "idle", "serve": None, "node": None, "url": "", "t0": 0.0, "ready": 0.0, "log": [], "busy": "", "error": "",
           "queue": "", "prog": {}, "watch": 0.0}
# 同时最多两条 SDK 连接：网关正是在一堆连接同时握手时开始断的，别自己再添一把火
_SDK_SEM = threading.BoundedSemaphore(int(os.environ.get("FLAGPILOT_TACC_SDK_PARALLEL") or 2))


def _tc() -> str:
    return os.environ.get("TCLOUD_BIN") or "tcloud"


def _submit_path() -> Path:
    return Path(os.environ.get("FLAGPILOT_TACC_SUBMIT") or ROOT / "deploy" / "tcloud" / "submit.sh")


def _tasks_root() -> Path:
    """本机题库根目录：和 submit.sh 的 ${FLAGPILOT_TASKS:-$ROOT/tasks} 同一条规则。"""
    return Path(os.environ.get("FLAGPILOT_TASKS") or ROOT / "tasks")


def _retry_wait() -> float:
    return float(os.environ.get("FLAGPILOT_TACC_RETRY_WAIT") or 5)


def _clean(text: str) -> str:
    return "\n".join(l.rstrip("\r") for l in (text or "").splitlines()
                     if "post-quantum" not in l and "store now, decrypt later" not in l and "pq.html" not in l)


def _sdk(*args: str, timeout: float = 120) -> str:
    with _SDK_SEM:
        try:
            r = subprocess.run([_tc(), *args], capture_output=True, timeout=timeout, cwd=str(ROOT))
        except (OSError, subprocess.TimeoutExpired):
            return ""
    return _clean((r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace"))


# tcloud 没能和平台建立 SSH 会话：什么都还没交，稍等再试不会重复交作业
_SESSION_FLAKE = re.compile(r"Failed to create remote session|ssh: handshake failed|Failed to dial", re.I)


def _ps(tries: int = 3) -> list[list[str]] | None:
    """`tcloud ps` 的作业行（按空白切开）。列不出来（SSH 握手失败、超时）返回 None——不能当成"没有作业"：
    以前就是这样把还在跑的作业当成已结束（取消"成功"、会话接不回来、等候脚本以为名额空了）。"""
    for i in range(max(1, tries)):
        rows = [l.split() for l in _sdk("ps", timeout=60).splitlines()]
        if any(r[:1] == ["JOBID"] for r in rows):
            return [r for r in rows if r and r[0].isdigit()]
        if i + 1 < tries:
            time.sleep(_retry_wait())
    return None


def _job_state(jid: str, tries: int = 3) -> str | None:
    """作业在 Slurm 里的状态（R、PD、CG…）；不在列表里返回空串；查不到（SDK 连不上）返回 None。"""
    rows = _ps(tries)
    if rows is None:
        return None
    return next((r[4] for r in rows if r[0] == jid and len(r) >= 5), "")


_NOT_WAITING = ("JobHeldUser", "JobHeldAdmin", "Dependency", "DependencyNeverSatisfied", "BeginTime")


def _queue_info(jid: str, rows: list[list[str]] | None = None) -> tuple[str, int]:
    """(排队原因, 前面还有几个在等的作业)。原因如 QOSMaxJobsPerUserLimit（个人并发名额满）、Resources（没空 GPU）。
    开跑了、已结束或查不到都返回 ("", 0)。"前面"按作业号数同一分区里更早交、还在等的作业（Slurm 按优先级排，只是参考）。"""
    rows = _ps(1) if rows is None else rows
    me = next((r for r in rows or [] if r[0] == jid), None)
    if not me or len(me) < 6 or me[4] != "PD":
        return "", 0
    ahead = sum(1 for r in rows if len(r) >= 6 and r[4] == "PD" and r[1] == me[1] and int(r[0]) < int(jid)
                and r[-1].strip("()") not in _NOT_WAITING)
    return me[-1].strip("()"), ahead


def _queue_reason(jid: str) -> str:
    return _queue_info(jid)[0]


def _gone(jid: str) -> bool:
    """确认作业已经不在 Slurm 里了。查不到（SDK 连不上）不算。"""
    return _job_state(jid) == ""


def _submit(mode: str, env_extra: dict, url: str = "", note: Callable[[str], None] | None = None) -> tuple[str, str]:
    """交一个作业。返回 (作业号, 失败原因)；成功时原因为空。

    原因取 submit.sh 输出的最后一行，网页直接显示（以前只有一句 "TACC submission failed"，
    看不出是题名不合法还是题目不存在）。含中文的原因不上页面，完整输出打进服务器日志。
    网关断开 SSH 握手时耐心重试（SUBMIT_TRIES 次，间隔 SUBMIT_BACKOFF 递增，合计约 2 分钟），每次重试经 note
    告诉页面在等什么；submit.sh 自己的重试关掉（FP_SUBMIT_TRIES=1），免得两层叠在一起、页面上又看不到。
    """
    env = {**os.environ, "LANG": os.environ.get("LANG") or "en_US.UTF-8", "FP_SUBMIT_TRIES": "1", **env_extra}
    wait = _retry_wait()
    out = ""
    for attempt in range(SUBMIT_TRIES):
        try:
            with _SDK_SEM:
                r = subprocess.run(["bash", str(_submit_path()), mode, *([url] if url else [])], capture_output=True,
                                   timeout=600, cwd=str(ROOT), env=env)
        except (OSError, subprocess.TimeoutExpired) as e:
            return "", type(e).__name__
        out = _clean((r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace"))
        m = re.findall(r"Submitted batch job (\d+)", out)
        if m:
            return m[-1], ""
        if attempt + 1 < SUBMIT_TRIES and _SESSION_FLAKE.search(out):
            pause = wait * SUBMIT_BACKOFF[min(attempt, len(SUBMIT_BACKOFF) - 1)]
            msg = f"TACC's login gateway dropped the connection; trying again in {pause:.0f} s ({attempt + 2}/{SUBMIT_TRIES})"
            print(f"[tacc] {mode}: {msg}", flush=True)
            if note:
                note(msg)
            time.sleep(pause)
            continue
        break
    print(f"[tacc] {mode} submission failed:\n{out[-1500:]}", flush=True)
    if _SESSION_FLAKE.search(out):
        return "", (f"tcloud could not open a session with TACC ({SUBMIT_TRIES} tries over about 2 minutes). "
                    "TACC's login gateway is overloaded: try again in a minute")
    lines = [l.strip() for l in out.splitlines() if l.strip() and not l.startswith("[tcloud]")]
    why = lines[-1][:200] if lines else ""
    return "", ("" if re.search(r"[　-鿿]", why) else why)


def _job_scope() -> str:
    """client/job.sh 选题用的 --scope。暂存的题必须标成它（见 _stage），从脚本里读：改了脚本这里跟着变。"""
    try:
        m = re.search(r"--scope\s+(\w[\w-]*)", (ROOT / "deploy" / "tcloud" / "client" / "job.sh").read_text(encoding="utf-8"))
    except OSError:
        m = None
    return m.group(1) if m else "core"


_SKIP = shutil.ignore_patterns("_private", "with-solutions", "solution", "__pycache__", ".DS_Store")


def _stage(task: str, root: Path, hint: str = "") -> str:
    """把这道题按数据集格式放进 root（root/index/tasks.jsonl + root/tasks/…），返回作业里用的名字；不需要暂存返回原名。

    两种题要暂存，否则作业里一道都选不中（只打印"没有匹配到题目"，网页上只看到"没有结果"）：
      * 上传的题（runs/custom/<名字>）：不在课程平台的题库里，换成 ASCII 别名。名字带中文（标题是中文时就会这样）
        过不了名字校验。只带 gold 的哈希，从不带明文答案。
      * 本机题库里 scope 不是作业所用 scope 的题（10 道 extra-gs）：原名照用，scope 改成作业的。
    作业一律带 --scope core 选题（deploy/tcloud/client/job.sh）：2026-11-21 上传的 babyrop 标成 custom，两次都没跑。
    hint：结果卡上"带提示重跑"。TACC 作业没有中途插话的通道，提示接在题面后面（题目也就照样暂存一份）。
    """
    from flagpilot import custom
    scope = _job_scope()
    t = custom.get(task) if task.startswith("custom-") else None
    if t is not None:
        name = "upload-" + hashlib.sha1(task.encode("utf-8")).hexdigest()[:10]
        src, dst = Path(t.assets_dir).resolve(), root / "tasks" / name
        dst.mkdir(parents=True, exist_ok=True)
        assets = []
        for a in t.assets:
            f = (src / a).resolve()
            if f.is_file() and f.is_relative_to(src):
                (dst / a).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst / a)
                assets.append(a)
        row = {"task_id": t.task_id, "name": name, "category": t.category, "title": t.brief_override or t.title,
               "query": t.query, "gold_sha256": t.gold_sha256, "assets": assets, "assets_dir": f"tasks/{name}",
               "suggested_tools": [], "scope": scope, "upload": task}
    else:
        index = _tasks_root() / "index" / "tasks.jsonl"
        try:
            rows = [json.loads(l) for l in index.read_text(encoding="utf-8").splitlines() if l.strip()]
        except (OSError, ValueError):
            return task
        row = next((r for r in rows if r.get("name") == task), None)
        if row is None or (row.get("scope") == scope and not hint):
            return task
        row, name = {**row, "scope": scope}, task
        d = row.get("assets_dir") or task
        if (_tasks_root() / d).is_dir():
            shutil.copytree(_tasks_root() / d, root / d, ignore=_SKIP, dirs_exist_ok=True)
    if hint:
        row["query"] = (row.get("query") or row.get("title") or "").rstrip() + "\n\nHint from the user: " + hint
    (root / "index").mkdir(parents=True, exist_ok=True)
    (root / "index" / "tasks.jsonl").write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    return name


_stage_upload = _stage          # 旧名字（测试和旧代码里用过）


def _preflight(root: Path, name: str, scope: str | None) -> str:
    """交作业前在本机按作业里的同一套规则选一次题。选不中就别交，直接说为什么——
    以前要白等一个作业（排队 + 激活一两分钟），最后只看到"没有结果"。空串 = 选得中。"""
    from flagpilot.tasks import load_index, select
    try:
        picked = select(load_index(Path(root)), names=[name], scope=scope)
    except (OSError, ValueError, KeyError, TypeError):
        return "the challenge list could not be read"
    if [t.name for t in picked] != [name]:
        return "the job would not find this challenge in its challenge list"
    return ""


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
    （之后的答疑作业就排在 QOSMaxJobsPerUserLimit 后面）。这里只接"没过 1 小时、TACC 上还在"的。
    SDK 一时连不上、查不到作业状态时也接：丢下一个孤儿作业的代价（白占 GPU 和名额一小时）比接回一个
    其实已经没了的会话大——后者下一次用时就会发现（看门狗、作业检查都会说清楚）。
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
        state = _job_state(jid)
        if state is not None and state not in ("R", "PD", "CF"):
            return False
        with _L:
            if S["phase"] != "idle":
                return False
            S.update(phase="starting", serve=jid, node=None, url="", t0=float(d["t0"]), ready=0.0, log=[], busy="", error="",
                     queue="", prog=_new_prog(float(d["t0"])))
        threading.Thread(target=_serve_loop, kwargs={"adopt": (jid, float(d["t0"]))}, daemon=True).start()
        return True
    if d.get("phase") != "ready" or not jid or not d.get("url") or time.time() > float(d.get("t0") or 0) + SESSION_S:
        return False
    state = _job_state(jid)
    if state is not None and state != "R":
        return False
    with _L:
        if S["phase"] != "idle":
            return False
        S.update(phase="ready", serve=jid, node=d.get("node"), url=d["url"], t0=float(d["t0"]),
                 ready=float(d.get("ready") or time.time()), log=[], busy="", error="", queue="")
    _log(f"reconnected to serve job {jid} on {d.get('node')} after a restart"
         + ("" if state else " (TACC did not answer; assuming it still runs)"))
    threading.Thread(target=_watchdog, args=(jid,), daemon=True).start()
    return True


def _log(msg: str) -> None:
    with _L:
        S["log"].append(time.strftime("%H:%M:%S ") + msg)
        del S["log"][:-60]


# ---------- start-up progress (the pop-up behind the "Starting Qwen" chip) ----------
def _new_prog(t_sub: float) -> dict:
    return {"t_sub": t_sub, "at": {}, "step": "", "detail": {}, "tail": [], "queue": "", "ahead": 0}


def _progress(p: dict) -> None:
    """记下服务日志里看到的启动进度。每一步记"第一次看到它"的本机时刻：两次查看之间跳过的步骤一并补上。"""
    ids = [s[0] for s in tacc_log.STEPS]
    now = time.time()
    with _L:
        g = S["prog"]
        if not g:
            return
        if p["step"]:
            for sid in ids[:ids.index(p["step"]) + 1]:
                g["at"].setdefault(sid, now)
        g.update(step=p["step"], detail=p["detail"], tail=p["tail"])


def _progress_view(g: dict, phase: str) -> dict:
    """给页面的进度：排队 + tacc_log.STEPS 每一步的状态（done / current / todo）与用时，以及大约还要多久。"""
    if not g:
        return {}
    now, at, cur = time.time(), g.get("at") or {}, g.get("step") or ""
    ids = [s[0] for s in tacc_log.STEPS]
    t_node = at.get("node")
    steps = [{"id": "queue", "label": "Waiting for 2 free GPUs", "state": "done" if t_node else "current",
              "s": round((t_node or now) - g.get("t_sub", now))}]
    for k, (sid, label, _pat, typical) in enumerate(tacc_log.STEPS):
        if sid not in at:
            steps.append({"id": sid, "label": label, "state": "todo", "s": typical})
            continue
        nxt = next((at[x] for x in ids[k + 1:] if x in at), None)
        done = nxt is not None or sid == "ready"
        steps.append({"id": sid, "label": label, "state": "done" if done else "current",
                      "s": round((nxt if nxt is not None else now) - at[sid]) if sid != "ready" else 0})
    eta = None
    if cur:
        eta = tacc_log.eta_s(cur, now - at.get(cur, now))
    elif t_node is None and phase == "starting":
        eta = None                                    # 还在排队：要等多久看集群，没法估
    return {"step": cur or ("queue" if phase == "starting" else ""), "steps": steps, "detail": g.get("detail") or {},
            "tail": g.get("tail") or [], "eta": eta, "typical": tacc_log.TYPICAL_S, "queue": g.get("queue") or "",
            "ahead": g.get("ahead") or 0, "elapsed": round(now - g.get("t_sub", now))}


def watch() -> dict:
    """页面开着启动进度弹窗：接下来 20 秒里每 FAST_POLL_S 秒看一次服务日志（平时 15 秒一次）。"""
    with _L:
        S["watch"] = time.time() + 20
    return status()


def status() -> dict:
    with _L:
        d = {k: (list(v) if isinstance(v, list) else v) for k, v in S.items() if k not in ("prog", "watch")}
        prog = json.loads(json.dumps(S.get("prog") or {}))
    left = (d["t0"] + SESSION_S - time.time()) if d["phase"] == "ready" else 0
    return {**d, "remaining": max(0, round(left)), "elapsed": round(time.time() - d["t0"]) if d["t0"] else 0,
            "progress": _progress_view(prog, d["phase"]), "min_run_s": MIN_RUN_S, "min_chat_s": MIN_CHAT_S}


def session_left() -> float:
    with _L:
        return (S["t0"] + SESSION_S - time.time()) if S["phase"] == "ready" else 0.0


def too_late(kind: str) -> str:
    """会话剩的时间不够这一次（kind = run | chat）时返回给人看的原因；够、或会话不在就绪状态时返回空串。"""
    need = MIN_RUN_S if kind == "run" else MIN_CHAT_S
    with _L:
        ready = S["phase"] == "ready"
    left = session_left()
    if not ready or left >= need:
        return ""
    when = f"{max(0, int(left))} s" if left < 90 else f"{round(left / 60)} min"
    return (f"This Qwen session ends in {when}, too soon for "
            + ("a run (it needs about 7 min). " if kind == "run" else "a reply (it needs about 3 min). ")
            + "Start a new session and try again")


def start() -> dict:
    with _L:
        if S["phase"] in ("starting", "ready"):
            return {"ok": True, "phase": S["phase"]}
        S.update(phase="starting", serve=None, node=None, url="", t0=time.time(), ready=0.0, error="", log=[], queue="",
                 prog=_new_prog(time.time()))
    threading.Thread(target=_serve_loop, daemon=True).start()
    return {"ok": True, "phase": "starting"}


def _fail(msg: str) -> None:
    _log(msg)
    with _L:
        S.update(phase="failed", error=msg, url="")
    _save()


def _nap(poll: float) -> None:
    """等到下一次看服务日志：平时 poll 秒；有人开着进度弹窗、或启动已到最后几步（离就绪不到一分钟）时 FAST_POLL_S 秒。"""
    t0 = time.time()
    while True:
        with _L:
            if S["phase"] == "stopping":
                return
            fast = time.time() < S["watch"] or (S.get("prog") or {}).get("step") in ("compile", "warmup", "server")
        interval = min(poll, FAST_POLL_S) if fast else poll
        left = interval - (time.time() - t0)
        if left <= 0:
            return
        time.sleep(min(0.5, left))


# 服务作业真的结束了：Slurm / srun 的结束标记，或 supervise.sh 停掉 vLLM 后的那一行。
# 绝不能用 "Traceback"：vLLM 每次启动都打印一段无害的 Traceback 警告（见模块说明）。
_SERVE_ENDED = re.compile(r"srun: error|CANCELLED AT|DUE TO TIME LIMIT|SUPERVISE stopped group|slurmstepd.*error")
# 结束时日志里有这些，说明是节点被别人占着：排除这个节点换一个重交
_NODE_BUSY = re.compile(r"Address already in use|Free memory on device .{0,80}less than desired|CUDA out of memory")


def _serve_ended_why(log: str) -> str:
    for pat, why in ((r"less than desired|CUDA out of memory", "its GPUs were already in use"),
                     (r"Address already in use", "port 8000 on its node was already in use"),
                     (r"DUE TO TIME LIMIT", "it reached the time limit"),
                     (r"CANCELLED AT", "it was cancelled on TACC")):
        if re.search(pat, log):
            return why
    errs = [l.strip() for l in log.splitlines() if re.search(r"^\S*Error: |ERROR ", l.strip()) and "WARNING" not in l]
    return (errs[-1][:160] if errs else "it ended before Qwen was ready")


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
            with _L:
                S["prog"] = _new_prog(t_sub)
            jid, why = _submit("serve", {"FP_EXCLUDE": excl}, note=_log)
            if not jid:
                return _fail("serve submission failed" + (f": {why}" if why else ""))
            # 1 小时从作业真正开跑算起。看到第一行输出之前先按提交时刻算（只会偏早，安全）；
            # 看到 SDK_BOOTSTRAP 后改成"上一次还没输出的那次查询 − 激活余量"，排队多久都不吃会话时长。
            with _L:
                S.update(serve=jid, t0=t_sub)
            _save()                       # 提交了就落盘：此刻重启网页服务，也能把这个作业接回来，而不是留下孤儿作业
            _log(f"serve job {jid} submitted (attempt {attempt})")
        deadline, retry, quiet, running, gone, last_ps = time.time() + 45 * 60, False, t_sub, False, 0, 0.0
        while time.time() < deadline:
            if S["phase"] == "stopping":
                _cancel(jid)
                _log(f"serve job {jid} cancelled")
                with _L:
                    S.update(phase="idle", serve=None, queue="")
                _save()
                return
            _nap(poll)
            if S["phase"] == "stopping":
                continue
            t_ask = time.time()
            L = _sdk("cat", f"slurm_log/qwen-public-serve-{jid}.log")
            if tacc_log.is_log(L):
                _progress(tacc_log.serve_progress(L))
            if not running:
                if "SDK_BOOTSTRAP=" in L:
                    running = True
                    with _L:
                        S.update(t0=max(t_sub, quiet - BOOT_MARGIN_S), queue="")
                else:
                    quiet = t_ask
            # 问 Slurm：排队时每次都问（原因、前面几个）；开跑后隔一阵问一次（作业还在不在）
            if not running or time.time() - last_ps >= float(os.environ.get("FLAGPILOT_TACC_ALIVE_EVERY", "60")):
                last_ps = time.time()
                rows = _ps(1)
                if rows is not None:
                    gone = 0 if any(r[0] == jid for r in rows) else gone + 1
                    if not running:
                        why, ahead = _queue_info(jid, rows)
                        with _L:
                            S["queue"] = why
                            S["prog"].update(queue=why, ahead=ahead)
            if S["phase"] == "stopping":                 # 查看期间点了断开：别再把它改回 ready
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
            if _SERVE_ENDED.search(L) or gone >= 2:
                if gone >= 2 and not _SERVE_ENDED.search(L):      # 最后再看一眼：结束前的几行可能刚落盘
                    L2 = _sdk("cat", f"slurm_log/qwen-public-serve-{jid}.log")
                    L = L2 if tacc_log.is_log(L2) else L
                if m and _NODE_BUSY.search(L):
                    excl = f"{excl},{m.group(2)}"
                    _log(f"node {m.group(2)} is busy; retrying elsewhere")
                    _cancel(jid)
                    retry = True
                    break
                _cancel(jid)
                return _fail(f"serve job {jid} stopped: {_serve_ended_why(L)}")
            with _L:
                q = S["queue"]
            _log(f"waiting for serve {jid}" + (f" on {m.group(2)}" if m else " (starting)" if running
                                                 else f" (queued: {q})" if q else " (queued)"))
        if not retry:
            _cancel(jid)
            return _fail(f"serve job {jid} not ready within 45 min (cluster busy)")
    _fail("no server after 4 attempts")


def _watchdog(jid: str) -> None:
    """就绪的会话：到 1 小时上限时收尾；每分钟问一次 Slurm 服务作业还在不在（连续两次确认没了才算），
    没了就把会话标成失败，而不是让下一次运行白等一个连不上的服务。"""
    gone, last = 0, time.time()
    while True:
        time.sleep(float(os.environ.get("FLAGPILOT_TACC_WATCH", "20")))
        with _L:
            if S["phase"] != "ready" or S["serve"] != jid:
                return
            over = time.time() > S["t0"] + SESSION_S
        if over:
            _log("session reached the 1-hour partition limit")
            stop()
            return
        if time.time() - last >= float(os.environ.get("FLAGPILOT_TACC_ALIVE_EVERY", "60")):
            last = time.time()
            st = _job_state(jid, tries=1)
            gone = gone + 1 if st == "" else 0
            if gone >= 2:
                with _L:
                    if S["phase"] != "ready" or S["serve"] != jid:
                        return
                _fail(f"the Qwen service job {jid} has ended on TACC; start a new session")
                return


def stop() -> dict:
    with _L:
        ph, jid = S["phase"], S["serve"]
        if ph == "starting":
            S["phase"] = "stopping"
            return {"ok": True}
        if ph != "ready":
            if ph == "failed":
                S.update(phase="idle", error="")
            return {"ok": True}
        S.update(phase="idle", url="", serve=None, node=None, queue="")
    _save()
    ok = _cancel(jid)
    _log(f"serve job {jid} cancelled" if ok else f"serve job {jid}: cancel not confirmed")
    return {"ok": True, "cancelled": ok}


def _claim(what: str, on_wait: Callable[[str], None] | None = None, timeout: float = 20 * 60,
           cancelled: Callable[[], bool] | None = None) -> str:
    """占用"客户端作业"这一个名额（平台上客户端作业共用一个工作目录，只能一个一个来）。

    被占着就排队等（以前直接报 "TACC is busy"，单题和答疑撞上就白白失败），on_wait 收到"在等谁"；
    会话结束、等太久或用户不要了（cancelled）才放弃，后者返回 CANCELLED。
    """
    t_end, told = time.time() + timeout, ""
    while True:
        if cancelled and cancelled():
            return CANCELLED
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
        time.sleep(min(1.0, float(os.environ.get("FLAGPILOT_TACC_TICK", "3"))))


def _release() -> None:
    with _L:
        S["busy"] = ""


def _cancel_then_release(jid: str) -> None:
    """用户点了 Finish / 撤回：页面上立刻结束，这里在后台把作业取消干净再放出名额——
    下一个作业要等这个真的停了才交，否则两个作业会在同一个工作目录里互相覆盖。"""
    try:
        _cancel(jid)
    finally:
        _release()


def _watch(jid: str, started: bool, last: dict, emit: Callable[[str], None]) -> str:
    """作业等着开跑或正在跑时的公共检查。返回放弃的原因；作业已经不在 Slurm 里了返回 GONE；空串 = 接着等。

    会话结束（到点或被断开）还在等的作业永远等不到服务，直接取消，免得它之后开跑、白占名额。
    每 REASON_EVERY_S 问一次 Slurm：还没开跑就把排队原因（变了才）告诉页面；作业从列表里消失、日志里却没有
    结束标记（被杀、节点故障、脚本中途退出）也能发现，不再干等 30 分钟。ps 没列出来不算消失；连续两次才算。
    """
    if S["phase"] != "ready":
        _cancel(jid)
        return "the TACC session ended before the job " + ("finished" if started else "started")
    if time.time() - last.get("t", 0) < REASON_EVERY_S:
        return ""
    last["t"] = time.time()
    rows = _ps(1)
    if rows is None:
        return ""
    me = next((r for r in rows if r[0] == jid), None)
    if me is None:
        last["gone"] = last.get("gone", 0) + 1
        return GONE if last["gone"] >= 2 else ""
    last["gone"] = 0
    if not started:
        why = me[-1].strip("()") if len(me) >= 6 and me[4] == "PD" else ""
        if why != last.get("why"):
            last["why"] = why
            if why:
                emit(why)
    return ""


def _tick() -> None:
    time.sleep(float(os.environ.get("FLAGPILOT_TACC_TICK", "3")))


def batch_name(task: str) -> str:
    """网页单题的批次名：≤ 40 个字符，随机后缀永远留着。以前先拼后缀再截到 40，导入题名字长，后缀被截掉，
    同一道题每次都同名，批量运行器会当成"已跑过"而跳过。"""
    head = re.sub(r"[^a-z0-9-]", "-", f"web-{task}".lower())[:34].rstrip("-")
    return f"{head}-{uuid.uuid4().hex[:5]}"


def _download(remote: str, dest: Path, tries: int = 4) -> bool:
    """tcloud download -r，带重试；落地了才算。"""
    for i in range(tries):
        _sdk("download", "-r", remote, str(dest) + "/", timeout=300)
        if (dest / Path(remote).name).exists():
            return True
        if i + 1 < tries:
            time.sleep(_retry_wait() * (i + 1))
    _log(f"could not download {remote}; fetch it later with: tcloud download -r {remote} {dest}/")
    return False


def run_task(task: str, tier: str, runs_root: Path, on_event: Callable[[dict], None],
             on_delta: Callable[[int, str], None] | None = None,
             cancelled: Callable[[], bool] | None = None, hint: str = "") -> Path | None:
    """单题：提交批次作业（只带这一道题），跟着日志把事件交给 on_event；结束后把运行目录下载回来。

    on_delta(turn, 文字)：作业日志里的 @@pd（模型逐字输出，约每秒一批）原样交给网页，不必等一整轮。
    cancelled()：用户点了 Finish。返回 None，作业在后台取消（名额等它真停了才放）。
    出错一律经 on_event 发 {"type": "error"} 并返回 None；会话快到点了还带 "renew": True（页面问要不要开新会话）。
    """
    cancelled = cancelled or (lambda: False)
    late = too_late("run")
    if late:
        on_event({"type": "error", "message": late, "renew": True})
        return None
    err = _claim(f"solving {task}", on_wait=lambda b: on_event({"type": "tacc", "stage": "hold", "busy": b}), cancelled=cancelled)
    if err == CANCELLED:
        return None
    if err:
        on_event({"type": "error", "message": err})
        return None
    release = True
    try:
        late = too_late("run")                     # 等名额可能等掉了时间
        if late:
            on_event({"type": "error", "message": late, "renew": True})
            return None
        batch = batch_name(task)
        env = {"FP_TASKS": task, "FP_SPLIT": "all", "FP_LIMIT": "0", "FP_ABLATIONS": tier, "FP_EVENTS": "1",
               "FP_BATCH": batch, "FP_DEADLINE_EPOCH": str(int(S["t0"] + SESSION_S - 60)), "FP_EXCLUDE": S["node"] or "",
               "FP_TIME": "00:30:00"}
        on_event({"type": "tacc", "stage": "uploading"})
        with tempfile.TemporaryDirectory(prefix="fp-task-") as d:
            name = _stage(task, Path(d), hint)
            staged = (Path(d) / "index" / "tasks.jsonl").exists()
            bad = _preflight(Path(d) if staged else _tasks_root(), name, _job_scope())
            if bad:
                on_event({"type": "error", "message": f"{task} cannot run on TACC: {bad}"})
                return None
            if staged:                             # 上传的题 / scope 不同的题：交它的数据集格式副本
                env.update(FP_TASKS=name, FLAGPILOT_TASKS=d)
            if cancelled():
                return None
            jid, why = _submit("batch", env, url=S["url"],
                               note=lambda m: on_event({"type": "tacc", "stage": "retrying", "message": m}))
        if not jid:
            on_event({"type": "error", "message": "TACC submission failed" + (f": {why}" if why else "")})
            return None
        on_event({"type": "tacc", "stage": "queued", "job": jid})
        seen, flags, last, L, end = {"ev": 0, "pd": 0}, {"summary": False, "started": False}, {}, "", ""
        t_end = time.time() + 30 * 60

        def drain(log: str) -> None:
            if not flags["started"] and "[fp] job=" in log:
                flags["started"] = True
                node = (re.search(r"\[fp\] job=\S+ node=(\S+)", log) or [None, ""])[1]
                on_event({"type": "tacc", "stage": "running", "job": jid, **({"node": node} if node else {})})
            evs = tacc_log.marked(log, "@@ev ")
            for raw in evs[seen["ev"]:]:
                ev = tacc_log.parse(raw)
                if ev is None:
                    continue
                flags["summary"] = flags["summary"] or ev.get("type") == "summary"
                on_event(ev)
            seen["ev"] = max(seen["ev"], len(evs))
            if on_delta is not None:
                pds = tacc_log.marked(log, "@@pd ")
                for raw in pds[seen["pd"]:]:
                    d = tacc_log.parse(raw)
                    if d is not None:
                        try:
                            on_delta(int(d.get("turn") or 0), str(d.get("d") or ""))
                        except (TypeError, ValueError):
                            pass
                seen["pd"] = max(seen["pd"], len(pds))

        while time.time() < t_end:
            _tick()
            if cancelled():
                release = False
                threading.Thread(target=_cancel_then_release, args=(jid,), daemon=True).start()
                return None
            w = _watch(jid, flags["started"], last, lambda why: on_event({"type": "tacc", "stage": "queued", "job": jid, "reason": why}))
            if w and w != GONE:
                on_event({"type": "error", "message": w})
                return None
            got = _sdk("cat", f"slurm_log/flagpilot-client-{jid}.log")
            if tacc_log.is_log(got) or "[fp] job=" in got:
                L = got                            # 读失败（SSH 断了）就沿用上一次的：已读的不回退
            drain(L)
            if "[fp] done:" in L or "srun: error" in L or "CANCELLED" in L:
                end = "done"
                break
            if w == GONE:                          # 作业没了：最后再读一次（结束前的几行可能刚落盘）
                got = _sdk("cat", f"slurm_log/flagpilot-client-{jid}.log")
                if tacc_log.is_log(got) or "[fp] job=" in got:
                    L = got
                drain(L)
                end = "gone"
                break
        if not end:
            _cancel(jid)
            on_event({"type": "error", "message": "no result within 30 minutes; the job was cancelled"})
            return None
        if not flags["summary"]:                   # 作业结束了却一次也没跑：把日志里的原因说出来，别只说"没有结果"
            on_event({"type": "error", "message": f"the TACC job {jid} ran no attempt: {tacc_log.why_no_result(L, jid)}"})
            return None
        out = Path(runs_root) / "tacc" / batch
        out.mkdir(parents=True, exist_ok=True)
        _download(f"flagpilot-runs/{batch}-{tier}", out)
        return out / f"{batch}-{tier}"
    finally:
        if release:
            _release()


def chat(message: str, history: list[dict], task: str, tools: bool,
         on_delta: Callable[[str], None], on_tool: Callable[[dict], None],
         on_stage: Callable[[dict], None] | None = None,
         cancelled: Callable[[], bool] | None = None) -> dict:
    """答疑一轮：小作业调用常驻服务；@@delta 逐段交给 on_delta，@@reply 是结果。

    on_stage 收到 uploading → queued（带作业号）→ running（带节点），网页在"三个点"展开后显示；
    提交时网关断开会收到 retrying（带说明）。cancelled()：这一问被撤回了，作业在后台取消。
    """
    stage = on_stage or (lambda ev: None)
    cancelled = cancelled or (lambda: False)
    late = too_late("chat")
    if late:
        return {"error": late, "answer": "", "renew": True}
    err = _claim("a chat reply", on_wait=lambda b: stage({"stage": "hold", "busy": b}), cancelled=cancelled)
    if err == CANCELLED:
        return {"error": "recalled", "answer": "", "cancelled": True}
    if err:
        return {"error": err, "answer": ""}
    release = True
    try:
        stage({"stage": "uploading"})
        with tempfile.TemporaryDirectory(prefix="fp-chat-") as d:
            Path(d, "msg.txt").write_text(message, encoding="utf-8")
            Path(d, "history.json").write_text(json.dumps(history[-12:], ensure_ascii=False), encoding="utf-8")
            name = ""
            if task:
                name = _stage(task, Path(d) / "tasks")
                staged = (Path(d) / "tasks" / "index" / "tasks.jsonl").exists()
                bad = _preflight(Path(d) / "tasks" if staged else _tasks_root(), name, None)   # chat-once 按名字找，不看 scope
                if bad:
                    return {"error": f"{task} cannot be used on TACC: {bad}", "answer": ""}
            env = {"FP_CHAT_DIR": d, "FP_CHAT_TASK": name, "FP_CHAT_TOOLS": "1" if tools else "0",
                   "FP_EXCLUDE": S["node"] or "", "FP_TIME": "00:15:00", "FP_SPLIT": "all"}
            if task and (Path(d) / "tasks" / "index" / "tasks.jsonl").exists():
                env["FLAGPILOT_TASKS"] = str(Path(d) / "tasks")
            if cancelled():
                return {"error": "recalled", "answer": "", "cancelled": True}
            jid, why = _submit("chat", env, url=S["url"], note=lambda m: stage({"stage": "retrying", "message": m}))
        if not jid:
            return {"error": "TACC submission failed" + (f": {why}" if why else ""), "answer": ""}
        stage({"stage": "queued", "job": jid})
        seen, running, t_end, last, L, gone_seen = {"delta": 0, "ev": 0}, False, time.time() + 15 * 60, {}, "", False
        while time.time() < t_end:
            _tick()
            if cancelled():
                release = False
                threading.Thread(target=_cancel_then_release, args=(jid,), daemon=True).start()
                return {"error": "recalled", "answer": "", "cancelled": True}
            w = _watch(jid, running, last, lambda why: stage({"stage": "queued", "job": jid, "reason": why}))
            if w and w != GONE:
                return {"error": w, "answer": ""}
            got = _sdk("cat", f"slurm_log/flagpilot-client-{jid}.log")
            if tacc_log.is_log(got) or "[fp] job=" in got:
                L = got
            if not running and "[fp] job=" in L:
                running = True
                node = (re.search(r"\[fp\] job=\S+ node=(\S+)", L) or [None, ""])[1]
                stage({"stage": "running", "job": jid, "node": node})
            deltas = tacc_log.marked(L, "@@delta ")
            for raw in deltas[seen["delta"]:]:
                try:
                    on_delta(str(json.loads(raw)))
                except ValueError:
                    pass
            seen["delta"] = max(seen["delta"], len(deltas))
            evs = tacc_log.marked(L, "@@ev ")
            for raw in evs[seen["ev"]:]:
                ev = tacc_log.parse(raw)
                if ev is not None:
                    on_tool(ev)
            seen["ev"] = max(seen["ev"], len(evs))
            rep = [r for r in (tacc_log.parse(x) for x in tacc_log.marked(L, "@@reply ")) if r is not None]
            if rep:
                return rep[-1]
            if "[fp] done: chat" in L or "srun: error" in L or "CANCELLED" in L or (w == GONE and gone_seen):
                return {"error": "the TACC job ended without a reply: " + tacc_log.why_no_result(L, jid), "answer": ""}
            gone_seen = gone_seen or w == GONE     # 作业没了：再读一轮日志（最后几行可能刚落盘）再下结论
        _cancel(jid)                                     # 别让它之后开跑、白占名额
        return {"error": "no reply within 15 minutes; the job was cancelled", "answer": ""}
    finally:
        if release:
            _release()
