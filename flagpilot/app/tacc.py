"""网页 → TACC：从 Lab 页启动 deploy/tcloud/pipeline.sh（只走课程官方 tcloud SDK），读它的日志出进度，
跑完把每一档的结果挂进回放（Runs 页可逐步复盘）。

为什么不让网页直接调集群上的 vLLM：课程规定不登录平台服务器、不自建 SSH 转发，笔记本到计算节点
的内网地址根本不通。所以网页这一侧只做"发起 + 观察 + 取回"，交互式解题仍用本机配置的模型。

进度从三处读：流水线自己的输出（阶段、作业号、节点）、它每轮轮询时落盘的 client-live.log
（逐题结论）与 serve-live.log（开服进度）。页面只显示解析后的结构化字段，不直接回显原始日志。
同一时间只允许一条流水线（课程平台上客户端作业用固定 WORKDIR，并发会互相覆盖）。
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from flagpilot import storage

ROOT = Path(__file__).resolve().parent.parent
TIERS_RE = re.compile(r"^B[0-3](,B[0-3])*$")
BATCH_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
NODES_RE = re.compile(r"^([A-Za-z0-9._-]+(,[A-Za-z0-9._-]+)*)?$")
TASKS_RE = re.compile(r"^([A-Za-z0-9._-]+(,[A-Za-z0-9._-]+)*)?$")
ATTEMPT_RE = re.compile(r"^\[(\d+)/(\d+)\]\s+(\S+)\s+(VERIFIED|unverified|wrong|budget|ERROR|reproduced)\s+turns=(\d+)\s+([\d.]+)s")
_LOCK = threading.Lock()
_S: dict = {"proc": None, "batch": "", "tiers": [], "out": "", "lines": [], "t0": 0.0, "t1": 0.0, "rc": None, "mounted": 0,
            "tasks": ""}


def pipeline_path() -> Path:
    # 测试用 FLAGPILOT_TACC_PIPELINE 换成假流水线；平时就是仓库里的那一个
    return Path(os.environ.get("FLAGPILOT_TACC_PIPELINE") or ROOT / "deploy" / "tcloud" / "pipeline.sh")


def available() -> dict:
    sdk = shutil.which(os.environ.get("TCLOUD_BIN") or "tcloud")
    return {"sdk": bool(sdk) or "FLAGPILOT_TACC_PIPELINE" in os.environ, "pipeline": pipeline_path().exists()}


def start(batch: str, tiers: str, limit: int, runs_root: Path, on_done: Callable[[Path], int],
          exclude: str = "", tasks: str = "") -> dict:
    tiers = (tiers or "").replace(" ", "")
    if not BATCH_RE.match(batch or ""):
        return {"error": "batch name: lowercase letters, digits and dashes"}
    if not TIERS_RE.match(tiers):
        return {"error": "tiers: e.g. B3 or B0,B3"}
    if not NODES_RE.match(exclude or ""):
        return {"error": "exclude: node names separated by commas"}
    if not TASKS_RE.match(tasks or ""):
        return {"error": "tasks: challenge names separated by commas"}
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return {"error": "limit must be an integer (0 = whole dev split)"}
    if not 0 <= limit <= 200:
        return {"error": "limit must be between 0 and 200"}
    av = available()
    if not (av["sdk"] and av["pipeline"]):
        return {"error": "tcloud SDK not found on this machine (see docs/TACC.md)"}
    with _LOCK:
        if _S["proc"] is not None and _S["proc"].poll() is None:
            return {"error": "a TACC pipeline is already running"}
        out = Path(runs_root) / "tacc" / batch
        out.mkdir(parents=True, exist_ok=True)
        # 网页服务常由没有 LANG 的环境拉起；bash 在非 UTF-8 locale 下会把紧跟变量名的多字节字符读进变量名
        env = {**os.environ, "LANG": os.environ.get("LANG") or "en_US.UTF-8",
               "FP_BATCH": batch, "FP_ABLATIONS": tiers, "FP_LIMIT": str(limit),
               "POLL": os.environ.get("FLAGPILOT_TACC_POLL", "20")}
        if tasks:                       # 指定题目（Solve 页的 "Run on TACC"）：不受 dev 划分限制
            env.update({"FP_TASKS": tasks, "FP_SPLIT": "all", "FP_LIMIT": "0"})
        proc = subprocess.Popen(["bash", str(pipeline_path()), str(out), exclude], cwd=str(ROOT), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                                start_new_session=True)
        _S.update({"proc": proc, "batch": batch, "tiers": tiers.split(","), "out": str(out), "lines": [],
                   "t0": time.time(), "t1": 0.0, "rc": None, "mounted": 0, "tasks": tasks})
    threading.Thread(target=_pump, args=(proc, out, on_done), daemon=True).start()
    return {"ok": True, "batch": batch}


def _pump(proc: subprocess.Popen, out: Path, on_done: Callable[[Path], int]) -> None:
    with proc.stdout:                             # type: ignore[union-attr]
        for line in proc.stdout:                  # type: ignore[union-attr]
            with _LOCK:
                _S["lines"].append(line.rstrip("\n")[:400])
                del _S["lines"][:-600]
    rc = proc.wait()
    mounted = 0
    try:
        mounted = on_done(out)
    except Exception:
        mounted = 0
    with _LOCK:
        _S.update({"rc": rc, "t1": time.time(), "mounted": mounted})


def stop() -> dict:
    """停流水线：发 SIGTERM，它的 trap 会取消服务作业（不留空转的 GPU）。"""
    with _LOCK:
        proc = _S["proc"]
    if proc is None or proc.poll() is not None:
        return {"error": "no pipeline is running"}
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.terminate()
    return {"ok": True}


def _phase(lines: list[str], running: bool, rc) -> str:
    text = "\n".join(lines)
    if not running and rc is not None:
        return "done" if rc == 0 else "failed"
    for key, ph in (("cancel serve job", "cleanup"), ("local: ", "cleanup"), ("batch finished", "download"),
                    ("waiting for batch", "batch"), ("client job", "batch"), ("serve ready", "ready"),
                    ("waiting for serve", "serve"), ("serve job", "queued")):
        if key in text:
            return ph
    return "starting" if running else "idle"


def _attempts(out: Path, tiers: list[str]) -> tuple[list[dict], dict]:
    """client-live.log → 逐题结论；按 "[fp] tier Bx" 分段归到各档。"""
    p = out / "client-live.log"
    rows: list[dict] = []
    per = {t: {"done": 0, "total": 0, "verified": 0, "finished": False, "secs": None} for t in tiers}
    if not p.exists():
        return rows, per
    tier = tiers[0] if len(tiers) == 1 else ""
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\[fp\] tier (B[0-3]) ", line)
        if m:
            tier = m.group(1)
            continue
        m = re.match(r"^\[fp\] tier-done \S+-(B[0-3]) (\d+)s", line)
        if m and m.group(1) in per:
            per[m.group(1)].update(finished=True, secs=int(m.group(2)))
            continue
        m = re.match(r"^\[fp\] skip (B[0-3]):", line)
        if m and m.group(1) in per:
            per[m.group(1)]["skipped"] = True
            continue
        m = ATTEMPT_RE.match(line)
        if m and tier in per:
            k, n, name, verdict, turns, secs = m.groups()
            per[tier].update(done=int(k), total=int(n))
            per[tier]["verified"] += verdict == "VERIFIED"
            rows.append({"tier": tier, "task": name, "verdict": verdict.lower(), "turns": int(turns), "secs": float(secs)})
    return rows[-80:], per


def _serve_info(out: Path) -> str:
    p = out / "serve-live.log"
    if not p.exists():
        return ""
    hits = re.findall(r"(torch\.compile took [0-9.]+ s|GPU KV cache size: [0-9,]+ tokens|Application startup complete)",
                      p.read_text(encoding="utf-8", errors="replace"))
    return hits[-1] if hits else ""


def summarize_tier(d: Path) -> dict | None:
    """一档的结果（runs.jsonl）：n、verified、平均轮次、墙钟中位数。"""
    f = d / "runs.jsonl"
    if not f.exists():
        return None
    try:
        rows = [r for r in storage.read_records(f) if r.get("verdict")]
    except Exception:
        return None
    if not rows:
        return None
    walls = sorted(float(r.get("wall_s") or 0) for r in rows)
    # 回放 id 与 server._mount_replay 的算法一致（运行目录绝对路径的 sha1 前 12 位），页面据此直接打开回放
    runs = [{"id": hashlib.sha1(str((d / r["run_id"]).resolve()).encode()).hexdigest()[:12], "task": r.get("task"),
             "verdict": r.get("verdict"), "turns": r.get("turns")} for r in rows if r.get("run_id") and (d / r["run_id"]).is_dir()]
    return {"n": len(rows), "verified": sum(r["verdict"] == "verified" for r in rows), "runs": runs[:60],
            "turns": round(sum(int(r.get("turns") or 0) for r in rows) / len(rows), 2),
            "wall_p50": walls[len(walls) // 2], "model": next((r.get("model") for r in rows if r.get("model")), "")}


def latest_results(runs_root: Path) -> dict:
    """最近一次完成的 TACC 批次（按目录修改时间），供空闲时展示。"""
    base = Path(runs_root) / "tacc"
    if not base.exists():
        return {}
    best = None
    for d in base.iterdir():
        tiers = sorted(p for p in d.glob("*-B[0-3]") if (p / "runs.jsonl").exists()) if d.is_dir() else []
        if tiers and (best is None or d.stat().st_mtime > best[0]):
            best = (d.stat().st_mtime, d, tiers)
    if not best:
        return {}
    _, d, tiers = best
    return {"batch": d.name, "finished": best[0],
            "tiers": {p.name.rsplit("-", 1)[1]: summarize_tier(p) for p in tiers}}


def status(runs_root: Path) -> dict:
    with _LOCK:
        proc, lines = _S["proc"], list(_S["lines"])
        snap = {k: _S[k] for k in ("batch", "tiers", "out", "t0", "t1", "rc", "mounted", "tasks")}
    running = proc is not None and proc.poll() is None
    out = Path(snap["out"]) if snap["out"] else None
    rows, per = _attempts(out, snap["tiers"]) if out else ([], {})
    jobs = {"serve": None, "client": None, "node": None}
    for ln in lines:
        m = re.search(r"serve job (\d+) submitted", ln)
        if m:
            jobs["serve"] = m.group(1)
        m = re.search(r"client job (\d+) submitted", ln)
        if m:
            jobs["client"] = m.group(1)
        m = re.search(r"serve ready: \S+ \(job \d+, node ([\w.-]+)\)", ln)
        if m:
            jobs["node"] = m.group(1)
    results = {}
    if out and not running:
        for t in snap["tiers"]:
            s = summarize_tier(out / f"{snap['batch']}-{t}")
            if s:
                results[t] = s
    english = [ln for ln in lines if not re.search(r"[　-鿿]", ln)]
    return {"available": available(), "running": running, "phase": _phase(lines, running, snap["rc"]),
            "batch": snap["batch"], "tiers": snap["tiers"], "tasks": snap["tasks"], "jobs": jobs, "serve_info": _serve_info(out) if out else "",
            "elapsed": round((snap["t1"] or time.time()) - snap["t0"], 1) if snap["t0"] else 0,
            "attempts": rows, "per_tier": per, "results": results, "mounted": snap["mounted"], "rc": snap["rc"],
            "log": english[-40:], "latest": latest_results(runs_root) if not running else {}}
