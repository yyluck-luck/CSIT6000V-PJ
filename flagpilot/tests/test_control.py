"""人在回路（网页单步 / 暂停 / 插提示）：控制器本身，以及它在 agent 主循环里的效果。"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_live_stream import FLAG, SCRIPT, make_task  # noqa: E402

from flagpilot import agent  # noqa: E402
from flagpilot.client import Completion  # noqa: E402
from flagpilot.config import Budget, ModelSpec, RunConfig, SandboxLimits  # noqa: E402
from flagpilot.control import RunControl  # noqa: E402


class TestRunControl(unittest.TestCase):
    def test_no_waiting_unless_step_or_paused(self):
        c = RunControl()
        self.assertEqual(c.gate(1, "shell", "cmd", "ls", lambda i: self.fail("must not wait")), ("approve", None, False))

    def test_decisions_need_a_waiting_step_and_hints_need_text(self):
        c = RunControl()
        self.assertFalse(c.set("approve"))
        self.assertFalse(c.set("hint", "   "))
        self.assertFalse(c.set("nonsense"))
        self.assertTrue(c.set("hint", "  try   base64 "))
        self.assertEqual(c.take_hints(), ["try base64"])
        self.assertEqual(c.take_hints(), [])

    def test_step_mode_blocks_until_a_decision(self):
        c = RunControl()
        c.set("step", True)
        seen = []
        def decide():
            while c.state()["waiting"] is None:
                time.sleep(0.01)
            c.set("edit", "echo hi")
        threading.Thread(target=decide).start()
        self.assertEqual(c.gate(3, "shell", "cmd", "rm -rf x", seen.append), ("edit", "echo hi", True))
        self.assertEqual(seen[0]["text"], "rm -rf x")
        self.assertIsNone(c.state()["waiting"])

    def test_resume_releases_a_pause_and_stop_wins(self):
        c = RunControl()
        c.set("pause")
        threading.Timer(0.05, c.set, args=("resume",)).start()
        self.assertEqual(c.gate(1, "shell", "cmd", "ls", lambda i: None)[0], "approve")
        c.set("stop")
        self.assertEqual(c.gate(2, "shell", "cmd", "ls", lambda i: None)[0], "stop")


class Scripted:
    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def scrape_metrics(self):
        from flagpilot.client import ServerMetrics
        return ServerMetrics(error="not configured")

    def complete(self, messages, max_tokens=None, on_delta=None):
        self.seen.append([dict(m) for m in messages])
        text = self.replies.pop(0)
        return Completion(text=text, prompt_tokens=10, completion_tokens=5, ttft_ms=1, latency_ms=2)


class TestControlInTheLoop(unittest.TestCase):
    def test_edit_skip_approve_and_hint_reach_the_trace_and_the_model(self):
        tmp = Path(tempfile.mkdtemp(prefix="fp-ctl-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        cfg = RunConfig(model=ModelSpec(name="mock"), budget=Budget(max_turns=8, max_wall_s=120),
                        limits=SandboxLimits(action_timeout_s=10), replay_timeout_s=20.0, tasks_root=tmp)
        client = Scripted([
            json.dumps({"thought": "look", "action": "shell", "cmd": "cat challenge/data.txt"}),
            json.dumps({"thought": "list", "action": "shell", "cmd": "ls -la"}),
            json.dumps({"thought": "look again", "action": "shell", "cmd": "cat challenge/data.txt"}),
            json.dumps({"thought": "decode", "action": "submit", "flag": FLAG, "script": SCRIPT}),
        ])
        ctl = RunControl()
        ctl.set("step", True)
        ctl.set("hint", "the data is base64")
        plan = [("edit", "echo edited-by-human"), ("skip", None), ("approve", None)]

        def human():
            last = None
            for op, value in plan:
                while True:            # 等下一条没处理过的命令（运行可能已经越过了上一条）
                    w = ctl.state()["waiting"]
                    if w is not None and w["turn"] != last:
                        break
                    time.sleep(0.005)
                last = w["turn"]
                self.assertFalse(ctl.set(op, value, turn=last + 99), "a decision for another step is refused")
                ctl.set(op, value, turn=last)
        t = threading.Thread(target=human, daemon=True)
        t.start()
        original = agent.build_client
        agent.build_client = lambda spec: client
        try:
            summary = agent.run_attempt(make_task(tmp), cfg, tmp, seed=0, control=ctl)
        finally:
            agent.build_client = original
        t.join(5)
        self.assertEqual(summary["verdict"], "verified")
        rows = [json.loads(x) for x in (tmp / "live-001-s0" / "trace.jsonl").read_text().splitlines() if x.strip()]
        kinds = [r["type"] for r in rows]
        self.assertEqual([r["text"] for r in rows if r["type"] == "hint"], ["the data is base64"])
        self.assertEqual(kinds.count("await"), 3)
        self.assertEqual([r["op"] for r in rows if r["type"] == "decision"], ["edit", "skip", "approve"])
        self.assertEqual([r["cmd"] for r in rows if r["type"] == "tool"], ["echo edited-by-human", "cat challenge/data.txt"])
        first = "\n".join(m["content"] for m in client.seen[0])
        self.assertIn("HINT from the person watching this run", first)
        third = client.seen[2][-1]["content"]
        self.assertIn("SKIPPED", third)

    def test_more_turns_given_during_a_run_are_used(self):
        """2026-11-22: turns are set before a run; while it runs, the page can only add to them (Solve's "+20 turns")."""
        tmp = Path(tempfile.mkdtemp(prefix="fp-ctl-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        cfg = RunConfig(model=ModelSpec(name="mock"), budget=Budget(max_turns=2, max_wall_s=120),
                        limits=SandboxLimits(action_timeout_s=10), replay_timeout_s=20.0, tasks_root=tmp)
        client = Scripted([json.dumps({"thought": "look", "action": "shell", "cmd": "cat challenge/data.txt"}),
                           json.dumps({"thought": "list", "action": "shell", "cmd": "ls -la"}),
                           json.dumps({"thought": "again", "action": "shell", "cmd": "cat challenge/data.txt"}),
                           json.dumps({"thought": "decode", "action": "submit", "flag": FLAG, "script": SCRIPT})])
        ctl = RunControl()
        self.assertFalse(ctl.set("turns", 0))
        self.assertFalse(ctl.set("turns", "many"))
        self.assertFalse(ctl.set("turns", 500))
        self.assertTrue(ctl.set("turns", 4))
        original = agent.build_client
        agent.build_client = lambda spec: client
        try:
            summary = agent.run_attempt(make_task(tmp), cfg, tmp, seed=0, control=ctl)
        finally:
            agent.build_client = original
        self.assertEqual((summary["verdict"], summary["turns"]), ("verified", 4))
        rows = [json.loads(x) for x in (tmp / "live-001-s0" / "trace.jsonl").read_text().splitlines() if x.strip()]
        self.assertEqual([(r["max_turns"], r["was"]) for r in rows if r["type"] == "budget"], [(4, 2)])

    def test_turns_given_while_the_last_turn_waits_for_approval_are_used(self):
        """The run is on its last turn, waiting for an OK in step mode, when "+20 turns" is clicked: the loop used to test the
        budget before taking the new turns, so the run ended out of turns although more had been given."""
        tmp = Path(tempfile.mkdtemp(prefix="fp-ctl-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        cfg = RunConfig(model=ModelSpec(name="mock"), budget=Budget(max_turns=1, max_wall_s=120),
                        limits=SandboxLimits(action_timeout_s=10), replay_timeout_s=20.0, tasks_root=tmp)
        client = Scripted([json.dumps({"thought": "look", "action": "shell", "cmd": "cat challenge/data.txt"}),
                           json.dumps({"thought": "list", "action": "shell", "cmd": "ls -la"}),
                           json.dumps({"thought": "decode", "action": "submit", "flag": FLAG, "script": SCRIPT})])
        ctl = RunControl()
        ctl.set("step", True)

        def human():
            while ctl.state()["waiting"] is None:
                time.sleep(0.005)
            self.assertTrue(ctl.set("turns", 3))
            ctl.set("step", False)
            ctl.set("approve", None, turn=ctl.state()["waiting"]["turn"])
        t = threading.Thread(target=human, daemon=True)
        t.start()
        original = agent.build_client
        agent.build_client = lambda spec: client
        try:
            summary = agent.run_attempt(make_task(tmp), cfg, tmp, seed=0, control=ctl)
        finally:
            agent.build_client = original
        t.join(5)
        self.assertEqual((summary["verdict"], summary["turns"]), ("verified", 3), summary)
        rows = [json.loads(x) for x in (tmp / "live-001-s0" / "trace.jsonl").read_text().splitlines() if x.strip()]
        self.assertEqual([(r["max_turns"], r["was"], r["turn"]) for r in rows if r["type"] == "budget"], [(3, 1, 2)])


if __name__ == "__main__":
    unittest.main()
