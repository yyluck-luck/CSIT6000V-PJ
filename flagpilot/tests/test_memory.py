"""上下文压缩、跨 run 记忆、会话持久化的测试。

三个契约：
  1. 压缩必须**保留** system、题面与最近几轮，且真的变小；
  2. 记忆只记结论，**不能把 flag 写进去**；
  3. 答疑会话落盘后能被 resume 回来（服务重启不丢）。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from flagpilot import chat, config, context
from flagpilot.client import Completion, ServerMetrics
from flagpilot.config import (Budget, RunConfig, SandboxLimits,
                              make_model_spec)
from flagpilot.memory import MemoryStore
from flagpilot.tasks import Task


def _msgs(turns: int, obs: str = "x" * 1200) -> list[dict]:
    out = [{"role": "system", "content": "S" * 200},
           {"role": "user", "content": "题目：" + "T" * 300}]
    for i in range(turns):
        out.append({"role": "assistant",
                    "content": json.dumps({"thought": "t", "action": "shell",
                                           "cmd": f"cat f{i}.txt"})})
        out.append({"role": "user", "content": f"[step {i+1}] shell\n"
                                               f"exit=0  time=0.01s\n--- output ---\n{obs}"})
    return out


class TestContextCompaction(unittest.TestCase):
    def test_short_history_is_untouched(self):
        msgs = _msgs(2)
        out, info = context.compact(msgs, keep_recent=6)
        self.assertEqual(len(out), len(msgs))
        self.assertEqual(info["dropped_msgs"], 0)

    def test_long_history_shrinks_and_keeps_edges(self):
        msgs = _msgs(12)
        out, info = context.compact(msgs, keep_recent=6)
        self.assertGreater(info["dropped_msgs"], 0)
        self.assertLess(info["after_chars"], info["before_chars"])
        self.assertEqual(out[0], msgs[0])          # system 原样
        self.assertEqual(out[-1], msgs[-1])        # 最近一轮原样
        self.assertIn("COMPACTED CONTEXT", out[1]["content"])

    def test_digest_keeps_action_and_result(self):
        out, _ = context.compact(_msgs(10), keep_recent=4)
        digest = out[1]["content"]
        self.assertIn("shell", digest)
        self.assertIn("cat f0.txt", digest)
        self.assertIn("exit=0", digest)

    def test_no_orphan_user_messages(self):
        """摘要与最近消息合并后，不应出现连续两条 user（有些模板会因此报错）。"""
        out, _ = context.compact(_msgs(10), keep_recent=5)
        roles = [m["role"] for m in out]
        self.assertFalse(any(a == b == "user" for a, b in zip(roles, roles[1:])))


class TestMemoryStore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-mem-"))
        self.store = MemoryStore(self.tmp)

    def test_empty_digest(self):
        self.assertEqual(self.store.digest("nope"), "")

    def test_record_and_digest(self):
        self.store.record("t-1", {"verdict": "verified", "reason": "match",
                                  "turns": 3, "wall_s": 12.5,
                                  "ablation": "B3", "model": "m1"})
        self.store.record("t-1", {"verdict": "budget", "reason": "max_turns",
                                  "turns": 20, "wall_s": 300,
                                  "ablation": "B0", "model": "m2"})
        self.assertEqual(self.store.count("t-1"), 2)
        d = self.store.digest("t-1")
        self.assertIn("跑过 2 次", d)
        self.assertIn("verified", d)
        self.assertIn("max_turns", d)

    def test_flag_never_persisted(self):
        """记忆里出现明文 flag，就等于把"解出"变成"查表"。"""
        self.store.record("t-2", {"verdict": "verified", "turns": 1,
                                  "flag": "picoCTF{secret_value}"})
        raw = self.store.task_file("t-2").read_text(encoding="utf-8")
        self.assertNotIn("secret_value", raw)
        self.assertNotIn("picoCTF", raw)

    def test_broken_line_is_skipped(self):
        p = self.store.task_file("t-3")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"verdict":"verified"}\n{broken\n', encoding="utf-8")
        self.assertEqual(self.store.count("t-3"), 1)


class _Fake:
    """总是要求执行同一个 shell 动作，用来把 run 推到轮次上限。"""

    def __init__(self):
        self.seen: list[list[dict]] = []

    def complete(self, messages, max_tokens=None, on_delta=None):
        self.seen.append([dict(m) for m in messages])
        return Completion(text='{"thought":"t","action":"shell","cmd":"echo hi"}',
                          prompt_tokens=10, completion_tokens=5,
                          ttft_ms=1, latency_ms=2)

    def scrape_metrics(self):
        return ServerMetrics()


class TestMemoryInjection(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-meminj-"))
        self.task = Task(-1, "memtask", "X", "", "", "0" * 64, [], "tasks/none",
                         [], "core")
        self.old_root = config.MEMORY_ROOT
        config.MEMORY_ROOT = self.tmp / "memory"

    def tearDown(self):
        config.MEMORY_ROOT = self.old_root

    def _cfg(self, memory: bool) -> RunConfig:
        # 模型名故意不叫 mock：agent 会跳过 mock 模型的记忆写入（防单测污染），
        # 这里要验证的是"读记忆"，所以借一个普通名字 + 打桩的 build_client。
        return RunConfig(budget=Budget(max_turns=2, max_wall_s=20),
                         limits=SandboxLimits(action_timeout_s=5),
                         model=make_model_spec("fake-model"),
                         memory=memory)

    def test_second_run_sees_previous_attempt(self):
        from flagpilot import agent as agent_mod

        fake = _Fake()
        old = agent_mod.build_client
        agent_mod.build_client = lambda spec: fake
        try:
            agent_mod.run_attempt(self.task, self._cfg(True), self.tmp / "a")
            agent_mod.run_attempt(self.task, self._cfg(True), self.tmp / "b")
        finally:
            agent_mod.build_client = old

        first_prompt = fake.seen[0][1]["content"]
        # 第一次不该有记忆；第二次的题面消息里应当带上历史
        self.assertNotIn("MEMORY:", first_prompt)
        self.assertTrue(any("MEMORY:" in m["content"]
                            for m in fake.seen[-1] if m["role"] == "user"))

    def test_memory_off_does_not_inject(self):
        from flagpilot import agent as agent_mod

        store = MemoryStore(config.MEMORY_ROOT)
        store.record("memtask", {"verdict": "verified", "turns": 1})
        fake = _Fake()
        old = agent_mod.build_client
        agent_mod.build_client = lambda spec: fake
        try:
            agent_mod.run_attempt(self.task, self._cfg(False), self.tmp / "c")
        finally:
            agent_mod.build_client = old
        self.assertNotIn("MEMORY:", fake.seen[0][1]["content"])


class TestChatPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-chatlog-"))
        self.cfg = RunConfig(model=make_model_spec("mock"),
                             limits=SandboxLimits(action_timeout_s=5))
        self._old = chat.build_client
        chat.build_client = lambda spec: _Fake()

    def tearDown(self):
        chat.build_client = self._old

    def test_log_then_resume(self):
        wd = self.tmp / "s1"
        sess = chat.ChatSession(self.cfg, task=None, workdir=wd)
        sess.ask("这题怎么下手")
        rows = chat.read_log(wd / "chat.jsonl")
        self.assertTrue(any(r["type"] == "user" for r in rows))
        self.assertTrue(any(r["type"] == "reply" for r in rows))

        again = chat.ChatSession.resume(self.cfg, None, wd)
        self.assertIsNotNone(again)
        self.assertEqual(again.turns, 1)
        self.assertIn("assistant", [m["role"] for m in again.messages])

    def test_resume_missing_returns_none(self):
        self.assertIsNone(chat.ChatSession.resume(self.cfg, None, self.tmp / "none"))


class TestAgentSummaryFields(unittest.TestCase):
    def test_summary_has_context_and_memory_fields(self):
        from flagpilot import agent as agent_mod

        tmp = Path(tempfile.mkdtemp(prefix="fp-sum-"))
        task = Task(-1, "sumtask", "X", "", "", "0" * 64, [], "tasks/none", [], "core")
        cfg = RunConfig(budget=Budget(max_turns=1, max_wall_s=20),
                        limits=SandboxLimits(action_timeout_s=5),
                        model=make_model_spec("mock"))
        old_root = config.MEMORY_ROOT
        config.MEMORY_ROOT = tmp / "memory"
        old = agent_mod.build_client
        agent_mod.build_client = lambda spec: _Fake()
        try:
            sumry = agent_mod.run_attempt(task, cfg, tmp / "r")
        finally:
            agent_mod.build_client = old
            config.MEMORY_ROOT = old_root
        for k in ("compactions", "ctx_chars", "memory", "memory_hits"):
            self.assertIn(k, sumry)


if __name__ == "__main__":
    unittest.main()
