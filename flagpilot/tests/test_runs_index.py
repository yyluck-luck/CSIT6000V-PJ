"""运行历史索引：历史页读的就是这一层。

三条约定要锁住：只认 12 位 hex 的目录名（`runs/web` 下还有别的东西）、
一条 trace 读不出来不能拖垮整页、文件没变就别重复解析（历史页一次列几十条，
每条几十 KB，全读会卡住页面）。
"""

import json
import os
import time
import unittest
from pathlib import Path
import tempfile

from app import runs_index


def _rid(n: int) -> str:
    return f"{n:012x}"


def _write_trace(parent: Path, name: str = "crypto-005-s0",
                 meta: dict | None = None, summary: dict | None = None,
                 filler: int = 0) -> Path:
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    lines = []
    if meta is not None:
        lines.append(json.dumps(meta, ensure_ascii=False))
    for i in range(filler):
        lines.append(json.dumps({"type": "model", "i": i}))
    if summary is not None:
        lines.append(json.dumps(summary, ensure_ascii=False))
    trace = d / "trace.jsonl"
    trace.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return trace


META = {"type": "meta", "task": "crypto-005", "category": "crypto", "ts": 1_700_000_000}
SUMM = {"type": "summary", "verdict": "verified", "reason": "match", "turns": 7,
        "model": "qwen", "ablation": "B3", "e2e_s": 12.5, "wall_s": 13.0,
        "total_tokens": 900, "completion_tokens": 400}


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-idx-"))
        self.web = self.tmp / "web"
        self.web.mkdir()
        runs_index._CACHE.clear()

    def tearDown(self):
        runs_index._CACHE.clear()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestListRuns(_Base):
    def test_missing_root_is_an_empty_list(self):
        self.assertEqual(runs_index.list_runs(self.tmp / "nope"), [])

    def test_only_hex_directory_names_count(self):
        """runs/web 下还有索引文件、临时目录；历史页不该把它们当运行。"""
        _write_trace(self.web / _rid(1), meta=META, summary=SUMM)
        _write_trace(self.web / "not-a-run", meta=META, summary=SUMM)
        _write_trace(self.web / "abc", meta=META, summary=SUMM)
        (self.web / "README.txt").write_text("x", encoding="utf-8")
        got = runs_index.list_runs(self.tmp)
        self.assertEqual([x["id"] for x in got], [_rid(1)])

    def test_newest_first(self):
        """排序按运行目录的 mtime —— 也就是这次运行的开始时刻。"""
        for n, age in ((1, 300), (2, 100), (3, 200)):
            d = self.web / _rid(n)
            _write_trace(d, meta=META, summary=SUMM)
            old = time.time() - age
            os.utime(d, (old, old))
        self.assertEqual([x["id"] for x in runs_index.list_runs(self.tmp)],
                         [_rid(2), _rid(3), _rid(1)])

    def test_every_attempt_of_a_run_is_listed(self):
        """一个目录下可以有多个 <task>-sN，每个都是一次 attempt。"""
        d = self.web / _rid(1)
        _write_trace(d, name="crypto-005-s0", meta=META, summary=SUMM)
        _write_trace(d, name="crypto-005-s1", meta=META, summary=SUMM)
        self.assertEqual(len(runs_index.list_runs(self.tmp)), 2)

    def test_limit_counts_directories_not_attempts(self):
        """limit 是"最多看多少个运行目录"，一个目录里的多个 attempt 都要出来。
        这不只是实现细节：历史页靠它保证最近 N 次运行完整，而不是被某个
        重试了很多遍的运行挤掉。"""
        for n in range(3):
            d = self.web / _rid(n)
            _write_trace(d, name="a-s0", meta=META, summary=SUMM)
            _write_trace(d, name="a-s1", meta=META, summary=SUMM)
        self.assertEqual(len(runs_index.list_runs(self.tmp, limit=2)), 4)

    def test_a_non_utf8_trace_shows_as_unknown_instead_of_breaking(self):
        """拷了一半、或者被别的工具改坏：这条显示 unknown，别的照常列。

        注意这里不是"跳过"——跳过会让用户以为那一次运行不存在；显示 unknown
        才说明"这次跑了，但读不出来"。
        """
        _write_trace(self.web / _rid(1), meta=META, summary=SUMM)
        bad = _write_trace(self.web / _rid(2), meta=META, summary=SUMM)
        bad.write_bytes(b"\x80\x81 not utf8 \xff")
        got = {x["id"]: x for x in runs_index.list_runs(self.tmp)}
        self.assertEqual(sorted(got), [_rid(1), _rid(2)])
        self.assertEqual(got[_rid(2)]["verdict"], "unknown")

    def test_a_trace_that_cannot_be_opened_is_skipped(self):
        """读不了（权限、I/O 错）才是跳过：这次运行确实列不出来。"""
        _write_trace(self.web / _rid(1), meta=META, summary=SUMM)
        bad = _write_trace(self.web / _rid(2), meta=META, summary=SUMM)
        os.chmod(bad, 0o000)
        try:
            got = runs_index.list_runs(self.tmp)
        finally:
            os.chmod(bad, 0o644)
        self.assertEqual([x["id"] for x in got], [_rid(1)])

    def test_a_run_directory_without_a_trace_is_skipped(self):
        (self.web / _rid(9) / "crypto-005-s0").mkdir(parents=True)
        self.assertEqual(runs_index.list_runs(self.tmp), [])


class TestSummarizeTrace(_Base):
    def test_meta_and_summary_are_projected(self):
        trace = _write_trace(self.web / _rid(1), meta=META, summary=SUMM)
        item = runs_index.summarize_trace(_rid(1), trace)
        self.assertEqual(item["id"], _rid(1))
        self.assertEqual(item["task"], "crypto-005")
        self.assertEqual(item["category"], "crypto")
        self.assertEqual(item["verdict"], "verified")
        self.assertEqual(item["turns"], 7)
        self.assertEqual(item["ablation"], "B3")
        self.assertEqual(item["source"], "web")
        self.assertFalse(item["running"])

    def test_no_summary_reads_as_unknown(self):
        trace = _write_trace(self.web / _rid(1), meta=META)
        item = runs_index.summarize_trace(_rid(1), trace)
        self.assertEqual(item["verdict"], "unknown")

    def test_task_falls_back_to_the_directory_name(self):
        """meta 丢了（加密没密钥、被截断）时，题目名还能从 <task>-sN 还原。"""
        trace = _write_trace(self.web / _rid(1), meta=None, summary=SUMM)
        item = runs_index.summarize_trace(_rid(1), trace)
        self.assertEqual(item["task"], "crypto-005")

    def test_the_flag_never_leaves_the_trace(self):
        """列表接口只给摘要：答案不该跟着历史页发到浏览器。"""
        trace = _write_trace(self.web / _rid(1), meta={**META, "flag": "picoCTF{secret}"},
                             summary={**SUMM, "flag": "picoCTF{secret}"})
        item = runs_index.summarize_trace(_rid(1), trace)
        self.assertNotIn("picoCTF{secret}", json.dumps(item, ensure_ascii=False))

    def test_cache_is_reused_while_the_file_is_unchanged(self):
        trace = _write_trace(self.web / _rid(1), meta=META, summary=SUMM)
        first = runs_index.summarize_trace(_rid(1), trace)
        calls = []
        real = runs_index._head_meta

        def counting(p):
            calls.append(p)
            return real(p)

        runs_index._head_meta = counting
        try:
            again = runs_index.summarize_trace(_rid(1), trace)
        finally:
            runs_index._head_meta = real
        self.assertEqual(calls, [], "文件没变却又解析了一遍")
        self.assertEqual(first, again)

    def test_cache_is_dropped_when_the_trace_grows(self):
        """运行还在写：文件变大之后必须重读，否则历史页永远停在第一次看到的状态。"""
        trace = _write_trace(self.web / _rid(1), meta=META)
        self.assertEqual(runs_index.summarize_trace(_rid(1), trace)["verdict"], "unknown")
        trace.write_text(trace.read_text(encoding="utf-8")
                         + json.dumps(SUMM) + "\n", encoding="utf-8")
        st = trace.stat()
        os.utime(trace, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        self.assertEqual(runs_index.summarize_trace(_rid(1), trace)["verdict"], "verified")


def _write_chat(root: Path, sid: str, rows: list[dict]) -> Path:
    d = root / sid
    d.mkdir(parents=True, exist_ok=True)
    f = d / "chat.jsonl"
    f.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return f


class _ChatBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-chat-"))
        runs_index._CHAT_CACHE.clear()

    def tearDown(self):
        runs_index._CHAT_CACHE.clear()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestListChats(_ChatBase):
    def test_newest_first(self):
        for sid, age in (("aaaa1111", 300), ("bbbb2222", 100), ("cccc3333", 200)):
            f = _write_chat(self.tmp, sid, [{"type": "user", "turn": 1, "text": "hi"}])
            old = time.time() - age
            os.utime(f, (old, old))
        self.assertEqual([x["id"] for x in runs_index.list_chats(self.tmp)],
                         ["bbbb2222", "cccc3333", "aaaa1111"])

    def test_title_is_the_first_question_on_one_line(self):
        _write_chat(self.tmp, "aaaa1111", [
            {"type": "meta", "task": "crypto-005"},
            {"type": "user", "turn": 1, "text": "  这题\n的   思路\t是什么？  "},
            {"type": "reply", "turn": 1, "text": "先看题面"},
        ])
        item = runs_index.list_chats(self.tmp)[0]
        self.assertEqual(item["title"], "这题 的 思路 是什么？")
        self.assertEqual(item["turns"], 1)
        self.assertEqual(item["task"], "crypto-005")

    def test_a_long_question_is_cut_at_eighty_characters(self):
        _write_chat(self.tmp, "aaaa1111", [{"type": "user", "turn": 1, "text": "长" * 200}])
        self.assertEqual(len(runs_index.list_chats(self.tmp)[0]["title"]), 80)

    def test_recalled_turns_do_not_count(self):
        """撤回只追加一条 recall：被撤回的那轮不该算进轮数，也不该当标题。"""
        _write_chat(self.tmp, "aaaa1111", [
            {"type": "user", "turn": 1, "text": "撤回掉的这句"},
            {"type": "reply", "turn": 1, "text": "回答"},
            {"type": "recall", "turn": 1},
            {"type": "user", "turn": 2, "text": "留下的这句"},
            {"type": "reply", "turn": 2, "text": "回答"},
        ])
        item = runs_index.list_chats(self.tmp)[0]
        self.assertEqual(item["title"], "留下的这句")
        self.assertEqual(item["turns"], 1)

    def test_a_session_with_only_a_recalled_question_is_hidden(self):
        _write_chat(self.tmp, "aaaa1111", [
            {"type": "user", "turn": 1, "text": "只问了一句"},
            {"type": "recall", "turn": 1},
        ])
        self.assertEqual(runs_index.list_chats(self.tmp), [])

    def test_directory_names_that_are_not_session_ids_are_ignored(self):
        _write_chat(self.tmp, "abcd1234", [{"type": "user", "turn": 1, "text": "hi"}])
        _write_chat(self.tmp, "not-a-session", [{"type": "user", "turn": 1, "text": "hi"}])
        _write_chat(self.tmp, "abc", [{"type": "user", "turn": 1, "text": "hi"}])
        self.assertEqual([x["id"] for x in runs_index.list_chats(self.tmp)], ["abcd1234"])

    def test_a_broken_log_is_skipped_not_raised(self):
        _write_chat(self.tmp, "abcd1234", [{"type": "user", "turn": 1, "text": "hi"}])
        bad = _write_chat(self.tmp, "bad12345", [{"type": "user", "turn": 1, "text": "hi"}])
        bad.write_bytes(b"\xff\xfe not utf8")
        self.assertEqual([x["id"] for x in runs_index.list_chats(self.tmp)], ["abcd1234"])

    def test_cache_is_reused_while_the_log_is_unchanged(self):
        _write_chat(self.tmp, "aaaa1111", [{"type": "user", "turn": 1, "text": "hi"}])
        runs_index.list_chats(self.tmp)
        calls = []
        import flagpilot.chat as chat
        real = chat.read_log

        def counting(p):
            calls.append(p)
            return real(p)

        chat.read_log = counting
        try:
            runs_index.list_chats(self.tmp)
        finally:
            chat.read_log = real
        self.assertEqual(calls, [], "日志没变却又读了一遍")


class TestThreadModel(unittest.TestCase):
    def test_last_choice_wins(self):
        rows = [
            {"type": "meta", "model": "qwen"},
            {"type": "model", "model": "qwen"},
            {"type": "model", "model": "deepseek"},
            {"type": "reply", "model": "deepseek", "text": "..."},
        ]
        self.assertEqual(runs_index.thread_model(rows), "deepseek")

    def test_picked_provider_beats_the_concrete_model(self):
        """界面只显示提供商：链换到哪个型号不该漏到列表里。"""
        rows = [{"type": "meta", "model": "qwen"},
                {"type": "model", "picked": "gemini", "model": "gemini-2.5-pro"}]
        self.assertEqual(runs_index.thread_model(rows), "gemini")

    def test_empty_thread_has_no_model(self):
        self.assertEqual(runs_index.thread_model([]), "")
        self.assertEqual(runs_index.thread_model([{"type": "user", "text": "hi"}]), "")


if __name__ == "__main__":
    unittest.main()
