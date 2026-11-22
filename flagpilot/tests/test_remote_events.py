"""A run on TACC shows the same steps on the page as a run on this machine.

The page learns about a run on TACC only from the events the job prints (flagpilot/remote.py PAGE_EVENTS). An event the
page handles but the job does not print is silently missing for TACC runs: on 2026-11-22 the new "action" and
"repeat_blocked" events would have been. These tests compare the list with the page's onEvent and the engine's events.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flagpilot.remote import PAGE_EVENTS  # noqa: E402

# events of onEvent a run on TACC cannot produce: step mode (await, decision), hints and extra turns (budget) need the
# local control channel, which a TACC job does not have; tacc comes from the web server, model_partial from @@pd lines
NOT_TRACE = {"await", "decision", "hint", "budget", "tacc", "model_partial"}


def page_events() -> set[str]:
    html = (ROOT / "app" / "index.html").read_text(encoding="utf-8")
    body = html[html.index("function onEvent("):]
    return set(re.findall(r"case '([a-z_]+)'", body[:body.index("\n}\n")]))


def engine_events() -> set[str]:
    return set(re.findall(r'tr\.event\(\s*"([a-z_]+)"', (ROOT / "flagpilot" / "agent.py").read_text(encoding="utf-8")))


class TestTaccRunsForwardWhatThePageShows(unittest.TestCase):
    def test_every_trace_event_the_page_handles_is_printed_by_the_job(self):
        missing = page_events() - NOT_TRACE - set(PAGE_EVENTS)
        self.assertFalse(missing, f"the page handles {sorted(missing)}, which a run on TACC never sends")

    def test_the_lists_name_real_events(self):
        shown = page_events()
        self.assertIn("action", shown)
        self.assertIn("repeat_blocked", shown)
        unknown = set(PAGE_EVENTS) - engine_events() - {"meta", "start", "summary", "done"}
        self.assertFalse(unknown, f"PAGE_EVENTS names events the engine does not emit: {sorted(unknown)}")


if __name__ == "__main__":
    unittest.main()
