"""提交修复链路的测试：重放失败后，反馈到底有没有回到模型手里。

这一层不测模型强不强，只测装置对不对。现场来源是 crypto-005：
flag 已经算对，solve.py 在干净目录里 AttributeError，而原实现直接把
结论判死（unverified / script_error）——模型连自己挂在哪都不知道，
因为重放的 stdout / stderr 从来没被回传过。
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flagpilot import agent, verify  # noqa: E402
from flagpilot.client import Completion, ServerMetrics  # noqa: E402
from flagpilot.config import Budget, ModelSpec, RunConfig, SandboxLimits  # noqa: E402
from flagpilot.tasks import Task  # noqa: E402

FLAG = "picoCTF{repair_loop_0123456789}"

# 能真正从附件推导出 flag 的脚本——重放必须判 verified
GOOD_SCRIPT = (
    "import base64\n"
    "for line in open('challenge/data.txt'):\n"
    "    if 'encoded=' in line:\n"
    "        print(base64.b64decode(line.split('=', 1)[1]).decode())\n"
)
# 抛 AttributeError：stderr 有 traceback，verify 判 script_error
CRASH_SCRIPT = "x = None\nprint(x.group(1))\n"
# 正常退出但什么都不打印：verify 判 no_output
SILENT_SCRIPT = "value = 1\n"


def submit(flag: str, script: str) -> str:
    return json.dumps({"thought": "submit", "action": "submit",
                       "flag": flag, "script": script})


def make_task(root: Path) -> Task:
    d = root / "tasks" / "repair-001"
    d.mkdir(parents=True, exist_ok=True)
    enc = base64.b64encode(FLAG.encode()).decode()
    (d / "data.txt").write_text(f"header line\nencoded={enc}\ntrailer\n", encoding="utf-8")
    return Task(
        task_id=1, name="repair-001", category="Synthetic", title="repair",
        query="decode the payload", gold_sha256=hashlib.sha256(FLAG.encode()).hexdigest(),
        assets=["data.txt"], assets_dir="tasks/repair-001",
        suggested_tools=[], scope="core",
    )


class ScriptedClient:
    """按顺序吐出预设动作的假模型，并记下每一轮实际收到的 messages。"""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.seen: list[list[dict]] = []

    def complete(self, messages, max_tokens=None):
        self.seen.append([dict(m) for m in messages])
        text = self.replies.pop(0) if self.replies else \
            '{"thought":"idle","action":"shell","cmd":"echo idle"}'
        return Completion(text=text, prompt_tokens=10, completion_tokens=5,
                          ttft_ms=1, latency_ms=2)

    def scrape_metrics(self):
        return ServerMetrics()


class RepairCase(unittest.TestCase):
    """跑一次真实的 run_attempt（真沙箱、假模型）。"""

    def run_attempt(self, replies, max_turns=8):
        tmp = Path(tempfile.mkdtemp(prefix="fp-repair-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        task = make_task(tmp)
        cfg = RunConfig(
            model=ModelSpec(name="mock"),
            budget=Budget(max_turns=max_turns, max_wall_s=120),
            limits=SandboxLimits(action_timeout_s=10),
            replay_timeout_s=20.0,
            tasks_root=tmp,
        )
        client = ScriptedClient(replies)
        original = agent.build_client
        agent.build_client = lambda spec: client
        try:
            summary = agent.run_attempt(task, cfg, tmp, seed=0)
        finally:
            agent.build_client = original
        trace = tmp / "repair-001-s0" / "trace.jsonl"
        rows = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()
                if line.strip()]
        return summary, rows, client


class TestRepairMessage(unittest.TestCase):
    """纯函数层：哪种结论该给机会，给的是哪一类。"""

    def r(self, verdict, reason, **kw):
        return verify.ReplayResult(verdict, reason, **kw)

    def test_script_failures_offer_script_repair(self):
        for reason in ("script_error", "timeout", "literal_flag", "no_output"):
            with self.subTest(reason=reason):
                kind, msg = agent.repair_message(
                    self.r("unverified", reason, detail="detail-here", stdout="stdout-here"))
                self.assertEqual(kind, "script")
                self.assertIn(reason, msg)
                # 重放输出必须回传，否则模型只能盲猜
                self.assertIn("stdout-here", msg)

    def test_wrong_flag_offers_flag_repair(self):
        kind, msg = agent.repair_message(self.r("wrong", "wrong_flag"))
        self.assertEqual(kind, "flag")
        self.assertIn("FLAG REJECTED", msg)

    def test_success_is_terminal(self):
        self.assertIsNone(agent.repair_message(self.r("verified", "match")))
        self.assertIsNone(agent.repair_message(self.r("reproduced", "reproduced_no_gold")))

    def test_unknown_reason_is_terminal(self):
        self.assertIsNone(agent.repair_message(self.r("unverified", "weird_reason")))

    def test_stdout_tail_is_bounded(self):
        """长输出只回传尾部，别把上下文撑爆。"""
        _, msg = agent.repair_message(
            self.r("unverified", "script_error", detail="d", stdout="A" + "x" * 5000 + "TAIL"))
        self.assertIn("TAIL", msg)
        self.assertNotIn("A" + "x" * 2000, msg)


class TestRepairLoop(RepairCase):
    """端到端：反馈真的进了 messages，结论真的能转正。"""

    def test_script_error_gets_repaired_to_verified(self):
        summary, rows, client = self.run_attempt([
            submit(FLAG, CRASH_SCRIPT),
            submit(FLAG, GOOD_SCRIPT),
        ])
        self.assertEqual(summary["verdict"], "verified")
        self.assertEqual(summary["reason"], "match")
        # 首次提交的结论要单独留档，报告才能分开"一次过"和"修完才过"
        self.assertEqual(summary["first_submit_verdict"], "unverified")
        self.assertEqual(summary["first_submit_reason"], "script_error")
        self.assertEqual(summary["script_repairs"], 1)
        # 第二次请求里必须带着第一次的 traceback 和原因
        second = "\n".join(m["content"] for m in client.seen[1])
        self.assertIn("REPLAY FAILED", second)
        self.assertIn("script_error", second)
        self.assertIn("AttributeError", second)

    def test_literal_flag_gets_repair_feedback(self):
        summary, _, client = self.run_attempt([
            submit(FLAG, f'print("{FLAG}")\n'),
            submit(FLAG, GOOD_SCRIPT),
        ])
        self.assertEqual(summary["verdict"], "verified")
        self.assertEqual(summary["script_repairs"], 1)
        second = "\n".join(m["content"] for m in client.seen[1])
        self.assertIn("literal_flag", second)

    def test_flag_retry_does_not_eat_script_budget(self):
        """flag 先错、改对后脚本又挂：两个额度各用一次，最后仍能修好。

        这条是"独立额度"的设计意图——如果共用一个计数器，flag 那次重试
        就会把脚本修复的机会挤掉。
        """
        summary, _, _ = self.run_attempt([
            submit("picoCTF{nope}", GOOD_SCRIPT),
            submit(FLAG, CRASH_SCRIPT),
            submit(FLAG, GOOD_SCRIPT),
        ])
        self.assertEqual(summary["verdict"], "verified")
        self.assertEqual(summary["submit_retries"], 1)
        self.assertEqual(summary["script_repairs"], 1)
        self.assertEqual(summary["first_submit_verdict"], "wrong")

    def test_resubmitting_identical_payload_stops(self):
        """原样重发不给额度：同样的输入不会有新结论。"""
        summary, rows, _ = self.run_attempt([
            submit("picoCTF{nope}", GOOD_SCRIPT),
            submit("picoCTF{nope}", GOOD_SCRIPT),
        ])
        self.assertEqual(summary["verdict"], "wrong")
        self.assertEqual(summary["turns"], 2)
        repairs = [r for r in rows if r.get("type") == "submit_repair"]
        self.assertEqual(len(repairs), 1)

    def test_script_budget_exhaustion_terminates(self):
        """脚本额度用完后必须收手，不能无限重试。"""
        summary, _, _ = self.run_attempt([
            submit(FLAG, CRASH_SCRIPT),
            submit(FLAG, SILENT_SCRIPT),
            submit(FLAG, GOOD_SCRIPT),
        ])
        self.assertEqual(summary["verdict"], "unverified")
        self.assertEqual(summary["reason"], "no_output")
        self.assertEqual(summary["script_repairs"], 1)
        self.assertEqual(summary["turns"], 2)

    def test_clean_solve_spends_no_budget(self):
        summary, _, _ = self.run_attempt([submit(FLAG, GOOD_SCRIPT)])
        self.assertEqual(summary["verdict"], "verified")
        self.assertEqual(summary["submit_retries"], 0)
        self.assertEqual(summary["script_repairs"], 0)
        self.assertEqual(summary["first_submit_verdict"], "verified")


if __name__ == "__main__":
    unittest.main(verbosity=2)
