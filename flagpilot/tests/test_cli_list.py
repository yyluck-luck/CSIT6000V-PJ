"""`list` 的排版：列之间必须留出间隔，scope 取到列宽上限时也不例外。

`python3 -m flagpilot list --scope extra-gs` 是 README 里给读者看的命令。scope 列宽原本是 8，
而 `extra-gs` 正好 8 个字符，于是行尾变成 `extra-gsFind the flag in the Python script!`——
两列粘在一起，读者看到的是题目名的一部分被吃掉了。
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


def write_taskset(root: Path) -> None:
    (root / "index").mkdir(parents=True)
    rows = []
    spec = [("crypto-001", "Cryptography", "core", "You will find the flag after decrypting this file"),
            ("general-007", "General Skills", "extra-gs", "Can you invoke help flags for a tool or binary?"),
            ("general-026", "General Skills", "extra-gs", "Fix the syntax error in this Python script")]
    for i, (name, cat, scope, title) in enumerate(spec):
        (root / "tasks" / name).mkdir(parents=True)
        rows.append({"task_id": i, "name": name, "category": cat, "scope": scope, "title": title,
                     "gold_sha256": "0" * 64, "assets_dir": f"tasks/{name}", "assets": []})
    (root / "index" / "tasks.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


class _ListOut(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "taskset"
        write_taskset(self.root)
        self.addCleanup(self._tmp.cleanup)

    def listing(self, *args: str) -> list[str]:
        buf = io.StringIO()
        with mock.patch.object(config, "TASKS_ROOT", self.root), \
                contextlib.redirect_stdout(buf):
            rc = cli.main(["list", *args])
        self.assertEqual(rc, 0)
        return [l for l in buf.getvalue().splitlines() if l.strip()]


class TestListColumns(_ListOut):

    def test_a_scope_at_full_column_width_keeps_a_gap(self):
        """scope 列宽 8、取值正好 8 个字符（extra-gs）时，标题被粘到了 scope 后面。"""
        rows = self.listing("--scope", "extra-gs")
        row = next(r for r in rows if r.startswith("general-007"))

        self.assertIn("extra-gs ", row, f"scope 与标题之间没有间隔：{row!r}")
        self.assertNotIn("extra-gsCan", row)

    def test_the_header_uses_the_same_widths_as_the_rows(self):
        rows = self.listing("--scope", "extra-gs")
        header, first = rows[0], rows[1]

        # 表头 "title" 的起始列要和正文标题的起始列一致
        self.assertEqual(header.index("title"), len("name              cat               scope    "))
        self.assertTrue(first[header.index("title") - 1] == " ",
                        f"标题没有落在表头 title 那一列：{first!r}")


class TestListFilters(_ListOut):
    """`--scope` 与 `--category` 的过滤：README 用 `list --scope extra-gs` 区分"补充题"。"""

    @staticmethod
    def names(rows: list[str]) -> list[str]:
        return [r.split()[0] for r in rows[1:] if r and not r.startswith("共 ")]

    def test_the_default_scope_is_core(self):
        self.assertEqual(self.names(self.listing()), ["crypto-001"])

    def test_extra_gs_shows_only_the_supplementary_tasks(self):
        self.assertEqual(self.names(self.listing("--scope", "extra-gs")), ["general-007", "general-026"])

    def test_category_filter_keeps_the_count_line_honest(self):
        rows = self.listing("--category", "General Skills", "--scope", "extra-gs")
        self.assertEqual(self.names(rows), ["general-007", "general-026"])
        self.assertIn("共 2 题", rows[-1])

    def test_an_unknown_category_prints_zero_instead_of_all(self):
        """过滤写错时宁可空列表，也不能把全部题目倒出来——那会把"43 题"读成"这一类的题数"。"""
        rows = self.listing("--category", "NoSuchCategory")
        self.assertEqual(self.names(rows), [])
        self.assertIn("共 0 题", rows[-1])


if __name__ == "__main__":
    unittest.main()
