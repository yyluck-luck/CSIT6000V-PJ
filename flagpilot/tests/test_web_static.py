"""首页与兜底路径。

评审打开页面的第一秒就走这条路：`/` 的 ETag 与 gzip、以及所有拼错的 URL
是不是干净地回 404 而不是把服务打挂或者漏出目录内容。
"""

import gzip
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


class TestStaticAndFallback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="fp-static-"))
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

    def fetch(self, path: str, headers: dict | None = None) -> tuple[int, dict, bytes]:
        req = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    # ---------- 首页 ----------

    def test_the_page_is_served_with_an_etag(self):
        code, headers, body = self.fetch("/")
        self.assertEqual(code, 200)
        self.assertTrue(headers.get("Content-Type", "").startswith("text/html"))
        self.assertTrue(headers.get("ETag"), "没有 ETag 的话每次都要重传 80KB")
        self.assertIn(b"<html", body.lower())

    def test_index_html_is_the_same_page(self):
        a = self.fetch("/")
        b = self.fetch("/index.html")
        self.assertEqual(a[0], b[0])
        self.assertEqual(a[2], b[2])

    def test_matching_etag_answers_304_with_no_body(self):
        _, headers, _ = self.fetch("/")
        code, h, body = self.fetch("/", {"If-None-Match": headers["ETag"]})
        self.assertEqual(code, 304)
        self.assertEqual(body, b"")
        self.assertEqual(h.get("ETag"), headers["ETag"])

    def test_gzip_is_used_only_when_asked(self):
        code, headers, _ = self.fetch("/", {"Accept-Encoding": "gzip"})
        self.assertEqual(code, 200)
        self.assertEqual(headers.get("Content-Encoding"), "gzip")
        self.assertEqual(headers.get("Vary"), "Accept-Encoding")

    def test_the_gzipped_page_decompresses_to_the_plain_one(self):
        _, _, plain = self.fetch("/")
        _, _, packed = self.fetch("/", {"Accept-Encoding": "gzip"})
        self.assertEqual(gzip.decompress(packed), plain)

    # ---------- 兜底 ----------

    def test_an_unknown_page_is_a_json_404(self):
        code, headers, body = self.fetch("/no-such-page")
        self.assertEqual(code, 404)
        self.assertTrue(headers.get("Content-Type", "").startswith("application/json"))
        self.assertIn("error", json.loads(body.decode("utf-8")))

    def test_an_unknown_api_path_is_a_json_404(self):
        code, _, body = self.fetch("/api/no-such-endpoint")
        self.assertEqual(code, 404)
        self.assertIn("error", json.loads(body.decode("utf-8")))

    def test_encoded_traversal_cannot_read_outside_the_page(self):
        """静态根外面有 README、runs/、shared.env：编码过的 ../ 也不能穿出去。"""
        for bad in ("/%2e%2e/README.md", "/..%2fREADME.md", "/%2e%2e%2fshared.env"):
            with self.subTest(bad=bad):
                code, _, body = self.fetch(bad)
                self.assertEqual(code, 404, body[:200])


if __name__ == "__main__":
    unittest.main()
