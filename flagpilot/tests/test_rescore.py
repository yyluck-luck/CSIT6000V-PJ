"""scripts/rescore.py: what today's verifier says about the submissions an attempt actually made.

2026-11-21: it used to pair the attempt's last flag with replay/solve.py, which holds the last script that reached the
replay, not the last one submitted (a typed-in flag is rejected before the replay and never written there). The
counterfactual it reports is now replayed in order: the first submission today's verifier accepts is where the attempt
would have ended; with none accepted, the attempt is judged on its last submission as before.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flagpilot.tasks import Task  # noqa: E402

FLAG = "picoCTF{rescore_check_42}"
SPEC = importlib.util.spec_from_file_location("rescore", Path(__file__).resolve().parent.parent / "scripts" / "rescore.py")
rescore = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rescore)

DECODE = "import base64\nprint(base64.b64decode(open('challenge/data.txt').read().split('=', 1)[1].strip()).decode())\n"
TYPED = f"print('{FLAG}')\n"
WRONG_OUTPUT = "print(42)\n"


def make_task(root: Path) -> Task:
    d = root / "tasks" / "synthetic-777"
    d.mkdir(parents=True)
    (d / "data.txt").write_text("blob=" + base64.b64encode(FLAG.encode()).decode() + "\n")
    return Task(task_id=777, name="synthetic-777", category="Synthetic", title="synthetic", query="Decode the file.",
                gold_sha256=hashlib.sha256(FLAG.encode()).hexdigest(), assets=["data.txt"],
                assets_dir="tasks/synthetic-777", suggested_tools=[], scope="core")


class TestRescore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-rescore-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.task = make_task(self.tmp)
        self.keep = self.tmp / "keep"
        self.keep.mkdir()

    def attempt(self, scripts: list[str], saved: str, verdict: str, reason: str) -> Path:
        a = self.tmp / "runs" / "batch-B3" / "synthetic-777-s0"
        (a / "replay").mkdir(parents=True, exist_ok=True)
        rows = [{"type": "meta", "task": "synthetic-777"}]
        for i, s in enumerate(scripts, 1):
            rows += [{"type": "submit", "turn": i, "flag": FLAG, "script": s}, {"type": "replay", "verdict": "unverified"}]
        rows.append({"type": "summary", "verdict": verdict, "reason": reason, "flag": FLAG})
        (a / "trace.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        (a / "replay" / "solve.py").write_text(saved)
        return a

    def recheck(self, a: Path) -> dict:
        return rescore.recheck(a, {"synthetic-777": self.task}, self.tmp, self.keep, "")

    def test_the_last_submission_is_judged_with_its_own_script(self):
        # job 30505 (v7 B2, general-019): print(42) was rejected, then the flag was typed in; solve.py kept print(42)
        a = self.attempt([WRONG_OUTPUT, TYPED], saved=WRONG_OUTPUT, verdict="unverified", reason="literal_flag")
        r = self.recheck(a)
        self.assertEqual((r["new"], r["new_reason"]), ("unverified", "literal_flag"), r)

    def test_an_earlier_submission_accepted_today_ends_the_attempt_there(self):
        # v6 B1 crypto-058-s0: the first script would pass today; the last one typed the flag in
        a = self.attempt([DECODE, TYPED], saved=DECODE, verdict="unverified", reason="literal_flag")
        r = self.recheck(a)
        self.assertEqual((r["new"], r["submit"]), ("verified", 1), r)

    def test_a_stale_saved_script_cannot_make_an_attempt_pass(self):
        # solve.py holds a script that works, but the attempt's only submissions were typed in: still not verified
        a = self.attempt([TYPED, TYPED], saved=DECODE, verdict="unverified", reason="literal_flag")
        self.assertEqual(self.recheck(a)["new"], "unverified")

    def test_an_attempt_that_ran_out_of_budget_keeps_its_ending(self):
        a = self.attempt([WRONG_OUTPUT], saved=WRONG_OUTPUT, verdict="budget", reason="max_turns")
        r = self.recheck(a)
        self.assertEqual((r["new"], r["new_reason"]), ("budget", "max_turns"), r)

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("gzip"), "macOS only")
    def test_on_macos_zcat_reads_gzip_as_on_the_cluster(self):
        import gzip
        import os
        d = self.tmp / "tasks" / "synthetic-777"
        (d / "data.gz").write_bytes(gzip.compress(f"blob={FLAG}\n".encode()))
        self.task.assets.append("data.gz")
        old = os.environ.get("PATH", "")
        self.addCleanup(os.environ.__setitem__, "PATH", old)
        rescore.zcat_like_linux(self.tmp)
        script = "import subprocess\nprint(subprocess.run(['zcat', 'challenge/data.gz'], capture_output=True, text=True).stdout.split('=', 1)[1].strip())\n"
        a = self.attempt([script], saved=script, verdict="verified", reason="match")
        self.assertEqual(self.recheck(a)["new"], "verified")


if __name__ == "__main__":
    unittest.main()
