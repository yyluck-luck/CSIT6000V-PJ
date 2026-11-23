"""API configuration checks using local request stubs."""

import io
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from app import server
from flagpilot import cli, config
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



class TestExtraEndpoints(unittest.TestCase):
    """.env 里成对的 NAME_BASE_URL + NAME_API_KEY：模型表决定走哪个端点，密钥只发给自己的端点，只发标准字段。"""

    def test_parsing(self):
        import tempfile
        from flagpilot.config import extra_endpoints
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / ".env"
            f.write_text("RAYGO_BASE_URL=https://api.example.com/v1/\nRAYGO_API_KEY=sk-1\nRAYGO_MODELS=a, b\n"
                         "PLAIN_BASE_URL=http://example.com/v1\nPLAIN_API_KEY=k\nNOKEY_BASE_URL=https://x/v1\n"
                         "GEMINI_BASE_URL=https://g/v1\nGEMINI_API_KEY=g\n# OLD_BASE_URL=https://y\n", encoding="utf-8")
            got = extra_endpoints({}, dotenv=f)
        self.assertEqual(list(got), ["raygo"])
        self.assertEqual(got["raygo"], {"base_url": "https://api.example.com/v1", "api_key": "sk-1", "models": ["a", "b"]})
        self.assertEqual(extra_endpoints({"FLAGPILOT_NO_DOTENV": "1"}), {})

    def test_routing_through_a_relay(self):
        import json as _json
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import app.server as S
        seen = []

        class Relay(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = _json.dumps({"data": [{"id": "relay-chat-1"}, {"id": "relay-image-2"}]}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                seen.append((self.headers.get("Authorization"), _json.loads(self.rfile.read(n))))
                self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
                self.wfile.write(b'data: {"choices": [{"delta": {"content": "{\\"action\\": \\"shell\\", \\"cmd\\": \\"ls\\"}"}, "finish_reason": "stop"}]}\n\ndata: [DONE]\n\n')

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Relay)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}/v1"
        try:
            with patch.dict(os.environ, {"FAKEREL_BASE_URL": base, "FAKEREL_API_KEY": "k-relay"}):
                S._load_routes(force=True)
                self.assertEqual(S._ROUTES.get("relay-chat-1"), "fakerel")
                self.assertNotIn("relay-image-2", S._ROUTES, "image models are not chat models")
                self.assertEqual(S._base_for("relay-chat-1"), base)
                self.assertEqual(S._api_key(base), "k-relay")
                self.assertNotEqual(S._api_key("http://127.0.0.1:9/v1"), "k-relay", "the relay's key is not sent anywhere else")
                spec = S._spec("relay-chat-1", base)
                self.assertEqual(spec.provider, "openai-compatible")
                from flagpilot.client import ChatClient
                comp = ChatClient(spec).complete([{"role": "user", "content": "hi"}])
                self.assertEqual(comp.text, '{"action": "shell", "cmd": "ls"}')
                auth, payload = seen[-1]
                self.assertEqual((auth, payload["model"]), ("Bearer k-relay", "relay-chat-1"))
                self.assertNotIn("chat_template_kwargs", payload, "a gateway gets standard fields only")
        finally:
            srv.shutdown(); srv.server_close()
            S._load_routes(force=True)


class TestProviders(unittest.TestCase):
    """菜单只列提供商；型号由服务端挑：便宜的在前，换不了就下一个，最后是备援。Lab 只取一个、不换。"""

    MODELS = ["gemini-pro-latest", "gemini-flash-latest", "gemini-3.1-flash-lite", "gemini-flash-lite-latest", "gemini-3.5-flash-lite",
              "gemini-3.1-flash-lite-preview", "gemma-4-31b-it", "deepseek-v4-pro", "deepseek-v4.1-flash", "glm-5.3", "glm-5.3-flash", "mock"]

    def setUp(self):
        import app.server as S
        from flagpilot import client as C
        self.S, self.C = S, C
        p = patch.object(S._MODELS, "get", side_effect=lambda key, factory: {"models": list(self.MODELS)})
        p.start()
        self.addCleanup(p.stop)
        q = patch.dict(os.environ, {"FLAGPILOT_FALLBACK_MODEL": "deepseek-v4.1-flash"})
        q.start()
        self.addCleanup(q.stop)
        self.addCleanup(C.DOWN.clear)

    def test_cheapest_first_and_whole_words(self):
        S = self.S
        self.assertEqual(S.provider_chain("gemini"), ["gemini-flash-lite-latest", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "deepseek-v4.1-flash"])
        self.assertEqual(S.provider_chain("glm"), ["glm-5.3-flash", "glm-5.3", "deepseek-v4.1-flash"])
        self.assertEqual(S.provider_chain("deepseek"), ["deepseek-v4.1-flash", "deepseek-v4-pro"])
        self.assertEqual([p["label"] for p in S.provider_list()], ["Gemini", "DeepSeek", "GLM"])

    def test_resolve_for_runs_and_for_experiments(self):
        S, C = self.S, self.C
        first, rest = S._resolve("provider:gemini", chain=True)
        self.assertEqual((first, rest), ("gemini-flash-lite-latest", "gemini-3.5-flash-lite,gemini-3.1-flash-lite,deepseek-v4.1-flash"))
        self.assertEqual(S._resolve("provider:gemini", chain=False), ("gemini-flash-lite-latest", ""))
        C.DOWN["gemini-flash-lite-latest"] = (__import__("time").time() + 60, "its daily free quota is used up")
        self.assertEqual(S._resolve("provider:gemini", chain=False)[0], "gemini-3.5-flash-lite", "a model that just failed is skipped")
        self.assertEqual(S._resolve("deepseek-v4.1-flash", chain=True), ("deepseek-v4.1-flash", ""), "an exact model keeps itself, no self-fallback")


class TestModelChain(unittest.TestCase):
    """FallbackClient：当前型号在接口层面失败就换下一个、这一轮当场重发；记住失败；最后一个才用自己的重试。"""

    def test_walks_the_chain_and_remembers(self):
        from flagpilot import client as C
        from flagpilot.client import Completion, FallbackClient
        from flagpilot.config import ModelSpec
        calls, made = [], {}

        class Fake:
            def __init__(self, name, err=""):
                self.name, self.err, self.busy_retries = name, err, None

            def scrape_metrics(self):
                return None

            def complete(self, messages, max_tokens=None, on_delta=None):
                calls.append((self.name, self.busy_retries))
                return Completion(text="" if self.err else f"from {self.name}", error=self.err)

        errs = {"a": "HTTP 429: the daily free quota for this model is used up; try again tomorrow or use another model", "b": "HTTP 503: overloaded", "c": ""}
        resolver_before, build_before = C.FALLBACK_RESOLVER, C.build_client
        C.FALLBACK_RESOLVER = lambda name: ModelSpec(name=name)
        C.build_client = lambda spec, chain=True: made.setdefault(spec.name, Fake(spec.name, errs[spec.name]))
        try:
            fc = FallbackClient(Fake("a", errs["a"]), ModelSpec(name="a", fallback="b,c"))
            comp = fc.complete([{"role": "user", "content": "hi"}])
            self.assertEqual((comp.text, fc.last_model), ("from c", "c"))
            self.assertEqual([n for n, _ in calls], ["a", "b", "c"])
            self.assertEqual([r for _, r in calls], [0, 0, None], "fail over fast; only the last model keeps its own retries")
            self.assertEqual(fc.switched["to"], "c")
            self.assertIn("a", C.DOWN)
            calls.clear()
            fc2 = FallbackClient(Fake("a", errs["a"]), ModelSpec(name="a", fallback="b,c"))
            fc2.complete([{"role": "user", "content": "again"}])
            self.assertEqual([n for n, _ in calls], ["c"], "models that just failed are skipped by the next run")
            calls.clear()
            fc.complete([{"role": "user", "content": "next turn"}])
            self.assertEqual([n for n, _ in calls], ["c"], "a run stays on the model it switched to")
            ok = FallbackClient(Fake("x"), ModelSpec(name="x", fallback="c"))
            self.assertEqual(ok.complete([]).error, "")
            bad = FallbackClient(Fake("y", "HTTP 400: bad request"), ModelSpec(name="y", fallback="c"))
            self.assertTrue(bad.complete([]).error.startswith("HTTP 400"), "a plain request error does not switch models")
        finally:
            C.FALLBACK_RESOLVER, C.build_client = resolver_before, build_before
            C.DOWN.clear()


class TestLocalCredentials(unittest.TestCase):

    def setUp(self):
        import tempfile
        from unittest import mock
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-env-"))
        self.files = mock.patch.object(config, "PROJECT_ROOT", self.tmp / "flagpilot")
        self.files.start()
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "DSV41_API_KEY", "DEEPSEEK_API_KEY",
                  "FLAGPILOT_NO_DOTENV", "RAYGO_BASE_URL", "RAYGO_API_KEY"):
            os.environ.pop(k, None)

    def tearDown(self):
        self.env.stop()
        self.files.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_legacy_shared_file_is_ignored(self):
        (self.tmp / "shared.env").write_text("GEMINI_API_KEY=old-gemini-fixture\n"
                                             "RAYGO_BASE_URL=https://relay.example/v1\nRAYGO_API_KEY=old-relay-fixture\n")
        self.assertEqual(config.gemini_api_key(), "")
        self.assertNotIn("raygo", config.extra_endpoints())

    def test_environment_takes_precedence_over_personal_env(self):
        (self.tmp / ".env").write_text("GEMINI_API_KEY=local-gemini-fixture\nDSV41_API_KEY=local-deepseek-fixture\n")
        self.assertEqual(config.gemini_api_key(), "local-gemini-fixture")
        self.assertEqual(config.track_h_api_key(), "local-deepseek-fixture")
        os.environ["GEMINI_API_KEY"] = "environment-gemini-fixture"
        self.assertEqual(config.gemini_api_key(), "environment-gemini-fixture")

    def test_dotenv_can_be_disabled(self):
        (self.tmp / ".env").write_text("GEMINI_API_KEY=local-gemini-fixture\n")
        os.environ["FLAGPILOT_NO_DOTENV"] = "1"
        self.assertEqual(config.gemini_api_key(), "")
        self.assertEqual(config.dotenv_value("GEMINI_API_KEY"), "")
        self.assertEqual(config.extra_endpoints(), {})
        self.assertEqual(config.track_h_api_key(), "")
