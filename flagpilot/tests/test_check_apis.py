"""scripts/check_apis.py: one tiny request per provider; report who answers, why not, and never a key."""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_apis  # noqa: E402
from flagpilot.client import Completion  # noqa: E402

SECRET = "-".join(["sk", "test", "secret", "value", "0000000000"])   # built at run time: no key shape in the source


class Fake:
    def __init__(self, error=""):
        self.error = error

    def complete(self, messages, max_tokens=None, on_delta=None):
        return Completion(text="" if self.error else "OK", prompt_tokens=1, completion_tokens=1, ttft_ms=1,
                          latency_ms=1, error=self.error)


def run(providers, chains, answers):
    """answers: model name -> error string ('' = answers)."""
    s = check_apis.server
    with mock.patch.object(s, "provider_list", return_value=providers), \
         mock.patch.object(s, "provider_chain", side_effect=lambda pid: chains[pid]), \
         mock.patch.object(s, "_base_for", return_value="https://example.test/v1"), \
         mock.patch.object(s, "_spec", side_effect=lambda name, base: name), \
         mock.patch.object(s, "_fallback_model", return_value="deepseek-v4.1-flash"), \
         mock.patch.object(check_apis.llm_client, "build_client", side_effect=lambda name, chain=False: Fake(answers[name])):
        out = io.StringIO()
        with redirect_stdout(out):
            code = check_apis.main()
    return code, out.getvalue()


class TestCheckApis(unittest.TestCase):
    def test_every_provider_answers(self):
        code, out = run([{"id": "provider:gemini", "label": "Gemini"}, {"id": "provider:glm", "label": "GLM"}],
                        {"gemini": ["gemini-flash-lite-latest", "deepseek-v4.1-flash"], "glm": ["glm-5.3-flash"]},
                        {"gemini-flash-lite-latest": "", "glm-5.3-flash": ""})
        self.assertEqual(code, 0)
        self.assertIn("OK    Gemini    answered by gemini-flash-lite-latest", out)
        self.assertIn("OK    GLM       answered by glm-5.3-flash", out)
        self.assertIn("fall back to: deepseek-v4.1-flash", out)

    def test_cheapest_fails_next_answers_and_a_dead_provider_fails_the_check(self):
        code, out = run([{"id": "provider:gemini", "label": "Gemini"}, {"id": "provider:gpt", "label": "GPT"}],
                        {"gemini": ["gemini-a", "gemini-b"], "gpt": ["gpt-x"]},
                        {"gemini-a": "HTTP 429: daily quota (quotaId GenerateRequestsPerDay)", "gemini-b": "",
                         "gpt-x": f"HTTP 401: invalid key {SECRET}"})
        self.assertEqual(code, 1)
        self.assertIn("OK    Gemini    answered by gemini-b", out)
        self.assertIn("FAIL  GPT", out)
        self.assertNotIn(SECRET, out, "a key echoed back in an error message is never printed")

    def test_no_keys_at_all(self):
        code, out = run([], {}, {})
        self.assertEqual(code, 1)
        self.assertIn("only the offline demo model and replays work", out)


if __name__ == "__main__":
    unittest.main()
