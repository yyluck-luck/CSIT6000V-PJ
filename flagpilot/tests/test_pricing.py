"""计价测试：Track H 的成本必须按"命中/未命中/输出"三档、按请求时刻的高峰规则算。

数字取自 DeepSeek 官方价目页（2026-11-16 核对），写成断言是为了：
有人改了价目表却没改来源与日期时，这里会第一个红。
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flagpilot import pricing  # noqa: E402
from flagpilot.client import Completion  # noqa: E402
from flagpilot.config import ModelSpec, make_model_spec  # noqa: E402

TABLE = pricing.PRICE_TABLES["deepseek-flash"]
MON_0700 = datetime(2026, 11, 16, 7, 0, tzinfo=timezone.utc)   # 周一高峰段
MON_0500 = datetime(2026, 11, 16, 5, 0, tzinfo=timezone.utc)   # 两个高峰段之间
SAT_0700 = datetime(2026, 11, 21, 7, 0, tzinfo=timezone.utc)   # 周末全天非高峰


class TestPeakWindows(unittest.TestCase):
    def test_weekday_windows(self):
        self.assertTrue(pricing.is_peak(TABLE, MON_0700))
        self.assertFalse(pricing.is_peak(TABLE, MON_0500))
        self.assertTrue(pricing.is_peak(TABLE, MON_0700.replace(hour=1)))
        self.assertFalse(pricing.is_peak(TABLE, MON_0700.replace(hour=4)))   # 区间右开
        self.assertFalse(pricing.is_peak(TABLE, MON_0700.replace(hour=10)))

    def test_weekend_is_off_peak(self):
        self.assertFalse(pricing.is_peak(TABLE, SAT_0700))

    def test_naive_datetime_is_treated_as_utc(self):
        self.assertTrue(pricing.is_peak(TABLE, datetime(2026, 11, 16, 7, 0)))

    def test_table_matches_documented_numbers(self):
        self.assertEqual((TABLE.off_peak.in_hit, TABLE.off_peak.in_miss, TABLE.off_peak.out),
                         (0.003, 0.15, 0.60))
        self.assertEqual((TABLE.peak.in_hit, TABLE.peak.in_miss, TABLE.peak.out),
                         (0.006, 0.30, 1.20))
        self.assertIn("api-docs.deepseek.com", TABLE.source)


class TestTurnCost(unittest.TestCase):
    def spec(self):
        return make_model_spec("deepseek-flash", "https://api.deepseek.com")

    def test_split_by_cache_and_off_peak(self):
        comp = Completion(text="", prompt_tokens=1_000_000, completion_tokens=1_000_000,
                          cache_hit_tokens=400_000, cache_miss_tokens=600_000)
        cost = pricing.turn_cost(self.spec(), comp, SAT_0700)
        self.assertAlmostEqual(cost, 0.4 * 0.003 + 0.6 * 0.15 + 1.0 * 0.60)

    def test_peak_doubles(self):
        comp = Completion(text="", prompt_tokens=1_000_000, completion_tokens=0,
                          cache_hit_tokens=0, cache_miss_tokens=1_000_000)
        self.assertAlmostEqual(pricing.turn_cost(self.spec(), comp, MON_0700), 0.30)
        self.assertAlmostEqual(pricing.turn_cost(self.spec(), comp, MON_0500), 0.15)

    def test_unreported_cache_counts_as_miss(self):
        """服务端没回报命中数：按全部未命中算（保守），不编命中率。"""
        comp = Completion(text="", prompt_tokens=1_000_000, completion_tokens=0)
        self.assertAlmostEqual(pricing.turn_cost(self.spec(), comp, SAT_0700), 0.15)

    def test_only_hit_reported_derives_miss(self):
        comp = Completion(text="", prompt_tokens=1_000_000, completion_tokens=0,
                          cache_hit_tokens=900_000)
        self.assertAlmostEqual(pricing.turn_cost(self.spec(), comp, SAT_0700),
                               0.9 * 0.003 + 0.1 * 0.15)

    def test_flat_prices_unchanged_without_table(self):
        """没有价目表时保持旧行为：prompt × in + completion × out。"""
        spec = ModelSpec(name="x", price_in_per_mtok=2.0, price_out_per_mtok=8.0)
        comp = Completion(text="", prompt_tokens=500_000, completion_tokens=250_000)
        self.assertAlmostEqual(pricing.turn_cost(spec, comp, SAT_0700), 1.0 + 2.0)

    def test_local_model_costs_nothing(self):
        spec = ModelSpec(name="local-model")
        comp = Completion(text="", prompt_tokens=10_000, completion_tokens=10_000)
        self.assertEqual(pricing.turn_cost(spec, comp, SAT_0700), 0.0)


if __name__ == "__main__":
    unittest.main()
