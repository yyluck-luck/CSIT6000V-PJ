"""导入辅助：AI 起草题面（模型不见答案，草稿里像 flag 的东西会被抹掉），模型不可用时退回规则草稿。"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import drafting  # noqa: E402
from flagpilot import custom  # noqa: E402
from flagpilot.client import Completion  # noqa: E402


class Reply:
    def __init__(self, text="", error="", boom=False):
        self.text, self.error, self.boom, self.seen = text, error, boom, []

    def complete(self, messages, max_tokens=None, on_delta=None):
        self.seen.append(messages)
        if self.boom:
            raise TimeoutError("slow")
        return Completion(text=self.text, prompt_tokens=1, completion_tokens=1, ttft_ms=1, latency_ms=1, error=self.error)


class TestDrafting(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="fp-draft-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.task = custom.create("sice_cream", "Challenge sice_cream from picoCTF 2019\n\n(Imported from x: ctfs/p/sice)\n\n"
                                  + custom.SERVICE_NOTE, files=[("sice_cream", b"\x7fELF" + b"\0" * 5000), ("libc.so.6", b"x" * 3000)],
                                  category="Binary Exploitation", gold="picoCTF{the_real_answer_42}", root=self.root)

    def test_scrub_keeps_format_hints_and_removes_flags(self):
        self.assertEqual(drafting.scrub("format picoCTF{...} answer picoCTF{abc_123}"), "format picoCTF{...} answer [flag removed]")

    def test_rules_draft_for_a_service_challenge(self):
        d = drafting.rules_draft(self.task)
        self.assertEqual((d["category"], d["offline"], d["by"]), ("pwn", "no", "rules"))
        self.assertIn("`sice_cream`", d["statement"])
        self.assertIn("cannot be solved offline", d["statement"])
        self.assertNotIn("Imported from", d["statement"])

    def test_the_model_never_sees_the_answer_and_its_flags_are_removed(self):
        fake = Reply('```json\n{"title": "Sice cream heap", "statement": "Heap challenge; the flag is picoCTF{guess_1}.", '
                     '"category": "PWN", "offline": "no", "why": "needs the server"}\n```')
        d = drafting.draft(self.task, fake, label="Gemini")
        prompt = "\n".join(m["content"] for m in fake.seen[0])
        self.assertNotIn("the_real_answer_42", prompt)
        self.assertNotIn(self.task.gold_sha256, prompt)
        self.assertEqual((d["title"], d["category"], d["offline"], d["by"]), ("Sice cream heap", "pwn", "no", "Gemini"))
        yes = drafting.draft(self.task, Reply('{"title": "t", "statement": "s", "category": "pwn", "offline": "yes", "why": "exploit locally"}'))
        self.assertEqual(yes["offline"], "no", "a service challenge's real flag is not in the files, whatever the model says")
        self.assertIn("[flag removed]", d["statement"])
        self.assertNotIn("guess_1", d["statement"])

    def test_falls_back_to_rules_when_the_model_fails(self):
        for fake in (Reply(error="HTTP 429"), Reply(text="no json here"), Reply(boom=True)):
            d = drafting.draft(self.task, fake)
            self.assertEqual(d["by"], "rules")
            self.assertIn("rule-based", d["note"])

    def test_files_decide_when_the_category_says_little(self):
        root = self.root / "cat"
        cases = {"capture.pcap": "forensics", "dump.pcapng": "forensics", "photo.JPG": "forensics", "app.php": "web", "crackme.exe": "reverse"}
        for fname, want in cases.items():
            t = custom.create("x", "raw text", files=[(fname, b"data")], category="misc", root=root)
            self.assertEqual(drafting.guess_category(t, drafting.files_of(t)), want, fname)
        t = custom.create("x", "raw", files=[("capture.pcap", b"d")], category="Cryptography", root=root)
        self.assertEqual(drafting.guess_category(t, drafting.files_of(t)), "crypto", "a specific category is kept")

    def test_parse_rejects_unusable_replies(self):
        self.assertIsNone(drafting.parse('{"title": "x"}'))
        self.assertEqual(drafting.parse('{"statement": "s", "category": "astronomy", "offline": "maybe"}')["category"], "misc")


class TestCustomEdit(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="fp-edit-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_edit_keeps_the_service_mark_and_tells_the_model(self):
        t = custom.create("old", "Challenge x\n\n" + custom.SERVICE_NOTE, files=[("a.bin", b"x")], root=self.root)
        self.assertTrue(t.service)
        t2 = custom.update(t.name, title="  New   title ", query="A rewritten statement.", category="pwn", root=self.root)
        self.assertEqual((t2.title, t2.query, t2.category, t2.service), ("New title", "A rewritten statement.", "pwn", True))
        self.assertIn("network service", t2.brief)
        with self.assertRaises(ValueError):
            custom.update(t.name, title="   ", root=self.root)
        with self.assertRaises(ValueError):
            custom.update("../../etc", title="x", root=self.root)


if __name__ == "__main__":
    unittest.main()
