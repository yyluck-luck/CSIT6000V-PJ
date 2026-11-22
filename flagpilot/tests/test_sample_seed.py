"""The sampling seed (2026-11-22): off by default, as in the evaluated batches (README limitation 12); when given, every
request of attempt s carries seed + s, the trace records it, and a server that rejects the field stops getting it."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_live_stream import FLAG, SCRIPT, make_task  # noqa: E402

from flagpilot import agent, cli  # noqa: E402
from flagpilot.client import ChatClient  # noqa: E402
from flagpilot.config import Budget, ModelSpec, RunConfig, SandboxLimits  # noqa: E402

SEEN: list[dict] = []
REJECT_SEED = {"on": False}


class Stub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        SEEN.append(body)
        if REJECT_SEED["on"] and "seed" in body:
            msg = json.dumps({"error": {"message": "Unknown name \"seed\": Cannot find field."}}).encode()
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
                      {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}):
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True


class TestClientSendsTheSeed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        SEEN.clear()
        REJECT_SEED["on"] = False

    def client(self, seed):
        # openai-compatible: no chat_template_kwargs, so the only field that can be refused here is seed
        return ChatClient(ModelSpec(name="stub", base_url=f"http://127.0.0.1:{self.srv.server_address[1]}/v1",
                                    provider="openai-compatible", seed=seed))

    def test_no_seed_unless_one_is_given(self):
        self.client(None).complete([{"role": "user", "content": "hi"}])
        self.assertNotIn("seed", SEEN[0])
        self.client(41).complete([{"role": "user", "content": "hi"}])
        self.assertEqual(SEEN[1]["seed"], 41)

    def test_a_server_that_rejects_the_seed_stops_getting_it(self):
        REJECT_SEED["on"] = True
        c = self.client(7)
        comp = c.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(comp.error, "")
        self.assertIn("seed", comp.note)
        self.assertEqual([("seed" in b) for b in SEEN], [True, False])
        c.complete([{"role": "user", "content": "again"}])
        self.assertNotIn("seed", SEEN[2], "once refused, never sent again by this client")


class TestAttemptSeed(unittest.TestCase):
    def test_attempt_s_samples_with_seed_plus_s_and_the_trace_says_so(self):
        tmp = Path(tempfile.mkdtemp(prefix="fp-seed-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        seen = []

        class Client:
            def __init__(self, spec):
                seen.append(spec.seed)

            def complete(self, messages, **kw):
                from flagpilot.client import Completion
                return Completion(text=json.dumps({"thought": "t", "action": "submit", "flag": FLAG, "script": SCRIPT}),
                                  prompt_tokens=5, completion_tokens=5, latency_ms=1, ttft_ms=1, finish_reason="stop")

            def scrape_metrics(self):
                from flagpilot.client import MockClient
                return MockClient().scrape_metrics()

        original = agent.build_client
        agent.build_client = lambda spec: Client(spec)
        try:
            cfg = RunConfig(model=ModelSpec(name="stub", seed=100), budget=Budget(max_turns=3, max_wall_s=120),
                            limits=SandboxLimits(action_timeout_s=10), replay_timeout_s=20.0, tasks_root=tmp)
            agent.run_attempt(make_task(tmp), cfg, tmp, seed=1)
            plain = RunConfig(model=ModelSpec(name="stub"), budget=Budget(max_turns=3, max_wall_s=120),
                              limits=SandboxLimits(action_timeout_s=10), replay_timeout_s=20.0, tasks_root=tmp)
            agent.run_attempt(make_task(tmp), plain, tmp / "plain", seed=1)
        finally:
            agent.build_client = original
        self.assertEqual(seen, [101, None])
        meta = json.loads((tmp / "live-001-s1" / "trace.jsonl").read_text().splitlines()[0])
        self.assertEqual((meta["sample_seed"], meta["model"]["seed"]), (101, 100))
        meta = json.loads((tmp / "plain" / "live-001-s1" / "trace.jsonl").read_text().splitlines()[0])
        self.assertNotIn("sample_seed", meta)
        self.assertNotIn("seed", meta["model"])


class TestCommandLine(unittest.TestCase):
    def test_sample_seed_reaches_the_model_spec(self):
        args = cli.build_parser().parse_args(["run", "--task", "demo-001", "--sample-seed", "9"])
        self.assertEqual(cli._build_cfg(args).model.seed, 9)
        args = cli.build_parser().parse_args(["run", "--task", "demo-001"])
        self.assertIsNone(cli._build_cfg(args).model.seed)

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_submit_sh_passes_fp_sample_seed_and_rejects_junk(self):
        text = (ROOT / "deploy" / "tcloud" / "submit.sh").read_text(encoding="utf-8")
        start = text.index('    if [[ -n "${FP_SAMPLE_SEED:-}" ]]; then')
        block = text[start:text.index("    fi\n", start) + 7]
        for value, rc, args in (("1234", 0, " --sample-seed 1234"), ("", 0, ""), ("12; rm -rf /", 1, "")):
            r = subprocess.run(["bash", "-c", 'ARGS=""\n' + block + 'echo "ARGS=$ARGS"'], capture_output=True, text=True,
                               env={**os.environ, "FP_SAMPLE_SEED": value})
            self.assertEqual(r.returncode, rc, (value, r.stderr))
            if rc == 0:
                self.assertEqual(r.stdout.strip(), "ARGS=" + args)


if __name__ == "__main__":
    unittest.main()
