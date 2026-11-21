"""app/linkfix.py: pasted challenge links that are a little wrong still import (GitHub is faked; nothing goes online)."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import catalog, linkfix
from flagpilot import config, custom

TREE = [
    {"path": "README.md", "type": "blob", "size": 30},
    {"path": "crypto", "type": "tree"},
    {"path": "crypto/Baby_RSA", "type": "tree"},
    {"path": "crypto/Baby_RSA/README.md", "type": "blob", "size": 60},
    {"path": "crypto/Baby_RSA/chall.py", "type": "blob", "size": 400},
    {"path": "crypto/Baby_RSA/output.txt", "type": "blob", "size": 900},
    {"path": "crypto/Baby_RSA/flag.txt", "type": "blob", "size": 30},
    {"path": "crypto/xor-easy", "type": "tree"},
    {"path": "crypto/xor-easy/enc.bin", "type": "blob", "size": 64},
    {"path": "pwn", "type": "tree"},
    {"path": "pwn/babyrop", "type": "tree"},
    {"path": "pwn/babyrop/babyrop", "type": "blob", "size": 9000},
    {"path": "pwn/babyrop/README.md", "type": "blob", "size": 50},
]
SINGLE = [{"path": "chall.py", "type": "blob", "size": 100}, {"path": "out.txt", "type": "blob", "size": 50},
          {"path": "README.md", "type": "blob", "size": 40}]
RAW = {"crypto/Baby_RSA/README.md": b"# Baby RSA\nSmall e. Decrypt output.txt.", "crypto/Baby_RSA/chall.py": b"print('rsa')",
       "crypto/Baby_RSA/output.txt": b"n=1\nc=2\n", "crypto/Baby_RSA/flag.txt": b"acme{sm4ll_e_br0ken}\n"}


def gh_error(code):
    e = catalog.CatalogError(f"The server answered HTTP {code}")
    e.code = code
    return e


def fake_get(url, limit=None, accept=""):
    api = catalog.API
    if url == f"{api}/repos/acme/ctf2024" or url == f"{api}/repos/ACME/CTF2024":
        return json.dumps({"full_name": "acme/ctf2024", "default_branch": "main"}).encode()
    if url == f"{api}/repos/acme/single":
        return json.dumps({"full_name": "acme/single", "default_branch": "main"}).encode()
    if url == f"{api}/repos/acme/ctf2024/git/trees/main?recursive=1":
        return json.dumps({"tree": TREE, "truncated": False}).encode()
    if url == f"{api}/repos/acme/single/git/trees/main?recursive=1":
        return json.dumps({"tree": SINGLE, "truncated": False}).encode()
    if url.startswith(f"{catalog.RAW}/acme/ctf2024/main/"):
        rel = url[len(f"{catalog.RAW}/acme/ctf2024/main/"):]
        if rel in RAW:
            return RAW[rel]
    if url.startswith(f"{catalog.RAW}/acme/single/main/"):
        return b"x"
    raise gh_error(404)


class LinkCase(unittest.TestCase):
    def setUp(self):
        linkfix._CACHE.clear()
        p = mock.patch.object(catalog, "_get", side_effect=fake_get)
        p.start()
        self.addCleanup(p.stop)

    def resolve(self, text, ask=None):
        return linkfix.resolve(text, ask=ask)


class TestTidy(unittest.TestCase):
    def test_a_link_typed_or_copied_carelessly_is_tidied(self):
        url, notes = linkfix.tidy("  <github.com/acme/ctf2024.git/tree/main/crypto/Baby_RSA?tab=readme-ov-file#readme>. ")
        self.assertEqual(url, "https://github.com/acme/ctf2024/tree/main/crypto/Baby_RSA")
        self.assertEqual(notes, ["added https://", "dropped ?tab=readme-ov-file and #readme", "dropped .git"])
        url, notes = linkfix.tidy("http://www.github.com/acme/ctf2024")
        self.assertEqual(url, "https://github.com/acme/ctf2024")
        self.assertIn("used https", notes)
        self.assertEqual(linkfix.tidy("picoCTF 2019 caesar"), ("", []))

    def test_github_link_shapes(self):
        g = linkfix.parse_github
        self.assertEqual(g("https://github.com/a/b")["kind"], "root")
        self.assertEqual(g("https://github.com/a/b/tree/main/x/y")["rest"], ["x", "y"])
        self.assertEqual(g("https://github.com/a/b/blob/dev/x.py")["kind"], "blob")
        self.assertEqual(g("https://raw.githubusercontent.com/a/b/main/x/y.bin")["rest"], ["x", "y.bin"])
        self.assertEqual(g("https://github.com/a/b/issues/3")["kind"], "root")
        self.assertIsNone(g("https://github.com/a/b/releases/download/v1/chall.zip"))


class TestGithub(LinkCase):
    def test_a_branch_that_does_not_exist_becomes_the_default(self):
        r = self.resolve("https://github.com/acme/ctf2024/tree/master/crypto/Baby_RSA")
        self.assertEqual((r["kind"], r["ref"], r["dir"]), ("github", "main", "crypto/Baby_RSA"))
        self.assertIn("branch master does not exist; used main", r["notes"])
        self.assertEqual(sorted(f for f, _ in r["files"]), ["README.md", "chall.py", "flag.txt", "output.txt"])

    def test_wrong_case_punctuation_and_typos_find_the_folder(self):
        for path, why in (("Crypto/baby_rsa", "letter case"), ("crypto/baby-rsa", "crypto/Baby_RSA"), ("crypto/Baby_RSAA", "closest")):
            r = self.resolve(f"https://github.com/acme/ctf2024/tree/main/{path}")
            self.assertEqual(r["dir"], "crypto/Baby_RSA", path)
            self.assertTrue(any(why in n for n in r["notes"]), (path, r["notes"]))

    def test_only_the_challenge_name_is_enough(self):
        r = self.resolve("https://github.com/acme/ctf2024/tree/main/babyrop")
        self.assertEqual(r["dir"], "pwn/babyrop")
        self.assertIn("found babyrop at pwn/babyrop", r["notes"])

    def test_a_file_brings_its_whole_challenge_folder(self):
        r = self.resolve("https://raw.githubusercontent.com/acme/ctf2024/main/crypto/Baby_RSA/chall.py")
        self.assertEqual((r["kind"], r["dir"]), ("github", "crypto/Baby_RSA"))
        self.assertTrue(any("whole challenge folder crypto/Baby_RSA" in n for n in r["notes"]))

    def test_a_repository_or_folder_of_challenges_offers_a_choice(self):
        with self.assertRaises(linkfix.Choose) as c:
            self.resolve("github.com/acme/ctf2024")
        self.assertEqual([x["label"] for x in c.exception.choices], ["crypto/Baby_RSA", "crypto/xor-easy", "pwn/babyrop"])
        self.assertEqual(c.exception.choices[0]["url"], "https://github.com/acme/ctf2024/tree/main/crypto/Baby_RSA")
        with self.assertRaises(linkfix.Choose) as c:
            self.resolve("https://github.com/acme/ctf2024/tree/main/crypto")
        self.assertEqual(len(c.exception.choices), 2)
        # a choice imports as it is
        r = self.resolve(c.exception.choices[1]["url"])
        self.assertEqual(r["dir"], "crypto/xor-easy")

    def test_a_repository_that_is_one_challenge_imports_whole(self):
        r = self.resolve("https://github.com/acme/single")
        self.assertEqual((r["kind"], r["dir"]), ("github", ""))
        self.assertEqual(len(r["files"]), 3)

    def test_unknown_repositories_and_paths_say_so(self):
        with self.assertRaisesRegex(catalog.CatalogError, "no public GitHub repository nobody/none"):
            self.resolve("https://github.com/nobody/none")
        with self.assertRaisesRegex(catalog.CatalogError, "no file or folder like web/nothing-like-it"):
            self.resolve("https://github.com/acme/ctf2024/tree/main/web/nothing-like-it")

    def test_the_model_only_picks_among_real_paths(self):
        url = "https://github.com/acme/ctf2024/tree/main/the-xor-one"
        r = self.resolve(url, ask=lambda prompt: "crypto/xor-easy")
        self.assertEqual(r["dir"], "crypto/xor-easy")
        self.assertTrue(any("the model picked crypto/xor-easy" in n for n in r["notes"]))
        with self.assertRaises(catalog.CatalogError):
            self.resolve(url, ask=lambda prompt: "crypto/made-up")       # not a real path: ignored

    def test_an_import_reports_what_it_fixed(self):
        tmp = Path(tempfile.mkdtemp(prefix="fp-linkfix-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        saved = (config.RUNS_ROOT, custom.store_root)
        config.RUNS_ROOT = tmp / "runs"
        try:
            out = catalog.import_url("github.com/acme/ctf2024/tree/master/crypto/baby-rsa?tab=readme-ov-file")
        finally:
            config.RUNS_ROOT = saved[0]
        t = out["task"]
        self.assertEqual(t.title, "Baby RSA")
        self.assertTrue(out["gold"], "flag.txt became the gold flag")
        self.assertEqual(sorted(t.assets), ["chall.py", "output.txt"])
        self.assertEqual(out["source"], "https://github.com/acme/ctf2024/tree/main/crypto/Baby_RSA")
        self.assertIn("branch master does not exist; used main", out["corrected"])
        self.assertIn("added https://", out["corrected"])


class TestBanks(LinkCase):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-linkbank-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "index").mkdir()
        (self.tmp / "index" / "tasks.jsonl").write_text(json.dumps({"name": "forensics-002", "title": "Hide and seek",
            "source": "https://play.picoctf.org/practice/challenge/362", "pico_challenge_id": 362}) + "\n")
        saved = config.TASKS_ROOT
        config.TASKS_ROOT = self.tmp
        self.addCleanup(setattr, config, "TASKS_ROOT", saved)
        rows = [{"path": "ctfs/picoCTF/2019/crypto/caesar", "ctf": "picoCTF", "year": "2019", "category": "Cryptography", "name": "caesar", "files": 2},
                {"path": "ctfs/picoCTF/2019/crypto/caesar-cipher-2", "ctf": "picoCTF", "year": "2019", "category": "Cryptography", "name": "caesar-cipher-2", "files": 1},
                {"path": "ctfs/picoCTF/2022/crypto/basic-mod1", "ctf": "picoCTF", "year": "2022", "category": "Cryptography", "name": "basic-mod1", "files": 1}]
        for name, val in (("ctfs", [{"name": "picoCTF", "sha": "x"}, {"name": "DEFCON", "sha": "y"}]), ("challenges", rows)):
            p = mock.patch.object(catalog, name, return_value=val)
            p.start()
            self.addCleanup(p.stop)

    def test_a_picoctf_practice_link_finds_the_challenge_already_here(self):
        r = self.resolve("https://play.picoctf.org/practice/challenge/362?category=4&page=1")
        self.assertEqual((r["kind"], r["task"]), ("local", "forensics-002"))
        with self.assertRaisesRegex(catalog.CatalogError, "login"):
            self.resolve("https://play.picoctf.org/practice/challenge/9999")

    def test_a_name_finds_the_challenge_in_the_archive(self):
        r = self.resolve("picoCTF 2022 basic-mod1")
        self.assertEqual((r["kind"], r["path"]), ("archive", "ctfs/picoCTF/2022/crypto/basic-mod1"))
        r = self.resolve("picoctf 2019 caesar")                      # exact name beats a longer one that contains it
        self.assertEqual(r["path"], "ctfs/picoCTF/2019/crypto/caesar")
        with self.assertRaises(linkfix.Choose):
            self.resolve("picoCTF crypto")

    def test_a_web_page_is_matched_by_its_words_not_imported_as_a_file(self):
        with mock.patch.object(linkfix, "_page", return_value=("text/html; charset=utf-8", "<html><title>basic-mod1 | picoCTF 2022</title>")):
            r = self.resolve("https://ctftime.org/writeup/12345")
        self.assertEqual((r["kind"], r["path"]), ("archive", "ctfs/picoCTF/2022/crypto/basic-mod1"))
        with mock.patch.object(linkfix, "_page", return_value=("application/octet-stream", "\x7fELF")):
            self.assertEqual(self.resolve("https://example.org/files/chall")["kind"], "file")

    def test_archive_paths_are_corrected_against_the_ctf(self):
        r = self.resolve("https://github.com/sajjadium/ctf-archives/tree/main/ctfs/PicoCTF/2022/crypto/basic-mod1")
        self.assertEqual(r["path"], "ctfs/picoCTF/2022/crypto/basic-mod1")
        self.assertIn("CTF PicoCTF is picoCTF (letter case)", r["notes"])


if __name__ == "__main__":
    unittest.main()
