"""Freeing space in the TACC home directory (2026-11-21: /mnt/home filled up mid-batch, "No space left on device").

  * deploy/tcloud/cleanup/job.sh: drops whole batches named in FP_DROP; every other attempt keeps only its records
    (trace.jsonl, writeup.md, replay/solve.py) even when the sandbox left its challenge copies read-only.
  * deploy/tcloud/client/job.sh prune_batch: the same pruning right after a batch's report is written, so a batch
    no longer leaves four copies of every challenge behind.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent.parent / "deploy" / "tcloud"
KEEP = {"trace.jsonl", "writeup.md", "replay/solve.py"}


def make_attempt(d: Path, readonly: bool = True) -> None:
    for rel, body in (("trace.jsonl", "{}\n"), ("writeup.md", "# w\n"), ("replay/solve.py", "print(1)\n"),
                      ("replay/challenge/big.bin", "x" * 4096), ("replay/.tmp/t", "t"), ("work/notes.txt", "n"),
                      ("no-material/challenge/big.bin", "x" * 4096), ("text-material/solve.py", "print(2)\n")):
        f = d / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
    if readonly:                                   # what the sandbox does to challenge copies
        for c in (d / "replay" / "challenge", d / "no-material" / "challenge"):
            for f in c.iterdir():
                f.chmod(stat.S_IRUSR)
            c.chmod(stat.S_IRUSR | stat.S_IXUSR)


def files_of(d: Path) -> set[str]:
    return {p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file()}


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class TestCleanup(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-cleanup-"))
        self.addCleanup(self.rm)
        self.user, self.work = self.tmp / "USERDIR", self.tmp / "WORKDIR"
        self.runs = self.user / "flagpilot-runs"
        for b in ("tacc-dev-v5-B0", "tacc-dev-v7-B0"):
            for a in ("crypto-005-s0", "forensics-065-s1"):
                make_attempt(self.runs / b / a)
            (self.runs / b / "runs.jsonl").write_text("{}\n")
            (self.runs / f"{b}.md").write_text("report\n")
        (self.user / "slurm_log").mkdir(parents=True)
        old, new = self.user / "slurm_log" / "old.log", self.user / "slurm_log" / "new.log"
        old.write_text("o")
        new.write_text("n")
        os.utime(old, (1, 1))
        (self.user / "CSIT6000V-project-cache" / "vllm").mkdir(parents=True)
        (self.user / "CSIT6000V-project-cache" / "vllm" / "graph.bin").write_text("c")

    def rm(self):
        for p in self.tmp.rglob("*"):
            try:
                p.chmod(0o700)
            except OSError:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_cleanup_job_drops_named_batches_and_keeps_only_records(self):
        self.work.mkdir()
        shutil.copy(DEPLOY / "cleanup" / "job.sh", self.work / "job.sh")
        (self.work / "run.env").write_text("FP_MODE=cleanup\nFP_DROP='tacc-dev-v5-B0 not-there ../evil'\n")
        r = subprocess.run(["bash", str(self.work / "job.sh")], capture_output=True, text=True, timeout=60,
                           env={**os.environ, "TACC_USERDIR": str(self.user), "TACC_WORKDIR": str(self.work), "SLURM_JOB_ID": "1"})
        self.assertIn("[fp] done: cleanup", r.stdout, r.stderr)
        self.assertIn("[fp] dropped tacc-dev-v5-B0", r.stdout)
        self.assertFalse((self.runs / "tacc-dev-v5-B0").exists())
        self.assertFalse((self.runs / "tacc-dev-v5-B0.md").exists())
        self.assertIn("[fp] pruned 2 attempts", r.stdout)
        for a in ("crypto-005-s0", "forensics-065-s1"):
            self.assertEqual(files_of(self.runs / "tacc-dev-v7-B0" / a), KEEP)
        self.assertTrue((self.runs / "tacc-dev-v7-B0" / "runs.jsonl").exists())
        self.assertTrue((self.runs / "tacc-dev-v7-B0.md").exists())
        self.assertFalse((self.user / "slurm_log" / "old.log").exists())
        self.assertTrue((self.user / "slurm_log" / "new.log").exists())
        self.assertTrue((self.user / "CSIT6000V-project-cache" / "vllm" / "graph.bin").exists(), "the compile cache stays")
        self.assertTrue(self.tmp.exists() and not (self.tmp / "evil").exists())

    def test_the_client_job_prunes_a_batch_after_its_report(self):
        src = (DEPLOY / "client" / "job.sh").read_text(encoding="utf-8")
        fn = re.search(r"^prune_batch\(\) \{.*?^\}\n", src, re.S | re.M).group(0)
        self.assertRegex(src, r'fp report --batch "\$1" --md "\$\{FLAGPILOT_RUNS\}/\$1\.md" \|\| true\n  prune_batch "\$\{FLAGPILOT_RUNS\}/\$1"',
                         "pruning comes right after the report, never before it")
        b = self.runs / "tacc-dev-v7-B0"
        r = subprocess.run(["bash", "-c", f"set -euo pipefail\n{fn}\nprune_batch {b}\necho ok"], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.stdout.strip(), "ok", r.stderr)
        for a in ("crypto-005-s0", "forensics-065-s1"):
            self.assertEqual(files_of(b / a), KEEP)
        self.assertTrue((b / "runs.jsonl").exists())

    def test_submit_stages_only_three_small_files_for_cleanup(self):
        fake = self.tmp / "bin" / "tcloud"                 # submit.sh checks that the SDK is installed (CI has none)
        fake.parent.mkdir()
        fake.write_text("#!/bin/sh\necho 'Submitted batch job 1'\n")
        fake.chmod(0o755)
        env = {**os.environ, "FP_DRY_RUN": "1", "FP_DROP": "tacc-dev-v5-B0 tacc-smoke", "TMPDIR": str(self.tmp),
               "PATH": f"{fake.parent}{os.pathsep}{os.environ.get('PATH', '')}"}
        r = subprocess.run(["bash", str(DEPLOY / "submit.sh"), "cleanup"], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        stage = next(self.tmp.glob("fp-tcloud-*/flagpilot-client"))
        self.assertEqual(sorted(p.name for p in stage.iterdir()), ["job.sh", "run.env", "tuxiv.conf"])
        self.assertIn("time=00:10:00", (stage / "tuxiv.conf").read_text())
        bad = subprocess.run(["bash", str(DEPLOY / "submit.sh"), "cleanup"], capture_output=True, text=True,
                             env={**env, "FP_DROP": "x; rm -rf /"}, timeout=60)
        self.assertNotEqual(bad.returncode, 0)


if __name__ == "__main__":
    unittest.main()
