"""Replies that broke the JSON protocol, read from finished runs' traces (README claim C4, known limitation 14).

  python3 scripts/protocol_breaks.py results/tacc/v7        # one line per batch, then the total

For every reply the agent rejected as a protocol break (an invalid_action of kind "protocol") it reports how the reply
ended (finish_reason), whether it stops inside its JSON object (a string or a bracket left open: flagpilot.tools.json_cut)
and whether an earlier reply of the same attempt was longer than the 2,000 characters the history keeps of a reply
(flagpilot.agent.REPLY_HISTORY_CHARS), so that the model had already been shown one of its own replies cut off in the
middle of its JSON. A trace keeps the first 4,000 characters of a reply: a longer broken reply counts as cut.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flagpilot import storage  # noqa: E402
from flagpilot.agent import REPLY_HISTORY_CHARS  # noqa: E402
from flagpilot.tools import json_cut  # noqa: E402

FIELDS = ("attempts", "breaks", "finish stop", "cut mid-JSON", "after a cut reply")


def rows_of(trace: Path) -> list[dict]:
    rows = []
    for line in trace.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = storage.decode_record(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(rec, dict):
            rows.append(rec)
    return rows


def scan(folder: Path) -> dict[str, Counter]:
    """{batch: counts} for every <batch>/<attempt>/trace.jsonl under folder."""
    out: dict[str, Counter] = {}
    for trace in sorted(Path(folder).glob("*/*/trace.jsonl")):
        c = out.setdefault(trace.parent.parent.name, Counter())
        c["attempts"] += 1
        rows = rows_of(trace)
        broken = {r.get("turn") for r in rows if r.get("type") == "invalid_action" and r.get("kind") == "protocol"}
        seen_cut = False                      # an earlier reply of this attempt reached the history cut short
        for r in rows:
            if r.get("type") != "model":
                continue
            text = str(r.get("text") or "")
            if r.get("turn") in broken:
                c["breaks"] += 1
                c["finish stop"] += (r.get("finish_reason") or "") == "stop"
                c["cut mid-JSON"] += json_cut(text) or len(text) >= 4000
                c["after a cut reply"] += seen_cut
            elif len(text) > REPLY_HISTORY_CHARS:
                seen_cut = True
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder", help="a folder of batches, such as results/tacc/v7")
    args = ap.parse_args(argv)
    stats = scan(Path(args.folder))
    if not stats:
        print(f"no attempts under {args.folder}")
        return 1
    total = Counter()
    print("| batch | " + " | ".join(FIELDS) + " |")
    print("|---" * (len(FIELDS) + 1) + "|")
    for batch, c in sorted(stats.items()):
        total.update(c)
        print(f"| {batch} | " + " | ".join(str(c[f]) for f in FIELDS) + " |")
    print("| all | " + " | ".join(str(total[f]) for f in FIELDS) + " |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
