"""Track H（DeepSeek V4.1）的线上行为，用假服务端测，不花钱、不需要密钥。

要钉住的三件事：
  1. 关思维链用 DeepSeek 认的 thinking.type，而不是 vLLM 的 chat_template_kwargs /
     reasoning_effort=none——后者在 DeepSeek 上关不掉思维链（文档只列 low/high/max）。
  2. usage 里的缓存命中/未命中被解析出来（两种格式都认），成本才能按三档计价。
  3. DeepSeek 返回 400 时不走"去掉 chat_template_kwargs 再试"那条降级——
     我们根本没发那个字段，白重试一次只会多一次失败请求。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flagpilot import cli  # noqa: E402
from flagpilot.client import ChatClient  # noqa: E402
from flagpilot.config import TRACK_H, make_model_spec, track_h_api_key  # noqa: E402

REQUESTS: list[dict] = []
MODE = {"usage": "deepseek", "status": 200}


class DeepSeekStub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        REQUESTS.append(json.loads(self.rfile.read(n) or b"{}"))
        if MODE["status"] != 200:
            msg = json.dumps({"error": {"message": "bad request"}}).encode()
            self.send_response(MODE["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(msg)
            self.close_connection = True
            return
        if MODE["usage"] == "deepseek":
            usage = {"prompt_tokens": 100, "completion_tokens": 10,
                     "prompt_cache_hit_tokens": 64, "prompt_cache_miss_tokens": 36,
                     "completion_tokens_details": {"reasoning_tokens": 0}}
        elif MODE["usage"] == "openai":
            usage = {"prompt_tokens": 100, "completion_tokens": 10,
                     "prompt_tokens_details": {"cached_tokens": 80}}
        else:
            usage = {"prompt_tokens": 100, "completion_tokens": 10}
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        for chunk in (
            {"choices": [{"delta": {"content": '{"action":"shell","cmd":"ls"}'},
                          "finish_reason": "stop"}]},
            {"choices": [], "usage": usage},
        ):
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True


class _StubServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), DeepSeekStub)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        REQUESTS.clear()
        MODE.update(usage="deepseek", status=200)

    def deepseek_client(self, thinking=False):
        spec = make_model_spec("deepseek-flash", f"http://127.0.0.1:{self.port}",
                               thinking=thinking, provider="deepseek")
        return ChatClient(spec)


class TestThinkingSwitch(_StubServer):
    def test_disabled_uses_deepseek_switch_only(self):
        self.deepseek_client().complete([{"role": "user", "content": "hi"}])
        body = REQUESTS[0]
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertNotIn("chat_template_kwargs", body)
        self.assertNotIn("reasoning_effort", body)

    def test_enabled_when_asked(self):
        self.deepseek_client(thinking=True).complete([{"role": "user", "content": "hi"}])
        self.assertEqual(REQUESTS[0]["thinking"], {"type": "enabled"})

    def test_vllm_path_untouched(self):
        """非 DeepSeek 端点保持原来的 vLLM 写法，不能被这次改动带偏。"""
        spec = make_model_spec("local-model", f"http://127.0.0.1:{self.port}/v1")
        self.assertEqual(spec.provider, "openai")
        ChatClient(spec).complete([{"role": "user", "content": "hi"}])
        body = REQUESTS[0]
        self.assertNotIn("thinking", body)
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})

    def test_400_does_not_trigger_template_downgrade(self):
        MODE["status"] = 400
        comp = self.deepseek_client().complete([{"role": "user", "content": "hi"}])
        self.assertTrue(comp.error.startswith("HTTP 400"))
        self.assertEqual(len(REQUESTS), 1, "DeepSeek 400 不应白重试一次")


class TestUsageParsing(_StubServer):
    def test_deepseek_cache_fields(self):
        comp = self.deepseek_client().complete([{"role": "user", "content": "hi"}])
        self.assertEqual((comp.cache_hit_tokens, comp.cache_miss_tokens), (64, 36))
        self.assertEqual(comp.reasoning_tokens, 0)

    def test_openai_cached_tokens(self):
        MODE["usage"] = "openai"
        comp = self.deepseek_client().complete([{"role": "user", "content": "hi"}])
        self.assertEqual((comp.cache_hit_tokens, comp.cache_miss_tokens), (80, 20))

    def test_absent_cache_stays_none(self):
        MODE["usage"] = "none"
        comp = self.deepseek_client().complete([{"role": "user", "content": "hi"}])
        self.assertIsNone(comp.cache_hit_tokens)
        self.assertIsNone(comp.cache_miss_tokens)


class TestPreset(unittest.TestCase):
    def test_official_endpoint_gets_provider_and_table(self):
        spec = make_model_spec("deepseek-flash", "https://api.deepseek.com")
        self.assertEqual(spec.provider, "deepseek")
        self.assertEqual(spec.price_table, "deepseek-flash")
        self.assertFalse(spec.soft_no_think)

    def test_explicit_provider_gets_table_off_official_host(self):
        """代理 / 网关地址不含 api.deepseek.com，显式 provider 也要拿到价目表。"""
        spec = make_model_spec("deepseek-flash", "http://127.0.0.1:9/gw", provider="deepseek")
        self.assertEqual(spec.provider, "deepseek")
        self.assertEqual(spec.price_table, "deepseek-flash")

    def test_explicit_flat_prices_win_over_table(self):
        spec = make_model_spec("deepseek-flash", "https://api.deepseek.com",
                               price_in_per_mtok=1.0, price_out_per_mtok=2.0)
        self.assertEqual(spec.price_table, "")

    def test_key_lookup_order_and_ignores_empty(self):
        self.assertEqual(track_h_api_key({"DSV41_API_KEY": "a", "DEEPSEEK_API_KEY": "b"}), "a")
        self.assertEqual(track_h_api_key({"DSV41_API_KEY": " ", "DEEPSEEK_API_KEY": "b"}), "b")
        self.assertEqual(track_h_api_key({"FLAGPILOT_API_KEY": "EMPTY"}), "")

    def _cfg(self, argv, env):
        args = cli.build_parser().parse_args(argv)
        with mock.patch.dict(os.environ, env, clear=True):
            return cli._build_cfg(args)

    def test_track_h_one_command_defaults(self):
        """--track H 一个参数就够：模型、端点、密钥、价目表都自动补上。"""
        cfg = self._cfg(["batch", "--track", "H"], {"DSV41_API_KEY": "sk-test",
                        "FLAGPILOT_BASE_URL": "http://vllm-node:8000/v1"})
        self.assertEqual(cfg.model.name, TRACK_H["model"])
        self.assertEqual(cfg.model.base_url, TRACK_H["base_url"])
        self.assertEqual(cfg.model.api_key, "sk-test")
        self.assertEqual(cfg.model.provider, "deepseek")
        self.assertEqual(cfg.model.price_table, "deepseek-flash")

    def test_track_h_endpoint_override_keeps_deepseek_handling(self):
        """FLAGPILOT_H_BASE_URL 改到代理时，thinking 开关和计价不能悄悄退回 vLLM 写法。"""
        cfg = self._cfg(["batch", "--track", "H"], {"DSV41_API_KEY": "sk-test",
                        "FLAGPILOT_H_BASE_URL": "http://127.0.0.1:9/gw"})
        self.assertEqual(cfg.model.base_url, "http://127.0.0.1:9/gw")
        self.assertEqual(cfg.model.provider, "deepseek")
        self.assertEqual(cfg.model.price_table, "deepseek-flash")

    def test_track_l_on_custom_host_stays_openai(self):
        cfg = self._cfg(["batch", "--model", "local-model",
                         "--base-url", "http://127.0.0.1:9/v1"], {})
        self.assertEqual(cfg.model.provider, "openai")
        self.assertEqual(cfg.model.price_table, "")

    def test_track_l_default_is_still_mock(self):
        cfg = self._cfg(["batch"], {})
        self.assertEqual(cfg.model.name, "mock")
        self.assertEqual(cfg.track, "L")

    def test_track_h_without_key_fails_loudly(self):
        with self.assertRaises(SystemExit):
            self._cfg(["batch", "--track", "H"], {})

    def test_track_h_mock_needs_no_key(self):
        """单测 / 离线演示：--track H --model mock 不该要求真密钥。"""
        cfg = self._cfg(["batch", "--track", "H", "--model", "mock"], {})
        self.assertEqual(cfg.model.name, "mock")


if __name__ == "__main__":
    unittest.main()
