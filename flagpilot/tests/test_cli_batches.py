"""命令行读批次：`--batch` / `--batches` 认现成的目录，不只是 runs/ 下的名字。

评测记录是随仓库提交的（`flagpilot/results/tacc/v7/...`），运行记录在 `runs/`。只认 `runs/` 的时候，
README 里"离线复现 v7 表格"那几条命令在新克隆上一个字都读不出来：`--batch tacc/v7/...` 被拼成
`runs/tacc/v7/...`，而数据在 `results/tacc/v7/...`。评审照着 README 做，看到的是一行 warn
（或"找不到"）然后什么都没有，也没法判断是命令错了还是数据没带。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from flagpilot import cli, config


def _batch(root: Path, name: str, rows: list[dict]) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "runs.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return d


def _row(task: str, verdict: str, turns: int = 4) -> dict:
    return {"run_id": f"{task}-s0", "task": task, "task_id": 1, "category": "Cryptography", "seed": 0,
            "verdict": verdict, "reason": "match" if verdict == "verified" else "wrong_flag",
            "turns": turns, "wall_s": 30.0, "total_s": 31.0, "setup_s": 1.0, "tail_s": 0.1,
            "model_s": 28.0, "tool_s": 2.0, "total_tokens": 4000, "completion_tokens": 400,
            "invalids": 0, "flag": "picoCTF{x}"}


def _rel(d: Path) -> str:
    """README 里写的是相对路径（`--batch results/tacc/v7/...`）：绝对路径本来就能用，
    出问题的是相对路径——它会被拼成 runs/results/...。测试必须按文档的写法走。"""
    return os.path.relpath(d)


def _run(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main(argv)
    return rc, buf.getvalue()


class TestBatchPath(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_report_reads_a_batch_directory_by_path(self):
        """数据在 results/ 下：直接给目录就该读，不该再套一层 runs/。"""
        d = _batch(self.tmp, "results/tacc/tacc-dev-v7-B0", [_row("crypto-005", "verified")])

        rc, out = _run(["report", "--batch", _rel(d)])

        self.assertEqual(rc, 0, out)
        self.assertIn("verified", out)
        self.assertIn("100.0%", out)

    def test_compare_resolves_two_directories(self):
        a = _batch(self.tmp, "results/v7-B0", [_row("crypto-005", "wrong"), _row("crypto-058", "verified")])
        b = _batch(self.tmp, "results/v7-B3", [_row("crypto-005", "verified"), _row("crypto-058", "verified")])

        rc, out = _run(["compare", "--batches", f"{_rel(a)},{_rel(b)}"])

        self.assertEqual(rc, 0, out)
        self.assertIn("50.0%", out)          # B0 只有 1/2
        self.assertIn("100.0%", out)         # B3 两题都对

    def test_pair_resolves_two_directories(self):
        a = _batch(self.tmp, "results/p-B0", [_row("crypto-005", "wrong")])
        b = _batch(self.tmp, "results/p-B3", [_row("crypto-005", "verified")])

        rc, out = _run(["pair", "--a", _rel(a), "--b", _rel(b)])

        self.assertEqual(rc, 0, out)
        self.assertIn("修好", out)
        self.assertIn("1", out)

    def test_a_bare_name_still_means_runs(self):
        """老写法（runs/ 下的批次名）不能被这次改动打断。"""
        rc, out = _run(["report", "--batch", "no-such-batch"])
        self.assertEqual(rc, 1)
        self.assertIn(str(config.RUNS_ROOT / "no-such-batch" / "runs.jsonl"), out,
                      "报错要给出实际找过的路径，否则看不出是名字错了还是目录不对")


class TestMarkdownOutput(unittest.TestCase):
    """`--md` 是 README 里把对比表写进实验记录的方式（`compare --batches ... --md e1.md`）。

    这几条命令的正文和落盘内容必须一致：报告里的表格是从这些 .md 拼进去的，文件少一列、
    或者父目录没建出来直接崩，都得在这里先红。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.a = _batch(self.tmp, "results/m-B0", [_row("crypto-005", "wrong"), _row("crypto-058", "verified")])
        self.b = _batch(self.tmp, "results/m-B3", [_row("crypto-005", "verified"), _row("crypto-058", "verified")])

    def test_compare_writes_the_same_table_it_prints(self):
        target = self.tmp / "deep" / "nested" / "compare.md"
        rc, out = _run(["compare", "--batches", f"{_rel(self.a)},{_rel(self.b)}", "--md", str(target)])

        self.assertEqual(rc, 0, out)
        self.assertTrue(target.is_file(), "父目录没有时应当自己建出来")
        body = target.read_text(encoding="utf-8")
        self.assertIn("| batch |", body)
        self.assertIn("50.0%", body)
        self.assertIn("100.0%", body)
        for line in body.strip().splitlines():
            self.assertIn(line, out, "落盘的表和打印的表不是同一份")

    def test_pair_writes_its_report(self):
        target = self.tmp / "pair.md"
        rc, out = _run(["pair", "--a", _rel(self.a), "--b", _rel(self.b), "--md", str(target)])

        self.assertEqual(rc, 0, out)
        body = target.read_text(encoding="utf-8")
        self.assertIn("McNemar", body)
        self.assertIn("修好", body)

    def test_a_reference_batch_that_is_not_loaded_is_an_error(self):
        """`--ref` 写错时不能静默给出不带参照的表：E3 的对比全靠它。"""
        rc, out = _run(["compare", "--batches", _rel(self.a), "--ref", "not-loaded"])
        self.assertEqual(rc, 1)
        self.assertIn("not-loaded", out)


if __name__ == "__main__":
    unittest.main()
