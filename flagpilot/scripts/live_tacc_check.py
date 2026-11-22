"""Check the web page's Qwen-on-TACC features against the real cluster, through the page's own API.

Needs a TACC account with the course SDK (`tcloud`) and a FlagPilot server started from this checkout
(`./start.sh`). It starts a Qwen session (two GPUs for up to an hour), so run it only when none of your jobs are on
TACC, and expect about 25 minutes. What it checks, each as PASS / FAIL:

  start-up      the session becomes ready; the panel walks through every step with an estimate of the time left;
                vLLM's harmless start-up traceback is in the log and did not stop the session
  solve         crypto-072 is verified; its events reach the page while the job runs, not all at once at the end
  scope         an uploaded challenge and an extra-gs challenge run (both once came back "no result")
  hint          a rerun's hint reaches the model
  Finish        pressed while the job is running (R in `tcloud ps`): the page is told at once, the job is cancelled
                before it finishes, the session stays up and the next run is accepted
  Ask           does not run on TACC (since 2026-11-22 it answers with the hosted models): a question for TACC is refused.
                The passes of 2026-11-21 in results/live are of the earlier version, whose four Ask checks (a Chinese and an
                English answer, a recall that cancels its job) made 20 checks; there are 17 now
  stop          ending the session leaves no job behind

  python3 scripts/live_tacc_check.py --base http://127.0.0.1:8080 --log live-check.log
Exit status 0 when every check passed.
"""

from __future__ import annotations

import argparse
import base64
import glob
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
B = "http://127.0.0.1:8080"
OUT = sys.stdout
RESULTS: list[tuple[str, bool]] = []
USER = ""                                  # TACC user, read off the serving job's row in `tcloud ps`


def log(*a) -> None:
    print(time.strftime("%H:%M:%S"), *a, file=OUT, flush=True)


def check(name: str, ok, detail="") -> None:
    RESULTS.append((name, bool(ok)))
    log("CHECK", "PASS" if ok else "FAIL", name, "|", str(detail)[:600])


def get(p: str, timeout: float = 60) -> dict:
    with urllib.request.urlopen(B + p, timeout=timeout) as r:
        return json.load(r)


def post(p: str, o: dict, timeout: float = 180) -> tuple[int, dict]:
    req = urllib.request.Request(B + p, data=json.dumps(o).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def sdk(*args: str, timeout: float = 40) -> str:
    try:
        r = subprocess.run(["tcloud", *args], capture_output=True, timeout=timeout)
        return (r.stdout + r.stderr).decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired):
        return ""                          # TACC's gateway sometimes hangs for ~140 s; the callers retry


def jobs() -> list[list[str]] | None:
    """Rows [jobid, user, state] of `tcloud ps`; None when the listing could not be read (never "no jobs")."""
    for _ in range(5):
        out = sdk("ps")
        if "JOBID" in out:
            return [[f[0], f[3], f[4]] for f in (l.split() for l in out.splitlines()) if len(f) > 4 and f[0].isdigit()]
        time.sleep(4)
    return None


def mine() -> list[list[str]] | None:
    rows = jobs()
    return None if rows is None else [r for r in rows if r[1] == USER]


def job_log(jid: str, prefix: str = "flagpilot-client") -> str:
    for _ in range(4):
        out = sdk("cat", f"slurm_log/{prefix}-{jid}.log")
        if "SDK_BOOTSTRAP" in out or "[fp] job=" in out:
            return out
        time.sleep(4)
    return ""


def wait_for(pred, limit: float, every: float = 4):
    t0 = time.time()
    while time.time() - t0 < limit:
        v = pred()
        if v:
            return v
        time.sleep(every)
    return None


def gone(jid: str) -> bool:
    rows = jobs()
    return rows is not None and not any(r[0] == jid for r in rows)


class Stream(threading.Thread):
    """One run's or Ask thread's server-sent events, read in the background, each stamped with its arrival time."""

    def __init__(self, rid: str, stop: tuple[str, ...]):
        super().__init__(daemon=True)
        self.rid, self.stop, self.evs, self.done = rid, stop, [], threading.Event()
        self.start()

    def run(self) -> None:
        try:
            with urllib.request.urlopen(B + f"/api/stream?id={self.rid}", timeout=300) as r:
                for line in r:
                    if line.startswith(b"data:"):
                        ev = json.loads(line[5:])
                        self.evs.append((time.time(), ev))
                        if ev.get("type") in self.stop:
                            break
        except Exception as e:             # noqa: BLE001 - reported; the checks judge the outcome
            log("stream", self.rid, "ended:", type(e).__name__, e)
        self.done.set()

    def job(self) -> str | None:
        return next((e.get("job") for _, e in self.evs if e.get("job")), None)

    def count(self, kind: str) -> int:
        return sum(1 for _, e in self.evs if e.get("type") == kind)

    def last(self, kinds: tuple[str, ...]) -> dict:
        return next((e for _, e in reversed(self.evs) if e.get("type") in kinds), {})


def run(task: str, hint: str = "", limit: float = 1500) -> Stream | None:
    body = {"task": task, "backend": "tacc", "ablate": "B3", **({"hint": hint} if hint else {})}
    code, r = post("/api/run", body)
    if code != 200:
        log("run", task, "refused:", code, r)
        return None
    st = Stream(r["run_id"], ("done",))
    st.done.wait(limit)
    d = st.last(("done",))
    log("run", task, "->", d.get("verdict"), "|", d.get("reason", ""), f"| {len(st.evs)} events")
    return st


def startup() -> dict | None:
    global USER
    log("start:", post("/api/tacc/session/start", {}))
    t0, steps, etas, last, s = time.time(), [], [], None, {}
    while time.time() - t0 < 45 * 60:
        s = get("/api/tacc/session?watch=1")
        g = s.get("progress") or {}
        if g.get("step") != last:
            last = g.get("step")
            steps.append(last)
            log(f"  step {last!r} after {round(time.time() - t0)} s, estimate {g.get('eta')} s, detail {g.get('detail')}")
        if g.get("eta") is not None:
            etas.append(g["eta"])
        if s.get("phase") in ("ready", "failed", "idle"):
            break
        time.sleep(4)
    check("the Qwen session becomes ready", s.get("phase") == "ready",
          f"{s.get('phase')} after {round(time.time() - t0)} s {s.get('error') or ''}")
    if s.get("phase") != "ready":
        log("session log:", s.get("log", [])[-10:])
        return None
    done = [x["id"] for x in s["progress"]["steps"] if x["state"] == "done"]
    log("step times:", [(x["id"], x["s"]) for x in s["progress"]["steps"]])
    check("the start-up panel walked through every step", done[-1:] == ["ready"] and len(done) >= 8, done)
    check("the start-up panel gave an estimate of the time left", bool(etas), f"{len(etas)} readings, first {etas[:3]}")
    slog = job_log(s["serve"], "qwen-public-serve")
    check("vLLM's harmless traceback was in the log and did not stop the session", "Traceback" in slog,
          f"serving job {s['serve']}: {slog.count('Traceback')} traceback(s), session {s.get('phase')}")
    USER = next((r[1] for r in jobs() or [] if r[0] == s["serve"]), "")
    log("serving job", s["serve"], "on", s.get("node"), "user", USER or "?")
    return s


def finish_while_running(serve: str) -> None:
    code, r = post("/api/run", {"task": "crypto-092", "backend": "tacc", "ablate": "B3"})
    if code != 200:
        return check("Finish: run accepted", False, f"{code} {r}")
    st = Stream(r["run_id"], ("done",))
    jid = wait_for(st.job, 900, 1)
    running = wait_for(lambda: any(x[0] == jid and x[2] == "R" for x in jobs() or []), 900, 3)
    # let it work until the page shows some of its output: after Slurm says R the job can take about a minute to
    # activate its environment before the model's first turn (the first check of 2026-11-21 waited only 45 s)
    wait_for(lambda: st.count("model") >= 2, 150, 1)
    seen = st.count("model")
    t_fin = time.time()
    c, fr = post("/api/run/control", {"id": r["run_id"], "op": "finish"})
    st.done.wait(30)
    lag = round(time.time() - t_fin, 1)
    left = wait_for(lambda: gone(jid), 300, 3)
    after = round(time.time() - t_fin) if left else None
    L = job_log(jid) if jid else ""
    d = st.last(("done",))
    check("Finish was pressed while the client job was running", bool(running), f"job {jid}; Finish -> {c} {fr}")
    check("Finish: the page is told at once ('stopped')", d.get("verdict") == "stopped" and lag < 5,
          f"{d.get('verdict')} {d.get('reason', '')} after {lag} s")
    check("Finish: the job is cancelled before it finishes", left and "[fp] tier-done" not in L,
          f"gone {after} s after Finish; finished anyway: {'[fp] tier-done' in L}")
    check("the model's output reached the page while the job ran", seen >= 1, f"{seen} model events before Finish")
    s = get("/api/tacc/session")
    check("Finish keeps the session", s.get("phase") == "ready" and s.get("serve") == serve, s.get("phase"))


def main() -> None:
    s = startup()
    if not s:
        return
    serve = s["serve"]

    st = run("crypto-072")
    d = st.last(("done",)) if st else {}
    check("crypto-072 is verified on TACC", d.get("verdict") == "verified", d.get("verdict"))
    if st:
        t_run = next((t for t, e in st.evs if e.get("stage") == "running"), None)
        times = [t for t, e in st.evs if e.get("type") in ("model", "tool", "submit", "replay")]
        spread = round(times[-1] - times[0], 1) if len(times) > 1 else 0.0
        check("its events reached the page over the run, not all at once", spread >= 5,
              f"first to last event {spread} s; 'running' shown {round(times[0] - t_run, 1) if t_run and times else '?'} s before the first")

    flag = "picoCTF{live_check_" + time.strftime("%H%M%S") + "}"
    blob = base64.b64encode(base64.b64encode(flag.encode())).decode()
    code, up = post("/api/upload", {"title": "live check upload", "category": "Cryptography", "gold": flag,
                                    "query": "The attached file holds the flag, encoded twice. Recover it.",
                                    "files": [{"name": "secret.txt", "data_b64": blob}]})
    name = (up.get("task") or {}).get("name", "")
    if name:
        try:
            st = run(name)
            d = st.last(("done",)) if st else {}
            check("an uploaded challenge runs on TACC and is verified", d.get("verdict") == "verified",
                  f"{d.get('verdict')} {d.get('reason', '')}")
        finally:
            post("/api/custom/delete", {"task": name})
    else:
        check("an uploaded challenge runs on TACC and is verified", False, f"upload refused: {code} {up}")

    st = run("general-026")
    d = st.last(("done",)) if st else {}
    check("an extra-gs challenge (general-026) runs on TACC", d.get("verdict") not in (None, "error", "refused"),
          f"{d.get('verdict')} {d.get('reason', '')}")

    t_hint = time.time()
    st = run("crypto-005", hint="LIVE-CHECK-HINT: print the final flag from solve.py.")
    files = [p for p in glob.glob(str(APP / "runs" / "tacc" / "web-crypto-005-*" / "**" / "*"), recursive=True)
             if p.endswith((".jsonl", ".md")) and os.path.getmtime(p) >= t_hint - 5]
    found = any("LIVE-CHECK-HINT" in Path(p).read_text(encoding="utf-8", errors="replace") for p in files)
    check("a rerun's hint reaches the model", found, f"{len(files)} record files of the run")

    finish_while_running(serve)
    st = run("crypto-072")
    d = st.last(("done",)) if st else {}
    check("the next run after Finish is accepted and verified", d.get("verdict") == "verified", d.get("verdict"))

    code, c = post("/api/chat", {"message": "In one sentence: what is a nonce?", "backend": "tacc", "tools": False})
    check("Ask does not run on TACC: a question for TACC is refused before anything is submitted",
          code == 400 and "hosted models" in str(c.get("error")),
          f"{code} {c}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", default=B, help="the FlagPilot server (default %(default)s)")
    ap.add_argument("--log", help="also append the report to this file")
    a = ap.parse_args()
    B = a.base.rstrip("/")
    if a.log:
        OUT = open(a.log, "a", buffering=1, encoding="utf-8")
    try:
        main()
    except Exception as e:                 # noqa: BLE001
        log("CRASH", type(e).__name__, e)
        RESULTS.append(("the check ran to the end", False))
    finally:
        log("stop:", post("/api/tacc/session/stop", {}))
        if USER:
            left = wait_for(lambda: mine() == [], 300, 5)
            check("ending the session leaves no job behind", left is not None, mine())
        n = sum(ok for _, ok in RESULTS)
        log(f"SUMMARY {n}/{len(RESULTS)} checks passed")
        for nm, ok in RESULTS:
            log("  ", "PASS" if ok else "FAIL", nm)
    sys.exit(0 if RESULTS and n == len(RESULTS) else 1)
