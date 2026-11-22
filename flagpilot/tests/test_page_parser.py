"""The page reads a model reply exactly as the engine does.

app/index.html keeps its own copy of the reply parser, for runs recorded before the engine wrote down the action it
parsed (the "action" event). On 2026-11-22 that copy knew none of the forms the engine accepts (DSML markup, a lone
</think>, raw line breaks inside strings, {"name", "arguments"}), and 32 of the 100 steps of a run showed "? —".

This test runs the page's parser under node on every reply recorded in results/ plus the hard cases below, and fails
on the first reply where the page and flagpilot.tools disagree. It is skipped where node is not installed (CI has it).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flagpilot.tools import as_action, json_object, markup_call, parse_action  # noqa: E402

KEYS = ("action", "thought", "cmd", "code", "path", "flag", "script", "scheme", "text")
D = "<｜DSML｜ "
E = "</｜DSML｜ "
HARD = [
    '{"thought": "t", "action": "shell", "cmd": "ls"}',
    '```json\n{"thought": "t", "action": "shell", "cmd": "ls"}\n```',
    'I will list the files.\n{"thought": "t", "action": "shell", "cmd": "ls"}\nThat is all.',
    '<think>draft {"action": "shell", "cmd": "rm x"}</think>{"thought": "t", "action": "shell", "cmd": "ls"}',
    'draft {"action": "shell", "cmd": "pwd"}</think>\n{"thought": "t", "action": "python", "code": "print(1)"}',
    '{"thought": "t", "action": "python", "code": "import os\nprint(os.listdir(\'.\'))\n\tdone = 1"}',
    '{"name": "shell", "arguments": {"cmd": "ls -la"}}',
    '{"name": "shell", "arguments": "{\\"cmd\\": \\"id\\"}", "thought": "who am I"}',
    f'Let me look.\n{D}invoke name="python">\n{D}parameter name="code" string="true">print(1)\n{E}parameter>\n{E}invoke>',
    f'{D}invoke name="action">shell{E}parameter>\n{D}parameter name="cmd" string="true">ls -la{E}parameter>',
    f'{D}invoke name="shell">{D}parameter name="cmd" string="true">ls{E}parameter>'
    f'{D}parameter name="timeout" string="false">30{E}parameter>{E}invoke>',
    '{"thought": "grep for {", "action": "shell", "cmd": "grep -o \'\\\\{[a-z]*\\\\}\' f"} and a stray }',
    '{"thought": "a", "action": "shell", "cmd": "ls"}\n{"thought": "b", "action": "shell", "cmd": "pwd"}',
    'Plan: {x}\n```json\n{"action": "shell", "cmd": "id"}\n```',
    '{"action": "submit", "flag": "picoCTF{x}", "script": "print(\'picoCTF{x}\')"}',
    '{"action": "decode", "scheme": "base64", "text": "aGk="}',
    "no JSON here", "", "   ", '{"foo": 1}', "[1, 2]", '{"action": "shell", "cmd": ',
]


def engine(text: str):
    """What the engine reads from a reply: the object parse_action builds its Action from (strings only)."""
    if not text or not text.strip():
        return None
    obj = json_object(text)
    if obj is None:
        obj = markup_call(text)
    obj = as_action(obj) if isinstance(obj, dict) else None
    if not isinstance(obj, dict):
        return None
    return {k: v for k, v in obj.items() if k in KEYS and isinstance(v, str)}


def page_parser() -> str:
    html = (ROOT / "app" / "index.html").read_text(encoding="utf-8")
    a, b = html.index("/* ==== reply parser (begin) ===="), html.index("/* ==== reply parser (end) ==== */")
    return html[a:b]


def recorded_replies() -> list[str]:
    seen, out = set(), []
    for f in sorted((ROOT / "results").rglob("trace.jsonl")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if '"model"' not in line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            t = ev.get("text") if ev.get("type") == "model" else None
            if isinstance(t, str) and t not in seen:
                seen.add(t)
                out.append(t)
    return out


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestPageReadsRepliesLikeTheEngine(unittest.TestCase):
    def page(self, texts: list[str]) -> list:
        tmp = Path(tempfile.mkdtemp(prefix="fp-page-parser-"))
        try:
            (tmp / "in.json").write_text(json.dumps(texts), encoding="utf-8")
            (tmp / "run.js").write_text(page_parser() + """
const fs = require('fs'), KEYS = %s;
const texts = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = texts.map(t => { const o = parseModel(t); if (!o || typeof o !== 'object' || Array.isArray(o)) return null;
  const r = {}; for (const k of KEYS) if (typeof o[k] === 'string') r[k] = o[k]; return r; });
fs.writeFileSync(process.argv[3], JSON.stringify(out));
""" % json.dumps(KEYS), encoding="utf-8")
            subprocess.run(["node", str(tmp / "run.js"), str(tmp / "in.json"), str(tmp / "out.json")],
                           check=True, capture_output=True, timeout=120)
            return json.loads((tmp / "out.json").read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def check(self, texts: list[str]) -> None:
        got = self.page(texts)
        for text, page in zip(texts, got):
            want = engine(text)
            self.assertEqual(page, want, f"page and engine disagree on this reply:\n{text[:600]!r}")

    def test_the_hard_cases(self):
        self.check(HARD)
        self.assertEqual(engine(HARD[8])["code"].strip(), "print(1)", "the DSML reply is an action for the engine")
        act, err = parse_action(HARD[8], ("shell", "python", "submit"))
        self.assertEqual((act.action, err), ("python", None))

    def test_every_recorded_reply(self):
        texts = recorded_replies()
        self.assertGreater(len(texts), 1000, "results/ holds the evaluated runs' replies")
        self.check(texts)


if __name__ == "__main__":
    unittest.main()
