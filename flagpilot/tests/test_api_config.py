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
