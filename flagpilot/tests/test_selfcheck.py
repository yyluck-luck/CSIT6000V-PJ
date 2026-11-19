"""提交前自检 + 材料缺失重放 + 修复轮预算解耦。

三件事解决的是同一个问题：**"脚本跑不通"这个信息到手太晚**。

- 自检：让模型在 submit 之前就知道脚本跑不跑得起来（代价一轮，而不是一次被拒）
- 材料缺失重放：抓住"字面量检查漏掉的编码形态"——删掉题目材料还能答对，
  说明答案不来自材料
- 修复轮预算：模型在最后一轮才提交时，proposal 承诺的那次反馈原本给不出去
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flagpilot import agent, tagger, tools, verify  # noqa: E402
from flagpilot.client import Completion, ServerMetrics  # noqa: E402
from flagpilot.config import Budget, ModelSpec, RunConfig, SandboxLimits  # noqa: E402
from flagpilot.sandbox import Sandbox  # noqa: E402
from flagpilot.tasks import Task  # noqa: E402

FLAG = "picoCTF{self_check_0123456789}"

# 从附件里真算出来的脚本
GOOD_SCRIPT = (
    "import base64\n"
    "for line in open('challenge/data.txt'):\n"
    "    if 'encoded=' in line:\n"
    "        print(base64.b64decode(line.split('=', 1)[1]).decode())\n"
)
# 把答案拆成整数列表再拼回来：不含 flag 字面量，也不含它的 hex/base64，
# 字面量检查抓不到——只有"删掉材料还能答对"能抓。
INT_SCRIPT = "print(''.join(chr(c) for c in %r))\n" % [ord(c) for c in FLAG]
SILENT_SCRIPT = "value = 1\n"
CRASH_SCRIPT = "x = None\nprint(x.group(1))\n"
NOFLAG_SCRIPT = "print('nothing interesting here')\n"


def make_task(root: Path) -> Task:
    d = root / "tasks" / "selfcheck-001"
    d.mkdir(parents=True, exist_ok=True)
    enc = base64.b64encode(FLAG.encode()).decode()
    (d / "data.txt").write_text(f"header line\nencoded={enc}\ntrailer\n", encoding="utf-8")
    return Task(
        task_id=1, name="selfcheck-001", category="Synthetic", title="selfcheck",
        query="decode the payload",
        gold_sha256=hashlib.sha256(FLAG.encode()).hexdigest(),
        assets=["data.txt"], assets_dir="tasks/selfcheck-001",
        suggested_tools=[], scope="core",
    )


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-selfcheck-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.task = make_task(self.tmp)


class TestDryRun(Base):
    def test_script_that_prints_a_flag(self):
        r = verify.dry_run(self.task, GOOD_SCRIPT, self.tmp, timeout_s=20)
        self.assertTrue(r.ok)
        self.assertEqual(r.reason, "printed")
        self.assertIn(FLAG, r.stdout)
        # 给模型的话要指向下一步，而不是复述输出
        self.assertIn("submit", r.as_observation())

    def test_script_that_prints_nothing(self):
        r = verify.dry_run(self.task, SILENT_SCRIPT, self.tmp, timeout_s=20)
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, "no_output")

    def test_script_that_crashes(self):
        r = verify.dry_run(self.task, CRASH_SCRIPT, self.tmp, timeout_s=20)
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, "script_error")
        self.assertIn("AttributeError", r.stderr)

    def test_output_without_flag_shape(self):
        r = verify.dry_run(self.task, NOFLAG_SCRIPT, self.tmp, timeout_s=20)
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, "no_flag")

    def test_empty_script(self):
        r = verify.dry_run(self.task, "   ", self.tmp, timeout_s=20)
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, "no_output")

    def test_dry_run_does_not_touch_workdir(self):
        """自检是只读的：它不该在工作目录里留下 solve.py 或 challenge/。"""
        work = self.tmp / "work"
        work.mkdir()
        before = sorted(p.name for p in work.iterdir())
        verify.dry_run(self.task, GOOD_SCRIPT, self.tmp, timeout_s=20)
        self.assertEqual(before, sorted(p.name for p in work.iterdir()))


class TestMaterialFree(Base):
    def test_int_list_script_slips_past_literal_check(self):
        """先确认它确实能绕过字面量检查——否则这个测试没在测想测的东西。"""
        material = verify.material_text(self.task, self.tmp)
        self.assertIsNone(verify.scan_script(INT_SCRIPT, FLAG, material))

    def test_int_list_script_is_caught_by_material_free(self):
        r = verify.evaluate(self.task, FLAG, INT_SCRIPT, self.tmp, timeout_s=20)
        self.assertEqual(r.verdict, "unverified")
        self.assertEqual(r.reason, "hardcoded_no_material")

    def test_good_script_survives_the_extra_check(self):
        r = verify.evaluate(self.task, FLAG, GOOD_SCRIPT, self.tmp, timeout_s=20)
        self.assertEqual(r.verdict, "verified")

    def test_check_can_be_disabled(self):
        r = verify.evaluate(self.task, FLAG, INT_SCRIPT, self.tmp,
                            timeout_s=20, material_free=False)
        self.assertEqual(r.verdict, "verified")

    def test_task_without_assets_is_not_accused(self):
        """没有附件的题本来就不该读材料，拿这条判它是误伤。"""
        bare = Task(task_id=2, name="bare", category="Synthetic", title="bare",
                    query="flag is derivable from the text",
                    gold_sha256=hashlib.sha256(FLAG.encode()).hexdigest(),
                    assets=[], assets_dir="tasks/bare", suggested_tools=[], scope="core")
        r = verify.evaluate(bare, FLAG, INT_SCRIPT, self.tmp, timeout_s=20)
        self.assertEqual(r.verdict, "verified")

    def test_tagger_files_it_under_hardcoded(self):
        rec = {"run_id": "x-s0", "verdict": "unverified", "reason": "hardcoded_no_material",
               "turns": 5, "invalids": 0, "run_dir": "",
               "replay": {"verdict": "unverified", "reason": "hardcoded_no_material",
                          "detail": "清空 challenge/ 后仍打印出同一个 flag"}}
        c = tagger.classify_run(None, rec)
        self.assertEqual(c["primary"], "hardcoded")


class TestRunScriptAction(Base):
    def test_registered_only_in_structured_mode(self):
        self.assertIn("run_script", tools.actions_for(True))
        self.assertNotIn("run_script", tools.actions_for(False))

    def test_requires_script_field(self):
        with self.assertRaises(ValueError):
            tools.Action.from_obj({"action": "run_script"}, tools.actions_for(True))

    def test_dispatch_returns_note_for_the_model(self):
        sb = Sandbox(self.tmp / "work", limits=SandboxLimits(action_timeout_s=10))
        (self.tmp / "work").mkdir(parents=True, exist_ok=True)
        calls = []

        def runner(script):
            calls.append(script)
            return verify.DryRunResult(True, "printed", stdout=FLAG, exit_code=0)

        box = tools.ToolBox(sb, structured=True, dry_runner=runner)
        act = tools.Action.from_obj({"action": "run_script", "script": GOOD_SCRIPT},
                                    box.allowed)
        res = box.execute(act)
        self.assertEqual(calls, [GOOD_SCRIPT])
        self.assertTrue(res.ok)
        self.assertIn(FLAG, res.stdout)
        self.assertIn("submit", res.note)

    def test_without_runner_it_says_so(self):
        (self.tmp / "work").mkdir(parents=True, exist_ok=True)
        sb = Sandbox(self.tmp / "work", limits=SandboxLimits(action_timeout_s=10))
        box = tools.ToolBox(sb, structured=True, dry_runner=None)
        act = tools.Action.from_obj({"action": "run_script", "script": "print(1)"},
                                    box.allowed)
        res = box.execute(act)
        self.assertFalse(res.ok)
        self.assertIn("自检不可用", res.stderr)


class ScriptedClient:
    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.seen: list[list[dict]] = []

    def complete(self, messages, max_tokens=None):
        self.seen.append([dict(m) for m in messages])
        text = self.replies.pop(0) if self.replies else \
            '{"thought":"idle","action":"shell","cmd":"echo idle"}'
        return Completion(text=text, prompt_tokens=10, completion_tokens=5,
                          ttft_ms=1, latency_ms=2)

    def scrape_metrics(self):
        return ServerMetrics()


def submit(flag: str, script: str) -> str:
    return json.dumps({"thought": "submit", "action": "submit",
                       "flag": flag, "script": script})


class TestRepairBudget(Base):
    """最后一轮才提交时，修复额度还发得出去吗。"""

    def run_attempt(self, replies, max_turns, repair_extends_budget=True):
        cfg = RunConfig(
            model=ModelSpec(name="mock"),
            budget=Budget(max_turns=max_turns, max_wall_s=120),
            limits=SandboxLimits(action_timeout_s=10),
            replay_timeout_s=20.0,
            tasks_root=self.tmp,
            repair_extends_budget=repair_extends_budget,
        )
        client = ScriptedClient(replies)
        original = agent.build_client
        agent.build_client = lambda spec: client
        try:
            return agent.run_attempt(self.task, cfg, self.tmp, seed=0), client
        finally:
            agent.build_client = original

    def test_submit_on_last_turn_still_gets_one_repair(self):
        # 第 2 轮（= 最后一轮，max_turns=2）提交一个崩掉的脚本，
        # 第 3 轮改成能跑通的脚本 —— 这一轮是靠修复额度借来的
        replies = [
            '{"thought":"a","action":"shell","cmd":"echo hi"}',
            submit(FLAG, CRASH_SCRIPT),
            submit(FLAG, GOOD_SCRIPT),
        ]
        summary, _ = self.run_attempt(replies, max_turns=2)
        self.assertEqual(summary["verdict"], "verified")
        self.assertEqual(summary["script_repairs"], 1)
        self.assertEqual(summary["bonus_turns"], 1)
        self.assertEqual(summary["turns"], 3)          # 真实调用次数
        self.assertEqual(summary["turns"] - summary["bonus_turns"], 2)  # 预算内

    def test_old_behaviour_when_disabled(self):
        replies = [
            '{"thought":"a","action":"shell","cmd":"echo hi"}',
            submit(FLAG, CRASH_SCRIPT),
            submit(FLAG, GOOD_SCRIPT),
        ]
        summary, _ = self.run_attempt(replies, max_turns=2, repair_extends_budget=False)
        self.assertEqual(summary["verdict"], "unverified")
        self.assertEqual(summary["reason"], "script_error")
        self.assertEqual(summary["script_repairs"], 0)
        self.assertEqual(summary["bonus_turns"], 0)

    def test_repair_still_capped_by_budget(self):
        """借轮次不等于无限重试：额度用完就必须收手。"""
        replies = [
            submit(FLAG, CRASH_SCRIPT),
            submit(FLAG, CRASH_SCRIPT),
            submit(FLAG, CRASH_SCRIPT),
            submit(FLAG, CRASH_SCRIPT),
        ]
        summary, _ = self.run_attempt(replies, max_turns=1)
        self.assertEqual(summary["script_repairs"], 1)
        self.assertLessEqual(summary["bonus_turns"], 2)

    def test_config_reaches_the_trace(self):
        summary, _ = self.run_attempt(['{"thought":"a","action":"shell","cmd":"echo hi"}'],
                                      max_turns=2)
        trace = Path(summary["run_dir"]) / "trace.jsonl"
        meta = json.loads(trace.read_text(encoding="utf-8").splitlines()[0])
        self.assertTrue(meta["self_check"])
        self.assertTrue(meta["material_free"])
        self.assertTrue(meta["repair_extends_budget"])


class TestOfflineReplay(Base):
    """交付给助教时最常用的一条路：给一个 runs 目录，就能看到每次运行。"""

    def _fake_run(self, name: str, verdict: str) -> Path:
        d = self.tmp / "runs" / name
        d.mkdir(parents=True)
        rows = [
            {"type": "meta", "task": name, "model": {"name": "m"}},
            {"type": "model", "turn": 1, "prompt_tokens": 1, "completion_tokens": 1},
            {"type": "summary", "verdict": verdict, "reason": "match", "turns": 1},
        ]
        (d / "trace.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        return d

    def test_mounts_only_finished_runs(self):
        from app.server import STATE, _mount_replay

        self._fake_run("a-001-s0", "verified")
        unfinished = self.tmp / "runs" / "b-002-s0"
        unfinished.mkdir()
        (unfinished / "trace.jsonl").write_text(
            json.dumps({"type": "meta", "task": "b"}) + "\n", encoding="utf-8")

        mounted = _mount_replay(str(self.tmp / "runs"))
        self.assertEqual(len(mounted), 1, "没跑完的 run 不该被挂上去")
        rid, name, verdict = mounted[0]
        self.assertEqual(name, "a-001-s0")
        self.assertEqual(verdict, "verified/match")
        # id 必须能过前端的参数校验，否则点开就是 400
        self.assertRegex(rid, r"^[0-9a-f]{12}$")
        self.assertIn(rid, STATE)
        self.assertEqual(STATE[rid]["summary"]["verdict"], "verified")

    def test_missing_dir_is_not_fatal(self):
        from app.server import _mount_replay
        self.assertEqual(_mount_replay(str(self.tmp / "nope")), [])

    def test_show_renders_a_timeline(self):
        import subprocess
        d = self._fake_run("c-003-s0", "verified")
        out = subprocess.run(
            [sys.executable, "-m", "flagpilot", "show", "--run-dir", str(d)],
            capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("verdict=verified", out.stdout)
        self.assertIn("[ 1] model", out.stdout)


if __name__ == "__main__":
    unittest.main()
