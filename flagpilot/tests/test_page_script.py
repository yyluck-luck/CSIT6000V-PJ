"""The page's script (app/index.html) is one plain <script>: two function declarations with the same name are legal
there, and the later one silently replaces the earlier everywhere. 2026-11-21: a new setTurns(n) for the turn budget
would have replaced the turn counter's setTurns(n, max) without any error (`node --check` and the browser accept it)."""

from __future__ import annotations

import re
import unittest
from collections import Counter
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "app" / "index.html"


class TestPageScript(unittest.TestCase):
    def test_no_two_functions_share_a_name(self):
        html = PAGE.read_text(encoding="utf-8")
        # top-level declarations start the line (inner functions in different scopes may share a name, legally)
        names = re.findall(r"^(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(", html, re.M)
        self.assertGreater(len(names), 100, "the scan found the page's functions")
        dup = sorted(n for n, c in Counter(names).items() if c > 1)
        self.assertEqual(dup, [], f"declared more than once (the later one wins silently): {dup}")


if __name__ == "__main__":
    unittest.main()
