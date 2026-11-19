"""Hybrid 升级的测试。

proposal 的承诺：stall 检测（连续 k 轮没有新证据）触发 L→H 升级，
并受 token 预算约束。这里验证判据、预算、以及升级后的记账。
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flagpilot import agent  # noqa: E402
from flagpilot.client import Completion, ServerMetrics  # noqa: E402
from flagpilot.config import (Budget, Hybrid, ModelSpec, RunConfig,  # noqa: E402
                              SandboxLimits)
from flagpilot.tasks import Task  # noqa: E402

FLAG = "picoCTF{hybrid_escalation_0123456789}"
GOOD_SCRIPT = (
    "import base64\n"
    "for line in open('challenge/data.txt'):\n"
    "    if 'encoded=' in line:\n"
    "        print(base64.b64decode(line.split('=', 1)[1]).decode())\n"
)
LOOP_ACTION = '{"thought":"again","action":"shell","cmd":"cat challenge/data.txt"}'
SUBMIT = json.dumps({"thought": "done", "action": "submit",
                     "flag": FLAG, "script": GOOD_SCRIPT})


def make_task(root: Path) -> Task:
    d = root / "tasks" / "hybrid-001"
    d.mkdir(parents=True, exist_ok=True)
    enc = base64.b64encode(FLAG.encode()).decode()
    (d / "data.txt").write_text(f"header\nencoded={enc}\ntrailer\n", encoding="utf-8")
    return Task(task_id=1, name="hybrid-001", category="Synthetic", title="h",
                query="q", gold_sha256=hashlib.sha256(FLAG.encode()).hexdigest(),
                assets=["data.txt"], assets_dir="tasks/hybrid-001",
                suggested_tools=[], scope="core")


class Stub:
    """按剧本吐动作的假 client。spec 用来区分 L / H。"""

    def __init__(self, spec, replies):
        self.spec = spec
        self.replies = list(replies)
        self.calls = 0

    def complete(self, messages, max_tokens=None):
        self.calls += 1
        text = self.replies.pop(0) if self.replies else LOOP_ACTION
        return Completion(text=text, prompt_tokens=100, completion_tokens=20,
                          ttft_ms=1, latency_ms=2)

    def scrape_metrics(self):
        return ServerMetrics()


class HybridCase(unittest.TestCase):
    def run_attempt(self, hybrid, l_replies, h_replies, max_turns=10):
        tmp = Path(tempfile.mkdtemp(prefix="fp-hybrid-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        task = make_task(tmp)
        hosted_spec = ModelSpec(name="fake-h", api_key="test-key")
        cfg = RunConfig(
            model=ModelSpec(name="mock"),
            budget=Budget(max_turns=max_turns, max_wall_s=120),
            limits=SandboxLimits(action_timeout_s=10),
            replay_timeout_s=20.0,
            tasks_root=tmp,
            hybrid=hybrid,
        )
        lc = Stub(ModelSpec(name="mock"), l_replies)
        hc = Stub(hosted_spec, h_replies)
        seen = []

        def fake_build(spec):
            seen.append(spec.name)
            return hc if spec.name == "fake-h" else lc

        original = agent.build_client
        agent.build_client = fake_build
        try:
            summary = agent.run_attempt(task, cfg, tmp, seed=0)
        finally:
            agent.build_client = original
        trace = tmp / "hybrid-001-s0" / "trace.jsonl"
        rows = [json.loads(l) for l in trace.read_text(encoding="utf-8").splitlines() if l.strip()]
        return summary, rows, lc, hc, seen


class TestStallDetection(HybridCase):
    def test_repeated_evidence_triggers_escalation(self):
        """同一份输出重复三轮 → stall → 升级到 H。"""
        summary, rows, lc, hc, _ = self.run_attempt(
            Hybrid(enabled=True, stall_turns=3, model=ModelSpec(name="fake-h", api_key="k")),
            [LOOP_ACTION] * 6, [SUBMIT])
        esc = [r for r in rows if r.get("type") == "escalate"]
        self.assertEqual(len(esc), 1, rows)
        self.assertEqual(esc[0]["to_model"], "fake-h")
        self.assertEqual(summary["hybrid"]["escalations"], 1)
        # 升级后由 H 完成提交
        self.assertEqual(summary["verdict"], "verified")
        self.assertGreater(hc.calls, 0)

    def test_fresh_evidence_does_not_escalate(self):
        """每轮都读到不同内容时不该误判成停滞。"""
        tmp_replies = [
            '{"thought":"a","action":"shell","cmd":"head -1 challenge/data.txt"}',
            '{"thought":"b","action":"shell","cmd":"tail -1 challenge/data.txt"}',
            '{"thought":"c","action":"file","path":"challenge/data.txt"}',
            SUBMIT,
        ]
        summary, rows, _, _, _ = self.run_attempt(
            Hybrid(enabled=True, stall_turns=3, model=ModelSpec(name="fake-h", api_key="k")),
            tmp_replies, [SUBMIT])
        self.assertEqual([r for r in rows if r.get("type") == "escalate"], [])
        self.assertEqual(summary["verdict"], "verified")

    def test_disabled_by_default(self):
        summary, rows, _, _, _ = self.run_attempt(
            Hybrid(), [LOOP_ACTION] * 5, [SUBMIT])
        self.assertFalse(summary["hybrid"]["enabled"])
        self.assertEqual([r for r in rows if r.get("type") == "escalate"], [])


class TestBudget(HybridCase):
    def test_missing_api_key_blocks_without_crash(self):
        spec = ModelSpec(name="fake-h", api_key="EMPTY")
        summary, rows, _, _, _ = self.run_attempt(
            Hybrid(enabled=True, stall_turns=2, model=spec), [LOOP_ACTION] * 6, [SUBMIT])
        skipped = [r for r in rows if r.get("type") == "escalate_skipped"]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["reason"], "missing_api_key")
        self.assertTrue(summary["hybrid"]["blocked"])
        self.assertEqual(summary["hybrid"]["escalations"], 0)

    def test_token_budget_blocks_escalation(self):
        summary, rows, _, _, _ = self.run_attempt(
            Hybrid(enabled=True, stall_turns=2, max_hosted_tokens=0,
                   model=ModelSpec(name="fake-h", api_key="k")),
            [LOOP_ACTION] * 6, [SUBMIT])
        self.assertEqual([r for r in rows if r.get("type") == "escalate"], [])
        self.assertEqual(summary["hybrid"]["escalations"], 0)

    def test_escalation_cap_respected(self):
        summary, rows, _, _, _ = self.run_attempt(
            Hybrid(enabled=True, stall_turns=1, max_escalations=1,
                   model=ModelSpec(name="fake-h", api_key="k")),
            [LOOP_ACTION] * 8, [LOOP_ACTION] * 8, max_turns=8)
        self.assertEqual(len([r for r in rows if r.get("type") == "escalate"]), 1)


class TestAccounting(HybridCase):
    def test_hosted_tokens_are_separate(self):
        summary, _, _, _, _ = self.run_attempt(
            Hybrid(enabled=True, stall_turns=2, model=ModelSpec(name="fake-h", api_key="k")),
            [LOOP_ACTION] * 6, [SUBMIT])
        h = summary["hybrid"]
        self.assertGreater(h["hosted_tokens"], 0)
        self.assertEqual(h["hosted_tokens"],
                         h["hosted_prompt_tokens"] + h["hosted_completion_tokens"])
        # L 轨的 token 照旧计，两个账本不互相污染
        self.assertGreater(summary["prompt_tokens"], 0)
        self.assertIn("cost_usd_total", summary)

    def test_escalation_turn_is_recorded(self):
        summary, _, _, _, _ = self.run_attempt(
            Hybrid(enabled=True, stall_turns=2, model=ModelSpec(name="fake-h", api_key="k")),
            [LOOP_ACTION] * 6, [SUBMIT])
        self.assertGreater(summary["hybrid"]["escalated_at_turn"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
