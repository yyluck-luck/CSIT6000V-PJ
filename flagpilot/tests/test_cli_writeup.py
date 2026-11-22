"""`writeup`：从一次已完成的 run 生成解题报告；没有结果时讲清楚，而不是甩 traceback。

网页上的 writeup 下载与本命令走同一条生成路径（`flagpilot/writeup.py`），所以这里的
"跑不出来的情形"就是评审点下载时可能撞上的情形。
"""

from __future__ import annotations

import contextlib
import io
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
class TestWriteupCommand(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.runs = self.tmp / "runs"
        self.cli(["batch", "--batch", "wu", "--model", "mock", "--limit", "1", "--workers", "1"])
        self.run_dir = sorted((self.runs / "wu").glob("*-s0"))[0]

    def cli(self, argv: list[str]) -> tuple[int, str]:
        with mock.patch.object(config, "TASKS_ROOT", DEMO), \
                mock.patch.object(config, "RUNS_ROOT", self.runs):
            return run_cli(argv)

    def test_it_writes_a_writeup_next_to_the_trace(self):
        rc, out = self.cli(["writeup", "--run-dir", str(self.run_dir)])
        self.assertEqual(rc, 0, out[-500:])
        target = self.run_dir / "writeup.md"
        self.assertTrue(target.is_file())
        body = target.read_text(encoding="utf-8")
        self.assertIn("demo-001", body, "报告里要点出是哪道题")
        self.assertIn("verified", body.lower(), "结论要写进去")

    def test_out_puts_it_where_asked(self):
        target = self.tmp / "elsewhere" / "wu.md"
        rc, out = self.cli(["writeup", "--run-dir", str(self.run_dir), "--out", str(target)])
        self.assertEqual(rc, 0, out[-500:])
        self.assertTrue(target.is_file())

    def test_an_unfinished_run_gets_a_sentence_not_a_traceback(self):
        """空目录 / 没跑完的 run：`show` 会说"没有找到 trace.jsonl"，这里也该是一句话 + 非零退出码。"""
        empty = self.tmp / "empty-run"
        empty.mkdir()
        rc, out = self.cli(["writeup", "--run-dir", str(empty)])
        self.assertEqual(rc, 1)
        self.assertNotIn("Traceback", out)
        self.assertIn("无法生成 writeup", out)


if __name__ == "__main__":
    unittest.main()
