"""/api/runs：历史页那一屏。

它要把两处来源合成一张表：进程内存里正在跑的运行，和盘上已经结束的运行。
合成错了，症状都是"页面上少一次运行"或"同一次运行出现两遍"，而这两种都
不会报错——评审只会觉得"怎么少了一条"。
"""

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import app.server as S
from flagpilot import config
from app import runs_index


def _rid(n: int) -> str:
    return f"{n:012x}"


def _write_trace(web: Path, rid: str, task: str = "crypto-005", attempt: int = 0,
                 summary: dict | None = None, age: int = 0) -> Path:
    d = web / rid / f"{task}-s{attempt}"
    d.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"type": "meta", "task": task, "category": "crypto"})]
    if summary is not None:
        lines.append(json.dumps(summary))
    f = d / "trace.jsonl"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if age:
        old = time.time() - age
        os.utime(web / rid, (old, old))
        os.utime(d, (old, old))
    return f


def _live(task: str = "crypto-005", kind: str = "live", **extra) -> dict:
    st = {"events": [{"type": "meta", "task": task, "category": "crypto"}],
          "t0": time.time(), "kind": kind}
    st.update(extra)
    return st


class TestHistoryAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="fp-hist-"))
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
        self.web = self.tmp / "web"
        shutil.rmtree(self.web, ignore_errors=True)
        self.web.mkdir(parents=True)
        self._state = dict(S.STATE)
        S.STATE.clear()
        runs_index._CACHE.clear()

    def tearDown(self):
        S.STATE.clear()
        S.STATE.update(self._state)
        runs_index._CACHE.clear()

    def runs(self) -> list[dict]:
        with urllib.request.urlopen(self.base + "/api/runs", timeout=10) as r:
            return json.loads(r.read().decode())["runs"]

    # ---------- 用例 ----------

    def test_an_empty_history_is_an_empty_list(self):
        self.assertEqual(self.runs(), [])

    def test_finished_runs_on_disk_are_listed(self):
        _write_trace(self.web, _rid(1), summary={"type": "summary", "verdict": "verified"})
        got = self.runs()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["id"], _rid(1))
        self.assertEqual(got[0]["verdict"], "verified")
        self.assertEqual(got[0]["source"], "web")
        self.assertFalse(got[0]["running"])

    def test_a_running_state_comes_before_the_finished_ones(self):
        _write_trace(self.web, _rid(1), age=300, summary={"type": "summary", "verdict": "verified"})
        S.STATE[_rid(2)] = _live()
        got = self.runs()
        self.assertEqual([x["id"] for x in got], [_rid(2), _rid(1)])
        self.assertTrue(got[0]["running"])
        self.assertEqual(got[0]["verdict"], "running")

    def test_the_same_run_is_not_listed_twice(self):
        """内存里有的，盘上就不用再补一遍。"""
        _write_trace(self.web, _rid(1), summary={"type": "summary", "verdict": "verified"})
        S.STATE[_rid(1)] = _live(summary={"verdict": "verified", "reason": "match"})
        got = self.runs()
        self.assertEqual([x["id"] for x in got], [_rid(1)])
        self.assertFalse(got[0]["running"])

    def test_a_chat_session_never_shows_up_in_history(self):
        S.STATE[_rid(1)] = _live(kind="chat")
        self.assertEqual(self.runs(), [])

    def test_a_state_without_events_is_not_a_run(self):
        S.STATE[_rid(1)] = {"t0": time.time(), "kind": "live"}
        self.assertEqual(self.runs(), [])

    def test_a_broken_trace_does_not_break_the_page(self):
        _write_trace(self.web, _rid(1), summary={"type": "summary", "verdict": "verified"})
        bad = _write_trace(self.web, _rid(2), summary={"type": "summary", "verdict": "verified"})
        bad.write_bytes(b"\x80\x81 not utf8 \xff")
        got = {x["id"] for x in self.runs()}
        self.assertEqual(got, {_rid(1), _rid(2)})

    def test_the_list_is_capped(self):
        """一次请求最多 300 条：历史页是给人看的，不是导数据。"""
        for n in range(305):
            _write_trace(self.web, _rid(n), summary={"type": "summary", "verdict": "verified"})
        self.assertEqual(len(self.runs()), 300)

    def test_attempts_of_one_run_share_the_run_id(self):
        _write_trace(self.web, _rid(1), attempt=0)
        _write_trace(self.web, _rid(1), attempt=1)
        self.assertEqual(len(self.runs()), 2)


if __name__ == "__main__":
    unittest.main()
