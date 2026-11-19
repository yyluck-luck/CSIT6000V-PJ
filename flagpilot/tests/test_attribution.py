"""失败归因的三处修正。

背景：27B 实地测试里 6 条失败有 3 条标签是错的——全部是 `max_tokens` 打满
导致的 JSON 截断，却被记成"不守 JSON 协议"。这不只是数字难看：

    记成"协议问题" → 结论是"小模型不听话"，于是去改 prompt；
    实际是"预算配小了" → 该改的是 max_tokens 或让它少写。

所以这里锁三件事：
  1. 截断能被认出来（finish_reason 优先，token 计数兜底）
  2. 三类失败的纠正语不同（截断不能喂"你的 JSON 格式错了"）
  3. 已经跑完的老 trace 也能被重新判对，不必重跑才能出正确报表
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from flagpilot import agent, tagger
from flagpilot.client import Completion
from flagpilot.config import RunConfig
from flagpilot.sandbox import ExecResult
from flagpilot.tools import Action


def comp(text="", completion_tokens=0, finish_reason="stop", usage_source="reported"):
    return Completion(text=text, completion_tokens=completion_tokens,
                      finish_reason=finish_reason, usage_source=usage_source)


class TestTruncationSignal(unittest.TestCase):
    def setUp(self):
        self.cfg = RunConfig()
        self.max = self.cfg.model.max_tokens

    def test_finish_reason_length_is_authoritative(self):
        # 服务端说 length，就是截断，不管 token 计数怎么报
        self.assertEqual(
            agent.truncation_signal(comp("{\"act", 10, "length"), self.max), "length")

    def test_max_tokens_finish_reason_also_counts(self):
        # vLLM 用 length，部分运行时用 max_tokens，语义相同，归一成一档
        self.assertEqual(
            agent.truncation_signal(comp("x", 10, "max_tokens"), self.max), "length")

    def test_token_count_fallback(self):
        # 有些运行时不报 finish_reason，只能看计数
        self.assertEqual(
            agent.truncation_signal(comp("x", self.max, ""), self.max), "tokens")

    def test_estimated_usage_is_not_evidence(self):
        # 估算的 usage 撞上限不能当证据——那是我们自己的粗估，不是服务端事实
        self.assertEqual(
            agent.truncation_signal(
                comp("x", self.max, "", usage_source="estimated"), self.max), "")

    def test_normal_reply_is_not_truncated(self):
        self.assertEqual(
            agent.truncation_signal(comp("{\"a\":1}", self.max - 5), self.max), "")


class TestCorrectionDispatch(unittest.TestCase):
    def test_truncated_asks_for_less_prose(self):
        msg = agent.correction_message("truncated", "输出被截断", 1536)
        self.assertIn("TRUNCATED", msg)
        self.assertIn("1536", msg)
        # 关键：不能再说"你的 JSON 格式不对"，那会让它继续解释
        self.assertNotIn("INVALID ACTION", msg)

    def test_thinking_overflow_asks_for_no_thinking(self):
        msg = agent.correction_message("thinking_overflow", "空回复", 1536)
        self.assertIn("EMPTY REPLY", msg)
        self.assertNotIn("INVALID ACTION", msg)

    def test_protocol_keeps_original_template(self):
        msg = agent.correction_message("protocol", "没有解析到合法的 JSON 动作", 1536)
        self.assertIn("INVALID ACTION", msg)
        self.assertIn("没有解析到合法的 JSON 动作", msg)


class TestReadonlyHint(unittest.TestCase):
    def res(self, stderr="", exit_code=1):
        return ExecResult(cmd="", ok=exit_code == 0, exit_code=exit_code,
                          timed_out=False, duration_ms=5, stdout="", stderr=stderr)

    def test_hint_on_permission_denied_in_challenge(self):
        act = Action(thought="", action="shell", cmd="cd challenge && tar xzf a.tgz")
        out = agent.format_observation(
            3, act, self.res("tar: a.tgz: Cannot open: Permission denied"))
        self.assertIn("READ-ONLY", out)
        self.assertIn("cp challenge/x.tgz .", out)

    def test_no_hint_when_successful(self):
        act = Action(thought="", action="shell", cmd="ls challenge")
        out = agent.format_observation(3, act, self.res("", exit_code=0))
        self.assertNotIn("READ-ONLY", out)

    def test_no_hint_when_unrelated_path(self):
        # 别的目录权限问题不该甩这句提示，否则每轮都在误导
        act = Action(thought="", action="shell", cmd="cat notes.txt")
        out = agent.format_observation(3, act, self.res("cat: notes.txt: Permission denied"))
        self.assertNotIn("READ-ONLY", out)


class TestPromptCarriesWorkdir(unittest.TestCase):
    def test_user_template_has_absolute_workdir(self):
        text = agent.USER_TEMPLATE.format(
            name="t", category="Cryptography", brief="b", turns=12,
            workdir="/tmp/run/work")
        self.assertIn("/tmp/run/work", text)
        # 光给路径不够：模型看到路径照样会去猜别处，得明确说"不要乱逛"
        self.assertIn("do NOT search the filesystem", text)

    def test_script_must_use_relative_paths(self):
        # 提示词里给了绝对路径，就必须同时警告别把它写进 solve.py，
        # 否则重放环境里那个路径不存在，脚本必挂。
        prompt = agent.build_system_prompt(agent.Ablation())
        self.assertIn("RELATIVE", prompt)


class TestTaggerAttribution(unittest.TestCase):
    def rec(self, **kw):
        base = {"run_id": "t-s0", "verdict": "budget", "reason": "invalid_action",
                "turns": 12, "invalids": 3,
                "replay": {"reason": "", "detail": ""}, "run_dir": ""}
        base.update(kw)
        return base

    def _trace(self, root: Path, lines: list[str]) -> Path:
        d = root / "t-s0"
        d.mkdir()
        (d / "trace.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return d

    def test_new_trace_reason_truncated(self):
        c = tagger.classify_run(None, self.rec(reason="truncated"))
        self.assertEqual(c["primary"], "truncated")
        self.assertNotIn("protocol", c["tags"])

    def test_old_trace_is_relabelled(self):
        """老 trace 没有 kind 字段，但 model 事件里有 token 计数——必须能反推。"""
        root = Path(tempfile.mkdtemp(prefix="fp-attr-"))
        try:
            d = self._trace(root, [
                '{"type":"meta","model":{"max_tokens":1536}}',
                '{"type":"model","turn":7,"completion_tokens":1536}',
                '{"type":"invalid_action","turn":7,"reason":"没有解析到合法的 JSON 动作"}',
                '{"type":"model","turn":8,"completion_tokens":1536}',
                '{"type":"invalid_action","turn":8,"reason":"没有解析到合法的 JSON 动作"}',
            ])
            c = tagger.classify_run(d, self.rec())
            self.assertEqual(c["primary"], "truncated")
            self.assertNotIn("protocol", c["tags"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_old_trace_short_reply_stays_protocol(self):
        """同样老 trace，但 token 没打满 —— 那才是真的不守协议，别一起放过。"""
        root = Path(tempfile.mkdtemp(prefix="fp-attr-"))
        try:
            d = self._trace(root, [
                '{"type":"meta","model":{"max_tokens":1536}}',
                '{"type":"model","turn":3,"completion_tokens":90}',
                '{"type":"invalid_action","turn":3,"reason":"没有解析到合法的 JSON 动作"}',
            ])
            c = tagger.classify_run(d, self.rec(invalids=3))
            self.assertEqual(c["primary"], "protocol")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_mixed_truncation_and_garbage_stays_protocol(self):
        """既有截断又有真的乱答：protocol 是更根本的问题，不能被截断盖掉。"""
        root = Path(tempfile.mkdtemp(prefix="fp-attr-"))
        try:
            d = self._trace(root, [
                '{"type":"meta","model":{"max_tokens":1536}}',
                '{"type":"model","turn":3,"completion_tokens":1536}',
                '{"type":"invalid_action","turn":3,"reason":"没有解析到合法的 JSON 动作"}',
                '{"type":"model","turn":4,"completion_tokens":40}',
                '{"type":"invalid_action","turn":4,"reason":"没有解析到合法的 JSON 动作"}',
            ])
            c = tagger.classify_run(d, self.rec())
            self.assertEqual(c["primary"], "protocol")
            self.assertIn("truncated", c["tags"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_empty_reply_still_thinking_overflow(self):
        root = Path(tempfile.mkdtemp(prefix="fp-attr-"))
        try:
            d = self._trace(root, [
                '{"type":"meta","model":{"max_tokens":1536}}',
                '{"type":"model","turn":2,"completion_tokens":1536}',
                '{"type":"invalid_action","turn":2,"reason":"空回复"}',
            ])
            c = tagger.classify_run(d, self.rec(invalids=3))
            self.assertEqual(c["primary"], "thinking_overflow")
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
