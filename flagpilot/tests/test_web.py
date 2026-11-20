"""演示服务的接口测试。

前端本身不做像素级测试，但**接口契约必须锁死**：
档位参数被悄悄忽略、SSE 的 done 事件缺字段，都会让"可视化测试"变成摆设。
"""

from __future__ import annotations

import base64
import json
import os
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
from flagpilot import config


class TestWebAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="fp-web-"))
        cls._runs = config.RUNS_ROOT
        config.RUNS_ROOT = cls.tmp
        # 接口测试必须自带题库：仓库里的 tasks/ 是本地软链，不进 git，
        # 全新 clone 后它不存在。有内置 demo-tasks 就用它，保证 clone 下来直接能跑。
        cls._tasks = config.TASKS_ROOT
        demo = Path(__file__).resolve().parent.parent / "demo-tasks"
        if (demo / "index" / "tasks.jsonl").exists():
            config.TASKS_ROOT = demo
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
        cls.port = cls.srv.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        config.RUNS_ROOT = cls._runs
        config.TASKS_ROOT = cls._tasks
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return r.read().decode()

    def post(self, path, obj):
        req = urllib.request.Request(self.base + path, data=json.dumps(obj).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())

    def stream_chat(self, sid, timeout=60):
        with urllib.request.urlopen(self.base + f"/api/stream?id={sid}", timeout=timeout) as s:
            t0 = time.time()
            while time.time() - t0 < timeout:
                line = s.readline().decode()
                if line.startswith("data:"):
                    ev = json.loads(line[5:])
                    if ev.get("type") == "chat_done":
                        return ev
                    if ev.get("type") == "error":
                        raise AssertionError("chat 出错：" + str(ev.get("message")))
        return None

    def upload(self, title, content=b"flag: picoCTF{upload_web_1234}\n", gold=""):
        return self.post("/api/upload", {
            "title": title,
            "query": "附件里直接写着答案",
            "category": "misc",
            "gold": gold,
            "files": [{"name": "note.txt", "data_b64": base64.b64encode(content).decode()}],
        })

    def task_name(self) -> str:
        """当前题库里第一道带 gold 的题。

        不写死题名：换成内置 demo-tasks 或任何自备题库，这套接口测试仍然成立。
        """
        if getattr(self, "_task", None):
            return self._task
        d = json.loads(self.get("/api/tasks"))
        for t in d["tasks"]:
            if t.get("has_gold"):
                self._task = t["name"]
                return self._task
        self.skipTest("current task set has no task with gold")

    def stream_done(self, run_id, timeout=40):
        with urllib.request.urlopen(self.base + f"/api/stream?id={run_id}", timeout=timeout) as s:
            t0 = time.time()
            while time.time() - t0 < timeout:
                line = s.readline().decode()
                if line.startswith("data:"):
                    ev = json.loads(line[5:])
                    if ev.get("type") == "done":
                        return ev
        return None

    # ---------- 用例 ----------

    def test_index_has_controls(self):
        html = self.get("/")
        # 界面全英文（2026-11-19 起）：标记跟着换成英文，检查的仍是同一批功能
        for marker in ("Four-tier sweep", "Base-model probe", "Agent flow", "solve.py",
                       "Upload challenge", "Ask the tutor", "Allow commands",
                       # 可展开的步骤链路：工具调用与思维链都要看得见
                       "Call chain", "Thought", "Tool output", "Expand all", "data-step",
                       # 每步的自动解读 + 工具/文件的"说人话"词表
                       "What this step does", "auto-explained", "TOOL_DOC", "chipWhat",
                       # 按 run_id 回放一次已有运行
                       "?run=", "recoverRun"):
            self.assertIn(marker, html)

    def test_chat_sessions_listed_newest_first(self):
        """答疑页左侧的会话列表：标题取第一句提问，不带回答正文。"""
        root = config.RUNS_ROOT / "chat"
        for sid, q, ts in (("aaaa1111bbbb", "first question", 1000), ("cccc2222dddd", "second question", 2000)):
            (root / sid).mkdir(parents=True, exist_ok=True)
            f = root / sid / "chat.jsonl"
            f.write_text(json.dumps({"type": "meta", "task": "demo-001"}) + "\n"
                         + json.dumps({"type": "user", "turn": 1, "text": q}) + "\n"
                         + json.dumps({"type": "reply", "turn": 1, "answer": "secret answer body"}) + "\n",
                         encoding="utf-8")
            os.utime(f, (ts, ts))
        d = json.loads(self.get("/api/chat/sessions"))
        ids = [x["id"] for x in d["sessions"]]
        self.assertLess(ids.index("cccc2222dddd"), ids.index("aaaa1111bbbb"))
        one = next(x for x in d["sessions"] if x["id"] == "cccc2222dddd")
        self.assertEqual((one["title"], one["turns"], one["task"]), ("second question", 1, "demo-001"))
        self.assertNotIn("secret answer body", json.dumps(d))

    def test_tasks_expose_ablations(self):
        d = json.loads(self.get("/api/tasks"))
        self.assertGreater(len(d["tasks"]), 0)
        self.assertEqual([a["name"] for a in d["ablations"]], ["B0", "B1", "B2", "B3"])
        self.assertIn(d["ablation"], ("B0", "B1", "B2", "B3"))

    def test_run_respects_ablation(self):
        r = self.post("/api/run", {"task": self.task_name(), "ablate": "B0"})
        self.assertEqual(r["ablation"], "B0")
        done = self.stream_done(r["run_id"])
        self.assertIsNotNone(done, "没等到 done 事件")
        # 档位必须真的透传到 summary，而不是只回显在 HTTP 响应里
        self.assertEqual(done.get("ablation"), "B0")
        self.assertIn(done.get("verdict"), ("verified", "unverified", "wrong", "budget", "error"))
        self.assertIn("server", done)
        # 结果里必须带模型名：否则报告里分不清这行数是哪个模型跑的
        self.assertTrue(done.get("model"))
        # 失败归因随 done 一起下发，界面不用切命令行才能知道为什么失败
        self.assertIn("failure", done)

    def test_result_replays_events_after_stream_dropped(self):
        """SSE 断开后必须还能把完整步骤补回来。

        现场来源：服务重启过一次，页面就永远停在"运行中…"、右侧全是破折号，
        而 runs/web 里的 trace 其实已经完整落盘了。
        """
        r = self.post("/api/run", {"task": self.task_name(), "ablate": "B3"})
        rid = r["run_id"]

        # 完全不消费 SSE，模拟页面关掉 / 连接断掉
        d = {}
        for _ in range(200):
            d = json.loads(self.get(f"/api/result?id={rid}"))
            if d.get("summary"):
                break
            time.sleep(0.1)
        self.assertTrue(d.get("summary"), "没等到 summary")
        types = [e.get("type") for e in d["events"]]
        self.assertIn("model", types)
        self.assertIn("done", types, "done 也要进历史，否则重连无从上锁")
        self.assertIn(d["summary"]["verdict"], ("verified", "unverified", "wrong", "budget", "error"))

        # 再开流：应整段重放，且立刻收尾，不重复
        got = []
        with urllib.request.urlopen(self.base + f"/api/stream?id={rid}", timeout=20) as s:
            for raw in s:
                line = raw.decode()
                if line.startswith("data:"):
                    got.append(json.loads(line[5:]))
                if got and got[-1].get("type") == "done":
                    break
        self.assertEqual([e["type"] for e in got], types)

    def test_result_survives_restart_by_reading_trace(self):
        """进程重启后 STATE 没了，`?run=` 仍应从 trace.jsonl 把结果读回来。"""
        r = self.post("/api/run", {"task": self.task_name(), "ablate": "B3"})
        rid = r["run_id"]
        self.assertIsNotNone(self.stream_done(rid))

        with S.LOCK:
            S.STATE.pop(rid, None)          # 模拟服务重启
        d = json.loads(self.get(f"/api/result?id={rid}"))
        self.assertTrue(d.get("summary"), "盘上有 trace 却读不回来")
        self.assertIn("model", [e.get("type") for e in d["events"]])
        self.assertIn(f"{self.task_name()}-s0", d["summary"].get("run_dir", ""))

    def test_replay_stream_ends_with_done(self):
        """挂载的回放经 SSE 读：最后一条必须是 done。以前兜底那条写成 {"type": "done", **summary}，
        被 summary 自带的 type 覆盖成了 summary，按 done 收尾的读端会一直等到超时。"""
        d = self.tmp / "replay-src" / "x-001-s0"
        d.mkdir(parents=True)
        rows = [{"type": "meta", "task": "x-001"}, {"type": "model", "turn": 1, "prompt_tokens": 1, "completion_tokens": 1},
                {"type": "summary", "verdict": "verified", "reason": "match", "turns": 1}]
        (d / "trace.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        (rid, _, _), = S._mount_replay(str(d.parent))
        got = []
        with urllib.request.urlopen(self.base + f"/api/stream?id={rid}", timeout=10) as s:
            for raw in s:
                line = raw.decode()
                if line.startswith("data:"):
                    got.append(json.loads(line[5:]))
                    if got[-1].get("type") == "done" or len(got) > len(rows):
                        break
        self.assertEqual([e["type"] for e in got], ["meta", "model", "summary", "done"])
        self.assertEqual((got[-1]["verdict"], got[-1]["reason"]), ("verified", "match"))

    def test_models_endpoint(self):
        d = json.loads(self.get("/api/models"))
        self.assertTrue(d["models"])
        self.assertEqual(d["current"], S.SETTINGS["model"])

    def test_switch_model(self):
        old = S.SETTINGS["model"]
        try:
            d = self.post("/api/settings", {"model": "test-model"})
            self.assertEqual(d["model"], "test-model")
            self.assertEqual(S.SETTINGS["model"], "test-model")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.post("/api/settings", {"model": "  "})
            self.assertEqual(ctx.exception.code, 400)
            ctx.exception.close()
        finally:
            S.SETTINGS["model"] = old

    def test_unknown_ablation_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/api/run", {"task": self.task_name(), "ablate": "B9"})
        self.assertEqual(ctx.exception.code, 400)
        ctx.exception.close()

    def test_probe_endpoint(self):
        d = self.post("/api/probe", {"task": self.task_name(), "mode": "brief"})
        self.assertEqual(d["mode"], "brief")
        self.assertFalse(d["hit"])      # mock 没有记忆
        self.assertTrue(d["refused"])   # 必须如实记成 UNKNOWN

    # ---------- 本地服务的防护 ----------

    def test_chat_history_rejects_path_traversal(self):
        """sid 直接拼路径曾是目录穿越：只允许 12 位十六进制。"""
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/chat/history?id=../../../../etc")
        self.assertEqual(ctx.exception.code, 400)
        ctx.exception.close()

    def test_post_requires_json_content_type(self):
        """跨站表单/sendBeacon 发不了 application/json，这条挡掉大半 CSRF。"""
        req = urllib.request.Request(
            self.base + "/api/run",
            data=json.dumps({"task": self.task_name()}).encode(),
            headers={"Content-Type": "text/plain"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 415)
        ctx.exception.close()

    def test_post_rejects_cross_origin(self):
        req = urllib.request.Request(
            self.base + "/api/settings",
            data=json.dumps({"model": "x"}).encode(),
            headers={"Content-Type": "application/json",
                     "Origin": "http://evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 403)
        ctx.exception.close()

    # ---------- 自建题目 ----------

    def test_upload_and_run_without_gold(self):
        """没有 gold 的题：最多判 reproduced，绝不能判 verified。"""
        d = self.upload("web 上传题")
        name = d["task"]["name"]
        self.assertFalse(d["task"]["has_gold"])
        self.assertIn("reproduced", d["warning"])

        tasks = json.loads(self.get("/api/tasks"))
        self.assertTrue(any(t["name"] == name for t in tasks["custom_tasks"]))

        r = self.post("/api/run", {"task": name, "ablate": "B3"})
        done = self.stream_done(r["run_id"])
        self.assertIsNotNone(done, "自建题没跑完")
        self.assertEqual(done["verdict"], "reproduced")
        self.assertFalse(done["replay"]["gold_checked"])
        self.post("/api/custom/delete", {"task": name})

    def test_upload_with_gold_can_reach_verified(self):
        d = self.upload("带答案的上传题", gold="picoCTF{upload_web_1234}")
        name = d["task"]["name"]
        self.assertTrue(d["task"]["has_gold"])
        r = self.post("/api/run", {"task": name, "ablate": "B3"})
        done = self.stream_done(r["run_id"])
        self.assertEqual(done["verdict"], "verified")
        self.post("/api/custom/delete", {"task": name})

    def test_delete_only_custom(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/api/custom/delete", {"task": self.task_name()})
        self.assertEqual(ctx.exception.code, 400)
        ctx.exception.close()

    # ---------- 答疑 ----------

    def test_chat_without_task(self):
        r = self.post("/api/chat", {"message": "ROT13 是什么？", "tools": False})
        sid = r["session_id"]
        done = self.stream_chat(sid)
        self.assertIsNotNone(done)
        self.assertTrue(done["answer"].strip())
        self.assertEqual(done["steps"], [])

    def test_chat_executes_real_command(self):
        name = self.upload("答疑用题")["task"]["name"]
        try:
            r = self.post("/api/chat", {"message": "帮我看看附件里有什么", "task": name})
            sid = r["session_id"]
            done = self.stream_chat(sid)
            self.assertGreaterEqual(len(done["steps"]), 1, "命令没有被真正执行")
            self.assertIn("ls", done["steps"][0]["cmd"])
            self.assertIn("note.txt", done["steps"][0]["output"])

            # 同一会话继续追问：轮次要累加，历史要带上
            r2 = self.post("/api/chat", {"message": "那答案是什么", "session_id": sid})
            self.assertEqual(r2["session_id"], sid)
            done2 = self.stream_chat(sid)
            self.assertGreaterEqual(done2["turns"], 2)
        finally:
            self.post("/api/custom/delete", {"task": name})

    def test_failed_chat_turn_is_recorded(self):
        """这一轮没答成：线程里留一条 error，重开时看得到原因（以前只有一条没有回复的提问）。"""
        from unittest import mock
        from flagpilot import chat as chat_mod
        with mock.patch.object(chat_mod.ChatSession, "ask", side_effect=RuntimeError("boom")):
            sid = self.post("/api/chat", {"message": "will fail", "tools": False})["session_id"]
            with self.assertRaises(AssertionError) as ctx:
                self.stream_chat(sid, timeout=20)
        self.assertIn("RuntimeError: boom", str(ctx.exception))
        hist = json.loads(self.get(f"/api/chat/history?id={sid}"))
        self.assertEqual(hist["messages"][-1]["type"], "error")
        self.assertIn("RuntimeError: boom", hist["messages"][-1]["error"])

    def test_chat_requires_message(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/api/chat", {"message": "  "})
        self.assertEqual(ctx.exception.code, 400)
        ctx.exception.close()

    def test_qwen3_disables_thinking_in_web_path(self):
        """web 与 CLI 必须用同一个构造函数，否则思维链会把每轮预算吃光。"""
        old = S.SETTINGS["model"]
        S.SETTINGS["model"] = "qwen3:8b"
        try:
            cfg = S._make_cfg("web")
            self.assertTrue(cfg.model.soft_no_think)
            self.assertFalse(cfg.model.enable_thinking)
        finally:
            S.SETTINGS["model"] = old


if __name__ == "__main__":
    unittest.main()
