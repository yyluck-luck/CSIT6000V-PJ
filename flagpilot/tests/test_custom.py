"""自建题目（上传）的单元测试。

重点不是"能创建"，而是三条边界：
  1. zip 解压不能穿越目录；
  2. 没有 gold 时**永远**判不出 verified；
  3. 自建题不能混进评测统计（metrics 里单列 reproduced）。
"""

from __future__ import annotations

import base64
import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from flagpilot import custom, metrics, verify
from flagpilot.config import SandboxLimits

FLAG = "picoCTF{uploaded_task_flag_9876}"


class TestCreate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-custom-"))
        self.store = self.tmp / "custom"

    def tearDown(self):
        for p in self.tmp.rglob("*"):
            try:
                p.chmod(0o755)
            except Exception:
                pass
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_and_list(self):
        t = custom.create(
            title="测试题",
            query="附件里有一个 base64，解出来就是 flag。",
            category="crypto",
            files=[("note.txt", b"encoded=" + base64.b64encode(FLAG.encode())), ("a.bin", b"\x00\x01")],
            root=self.store,
        )
        self.assertTrue(t.name.startswith("custom-"))
        self.assertFalse(t.has_gold)
        self.assertEqual(len(t.assets), 2)
        self.assertIn("附件里有一个 base64", t.brief)
        self.assertIn("Files provided", t.brief)
        self.assertEqual(len(custom.load(self.store)), 1)
        self.assertEqual(custom.get(t.name, self.store).name, t.name)

    def test_gold_hash_matches(self):
        t = custom.create(title="带答案", query="x", gold=FLAG, root=self.store)
        self.assertTrue(t.has_gold)
        self.assertEqual(t.gold_sha256, hashlib.sha256(FLAG.encode()).hexdigest())

    def test_requires_something(self):
        with self.assertRaises(ValueError):
            custom.create(title="", query="", files=[], root=self.store)

    def test_filename_sanitized(self):
        t = custom.create(title="n", query="q",
                          files=[("../../evil name!.txt", b"x")], root=self.store)
        self.assertEqual(t.assets, ["evil_name_.txt"])
        # 文件必须落在 challenge/ 里，而不是往上跑两级
        self.assertTrue((Path(t.assets_dir) / "evil_name_.txt").exists())

    def test_duplicate_names_get_suffix(self):
        t = custom.create(title="n", query="q",
                          files=[("a.txt", b"1"), ("a.txt", b"2")], root=self.store)
        self.assertEqual(sorted(t.assets), ["a-2.txt", "a.txt"])

    def test_zip_is_extracted(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("inner/hello.txt", "hi")
        t = custom.create(title="zip", query="q",
                          files=[("pack.zip", buf.getvalue())], root=self.store)
        self.assertIn("pack.zip", t.assets)
        self.assertIn("inner/hello.txt", t.assets)

    def test_zip_traversal_rejected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../escaped.txt", "boom")
        with self.assertRaises(ValueError):
            custom.create(title="zip", query="q",
                          files=[("pack.zip", buf.getvalue())], root=self.store)

    def test_delete(self):
        t = custom.create(title="d", query="q", files=[("a.txt", b"x")], root=self.store)
        self.assertTrue(custom.delete(t.name, self.store))
        self.assertEqual(custom.load(self.store), [])
        # 只允许删 store 下的目录
        self.assertFalse(custom.delete("../x", self.store))


class TestNoGold(unittest.TestCase):
    """没有 gold 的题目，判据必须降级，而不是"宽松通过"。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-nogold-"))
        enc = base64.b64encode(FLAG.encode()).decode()
        self.task = custom.create(
            title="自建无答案",
            query="解 base64",
            files=[("data.txt", f"encoded={enc}\n".encode())],
            root=self.tmp / "custom",
        )

    def tearDown(self):
        import shutil

        for p in self.tmp.rglob("*"):
            try:
                p.chmod(0o755)
            except Exception:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reproduced_not_verified(self):
        script = (
            "import base64\n"
            "for line in open('challenge/data.txt'):\n"
            "    if 'encoded=' in line:\n"
            "        print(base64.b64decode(line.split('=', 1)[1]).decode())\n"
        )
        r = verify.evaluate(self.task, FLAG, script, self.tmp,
                            limits=SandboxLimits(action_timeout_s=15))
        self.assertEqual(r.verdict, "reproduced")
        self.assertEqual(r.reason, "reproduced_no_gold")
        self.assertFalse(r.gold_checked)
        self.assertFalse(r.ok, "reproduced 不能算 ok")

    def test_wrong_answer_still_unverified(self):
        r = verify.evaluate(self.task, "picoCTF{not_it}", "print('nothing')", self.tmp,
                            limits=SandboxLimits(action_timeout_s=15))
        self.assertNotEqual(r.verdict, "verified")
        self.assertNotEqual(r.verdict, "reproduced")

    def test_metrics_does_not_count_reproduced_as_raw(self):
        s = metrics.summarize([
            {"verdict": "reproduced", "category": "custom", "replay": {"reason": "reproduced_no_gold"}},
            {"verdict": "verified", "category": "crypto", "replay": {"reason": "match"}},
        ], with_tags=True)
        self.assertEqual(s["n"], 2)
        self.assertEqual(s["verified"], 1)
        self.assertEqual(s["raw"], 1, "reproduced 不得计入 raw")
        self.assertEqual(s["reproduced"], 1)


if __name__ == "__main__":
    unittest.main()
