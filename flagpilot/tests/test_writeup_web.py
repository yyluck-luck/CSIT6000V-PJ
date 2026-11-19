"""Encrypted run downloads and recovery with an isolated synthetic task set."""
import importlib.util
import hashlib
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from app import server
from flagpilot import config, storage

FLAG = "picoCTF{web_encryption_0123456789}"


@unittest.skipUnless(importlib.util.find_spec("cryptography"), "Install requirements-encryption.txt")
class TestWriteupWeb(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        tasks = self.root / "tasks"
        (tasks / "index").mkdir(parents=True)
        (tasks / "sample").mkdir()
        (tasks / "sample/note.txt").write_text(FLAG)
        row = {"task_id": 1, "name": "sample", "category": "Synthetic", "title": "Read note.txt",
               "gold_sha256": hashlib.sha256(FLAG.encode()).hexdigest(), "assets_dir": "sample", "assets": ["note.txt"]}
        (tasks / "index/tasks.jsonl").write_text(json.dumps(row) + "\n")
        key = self.root / "storage.fernet.key"
        storage.generate_key(key)
        make_cfg = server._make_cfg
        def isolated_cfg(*args, **kwargs):
            cfg = make_cfg(*args, **kwargs)
            cfg.limits.max_procs = 0
            return cfg
        self.patches = [
            patch.object(server, "_make_cfg", side_effect=isolated_cfg),
            patch.dict(os.environ, {"FLAGPILOT_ENCRYPTION_KEY_FILE": str(key)}),
            patch.object(config, "TASKS_ROOT", tasks),
            patch.object(config, "RUNS_ROOT", self.root / "runs"),
            patch.object(config, "MEMORY_ROOT", self.root / "memory"),
            patch.dict(server.SETTINGS, {"model": "mock", "base_url": "", "track": "L", "ablation": "B3"}, clear=True),
            patch.dict(server.STATE, {}, clear=True),
            patch.object(server, "RUNNING", 0),
        ]
        for p in self.patches: p.start()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.base = f"http://127.0.0.1:{self.srv.server_port}"
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join()
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def complete(self):
        req = urllib.request.Request(self.base + "/api/run", data=b'{"task":"sample"}',
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r: rid = json.load(r)["run_id"]
        for _ in range(100):
            with urllib.request.urlopen(self.base + "/api/result?id=" + rid, timeout=5) as r:
                result = json.load(r)
            if result.get("summary"): return rid, result
            time.sleep(0.05)
        self.fail("Synthetic run did not finish")

    def test_download_after_memory_reset_uses_encrypted_history(self):
        rid, result = self.complete()
        self.assertEqual(result["summary"]["verdict"], "verified")
        server.STATE.clear()
        with urllib.request.urlopen(self.base + "/api/result?id=" + rid, timeout=5) as r:
            restored = json.load(r)
        self.assertEqual(restored["summary"]["web_run_id"], rid)
        self.assertEqual(restored["summary"]["flag"], FLAG)
        with urllib.request.urlopen(self.base + "/api/writeup?id=" + rid, timeout=5) as r:
            report = r.read().decode()
            self.assertIn("attachment", r.headers["Content-Disposition"])
            self.assertEqual(r.headers["Cache-Control"], "no-store")
        self.assertIn("# FlagPilot Solution Writeup", report)
        self.assertIn("Verdict: verified", report)
        self.assertIn(FLAG, report)
        for path in (self.root / "runs").rglob("*"):
            if path.is_file(): self.assertNotIn(FLAG.encode(), path.read_bytes(), str(path))

    def test_invalid_and_unknown_ids(self):
        for rid, status in [("../..", 400), ("a" * 12, 404)]:
            with self.subTest(rid=rid), self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(self.base + "/api/writeup?id=" + rid, timeout=5)
            self.assertEqual(ctx.exception.code, status)
            ctx.exception.close()

    def test_missing_key_returns_clear_error_without_ciphertext_download(self):
        rid, _ = self.complete()
        server.STATE.clear()
        with patch.dict(os.environ, {"FLAGPILOT_ENCRYPTION_KEY_FILE": ""}):
            for route in ("/api/result?id=", "/api/writeup?id="):
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(self.base + route + rid, timeout=5)
                self.assertEqual(ctx.exception.code, 409)
                self.assertIn("ENCRYPTION_KEY_FILE", ctx.exception.read().decode())
                ctx.exception.close()

    def test_incomplete_run_has_no_writeup(self):
        run = self.root / "runs/web" / ("b" * 12) / "sample-s0"
        storage.write_records(run / "trace.jsonl", [{"type": "meta"}])
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.base + "/api/writeup?id=" + "b" * 12, timeout=5)
        self.assertEqual(ctx.exception.code, 409)
        ctx.exception.close()
