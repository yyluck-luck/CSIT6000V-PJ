"""/api/task：站内题面预览。

和 /api/tasks 的区别是它**带题面正文**（以及附件清单），所以两条边界最要紧：
名字不合法时必须是 404 而不是 500，而且正文可以给、答案不能跟着给。
"""

import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import app.server as S
from flagpilot import config

FIELDS = {"name", "title", "category", "statement", "files", "source",
          "custom", "has_gold", "service"}


class TestTaskPreview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="fp-task-"))
        cls._runs = config.RUNS_ROOT
        config.RUNS_ROOT = cls.tmp
        cls._tasks = config.TASKS_ROOT
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
        config.RUNS_ROOT = cls._runs
        config.TASKS_ROOT = cls._tasks
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def raw(self, path: str) -> tuple[int, str]:
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return r.status, r.read().decode()

    def body(self, path: str) -> dict:
        return json.loads(self.raw(path)[1])

    def expect(self, path: str) -> int:
        try:
            self.raw(path)
        except urllib.error.HTTPError as exc:
            return exc.code
        raise AssertionError(f"{path} 本该失败，却返回 200")

    def first_task(self) -> dict:
        tasks = self.body("/api/tasks")["tasks"]
        if not tasks:
            self.skipTest("current task set is empty")
        return tasks[0]

    # ---------- 用例 ----------

    def test_unknown_name_is_404(self):
        self.assertEqual(self.expect("/api/task?name=no-such-task-xyz"), 404)

    def test_a_missing_name_is_404_not_500(self):
        self.assertEqual(self.expect("/api/task"), 404)

    def test_an_absurdly_long_name_is_rejected_cheaply(self):
        """200 字符以上直接不看：名字只可能是短 slug，长的不该进解析。"""
        self.assertEqual(self.expect("/api/task?name=" + "a" * 500), 404)

    def test_a_real_task_comes_back_with_the_exact_field_set(self):
        t = self.first_task()
        d = self.body("/api/task?name=" + urllib.parse.quote(t["name"]))
        self.assertEqual(set(d), FIELDS)
        self.assertEqual(d["name"], t["name"])
        self.assertEqual(d["category"], t["category"])
        self.assertTrue(d["statement"], "题面不能为空")
        self.assertIsInstance(d["files"], list)

    def test_the_payload_never_carries_the_answer(self):
        """has_gold 是布尔，答案本身不在这条接口上。"""
        t = self.first_task()
        d = self.body("/api/task?name=" + urllib.parse.quote(t["name"]))
        self.assertIsInstance(d["has_gold"], bool)
        self.assertNotIn("gold", d)
        self.assertNotIn("solution", d)

    def test_attachment_names_are_bare_filenames(self):
        """附件清单来自题目的 assets/：给出去的是文件名，不是服务器上的路径。"""
        t = self.first_task()
        d = self.body("/api/task?name=" + urllib.parse.quote(t["name"]))
        for f in d["files"]:
            self.assertNotIn("/", f["name"], f)
            self.assertNotIn("..", f["name"], f)
            self.assertGreaterEqual(f["size"], 0)


if __name__ == "__main__":
    unittest.main()
