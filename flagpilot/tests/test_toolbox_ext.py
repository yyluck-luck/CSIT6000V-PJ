"""SMT 与反汇编动作的测试。

proposal 的 toolbox 承诺里有 "an SMT solver, binutils, a disassembler"。
这里验证的是：动作被正确注册、参数校验到位、缺依赖时给出可读的失败而不是崩。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flagpilot import tools  # noqa: E402
from flagpilot.config import SandboxLimits  # noqa: E402
from flagpilot.sandbox import Sandbox, probe_capabilities  # noqa: E402
from flagpilot.tools import Action, ToolBox, actions_for, parse_action  # noqa: E402


class TestActionSet(unittest.TestCase):
    def test_new_actions_registered(self):
        self.assertIn("smt", actions_for(True))
        self.assertIn("disasm", actions_for(True))
        # 基础档不能被偷偷放宽
        self.assertNotIn("smt", actions_for(False))
        self.assertNotIn("disasm", actions_for(False))

    def test_smt_requires_code(self):
        act, err = parse_action('{"action":"smt"}', actions_for(True))
        self.assertIsNone(act)
        self.assertIn("code", err)

    def test_disasm_requires_path(self):
        act, err = parse_action('{"action":"disasm"}', actions_for(True))
        self.assertIsNone(act)
        self.assertIn("path", err)

    def test_disasm_accepts_section(self):
        act, err = parse_action(
            '{"action":"disasm","path":"challenge/a.out","text":".text"}',
            actions_for(True))
        self.assertIsNone(err)
        self.assertEqual(act.text, ".text")


class ToolboxCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-toolbox-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.sb = Sandbox(self.tmp / "work", limits=SandboxLimits(action_timeout_s=10),
                          caps=probe_capabilities(), tasks_root=self.tmp)
        self.ch = self.sb.workdir / "challenge"
        self.ch.mkdir(parents=True, exist_ok=True)
        self.tb = ToolBox(self.sb, structured=True)


class TestSmt(ToolboxCase):
    def test_missing_z3_fails_with_guidance(self):
        with mock.patch.object(tools, "z3_available", return_value=False):
            r = self.tb.execute(Action(thought="t", action="smt", code="x = Int('x')"))
        self.assertFalse(r.ok)
        self.assertIn("z3", r.stderr)
        self.assertIn("python", r.stderr)   # 给了替代路径

    def test_import_is_injected(self):
        captured = {}

        def fake_python(code, timeout=None):
            captured["code"] = code
            return mock.Mock(ok=True, stdout="sat", stderr="", exit_code=0,
                             timed_out=False, duration_ms=1, cmd="")

        with mock.patch.object(tools, "z3_available", return_value=True), \
                mock.patch.object(self.sb, "run_python", side_effect=fake_python):
            self.tb.execute(Action(thought="t", action="smt", code="x = Int('x')"))
        self.assertTrue(captured["code"].startswith("from z3 import *\n"))


class TestDisasm(ToolboxCase):
    def test_missing_binary_fails_with_guidance(self):
        (self.ch / "blob.bin").write_bytes(b"\x7fELF" + b"\x00" * 64)
        with mock.patch.object(tools, "disasm_binary", return_value=None):
            r = self.tb.execute(Action(thought="t", action="disasm", path="challenge/blob.bin"))
        self.assertFalse(r.ok)
        self.assertIn("objdump", r.stderr)

    def test_runs_on_real_executable(self):
        src = shutil.which("objdump") or shutil.which("ls")
        self.assertIsNotNone(src)
        shutil.copyfile(src, self.ch / "target")
        r = self.tb.execute(Action(thought="t", action="disasm", path="challenge/target"))
        self.assertTrue(r.ok, r.stderr[:300])
        self.assertGreater(len(r.stdout), 200, "反汇编输出不该是空的")

    def test_path_escape_blocked(self):
        r = self.tb.execute(Action(thought="t", action="disasm", path="/bin/ls"))
        self.assertFalse(r.ok)
        self.assertIn("相对路径", r.stderr)

    def test_output_is_truncated(self):
        """上万行的反汇编必须截断，否则一轮就把上下文撑爆。"""
        src = shutil.which("objdump") or shutil.which("ls")
        shutil.copyfile(src, self.ch / "target")
        r = self.tb.execute(Action(thought="t", action="disasm", path="challenge/target"))
        self.assertLessEqual(len(r.stdout.splitlines()), tools._DISASM_MAX_LINES + 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
