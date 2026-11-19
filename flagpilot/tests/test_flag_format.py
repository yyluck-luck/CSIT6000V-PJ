"""题面里告诉模型 flag 格式。

现场（TACC 作业 29323，general-019）：题面只问"把 42 转成二进制"，模型算对了 101010，
却先猜 `flag{101010}`、再猜 `101010`，两次都判 wrong——本题库 53 道题的 gold 全是
`picoCTF{…}`。真实比赛都会公布 flag 格式；不说，等于让模型在格式上蒙，量到的就不是解题能力。

格式属于题面信息（不是解题规则），所以 B0–B3 四档一视同仁，不影响消融的加法语义。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flagpilot import agent  # noqa: E402
from flagpilot.tasks import load_index  # noqa: E402


def _index(rows: list[dict]) -> Path:
    root = Path(tempfile.mkdtemp(prefix="fp-flagfmt-"))
    (root / "index").mkdir()
    base = {"category": "General Skills", "title": "t", "query": "q", "gold_sha256": "0" * 64,
            "assets": [], "suggested_tools": [], "scope": "core"}
    (root / "index" / "tasks.jsonl").write_text(
        "\n".join(json.dumps({**base, "task_id": i, "name": f"t-{i}", "assets_dir": f"t-{i}", **r})
                  for i, r in enumerate(rows)) + "\n", encoding="utf-8")
    return root


class TestFlagFormat(unittest.TestCase):
    def test_picoctf_source_implies_wrapper(self):
        tasks = load_index(_index([{"pico_challenge_id": 362, "source": "https://play.picoctf.org/practice/challenge/362"}]))
        self.assertEqual(tasks[0].flag_format, "picoCTF{...}")

    def test_explicit_format_wins(self):
        tasks = load_index(_index([{"flag_format": "csawctf{...}", "pico_challenge_id": 1}]))
        self.assertEqual(tasks[0].flag_format, "csawctf{...}")

    def test_unknown_source_gets_no_hint(self):
        tasks = load_index(_index([{}]))
        self.assertEqual(tasks[0].flag_format, "")
        self.assertEqual(agent.flag_format_note(tasks[0]), "")

    def test_note_names_the_wrapper(self):
        tasks = load_index(_index([{"pico_challenge_id": 7}]))
        note = agent.flag_format_note(tasks[0])
        self.assertIn("picoCTF{...}", note)
        self.assertIn("wrapper", note)


if __name__ == "__main__":
    unittest.main()
