"""整批 prefix cache 命中率（服务端计数器首尾差）。

并发 > 1 时单个 attempt 的计数窗口互相重叠，metrics 不报命中率；可整批看，最早一次
before 与最晚一次 after 的差值就是这一批的总量。数字取自 TACC 作业 29323 的真实计数：
15680 / 29320 = 53.5%。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flagpilot import cachestats, metrics  # noqa: E402

H, Q = "vllm:prefix_cache_hits_total", "vllm:prefix_cache_queries_total"


class TestBatchCache(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="fp-cache-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _run(self, name, before, after, workers=4):
        d = self.root / name
        d.mkdir()
        evs = [{"type": "server_metrics", "phase": "before", "ts": before[0], H: before[1], Q: before[2]},
               {"type": "server_metrics", "phase": "after", "ts": after[0], H: after[1], Q: after[2]}]
        (d / "trace.jsonl").write_text("\n".join(json.dumps(e) for e in evs) + "\n", encoding="utf-8")
        return {"run_id": name, "verdict": "verified", "category": "Cryptography", "turns": 3,
                "wall_s": 10, "total_tokens": 100, "cost_usd": 0, "ablation": "B3",
                "run_dir": str(d), "workers": workers, "replay": {"reason": "match"},
                "server": {"cache_queries": 1, "cache_hits": 1}}

    def results(self):
        # 两个 attempt 并发：窗口互相重叠，但首 before / 末 after 框住了整批
        return [self._run("a-s0", (1.0, 0, 0), (5.0, 10000, 20000)),
                self._run("b-s0", (2.0, 100, 400), (9.0, 15680, 29320))]

    def test_first_before_to_last_after(self):
        c = cachestats.batch_cache(self.results())
        self.assertEqual((c["hits"], c["queries"]), (15680, 29320))
        self.assertAlmostEqual(c["hit_rate"], 0.5348, places=4)

    def test_report_shows_batch_rate_under_concurrency(self):
        s = metrics.summarize(self.results(), with_tags=False)
        self.assertIsNone(s["cache_hit_rate"])          # 单 attempt 口径仍然不报
        self.assertIn("整批 53.5%", metrics.render(s))
        self.assertIn("| prefix cache 命中率（整批） | 53.5%", metrics.to_markdown(s))

    def test_missing_counters_give_none(self):
        d = self.root / "x"
        d.mkdir()
        (d / "trace.jsonl").write_text('{"type":"model"}\n', encoding="utf-8")
        self.assertIsNone(cachestats.batch_cache([{"run_dir": str(d)}, {"run_dir": ""}]))


class TestDownloadedBatch(unittest.TestCase):
    """从 TACC 下载回来的批次：run_dir 是集群上的绝对路径，本机要改指批次目录下的同名子目录。"""

    def test_run_dir_remapped_to_batch_folder(self):
        from flagpilot import cli
        root = Path(tempfile.mkdtemp(prefix="fp-dl-"))
        try:
            (root / "a-s0").mkdir()
            (root / "a-s0" / "trace.jsonl").write_text('{"type":"meta"}\n', encoding="utf-8")
            (root / "runs.jsonl").write_text(json.dumps({"run_id": "a-s0", "run_dir": "/mnt/home/x/flagpilot-runs/b/a-s0"}) + "\n",
                                             encoding="utf-8")
            rows = cli._load_batch(root / "runs.jsonl")
            self.assertEqual(rows[0]["run_dir"], str(root / "a-s0"))
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
