"""The repetition guard: a reply stuck repeating the same text is cut off early and the model is told why.

v6 on TACC: Qwen wrote base64 "X19f" for the whole 4096-token budget, "fbsdfd" for three turns in a row, and one block
of comments over and over. Each loop cost 25-40 s and still ended as an invalid action.
"""

from __future__ import annotations

import glob
import hashlib
import json
import shutil
import string
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flagpilot import agent, tagger  # noqa: E402
from flagpilot.client import ChatClient, Completion, looping  # noqa: E402
from flagpilot.config import Budget, ModelSpec, RunConfig, SandboxLimits  # noqa: E402
from flagpilot.tasks import Task  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class TestLooping(unittest.TestCase):
    def test_loops_seen_on_tacc_are_caught(self):
        head = '{"thought": "decode the blob", "action": "python", "code": "s = \'"'
        self.assertTrue(looping(head + "X19f" * 1200))
        self.assertTrue(looping(head + "fbsdfd" * 800))
        block = ("# Let me look at the email more carefully\\n# Check if there's any hidden content in the email\\n"
                 "# by looking at the email structure\\n\\n")
        self.assertTrue(looping(head + block * 14))

    def test_real_replies_that_hit_the_limit_in_v6_are_caught_from_their_text(self):
        caught = []
        for f in glob.glob(str(ROOT / "results" / "tacc" / "v6" / "*" / "*" / "trace.jsonl")):
            with open(f, encoding="utf-8") as fh:
                lines = fh.read().split("\n")          # not splitlines(): records hold U+2028 and friends
            for line in lines:
                r = json.loads(line) if line.strip() else {}
                if r.get("type") == "model" and r.get("finish_reason") == "length" and \
                        any(x in (r.get("text") or "")[-400:] for x in ("X19fX19f", "fbsdfdfbsdfd")):
                    caught.append(looping(r.get("text") or ""))
        if not caught:
            self.skipTest("v6 records not present")
        self.assertTrue(all(caught), caught)

    def test_ordinary_long_replies_are_left_alone(self):
        code = "\n".join(f"x{i} = pow({i * 7919}, {i + 3}, {10**9 + 7}) ^ 0x{i * 2654435761 % 2**32:08x}" for i in range(120))
        self.assertFalse(looping(json.dumps({"thought": "compute", "action": "python", "code": code})))
        abc = string.ascii_uppercase
        table = "\n".join(" ".join(abc[(r + c) % 26] for c in range(26)) for r in range(26))     # a Vigenere square
        self.assertFalse(looping(table * 1))
        n = "".join(str((i * 7 + 3) % 10) for i in range(600))                                    # one long constant, twice
        self.assertFalse(looping(f"n = {n}\nprint(n % 65537)\nassert n == {n}\n" + "# done\n" * 3))
        self.assertFalse(looping("ab" * 500), "short replies never count")
        self.assertFalse(looping(" " * 5000), "whitespace runs are not a loop worth stopping")


class Looper(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    sent = 0

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            first = {"choices": [{"delta": {"content": '{"thought": "decode", "action": "python", "code": "s = \''}}]}
            self.wfile.write(b"data: " + json.dumps(first).encode() + b"\n\n")
            for _ in range(5000):                       # 20 000 characters of "X19f" if nobody stops it
                chunk = {"choices": [{"delta": {"content": "X19f"}}]}
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
                self.wfile.flush()
                Looper.sent += 1
                time.sleep(0.0005)
            self.wfile.write(b"data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "length"}]}).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True


class TestStreamIsCutOff(unittest.TestCase):
    def test_the_client_stops_reading_a_looping_reply(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Looper)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        c = ChatClient(ModelSpec(name="stub", provider="openai-compatible", base_url=f"http://127.0.0.1:{srv.server_address[1]}/v1",
                                 api_key="x", max_tokens=4096))
        comp = c.complete([{"role": "user", "content": "go"}])
        self.assertEqual(comp.finish_reason, "repetition")
        self.assertLess(len(comp.text), 4000, "stopped within a few hundred characters of the loop becoming clear")
        self.assertEqual(agent.truncation_signal(comp, 4096), "repetition")


class TestAgentCorrects(unittest.TestCase):
    def test_a_cut_off_loop_is_an_invalid_action_with_its_own_correction(self):
        flag = "picoCTF{loop_guard_1}"
        tmp = Path(tempfile.mkdtemp(prefix="fp-loop-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        d = tmp / "tasks" / "loop-001"
        d.mkdir(parents=True)
        (d / "data.txt").write_text(flag + "\n", encoding="utf-8")
        task = Task(task_id=1, name="loop-001", category="Synthetic", title="loop", query="read the file",
                    gold_sha256=hashlib.sha256(flag.encode()).hexdigest(), assets=["data.txt"],
                    assets_dir="tasks/loop-001", suggested_tools=[], scope="core")
        script = "print(open('challenge/data.txt').read().strip())\n"
        seen = []

        class Stuck:
            spec = ModelSpec(name="stub", max_tokens=4096)

            def __init__(self):
                self.n = 0

            def complete(self, messages, max_tokens=None, on_delta=None):
                seen.append(messages[-1]["content"])
                self.n += 1
                if self.n == 1:
                    return Completion(text='{"thought": "x", "code": "' + "X19f" * 400, completion_tokens=400,
                                      finish_reason="repetition", max_tokens=4096, usage_source="estimated")
                return Completion(text=json.dumps({"action": "submit", "flag": flag, "script": script}),
                                  completion_tokens=40, finish_reason="stop", max_tokens=4096)

            def scrape_metrics(self):
                from flagpilot.client import ServerMetrics
                return ServerMetrics()

        cfg = RunConfig(model=ModelSpec(name="stub", max_tokens=4096), budget=Budget(max_turns=6, max_wall_s=120),
                        limits=SandboxLimits(action_timeout_s=10), replay_timeout_s=20.0, tasks_root=tmp)
        original = agent.build_client
        agent.build_client = lambda spec: Stuck()
        try:
            summary = agent.run_attempt(task, cfg, tmp, seed=0)
        finally:
            agent.build_client = original
        rows = [json.loads(x) for x in (tmp / "loop-001-s0" / "trace.jsonl").read_text().splitlines() if x.strip()]
        bad = [r for r in rows if r["type"] == "invalid_action"]
        self.assertEqual(bad[0]["kind"], "repetition")
        self.assertIn("repeating the same text", seen[1])
        self.assertEqual(summary["verdict"], "verified")
        tags = tagger.classify_run(tmp / "loop-001-s0", {"verdict": "verified", "reason": ""})["tags"]
        self.assertIn("repetition", tags)


if __name__ == "__main__":
    unittest.main()
