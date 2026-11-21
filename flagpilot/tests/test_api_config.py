"""API configuration checks using local request stubs."""

import io
import json
import os
import unittest
from unittest.mock import patch

from app import server
from flagpilot import cli
from flagpilot.client import ChatClient
from flagpilot.config import make_model_spec


class TestAPIConfiguration(unittest.TestCase):
    def test_cli_uses_configured_model(self):
        with patch.dict(os.environ, {
            "FLAGPILOT_MODEL": "flint-40b-free",
            "FLAGPILOT_BASE_URL": "https://opencode.ai/inference/openai/v1",
            "FLAGPILOT_API_KEY": "local-test-key",
        }):
            args = cli.build_parser().parse_args(["run", "--task", "crypto-005"])
            self.assertEqual(cli._resolve_endpoint(args), (
                "flint-40b-free", "https://opencode.ai/inference/openai/v1", "local-test-key"))
            explicit = cli.build_parser().parse_args(["run", "--model", "mock"])
            self.assertEqual(cli._resolve_endpoint(explicit)[0], "mock")

    def test_web_uses_server_side_key_and_keeps_it_out_of_fingerprint(self):
        with patch.dict(os.environ, {"FLAGPILOT_API_KEY": "local-test-key"}), patch.dict(
            server.SETTINGS, {"model": "flint-40b-free", "base_url":
            "https://opencode.ai/inference/openai/v1", "track": "L", "ablation": "B3"}, clear=True):
            cfg = server._make_cfg("api-check")
            self.assertEqual(cfg.model.api_key, "local-test-key")
            self.assertNotIn("local-test-key", json.dumps(cfg.fingerprint()))

    def test_opencode_uses_standard_chat_fields(self):
        spec = make_model_spec("flint-40b-free", "https://opencode.ai/inference/openai/v1",
                               api_key="local-test-key")
        payload = {"choices": [{"delta": {"content": "OK"}, "finish_reason": "stop"}]}
        body = ("data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n").encode()
        with patch("urllib.request.urlopen", return_value=io.BytesIO(body)) as request:
            result = ChatClient(spec).complete([{"role": "user", "content": "Hi"}])
        req = request.call_args.args[0]
        sent = json.loads(req.data)
        self.assertEqual(result.text, "OK")
        self.assertEqual(req.get_header("Authorization"), "Bearer local-test-key")
        self.assertEqual(req.get_header("User-agent"), "FlagPilot/0.1.0")
        self.assertNotIn("chat_template_kwargs", sent)
        self.assertNotIn("reasoning_effort", sent)
        self.assertNotIn("thinking", sent)

    def test_model_discovery_authenticates(self):
        with patch.dict(os.environ, {"FLAGPILOT_API_KEY": "local-test-key"}), patch.dict(
            server.SETTINGS, {"model": "flint-40b-free", "base_url":
            "https://opencode.ai/inference/openai/v1"}, clear=True), patch(
            "urllib.request.urlopen", return_value=io.BytesIO(b'{"data":[{"id":"flint-40b-free"}]}')) as request:
            self.assertEqual(server.list_models(), ["flint-40b-free"])
            self.assertEqual(request.call_args.args[0].get_header("Authorization"),
                             "Bearer local-test-key")


class TestGemini(unittest.TestCase):
    """Gemini API（AI Studio 的 OpenAI 兼容端点）：密钥从 .env 来、只发标准字段、限流 / 过载时退避重试。"""

    def test_spec_endpoint_and_key(self):
        import tempfile
        from pathlib import Path
        from flagpilot.config import GEMINI, gemini_api_key
        with patch.dict(os.environ, {"GEMINI_API_KEY": "env-key"}):
            spec = make_model_spec("gemini-flash-lite-latest")
        self.assertEqual((spec.provider, spec.base_url, spec.api_key), ("gemini", GEMINI["base_url"], "env-key"))
        with tempfile.TemporaryDirectory() as d:
            dot = Path(d) / ".env"
            dot.write_text("DSV41_API_KEY=x\nGEMINI_API_KEY=file-key\n", encoding="utf-8")
            self.assertEqual(gemini_api_key(environ={}, dotenv=dot), "file-key")
            self.assertEqual(gemini_api_key(environ={"GOOGLE_API_KEY": "g"}, dotenv=dot), "g")   # 环境变量优先
        self.assertEqual(make_model_spec("qwen3.8-27b").provider, "openai")                    # 别的模型不受影响

    def _client(self, bodies):
        """按顺序返回 (status, body)；记录每次请求体。"""
        from flagpilot.config import ModelSpec
        sent = []

        def urlopen(req, timeout=None):
            sent.append(json.loads(req.data))
            status, body = bodies[min(len(sent) - 1, len(bodies) - 1)]
            if status != 200:
                import urllib.error
                raise urllib.error.HTTPError(req.full_url, status, "x", {}, io.BytesIO(body.encode()))
            return io.BytesIO(body.encode())
        spec = ModelSpec(name="gemini-x", base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                         api_key="k", provider="gemini")
        return ChatClient(spec), sent, urlopen

    def test_standard_fields_only_and_retry_on_overload(self):
        ok = "data: " + json.dumps({"choices": [{"delta": {"content": "OK"}, "finish_reason": "stop"}]}) + "\n\ndata: [DONE]\n\n"
        busy = '[{"error": {"code": 503, "message": "This model is currently experiencing high demand."}}]'
        client, sent, urlopen = self._client([(503, busy), (200, ok)])
        with patch("urllib.request.urlopen", urlopen), patch("time.sleep") as slept:
            comp = client.complete([{"role": "user", "content": "hi"}])
        self.assertEqual((comp.error, comp.text, len(sent), comp.retries), ("", "OK", 2, 1))
        self.assertTrue(slept.called)
        for body in sent:
            self.assertNotIn("chat_template_kwargs", body)
            self.assertNotIn("reasoning_effort", body)          # flash-lite 会 400

    def test_retries_are_bounded(self):
        busy = '{"error": {"code": 429, "details": [{"retryDelay": "3s"}]}}'
        client, sent, urlopen = self._client([(429, busy)])
        with patch("urllib.request.urlopen", urlopen), patch("time.sleep") as slept:
            comp = client.complete([{"role": "user", "content": "hi"}])
        self.assertIn("HTTP 429", comp.error)
        self.assertEqual(len(sent), 3)                          # 1 次 + 重试 2 次
        self.assertEqual([c.args[0] for c in slept.call_args_list], [3.0, 3.0])   # 按 retryDelay 等

    def test_wait_parsing(self):
        from flagpilot.client import busy_wait_s
        self.assertEqual(busy_wait_s("", "7", 0), 7.0)
        self.assertEqual(busy_wait_s('"retryDelay": "12s"', None, 0), 12.0)
        self.assertEqual(busy_wait_s("", None, 0), 2.0)
        self.assertEqual(busy_wait_s('"retryDelay": "90s"', None, 1), 20.0)   # 有上限
        self.assertEqual(busy_wait_s("Please retry in 43.6s.", None, 0, cap=65.0), 43.6)

    def test_quota_kind_reads_google_details(self):
        """429 是按分钟还是按天：按分钟就照服务端说的等，按天就直接失败（等也没用）。"""
        from flagpilot.client import quota_kind
        minute = '{"error": {"code": 429, "details": [{"violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]}, {"retryDelay": "43s"}]}}'
        day = '{"error": {"code": 429, "details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}'
        self.assertEqual(quota_kind(minute), "minute")
        self.assertEqual(quota_kind(day), "day")
        self.assertEqual(quota_kind("Please retry in 12s."), "minute")
        self.assertEqual(quota_kind("Too many requests"), "")

    def test_model_list_strips_prefix_and_drops_non_chat_models(self):
        listing = {"data": [{"id": "models/gemini-3.8-flash"}, {"id": "models/gemini-2.5-flash-preview-tts"},
                            {"id": "models/gemini-3-pro-image"}, {"id": "models/gemini-flash-lite-latest"},
                            {"id": "models/antigravity-preview-latest"}, {"id": "models/deep-research-preview-04-2026"},
                            {"id": "models/gemini-2.5-flash-native-audio-latest"}, {"id": "models/gemini-2.5-computer-use-preview-10-2025"}]}
        with patch.dict(os.environ, {"GEMINI_API_KEY": "gkey"}, clear=False), patch.dict(
                server.SETTINGS, {"model": "gemini-flash-lite-latest", "base_url": ""}), patch(
                "urllib.request.urlopen", return_value=io.BytesIO(json.dumps(listing).encode())) as request:
            os.environ.pop("FLAGPILOT_API_KEY", None)
            got = server.list_models()
        self.assertEqual(got, ["gemini-3.8-flash", "gemini-flash-lite-latest"])
        self.assertEqual(request.call_args.args[0].get_header("Authorization"), "Bearer gkey")

    def test_empty_stream_is_retried_like_overload(self):
        """Gemini 过载时回 200 却在出字前结束流：重试，而不是记成"空回复 → 无效动作"。"""
        empty = "data: " + json.dumps({"choices": [{"delta": {"extra_content": {"google": {"thought_signature": "x"}}}}]}) + "\n\ndata: [DONE]\n\n"
        ok = "data: " + json.dumps({"choices": [{"delta": {"content": '{"action":"shell","cmd":"ls"}'}}]}) + "\n\ndata: [DONE]\n\n"
        client, sent, urlopen = self._client([(200, empty), (200, ok)])
        with patch("urllib.request.urlopen", urlopen), patch("time.sleep"):
            comp = client.complete([{"role": "user", "content": "hi"}])
        self.assertEqual((comp.error, len(sent)), ("", 2))
        self.assertIn('"action":"shell"', comp.text)
        client, sent, urlopen = self._client([(200, 'data: {"error": {"code": 503, "message": "overloaded"}}\n\n')])
        with patch("urllib.request.urlopen", urlopen), patch("time.sleep"):
            comp = client.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(len(sent), 3)                                      # 流里的 503 也按忙处理，重试有上限
        self.assertIn("stream error 503", comp.error)
