"""沙箱与动作解析的测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flagpilot.config import Policy, SandboxLimits  # noqa: E402
from flagpilot.sandbox import Sandbox, probe_capabilities  # noqa: E402
from flagpilot.tasks import Task  # noqa: E402
from flagpilot.tools import parse_action  # noqa: E402
from flagpilot.agent import truncate  # noqa: E402


class TestSandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        d = self.root / "tasks" / "t-1"
        d.mkdir(parents=True)
        (d / "a.txt").write_text("hello\n")
        self.task = Task(1, "t-1", "Synthetic", "t", "t", "0" * 64, ["a.txt"], "tasks/t-1", [], "core")
        self.sb = Sandbox(self.root / "work", limits=SandboxLimits(action_timeout_s=5),
                          caps=probe_capabilities(), tasks_root=self.root)

    def tearDown(self):
        self.sb.cleanup()
        self.tmp.cleanup()

    def test_stage_and_read(self):
        self.sb.stage(self.task)
        r = self.sb.run_shell("cat challenge/a.txt")
        self.assertEqual(r.exit_code, 0)
        self.assertIn("hello", r.stdout)

    def test_challenge_is_readonly(self):
        self.sb.stage(self.task)
        if self.sb.challenge_readonly is False:
            # netns 隔离下进程是命名空间内的 root（unshare -r），带
            # CAP_DAC_OVERRIDE，权限位挡不住写入。这不是代码缺陷，是那条隔离
            # 路径的固有性质；TACC 上就是这样。如实跳过，而不是假装通过。
            self.skipTest("sandbox runs as namespace root; permission bits do not block writes")
        r = self.sb.run_shell("echo x > challenge/a.txt")
        self.assertNotEqual(r.exit_code, 0)

    def test_readonly_probe_matches_actual_behaviour(self):
        """探测结论必须和真实写入结果一致，否则跳过条件就是自欺欺人。"""
        self.sb.stage(self.task)
        r = self.sb.run_shell("echo x > challenge/a.txt")
        self.assertEqual(self.sb.challenge_readonly, r.exit_code != 0)

    def test_workdir_is_writable(self):
        self.sb.stage(self.task)
        r = self.sb.run_shell("echo scratch > notes.txt && cat notes.txt")
        self.assertEqual(r.exit_code, 0)
        self.assertIn("scratch", r.stdout)

    def test_timeout_kills(self):
        self.sb.stage(self.task)
        r = self.sb.run_shell("sleep 5", timeout=0.5)
        self.assertTrue(r.timed_out)

    def test_python_action(self):
        self.sb.stage(self.task)
        r = self.sb.run_python("print(6*7)")
        self.assertIn("42", r.stdout)

    def test_file_size_limit_actually_applies(self):
        """回归：曾因 RLIMIT_AS 在 macOS 抛错，把后面的 FSIZE 一起吞掉。

        表现是"1MB 上限"的沙箱能写出 5MB 文件，而且不报错——限额形同虚设。
        """
        sb = Sandbox(self.root / "work2",
                     limits=SandboxLimits(action_timeout_s=10, file_size_mb=1),
                     caps=probe_capabilities())
        r = sb.run_shell("head -c 5000000 /dev/zero > big.bin")
        self.assertNotEqual(r.exit_code, 0, "超过单文件上限必须失败")
        size = sb.run_shell("wc -c < big.bin 2>/dev/null || echo 0").stdout.strip()
        self.assertLessEqual(int(size or 0), 1024 * 1024)
        sb.cleanup()

    def test_nofile_limit_applies(self):
        """NOFILE 是唯一跨平台都能设的项，顺带验证 preexec_fn 真的进了子进程。"""
        r = self.sb.run_python(
            "import resource;print(resource.getrlimit(resource.RLIMIT_NOFILE)[0])")
        self.assertIn("512", r.stdout)

    def test_symlink_escape_is_blocked(self):
        """回归：路径守卫曾用 startswith 比字符串，/work2 会被当成 /work 的子路径。"""
        from flagpilot.tools import ToolBox

        outside = self.root / "work2" / "secret.txt"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_text("SECRET", encoding="utf-8")
        (self.sb.workdir / "link.txt").symlink_to(outside)

        tb = ToolBox(self.sb)
        with self.assertRaises(ValueError):
            tb._resolve("link.txt")


class TestParseAction(unittest.TestCase):
    def test_plain_json(self):
        a, err = parse_action('{"thought":"t","action":"shell","cmd":"ls"}')
        self.assertIsNone(err)
        self.assertEqual(a.action, "shell")

    def test_fenced_json(self):
        a, err = parse_action('```json\n{"action":"python","code":"print(1)"}\n```')
        self.assertIsNone(err)
        self.assertEqual(a.action, "python")

    def test_json_with_prose_around(self):
        a, err = parse_action('I will look around.\n{"action":"shell","cmd":"ls"}\nDone.')
        self.assertIsNone(err)
        self.assertEqual(a.cmd, "ls")

    def test_missing_field(self):
        a, err = parse_action('{"action":"shell"}')
        self.assertIsNone(a)
        self.assertIn("cmd", err)

    def test_bad_action(self):
        a, err = parse_action('{"action":"rm_rf","cmd":"x"}')
        self.assertIsNone(a)
        self.assertIn("action", err)

    def test_thinking_block_is_stripped(self):
        raw = '让我想想...\n<think>先看目录，再决定用什么工具</think>\n{"action":"shell","cmd":"ls"}'
        a, err = parse_action(raw)
        self.assertIsNone(err)
        self.assertEqual(a.cmd, "ls")

    def test_truncated_json(self):
        a, err = parse_action('{"action":"shell","cmd":"ls"')
        self.assertIsNone(a)

    def test_submit_requires_script(self):
        a, err = parse_action('{"action":"submit","flag":"picoCTF{x}"}')
        self.assertIsNone(a)
        self.assertIn("script", err)


class TestTruncate(unittest.TestCase):
    def test_short_passthrough(self):
        out, cut = truncate("abc", Policy())
        self.assertEqual(out, "abc")
        self.assertFalse(cut)

    def test_head_tail(self):
        text = "H" * 3000 + "M" * 5000 + "T" * 3000
        out, cut = truncate(text, Policy(truncate_head_chars=1000, truncate_tail_chars=1000))
        self.assertTrue(cut)
        self.assertTrue(out.startswith("H" * 1000))
        self.assertTrue(out.endswith("T" * 1000))
        self.assertIn("truncated", out)

    def test_no_truncate_policy(self):
        text = "x" * 20000
        out, cut = truncate(text, Policy(truncate_head_chars=None, hard_cap_chars=5000))
        self.assertTrue(cut)
        self.assertEqual(len(out), 5000)

    def test_deterministic(self):
        text = "H" * 3000 + "T" * 3000
        p = Policy()
        self.assertEqual(truncate(text, p)[0], truncate(text, p)[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
