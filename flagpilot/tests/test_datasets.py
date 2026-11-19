"""NYU CTF Bench 转换器的测试。

用合成仓库，不依赖上游那 3.7G 的 clone——单测要能在没有网络的环境跑。
重点验证三件事：筛选口径、排除清单、以及明文 flag 绝不落盘。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flagpilot import datasets  # noqa: E402
from flagpilot.tasks import load_index  # noqa: E402

FLAG = "flag{synthetic_nyu_flag_0123456789}"


def write_challenge(base: Path, name: str, category: str, *,
                    compose: bool = False, flag: str = FLAG,
                    files: list[str] | None = None, extra: dict | None = None) -> Path:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    meta = {
        "name": name, "category": category,
        "description": f"Challenge {name} description.",
        "flag": flag, "files": files or [], "compose": compose,
    }
    meta.update(extra or {})
    (d / "challenge.json").write_text(json.dumps(meta), encoding="utf-8")
    for f in files or []:
        (d / f).write_text(f"content of {f}\n", encoding="utf-8")
    # 这些不该被拷走
    (d / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (d / "test_solver").mkdir(exist_ok=True)
    (d / "test_solver" / "solve.py").write_text("print('solution')\n", encoding="utf-8")
    return d


class ConvertCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-nyu-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        split = self.repo / "development" / "2020" / "CSAW-Quals"
        write_challenge(split / "crypto", "easy", "crypto", files=["data.txt"])
        write_challenge(split / "rev", "crackme", "rev", files=["bin"])
        write_challenge(split / "web", "site", "web", files=["index.html"])
        write_challenge(split / "crypto", "server", "crypto", compose=True)
        write_challenge(split / "crypto", "noflag", "crypto", flag="")
        self.out = self.tmp / "out"

    def convert(self, **kw):
        return datasets.convert(self.repo, "dev", self.out, **kw)


class TestSelection(ConvertCase):
    def test_only_crypto_and_rev(self):
        stats = self.convert()
        names = {t.name for t in load_index(self.out)}
        self.assertEqual(len(names), 2, names)
        self.assertIn("2020-csaw-quals-crypto-easy", names)
        self.assertIn("2020-csaw-quals-rev-crackme", names)

    def test_compose_challenges_excluded(self):
        self.convert()
        names = {t.name for t in load_index(self.out)}
        self.assertNotIn("2020-csaw-quals-crypto-server", names)

    def test_compose_can_be_included(self):
        stats = self.convert(include_compose=True)
        self.assertEqual(stats["tasks"], 3)

    def test_no_flag_excluded(self):
        stats = self.convert()
        self.assertEqual(stats["tasks"], 2)
        self.assertEqual(stats["excluded"], 2)   # compose + noflag


class TestArtifacts(ConvertCase):
    def test_categories_are_mapped(self):
        self.convert()
        cats = {t.category for t in load_index(self.out)}
        self.assertEqual(cats, {"Cryptography", "Reverse Engineering"})

    def test_plaintext_flag_never_written(self):
        """这是硬要求：题库目录里不能出现明文 flag。"""
        self.convert()
        for f in self.out.rglob("*"):
            if f.is_file():
                self.assertNotIn(FLAG, f.read_text(encoding="utf-8", errors="ignore"),
                                 f"明文 flag 泄漏到 {f}")

    def test_gold_hash_matches_flag(self):
        self.convert()
        tasks = load_index(self.out)
        want = hashlib.sha256(FLAG.encode()).hexdigest()
        self.assertTrue(all(t.gold_sha256 == want for t in tasks))

    def test_challenge_json_is_not_copied(self):
        """challenge.json 含明文 flag，stage_assets 挡不住它，转换阶段就不能带。"""
        self.convert()
        self.assertEqual(list(self.out.rglob("challenge.json")), [])

    def test_only_declared_files_copied(self):
        self.convert()
        task_dir = self.out / "tasks" / "2020-csaw-quals-crypto-easy"
        self.assertTrue((task_dir / "data.txt").exists())
        self.assertFalse((task_dir / "Dockerfile").exists())
        self.assertFalse((task_dir / "test_solver").exists())

    def test_excluded_report_written(self):
        self.convert()
        md = (self.out / "EXCLUDED.md").read_text(encoding="utf-8")
        self.assertIn("crypto/server", md)
        self.assertIn("compose", md)
        self.assertIn("noflag", md)

    def test_source_metadata(self):
        self.convert()
        src = json.loads((self.out / "SOURCE.json").read_text(encoding="utf-8"))
        self.assertEqual(src["dataset"], "NYU CTF Bench")
        self.assertEqual(src["split"], "dev")
        self.assertEqual(src["tasks"], 2)
        self.assertIn("GPL-2.0", src["license"])


class TestNaming(ConvertCase):
    def test_name_is_path_derived_and_safe(self):
        split = self.repo / "development" / "2020" / "CSAW-Quals" / "rev"
        write_challenge(split, "Odd Name's Here", "rev", files=["a b.bin"])
        self.convert()
        names = [t.name for t in load_index(self.out)]
        self.assertTrue(all(" " not in n and "'" not in n for n in names), names)

    def test_duplicate_names_get_suffix(self):
        a = self.repo / "development" / "2020" / "CSAW-Quals" / "crypto" / "x"
        b = self.repo / "development" / "2020" / "CSAW-Finals" / "crypto" / "x"
        write_challenge(a.parent, "x", "crypto")
        write_challenge(b.parent, "x", "crypto")
        self.convert()
        names = [t.name for t in load_index(self.out)]
        self.assertEqual(len(names), len(set(names)), names)


class TestMissingSplit(unittest.TestCase):
    def test_unknown_split_raises(self):
        tmp = Path(tempfile.mkdtemp(prefix="fp-nyu-empty-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with self.assertRaises(FileNotFoundError):
            list(datasets.iter_challenges(tmp, "dev"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
