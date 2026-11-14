"""答疑/讲解模式的测试。

两个契约必须锁死：
  1. `<run>` 里的命令**真的**在沙箱里执行，输出被写回历史；
  2. 关掉工具后，命令一律不执行（否则"不允许执行"就成了摆设）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from flagpilot import chat, custom
from flagpilot.client import Completion
from flagpilot.config import RunConfig, SandboxLimits, make_model_spec


class FakeClient:
    """按脚本逐次回复，并在调用时记录它收到了什么。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0
        self.seen: list[list[dict]] = []

    def scrape_metrics(self):
        from flagpilot.client import ServerMetrics

        return ServerMetrics(error="not configured")

    def complete(self, messages, max_tokens=None, on_delta=None):
        self.calls += 1
        self.seen.append([dict(m) for m in messages])
        text = self.replies[min(self.calls - 1, len(self.replies) - 1)]
        if on_delta is not None:
            on_delta(text)
        return Completion(text=text, prompt_tokens=10, completion_tokens=8,
                          ttft_ms=1, latency_ms=2, finish_reason="stop",
                          usage_source="estimated")


class TestParseCommands(unittest.TestCase):
    def test_basic(self):
        cmds, body = chat.parse_commands("<run>ls -la</run>\n我看一下目录。")
        self.assertEqual(cmds, ["ls -la"])
        self.assertIn("我看一下目录", body)
        self.assertNotIn("<run>", body)

    def test_fence_variant(self):
        cmds, body = chat.parse_commands("先跑一下：\n```bash\nstrings a.bin\n```\n然后再说。")
        self.assertEqual(cmds, ["strings a.bin"])
        self.assertNotIn("```", body)

    def test_no_command(self):
        cmds, body = chat.parse_commands("这道题是 ROT13，直接解就行。")
        self.assertEqual(cmds, [])
        self.assertEqual(body, "这道题是 ROT13，直接解就行。")

    def test_multiple(self):
        cmds, _ = chat.parse_commands("<run>a</run><run>b</run><run>c</run><run>d</run>")
        self.assertEqual(cmds, ["a", "b", "c", "d"])


class TestChatSession(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-chat-"))
        self.task = custom.create(
            title="讲解用题",
            query="附件里有答案",
            files=[("note.txt", b"hello from challenge\n")],
            root=self.tmp / "custom",
        )
        self.cfg = RunConfig(model=make_model_spec("mock"),
                             limits=SandboxLimits(action_timeout_s=15))

    def tearDown(self):
        import shutil

        for p in self.tmp.rglob("*"):
            try:
                p.chmod(0o755)
            except Exception:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _patch(self, fake):
        old = chat.build_client
        chat.build_client = lambda spec: fake
        self.addCleanup(lambda: setattr(chat, "build_client", old))

    def test_tool_is_really_executed(self):
        fake = FakeClient([
            "<run>cat challenge/note.txt</run>\n我先看文件内容。",
            "文件里写着 hello from challenge，所以这题只是读文件。",
        ])
        self._patch(fake)
        sess = chat.ChatSession(self.cfg, task=self.task,
                                workdir=self.tmp / "work" / "s1")
        reply = sess.ask("这道题该从哪下手？")

        self.assertEqual(len(reply.steps), 1)
        self.assertIn("hello from challenge", reply.steps[0].output)
        self.assertEqual(reply.steps[0].exit_code, 0)
        self.assertIn("hello from challenge", reply.answer)
        # 命令输出必须以 OBSERVATION 的形式回灌给模型
        self.assertTrue(any(m["content"].startswith("OBSERVATION")
                            for m in fake.seen[1] if m["role"] == "user"))

    def test_tools_disabled(self):
        fake = FakeClient(["<run>rm -rf challenge</run>\n我不能执行，只能讲解。"])
        self._patch(fake)
        sess = chat.ChatSession(self.cfg, task=self.task,
                                workdir=self.tmp / "work" / "s2", use_tools=False)
        reply = sess.ask("随便跑点什么")
        self.assertEqual(reply.steps, [], "关掉工具后不得执行任何命令")
        self.assertFalse((Path(self.task.assets_dir) / "gone").exists())

    def test_max_rounds_stops(self):
        fake = FakeClient(["<run>echo loop</run>\n再跑一次。"])
        self._patch(fake)
        sess = chat.ChatSession(self.cfg, task=self.task,
                                workdir=self.tmp / "work" / "s3", max_rounds=2)
        reply = sess.ask("一直跑")
        self.assertEqual(len(reply.steps), 2, "max_rounds 应限制执行轮数")
        self.assertEqual(reply.rounds, 3)  # 2 次执行 + 1 次收尾
        self.assertTrue(reply.answer)

    def test_coach_mode_injects_constraint(self):
        """用户说"不要直接给答案"时，本轮必须加一条硬约束。"""
        fake = FakeClient(["好，只讲方法。"])
        self._patch(fake)
        sess = chat.ChatSession(self.cfg, task=self.task, workdir=self.tmp / "work" / "s5")
        sess.ask("不要直接给答案，讲讲方法")
        sent = fake.seen[0][-1]["content"]
        self.assertIn("本轮约束", sent)
        self.assertIn("不要输出 flag", sent)

    def test_coach_mode_off_by_default(self):
        fake = FakeClient(["这是答案。"])
        self._patch(fake)
        sess = chat.ChatSession(self.cfg, task=self.task, workdir=self.tmp / "work" / "s6")
        sess.ask("这题考什么")
        self.assertNotIn("本轮约束", fake.seen[0][-1]["content"])

    def test_coach_mode_masks_flag_end_to_end(self):
        """提示模式：工具输出、回灌历史、最终回答里都不能出现明文 flag。"""
        task = custom.create(
            title="会泄漏的题", query="文件里直接有答案",
            files=[("leak.txt", b"answer: picoCTF{leak_me_123456}\n")],
            root=self.tmp / "custom",
        )
        fake = FakeClient([
            "<run>cat challenge/leak.txt</run>\n我看一下文件。",
            "文件里写着 picoCTF{leak_me_123456}。",
        ])
        self._patch(fake)
        sess = chat.ChatSession(self.cfg, task=task, workdir=self.tmp / "work" / "s7")
        reply = sess.ask("别直接给答案，给我思路")

        self.assertNotIn("leak_me_123456", reply.steps[0].output)
        self.assertNotIn("leak_me_123456", reply.answer)
        obs = fake.seen[1][-1]["content"]
        self.assertNotIn("leak_me_123456", obs, "回灌给模型的历史也必须是脱敏后的")

    def test_history_is_kept(self):
        fake = FakeClient(["第一问的回答", "第二问的回答"])
        self._patch(fake)
        sess = chat.ChatSession(self.cfg, task=None, workdir=self.tmp / "work" / "s4")
        sess.ask("问题一")
        sess.ask("问题二")
        # 第二轮请求里应该能看到第一轮的问答
        blob = "\n".join(m["content"] for m in fake.seen[1])
        self.assertIn("问题一", blob)
        self.assertIn("第一问的回答", blob)


if __name__ == "__main__":
    unittest.main()
