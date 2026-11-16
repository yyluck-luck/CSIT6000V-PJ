"""E3 / 报告用到的新指标：分位数、配对比较、GPU 秒账本、带护栏的对比表。

数字都是手算后写死的期望值——统计口径一旦被改错，这里先红。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flagpilot import gpu, metrics  # noqa: E402


def rec(run_id, verdict, wall=10.0, total=None, **kw):
    r = {"run_id": run_id, "verdict": verdict, "category": "Cryptography",
         "turns": 3, "wall_s": wall, "total_tokens": 100, "cost_usd": 0.0,
         "ablation": "B3", "run_dir": "", "replay": {"reason": "match"}}
    if total is not None:
        r["total_s"] = total
    r.update(kw)
    return r


class TestPercentile(unittest.TestCase):
    def test_linear_interpolation_matches_numpy_default(self):
        xs = [1, 2, 3, 4]
        self.assertEqual(metrics.percentile(xs, 50), 2.5)
        self.assertAlmostEqual(metrics.percentile(xs, 95), 3.85)
        self.assertEqual(metrics.percentile([7], 95), 7)
        self.assertIsNone(metrics.percentile([], 50))

    def test_order_does_not_matter(self):
        self.assertEqual(metrics.percentile([4, 1, 3, 2], 50), 2.5)


class TestSummaryLatency(unittest.TestCase):
    def test_solve_time_uses_verified_end_to_end(self):
        rs = [rec("a-s0", "verified", wall=10, total=12),
              rec("b-s0", "verified", wall=20, total=24),
              rec("c-s0", "wrong", wall=99, total=100)]
        s = metrics.summarize(rs, with_tags=False)
        # 解题耗时只看 verified，且用端到端 total_s（含重放）
        self.assertEqual(s["solve_n"], 2)
        self.assertEqual(s["solve_s_p50"], 18.0)
        self.assertAlmostEqual(s["solve_s_p95"], 23.4)
        # 墙钟分位覆盖全部 attempt
        self.assertEqual(s["wall_n"], 3)
        self.assertEqual(s["wall_s_p50"], 20.0)

    def test_old_runs_without_total_fall_back_to_wall(self):
        s = metrics.summarize([rec("a-s0", "verified", wall=7)], with_tags=False)
        self.assertEqual(s["solve_s_p50"], 7.0)

    def test_no_verified_means_no_solve_percentiles(self):
        s = metrics.summarize([rec("a-s0", "wrong")], with_tags=False)
        self.assertEqual(s["solve_n"], 0)
        self.assertIsNone(s["solve_s_p50"])


class TestSummaryCostAndCache(unittest.TestCase):
    def test_api_cache_rate_and_cost_per_verified(self):
        rs = [rec("a-s0", "verified", cost_usd=0.02, api_cache_hit_tokens=900,
                  api_cache_miss_tokens=100),
              rec("b-s0", "wrong", cost_usd=0.01, api_cache_hit_tokens=100,
                  api_cache_miss_tokens=900)]
        s = metrics.summarize(rs, with_tags=False)
        self.assertEqual(s["api_cache_hit_rate"], 0.5)
        self.assertAlmostEqual(s["cost_per_verified_usd"], 0.03)

    def test_track_l_runs_have_no_api_cache_rate(self):
        s = metrics.summarize([rec("a-s0", "verified")], with_tags=False)
        self.assertIsNone(s["api_cache_hit_rate"])
        self.assertIsNone(s["cost_per_verified_usd"])

    def test_gpu_seconds_per_verified(self):
        rs = [rec("a-s0", "verified"), rec("b-s0", "verified"), rec("c-s0", "wrong")]
        s = metrics.summarize(rs, with_tags=False,
                              gpu={"gpu_seconds": 600.0, "busy_gpu_seconds": 300.0, "jobs": 1})
        self.assertEqual(s["gpu_seconds"], 600.0)
        self.assertEqual(s["gpu_s_per_verified"], 300.0)
        self.assertEqual(s["busy_gpu_s_per_verified"], 150.0)
        self.assertIn("GPU", metrics.render(s))


class TestConcurrentCounters(unittest.TestCase):
    """并发 > 1 时服务端计数器被多个 attempt 分摊：不报命中率，免得把错数写进 E3 表。"""

    def server(self):
        return {"cache_hits": 80, "cache_queries": 100, "prefill_tokens": 900, "decode_tokens": 100}

    def test_single_worker_reports_server_cache(self):
        s = metrics.summarize([rec("a-s0", "verified", workers=1, server=self.server())],
                              with_tags=False)
        self.assertEqual(s["cache_hit_rate"], 0.8)
        self.assertEqual(s["prefill_share"], 0.9)
        self.assertIsNone(s["server_counters_note"])

    def test_concurrent_batch_suppresses_server_cache(self):
        s = metrics.summarize([rec("a-s0", "verified", workers=4, server=self.server()),
                               rec("b-s0", "verified", workers=4, server=self.server())],
                              with_tags=False)
        self.assertIsNone(s["cache_hit_rate"])
        self.assertIsNone(s["prefill_share"])
        self.assertIn("并发", s["server_counters_note"])
        self.assertIn("并发", metrics.render(s))

    def test_api_cache_unaffected_by_concurrency(self):
        s = metrics.summarize([rec("a-s0", "verified", workers=8, api_cache_hit_tokens=3,
                                   api_cache_miss_tokens=1)], with_tags=False)
        self.assertEqual(s["api_cache_hit_rate"], 0.75)


class TestPaired(unittest.TestCase):
    def test_transitions_and_exact_mcnemar(self):
        a = [rec(f"t{i}-s0", v) for i, v in enumerate(
            ["wrong", "wrong", "wrong", "wrong", "verified", "verified", "wrong"])]
        b = [rec(f"t{i}-s0", v) for i, v in enumerate(
            ["verified", "verified", "verified", "verified", "verified", "wrong", "wrong"])]
        b.append(rec("only-in-b-s0", "verified"))
        p = metrics.paired(a, b)
        self.assertEqual((p["fixed"], p["broken"], p["both_ok"], p["both_fail"], p["unpaired"]),
                         (4, 1, 1, 1, 1))
        # 精确 McNemar：discordant n=5，k=min(4,1)=1 → p = 2*(C(5,0)+C(5,1))/2^5 = 0.375
        self.assertAlmostEqual(p["p_value"], 0.375)
        self.assertEqual(p["n_pairs"], 7)
        self.assertIn("0.375", metrics.render_paired(p, "A", "B"))

    def test_raw_key_counts_unverified_as_success(self):
        a = [rec("t0-s0", "wrong")]
        b = [rec("t0-s0", "unverified")]
        self.assertEqual(metrics.paired(a, b, key="raw")["fixed"], 1)
        self.assertEqual(metrics.paired(a, b, key="verified")["fixed"], 0)

    def test_no_discordant_pairs_gives_p_one(self):
        a = [rec("t0-s0", "verified")]
        self.assertEqual(metrics.paired(a, a)["p_value"], 1.0)


class TestCompareGuard(unittest.TestCase):
    def test_e3_columns_and_guard_vs_reference(self):
        ref = metrics.summarize([rec(f"t{i}-s0", "verified") for i in range(10)], with_tags=False)
        ok = metrics.summarize([rec(f"t{i}-s0", "verified" if i < 10 else "wrong")
                                for i in range(10)], with_tags=False)
        bad = metrics.summarize([rec(f"t{i}-s0", "verified" if i < 8 else "wrong")
                                 for i in range(10)], with_tags=False)
        table = metrics.compare({"ref": ref, "ok": ok, "bad": bad},
                                keys=metrics.E3_KEYS, ref="ref", guard_pts=5.0)
        self.assertIn("solve p95", table)
        self.assertIn("Δ vs ref", table)
        rows = {line.split("|")[1].strip(): line
                for line in table.splitlines()[2:] if line.startswith("|")}
        self.assertIn("护栏", table)            # 表后附护栏口径说明
        self.assertIn("✓", rows["ok"])
        self.assertIn("✗", rows["bad"])          # 掉了 20 个百分点，超过 5 点护栏

    def test_default_compare_unchanged(self):
        s = metrics.summarize([rec("a-s0", "verified")], with_tags=False)
        table = metrics.compare({"B3": s})
        self.assertIn("| batch | n |", table)
        self.assertNotIn("Δ vs ref", table)


class TestGpuLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_detect_gpus(self):
        self.assertEqual(gpu.detect_gpus({"FP_GPUS": "4", "CUDA_VISIBLE_DEVICES": "0,1"}), 4)
        self.assertEqual(gpu.detect_gpus({"SLURM_GPUS_ON_NODE": "2"}), 2)
        self.assertEqual(gpu.detect_gpus({"CUDA_VISIBLE_DEVICES": "0,1,2"}), 3)
        self.assertEqual(gpu.detect_gpus({"CUDA_VISIBLE_DEVICES": ""}), 0)
        self.assertEqual(gpu.detect_gpus({"CUDA_VISIBLE_DEVICES": "NoDevFiles"}), 0)
        self.assertEqual(gpu.detect_gpus({}), 0)

    def test_busy_seconds_util_weighted_and_windowed(self):
        csv = self.tmp / "util.csv"
        # 两张卡，各 3 个采样点，间隔 10 s
        csv.write_text("100,0,50,1000\n110,0,100,1000\n120,0,0,1000\n"
                       "100,1,0,1000\n110,1,20,1000\n120,1,20,1000\n"
                       "garbage line\n", encoding="utf-8")
        # 卡0：50%×10 + 100%×10 = 15；卡1：0×10 + 20%×10 = 2
        self.assertAlmostEqual(gpu.busy_gpu_seconds(csv), 17.0)
        # 窗口 [105, 115]：卡0 50%×5 + 100%×5 = 7.5；卡1 0×5 + 20%×5 = 1
        self.assertAlmostEqual(gpu.busy_gpu_seconds(csv, start=105, end=115), 8.5)
        self.assertIsNone(gpu.busy_gpu_seconds(self.tmp / "missing.csv"))
        self.assertIsNone(gpu.busy_gpu_seconds(csv, start=500, end=600))

    def test_ledger_accumulates_jobs(self):
        gpu.append_ledger(self.tmp, {"job_id": "1", "gpus": 2, "wall_s": 100.0,
                                     "gpu_seconds": 200.0, "busy_gpu_seconds": 80.0})
        gpu.append_ledger(self.tmp, {"job_id": "2", "gpus": 2, "wall_s": 50.0,
                                     "gpu_seconds": 100.0, "busy_gpu_seconds": None})
        tot = gpu.load_ledger(self.tmp)
        self.assertEqual(tot["jobs"], 2)
        self.assertEqual(tot["gpu_seconds"], 300.0)
        # 有一个作业没采到利用率：busy 只给"已采样部分"，并标出覆盖了几个作业
        self.assertEqual(tot["busy_gpu_seconds"], 80.0)
        self.assertEqual(tot["busy_jobs"], 1)
        self.assertIsNone(gpu.load_ledger(self.tmp / "nope"))

    def test_runner_writes_ledger_only_with_gpus(self):
        from unittest import mock

        from flagpilot.config import RunConfig
        from flagpilot.runner import BatchRunner

        r = BatchRunner(RunConfig(), self.tmp / "b", workers=1)
        with mock.patch.dict("os.environ", {"FP_GPUS": "2", "SLURM_JOB_ID": "77"}):
            r._record_gpu(t_start=1000.0, t_end=1100.0, attempts=3)
        rows = [json.loads(x) for x in (self.tmp / "b" / gpu.LEDGER).read_text().splitlines()]
        self.assertEqual(rows[0]["job_id"], "77")
        self.assertEqual(rows[0]["gpu_seconds"], 200.0)
        r2 = BatchRunner(RunConfig(), self.tmp / "c", workers=1)
        with mock.patch.dict("os.environ", {}, clear=True):
            r2._record_gpu(t_start=0.0, t_end=10.0, attempts=1)
        self.assertFalse((self.tmp / "c" / gpu.LEDGER).exists(), "本地没 GPU 不该写账本")


class TestMarkdownExport(unittest.TestCase):
    """报告引用的是 Markdown 表：文本输出里有的关键数字，Markdown 里也必须有。"""

    def test_tiny_api_cost_is_not_rounded_away(self):
        # 一题冒烟的真实量级是几十微美元；四位小数会舍成 0，成本行整行消失。
        s = metrics.summarize([rec("a-s0", "budget", cost_usd=2.3e-05)], with_tags=False)
        self.assertAlmostEqual(s["total_cost_usd"], 2.3e-05)
        self.assertIn("$0.000023", metrics.to_markdown(s))
        self.assertIn("$0.000023", metrics.render(s))

    def test_e3_cost_cell_keeps_micro_dollars(self):
        self.assertEqual(metrics._cell("cost_per_verified_usd", 2.3e-05), "$0.000023")

    def test_wall_percentiles_in_markdown(self):
        s = metrics.summarize([rec("a-s0", "wrong", wall=10), rec("b-s0", "wrong", wall=20)],
                              with_tags=False)
        md = metrics.to_markdown(s)
        self.assertIn("墙钟 p50 / p95", md)
        self.assertIn("15.0 / 19.5 s", md)

    def test_gpu_totals_and_counter_note_in_markdown(self):
        server = {"cache_hits": 8, "cache_queries": 10, "prefill_tokens": 9, "decode_tokens": 1}
        s = metrics.summarize([rec("a-s0", "wrong", workers=4, server=server),
                               rec("b-s0", "wrong", workers=4, server=server)],
                              with_tags=False,
                              gpu={"gpu_seconds": 600.0, "busy_gpu_seconds": 300.0, "jobs": 1})
        md = metrics.to_markdown(s)
        self.assertIn("600 / 300", md)   # 0 个 verified 也要看得到总 GPU 秒
        self.assertIn("并发", md)         # 并发格为什么没有命中率


if __name__ == "__main__":
    unittest.main()
