"""Tool calls written as a model's own markup instead of the JSON protocol (2026-11-22).

Imported challenge AdultCSP: Gemini was overloaded, the provider chain handed the turn to DeepSeek V4, and DeepSeek wrote
its tool calls as DSML in the reply text. Three such replies were rejected as protocol failures and ended the run. The
replies below are copied from that run (2161eb3454b0) and from AXOVI's (3418f711a7c6, turn 18: a malformed variant).
The same day GLM wrote JSON with raw tabs and newlines inside strings, and DeepSeek a draft action before a lone </think>;
those replies are below too. Of the evaluated batches' protocol breaks (Qwen), none of v7's 51 is of these kinds; 2 of v6's 85
would now be read as actions (v6 B1 crypto-072-s1, turns 12 and 15: '{"thought":' then a stray </think> then the whole JSON).
Recorded verdicts do not change: re-scoring replays submissions, and these replies were never submissions."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from flagpilot.tools import as_action, markup_call, parse_action  # noqa: E402

TURN5 = ("\n<｜DSML｜ calls>\n<｜DSML｜ invoke name=\"python\">\n<｜DSML｜ parameter name=\"code\" string=\"true\">with open('challenge/diff.txt')"
         " as f: t=f.read(); print(t[5500:8500])</｜DSML｜ parameter>\n</｜DSML｜ invoke>\n</｜DSML｜ calls>")
TURN9 = ("\n<｜DSML｜ calls>\n<｜DSML｜ invoke name=\"shell\">\n<｜DSML｜ parameter name=\"cmd\" string=\"true\">grep -r -i -a -E "
         "\"flag|dice\\{|ctf\\{\" challenge/ | head -50; echo ---; ls -la challenge/</｜DSML｜ parameter>\n</｜DSML｜ invoke>\n</｜DSML｜ calls>")
AXOVI18 = ("\n<｜DSML｜ calls>\n<｜DSML｜ invoke name=\"action\" string=\"true\">shell</｜DSML｜ parameter>\n<｜DSML｜ parameter name=\"cmd\" "
           "string=\"true\">xxd ./challenge/axovi | sed -n '1,60p'</｜DSML｜ parameter>\n</｜DSML｜ invoke>\n</｜DSML｜ calls>")


# AXOVI turn 10 (6fd0b09623af, DeepSeek after Gemini was overloaded): a draft inside the reasoning, then a lone </think>, then
# the real answer; the draft's command holds a brace inside a string
AXOVI10 = '{"thought": "Search whole binary for flag-like patterns including brackets and known prefixes.", "action": "shell", "cmd": "grep -a -o -E \'(flag|CTF|battle)[^ ]*\' ./challenge/axovi; echo \'===\'; strings -n 4 ./challenge/axovi | grep -iE \'flag|\\\\{|battle|win\' ; echo \'===notes===\'; xxd -s 0x338 -l 0x40 ./challenge/axovi"}\n\nLet me also consider: maybe the flag isn\'t in the binary. Let me run the binary to confirm behavior.</think>{"thought": "Check for embedded flag strings and observe runtime behavior of the binary.", "action": "shell", "cmd": "strings -n 4 ./challenge/axovi | grep -iE \'flag|battle|win|shell\'; echo \'===run===\'; printf \'hello\\\\n\' | ./challenge/axovi; echo; echo \\"exit=$?\\""}'
# pemu (GLM): a whole JSON object whose string holds a raw tab (JSON wants \\t)
PEMU16 = '{"thought": "Find PLT imports and where execute() calls them.", "action": "shell", "cmd": "objdump -d -j .plt challenge/pemu__loader | grep \'@plt\' ; echo ---; objdump -d --start-address=0x1939c --stop-address=0x1b2e0 challenge/pemu__loader | grep \'jmpl\\\\|calll\' | grep -v \'jmpl\t\\\\*%eax\' | head -30"}'


class TestMarkupCalls(unittest.TestCase):
    def test_the_recorded_dsml_replies_are_actions(self):
        a, err = parse_action(TURN5)
        self.assertEqual((a.action, a.code, err), ("python", "with open('challenge/diff.txt') as f: t=f.read(); print(t[5500:8500])", None))
        a, err = parse_action(TURN9)
        self.assertEqual((a.action, err), ("shell", None))
        self.assertTrue(a.cmd.startswith('grep -r -i -a -E "flag|dice\\{|ctf\\{" challenge/'))
        a, err = parse_action(AXOVI18)
        self.assertEqual((a.action, a.cmd, err), ("shell", "xxd ./challenge/axovi | sed -n '1,60p'", None))

    def test_text_before_the_markup_is_the_thought_and_typed_parameters_are_json(self):
        obj = markup_call('Look at the file first.\n<｜DSML｜ invoke name="strings"><｜DSML｜ parameter name="path" string="true">'
                          'challenge/a.bin</｜DSML｜ parameter><｜DSML｜ parameter name="min_len" string="false">8</｜DSML｜ parameter>'
                          '</｜DSML｜ invoke>')
        self.assertEqual(obj, {"action": "strings", "path": "challenge/a.bin", "min_len": 8, "thought": "Look at the file first."})
        self.assertEqual(parse_action(f"<|DSML| invoke name=\"shell\"><|DSML| parameter name=\"cmd\">ls</|DSML| parameter></|DSML| invoke>")[0].cmd,
                         "ls", "ASCII bars too")

    def test_function_call_json_is_the_same_action(self):
        self.assertEqual(parse_action(json.dumps({"name": "shell", "arguments": {"cmd": "ls -la"}}))[0].cmd, "ls -la")
        self.assertEqual(parse_action(json.dumps({"name": "python", "arguments": json.dumps({"code": "print(1)"})}))[0].code, "print(1)")
        self.assertEqual(as_action({"action": "shell", "cmd": "x", "name": "y", "arguments": {}}),
                         {"action": "shell", "cmd": "x", "name": "y", "arguments": {}}, "the protocol's own field wins")

    def test_the_answer_after_a_lone_think_tag_wins_and_braces_inside_strings_do_not_count(self):
        a, err = parse_action(AXOVI10)
        self.assertEqual((a.action, err), ("shell", None))
        self.assertIn("===run===", a.cmd, "the answer after </think>, not the draft before it")
        a, _ = parse_action('{"thought": "x", "action": "shell", "cmd": "grep -E \'\\\\{\' f"} and some words after it')
        self.assertEqual(a.cmd, "grep -E '\\{' f")
        a, _ = parse_action('<think>plan</think>{"action": "shell", "cmd": "ls"}')
        self.assertEqual(a.cmd, "ls", "a whole think block is still removed")

    def test_v6_qwen_reply_with_a_stray_think_tag(self):
        # v6 B1 crypto-072-s1 turn 15, as recorded: rejected then, read as the action after the tag today
        a, err = parse_action('{"thought":\n</think>\n\n{"thought": "Let me check the .tmp/action.py file - it might contain the '
                              'actual challenge parameters or ciphertext.", "action": "shell", "cmd": "cat ./.tmp/action.py"}')
        self.assertEqual((a.action, a.cmd, err), ("shell", "cat ./.tmp/action.py", None))

    def test_raw_control_characters_inside_strings(self):
        a, err = parse_action(PEMU16)
        self.assertEqual((a.action, err), ("shell", None))
        self.assertIn("jmpl\t", a.cmd, "the tab the model wrote stays in the command")

    def test_what_is_still_not_an_action(self):
        self.assertIsNone(markup_call("Let me think about this."))
        self.assertIsNone(markup_call('<｜DSML｜ parameter name="cmd" string="true">ls</｜DSML｜ parameter>'), "no action named")
        a, err = parse_action('<｜DSML｜ invoke name="launch_rockets"><｜DSML｜ parameter name="n">3</｜DSML｜ parameter></｜DSML｜ invoke>')
        self.assertIsNone(a)
        self.assertIn("launch_rockets", err, "an unknown action is named in the correction")

    def test_in_the_loop_a_dsml_turn_runs_instead_of_counting_as_a_protocol_failure(self):
        from test_live_stream import FLAG, SCRIPT, make_task
        from flagpilot import agent
        from flagpilot.client import Completion, ServerMetrics
        from flagpilot.config import Budget, ModelSpec, RunConfig, SandboxLimits

        replies = [TURN9.replace("grep -r -i -a -E \"flag|dice\\{|ctf\\{\" challenge/ | head -50; echo ---; ls -la challenge/", "ls challenge"),
                   json.dumps({"thought": "decode", "action": "submit", "flag": FLAG, "script": SCRIPT})]

        class Client:
            def complete(self, messages, **kw):
                return Completion(text=replies.pop(0), prompt_tokens=5, completion_tokens=5, latency_ms=1, ttft_ms=1, finish_reason="stop")

            def scrape_metrics(self):
                return ServerMetrics(error="not configured")

        tmp = Path(tempfile.mkdtemp(prefix="fp-dsml-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        cfg = RunConfig(model=ModelSpec(name="mock"), budget=Budget(max_turns=4, max_wall_s=60),
                        limits=SandboxLimits(action_timeout_s=10), replay_timeout_s=20.0, tasks_root=tmp)
        original = agent.build_client
        agent.build_client = lambda spec: Client()
        try:
            summary = agent.run_attempt(make_task(tmp), cfg, tmp, seed=0)
        finally:
            agent.build_client = original
        self.assertEqual((summary["verdict"], summary["invalids"], summary["turns"]), ("verified", 0, 2))


if __name__ == "__main__":
    unittest.main()
