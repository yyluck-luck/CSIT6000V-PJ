"""Ask's context (2026-11-22, "I'd also like to see context management included in Ask"): which earlier exchanges the
model sees. Recalled turns never; turns the user leaves out or starts fresh after, not; the oldest beyond 60% of the
model's window are left out automatically and the model is told. The same plan serves local chats and TACC chats."""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import app.server as S
from flagpilot import config, storage
from flagpilot.chat import (CHARS_PER_TOKEN, SYSTEM_TOKENS, ChatSession, context_note, context_plan, context_window,
                            read_log)
from flagpilot.config import ModelSpec, RunConfig


def rows_of(n: int, size: int = 40) -> list[dict]:
    rows = [{"type": "meta", "task": "", "model": "mock"}]
    for t in range(1, n + 1):
        rows += [{"type": "user", "turn": t, "text": f"question {t} " + "q" * size},
                 {"type": "reply", "turn": t, "answer": f"answer {t} " + "a" * size}]
    return rows


def states(plan: dict) -> dict:
    return {t["turn"]: t["state"] for t in plan["turns"]}


class TestPlan(unittest.TestCase):
    def test_everything_fits_by_default(self):
        plan = context_plan(rows_of(3), 32_768)
        self.assertEqual(states(plan), {1: "in", 2: "in", 3: "in"})
        self.assertEqual([p["turn"] for p in plan["pairs"]], [1, 2, 3])
        self.assertGreater(plan["tokens"], SYSTEM_TOKENS)
        self.assertEqual(context_note(plan), "")

    def test_recalled_and_unanswered_turns_are_not_listed(self):
        rows = rows_of(3) + [{"type": "recall", "turn": 2}, {"type": "user", "turn": 4, "text": "no reply yet"}]
        self.assertEqual(states(context_plan(rows, 32_768)), {1: "in", 3: "in"})

    def test_left_out_put_back_fresh_and_reset(self):
        rows = rows_of(4) + [{"type": "context", "op": "exclude", "turn": 2}]
        self.assertEqual(states(context_plan(rows, 32_768)), {1: "in", 2: "excluded", 3: "in", 4: "in"})
        rows.append({"type": "context", "op": "include", "turn": 2})
        self.assertEqual(states(context_plan(rows, 32_768))[2], "in")
        rows.append({"type": "context", "op": "fresh", "turn": 3})
        plan = context_plan(rows, 32_768)
        self.assertEqual(states(plan), {1: "before_fresh", 2: "before_fresh", 3: "in", 4: "in"})
        self.assertIn("2 earlier exchange(s)", context_note(plan))
        self.assertIn("left out by the user", context_note(plan))
        rows.append({"type": "context", "op": "reset"})
        self.assertEqual(set(states(context_plan(rows, 32_768)).values()), {"in"})

    def test_the_oldest_are_left_out_to_fit_and_the_newest_always_stays(self):
        size = int(4000 * CHARS_PER_TOKEN)                       # each exchange is about 8,000 tokens
        plan = context_plan(rows_of(5, size), 32_768)            # room for 60% of 32k minus system and reply: one exchange
        self.assertEqual(states(plan), {1: "auto", 2: "auto", 3: "auto", 4: "auto", 5: "in"})
        self.assertIn("to fit your context window", context_note(plan))
        plan = context_plan(rows_of(5, size * 10), 32_768)
        self.assertEqual(states(plan)[5], "in", "the newest exchange is always sent")
        self.assertEqual(set(states(context_plan(rows_of(5, size), 1_000_000)).values()), {"in"}, "Gemini's window holds all")

    def test_windows(self):
        self.assertEqual(context_window("qwen3.8-27b"), 32_768)
        self.assertEqual(context_window("gemini-2.5-flash"), 1_000_000)
        self.assertEqual(context_window("deepseek-v4.1-flash"), 128_000)
        self.assertEqual(context_window("mock"), 32_768)


class TestSession(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-chatctx-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = RunConfig(model=ModelSpec(name="mock"), tasks_root=self.tmp)

    def write(self, rows):
        d = self.tmp / "chat" / "s1"
        d.mkdir(parents=True, exist_ok=True)
        for r in rows:
            storage.append_record(d / "chat.jsonl", r)
        return d

    def test_resume_and_load_context_follow_the_plan(self):
        d = self.write(rows_of(3) + [{"type": "context", "op": "exclude", "turn": 1}])
        sess = ChatSession.resume(self.cfg, None, d)
        text = json.dumps(sess.messages)
        self.assertNotIn("question 1", text)
        self.assertIn("question 2", text)
        self.assertIn("left out by the user", sess.messages[0]["content"])
        self.assertEqual(sess.turns, 3)
        storage.append_record(d / "chat.jsonl", {"type": "context", "op": "fresh", "turn": 3})
        sess.load_context()
        text = json.dumps(sess.messages)
        self.assertNotIn("question 2", text)
        self.assertEqual([m["role"] for m in sess.messages], ["system", "user", "assistant"])

    def test_the_remote_chat_takes_the_planned_history_and_its_note(self):
        from flagpilot import remote
        seen = {}
        original = ChatSession.ask

        def fake_ask(sess, message, **kw):
            seen["messages"] = [dict(m) for m in sess.messages]
            from flagpilot.chat import ChatReply
            return ChatReply(answer="ok")
        ChatSession.ask = fake_ask
        try:
            remote.chat_once(self.cfg, "next", [{"user": "q2", "answer": "a2"}, {"note": "Note: 1 earlier exchange(s) ..."}],
                             workdir=self.tmp / "remote")
        finally:
            ChatSession.ask = original
        self.assertIn("Note: 1 earlier", seen["messages"][0]["content"])
        self.assertEqual([m["content"] for m in seen["messages"][1:]], ["q2", "a2"])


class TestWeb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="fp-chatctx-web-"))
        cls._runs, cls._tasks = config.RUNS_ROOT, config.TASKS_ROOT
        config.RUNS_ROOT = cls.tmp
        demo = Path(__file__).resolve().parent.parent / "demo-tasks"
        if (demo / "index" / "tasks.jsonl").exists():
            config.TASKS_ROOT = demo
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        config.RUNS_ROOT, config.TASKS_ROOT = cls._runs, cls._tasks
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return json.loads(r.read().decode())

    def post(self, path, obj):
        req = urllib.request.Request(self.base + path, data=json.dumps(obj).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())

    def code(self, path, obj):
        try:
            self.post(path, obj)
            return 200
        except urllib.error.HTTPError as e:
            e.close()
            return e.code

    def thread(self, sid, n):
        d = self.tmp / "chat" / sid
        d.mkdir(parents=True, exist_ok=True)
        for r in rows_of(n):
            storage.append_record(d / "chat.jsonl", r)
        return d

    def test_the_context_endpoint(self):
        self.thread("aaaaaaaaaaa1", 3)
        self.assertEqual(self.code("/api/chat/context", {"session_id": "aaaaaaaaaaa1", "op": "drop", "turn": 1}), 400)
        self.assertEqual(self.code("/api/chat/context", {"session_id": "aaaaaaaaaaa1", "op": "exclude"}), 400)
        self.assertEqual(self.code("/api/chat/context", {"session_id": "aaaaaaaaaaa1", "op": "exclude", "turn": True}), 400)
        self.assertEqual(self.code("/api/chat/context", {"session_id": "../etc", "op": "reset"}), 400)
        self.assertEqual(self.code("/api/chat/context", {"session_id": "bbbbbbbbbbb1", "op": "reset"}), 404)
        c = self.post("/api/chat/context", {"session_id": "aaaaaaaaaaa1", "op": "exclude", "turn": 1})
        self.assertEqual({t["turn"]: t["state"] for t in c["turns"]}, {1: "excluded", 2: "in", 3: "in"})
        self.assertEqual(c["window"], 32_768)
        h = self.get("/api/chat/history?id=aaaaaaaaaaa1")
        self.assertEqual({t["turn"]: t["state"] for t in h["context"]["turns"]}[1], "excluded")
        c = self.post("/api/chat/context", {"session_id": "aaaaaaaaaaa1", "op": "fresh", "turn": 3})
        self.assertEqual({t["turn"]: t["state"] for t in self.get("/api/chat/context?id=aaaaaaaaaaa1")["turns"]},
                         {1: "excluded", 2: "before_fresh", 3: "in"})
        c = self.post("/api/chat/context", {"session_id": "aaaaaaaaaaa1", "op": "reset"})
        self.assertEqual({t["state"] for t in c["turns"]}, {"in"})
        self.assertEqual(self.get("/api/chat/context?id=ccccccccccc9")["turns"], [], "not written yet: empty, not an error")

    def test_a_local_chat_leaves_out_what_the_user_left_out(self):
        d = self.thread("aaaaaaaaaaa2", 2)
        self.post("/api/chat/context", {"session_id": "aaaaaaaaaaa2", "op": "exclude", "turn": 1})
        r = self.post("/api/chat", {"message": "third question", "session_id": "aaaaaaaaaaa2", "tools": False})
        self.assertEqual(r["turn"], 3)
        for _ in range(200):
            if any(x.get("type") == "reply" and x.get("turn") == 3 for x in read_log(d / "chat.jsonl")):
                break
            time.sleep(0.05)
        text = json.dumps(S.CHATS["aaaaaaaaaaa2"].messages)
        self.assertNotIn("question 1", text)
        self.assertIn("question 2", text)
        self.assertIn("third question", text)

    def test_a_tacc_chat_sends_the_same_plan(self):
        self.thread("aaaaaaaaaaa3", 3)
        self.post("/api/chat/context", {"session_id": "aaaaaaaaaaa3", "op": "exclude", "turn": 2})
        seen = {}
        done = threading.Event()

        def fake_chat(message, history, task, tools, on_delta, on_tool, on_stage=None, cancelled=None):
            seen["history"] = history
            done.set()
            return {"answer": "fine", "steps": []}
        original = S.tacc_live.chat
        S.tacc_live.chat = fake_chat
        try:
            self.post("/api/chat", {"message": "fourth", "session_id": "aaaaaaaaaaa3", "backend": "tacc"})
            self.assertTrue(done.wait(10))
        finally:
            S.tacc_live.chat = original
        users = [h.get("user", "") for h in seen["history"] if "user" in h]
        self.assertEqual([u.split()[1] for u in users], ["1", "3"])
        self.assertIn("left out by the user", seen["history"][-1]["note"])


if __name__ == "__main__":
    unittest.main()
