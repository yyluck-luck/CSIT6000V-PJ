"""Copy an evaluation's run records into the repository, without the bulky parts.

Kept, so that every reported number can be checked offline (Runs, Lab, `flagpilot compare`, scripts/rescore.py):
  <exp>/*.md, <exp>/*.log           batch summaries, pipeline / serve / client logs (not the *-live.log copies)
  <exp>/<batch>/runs.jsonl          the ledger: one row per attempt
  <exp>/<batch>/<attempt>/trace.jsonl, writeup.md, replay/solve.py
Left out: the agent's working directory and the copies of the challenge files (they come from the task set).

  python3 scripts/export_records.py runs/tacc/v7 results/tacc/v7
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

KEEP_IN_ATTEMPT = ("trace.jsonl", "writeup.md", "replay/solve.py")


def export(src: Path, dst: Path) -> tuple[int, int]:
    if not src.is_dir():
        raise SystemExit(f"not a folder: {src}")
    files = []
    for f in sorted(src.iterdir()):
        if f.is_file() and f.suffix in (".md", ".log") and not f.name.endswith("-live.log"):
            files.append(f)
    for batch in sorted(p for p in src.iterdir() if p.is_dir() and (p / "runs.jsonl").is_file()):
        files.append(batch / "runs.jsonl")
        for attempt in sorted(p for p in batch.iterdir() if p.is_dir()):
            files += [attempt / k for k in KEEP_IN_ATTEMPT if (attempt / k).is_file()]
    if dst.exists():
        shutil.rmtree(dst)
    size = 0
    for f in files:
        out = dst / f.relative_to(src)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, out)
        size += f.stat().st_size
    return len(files), size


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-1])
        return 2
    n, size = export(Path(argv[0]), Path(argv[1]))
    print(f"{n} files, {size / 1048576:.1f} MB -> {argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
