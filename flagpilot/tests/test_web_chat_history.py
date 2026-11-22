"""/api/chat/history：点开会话时读的那一段。

会话日志是**追加**写的，最容易被写坏的地方就是"最后一行只写了一半"或者
"磁盘满时混进几个坏字节"。这时的正确行为是丢掉那一行、其余照常显示；
整段历史消失（列表里看不见）或者 500（点开就崩）都会让人以为记录丢了。
"""

import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import app.server as S
from flagpilot import config

SID = "abcdef123456"


def _row(kind: str, turn: int, text: str = "", **extra) -> bytes:
    return (json.dumps({"type": kind, "turn": turn, "text": text, **extra},
                       ensure_ascii=False) + "\n").encode()


class TestChatHistory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="fp-chathist-"))
        cls._runs = config.RUNS_ROOT
        config.RUNS_ROOT = cls.tmp
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        config.RUNS_ROOT = cls._runs
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        shutil.rmtree(self.tmp / "chat", ignore_errors=True)

    def write_log(self, raw: bytes, sid: str = SID) -> None:
        d = self.tmp / "chat" / sid
        d.mkdir(parents=True, exist_ok=True)
        (d / "chat.jsonl").write_bytes(raw)

    def fetch(self, path: str) -> tuple[int, dict]:
        try:
            with urllib.request.urlopen(self.base + path, timeout=15) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode() or "{}")

    # ---------- 用例 ----------

    def test_no_id_is_an_empty_answer(self):
        code, d = self.fetch("/api/chat/history")
        self.assertEqual(code, 200)
        self.assertFalse(d["exists"])
        self.assertEqual(d["messages"], [])

    def test_a_bad_id_is_400(self):
        for bad in ("../../etc", "abcdef", "ZZZZZZZZZZZZ"):
            with self.subTest(bad=bad):
                self.assertEqual(self.fetch("/api/chat/history?id=" + bad)[0], 400)

    def test_a_normal_conversation_comes_back(self):
        self.write_log(_row("meta", 0, task="crypto-005")
                       + _row("user", 1, "这题怎么做？")
                       + _row("reply", 1, "先看题面", model="qwen"))
        code, d = self.fetch(f"/api/chat/history?id={SID}")
        self.assertEqual(code, 200)
        self.assertTrue(d["exists"])
        self.assertEqual([m["type"] for m in d["messages"]], ["user", "reply"])
        self.assertEqual(d["model"], "qwen")

    def test_a_recalled_turn_is_dropped(self):
        self.write_log(_row("user", 1, "撤回的")
                       + _row("reply", 1, "回答")
                       + _row("recall", 1)
                       + _row("user", 2, "保留的"))
        _, d = self.fetch(f"/api/chat/history?id={SID}")
        self.assertEqual([m["text"] for m in d["messages"]], ["保留的"])

    def test_a_half_written_last_line_is_ignored(self):
        """进程被杀时最后一行常常只有半截。"""
        self.write_log(_row("user", 1, "第一句") + b'{"type": "reply", "tur')
        code, d = self.fetch(f"/api/chat/history?id={SID}")
        self.assertEqual(code, 200)
        self.assertEqual([m["text"] for m in d["messages"]], ["第一句"])

    def test_a_corrupt_byte_does_not_take_the_whole_log_down(self):
        """磁盘满/编码错会混进坏字节。那一行读不出来，别的行不能跟着消失。"""
        self.write_log(_row("user", 1, "第一句")
                       + b"\xff\xfe not utf8\n"
                       + _row("user", 2, "第三句"))
        code, d = self.fetch(f"/api/chat/history?id={SID}")
        self.assertEqual(code, 200, "整段历史被一个坏字节打成了 500")
        self.assertEqual([m["text"] for m in d["messages"]], ["第一句", "第三句"])


if __name__ == "__main__":
    unittest.main()
