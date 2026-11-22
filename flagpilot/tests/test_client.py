"""客户端测试：用一个假服务端模拟"不认识 chat_template_kwargs"的运行时。

这个场景不是假设——Ollama / llama.cpp / 老版本 vLLM 都可能直接 400。
客户端必须能自己降级，否则本地拿真模型做冒烟会一开始就卡住。
"""

from __future__ import annotations

import io
import json
import os
import sys
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flagpilot.client import ChatClient  # noqa: E402
from flagpilot.config import ModelSpec  # noqa: E402

REQUESTS: list[dict] = []


class Stub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        REQUESTS.append(body)

        if "chat_template_kwargs" in body:
            msg = json.dumps({"error": "unknown field chat_template_kwargs"}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(msg)
            self.close_connection = True
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        for chunk in (
            {"choices": [{"delta": {"content": '{"action":"shell"'}}]},
            {"choices": [{"delta": {"content": ',"cmd":"ls"}'}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 7}},
        ):
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True


class TestDowngrade(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        REQUESTS.clear()
        self.client = ChatClient(ModelSpec(
            name="stub",
            base_url=f"http://127.0.0.1:{self.port}/v1",
            enable_thinking=False,
        ))

    def test_retries_without_template_kwargs(self):
        comp = self.client.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(comp.error, "")
        self.assertIn("shell", comp.text)
        self.assertIn("降级", comp.note)
        self.assertEqual(len(REQUESTS), 2, "第一次 400，应当重试一次")
        self.assertIn("chat_template_kwargs", REQUESTS[0])
        self.assertNotIn("chat_template_kwargs", REQUESTS[1])

    def test_downgrade_is_sticky(self):
        self.client.complete([{"role": "user", "content": "hi"}])
        REQUESTS.clear()
        self.client.complete([{"role": "user", "content": "again"}])
        self.assertEqual(len(REQUESTS), 1, "降级后不应再发会被拒的字段")
        self.assertNotIn("chat_template_kwargs", REQUESTS[0])

    def test_usage_is_reported(self):
        comp = self.client.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(comp.prompt_tokens, 12)
        self.assertEqual(comp.completion_tokens, 7)
        self.assertEqual(comp.usage_source, "reported")


class TestPayloadFields(unittest.TestCase):
    """关闭思维链要靠 reasoning_effort，而不是 /no_think（Ollama 忽略后者）。"""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        REQUESTS.clear()

    def mk(self, thinking: bool):
        return ChatClient(ModelSpec(name="stub", base_url=f"http://127.0.0.1:{self.port}/v1",
                                    enable_thinking=thinking))

    def test_off_sends_reasoning_effort_none(self):
        self.mk(False).complete([{"role": "user", "content": "hi"}])
        self.assertEqual(REQUESTS[0].get("reasoning_effort"), "none")
        self.assertEqual(REQUESTS[0]["chat_template_kwargs"], {"enable_thinking": False})

    def test_on_does_not_send_reasoning_effort(self):
        self.mk(True).complete([{"role": "user", "content": "hi"}])
        self.assertNotIn("reasoning_effort", REQUESTS[0])
        self.assertEqual(REQUESTS[0]["chat_template_kwargs"], {"enable_thinking": True})


class ReasoningStub(BaseHTTPRequestHandler):
    """模拟 Ollama：思维链走 reasoning 字段，content 一直是空的。"""

    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        for chunk in (
            {"choices": [{"delta": {"reasoning": '{"thought":"看看文件",'}}]},
            {"choices": [{"delta": {"reasoning": '"action":"shell","cmd":"ls"}'},
                          "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 9}},
        ):
            self.wfile.write(b"data: " + json.dumps(chunk, ensure_ascii=False).encode() + b"\n\n")
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True


class TestReasoningFallback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), ReasoningStub)
        port = cls.srv.server_address[1]
        cls.client = ChatClient(ModelSpec(name="stub", base_url=f"http://127.0.0.1:{port}/v1",
                                          enable_thinking=False))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_action_recovered_from_reasoning(self):
        comp = self.client.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(comp.error, "")
        # 正文为空时不能直接判死：动作就在 reasoning 里
        self.assertIn('"action":"shell"', comp.text.replace(" ", ""))
        self.assertIn("reasoning", comp.note)
        self.assertTrue(comp.reasoning, "思维链原文要留档，便于事后诊断")


class TestErrorResponseClosed(unittest.TestCase):
    """被拒的响应必须关掉：HTTPError 自己持有响应体（3.12 起是临时文件包装），
    不关会泄漏句柄，长批次里每次降级重试都漏一个。"""

    def test_downgrade_path_closes_both_rejections(self):
        closed = []

        class Rejected(urllib.error.HTTPError):
            def close(self):
                closed.append(self.code)
                super().close()

        def urlopen(req, timeout=None):
            raise Rejected(req.full_url, 400, "Bad Request", {}, io.BytesIO(b'{"error":"bad"}'))

        client = ChatClient(ModelSpec(name="stub", base_url="http://127.0.0.1:9/v1",
                                      enable_thinking=False))
        with mock.patch("urllib.request.urlopen", urlopen):
            comp = client.complete([{"role": "user", "content": "hi"}])
        self.assertIn("HTTP 400", comp.error)
        self.assertEqual(closed, [400, 400], "降级前后两次 400 都得关掉")


OVERFLOW = {"limit": 32768, "input": 29000, "fmt": "v026"}
MESSAGES = {
    # 课程 TACC 上的 vLLM 0.26.0（vllm/renderers/params.py::_token_len_check）原文
    "v026": "This model's maximum context length is {limit} tokens. However, you requested {m} output tokens "
            "and your prompt contains {inp} input tokens, for a total of {tot} tokens. Please reduce the length "
            "of the input prompt or the number of requested output tokens.",
    # 同一处在 prompt 被分词器截断时的写法：N 只是下限
    "v026_atleast": "This model's maximum context length is {limit} tokens. However, you requested {m} output "
                    "tokens and your prompt contains at least {inp} input tokens, for a total of at least {tot} "
                    "tokens. Please reduce the length of the input prompt or the number of requested output tokens.",
    # vLLM 0.6–0.1x 与更早版本（serving_engine._validate_input）
    "new": "'max_tokens' or 'max_completion_tokens' is too large: {m}. This model's maximum context length is "
           "{limit} tokens and your request has {inp} input tokens ({m} > {limit} - {inp}).",
    "old": "This model's maximum context length is {limit} tokens. However, you requested {tot} tokens "
           "({inp} in the messages, {m} in the completion). Please reduce the length of the messages or completion.",
    "bare": "This model's maximum context length is {limit} tokens.",
}


class OverflowStub(Stub):
    """模拟 vLLM：输入 + max_tokens 超过上下文就 400；chat_template_kwargs 照常接受。"""

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        REQUESTS.append(body)
        m, lim, inp = body["max_tokens"], OVERFLOW["limit"], OVERFLOW["input"]
        if inp + m > lim:
            text = MESSAGES[OVERFLOW["fmt"]].format(m=m, limit=lim, inp=inp, tot=inp + m)
            # vLLM 0.26 的错误体：{"error": {"message", "type", "param", "code"}}
            msg = json.dumps({"error": {"message": text, "type": "BadRequestError", "param": "input_tokens",
                                        "code": 400}}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(msg)
            self.close_connection = True
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        for chunk in ({"choices": [{"delta": {"content": '{"action":"shell","cmd":"ls"}'}, "finish_reason": "stop"}]},
                      {"choices": [], "usage": {"prompt_tokens": inp, "completion_tokens": 9}}):
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True


class TestContextOverflow(unittest.TestCase):
    """输入 + max_tokens 超过上下文的 400 不是"不认 chat_template_kwargs"：
    以前会被误判成后者，永久关掉思考开关，而这一轮照样失败。现在按报错把本轮上限调小重试。"""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), OverflowStub)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        REQUESTS.clear()
        OVERFLOW.update(limit=32768, input=29000, fmt="v026")
        self.client = ChatClient(ModelSpec(name="stub", base_url=f"http://127.0.0.1:{self.port}/v1",
                                           enable_thinking=False, max_tokens=4096))

    def test_fits_max_tokens_from_vllm_messages(self):
        for fmt in ("v026", "new", "old"):
            with self.subTest(fmt=fmt):
                REQUESTS.clear()
                OVERFLOW["fmt"] = fmt
                comp = self.client.complete([{"role": "user", "content": "hi"}])
                self.assertEqual(comp.error, "")
                self.assertEqual([r["max_tokens"] for r in REQUESTS], [4096, 32768 - 29000 - 8])
                self.assertEqual(comp.max_tokens, 3760)
                self.assertIn("3760", comp.note)
                # 思考开关一直在：两次请求都带着 chat_template_kwargs，也没有被记成降级
                self.assertTrue(all("chat_template_kwargs" in r for r in REQUESTS))
                self.assertFalse(self.client._no_template_kwargs)

    def test_next_turn_asks_for_the_full_budget_again(self):
        self.client.complete([{"role": "user", "content": "hi"}])
        OVERFLOW["input"] = 1000
        REQUESTS.clear()
        comp = self.client.complete([{"role": "user", "content": "short again"}])
        self.assertEqual([r["max_tokens"] for r in REQUESTS], [4096])
        self.assertEqual(comp.max_tokens, 4096)

    def test_unreadable_numbers_halve(self):
        for fmt in ("bare", "v026_atleast"):                # "at least N" 只是下限，不能拿来精确算
            with self.subTest(fmt=fmt):
                REQUESTS.clear()
                OVERFLOW["fmt"] = fmt
                comp = self.client.complete([{"role": "user", "content": "hi"}])
                self.assertEqual([r["max_tokens"] for r in REQUESTS], [4096, 2048])
                self.assertEqual(comp.error, "")

    def test_full_context_is_an_error_not_a_downgrade(self):
        OVERFLOW["input"] = 32700                    # 只剩 60 个 token：不值得再要一轮
        comp = self.client.complete([{"role": "user", "content": "hi"}])
        self.assertIn("HTTP 400", comp.error)
        self.assertIn("maximum context length", comp.error)
        self.assertEqual(len(REQUESTS), 1)
        self.assertFalse(self.client._no_template_kwargs)

    def test_retries_are_bounded(self):
        OVERFLOW.update(fmt="bare", input=32000)     # 只剩 768，读不出数字只能减半：4096 → 2048 → 1024，然后放弃
        comp = self.client.complete([{"role": "user", "content": "hi"}])
        self.assertEqual([r["max_tokens"] for r in REQUESTS], [4096, 2048, 1024])
        self.assertIn("HTTP 400", comp.error)


if __name__ == "__main__":
    unittest.main(verbosity=2)


QUOTA = {"left": 0, "kind": "Minute"}


class QuotaStub(Stub):
    """Gemini 式 429：正文很长，quotaId / retryDelay 在 details 里、500 字以后。前 left 次限流，之后正常。"""

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        REQUESTS.append(json.loads(self.rfile.read(n) or b"{}"))
        if QUOTA["left"] > 0:
            QUOTA["left"] -= 1
            msg = json.dumps([{"error": {"code": 429, "message": "You exceeded your current quota. " + "x" * 600,
                                         "status": "RESOURCE_EXHAUSTED",
                                         "details": [{"violations": [{"quotaId": f"GenerateRequestsPer{QUOTA['kind']}PerProjectPerModel-FreeTier"}]},
                                                     {"retryDelay": "43s"}]}}]).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(msg)
            self.close_connection = True
            return
        self.send_response(200)                  # 不能调 super().do_POST()：请求体已经读过了，再读会一直等
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        for chunk in ({"choices": [{"delta": {"content": '{"action":"shell","cmd":"ls"}'}, "finish_reason": "stop"}]},
                      {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 7}}):
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True


class TestQuota(unittest.TestCase):
    """按分钟的配额照服务端说的等、多给几次；按天的配额用完了立刻失败（Lab 实测 40 次里 5 次被旧逻辑记成 error）。"""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), QuotaStub)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        REQUESTS.clear()
        self.waits = []
        p = mock.patch("flagpilot.client.time.sleep", side_effect=self.waits.append)
        p.start()
        self.addCleanup(p.stop)
        self.client = ChatClient(ModelSpec(name="stub", base_url=f"http://127.0.0.1:{self.port}/v1", enable_thinking=False))

    def test_per_minute_quota_waits_as_told_and_retries(self):
        QUOTA.update(left=3, kind="Minute")
        comp = self.client.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(comp.error, "")
        self.assertEqual(self.waits, [43.0, 43.0, 43.0])
        self.assertEqual((len(REQUESTS), comp.retries), (4, 3))

    def test_per_minute_quota_gives_up_after_four_retries(self):
        QUOTA.update(left=9, kind="Minute")
        comp = self.client.complete([{"role": "user", "content": "hi"}])
        self.assertTrue(comp.error.startswith("HTTP 429"))
        self.assertEqual(len(REQUESTS), 5)

    def test_daily_quota_fails_at_once_and_says_why(self):
        QUOTA.update(left=1, kind="Day")
        comp = self.client.complete([{"role": "user", "content": "hi"}])
        self.assertIn("daily free quota", comp.error)
        self.assertEqual((len(REQUESTS), self.waits), (1, []))


GLITCH = {"left": 0, "mode": "malformed"}


class GlitchStub(Stub):
    """Gemini 兼容端点的两种接口毛病：MALFORMED_FUNCTION_CALL（无正文）和一两个字符的乱码；之后正常。"""

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        REQUESTS.append(json.loads(self.rfile.read(n) or b"{}"))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        other = GLITCH["mode"] == "gemini-only" and not str(REQUESTS[-1].get("model", "")).startswith("gemini")
        if GLITCH["left"] > 0 and not other:
            GLITCH["left"] -= 1
            chunk = ({"choices": [{"delta": {}, "finish_reason": "function_call_filter: MALFORMED_FUNCTION_CALL"}]} if GLITCH["mode"] in ("malformed", "gemini-only")
                     else {"choices": [{"delta": {"content": "OK"}, "finish_reason": "stop"}]} if GLITCH["mode"] == "ok"
                     else {"choices": [{"delta": {"content": "岨"}, "finish_reason": "stop"}]})
        else:
            chunk = {"choices": [{"delta": {"content": '{"action":"shell","cmd":"ls"}'}, "finish_reason": "stop"}]}
        self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True


class TestGeminiGlitches(unittest.TestCase):
    """2026-11-20：两次网页运行各有 3 次"协议失败"，其实全是接口返回的畸形函数调用 / 乱码，第三次就把运行结束了。"""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), GlitchStub)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        REQUESTS.clear()
        p = mock.patch("flagpilot.client.time.sleep")
        p.start()
        self.addCleanup(p.stop)
        self.client = ChatClient(ModelSpec(name="gemini-flash-lite-latest", provider="gemini",
                                           base_url=f"http://127.0.0.1:{self.port}/v1", enable_thinking=False))

    def test_malformed_function_call_is_retried(self):
        GLITCH.update(left=2, mode="malformed")
        comp = self.client.complete([{"role": "user", "content": "hi"}])
        self.assertEqual((comp.text, comp.error, len(REQUESTS), comp.retries), ('{"action":"shell","cmd":"ls"}', "", 3, 2))
        self.assertIn("MALFORMED_FUNCTION_CALL", comp.note)

    def test_one_garbage_character_is_retried(self):
        GLITCH.update(left=1, mode="garbage")
        comp = self.client.complete([{"role": "user", "content": "hi"}])
        self.assertEqual((comp.text, len(REQUESTS)), ('{"action":"shell","cmd":"ls"}', 2))

    def test_a_short_real_answer_is_not_retried(self):
        GLITCH.update(left=1, mode="ok")
        comp = self.client.complete([{"role": "user", "content": "hi"}])
        self.assertEqual((comp.text, len(REQUESTS), comp.retries), ("OK", 1, 0))

    def test_gives_up_after_the_retries(self):
        GLITCH.update(left=9, mode="malformed")
        comp = self.client.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(comp.text, "")
        self.assertEqual(len(REQUESTS), 3)
        self.assertTrue(comp.glitch, "the chain can tell this provider kept failing to answer")

    # 2026-11-21 (an imported Chromium challenge): each malformed turn had been retried twice, and all three requests
    # came back MALFORMED_FUNCTION_CALL again: the same request gets the same answer. A retry now asks for plain JSON
    # and samples a little more freely; after that, the chain gives the turn to the next provider.
    def test_a_retry_after_a_malformed_call_asks_for_plain_json(self):
        GLITCH.update(left=1, mode="malformed")
        comp = self.client.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(comp.text, '{"action":"shell","cmd":"ls"}')
        self.assertEqual(len(REQUESTS[0]["messages"]), 1)
        nudge = REQUESTS[1]["messages"][-1]
        self.assertEqual(nudge["role"], "user")
        self.assertIn("plain text", nudge["content"])
        self.assertGreaterEqual(REQUESTS[1]["temperature"], 0.7)
        self.assertFalse(comp.glitch)

    def test_after_the_retries_the_chain_moves_on(self):
        from flagpilot import client as client_mod
        GLITCH.update(left=99, mode="gemini-only")
        url = f"http://127.0.0.1:{self.port}/v1"
        old = client_mod.FALLBACK_RESOLVER
        client_mod.FALLBACK_RESOLVER = lambda name: ModelSpec(name=name, provider="openai-compatible", base_url=url)
        self.addCleanup(setattr, client_mod, "FALLBACK_RESOLVER", old)
        fc = client_mod.build_client(ModelSpec(name="gemini-flash-lite-latest", provider="gemini", base_url=url,
                                               enable_thinking=False, fallback="backup-model"))
        comp = fc.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(comp.text, '{"action":"shell","cmd":"ls"}')
        self.assertEqual([r["model"] for r in REQUESTS], ["gemini-flash-lite-latest"] * 3 + ["backup-model"])
        self.assertEqual((fc.switched or {}).get("to"), "backup-model")


    # 2026-11-21: a server started with HTTPS_PROXY pointing at a local proxy that had stopped answered every Ask message
    # with "URLError: [Errno 61] Connection refused", for every provider. A refused proxy now falls back to a direct call.
    def test_a_proxy_that_refuses_falls_back_to_a_direct_call(self):
        GLITCH.update(left=0, mode="ok")
        env = {"HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9",
               "https_proxy": "http://127.0.0.1:9", "NO_PROXY": "", "no_proxy": ""}
        with mock.patch.dict(os.environ, env):
            comp = self.client.complete([{"role": "user", "content": "hi"}])
        self.assertEqual((comp.text, comp.error), ('{"action":"shell","cmd":"ls"}', ""))


class TestInlineThought(unittest.TestCase):
    """Gemma 4 先写 <thought>…</thought> 再给答案；思考里常有答案的草稿，不能让解析器先看到草稿。"""

    def test_split(self):
        from flagpilot.client import split_inline_thought
        draft = '<thought>Plan: reply `{"action": "shell", "cmd": "rm -rf x"}`? No.</thought>{"action": "shell", "cmd": "ls"}'
        self.assertEqual(split_inline_thought(draft), ('{"action": "shell", "cmd": "ls"}', 'Plan: reply `{"action": "shell", "cmd": "rm -rf x"}`? No.'))
        self.assertEqual(split_inline_thought("<think>a</think>\nanswer"), ("answer", "a"))
        self.assertEqual(split_inline_thought("<thought>cut off mid"), ("", "cut off mid"))
        self.assertEqual(split_inline_thought('{"action": "shell"} <thought>later</thought>'), ('{"action": "shell"} <thought>later</thought>', ""))
        self.assertEqual(split_inline_thought(""), ("", ""))
