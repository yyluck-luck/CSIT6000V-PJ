"""First run: fetch the evaluation challenges and build flagpilot/tasks/ (./start.sh does this once, by itself).

The challenges come from InterCode-CTF (MIT licence, picoCTF tasks), pinned to the commit below so that the 53-task set
stays the one the recorded results were produced on. Only its data/ctf folder is downloaded (a shallow, sparse git
clone of about 100 MB); scripts/build_taskset.py then keeps the offline Forensics / Cryptography / General Skills
tasks, drops reference solutions, and writes sha256 hashes for the agent. The plaintext answers stay in
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
# Pinned on purpose: this commit rebuilds tasks/index/tasks.jsonl byte for byte (checked 2026-11-22), so the 53-task
# set, docs/splits-picoctf-53.json and the recorded v7 results stay reproducible even if upstream moves. Build another
# set deliberately with FLAGPILOT_INTERCODE_REF=<commit>.
REF = os.environ.get("FLAGPILOT_INTERCODE_REF", "c3e46d827cfc9d4c704ec078f7abf9f41e3191d8")


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
            run(["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", "--no-checkout", "--quiet",
                 REPO, str(src)])
            try:
                run(["git", "-C", str(src), "fetch", "--depth", "1", "--quiet", "origin", REF])
            except RuntimeError as e:
                raise RuntimeError(
                    f"the pinned InterCode-CTF commit {REF[:8]} is not fetchable ({e}); "
                    f"set FLAGPILOT_INTERCODE_REF to a commit that contains data/ctf") from e
            run(["git", "-C", str(src), "checkout", "--quiet", REF])
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
    print(f"Evaluation challenges ready: {n} in flagpilot/tasks (InterCode-CTF {REF[:8]}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
