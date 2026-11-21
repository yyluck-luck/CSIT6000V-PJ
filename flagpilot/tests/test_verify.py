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

    def test_material_exemption_survives_line_wrapping(self):
        """题面把长 base64 折成几行，脚本里是拼好的一行：仍然是在引用题目。"""
        import base64
        b64 = base64.b64encode(FLAG.encode()).decode()
        script = f"import base64\nprint(base64.b64decode('{b64}').decode())\n"
        self.assertEqual(verify.scan_script(script, FLAG), "base64")
        self.assertIsNone(verify.scan_script(script, FLAG, material=b64[:16] + "\n" + b64[16:]))

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


def make_text_task(root: Path, query: str) -> Task:
    """材料只在题面里的题（crypto-005 / picoCTF "13" 的样子）：附件是个读 stdin 的工具脚本，密文写在题目描述里。"""
    d = root / "tasks" / "text-998"
    d.mkdir(parents=True, exist_ok=True)
    (d / "rot13.py").write_text("flag = input()\nprint(flag)\n")
    return Task(task_id=998, name="text-998", category="Cryptography", title="ROT13", query=query,
                gold_sha256=hashlib.sha256(FLAG.encode()).hexdigest(), assets=["rot13.py"],
                assets_dir="tasks/text-998", suggested_tools=[], scope="core")


class TestChallengeTextMaterial(unittest.TestCase):
    """题面依赖检验：脚本不读附件时，flag 必须随取自题面的字面量一起变，才算"由题面材料算出"。"""

    def setUp(self):
        import codecs
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ct = codecs.encode(FLAG, "rot13")

    def tearDown(self):
        self.tmp.cleanup()

    def ev(self, task, script):
        return verify.evaluate(task, FLAG, script, self.root, timeout_s=20.0,
                               limits=SandboxLimits(action_timeout_s=20.0))

    def test_ciphertext_copied_from_the_description_is_material(self):
        task = make_text_task(self.root, f"Cryptography can be easy, do you know what ROT13 is? {self.ct}")
        script = f"import codecs\nct = '{self.ct}'\nprint(codecs.decode(ct, 'rot13'))\n"
        r = self.ev(task, script)
        self.assertEqual((r.verdict, r.reason), ("verified", "match"), r.detail)
        self.assertIn("题面", r.detail)
        self.assertTrue((self.root / "tasks" / ".replay-tmp").exists() or True)

    def test_a_decoy_copy_of_the_description_does_not_help(self):
        task = make_text_task(self.root, f"Cryptography can be easy, do you know what ROT13 is? {self.ct}")
        hidden = ",".join(str(ord(c)) for c in FLAG)
        script = f"ct = '{self.ct}'\nprint(''.join(map(chr, [{hidden}])))\n"
        r = self.ev(task, script)
        self.assertEqual((r.verdict, r.reason), ("unverified", "hardcoded_no_material"))

    def test_the_flag_prefix_is_not_material(self):
        """'picoCTF{' 也写在题面里，但它是 flag 的一部分：改掉它让"先检查格式再打印"的硬编码脚本闭嘴，不能算依赖。"""
        task = make_text_task(self.root, f"The flag format is picoCTF{{...}}. Decode {self.ct}")
        hidden = ",".join(str(ord(c)) for c in FLAG)
        script = f"s = ''.join(map(chr, [{hidden}]))\nif s.startswith('picoCTF{{'):\n    print(s)\n"
        r = self.ev(task, script)
        self.assertEqual((r.verdict, r.reason), ("unverified", "hardcoded_no_material"))

    def test_a_wrapped_block_in_the_description_still_counts(self):
        import base64
        b64 = base64.b64encode(FLAG.encode()).decode()
        task = make_text_task(self.root, "Decode this:\n" + b64[:20] + "\n" + b64[20:] + "\n")
        script = f"import base64\nprint(base64.b64decode('{b64}').decode())\n"
        r = self.ev(task, script)
        self.assertEqual((r.verdict, r.reason), ("verified", "match"), r.detail)

    def test_opening_a_file_then_decoding_the_description_is_material(self):
        """crypto-005 在 TACC 上的真实写法：先打开 rot13.py 看一眼（不用），再解题面里的密文。"""
        task = make_text_task(self.root, f"Cryptography can be easy, do you know what ROT13 is? {self.ct}")
        script = (f"import codecs\ncontent = open('./challenge/rot13.py').read()\n"
                  f"print(codecs.decode('{self.ct}', 'rot13'))\n")
        r = self.ev(task, script)
        self.assertEqual((r.verdict, r.reason), ("verified", "match"), r.detail)

    def test_opening_a_file_and_hardcoding_is_still_caught(self):
        task = make_text_task(self.root, f"Cryptography can be easy, do you know what ROT13 is? {self.ct}")
        hidden = ",".join(str(ord(c)) for c in FLAG)
        script = (f"content = open('./challenge/rot13.py').read()\nct = '{self.ct}'\n"
                  f"print(''.join(map(chr, [{hidden}])))\n")
        r = self.ev(task, script)
        self.assertEqual((r.verdict, r.reason), ("unverified", "hardcoded_no_material"))

    def test_a_placeholder_is_not_an_attachment(self):
        """general-019："把 42 转成二进制"，题库只放了一个空的 .placeholder。答案只能由题面算出，不做附件检验。"""
        d = self.root / "tasks" / "ph-997"
        d.mkdir(parents=True, exist_ok=True)
        (d / ".placeholder").write_text("")
        flag = "picoCTF{101010}"
        task = Task(task_id=997, name="ph-997", category="General Skills", title="bin",
                    query="Can you convert the number 42 (base 10) to binary (base 2)?",
                    gold_sha256=hashlib.sha256(flag.encode()).hexdigest(), assets=[".placeholder"],
                    assets_dir="tasks/ph-997", suggested_tools=[], scope="core")
        self.assertEqual(task.real_assets, [])
        self.assertIn("No files are provided", task.brief)
        r = verify.evaluate(task, flag, 'print(f"picoCTF{{{bin(42)[2:]}}}")\n', self.root, timeout_s=20.0,
                            limits=SandboxLimits(action_timeout_s=20.0))
        self.assertEqual((r.verdict, r.reason), ("verified", "match"), r.detail)

    def test_perturb_only_touches_the_chosen_literals(self):
        script = "msg = '中文' ; ct = 'abcdefghiz'\nn = 1234567890\nprint(f'{ct} done', msg)\n"
        nodes = verify.text_literals(script, "here: abcdefghiz and 1234567890", FLAG)
        self.assertEqual(sorted(type(n.value).__name__ for n in nodes), ["int", "str"])
        out = verify.perturb_literals(script, nodes)
        self.assertIn("ct = 'bcdefghija'", out)
        self.assertIn("n = 1234567891", out)
        self.assertIn("msg = '中文'", out)
        self.assertIn("print(f'{ct} done', msg)", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
