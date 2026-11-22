"""doctor 的判定：本机跑得动就报通过，配置错了才红。

`./README.md` 第 1 步让人跑 `doctor`，再"读隔离等级那几行"。如果一台**正常的**开发机也永远
返回非零、结论永远写"先处理上面的问题"，这条命令就没法当门禁用，读者也分不清哪些是真要修的。
原来的写法把 `_private/gold_private.json`（本机评分要读、submit.sh 上传时排除）当成和"题库指向
with-solutions"同级的错误；本文件锁住这两级的区别，以及 --strict 的把关用法。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from flagpilot import cli, config


def write_taskset(root: Path) -> None:
    """两题的小题库：目录、附件、index 三者一致，doctor 的一致性检查应当通过。"""
    (root / "index").mkdir(parents=True)
    rows = []
    for i, (name, cat, scope) in enumerate([("crypto-001", "Cryptography", "core"),
                                            ("general-001", "General Skills", "extra-gs")]):
        (root / "tasks" / name).mkdir(parents=True)
        (root / "tasks" / name / "note.txt").write_text("picoCTF{x}\n", encoding="utf-8")
        rows.append({"task_id": i, "name": name, "category": cat, "scope": scope,
                     "title": f"{name} title", "gold_sha256": "0" * 64,
                     "assets_dir": f"tasks/{name}", "assets": ["note.txt"]})
    (root / "index" / "tasks.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


class TestDoctorVerdict(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "taskset"
        write_taskset(self.root)
        self.addCleanup(self._tmp.cleanup)

    def doctor(self, root: Path, *extra: str) -> tuple[int, str]:
        buf = io.StringIO()
        with mock.patch.object(config, "TASKS_ROOT", root), \
                mock.patch.dict(os.environ, {"FLAGPILOT_NO_DOTENV": "1"}), \
                contextlib.redirect_stdout(buf):
            rc = cli.main(["doctor", *extra])
        return rc, buf.getvalue()

    def test_a_normal_checkout_is_not_a_failure(self):
        """题库按 docs/TASKS.md 的布局放着明文 gold，这是本机评分的正常状态，不是错误。"""
        (self.root / "_private").mkdir()
        (self.root / "_private" / "gold_private.json").write_text("{}", encoding="utf-8")

        rc, out = self.doctor(self.root)

        self.assertEqual(rc, 0, out[-800:])
        self.assertIn("可以开始跑", out)
        self.assertIn("明文 gold 仅在本机", out)
        self.assertIn("submit.sh 不会带", out, "提示里要说明上传时已经排除了它")

    def test_strict_turns_the_local_gold_into_a_problem(self):
        """上集群前的把关：--strict 时同一件事要被判成不通过。"""
        (self.root / "_private").mkdir()
        (self.root / "_private" / "gold_private.json").write_text("{}", encoding="utf-8")

        rc, out = self.doctor(self.root, "--strict")

        self.assertEqual(rc, 1, out[-800:])
        self.assertIn("泄漏检查         不通过", out)

    def test_a_task_set_with_solutions_fails_without_strict(self):
        """题库指到带题解的版本是真的配置错误，不需要 --strict 就该红。"""
        bad = self.tmp / "ctf-with-solutions"
        write_taskset(bad)

        rc, out = self.doctor(bad)

        self.assertEqual(rc, 1, out[-800:])
        self.assertIn("带题解", out)

    def test_a_leftover_solution_directory_fails(self):
        (self.root / "tasks" / "crypto-001" / "solution").mkdir()

        rc, out = self.doctor(self.root)

        self.assertEqual(rc, 1, out[-800:])
        self.assertIn("题解目录残留", out)

    def test_a_missing_index_says_which_variable_points_at_the_task_set(self):
        empty = self.tmp / "empty"
        empty.mkdir()

        rc, out = self.doctor(empty)

        self.assertEqual(rc, 1)
        self.assertIn("FLAGPILOT_TASKS", out, "只说读取失败，读者不知道该查什么")


if __name__ == "__main__":
    unittest.main()
