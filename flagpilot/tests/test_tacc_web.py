"""网页 → TACC：Lab 页发起流水线、看进度、跑完自动挂成回放。

真实流水线要课程 SDK 和集群；这里用 FLAGPILOT_TACC_PIPELINE 换成一个假流水线，
它按真实流水线的英文日志格式打印、写 client-live.log、产出一档的 runs.jsonl 与 trace。
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import tacc  # noqa: E402

FAKE = r"""#!/usr/bin/env bash
OUT="$1"; B="$FP_BATCH"; T="$FP_ABLATIONS"
echo "[t] serve job 101 submitted (attempt 1, exclude=none)"
echo "[t] waiting for serve 101 @10-0-1-1 (torch.compile took 9.0 s)"
echo "[t] serve ready: http://10-0-1-1:8000/v1 (job 101, node 10-0-1-1)"
echo "[t] client job 102 submitted"
printf '%s\n' "[fp] tier B3 -> $B-B3 (900s left)" "[1/2] demo-001-s0         VERIFIED   turns=3 1.2s" \
  "[2/2] demo-002-s0         wrong      turns=4 2.0s" "[fp] tier-done $B-B3 9s" "[fp] done: /x/$B" > "$OUT/client-live.log"
mkdir -p "$OUT/$B-B3/demo-001-s0"
printf '%s\n' '{"run_id":"demo-001-s0","task":"demo-001","verdict":"verified","turns":3,"wall_s":1.2,"model":"qwen"}' \
  '{"run_id":"demo-002-s0","task":"demo-002","verdict":"wrong","turns":4,"wall_s":2.0,"model":"qwen"}' > "$OUT/$B-B3/runs.jsonl"
printf '%s\n' '{"type":"meta","task":"demo-001"}' '{"type":"summary","verdict":"verified","reason":"match","turns":3}' > "$OUT/$B-B3/demo-001-s0/trace.jsonl"
sleep "${FAKE_SLEEP:-0}"
echo "[t] batch finished (ok=1); client log tail:"
echo "[t] cancel serve job 101"
"""


class TaccWebCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-tacc-web-"))
        self.fake = self.tmp / "fake_pipeline.sh"
        self.fake.write_text(FAKE, encoding="utf-8")
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IEXEC)
        self.env = mock.patch.dict(os.environ, {"FLAGPILOT_TACC_PIPELINE": str(self.fake)})
        self.env.start()
        self.mounted: list[Path] = []

    def tearDown(self):
        self.env.stop()
        tacc.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def on_done(self, out: Path) -> int:
        self.mounted.extend(sorted(out.glob("*-B[0-3]")))
        return len(self.mounted)

    def wait(self, pred, timeout=10):
        t = time.time()
        while time.time() - t < timeout:
            s = tacc.status(self.tmp / "runs")
            if pred(s):
                return s
            time.sleep(0.05)
        self.fail("timed out; last status: " + json.dumps(s)[:600])

    def test_run_parse_and_mount(self):
        r = tacc.start("web-1", "B3", 0, self.tmp / "runs", self.on_done)
        self.assertEqual(r, {"ok": True, "batch": "web-1"})
        s = self.wait(lambda s: s["phase"] == "done")
        self.assertEqual(s["jobs"], {"serve": "101", "client": "102", "node": "10-0-1-1"})
        self.assertEqual(s["per_tier"]["B3"]["done"], 2)
        self.assertEqual(s["per_tier"]["B3"]["verified"], 1)
        self.assertTrue(s["per_tier"]["B3"]["finished"])
        self.assertEqual([a["verdict"] for a in s["attempts"]], ["verified", "wrong"])
        self.assertEqual(s["results"]["B3"]["n"], 2)
        self.assertEqual(s["results"]["B3"]["verified"], 1)
        self.assertEqual(s["mounted"], 1)
        run = s["results"]["B3"]["runs"][0]                 # 只有落了目录的那一题能回放
        self.assertEqual((run["task"], run["verdict"], len(run["id"])), ("demo-001", "verified", 12))
        self.assertEqual([p.name for p in self.mounted], ["web-1-B3"])
        self.assertIn("torch.compile", s["log"][1])
        # idle again: the latest finished batch is offered for display
        self.assertEqual(s["latest"]["batch"], "web-1")

    def test_validation_and_single_flight(self):
        root = self.tmp / "runs"
        self.assertIn("error", tacc.start("Bad Name", "B3", 0, root, self.on_done))
        self.assertIn("error", tacc.start("ok", "B9", 0, root, self.on_done))
        self.assertIn("error", tacc.start("ok", "B3", 999, root, self.on_done))
        self.assertIn("error", tacc.start("ok", "B3", 0, root, self.on_done, exclude="a;rm"))
        self.assertIn("error", tacc.start("ok", "B3", 0, root, self.on_done, tasks="x y"))
        with mock.patch.dict(os.environ, {"FAKE_SLEEP": "5"}):
            self.assertTrue(tacc.start("slow", "B3", 0, root, self.on_done).get("ok"))
            self.assertIn("already running", tacc.start("again", "B3", 0, root, self.on_done)["error"])
            self.assertTrue(tacc.stop().get("ok"))
            s = self.wait(lambda s: not s["running"])
            self.assertEqual(s["phase"], "failed")          # stopped before the batch finished


if __name__ == "__main__":
    unittest.main()
