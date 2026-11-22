"""Reading what the web app needs out of TACC job logs. Pure functions: no SDK calls, easy to test.

  * marked(log, prefix): the payloads of one kind of marker line (@@ev, @@pd, @@delta, @@reply).
    `tcloud cat` can catch a job halfway through writing a line. A last line that is not yet complete JSON
    is left for the next read. Before, it was skipped and counted as read, so that event was lost for good
    (a lost summary event reads as "the TACC job returned no result").
  * why_no_result(log, jid): one English sentence on why a job that ended produced no result.
  * serve_progress(log): how far the Qwen service job has got in starting up, for the progress pop-up.
"""

from __future__ import annotations

import json
import re

_CJK = re.compile(r"[\u3000-\u9fff]")


def _json_ok(text: str) -> bool:
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def marked(log: str, prefix: str) -> list[str]:
    """Payloads of the lines that start with prefix, in order. An incomplete last line is not counted yet."""
    lines = (log or "").splitlines()
    out = [l[len(prefix):] for l in lines if l.startswith(prefix)]
    if out and lines and lines[-1].startswith(prefix) and not _json_ok(out[-1]):
        out.pop()
    return out


def parse(raw: str) -> dict | None:
    try:
        v = json.loads(raw)
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def is_log(text: str) -> bool:
    """Did `tcloud cat` return the job's log, rather than an SDK error (no session, file not there yet)?"""
    return "SDK_BOOTSTRAP=" in (text or "")


# job.sh says this when the payload (code, challenge, tools) did not arrive whole: the SDK uploads file by file, and
# with TACC's gateway dropping connections it has submitted a job whose payload never came (2026-11-21, job 30538)
PAYLOAD_MISSING = "[fp] payload missing"


def why_no_result(log: str, jid: str) -> str:
    """Why a client job that has ended produced no result, in words a user can act on. Never Chinese."""
    log = log or ""
    if PAYLOAD_MISSING in log:
        return "the upload to TACC did not arrive whole (TACC's login gateway dropped part of it); run it again"
    if "没有匹配到题目" in log:
        return "the challenge was not in the job's task set"
    m = re.search(r"\[fp\] skip B\d: (-?\d+)s left", log)
    if m:
        return (f"only {max(0, int(m.group(1)))} s of the Qwen session were left when the job started, "
                "too little for an attempt. Start a new session and run it again")
    if "[fp] service=" in log and re.search(r"urlopen error|Connection refused|timed out|RemoteDisconnected|"
                                            r"Name or service not known|No route to host", log):
        return "the Qwen service did not answer; its session may have ended. Start a new session and run it again"
    if "server-url.txt does not hold" in log:
        return "the job was given no service address"
    if "DUE TO TIME LIMIT" in log:
        return "the job reached its time limit"
    if "CANCELLED" in log:
        return "the job was cancelled on TACC"
    if not is_log(log):
        return "the job ended before it started running"
    tail = [l.strip() for l in log.splitlines()
            if l.strip() and not l.startswith("@@") and not _CJK.search(l) and not l.startswith("[fp] job=")]
    last = tail[-1][:160] if tail else "it ended without a result"
    return f"{last} (full log: tcloud cat slurm_log/flagpilot-client-{jid}.log)"


# ---------- the Qwen service's start-up, step by step ----------
# Marker = the first log line that shows the step has begun. Typical = seconds the step took in the serve jobs of
# 2026-11-21 (30221, 30235, 30425: within a few seconds of each other), used for the time-left estimate.
STEPS = (
    ("node", "GPUs assigned", r"^SERVICE_BASE_URL=", 100),
    ("vllm", "Starting vLLM", r"vLLM API server version|non-default args", 68),
    ("engine", "Starting the engine on 2 GPUs", r"Initializing a V1 LLM engine", 65),
    ("weights", "Loading the model weights", r"Filesystem type for checkpoints|Loading safetensors checkpoint", 10),
    ("compile", "Loading compiled kernels", r"Model loading took|Using cache directory", 45),
    ("warmup", "Allocating memory and warming up", r"torch\.compile took|Initial profiling/warmup", 15),
    ("server", "Opening the API", r"init engine \(profile|Starting vLLM server", 5),
    ("ready", "Ready", r"Application startup complete", 0),
)
TYPICAL_S = sum(s[3] for s in STEPS)


def serve_progress(log: str) -> dict:
    """{"step": id of the latest step reached ("" = not running yet), "detail": {...}, "tail": [...]}.

    detail carries what the log tells about the current step: the node, the GPUs' free memory before start,
    the weights' size and loading percentage, the compile time, the KV cache size.
    """
    log = log or ""
    step, detail = "", {}
    for sid, _label, pat, _t in STEPS:
        if re.search(pat, log, re.M):
            step = sid
    m = re.search(r"^SERVICE_BASE_URL=http://([^:\s]+):", log, re.M)
    if m:
        detail["node"] = m.group(1)
    gpus = re.findall(r"^PRECHECK gpu \S+ (\d+), (\d+) MiB, (\d+) MiB", log, re.M)
    if gpus:
        detail["gpus"] = f"{len(gpus)} GPUs, {int(gpus[0][2]) // 1024} GB each"
    m = re.search(r"Checkpoint size: ([\d.]+) GiB", log)
    if m:
        detail["weights_gib"] = float(m.group(1))
    pct = re.findall(r"Loading safetensors checkpoint shards:\s+(\d+)% Completed", log)
    if pct:
        detail["weights_pct"] = int(pct[-1])
    m = re.search(r"torch\.compile took ([\d.]+) s", log)
    if m:
        detail["compile_s"] = float(m.group(1))
    m = re.search(r"GPU KV cache size: ([\d,]+) tokens", log)
    if m:
        detail["kv_tokens"] = int(m.group(1).replace(",", ""))
    m = re.search(r"version (\d+\.\d+\.\d+)", log)
    if m and step:
        detail["vllm"] = m.group(1)
    tail = []
    for l in log.splitlines():
        l = re.sub(r"^\((?:APIServer|EngineCore|Worker_TP\d+) pid=\d+\)\s*", "", l).strip()
        l = re.sub(r"^(INFO|WARNING) \d\d-\d\d \d\d:\d\d:\d\d \[[^\]]+\]\s*", "", l)
        if l and not _CJK.search(l) and "█" not in l and "▀" not in l:
            tail.append(l[:200])
    return {"step": step, "detail": detail, "tail": tail[-12:]}


def eta_s(step: str, in_step_s: float) -> int:
    """Seconds still to go from step (entered in_step_s ago), using the typical step times. The current step counts
    at least 5 s while it runs (one that runs long has not finished just because its typical time is up), and the
    steps after it count in full: they have not started. (2026-11-21, job 30530: with slow shared storage the engine
    step took five times its usual time, and the panel said "about 5 s left" with four steps still to go.)"""
    if step == "ready":
        return 0
    ids = [s[0] for s in STEPS]
    if step not in ids:
        return TYPICAL_S
    i = ids.index(step)
    return int(max(5, STEPS[i][3] - in_step_s) + sum(s[3] for s in STEPS[i + 1:]))


def slow(step: str, in_step_s: float) -> bool:
    """The current step has run well past its usual time (twice it, plus half a minute)."""
    typical = next((s[3] for s in STEPS if s[0] == step), None)
    return typical is not None and step != "ready" and in_step_s > 2 * typical + 30
