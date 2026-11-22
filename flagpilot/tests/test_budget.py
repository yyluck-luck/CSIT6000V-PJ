"""Turns per attempt (2026-11-21, "why only 20 rounds?").

20 turns stays the evaluation's budget: every batch in the README (v5-v7) used it, so Budget.for_turns(20) must equal the
budget those batches had. More turns scale the cumulative output-token and time budgets with them (1,600 tokens and
75 s a turn); otherwise a longer attempt would stop on those first.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flagpilot import cli  # noqa: E402
from flagpilot.config import Budget  # noqa: E402


class TestBudget(unittest.TestCase):
    def test_twenty_turns_is_exactly_the_evaluation_budget(self):
        self.assertEqual(Budget.for_turns(20), Budget())
        self.assertEqual((Budget().max_turns, Budget().max_completion_tokens, Budget().max_wall_s), (20, 32_000, 1_500))

    def test_more_turns_bring_tokens_and_time_with_them(self):
        for turns, tokens, wall in ((40, 64_000, 3_000), (60, 96_000, 4_500), (100, 160_000, 7_500)):
            b = Budget.for_turns(turns)
            self.assertEqual((b.max_turns, b.max_completion_tokens, b.max_wall_s), (turns, tokens, wall))
            self.assertEqual(b.max_prompt_tokens, Budget().max_prompt_tokens, "a per-request limit: unchanged")
        self.assertEqual(Budget.for_turns(40, wall_s=900).max_wall_s, 900, "an explicit time budget wins")

    def test_the_cli_uses_the_same_budget(self):
        p = cli.build_parser()
        base = ["batch", "--batch", "b", "--model", "mock"]
        self.assertEqual(cli._build_cfg(p.parse_args(base)).budget, Budget())
        b = cli._build_cfg(p.parse_args(base + ["--turns", "40"])).budget
        self.assertEqual((b.max_turns, b.max_completion_tokens, b.max_wall_s), (40, 64_000, 3_000))
        self.assertEqual(cli._build_cfg(p.parse_args(base + ["--turns", "40", "--wall", "600"])).budget.max_wall_s, 600)


if __name__ == "__main__":
    unittest.main()
