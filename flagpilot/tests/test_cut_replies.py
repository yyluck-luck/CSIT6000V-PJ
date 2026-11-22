"""Replies that end in the middle of their JSON (README, known limitation 16; 2026-11-22).

All 51 protocol breaks in v7 ended that way with finish_reason "stop", 48 of them inside a string; 47 came after a reply
that the history had cut at 2000 characters in the same attempt (v6: 81 of 85). Three switches, all off by default as in
v5–v7: the history keeps long replies as whole JSON (elide), a cut reply is continued once, tool outputs end with a fence.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_live_stream import FLAG, SCRIPT, make_task  # noqa: E402

from flagpilot import agent, cli  # noqa: E402
from flagpilot.client import Completion, ServerMetrics  # noqa: E402
from flagpilot.config import Budget, ModelSpec, Policy, RunConfig, SandboxLimits  # noqa: E402
from flagpilot.tools import json_cut  # noqa: E402

FULL = json.dumps({"thought": "decode it", "action": "submit", "flag": FLAG, "script": SCRIPT})
CUT, REST = FULL[:-30], FULL[-30:]


class Scripted:
    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def scrape_metrics(self):
        return ServerMetrics(error="not configured")

    def complete(self, messages, max_tokens=None, on_delta=None):
        self.seen.append([dict(m) for m in messages])
        text = self.replies.pop(0)
        return Completion(text=text, prompt_tokens=10, completion_tokens=5, ttft_ms=1, latency_ms=2, finish_reason="stop")


def run(replies, **policy):
    tmp = Path(tempfile.mkdtemp(prefix="fp-cut-"))
    cfg = RunConfig(model=ModelSpec(name="mock"), budget=Budget(max_turns=6, max_wall_s=120), policy=Policy(**policy),
                    limits=SandboxLimits(action_timeout_s=10), replay_timeout_s=20.0, tasks_root=tmp)
    client = Scripted(replies)
    original = agent.build_client
    agent.build_client = lambda spec: client
    try:
        summary = agent.run_attempt(make_task(tmp), cfg, tmp, seed=0)
    finally:
        agent.build_client = original
    rows = [json.loads(x) for x in (tmp / "live-001-s0" / "trace.jsonl").read_text().splitlines() if x.strip()]
    shutil.rmtree(tmp, ignore_errors=True)
    return summary, rows, client


class TestCutDetection(unittest.TestCase):
    def test_what_counts_as_cut(self):
        self.assertTrue(json_cut(CUT))
        self.assertTrue(json_cut('```json\n{"thought": "x", "action": "python", "code": "n = [1, 2, 3'))
        self.assertTrue(json_cut('{"thought": "Let me think about this differently. Th'))      # forensics-059
        self.assertFalse(json_cut(FULL))
        self.assertFalse(json_cut("I will now look at the file."))
        # crypto-092-s0: quotes not escaped; the braces close, so this is not a cut reply
        self.assertFalse(json_cut('{"thought": "The message is "1nv3r53ly". Submit.", "action": "shell", "cmd": "ls"}'))
        self.assertFalse(json_cut('{"thought": "a \\"quoted\\" word", "action": "shell", "cmd": "ls"}'))


class TestHistoryShape(unittest.TestCase):
    def long_reply(self) -> str:
        code = "n = " + "1234567890" * 300 + "\nprint(n % 97)\n"
        return json.dumps({"thought": "compute it", "action": "python", "code": code})

    def test_cut_is_the_first_2000_characters_as_before(self):
        text = self.long_reply()
        self.assertEqual(agent.history_reply(text), text[:2000])
        self.assertEqual(agent.history_reply("short"), "short")

    def test_elide_keeps_the_json_whole_and_says_what_is_missing(self):
        text = self.long_reply()
        out = agent.history_reply(text, "elide")
        self.assertLessEqual(len(out), 2000)
        obj = json.loads(out)
        self.assertEqual((obj["thought"], obj["action"]), ("compute it", "python"))
        self.assertIn("characters not shown", obj["code"])
        self.assertTrue(obj["code"].startswith("n = 1234567890") and obj["code"].endswith("print(n % 97)\n"))
        self.assertEqual(agent.history_reply(CUT * 40, "elide"), (CUT * 40)[:2000], "not JSON: cut as before")


class TestInTheLoop(unittest.TestCase):
    def test_off_by_default_a_cut_reply_is_a_protocol_break(self):
        summary, rows, _ = run([CUT, FULL])
        self.assertEqual([r["kind"] for r in rows if r["type"] == "invalid_action"], ["protocol"])
        self.assertEqual((summary["verdict"], summary["turns"]), ("verified", 2))
        self.assertFalse([r for r in rows if r["type"] == "continued"])

    def test_continue_cut_finishes_the_reply_in_the_same_turn(self):
        summary, rows, client = run([CUT, REST], continue_cut=True)
        self.assertEqual((summary["verdict"], summary["turns"], summary["invalids"]), ("verified", 1, 0))
        cont = [r for r in rows if r["type"] == "continued"]
        self.assertEqual([(c["ok"], c["turn"]) for c in cont], [(True, 1)])
        self.assertEqual((summary["prompt_tokens"], summary["completion_tokens"]), (20, 10), "the extra request is counted")
        ask = client.seen[1]
        self.assertEqual((ask[-2]["role"], ask[-2]["content"]), ("assistant", CUT))
        self.assertIn("stopped in the middle of its JSON object", ask[-1]["content"])

    def test_a_model_that_starts_over_is_fine_too(self):
        summary, rows, _ = run([CUT, FULL], continue_cut=True)
        self.assertEqual((summary["verdict"], summary["turns"], summary["invalids"]), ("verified", 1, 0))

    def test_a_continuation_that_does_not_help_falls_back_to_the_correction(self):
        summary, rows, _ = run([CUT, "sorry", FULL], continue_cut=True)
        self.assertEqual([r["kind"] for r in rows if r["type"] == "invalid_action"], ["protocol"])
        self.assertEqual([c["ok"] for c in rows if c["type"] == "continued"], [False])
        self.assertEqual(summary["verdict"], "verified")

    def test_elide_and_fence_change_what_the_model_sees_next(self):
        code = "x = '" + "ab" * 1500 + "'\nprint(len(x))\n"
        long_act = json.dumps({"thought": "measure", "action": "python", "code": code})
        _, _, plain = run([long_act, FULL])
        _, _, shaped = run([long_act, FULL], reply_history="elide", fence_output=True)
        before, after = plain.seen[1], shaped.seen[1]
        self.assertEqual(before[-2]["content"], long_act[:2000])
        self.assertTrue(json_cut(before[-2]["content"]), "v5–v7: the history shows a reply cut mid-JSON")
        self.assertIsInstance(json.loads(after[-2]["content"]), dict)
        self.assertFalse(before[-1]["content"].rstrip().endswith(agent.OUTPUT_FENCE))
        self.assertTrue(after[-1]["content"].rstrip().endswith(agent.OUTPUT_FENCE))


class TestSwitches(unittest.TestCase):
    def test_command_line(self):
        cfg = cli._build_cfg(cli.build_parser().parse_args(["run", "--task", "demo-001"]))
        self.assertEqual((cfg.policy.reply_history, cfg.policy.continue_cut, cfg.policy.fence_output), ("cut", False, False))
        cfg = cli._build_cfg(cli.build_parser().parse_args(
            ["run", "--task", "demo-001", "--reply-history", "elide", "--continue-cut", "--fence-output"]))
        self.assertEqual((cfg.policy.reply_history, cfg.policy.continue_cut, cfg.policy.fence_output), ("elide", True, True))
        self.assertEqual(cfg.fingerprint()["policy"]["reply_history"], "elide", "the trace records the switches")

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_submit_sh(self):
        text = (ROOT / "deploy" / "tcloud" / "submit.sh").read_text(encoding="utf-8")
        start = text.index('    case "${FP_REPLY_HISTORY:-cut}" in')
        end = text.index('--fence-output"; fi\n', start) + len('--fence-output"; fi\n')
        block = text[start:end]
        for env, rc, args in (({}, 0, ""), ({"FP_REPLY_HISTORY": "elide", "FP_CONTINUE_CUT": "1", "FP_FENCE_OUTPUT": "1"}, 0,
                              " --reply-history elide --continue-cut --fence-output"), ({"FP_REPLY_HISTORY": "x;y"}, 1, "")):
            e = {k: v for k, v in os.environ.items() if not k.startswith("FP_")}
            r = subprocess.run(["bash", "-c", 'ARGS=""\n' + block + 'echo "ARGS=$ARGS"'], capture_output=True, text=True,
                               env={**e, **env})
            self.assertEqual(r.returncode, rc, (env, r.stderr))
            if rc == 0:
                self.assertEqual(r.stdout.strip(), "ARGS=" + args)


if __name__ == "__main__":
    unittest.main()
