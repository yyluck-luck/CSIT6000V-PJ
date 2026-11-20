"""批跑调度（LPT）：慢题先跑，缩短整批墙钟；只改开跑顺序，不改任何 attempt 的输入。"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flagpilot import runner, schedule  # noqa: E402
from flagpilot.config import RunConfig  # noqa: E402


def T(name, cat="General Skills"):
    return SimpleNamespace(name=name, category=cat, task_id=0)


class TestMakespan(unittest.TestCase):
    def test_long_task_last_is_the_bad_case(self):
        fifo = [10] * 6 + [60]
        lpt = sorted(fifo, reverse=True)
        self.assertEqual(schedule.makespan(fifo, 4), 70)   # 慢题最后才进槽，独自拖 60 s
        self.assertEqual(schedule.makespan(lpt, 4), 60)    # 先放慢题，短题填空隙
        self.assertEqual(schedule.makespan([], 4), 0)


class TestEstimator(unittest.TestCase):
    HIST = [{"task": "a", "category": "forensics", "wall_s": 50.0},
            {"task": "a", "category": "forensics", "wall_s": 30.0},
            {"task": "b", "category": "forensics", "wall_s": 10.0}]

    def test_fallback_chain(self):
        est = schedule.Estimator(self.HIST)
        self.assertEqual(est.seconds(T("a", "Forensics")), (40.0, "task"))          # 本题中位数
        self.assertEqual(est.seconds(T("zz", "Forensics")), (30.0, "category"))     # 同类中位数
        self.assertEqual(est.seconds(T("zz", "Cryptography"))[1], "prior")          # 类别先验
        self.assertEqual(est.seconds(T("zz", "Something New"))[0], schedule.DEFAULT_PRIOR_S)

    def test_order_is_stable_and_deterministic(self):
        est = schedule.Estimator([])
        jobs = [(T("g1"), 0), (T("f1", "Forensics"), 0), (T("g2"), 0), (T("f2", "Forensics"), 0)]
        got = [j[0].name for j in schedule.lpt_order(jobs, est)]
        self.assertEqual(got, ["f1", "f2", "g1", "g2"])            # 同分保持原顺序
        self.assertEqual(got, [j[0].name for j in schedule.lpt_order(jobs, est)])


class TestHistoryAndRunner(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="fp-sched-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_history_skips_bad_rows_and_files(self):
        (self.root / "old").mkdir()
        (self.root / "old" / "runs.jsonl").write_text(
            json.dumps({"task": "slow", "category": "General Skills", "wall_s": 90}) + "\n"
            + json.dumps({"task": "x", "wall_s": 0}) + "\n"           # 非正数：丢
            + json.dumps({"wall_s": 5}) + "\n", encoding="utf-8")       # 没有 task：丢
        (self.root / "broken").mkdir()
        (self.root / "broken" / "runs.jsonl").write_bytes(b"\x00\xff not json\n")
        rows = schedule.load_history([self.root, self.root / "missing"])
        self.assertEqual([r["task"] for r in rows], ["slow"])

    def test_runner_submits_slowest_first(self):
        (self.root / "old").mkdir()
        (self.root / "old" / "runs.jsonl").write_text(
            json.dumps({"task": "slow", "category": "General Skills", "wall_s": 90}) + "\n", encoding="utf-8")
        seen = []

        def fake_attempt(task, cfg, out_dir, seed, hook):
            seen.append(task.name)
            return {"verdict": "wrong", "reason": "x", "turns": 1, "wall_s": 1, "total_tokens": 1,
                    "cost_usd": 0, "run_dir": ""}

        # quick 是密码题（无历史 → 先验 30 s）；slow 有 90 s 的本题历史；forensic 走取证先验 40 s
        tasks = [T("quick", "Cryptography"), T("slow"), T("forensic", "Forensics")]
        with mock.patch.object(runner, "run_attempt", fake_attempt):
            r = runner.BatchRunner(RunConfig(), self.root / "new", workers=1)
            r.run(tasks, resume=False)
            self.assertEqual(seen, ["slow", "forensic", "quick"])      # 90 s 历史 > 取证先验 > 密码先验
            seen.clear()
            r2 = runner.BatchRunner(RunConfig(), self.root / "fifo", workers=1)
            r2.run(tasks, resume=False, order="fifo")
            self.assertEqual(seen, ["quick", "slow", "forensic"])


if __name__ == "__main__":
    unittest.main()
