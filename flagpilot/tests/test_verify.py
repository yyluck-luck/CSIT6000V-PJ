"""重放验证器的单元测试。用合成题目，不依赖真实题库。"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flagpilot import verify  # noqa: E402
from flagpilot.config import SandboxLimits  # noqa: E402
from flagpilot.tasks import Task  # noqa: E402

FLAG = "picoCTF{synthetic_test_flag_0123456789}"


def make_task(root: Path) -> Task:
    d = root / "tasks" / "synthetic-999"
    d.mkdir(parents=True, exist_ok=True)
    import base64
    enc = base64.b64encode(FLAG.encode()).decode()
    (d / "data.txt").write_text(f"header line\nencoded={enc}\ntrailer\n")
    (d / "decoy.txt").write_text("nothing useful here\n")
    return Task(
        task_id=999,
        name="synthetic-999",
        category="Synthetic",
        title="synthetic",
        query="synthetic",
        gold_sha256=hashlib.sha256(FLAG.encode()).hexdigest(),
        assets=["data.txt"],
        assets_dir="tasks/synthetic-999",
        suggested_tools=[],
        scope="core",
    )


class TestFlagHashing(unittest.TestCase):
    def test_strip_and_match(self):
        self.assertTrue(verify.flag_matches(FLAG + "\n", hashlib.sha256(FLAG.encode()).hexdigest()))

    def test_empty_never_passes(self):
        self.assertFalse(verify.flag_matches("", hashlib.sha256(b"").hexdigest()))

    def test_case_sensitive(self):
        self.assertFalse(verify.flag_matches(FLAG.upper(), hashlib.sha256(FLAG.encode()).hexdigest()))


class TestLiteralScan(unittest.TestCase):
    def test_exact(self):
        self.assertEqual(verify.scan_script(f'print("{FLAG}")', FLAG), "exact")

    def test_hex(self):
        self.assertEqual(verify.scan_script(f'print(bytes.fromhex("{FLAG.encode().hex()}"))', FLAG), "hex")

    def test_reversed(self):
        self.assertEqual(verify.scan_script(f'print("{FLAG[::-1]}"[::-1])', FLAG), "reversed")

    def test_material_exemption(self):
        """ROT13 题的密文 = flag 的 rot13 形态。引用题目输入不是作弊。"""
        import codecs
        ct = codecs.encode(FLAG, "rot13")
        script = f'ct = "{ct}"\nprint(codecs.decode(ct, "rot13"))\n'
        self.assertIsNotNone(verify.scan_script(script, FLAG), "无材料时应当命中")
        self.assertIsNone(verify.scan_script(script, FLAG, material=f"hint: {ct}"), "材料里有则应豁免")

    def test_clean_script_passes(self):
        self.assertIsNone(verify.scan_script("print(open('challenge/data.txt').read())", FLAG))


class TestEvaluate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.task = make_task(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def ev(self, flag, script, timeout=20.0):
        return verify.evaluate(
            self.task, flag, script, self.root, timeout_s=timeout,
            limits=SandboxLimits(action_timeout_s=timeout),
        )

    def test_wrong_flag(self):
        r = self.ev("picoCTF{nope}", "print('x')")
        self.assertEqual((r.verdict, r.reason), ("wrong", "wrong_flag"))

    def test_hardcoded_flag_is_not_verified(self):
        r = self.ev(FLAG, f'print("{FLAG}")')
        self.assertEqual((r.verdict, r.reason), ("unverified", "literal_flag"))

    def test_legitimate_reproduction(self):
        script = (
            "import base64\n"
            "for line in open('challenge/data.txt'):\n"
            "    if 'encoded=' in line:\n"
            "        print(base64.b64decode(line.split('=')[1]).decode())\n"
        )
        r = self.ev(FLAG, script)
        self.assertEqual((r.verdict, r.reason), ("verified", "match"))

    def test_script_reads_wrong_file(self):
        r = self.ev(FLAG, 'print(open("challenge/decoy.txt").read())\n')
        self.assertEqual(r.verdict, "unverified")

    def test_missing_script(self):
        r = self.ev(FLAG, "")
        self.assertEqual((r.verdict, r.reason), ("unverified", "no_output"))

    def test_timeout(self):
        r = self.ev(FLAG, "import time\ntime.sleep(30)\nprint('picoCTF{x}')\n", timeout=1.0)
        self.assertEqual((r.verdict, r.reason), ("unverified", "timeout"))

    def test_script_error_no_output(self):
        r = self.ev(FLAG, "raise SystemExit(1)\n")
        self.assertEqual(r.verdict, "unverified")

    def test_crash_is_script_error_not_no_output(self):
        """脚本抛异常（stderr 有 traceback）应记 script_error，不是 no_output。

        现场来源：27B 拿 crypto-005 时 flag 算对了，但 solve.py 假设
        challenge/rot13.py 里存着密文（实际密文在题面里），重放时
        AttributeError。把这种崩掉记成"没输出"，会让失败分布失真。
        """
        r = self.ev(FLAG, "x = None\nprint(x.group(1))\n")
        self.assertEqual((r.verdict, r.reason), ("unverified", "script_error"))
        self.assertIn("AttributeError", r.detail)

    def test_silent_success_is_no_output(self):
        r = self.ev(FLAG, "value = 1\n")
        self.assertEqual((r.verdict, r.reason), ("unverified", "no_output"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
