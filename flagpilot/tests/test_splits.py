"""dev/test 划分与"测试集只跑一次"守卫。用合成小题库，不依赖真实题库。"""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flagpilot import cli, splits  # noqa: E402
from flagpilot.tasks import load_index  # noqa: E402


def write_taskset(root: Path) -> None:
    (root / "index").mkdir(parents=True)
    rows = []
    for i, cat in enumerate(["Cryptography"] * 6 + ["Forensics"] * 4):
        name = f"{cat[:4].lower()}-{i:03d}"
        (root / "tasks" / name).mkdir(parents=True)
        rows.append({"task_id": i, "name": name, "category": cat, "gold_sha256": "0" * 64,
                     "assets_dir": f"tasks/{name}", "assets": []})
    (root / "index" / "tasks.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


class _TmpTaskset(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "taskset"
        write_taskset(self.root)
        self.tasks = load_index(self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestMakeSplit(_TmpTaskset):
    def test_deterministic_disjoint_stratified(self):
        a = splits.make_split(self.tasks, seed=0, test_frac=0.5)
        b = splits.make_split(self.tasks, seed=0, test_frac=0.5)
        self.assertEqual(a, b)
        self.assertFalse(set(a["dev"]) & set(a["test"]))
        self.assertEqual(len(a["dev"]) + len(a["test"]), 10)
        # 分层：每个类别各自一半
        self.assertEqual(a["counts"], {"Cryptography": {"dev": 3, "test": 3},
                                       "Forensics": {"dev": 2, "test": 2}})
        self.assertEqual(a["status"], "proposed")

    def test_seed_changes_membership(self):
        a = splits.make_split(self.tasks, seed=0)
        c = splits.make_split(self.tasks, seed=1)
        self.assertNotEqual(a["test"], c["test"])

    def test_bad_fraction(self):
        with self.assertRaises(ValueError):
            splits.make_split(self.tasks, test_frac=1.0)


class TestLoadAndFilter(_TmpTaskset):
    def test_roundtrip_and_filter(self):
        path = self.root / "index" / "splits.json"
        data = splits.make_split(self.tasks, index_sha=splits.index_sha256(self.root))
        path.write_text(json.dumps(data), encoding="utf-8")
        sp = splits.load_split(path)
        dev = splits.filter_tasks(self.tasks, "dev", sp)
        test = splits.filter_tasks(self.tasks, "test", sp)
        self.assertEqual({t.name for t in dev}, set(data["dev"]))
        self.assertEqual(len(dev) + len(test), 10)
        self.assertEqual(len(splits.filter_tasks(self.tasks, "all", sp)), 10)

    def test_overlap_rejected(self):
        path = self.tmp / "bad.json"
        path.write_text(json.dumps({"dev": ["x"], "test": ["x"]}), encoding="utf-8")
        with self.assertRaises(ValueError):
            splits.load_split(path)

    def test_missing_file_explains_how_to_make_one(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            splits.load_split(self.tmp / "nope.json")
        self.assertIn("make_split.py", str(ctx.exception))


class TestGuard(unittest.TestCase):
    def setUp(self):
        self.runs = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.runs, ignore_errors=True)

    def test_requires_confirmation(self):
        with self.assertRaises(PermissionError):
            splits.guard_test_run(self.runs, "final", "fp1", confirm=False)

    def test_first_run_then_resume_allowed(self):
        self.assertIn("登记", splits.guard_test_run(self.runs, "final", "fp1", True))
        self.assertIn("续跑", splits.guard_test_run(self.runs, "final", "fp1", True))

    def test_same_config_under_new_batch_name_refused(self):
        splits.guard_test_run(self.runs, "final", "fp1", True)
        with self.assertRaises(PermissionError):
            splits.guard_test_run(self.runs, "final-again", "fp1", True)

    def test_same_batch_with_changed_config_refused(self):
        splits.guard_test_run(self.runs, "final", "fp1", True)
        with self.assertRaises(PermissionError):
            splits.guard_test_run(self.runs, "final", "fp2", True)

    def test_config_hash_ignores_batch_name(self):
        self.assertEqual(splits.config_hash({"batch": "a", "x": 1}),
                         splits.config_hash({"batch": "b", "x": 1}))
        self.assertNotEqual(splits.config_hash({"x": 1}), splits.config_hash({"x": 2}))


class TestCliGuard(_TmpTaskset):
    def run_cli(self, argv):
        path = self.root / "index" / "splits.json"
        path.write_text(json.dumps(splits.make_split(
            self.tasks, index_sha=splits.index_sha256(self.root))), encoding="utf-8")
        runs = self.tmp / "runs"
        buf = io.StringIO()
        with mock.patch("flagpilot.config.TASKS_ROOT", self.root), \
                mock.patch("flagpilot.config.RUNS_ROOT", runs), \
                mock.patch("flagpilot.cli.BatchRunner") as runner, redirect_stdout(buf):
            runner.return_value.load_results.return_value = []
            code = cli.main(argv)
        return code, buf.getvalue(), runner

    def test_test_split_without_confirm_exits_nonzero(self):
        code, out, runner = self.run_cli(["batch", "--split", "test", "--scope", ""])
        self.assertEqual(code, 2)
        self.assertIn("--confirm-test", out)
        runner.assert_not_called()

    def test_dev_split_runs_only_dev_tasks(self):
        code, _, runner = self.run_cli(["batch", "--split", "dev", "--scope", ""])
        self.assertEqual(code, 0)
        ran = runner.return_value.run.call_args.args[0]
        self.assertEqual(len(ran), 5)

    def test_confirmed_test_run_is_recorded(self):
        code, out, _ = self.run_cli(["batch", "--split", "test", "--confirm-test",
                                     "--scope", ""])
        self.assertEqual(code, 0)
        self.assertIn("登记 test 批次", out)
        self.assertTrue((self.tmp / "runs" / splits.LEDGER).exists())


if __name__ == "__main__":
    unittest.main()
