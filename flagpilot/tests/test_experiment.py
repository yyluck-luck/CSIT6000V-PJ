"""实验层测试：消融档位、结构化动作、记忆探针、失败分类、批次对比。

这一层不测"模型强不强"，只测"实验装置对不对"——
装置错了，跑出来的数再好看也是假的。
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

from flagpilot import metrics, probe, tagger
from flagpilot.agent import build_system_prompt
from flagpilot.config import ABLATIONS, SandboxLimits, get_ablation
from flagpilot.sandbox import Sandbox, probe_capabilities
from flagpilot.tools import Action, ToolBox, actions_for, parse_action

FLAG = "picoCTF{experiment_layer_ok}"


def sha(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()


class TestAblation(unittest.TestCase):
    def test_ladder_is_additive(self):
        """每一档只能比前一档多一个变量，否则提升不可归因。"""
        b0, b1, b2, b3 = (ABLATIONS[k] for k in ("B0", "B1", "B2", "B3"))
        self.assertFalse(b0.truncate)
        self.assertFalse(b0.structured_tools)
        self.assertEqual(b0.prompt, "minimal")

        self.assertTrue(b1.truncate)
        self.assertFalse(b1.structured_tools)   # B1 只多了截断
        self.assertEqual(b2.prompt, "minimal")

        self.assertTrue(b2.structured_tools)
        self.assertEqual(b2.prompt, "minimal")  # B2 只多了工具箱

        self.assertEqual(b3.prompt, "full")     # B3 只多了 prompt
        self.assertTrue(b3.truncate and b3.structured_tools)

    def test_unknown_ablation_raises(self):
        with self.assertRaises(ValueError):
            get_ablation("B9")

    def test_prompt_contains_rules_only_when_full(self):
        self.assertNotIn("Rules:", build_system_prompt(ABLATIONS["B1"]))
        self.assertIn("Rules:", build_system_prompt(ABLATIONS["B3"]))

    def test_prompt_lists_structured_actions_only_when_enabled(self):
        self.assertNotIn('"action": "decode"', build_system_prompt(ABLATIONS["B1"]))
        self.assertIn('"action": "decode"', build_system_prompt(ABLATIONS["B2"]))


class TestStructuredTools(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="fp-exp-"))
        self.sb = Sandbox(self.root / "work", limits=SandboxLimits(action_timeout_s=10),
                          caps=probe_capabilities(), tasks_root=self.root)
        self.ch = self.sb.workdir / "challenge"
        self.ch.mkdir(parents=True, exist_ok=True)
        (self.ch / "note.txt").write_text("hello " + FLAG + "\n", encoding="utf-8")
        (self.ch / "blob.bin").write_bytes(b"\x7fELF" + b"\x00" * 32 + b"picoCTF")
        self.tb = ToolBox(self.sb, structured=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        self.sb.cleanup()

    def test_action_sets(self):
        self.assertEqual(actions_for(False), ("shell", "python", "submit"))
        self.assertIn("decode", actions_for(True))

    def test_structured_action_rejected_in_base_mode(self):
        a, err = parse_action('{"action":"decode","scheme":"rot13","text":"nop"}',
                              actions_for(False))
        self.assertIsNone(a)
        self.assertIn("action 必须是", err)

    def test_file_action_reads_magic(self):
        r = self.tb.execute(Action(thought="t", action="file", path="challenge/blob.bin"))
        self.assertTrue(r.ok)
        self.assertIn("ELF", r.stdout)

    def test_strings_action_finds_flag(self):
        r = self.tb.execute(Action(thought="t", action="strings",
                                   path="challenge/note.txt", min_len=6))
        self.assertTrue(r.ok)
        self.assertIn(FLAG, r.stdout)

    def test_decode_rot13(self):
        ct = "cvpbPGS{rkcrevzrag_ynlre_bx}"
        r = self.tb.execute(Action(thought="t", action="decode", scheme="rot13", text=ct))
        self.assertTrue(r.ok)
        self.assertEqual(r.stdout.strip(), FLAG)

    def test_path_escape_is_blocked(self):
        for bad in ("../etc/passwd", "/etc/passwd"):
            r = self.tb.execute(Action(thought="t", action="file", path=bad))
            self.assertFalse(r.ok, bad)
            self.assertIn("path", r.stderr)


class TestProbe(unittest.TestCase):
    def test_candidates_include_bare_and_embedded(self):
        cands = probe.extract_candidates(f'sure: {FLAG} — done')
        self.assertIn(FLAG, cands)

    def test_summarize_counts_hits(self):
        s = probe.summarize([
            {"task": "a", "hit": True, "refused": False, "error": ""},
            {"task": "b", "hit": False, "refused": True, "error": ""},
        ])
        self.assertEqual((s["n"], s["hits"], s["refused"]), (2, 1, 1))
        self.assertAlmostEqual(s["hit_rate"], 0.5)
        self.assertIn("a", s["hit_tasks"])

    def test_run_probe_detects_recall(self):
        """构造一个"记得答案"的假模型，探针必须把它抓出来。"""
        class Fake:
            class C:
                text = '{"flag": "%s"}' % FLAG
                tokens = 12
                error = ""
            def complete(self, messages, max_tokens=None):
                return self.C()

        from flagpilot.config import RunConfig

        class T:
            name, task_id, category, title, query = "synthetic-1", 1, "Cryptography", "", ""
            assets, assets_dir = [], "tasks/none"
            gold_sha256 = sha(FLAG)

        old = probe.build_client
        probe.build_client = lambda spec: Fake()
        try:
            r = probe.run_probe(T(), RunConfig(), mode="name")
        finally:
            probe.build_client = old
        self.assertTrue(r.hit)
        self.assertFalse(r.refused)

    def test_run_probe_records_refusal(self):
        """mock 没有记忆，必须如实记成 refused，不能算命中。"""
        from flagpilot.config import RunConfig

        class T:
            name, task_id, category, title, query = "synthetic-2", 2, "Cryptography", "", ""
            assets, assets_dir = [], "tasks/none"
            gold_sha256 = sha(FLAG)

        r = probe.run_probe(T(), RunConfig(), mode="brief")
        self.assertFalse(r.hit)
        self.assertTrue(r.refused)


class TestTagger(unittest.TestCase):
    def rec(self, **kw):
        base = {"run_id": "t-s0", "verdict": "verified", "reason": "match",
                "turns": 3, "invalids": 0, "replay": {"reason": "match", "detail": ""},
                "run_dir": ""}
        base.update(kw)
        return base

    def test_verified_is_ok(self):
        c = tagger.classify_run(None, self.rec())
        self.assertEqual(c["primary"], "ok")

    def test_hardcoded_beats_wrong(self):
        c = tagger.classify_run(None, self.rec(verdict="unverified",
                                               replay={"reason": "literal_flag", "detail": "x"}))
        self.assertEqual(c["primary"], "hardcoded")

    def test_not_reproducible(self):
        c = tagger.classify_run(None, self.rec(verdict="unverified",
                                               replay={"reason": "script_error", "detail": "x"}))
        self.assertEqual(c["primary"], "not_reproducible")

    def test_protocol_budget(self):
        c = tagger.classify_run(None, self.rec(verdict="budget", reason="invalid_action"))
        self.assertEqual(c["primary"], "protocol")

    def test_no_submit(self):
        c = tagger.classify_run(None, self.rec(verdict="budget", reason="max_turns", turns=20))
        self.assertEqual(c["primary"], "no_submit")

    def test_environment_tag_from_trace(self):
        root = Path(tempfile.mkdtemp(prefix="fp-tag-"))
        try:
            d = root / "t-s0"
            d.mkdir()
            (d / "trace.jsonl").write_text(
                '{"type":"tool","turn":1,"action":"shell","cmd":"tshark -r a.pcap",'
                '"stderr_head":"sh: tshark: command not found","timed_out":false}\n',
                encoding="utf-8")
            c = tagger.classify_run(d, self.rec(verdict="budget", reason="max_turns"))
            self.assertIn("tool_missing", c["tags"])
            # 环境类标签不该盖过"没提交"这个主因，但必须被记下来
            self.assertEqual(c["primary"], "tool_missing")
        finally:
            shutil.rmtree(root, ignore_errors=True)


class TestMetricsTables(unittest.TestCase):
    def results(self):
        return [
            {"run_id": "a-s0", "verdict": "verified", "category": "Cryptography",
             "turns": 3, "wall_s": 10, "total_tokens": 1000, "cost_usd": 0,
             "ablation": "B3", "run_dir": "", "replay": {"reason": "match"}},
            {"run_id": "b-s0", "verdict": "unverified", "category": "Cryptography",
             "turns": 5, "wall_s": 20, "total_tokens": 2000, "cost_usd": 0,
             "ablation": "B3", "run_dir": "", "replay": {"reason": "literal_flag"}},
            {"run_id": "c-s0", "verdict": "wrong", "category": "Forensics",
             "turns": 7, "wall_s": 30, "total_tokens": 3000, "cost_usd": 0,
             "ablation": "B3", "run_dir": "", "replay": {"reason": "wrong_flag"}},
        ]

    def test_gap_and_tags(self):
        s = metrics.summarize(self.results())
        self.assertEqual(s["n"], 3)
        self.assertAlmostEqual(s["verified_rate"], 1 / 3)
        self.assertAlmostEqual(s["raw_rate"], 2 / 3)
        self.assertAlmostEqual(s["gap"], 1 / 3)
        self.assertIn("hardcoded", s["failure_tags"])

    def test_markdown_has_ci(self):
        md = metrics.to_markdown(metrics.summarize(self.results()), title="demo")
        self.assertIn("| verified 解出率 |", md)
        self.assertIn("95% CI", md)
        self.assertIn("### demo", md)

    def test_compare_table(self):
        s = metrics.summarize(self.results(), with_tags=False)
        table = metrics.compare({"B2": s, "B3": s})
        self.assertIn("| batch | n |", table)
        self.assertIn("| B2 |", table)
        self.assertIn("| B3 |", table)


class TestLoopGuard(unittest.TestCase):
    def test_repeat_warning_is_emitted(self):
        """同一个动作连做三次必须被拦一次，否则小模型会一直转到轮次耗尽。"""
        import json
        from flagpilot import agent
        from flagpilot.client import Completion, ServerMetrics
        from flagpilot.config import Budget, RunConfig
        from flagpilot.tasks import Task

        tmp = Path(tempfile.mkdtemp(prefix="fp-loop-"))
        task = Task(task_id=-1, name="rep", category="X", title="", query="",
                    gold_sha256="0" * 64, assets=[], assets_dir="tasks/none",
                    suggested_tools=[], scope="core")
        cfg = RunConfig(budget=Budget(max_turns=6, max_wall_s=30),
                        limits=SandboxLimits(action_timeout_s=5))

        class C:
            def complete(self, messages, max_tokens=None):
                return Completion(text='{"thought":"t","action":"shell","cmd":"echo hi"}',
                                  prompt_tokens=10, completion_tokens=5, ttft_ms=1, latency_ms=2)

            def scrape_metrics(self):
                return ServerMetrics()

        old = agent.build_client
        agent.build_client = lambda spec: C()
        try:
            agent.run_attempt(task, cfg, tmp, seed=0)
            with open(tmp / "rep-s0" / "trace.jsonl", encoding="utf-8") as fh:
                rows = [json.loads(l) for l in fh]
        finally:
            agent.build_client = old
            shutil.rmtree(tmp, ignore_errors=True)

        warns = [r for r in rows if r.get("type") in ("repeat_warning", "repeat_blocked")]
        self.assertTrue(warns, "连续三次同一动作应被拦下（repeat_warning 或 repeat_blocked）")

    @staticmethod
    def _run(replies, max_turns):
        """Run the agent against a scripted model: replies(turn) gives the action JSON of each call. Returns the trace
        rows and every message list the model was sent."""
        import json
        from flagpilot import agent
        from flagpilot.client import Completion, ServerMetrics
        from flagpilot.config import Budget, RunConfig
        from flagpilot.tasks import Task

        tmp = Path(tempfile.mkdtemp(prefix="fp-loop-"))
        task = Task(task_id=-1, name="rep", category="X", title="", query="",
                    gold_sha256="0" * 64, assets=[], assets_dir="tasks/none",
                    suggested_tools=[], scope="core")
        cfg = RunConfig(budget=Budget(max_turns=max_turns, max_wall_s=60),
                        limits=SandboxLimits(action_timeout_s=5))
        sent = []

        class C:
            def complete(self, messages, max_tokens=None):
                sent.append([dict(m) for m in messages])
                return Completion(text=json.dumps(replies(len(sent))), prompt_tokens=10, completion_tokens=5,
                                  ttft_ms=1, latency_ms=2)

            def scrape_metrics(self):
                return ServerMetrics()

        old = agent.build_client
        agent.build_client = lambda spec: C()
        try:
            agent.run_attempt(task, cfg, tmp, seed=0)
            with open(tmp / "rep-s0" / "trace.jsonl", encoding="utf-8") as fh:
                rows = [json.loads(l) for l in fh]
        finally:
            agent.build_client = old
            shutil.rmtree(tmp, ignore_errors=True)
        return rows, sent

    def test_an_action_that_gave_the_same_result_twice_in_a_row_is_not_run_again(self):
        """2026-11-22, Qwen 27B on TACC: from turn 18 on the model sent the very same reply 83 times (`ls ... | grep`,
        exit 1, no output). The guard warned once, at turn 19, and then let it run to the 100-turn limit. Now the third
        identical action in a row whose two runs gave the same result is not run; the model is told why; and when it
        keeps sending it, the run ends early instead of using every turn."""
        rows, sent = self._run(lambda n: {"thought": "t", "action": "shell", "cmd": "echo hi"}, max_turns=20)
        tools = [r for r in rows if r.get("type") == "tool"]
        blocked = [r for r in rows if r.get("type") == "repeat_blocked"]
        summary = rows[-1]
        self.assertEqual(len(tools), 2, "run twice, then not again")
        self.assertEqual([int(b["turn"]) for b in blocked], [3, 4, 5])
        self.assertEqual(blocked[0]["same_as"], [1, 2])
        self.assertEqual((summary["verdict"], summary["reason"], int(summary["turns"])), ("budget", "repeat_loop", 5))
        told = sent[3][-1]["content"]
        self.assertIn("NOT RUN", told)
        self.assertIn("steps 1 and 2", told)

    def test_the_same_action_runs_again_when_something_else_happened_or_its_result_changed(self):
        script = ["echo hi", "echo hi", "touch x", "echo hi", "echo hi"]
        rows, _ = self._run(lambda n: {"thought": "t", "action": "shell", "cmd": script[min(n, len(script)) - 1]}, max_turns=5)
        self.assertEqual(len([r for r in rows if r.get("type") == "tool"]), 5, "another action in between: nothing blocked")
        self.assertFalse([r for r in rows if r.get("type") == "repeat_blocked"])
        rows, _ = self._run(lambda n: {"thought": "t", "action": "shell", "cmd": "echo x >> n.txt; wc -l < n.txt"}, max_turns=6)
        self.assertEqual(len([r for r in rows if r.get("type") == "tool"]), 6, "a result that changes every time is not a loop")
        self.assertFalse([r for r in rows if r.get("type") == "repeat_blocked"])

    def test_the_repeat_warning_is_given_again_when_the_model_keeps_coming_back(self):
        """A loop over two actions (A, B, A, B, ...) is warned about more than once, not only the first time."""
        rows, _ = self._run(lambda n: {"thought": "t", "action": "shell", "cmd": "echo a" if n % 2 else "echo b"}, max_turns=14)
        warned_a = [r for r in rows if r.get("type") == "repeat_warning" and r.get("cmd") == "echo a"]
        self.assertGreaterEqual(len(warned_a), 2, [int(r["turn"]) for r in warned_a])


if __name__ == "__main__":
    unittest.main()
