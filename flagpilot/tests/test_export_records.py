"""scripts/export_records.py: keep what checks the numbers (ledger, traces, scripts, write-ups, summaries, logs),
leave out the working directory and the challenge copies."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import export_records  # noqa: E402


class TestExportRecords(unittest.TestCase):
    def test_keeps_the_evidence_and_drops_the_bulk(self):
        tmp = Path(tempfile.mkdtemp(prefix="fp-export-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        src = tmp / "v9"
        att = src / "tacc-dev-v9-B0" / "crypto-005-s0"
        for f in ("trace.jsonl", "writeup.md", "replay/solve.py", "replay/challenge/rot13.py", "work/scratch.txt",
                  "no-material/solve.py"):
            (att / f).parent.mkdir(parents=True, exist_ok=True)
            (att / f).write_text("x")
        (src / "tacc-dev-v9-B0" / "runs.jsonl").write_text("{}\n")
        for f in ("tacc-dev-v9-B0.md", "pipeline-1.log", "serve-1.log", "serve-live.log"):
            (src / f).write_text("x")
        (src / "stray-dir-without-ledger").mkdir()
        n, _ = export_records.export(src, tmp / "out")
        got = sorted(str(p.relative_to(tmp / "out")) for p in (tmp / "out").rglob("*") if p.is_file())
        self.assertEqual(got, ["pipeline-1.log", "serve-1.log", "tacc-dev-v9-B0.md", "tacc-dev-v9-B0/crypto-005-s0/replay/solve.py",
                               "tacc-dev-v9-B0/crypto-005-s0/trace.jsonl", "tacc-dev-v9-B0/crypto-005-s0/writeup.md",
                               "tacc-dev-v9-B0/runs.jsonl"])
        self.assertEqual(n, 7)


if __name__ == "__main__":
    unittest.main()
