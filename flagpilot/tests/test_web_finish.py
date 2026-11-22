"""Finish, rerun with a hint, the start-up panel's fast polling, recalling a TACC reply, quiet disconnects (2026-11-21).

Finish was asked for after a TACC run sat on "queued" with no way out: the page's Stop only reached local runs.
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import server as S  # noqa: E402
from app import tacc_live  # noqa: E402
from flagpilot import config  # noqa: E402

DEMO = Path(__file__).resolve().parent.parent / "demo-tasks"


class TestFinishAndHints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="fp-finish-"))
        cls._runs, cls._tasks = config.RUNS_ROOT, config.TASKS_ROOT
        config.RUNS_ROOT, config.TASKS_ROOT = cls.tmp, DEMO
        cls.srv = S.QuietServer(("127.0.0.1", 0), S.Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        config.RUNS_ROOT, config.TASKS_ROOT = cls._runs, cls._tasks
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def post(self, path, obj):
        req = urllib.request.Request(self.base + path, data=json.dumps(obj).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=20) as r:
            return json.loads(r.read().decode())

    def until(self, fn, timeout=20.0):
        t = time.time()
        while time.time() - t < timeout:
            v = fn()
            if v:
                return v
            time.sleep(0.05)
        return None

    def done_of(self, rid, timeout=30):
        with urllib.request.urlopen(self.base + f"/api/stream?id={rid}", timeout=timeout) as s:
            t = time.time()
            while time.time() - t < timeout:
                line = s.readline().decode()
                if line.startswith("data:"):
                    ev = json.loads(line[5:])
                    if ev.get("type") == "done":
                        return ev
        return None

    def test_finish_ends_a_local_run_at_once_and_only_once(self):
        rid = self.post("/api/run", {"task": "demo-001", "step": True})["run_id"]
        ctl = S.STATE[rid]["control"]
        self.assertTrue(self.until(lambda: ctl.waiting), "the run waits at its first command (step mode)")
        t = time.time()
        self.assertTrue(self.post("/api/run/control", {"id": rid, "op": "finish"})["ok"])
        ev = self.done_of(rid)
        self.assertLess(time.time() - t, 5)
        self.assertEqual((ev["verdict"], ev["reason"]), ("stopped", "finished by you"))
        # the agent stops at its gate; its own end must not add a second verdict
        self.assertTrue(self.until(lambda: S.STATE[rid]["q"].qsize() > 0 or not ctl.waiting, 10))
        time.sleep(0.5)
        dones = [e for e in self.get(f"/api/result?id={rid}")["events"] if e.get("type") == "done"]
        self.assertEqual(len(dones), 1)
        again = self.post("/api/run/control", {"id": rid, "op": "finish"})
        self.assertTrue(again.get("already"))

    def test_a_run_takes_its_own_model_and_more_turns_while_it_runs(self):
        """2026-11-22: model, tier and turns are set before a run (Solve's settings bar) and travel with it; while it runs,
        only more turns can be given ("+20 turns")."""
        r = self.post("/api/run", {"task": "demo-001", "step": True, "turns": 20, "model": "mock"})
        rid = r["run_id"]
        self.assertEqual((r["model"], r["turns"]), ("mock", 20))
        ctl = S.STATE[rid]["control"]
        self.assertTrue(self.until(lambda: ctl.waiting), "the run waits at its first command (step mode)")
        self.assertTrue(self.post("/api/run/control", {"id": rid, "op": "turns", "value": 40})["ok"])
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/api/run/control", {"id": rid, "op": "turns", "value": "lots"})
        ctx.exception.close()
        self.post("/api/run/control", {"id": rid, "op": "step", "value": False})
        self.post("/api/run/control", {"id": rid, "op": "approve", "turn": ctl.waiting["turn"]} if ctl.waiting else {"id": rid, "op": "resume"})
        self.assertIsNotNone(self.done_of(rid))
        evs = self.get(f"/api/result?id={rid}")["events"]
        meta = next(e for e in evs if e.get("type") == "meta")
        self.assertEqual((meta["model"]["name"], meta["budget"]["max_turns"]), ("mock", 20))
        self.assertEqual([(e["max_turns"], e["was"]) for e in evs if e.get("type") == "budget"], [(40, 20)])
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/api/run", {"task": "demo-001", "model": "bad model; rm"})
        self.assertEqual(ctx.exception.code, 400)
        ctx.exception.close()

    def test_a_hint_given_before_the_run_reaches_its_first_turn(self):
        rid = self.post("/api/run", {"task": "demo-001", "hint": "  try   base64  "})["run_id"]
        self.assertIsNotNone(self.done_of(rid))
        hints = [e for e in self.get(f"/api/result?id={rid}")["events"] if e.get("type") == "hint"]
        self.assertEqual([(h["turn"], h["text"]) for h in hints], [(1, "try base64")])

    def test_finish_and_hints_reach_a_tacc_run(self):
        seen = {}

        def fake_run_task(task, tier, runs_root, on_event, on_delta=None, cancelled=None, hint="", turns=20):
            seen["hint"], seen["turns"] = hint, turns
            on_event({"type": "tacc", "stage": "queued", "job": "777"})
            t = time.time()
            while time.time() - t < 10 and not cancelled():
                time.sleep(0.02)
            seen["cancelled"] = cancelled()
            return None

        with mock.patch.object(tacc_live, "run_task", fake_run_task):
            rid = self.post("/api/run", {"task": "demo-001", "backend": "tacc", "hint": "look at the header"})["run_id"]
            self.assertTrue(self.until(lambda: any(e.get("type") == "tacc" for e in S.STATE[rid]["events"])))
            self.assertTrue(self.post("/api/run/control", {"id": rid, "op": "finish"})["ok"])
            ev = self.done_of(rid)
            self.assertEqual(ev["verdict"], "stopped")
            self.assertTrue(self.until(lambda: seen.get("cancelled")), "the TACC job is told to stop")
        self.assertEqual(seen["hint"], "look at the header")
        self.assertEqual(seen["turns"], 40, "a TACC run from the page gets the page's turn budget")
        time.sleep(0.3)
        self.assertEqual([e.get("type") for e in self.get(f"/api/result?id={rid}")["events"]].count("done"), 1)

    def test_the_progress_panel_asks_for_fast_looks_at_the_serve_log(self):
        tacc_live.S["watch"] = 0.0
        d = self.get("/api/tacc/session?watch=1")
        self.assertGreater(tacc_live.S["watch"], time.time() + 10)
        self.assertIn("progress", d)
        self.assertEqual((d["min_run_s"], d["min_chat_s"]), (tacc_live.MIN_RUN_S, tacc_live.MIN_CHAT_S))
        tacc_live.S["watch"] = 0.0
        self.get("/api/tacc/session")
        self.assertEqual(tacc_live.S["watch"], 0.0)

    def test_recalling_a_question_cancels_its_tacc_job(self):
        seen = {}

        def fake_chat(m, h, task, tools, on_delta, on_tool, on_stage=None, cancelled=None):
            t = time.time()
            while time.time() - t < 10 and not cancelled():
                time.sleep(0.02)
            seen["cancelled"] = cancelled()
            return {"error": "recalled", "answer": "", "cancelled": True}

        with mock.patch.object(tacc_live, "chat", fake_chat):
            d = self.post("/api/chat", {"message": "what is this?", "backend": "tacc"})
            sid, turn = d["session_id"], d["turn"]
            self.post("/api/chat/recall", {"session_id": sid, "turn": turn})
            self.assertTrue(self.until(lambda: seen.get("cancelled")), "recall reaches the TACC job")
        time.sleep(0.3)
        rows = [json.loads(l) for l in (self.tmp / "chat" / sid / "chat.jsonl").read_text().splitlines() if l.strip()]
        self.assertNotIn("error", [r["type"] for r in rows], "a recalled question is not logged as a failure")

    def test_a_closed_tab_is_not_logged_as_a_server_error(self):
        err = io.StringIO()
        with redirect_stderr(err):
            try:
                raise ConnectionResetError(54, "Connection reset by peer")
            except ConnectionResetError:
                self.srv.handle_error(None, ("127.0.0.1", 1))
        self.assertEqual(err.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
