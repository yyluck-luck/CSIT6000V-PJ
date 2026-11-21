"""TACC 作业的日志打印：模型逐字输出按批打成 @@pd（不逐 token 刷屏），换轮时先把上一轮剩下的打出去。"""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flagpilot.remote import DeltaPrinter  # noqa: E402


class TestDeltaPrinter(unittest.TestCase):
    def lines(self, out):
        return [json.loads(l[5:]) for l in out.getvalue().splitlines() if l.startswith("@@pd ")]

    def test_batches_and_flushes_on_a_new_turn(self):
        clock = [0.0]
        p = DeltaPrinter(every_s=1.0, max_chars=1000)
        p._now = lambda: clock[0]
        out = io.StringIO()
        with redirect_stdout(out):
            p(1, "a")                 # the first chunk of a turn goes out at once
            p(1, "b"); p(1, "c")      # within a second: held
            clock[0] = 1.5
            p(1, "d")                 # a second later: b, c and d go out together
            p(2, "x")                 # a new turn: turn 1 has nothing left, turn 2 starts
            clock[0] = 1.6
            p(2, "y")
            p(3, "z")                 # the next turn flushes turn 2's tail first, then starts at once
        self.assertEqual(self.lines(out), [{"turn": 1, "d": "a"}, {"turn": 1, "d": "bcd"}, {"turn": 2, "d": "x"},
                                           {"turn": 2, "d": "y"}, {"turn": 3, "d": "z"}])

    def test_a_long_burst_goes_out_before_a_second_passes(self):
        p = DeltaPrinter(every_s=60, max_chars=5)
        p._now = lambda: 0.0
        out = io.StringIO()
        with redirect_stdout(out):
            p(1, "a"); p(1, "bcd"); p(1, "efg")
        self.assertEqual(self.lines(out), [{"turn": 1, "d": "a"}, {"turn": 1, "d": "bcdefg"}])


if __name__ == "__main__":
    unittest.main()
