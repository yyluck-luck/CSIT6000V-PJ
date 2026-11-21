"""在线题库：题目目录识别、"不抄答案"的附件规则、gold 判定、SSRF 拦截、链接导入（网络全部打桩）。"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import catalog
from flagpilot import config, custom

TREE = [
    {"path": "2019", "type": "tree"}, {"path": "2019/README.md", "type": "blob", "size": 40},
    {"path": "2019/crypto", "type": "tree"},
    {"path": "2019/crypto/AES-ABC", "type": "tree"},
    {"path": "2019/crypto/AES-ABC/README.md", "type": "blob", "size": 100},
    {"path": "2019/crypto/AES-ABC/aes-abc.py", "type": "blob", "size": 1600},
    {"path": "2019/crypto/AES-ABC/solve.py", "type": "blob", "size": 900},
    {"path": "2019/crypto/AES-ABC/flag.txt", "type": "blob", "size": 30},
    {"path": "2019/crypto/AES-ABC/dist", "type": "tree"},
    {"path": "2019/crypto/AES-ABC/dist/body.enc", "type": "blob", "size": 5000},
    {"path": "2019/crypto/AES-ABC/writeup", "type": "tree"},
    {"path": "2019/crypto/AES-ABC/writeup/notes.md", "type": "blob", "size": 50},
    {"path": "2019/forensics", "type": "tree"},
    {"path": "2019/forensics/Flags", "type": "tree"},
    {"path": "2019/forensics/Flags/flag.png", "type": "blob", "size": 4000},
    {"path": "2020", "type": "tree"},
    {"path": "2020/warmup", "type": "tree"},
    {"path": "2020/warmup/note.txt", "type": "blob", "size": 10},
]
FILES = {
    "README.md": b"Decrypt the image. AES in a custom mode.",
    "aes-abc.py": b"print('enc')", "dist/body.enc": b"\x00" * 64,
    "flag.txt": b"picoCTF{aes_abc_m0de_1s_br0ken}\n",
}


class TestCatalog(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-cat-"))
        self.saved = (config.RUNS_ROOT, custom.store_root)
        config.RUNS_ROOT = self.tmp / "runs"
        self.addCleanup(self.restore)
        ctfs = [{"name": "picoCTF", "sha": "abc", "type": "dir"}]

        def fake_get(url, limit=None, accept=""):
            if url.endswith("/contents/ctfs?ref=main"):
                return json.dumps(ctfs).encode()
            if "/git/trees/abc" in url:
                return json.dumps({"tree": TREE, "truncated": False}).encode()
            rel = url.split("/ctfs/picoCTF/2019/crypto/AES-ABC/", 1)[-1]
            return FILES[rel]
        p = mock.patch.object(catalog, "_get", side_effect=fake_get)
        p.start()
        self.addCleanup(p.stop)

    def restore(self):
        config.RUNS_ROOT = self.saved[0]
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_folder_of_challenges_is_not_one_challenge(self):
        """0CTF/2018/Finals 有个 README，底下才是一道道题：以前整个 Finals 被当成了一道题。"""
        tree = [{"path": "2018", "type": "tree"}, {"path": "2018/Finals", "type": "tree"}, {"path": "2018/Finals/README.md", "type": "blob", "size": 50},
                {"path": "2018/Finals/web", "type": "tree"}, {"path": "2018/Finals/web/blog", "type": "tree"},
                {"path": "2018/Finals/web/blog/app.py", "type": "blob", "size": 900},
                {"path": "2018/Finals/pwn", "type": "tree"}, {"path": "2018/Finals/pwn/heap", "type": "tree"},
                {"path": "2018/Finals/pwn/heap/heap", "type": "blob", "size": 9000},
                {"path": "2018/Misc-Collection", "type": "tree"}, {"path": "2018/Misc-Collection/notes.md", "type": "blob", "size": 10},
                {"path": "2018/Misc-Collection/one", "type": "tree"}, {"path": "2018/Misc-Collection/one/x.bin", "type": "blob", "size": 10},
                {"path": "2018/solo", "type": "tree"}, {"path": "2018/solo/README.md", "type": "blob", "size": 10}, {"path": "2018/solo/chal", "type": "blob", "size": 10}]
        self.assertEqual(sorted(catalog.challenge_dirs(tree)), ["2018/Finals/pwn/heap", "2018/Finals/web/blog", "2018/Misc-Collection/one", "2018/solo"])

    def test_challenge_dirs_skip_year_and_category_levels(self):
        dirs = catalog.challenge_dirs(TREE)
        self.assertEqual(sorted(dirs), ["2019/crypto/AES-ABC", "2019/forensics/Flags", "2020/warmup"])
        self.assertIn(["dist/body.enc", 5000], dirs["2019/crypto/AES-ABC"])

    def test_challenges_rows_and_search(self):
        rows = catalog.challenges("picoCTF")
        aes = next(r for r in rows if r["name"] == "AES-ABC")
        self.assertEqual((aes["year"], aes["category"], aes["readme"]), ("2019", "Cryptography", True))
        res = catalog.search("pico 2019 crypto")
        self.assertEqual([r["name"] for r in res["challenges"]], ["AES-ABC"])
        self.assertEqual(catalog.search("pico")["ctfs"], ["picoCTF"])

    def test_preview_keeps_solutions_and_flag_out(self):
        pv = catalog.preview("ctfs/picoCTF/2019/crypto/AES-ABC")
        self.assertEqual(sorted(f["name"] for f in pv["files"]), ["aes-abc.py", "dist/body.enc"])
        why = {s["name"]: s["why"] for s in pv["skipped"]}
        self.assertEqual(why["solve.py"], "looks like a solution")
        self.assertEqual(why["writeup/notes.md"], "looks like a solution")
        self.assertIn("gold flag", why["flag.txt"])
        self.assertTrue(pv["gold"])
        self.assertIn("Decrypt the image", pv["readme"])

    def test_import_creates_a_challenge_with_gold_and_no_answers(self):
        root = self.tmp / "custom"
        with mock.patch.object(custom, "store_root", return_value=root):
            r = catalog.import_archive("ctfs/picoCTF/2019/crypto/AES-ABC")
        t = r["task"]
        self.assertTrue(t.has_gold)
        self.assertEqual(t.category, "Cryptography")
        names = sorted(t.assets)
        self.assertTrue(any("aes-abc.py" in n for n in names) and any("body.enc" in n for n in names))
        self.assertFalse(any("solve" in n or "flag" in n or "writeup" in n for n in names), names)
        stored = "".join(p.read_text(errors="replace") for p in root.rglob("*") if p.is_file())
        self.assertNotIn("aes_abc_m0de_1s_br0ken", stored, "the gold flag is stored only as a hash")

    def test_service_challenges_are_flagged(self):
        self.assertTrue(catalog.is_service([["Dockerfile", 10], ["chall", 100]], ""))
        self.assertTrue(catalog.is_service([["src/ctf.xinetd", 10]], ""))
        self.assertTrue(catalog.is_service([["chall", 1]], "Connect with nc chall.example.com 31337"))
        self.assertFalse(catalog.is_service([["cipher.txt", 10], ["enc.py", 20]], "Decrypt the message."))
        self.assertFalse(catalog.preview("ctfs/picoCTF/2019/crypto/AES-ABC")["service"])

    def test_titles_come_from_the_question(self):
        self.assertEqual(catalog.readme_title("# AES-ABC\nDecrypt it."), "AES-ABC")
        self.assertEqual(catalog.readme_title("## Description\nSome text\n# **Children Tcache**\n"), "Children Tcache")
        self.assertEqual(catalog.readme_title("no heading at all"), "")
        pv = catalog.preview("ctfs/picoCTF/2019/crypto/AES-ABC")
        self.assertEqual(pv["title"], "AES-ABC")
        self.assertEqual(pv["source"], "https://github.com/sajjadium/ctf-archives/tree/main/ctfs/picoCTF/2019/crypto/AES-ABC")

    def test_an_import_keeps_its_source_page(self):
        root = self.tmp / "custom2"
        with mock.patch.object(custom, "store_root", return_value=root):
            r = catalog.import_archive("ctfs/picoCTF/2019/crypto/AES-ABC")
            again = custom.get(r["task"].name, root=root)
        self.assertEqual(r["task"].title, "AES-ABC")
        self.assertTrue(r["task"].source.startswith("https://github.com/sajjadium/ctf-archives/tree/main/ctfs/picoCTF/"))
        self.assertEqual(again.source, r["task"].source, "the link survives a reload from disk")

    def test_fake_flags_are_not_gold(self):
        self.assertEqual(catalog.gold_from("flag{this_is_a_fake_flag}"), "")
        self.assertEqual(catalog.gold_from("FLAG{REDACTED}"), "")
        self.assertEqual(catalog.gold_from("not a flag"), "")
        self.assertEqual(catalog.gold_from("picoCTF{s0me_r3al_0ne}\n"), "picoCTF{s0me_r3al_0ne}")

    def test_a_challenge_whose_description_is_the_material_imports_without_files(self):
        """picoCTF 2019 "13": the ROT13 line is in the README; there is nothing else to attach."""
        def only_readme(url, limit=None, accept=""):
            if url.endswith("/contents/ctfs?ref=main"):
                return json.dumps([{"name": "picoCTF", "sha": "abc", "type": "dir"}]).encode()
            if "/git/trees/abc" in url:
                return json.dumps({"tree": [{"path": "2019", "type": "tree"}, {"path": "2019/crypto", "type": "tree"},
                                            {"path": "2019/crypto/13", "type": "tree"},
                                            {"path": "2019/crypto/13/README.md", "type": "blob", "size": 60}], "truncated": False}).encode()
            return b"# 13\nCryptography can be easy, do you know what ROT13 is? cvpbPGS{abg_gbb_onq_bs_n_ceboyrz}"
        with mock.patch.object(catalog, "_get", side_effect=only_readme):
            out = catalog.import_archive("ctfs/picoCTF/2019/crypto/13")
        self.assertEqual(out["task"].assets, [])
        self.assertIn("cvpbPGS{", out["task"].query)

    def test_bad_paths_are_refused(self):
        for bad in ("ctfs/picoCTF/../../etc", "etc/passwd", "ctfs/picoCTF/2019/nope"):
            with self.assertRaises(catalog.CatalogError):
                catalog.preview(bad)


class TestLinks(unittest.TestCase):
    def test_private_and_odd_links_are_refused(self):
        for bad in ("file:///etc/passwd", "ftp://example.com/x", "http://127.0.0.1/x", "http://localhost:8080/api/tasks",
                    "http://10.0.0.5/x", "http://169.254.169.254/latest/meta-data", "http://[::1]/x"):
            with self.assertRaises(catalog.CatalogError, msg=bad):
                catalog.check_url(bad)

    def test_github_links_are_recognised(self):
        self.assertTrue(catalog._GH_TREE.match("https://github.com/sajjadium/ctf-archives/tree/main/ctfs/picoCTF/2019/crypto/caesar"))
        self.assertTrue(catalog._GH_BLOB.match("https://github.com/a/b/blob/main/x/y.bin"))
        self.assertIsNone(catalog._GH_TREE.match("https://github.com/a/b"))


if __name__ == "__main__":
    unittest.main()
