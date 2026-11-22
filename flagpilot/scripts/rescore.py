"""Re-check finished runs with today's verifier, without touching the records.

Every attempt's trace keeps each submission it made (flag and script). This replays them through
flagpilot.verify.evaluate in order, as the agent made them: the first one today's verifier accepts is where the attempt
would have ended; with none accepted, the attempt is judged on its last submission, or keeps the ending it had when it
went on to its budget. The verdicts are compared with the ones recorded at the time, per batch (tier), with Wilson 95%
intervals. Use it after a verifier fix to see what the fix changes.

Until 2026-11-21 this paired the last flag with replay/solve.py, which holds the last script that reached the replay,
not the last one submitted (a typed-in flag is rejected before the replay). On macOS, zcat reads only .Z files while the
TACC jobs ran GNU zcat, and macOS's strings passes non-ASCII bytes through: GNU-like ones go first on the PATH the
replays inherit.

  python3 scripts/rescore.py runs/tacc/v6                # every batch under the folder
  python3 scripts/rescore.py runs/tacc/v6 --only hardcoded_no_material
  python3 scripts/rescore.py runs/tacc/v6 --csv out.csv  # one row per attempt

The replays run the python3 on PATH. The cluster's jobs carried pycryptodome and gmpy2 (deploy/tcloud/submit.sh): a
python3 without them fails every script that imports them, and verified attempts read as script_error (2026-11-22:
six crypto-079 attempts did). The script stops unless that python3 has both, or --lenient is given.

Attempts that never submitted (budget, errors) keep their verdict: there is nothing to replay.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.lab import wilson  # noqa: E402
from flagpilot import config, verify  # noqa: E402
from flagpilot.config import SandboxLimits  # noqa: E402
from flagpilot.sandbox import probe_capabilities  # noqa: E402

VENDORED = ("Crypto", "gmpy2")       # what the TACC jobs carried (FP_VENDOR, deploy/tcloud/submit.sh)


def replay_python_lacks() -> tuple[str, list[str]]:
    """The python3 the replays use, and which of the cluster's vendored modules it cannot import."""
    py = probe_capabilities().python
    return py, [m for m in VENDORED if subprocess.run([py, "-c", f"import {m}"], capture_output=True).returncode != 0]
from flagpilot.tasks import load_index  # noqa: E402


def rows_of(trace: Path) -> list[dict]:
    out = []
    for line in trace.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue                     # a line cut short when the job ended
    return out


SUBMISSION_ENDED = ("verified", "unverified", "wrong", "reproduced")     # verdicts an attempt gets from its last submission


def recheck(attempt: Path, tasks: dict, tasks_root: Path, keep: Path, only: str) -> dict | None:
    rows = rows_of(attempt / "trace.jsonl")
    summ = next((r for r in rows if r.get("type") == "summary"), None)
    if summ is None:
        return None
    old_v = summ.get("verdict")
    old_r = summ.get("reason") or (summ.get("replay") or {}).get("reason") or ""
    name = attempt.name.rsplit("-s", 1)[0]
    subs = [r for r in rows if r.get("type") == "submit"]
    saved = attempt / "replay" / "solve.py"
    row = {"batch": attempt.parent.name, "attempt": attempt.name, "old": old_v, "old_reason": old_r,
           "new": old_v, "new_reason": old_r, "submit": ""}
    if only and old_r != only:
        return row
    if not subs or name not in tasks:
        return row
    last = None
    for i, sub in enumerate(subs, 1):
        script = sub.get("script")
        if script is None:                 # a trace without scripts: only the last one, kept next to it, can be judged
            if i < len(subs) or not saved.is_file():
                continue
            script = saved.read_text(encoding="utf-8", errors="replace")
        last = verify.evaluate(tasks[name], sub.get("flag") or "", script, tasks_root, timeout_s=60,
                               limits=SandboxLimits(action_timeout_s=60),
                               keep_dir=keep / attempt.parent.name / attempt.name / f"replay-{i}")
        if last.verdict == "verified":     # the attempt would have ended here
            row.update(new="verified", new_reason=last.reason, submit=i)
            return row
    if last is not None and old_v in SUBMISSION_ENDED:
        row.update(new=last.verdict, new_reason=last.reason, submit=len(subs))
    return row


# GNU strings' defaults: the whole file, runs of at least 4 printable ASCII characters or tabs (macOS's strings also
# passes bytes >= 0x80 through, so a script that decodes its output as UTF-8 fails there: v7 B1 forensics-065)
STRINGS = r"""#!/usr/bin/env python3
import re, sys
args, files, n, radix, i = sys.argv[1:], [], 4, None, 0
while i < len(args):
    a = args[i]
    if a == "-n" or a == "-t":
        i += 1
        if a == "-n": n = int(args[i])
        else: radix = args[i]
    elif a.startswith("--bytes="): n = int(a.split("=", 1)[1])
    elif re.fullmatch(r"-n\d+|-\d+", a): n = int(a.lstrip("-n"))
    elif re.fullmatch(r"-t[odx]", a): radix = a[2]
    elif not a.startswith("-") or a == "-": files.append(a) if a != "-" else None
    i += 1
pat, out = re.compile(rb"[\x20-\x7e\t]{%d,}" % n), sys.stdout.buffer
for data in ([open(f, "rb").read() for f in files] if files else [sys.stdin.buffer.read()]):
    for m in pat.finditer(data):
        out.write((({"o": b"%7o ", "d": b"%7d ", "x": b"%7x "}[radix] % m.start()) if radix else b"") + m.group() + b"\n")
"""


def tools_like_linux(tmp: Path) -> bool:
    """The TACC jobs ran GNU tools; on macOS two that the recorded scripts call behave differently: zcat reads only .Z
    files (GNU zcat reads gzip) and strings passes non-ASCII bytes through. Put GNU-like ones first on the PATH (the
    sandbox passes the PATH on), so the replays behave as they did on the cluster."""
    if sys.platform != "darwin" or not shutil.which("gzip"):
        return False
    d = Path(tmp) / "linux-bin"
    d.mkdir(parents=True, exist_ok=True)
    for name, body in (("zcat", '#!/bin/sh\nexec gzip -dc "$@"\n'), ("strings", STRINGS)):
        (d / name).write_text(body)
        (d / name).chmod(0o755)
    os.environ["PATH"] = f"{d}{os.pathsep}{os.environ.get('PATH', '')}"
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder", help="a folder of batches (each batch holds <task>-s<seed>/trace.jsonl)")
    ap.add_argument("--only", default="", help="re-check only attempts that ended with this reason")
    ap.add_argument("--csv", default="", help="write one row per attempt here")
    ap.add_argument("--tasks", default="", help="the task set (default: the configured one)")
    ap.add_argument("--lenient", action="store_true", help="go on although the replays' python3 lacks pycryptodome or gmpy2")
    args = ap.parse_args(argv)
    py, lacking = replay_python_lacks()
    if lacking:
        print(f"the replays run {py}, which cannot import {', '.join(lacking)} (pycryptodome / gmpy2, carried by the cluster's "
              "jobs): scripts that need them would fail here and not there. Put a python3 that has them first on PATH, "
              "or pass --lenient.", file=sys.stderr)
        if not args.lenient:
            return 2
    tasks_root = Path(args.tasks) if args.tasks else config.TASKS_ROOT
    tasks = {t.name: t for t in load_index(tasks_root)}
    attempts = sorted(p.parent for p in Path(args.folder).glob("*/*/trace.jsonl"))
    if not attempts:
        print(f"no attempts under {args.folder}")
        return 1
    out: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="fp-rescore-") as keep:
        if tools_like_linux(Path(keep)):
            print("macOS: zcat and strings behave as the GNU ones on the cluster did")
        for a in attempts:
            r = recheck(a, tasks, tasks_root, Path(keep), args.only)
            if r is None:
                continue
            out.append(r)
            if (r["old"], r["old_reason"]) != (r["new"], r["new_reason"]):
                print(f"  {r['batch']}/{r['attempt']}: {r['old']}/{r['old_reason']} -> {r['new']}/{r['new_reason']}", flush=True)
    print()
    print(f"| batch | n | verified then | 95% CI | verified now | 95% CI |")
    print(f"|---|---|---|---|---|---|")
    for batch in sorted({r["batch"] for r in out}):
        rs = [r for r in out if r["batch"] == batch]
        n, a, b = len(rs), sum(r["old"] == "verified" for r in rs), sum(r["new"] == "verified" for r in rs)
        (al, ah), (bl, bh) = wilson(a, n), wilson(b, n)
        print(f"| {batch} | {n} | {a} ({a / n:.1%}) | {al:.1%}–{ah:.1%} | {b} ({b / n:.1%}) | {bl:.1%}–{bh:.1%} |")
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(out[0]))
            w.writeheader()
            w.writerows(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
