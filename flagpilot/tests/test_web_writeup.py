"""/api/writeup：历史页上的「下载 writeup」。

评审会真的点这个按钮，所以三条都要钉住：id 不合法是 400、run 不存在是 404、
run 还没跑完是 409（说清原因），而不是让 writeup.render 的 ValueError 变成 500。
"""

import contextlib
import io
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
from flagpilot import cli, config

DEMO = Path(__file__).resolve().parent.parent / "demo-tasks"
RID = "abcdef123456"


@unittest.skipUnless((DEMO / "index" / "tasks.jsonl").is_file(), "需要仓库自带的 demo-tasks")
class TestWriteupDownload(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="fp-wu-"))
        cls._runs = config.RUNS_ROOT
        cls._tasks = config.TASKS_ROOT
        config.RUNS_ROOT = cls.tmp
        config.TASKS_ROOT = DEMO
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

    def setUp(self):
        shutil.rmtree(self.tmp / "web", ignore_errors=True)
        (self.tmp / "web").mkdir(parents=True)

    def fetch(self, path: str) -> tuple[int, dict, bytes]:
        req = urllib.request.Request(self.base + path)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def make_run(self) -> Path:
        """跑一次真的 mock 批次，落到 runs/web/<rid>/ —— 就是服务读取的位置。"""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["batch", "--batch", f"web/{RID}", "--model", "mock",
                           "--limit", "1", "--workers", "1"])
        self.assertEqual(rc, 0, buf.getvalue()[-500:])
        dirs = sorted((self.tmp / "web" / RID).glob("*-s0"))
        self.assertTrue(dirs, "批次没有产出 run 目录")
        return dirs[0]

    # ---------- 用例 ----------

    def test_an_empty_id_is_400(self):
        self.assertEqual(self.fetch("/api/writeup")[0], 400)

    def test_a_path_that_is_not_a_run_id_is_400(self):
        for bad in ("../../etc/passwd", "abcdef", "ABCDEF123456", "abcdef12345g"):
            with self.subTest(bad=bad):
                self.assertEqual(self.fetch("/api/writeup?id=" + bad)[0], 400)

    def test_an_unknown_run_is_404(self):
        self.assertEqual(self.fetch(f"/api/writeup?id={'0' * 12}")[0], 404)

    def test_a_finished_run_downloads_markdown(self):
        self.make_run()
        code, headers, body = self.fetch(f"/api/writeup?id={RID}")
        self.assertEqual(code, 200)
        self.assertTrue(headers.get("Content-Type", "").startswith("text/markdown"))
        self.assertIn(f'filename="flagpilot-{RID}.md"', headers.get("Content-Disposition", ""))
        self.assertIn("#", body.decode("utf-8"), "writeup 应当是一份 markdown")

    def test_a_run_that_never_finished_is_409_with_a_reason(self):
        d = self.tmp / "web" / RID / "crypto-005-s0"
        d.mkdir(parents=True)
        (d / "trace.jsonl").write_text(
            json.dumps({"type": "meta", "task": "crypto-005"}) + "\n", encoding="utf-8")
        code, _, body = self.fetch(f"/api/writeup?id={RID}")
        self.assertEqual(code, 409)
        self.assertIn("error", json.loads(body.decode("utf-8")))

    def test_an_existing_writeup_is_served_without_regenerating(self):
        run_dir = self.make_run()
        self.fetch(f"/api/writeup?id={RID}")
        md = run_dir / "writeup.md"
        self.assertTrue(md.exists())
        before = md.stat().st_mtime_ns
        self.fetch(f"/api/writeup?id={RID}")
        self.assertEqual(md.stat().st_mtime_ns, before)


if __name__ == "__main__":
    unittest.main()
