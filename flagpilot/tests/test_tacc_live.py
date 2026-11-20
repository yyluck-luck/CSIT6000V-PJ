"""网页的 TACC 会话（app/tacc_live.py）：常驻 Qwen 服务 + 单题 / 答疑小作业。

真实流程只能在课程平台上跑；这里用假的 submit.sh 与假的 tcloud 按真实日志格式回放：
服务日志里有 SERVICE_BASE_URL 与 "Application startup complete"，客户端日志里有 @@ev / @@delta / @@reply。
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

from app import tacc_live  # noqa: E402

SUBMIT = r"""#!/usr/bin/env bash
st="$FAKE_STATE"; n=$(( $(cat "$st/jobn" 2>/dev/null || echo 500) + 1 )); echo "$n" > "$st/jobn"
echo "$1" > "$st/job-$n.mode"; echo "$1 job=$n url=${2:-} tasks=${FP_TASKS:-} events=${FP_EVENTS:-} chat=${FP_CHAT_TASK:-} excl=${FP_EXCLUDE:-}" >> "$st/record"
printf 'Submitted batch job %s\n' "$n"
"""
TCLOUD = r"""#!/usr/bin/env bash
st="$FAKE_STATE"
case "$1" in
  cat) f=$(basename "$2"); n=$(echo "$f" | sed 's/[^0-9]//g'); mode=$(cat "$st/job-$n.mode" 2>/dev/null)
    case "$mode" in
      serve) c=$(( $(cat "$st/cats-$n" 2>/dev/null || echo 0) + 1 )); echo "$c" > "$st/cats-$n"
             if (( c <= ${FAKE_QUEUE:-0} )); then echo "cat: slurm_log/$f: No such file or directory"; exit 0; fi
             if [[ "$n" == "${FAKE_PRECHECK_JOB:-}" ]]; then printf 'SDK_BOOTSTRAP=/env\r\nPRECHECK_FAIL node=10-0-1-12: port 8000 is already in use\r\n'; exit 0; fi
             printf 'SDK_BOOTSTRAP=/env\r\nSERVICE_BASE_URL=http://10-0-9-9:8000/v1\r\nINFO:     Application startup complete.\r\n' ;;
      batch) printf '[fp] job=%s\n@@ev {"type": "meta", "budget": {"max_turns": 20}}\n@@ev {"type": "model", "turn": 1, "text": "{}", "prompt_tokens": 5, "completion_tokens": 3}\n' "$n"
             printf '@@ev {"type": "summary", "verdict": "verified", "reason": "match", "turns": 1, "wall_s": 2.0}\n[fp] done: /x\n' ;;
      chat) printf '[fp] job=%s node=10-0-9-9 mode=chat batch=\n' "$n"; printf '@@delta "Hello "\n@@delta "from Qwen"\n@@ev {"type": "chat_tool", "cmd": "ls", "exit_code": 0, "output": "a"}\n@@reply {"answer": "Hello from Qwen", "rounds": 1, "steps": []}\n[fp] done: chat\n' ;;
    esac ;;
  download) mkdir -p "${@: -1}/$(basename "${@: -2:1}")"; echo "download $*" >> "$st/record" ;;
  cancel) echo "cancel $3" >> "$st/record" ;;
esac
"""


class TaccLiveCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-tacc-live-"))
        (self.tmp / "state").mkdir()
        for name, body in (("submit.sh", SUBMIT), ("tcloud", TCLOUD)):
            f = self.tmp / name
            f.write_text(body, encoding="utf-8")
            f.chmod(f.stat().st_mode | stat.S_IEXEC)
        self.env = mock.patch.dict(os.environ, {
            "FLAGPILOT_TACC_SUBMIT": str(self.tmp / "submit.sh"), "TCLOUD_BIN": str(self.tmp / "tcloud"),
            "FAKE_STATE": str(self.tmp / "state"), "FLAGPILOT_TACC_POLL": "0.02", "FLAGPILOT_TACC_TICK": "0.02"})
        self.env.start()
        tacc_live.S.update(phase="idle", serve=None, node=None, url="", t0=0.0, ready=0.0, log=[], busy="", error="")

    def tearDown(self):
        tacc_live.stop()
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ready(self):
        self.assertTrue(tacc_live.start()["ok"])
        t = time.time()
        while time.time() - t < 5 and tacc_live.status()["phase"] != "ready":
            time.sleep(0.02)
        s = tacc_live.status()
        self.assertEqual((s["phase"], s["url"], s["node"]), ("ready", "http://10-0-9-9:8000/v1", "10-0-9-9"))
        self.assertGreater(s["remaining"], 3000)

    def record(self):
        return (self.tmp / "state" / "record").read_text(encoding="utf-8")

    def test_session_solve_chat_and_stop(self):
        self.ready()
        evs = []
        d = tacc_live.run_task("demo-001", "B3", self.tmp / "runs", evs.append)
        types = [e["type"] for e in evs]
        self.assertEqual(types[:2], ["tacc", "tacc"])                      # uploading, queued
        self.assertIn("model", types)
        self.assertEqual(evs[-1]["verdict"], "verified")
        self.assertTrue(d.exists())
        self.assertIn("batch job=", self.record())
        self.assertIn("tasks=demo-001 events=1", self.record())          # one challenge, events on
        deltas, tools, stages = [], [], []
        rep = tacc_live.chat("hi", [{"user": "a", "answer": "b"}], "", True, deltas.append, tools.append, stages.append)
        self.assertEqual([s["stage"] for s in stages], ["uploading", "queued", "running"])   # 网页"三个点"展开后的阶段
        self.assertEqual((stages[1]["job"], stages[2]["node"]), ("503", "10-0-9-9"))   # 501 服务、502 单题、503 答疑
        self.assertEqual("".join(deltas), "Hello from Qwen")
        self.assertEqual(rep["answer"], "Hello from Qwen")
        self.assertEqual(tools[0]["cmd"], "ls")
        tacc_live.stop()
        self.assertEqual(tacc_live.status()["phase"], "idle")
        self.assertIn("cancel 501", self.record())                        # the serve job is released

    def test_session_hour_counts_from_job_start_not_submission(self):
        """排队期间不吃会话时长：t0 从"最后一次还没输出的查询"算起（余量置 0 便于测量）。"""
        os.environ["FAKE_QUEUE"] = "3"
        with mock.patch.object(tacc_live, "BOOT_MARGIN_S", 0):
            t_before = time.time()
            self.ready()
        t0 = tacc_live.status()["t0"]
        self.assertGreaterEqual(t0 - t_before, 0.05)                  # 3 次查询都还在排队
        self.assertTrue(any("(queued)" in l for l in tacc_live.status()["log"]))

    def test_retry_after_precheck_resets_clock_and_excludes_node(self):
        os.environ["FAKE_PRECHECK_JOB"] = "501"
        t_before = time.time()
        self.ready()
        rec = self.record().splitlines()
        self.assertEqual([l.split()[1] for l in rec], ["job=501", "job=502"])
        self.assertTrue(rec[1].endswith(",10-0-1-12"))                 # 第二次提交排除了占卡节点
        self.assertGreater(tacc_live.status()["t0"], t_before + 0.015)  # 时钟按第二次提交重算

    def test_not_connected_and_one_job_at_a_time(self):
        evs = []
        self.assertIsNone(tacc_live.run_task("demo-001", "B3", self.tmp / "runs", evs.append))
        self.assertIn("not connected", evs[0]["message"])
        self.ready()
        tacc_live.S["busy"] = "solving x"
        self.assertIn("busy", tacc_live.chat("hi", [], "", True, lambda t: None, lambda e: None)["error"])
        tacc_live.S["busy"] = ""


class TaccChatHTTP(unittest.TestCase):
    """/api/chat 带 backend=tacc：回复经 SSE 回来，问答写进本机会话日志（线程列表照常可用）。"""

    def test_chat_over_tacc_is_logged(self):
        import threading
        import urllib.request
        from http.server import ThreadingHTTPServer
        from app import server as S
        from flagpilot import config
        tmp = Path(tempfile.mkdtemp(prefix="fp-tacc-http-"))
        saved = config.RUNS_ROOT
        config.RUNS_ROOT = tmp
        try:
            def fake_chat(m, h, t, tools, on_delta, on_tool, on_stage=None):
                on_stage({"stage": "queued", "job": "7"})
                on_delta("Hi")
                return {"answer": "Hi there", "steps": []}
            with mock.patch.object(tacc_live, "chat", fake_chat):
                srv = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
                threading.Thread(target=srv.serve_forever, daemon=True).start()
                base = f"http://127.0.0.1:{srv.server_address[1]}"
                req = urllib.request.Request(base + "/api/chat", data=json.dumps({"message": "hello", "backend": "tacc"}).encode(),
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    sid = json.loads(r.read())["session_id"]
                got = []
                with urllib.request.urlopen(base + f"/api/stream?id={sid}", timeout=10) as r:
                    for line in r:
                        if line.startswith(b"data:"):
                            ev = json.loads(line[5:])
                            got.append(ev["type"])
                            if ev["type"] in ("chat_done", "error"):
                                break
                srv.shutdown()
                srv.server_close()
            self.assertEqual(got, ["chat_stage", "chat_delta", "chat_done"])
            rows = [json.loads(l) for l in (tmp / "chat" / sid / "chat.jsonl").read_text().splitlines()]
            self.assertEqual([r["type"] for r in rows], ["meta", "user", "reply"])
            self.assertEqual(rows[-1]["answer"], "Hi there")
            # 失败的一轮也落盘：重开线程能看到原因；之后的问答配对不受影响
            with mock.patch.object(tacc_live, "chat", lambda *a, **k: {"error": "TACC submission failed", "answer": ""}):
                srv = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
                threading.Thread(target=srv.serve_forever, daemon=True).start()
                base = f"http://127.0.0.1:{srv.server_address[1]}"
                req = urllib.request.Request(base + "/api/chat", data=json.dumps({"message": "again", "backend": "tacc", "session_id": sid}).encode(),
                                             headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=10).close()
                last = {}
                with urllib.request.urlopen(base + f"/api/stream?id={sid}", timeout=10) as r:
                    for line in r:
                        if line.startswith(b"data:"):
                            last = json.loads(line[5:])
                            if last.get("type") in ("chat_done", "error"):
                                break
                hist = json.loads(urllib.request.urlopen(base + f"/api/chat/history?id={sid}", timeout=10).read())
                srv.shutdown()
                srv.server_close()
            self.assertEqual((last["type"], last["message"]), ("error", "TACC submission failed"))
            self.assertEqual([m["type"] for m in hist["messages"]], ["user", "reply", "user", "error"])
            self.assertEqual(hist["messages"][-1]["error"], "TACC submission failed")
        finally:
            config.RUNS_ROOT = saved
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
