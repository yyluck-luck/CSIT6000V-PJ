"""`batch` 与 `report` 之间的契约：runs.jsonl 是唯一的事实来源。

报告、对比、配对全从批次的 runs.jsonl 读；一张表要能被复现，前提是同一条 attempt 只占一行
（`_upsert_result` 按 run_id 覆盖）。重跑同一批是日常操作——`report` 里 attempts 变成 6 就说明
覆盖没生效，而那个数字会直接进报告。
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from flagpilot import cli, config

DEMO = Path(__file__).resolve().parent.parent / "demo-tasks"


def run_cli(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main(argv)
    return rc, buf.getvalue()


@unittest.skipUnless((DEMO / "index" / "tasks.jsonl").is_file(), "需要仓库自带的 demo-tasks")
class TestBatchAndReport(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.runs = Path(self._tmp.name) / "runs"
        self.batch = self.runs / "contract"

    def cli(self, argv: list[str]) -> tuple[int, str]:
        with mock.patch.object(config, "TASKS_ROOT", DEMO), \
                mock.patch.object(config, "RUNS_ROOT", self.runs):
            return run_cli(argv)

    def run_batch(self) -> None:
        rc, out = self.cli(["batch", "--batch", "contract", "--model", "mock",
                            "--limit", "3", "--workers", "1"])
        self.assertEqual(rc, 0, out[-1200:])

    def rows(self) -> list[dict]:
        return [json.loads(l) for l in (self.batch / "runs.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]

    def test_a_batch_writes_one_row_per_attempt(self):
        self.run_batch()
        rows = self.rows()
        self.assertEqual(len(rows), 3)
        self.assertEqual({r["task"] for r in rows}, {"demo-001", "demo-002", "demo-003"})
        for r in rows:
            self.assertIn(r["verdict"], {"verified", "wrong", "budget", "error", "reproduced"})
            self.assertTrue(r["run_id"].endswith("-s0"))

    def test_rerunning_a_batch_overwrites_instead_of_appending(self):
        """重跑同一批不该让 attempts 翻倍：报告里的 n 就是从这里数的。"""
        self.run_batch()
        self.run_batch()
        self.assertEqual(len(self.rows()), 3, "同一条 attempt 被写了两行")

    def test_report_counts_what_the_batch_wrote(self):
        self.run_batch()
        rc, out = self.cli(["report", "--batch", "contract"])
        self.assertEqual(rc, 0, out[-800:])
        self.assertIn("attempts        3", out)
        self.assertIn("verified        3  (100.0%", out)
        self.assertIn("gap             0.0%", out)

    def test_report_json_is_machine_readable(self):
        self.run_batch()
        rc, out = self.cli(["report", "--batch", "contract", "--json"])
        self.assertEqual(rc, 0, out[-800:])
        data = json.loads(out)
        self.assertEqual(data["n"], 3)
        self.assertEqual(data["verified"], 3)

    def test_report_on_a_batch_that_was_never_run_says_so(self):
        rc, out = self.cli(["report", "--batch", "never-ran"])
        self.assertEqual(rc, 1)
        self.assertIn("runs.jsonl", out)


if __name__ == "__main__":
    unittest.main()
