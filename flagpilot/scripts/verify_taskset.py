#!/usr/bin/env python3
"""Flag verifier for the local CTF task set.

Only hashes are stored in index/tasks.jsonl; plaintext gold lives in
_private/gold_private.json and is used for nothing except the self-test.

The script normally sits next to the task set it checks (<root>/scripts/), but
the task set may also live outside this repository — pass --root to point at it:

    python3 scripts/verify_taskset.py --selftest
    python3 scripts/verify_taskset.py --root ../ctf-tasks/no-solutions --selftest
    python3 scripts/verify_taskset.py --task crypto-005 --submit 'picoCTF{...}'
    python3 scripts/verify_taskset.py --list

Exit code: 0 = pass / self-test OK, 1 = fail.
"""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "index" / "tasks.jsonl"
PRIVATE = ROOT / "_private" / "gold_private.json"


def use_root(root) -> None:
    """题库未必和这个脚本放在一起；--root 指过去即可。"""
    global INDEX, PRIVATE
    INDEX = Path(root) / "index" / "tasks.jsonl"
    PRIVATE = Path(root) / "_private" / "gold_private.json"


def sha256(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()


def load() -> list[dict]:
    return [json.loads(line) for line in INDEX.read_text().splitlines() if line.strip()]


def verify(submitted: str, expected: str, ci: bool = False) -> dict:
    """Return {pass, reason}. Empty / whitespace submissions never pass."""
    s = (submitted or "").strip()
    if not s:
        return {"pass": False, "reason": "empty"}
    if len(s) > 256:
        return {"pass": False, "reason": "too_long"}
    a, b = (s.lower(), expected) if ci else (s, expected)
    if sha256(a) == b:
        return {"pass": True, "reason": "match"}
    return {"pass": False, "reason": "wrong"}


def selftest(ci: bool = False) -> int:
    tasks = load()
    gold = json.loads(PRIVATE.read_text())
    failures = []

    for t in tasks:
        g = gold[str(t["task_id"])]
        cases = [
            ("gold", g, True),
            ("mutated", g[:-1] + ("x" if g[-1] != "x" else "y"), False),
            ("empty", "", False),
            ("garbage", "picoCTF{deadbeef}", False),
        ]
        for label, value, want in cases:
            got = verify(value, t["gold_sha256"], ci=ci)["pass"]
            if got != want:
                failures.append((t["name"], label, got))

    print(f"self-test on {len(tasks)} tasks x 4 assertions = {len(tasks) * 4} checks")
    if failures:
        print("FAILED:")
        for name, label, got in failures:
            print(f"  {name:22s} {label:8s} -> {got}")
        return 1
    print("all assertions passed: gold passes, mutated/empty/garbage fail")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", help="题库根目录（默认：本脚本上一级）")
    ap.add_argument("--task", help="task name, e.g. crypto-005")
    ap.add_argument("--submit", help="submitted flag")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--ci", action="store_true",
                    help="case-insensitive, to match InterCode's original reward check")
    args = ap.parse_args()
    if args.root:
        use_root(args.root)

    if args.selftest:
        return selftest(ci=args.ci)

    tasks = load()
    if args.list:
        for t in tasks:
            print(f"{t['name']:22s} {t['category']:18s} {t['title'][:70]}")
        return 0

    if not args.task or args.submit is None:
        ap.print_help()
        return 0

    match = [t for t in tasks if t["name"] == args.task or str(t["task_id"]) == args.task]
    if not match:
        print(f"unknown task: {args.task}")
        return 1
    res = verify(args.submit, match[0]["gold_sha256"], ci=args.ci)
    print(json.dumps({"task": match[0]["name"], "submitted": args.submit, **res},
                     ensure_ascii=False))
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
