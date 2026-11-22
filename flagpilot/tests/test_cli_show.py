"""`show`：不给服务、不给模型、不给网络，把一次已完成的 run 复盘成时间线。

README 的 Step 5/6 让评审用 `show --run-dir` 看单条 attempt、用 `--out` 落成文件。这些命令
以前一条测试都没有：渲染逻辑改了没人会发现，`--full` 与 `--out` 更是只靠手跑验证。
这里跑一次真实的 mock 批次（demo 题库，随仓库提交），再拿它的 run_dir 当输入——不伪造 trace，
也就不会测试自己编造的字段。
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
class TestShow(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.runs = cls.tmp / "runs"
        with mock.patch.object(config, "TASKS_ROOT", DEMO), \
                mock.patch.object(config, "RUNS_ROOT", cls.runs):
            rc, out = run_cli(["batch", "--batch", "show-fixture", "--model", "mock",
                               "--limit", "1", "--workers", "1"])
        assert rc == 0, out[-1500:]
        cls.run_dir = sorted((cls.runs / "show-fixture").glob("*-s0"))[0]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def show(self, *extra: str) -> tuple[int, str]:
        with mock.patch.object(config, "RUNS_ROOT", self.runs):
            return run_cli(["show", "--run-dir", str(self.run_dir), *extra])

    def test_the_header_states_what_the_run_was(self):
        rc, out = self.show()
        self.assertEqual(rc, 0, out[-500:])
        head = out.splitlines()
        self.assertTrue(head[0].startswith("# demo-001 · demo-001-s0"), head[0])
        self.assertIn("model=mock", head[1])
        self.assertIn("ablation=custom", head[1])
        self.assertIn("max_turns=", head[1])
        self.assertIn("isolation=", head[2], "隔离等级必须在复盘里出现——报告要引用它")

    def test_each_turn_is_one_line_by_default(self):
        rc, out = self.show()
        self.assertEqual(rc, 0)
        turns = [l for l in out.splitlines() if l.strip().startswith("[")]
        self.assertGreaterEqual(len(turns), 2, out)
        self.assertRegex(turns[0], r"^\[\s*1\] model")

    def test_full_adds_the_observation_text(self):
        _, short = self.show()
        _, full = self.show("--full")
        self.assertGreater(len(full), len(short), "--full 没有多打出任何内容")

    def test_out_writes_the_same_text(self):
        rc, out = self.show()
        self.assertEqual(rc, 0)
        target = self.tmp / "nested" / "run.md"
        rc, out2 = self.show("--out", str(target))
        self.assertEqual(rc, 0, out2[-300:])
        self.assertTrue(target.is_file(), "父目录不存在时应当自己建出来")
        body = target.read_text(encoding="utf-8")
        self.assertEqual(body.rstrip("\n"), out.rstrip("\n"), "落盘的内容和打印的不一致")

    def test_a_missing_run_directory_names_the_path_it_looked_at(self):
        with mock.patch.object(config, "RUNS_ROOT", self.runs):
            rc, out = run_cli(["show", "--run-dir", str(self.tmp / "nope" / "x-s0")])
        self.assertEqual(rc, 1)
        self.assertIn("trace.jsonl", out, f"报错要给出实际找过的文件：{out!r}")


if __name__ == "__main__":
    unittest.main()
