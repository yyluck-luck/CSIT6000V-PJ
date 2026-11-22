"""回放挂载：目录里有一条读不了的 trace，不能拖垮整页。

给助教/评审复现用的是 `./start.sh --replay <dir>`：把 runs/ 拷过来就能在浏览器里点开每一次真实运行。
现场拷来的目录不干净——可能有加密过的 trace（没有密钥）、有传了一半的文件、有别的工具写的二进制。
挂载本该是"有什么就回放什么"，可它以前连异常都不接：任意一条读不了，`read_jsonl` 抛出来，
`main()` 在启动阶段就死了，一个页面都起不来，而且报错里只有一行 UnicodeDecodeError，看不出是哪条。
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import app.server as S


def _write_trace(folder: Path, rows: list[dict]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "trace.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def _good(folder: Path) -> None:
    _write_trace(folder, [
        {"type": "meta", "task": folder.name},
        {"type": "model", "turn": 1, "prompt_tokens": 10, "completion_tokens": 10},
        {"type": "summary", "verdict": "verified", "reason": "match", "turns": 1},
    ])


class TestReplayMountSurvivesBadTraces(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def mount(self) -> list[tuple[str, str, str]]:
        with contextlib.redirect_stdout(io.StringIO()):
            return S._mount_replay(str(self.root))

    def test_a_binary_trace_does_not_stop_the_mount(self):
        """加密失败或拷贝中断会留下非 UTF-8 的 trace：跳过它，其余照常挂上。"""
        bad = self.root / "tacc-dev-v5-B3" / "crypto-005-s0"
        bad.mkdir(parents=True)
        (bad / "trace.jsonl").write_bytes(b"\x80\x81\x82\x00\xff not utf-8")
        _good(self.root / "tacc-dev-v5-B3" / "forensics-012-s0")

        mounted = self.mount()

        self.assertEqual([name for _, name, _ in mounted], ["forensics-012-s0"],
                         "一条读不了的 trace 让整个回放目录都没挂上")

    def test_an_encrypted_trace_without_its_key_is_skipped(self):
        """存储加密过的 trace 换了机器、没带密钥：那是"这条读不了"，不是"服务起不来"。"""
        enc = self.root / "runs" / "crypto-058-s0"
        _write_trace(enc, [{"_flagpilot_encrypted": "fernet-v1", "token": "AAAA"}])
        _good(self.root / "runs" / "crypto-072-s0")

        mounted = self.mount()

        self.assertEqual([name for _, name, _ in mounted], ["crypto-072-s0"])

    def test_the_skipped_traces_are_counted_and_named(self):
        """跳过要说话，否则 Run 页少了几条没人知道是坏了还是没跑。"""
        for i in range(2):
            d = self.root / f"bad-{i}"
            d.mkdir()
            (d / "trace.jsonl").write_bytes(b"\xff\xfe")
        _good(self.root / "ok-001-s0")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            S._mount_replay(str(self.root))
        out = buf.getvalue()

        self.assertIn("2", out, f"跳过的条数没有报出来：{out!r}")
        self.assertIn("bad-0", out, "没有说是哪几条 trace 读不了")


    def test_a_trace_that_is_still_being_written_is_not_called_broken(self):
        """0 字节的 trace = 刚开跑还没落第一条事件，不该被算进"读不了"。

        读取改成容错之后，坏字节和空文件都返回空列表，只能靠大小分开；分错了就会在
        每批刚启动时报一堆"读不了"，把真正的损坏淹没掉。
        """
        for i in range(3):
            d = self.root / f"running-{i}"
            d.mkdir()
            (d / "trace.jsonl").write_bytes(b"")
        _good(self.root / "ok-002-s0")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mounted = S._mount_replay(str(self.root))

        self.assertEqual([name for _, name, _ in mounted], ["ok-002-s0"])
        self.assertNotIn("读不了", buf.getvalue(), "空文件被当成损坏了")


if __name__ == "__main__":
    unittest.main()
