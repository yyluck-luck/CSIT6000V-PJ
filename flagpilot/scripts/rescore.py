"""Re-check finished runs with today's verifier, without touching the records.

Every attempt keeps its final submission (the flag in its trace, the script in replay/solve.py). This replays that
submission through flagpilot.verify.evaluate again and compares the verdict with the one recorded at the time, per
batch (tier), with Wilson 95% intervals. Use it after a verifier fix to see what the fix changes.

  python3 scripts/rescore.py runs/tacc/v6                # every batch under the folder
  python3 scripts/rescore.py runs/tacc/v6 --only hardcoded_no_material
  python3 scripts/rescore.py runs/tacc/v6 --csv out.csv  # one row per attempt

Attempts that never submitted (budget, errors) keep their verdict: there is nothing to replay.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.lab import wilson  # noqa: E402
from flagpilot import config, verify  # noqa: E402
from flagpilot.config import SandboxLimits  # noqa: E402
from flagpilot.tasks import load_index  # noqa: E402


def rows_of(trace: Path) -> list[dict]:
    out = []
    for line in trace.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue                     # a line cut short when the job ended
    return out


def recheck(attempt: Path, tasks: dict, tasks_root: Path, keep: Path, only: str) -> dict | None:
    rows = rows_of(attempt / "trace.jsonl")
    summ = next((r for r in rows if r.get("type") == "summary"), None)
    if summ is None:
        return None
    old_v = summ.get("verdict")
    old_r = summ.get("reason") or (summ.get("replay") or {}).get("reason") or ""
    name = attempt.name.rsplit("-s", 1)[0]
    subs = [r for r in rows if r.get("type") == "submit"]
    script = attempt / "replay" / "solve.py"
    row = {"batch": attempt.parent.name, "attempt": attempt.name, "old": old_v, "old_reason": old_r,
           "new": old_v, "new_reason": old_r}
    if only and old_r != only:
        return row
    if not subs or not script.is_file() or name not in tasks:
        return row
    res = verify.evaluate(tasks[name], subs[-1].get("flag") or "", script.read_text(encoding="utf-8", errors="replace"),
                          tasks_root, timeout_s=60, limits=SandboxLimits(action_timeout_s=60),
                          keep_dir=keep / attempt.parent.name / attempt.name / "replay")
    row.update(new=res.verdict, new_reason=res.reason)
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder", help="a folder of batches (each batch holds <task>-s<seed>/trace.jsonl)")
    ap.add_argument("--only", default="", help="re-check only attempts that ended with this reason")
    ap.add_argument("--csv", default="", help="write one row per attempt here")
    ap.add_argument("--tasks", default="", help="the task set (default: the configured one)")
    args = ap.parse_args(argv)
    tasks_root = Path(args.tasks) if args.tasks else config.TASKS_ROOT
    tasks = {t.name: t for t in load_index(tasks_root)}
    attempts = sorted(p.parent for p in Path(args.folder).glob("*/*/trace.jsonl"))
    if not attempts:
        print(f"no attempts under {args.folder}")
        return 1
    out: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="fp-rescore-") as keep:
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
