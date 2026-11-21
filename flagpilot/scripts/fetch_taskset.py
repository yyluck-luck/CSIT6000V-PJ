"""First run: fetch the evaluation challenges and build flagpilot/tasks/ (./start.sh does this once, by itself).

The challenges come from InterCode-CTF (MIT licence, picoCTF tasks). Only its data/ctf folder is downloaded (a shallow,
sparse git clone of about 100 MB); scripts/build_taskset.py then keeps the offline Forensics / Cryptography / General
Skills tasks, drops reference solutions, and writes sha256 hashes for the agent. The plaintext answers stay in
tasks/_private/, which the agent never sees and git never commits.

  python3 scripts/fetch_taskset.py      # builds flagpilot/tasks unless it is already there

The set is built next to its final place and renamed into it at the end, so a half-built set never looks ready.
Exit status 0 when the task set is ready.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = "https://github.com/princeton-nlp/intercode.git"


def run(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout or "").strip()[-400:] or f"{cmd[0]} failed")
    return r.stdout


def main() -> int:
    app = Path(__file__).resolve().parent.parent
    out = app / "tasks"
    if (out / "index" / "tasks.jsonl").is_file():
        print("The evaluation challenges are already in flagpilot/tasks.")
        return 0
    if not shutil.which("git"):
        print("git is needed to fetch the evaluation challenges.")
        return 1
    building = app / ".run" / "tasks-building"
    shutil.rmtree(building, ignore_errors=True)
    building.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="fp-intercode-") as d:
            src = Path(d) / "intercode"
            run(["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", "--quiet", REPO, str(src)])
            run(["git", "-C", str(src), "sparse-checkout", "set", "data/ctf"])
            run([sys.executable, str(app / "scripts" / "build_taskset.py"), "--src", str(src / "data" / "ctf"),
                 "--out", str(building)])
        if not (building / "index" / "tasks.jsonl").is_file():
            raise RuntimeError("the build wrote no index")
        if out.exists():                      # an empty or broken leftover: replace it
            shutil.rmtree(out)
        os.replace(building, out)
    except (RuntimeError, OSError) as e:
        shutil.rmtree(building, ignore_errors=True)
        print(f"Could not fetch the evaluation challenges: {e}")
        return 1
    n = sum(1 for line in (out / "index" / "tasks.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())
    print(f"Evaluation challenges ready: {n} in flagpilot/tasks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
