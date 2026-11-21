"""网页的 TACC 会话（app/tacc_live.py）：常驻 Qwen 服务 + 单题 / 答疑小作业。

真实流程只能在课程平台上跑；这里用假的 submit.sh 与假的 tcloud 按真实日志格式回放：
服务日志里有 SERVICE_BASE_URL 与 "Application startup complete"，客户端日志里有 @@ev / @@delta / @@reply。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
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
st="$FAKE_STATE"
if [[ -n "${FAKE_SUBMIT_FAIL:-}" ]]; then echo "[tcloud] staging"; echo "$FAKE_SUBMIT_FAIL" >&2; exit 1; fi
if [[ -n "${FLAGPILOT_TASKS:-}" ]]; then cat "$FLAGPILOT_TASKS/index/tasks.jsonl" >> "$st/staged"; (cd "$FLAGPILOT_TASKS" && find tasks -type f) >> "$st/staged"; cat "$FLAGPILOT_TASKS"/tasks/*/* >> "$st/staged" 2>/dev/null; fi
n=$(( $(cat "$st/jobn" 2>/dev/null || echo 500) + 1 )); echo "$n" > "$st/jobn"
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
      chat) if [[ -n "${FAKE_QUEUE_CHAT:-}" ]]; then echo "cat: slurm_log/$f: No such file or directory"; exit 0; fi
            printf '[fp] job=%s node=10-0-9-9 mode=chat batch=\n' "$n"; printf '@@delta "Hello "\n@@delta "from Qwen"\n@@ev {"type": "chat_tool", "cmd": "ls", "exit_code": 0, "output": "a"}\n@@reply {"answer": "Hello from Qwen", "rounds": 1, "steps": []}\n[fp] done: chat\n' ;;
    esac ;;
  download) mkdir -p "${@: -1}/$(basename "${@: -2:1}")"; echo "download $*" >> "$st/record" ;;
  cancel) echo "cancel $3" >> "$st/record"
          if [[ -z "${FAKE_CANCEL_STUCK:-}" && -f "$st/ps" ]]; then grep -v "^ *$3 " "$st/ps" > "$st/ps.new"; mv "$st/ps.new" "$st/ps"; fi ;;
  ps) echo "             JOBID PARTITION     NAME     USER ST       TIME  NODES NODELIST(REASON)"; cat "$st/ps" 2>/dev/null ;;
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
        os.environ.pop("FLAGPILOT_TASKS", None)          # 只看 tacc_live 自己给的题库（verify.sh 会设这个变量）
        tacc_live.STATE_FILE = self.tmp / "session.json"
        tacc_live.S.update(phase="idle", serve=None, node=None, url="", t0=0.0, ready=0.0, log=[], busy="", error="")

    def tearDown(self):
        tacc_live.stop()
        tacc_live.STATE_FILE = None
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
        subs = [l for l in rec if " job=" in l]
        self.assertEqual([l.split()[1] for l in subs], ["job=501", "job=502"])
        self.assertIn("cancel 501", rec, "the failed serve job is cancelled before retrying elsewhere (it used to hold a slot)")
        self.assertTrue(subs[1].endswith(",10-0-1-12"))                # 第二次提交排除了占卡节点
        self.assertGreater(tacc_live.status()["t0"], t_before + 0.015)  # 时钟按第二次提交重算

    def test_uploaded_challenge_goes_up_as_a_dataset_copy(self):
        """上传的题（中文标题 → 中文名字）以前交不上去，看起来像"发中文就出错"。现在包成 ASCII 别名的数据集副本。"""
        from flagpilot import config, custom
        saved = config.RUNS_ROOT
        config.RUNS_ROOT = self.tmp / "runs"
        try:
            t = custom.create("未命名题目", "decode it", files=[("note.txt", b"cGljb0NURnt4fQ==\n")],
                              gold="picoCTF{upload_gold_9}")
            self.assertIn("未命名题目", t.name)
            self.ready()
            rep = tacc_live.chat("这是什么？", [], t.name, True, lambda d: None, lambda e: None)
            self.assertEqual(rep["answer"], "Hello from Qwen")
            self.assertIsNotNone(tacc_live.run_task(t.name, "B3", self.tmp / "runs", lambda e: None))
        finally:
            config.RUNS_ROOT = saved
        rec, staged = self.record(), (self.tmp / "state" / "staged").read_text(encoding="utf-8")
        alias = re.search(r"chat=(upload-[0-9a-f]{10})", rec).group(1)
        self.assertIn(f"tasks={alias} ", rec)                              # 单题求解也走别名
        row = next(json.loads(l) for l in staged.splitlines() if l.startswith("{") and f'"{alias}"' in l)
        self.assertEqual((row["name"], row["upload"], row["assets"], row["assets_dir"]),
                         (alias, t.name, ["note.txt"], f"tasks/{alias}"))
        self.assertEqual(row["gold_sha256"], t.gold_sha256)
        self.assertIn(f"tasks/{alias}/note.txt", staged)
        self.assertNotIn("picoCTF{upload_gold_9}", staged)                 # 明文答案绝不出本机

    def test_submit_failure_reason_reaches_the_page(self):
        import contextlib
        import io
        self.ready()
        os.environ["FAKE_SUBMIT_FAIL"] = "unknown challenge(s): nope"
        with contextlib.redirect_stdout(io.StringIO()) as log:          # 完整输出进服务器日志
            rep = tacc_live.chat("hi", [], "", True, lambda d: None, lambda e: None)
        self.assertIn("unknown challenge(s): nope", log.getvalue())
        self.assertEqual(rep["error"], "TACC submission failed: unknown challenge(s): nope")
        os.environ["FAKE_SUBMIT_FAIL"] = "题库索引不存在"
        with contextlib.redirect_stdout(io.StringIO()):
            rep = tacc_live.chat("hi", [], "", True, lambda d: None, lambda e: None)
        self.assertEqual(rep["error"], "TACC submission failed")            # 页面上不出现中文

    def test_not_connected_and_one_job_at_a_time(self):
        evs = []
        self.assertIsNone(tacc_live.run_task("demo-001", "B3", self.tmp / "runs", evs.append))
        self.assertIn("not connected", evs[0]["message"])
        self.ready()
        # 另一个作业占着名额：这条答疑排队（hold），名额一空就接着交，而不是报 "busy"
        tacc_live.S["busy"] = "solving x"
        threading.Timer(0.3, lambda: tacc_live.S.update(busy="")).start()
        stages = []
        rep = tacc_live.chat("hi", [], "", True, lambda t: None, lambda e: None, stages.append)
        self.assertEqual(rep["answer"], "Hello from Qwen")
        self.assertEqual(stages[0], {"stage": "hold", "busy": "solving x"})
        self.assertEqual([x["stage"] for x in stages[1:4]], ["uploading", "queued", "running"])
        # 排队时会话结束：放弃，不会一直挂着
        tacc_live.S["busy"] = "a chat reply"
        threading.Timer(0.3, lambda: tacc_live.S.update(phase="idle")).start()
        self.assertIn("not connected", tacc_live.chat("hi", [], "", True, lambda t: None, lambda e: None)["error"])
        tacc_live.S.update(busy="", phase="ready")

    def test_queue_reason_and_session_end_cancel_the_waiting_job(self):
        """作业排队时告诉页面为什么（个人名额满 / 没空 GPU）；会话到点时取消还在排队的作业，免得它之后白占名额。"""
        self.ready()
        st = self.tmp / "state"
        os.environ["FAKE_QUEUE_CHAT"] = "1"          # 答疑作业一直不开跑
        (st / "ps").write_text("             502      tacc run.slur   yliutz PD       0:00      1 (QOSMaxJobsPerUserLimit)\n")
        stages = []
        threading.Timer(0.6, lambda: tacc_live.S.update(phase="idle")).start()
        with mock.patch.object(tacc_live, "REASON_EVERY_S", 0):
            rep = tacc_live.chat("hi", [], "", True, lambda t: None, lambda e: None, stages.append)
        self.assertIn({"stage": "queued", "job": "502", "reason": "QOSMaxJobsPerUserLimit"}, stages)
        self.assertEqual(rep["error"], "the TACC session ended before the job started")
        self.assertIn("cancel 502", self.record())
        self.assertNotIn("502", (st / "ps").read_text())   # 确认取消生效（ps 里已经没有它）

    def test_a_starting_session_is_saved_and_picked_up_after_a_restart(self):
        """2026-11-20：网页服务在 Qwen 还没起来时重启，提交的服务作业没人管，排到了就白占 GPU 一小时。
        现在提交即落盘；重启后作业还在排队 / 运行就接着等（不重交），作业没了就不接。"""
        st = self.tmp / "state"
        os.environ["FAKE_QUEUE"] = "1000"                  # 一直排队：日志文件还不存在
        self.assertTrue(tacc_live.start()["ok"])
        t = time.time()
        while time.time() - t < 5 and not (self.tmp / "session.json").exists():
            time.sleep(0.02)
        saved = json.loads((self.tmp / "session.json").read_text())
        self.assertEqual((saved["phase"], saved["serve"]), ("starting", "501"))
        # 模拟重启：先让原来的启动线程正常退出（不然它会在后面的测试里继续跑），再把内存里的会话清空
        tacc_live.stop()
        t = time.time()
        while time.time() - t < 5 and tacc_live.status()["phase"] != "idle":
            time.sleep(0.02)
        time.sleep(0.1)
        tacc_live.S.update(phase="idle", serve=None, node=None, url="", t0=0.0, ready=0.0, log=[], busy="")
        (self.tmp / "session.json").write_text(json.dumps(saved))
        os.environ["FAKE_QUEUE"] = "0"
        (st / "ps").write_text("             501      tacc run.slur   yliutz PD       0:00      1 (Priority)\n")
        submitted = self.record().count("serve job=")
        self.assertTrue(tacc_live.restore(self.tmp / "session.json"))
        t = time.time()
        while time.time() - t < 5 and tacc_live.status()["phase"] != "ready":
            time.sleep(0.02)
        s = tacc_live.status()
        self.assertEqual((s["phase"], s["serve"]), ("ready", "501"))
        self.assertEqual(self.record().count("serve job="), submitted, "the waiting job is picked up, not submitted again")
        self.assertTrue(any("reconnected to serve job 501" in x for x in s["log"]))
        tacc_live.stop()                                     # 收拾干净：接回来的会话也要停掉
        t = time.time()
        while time.time() - t < 5 and tacc_live.status()["phase"] != "idle":
            time.sleep(0.02)
        # 作业已经不在了：不接
        (self.tmp / "session.json").write_text(json.dumps(saved))
        (st / "ps").write_text("")
        self.assertFalse(tacc_live.restore(self.tmp / "session.json"))

    def test_session_survives_a_web_server_restart(self):
        """网页服务重启：状态落盘，重启后核实服务作业还在跑就接回来，否则不接（以前一重启就丢下一个白占 GPU 的作业）。"""
        self.ready()
        t = time.time()                      # 提交时就落过一次盘（starting）；等"就绪"那次写完再读
        while time.time() - t < 3 and json.loads((self.tmp / "session.json").read_text()).get("phase") != "ready":
            time.sleep(0.01)
        saved = json.loads((self.tmp / "session.json").read_text())
        self.assertEqual((saved["phase"], saved["serve"], saved["url"]), ("ready", "501", "http://10-0-9-9:8000/v1"))
        st = self.tmp / "state"
        # 模拟进程重启：内存里的会话没了，作业还在 TACC 上跑
        tacc_live.S.update(phase="idle", serve=None, node=None, url="", t0=0.0, ready=0.0, log=[], busy="")
        (st / "ps").write_text("             501      tacc run.slur   yliutz  R      12:00      1 10-0-9-9\n")
        self.assertTrue(tacc_live.restore(self.tmp / "session.json"))
        s = tacc_live.status()
        self.assertEqual((s["phase"], s["serve"], s["url"]), ("ready", "501", "http://10-0-9-9:8000/v1"))
        self.assertGreater(s["remaining"], 3000)
        self.assertIn("reconnected", s["log"][-1])
        # 作业已经不在了、或会话已过 1 小时：不接
        tacc_live.S.update(phase="idle", serve=None)
        (st / "ps").write_text("")
        self.assertFalse(tacc_live.restore(self.tmp / "session.json"))
        (st / "ps").write_text("             501      tacc run.slur   yliutz  R      12:00      1 10-0-9-9\n")
        old = dict(saved, t0=time.time() - 3600)
        (self.tmp / "session.json").write_text(json.dumps(old))
        self.assertFalse(tacc_live.restore(self.tmp / "session.json"))
        self.assertEqual(tacc_live.status()["phase"], "idle")
        # 用户已经自己开了新会话：不覆盖
        (self.tmp / "session.json").write_text(json.dumps(saved))
        tacc_live.S["phase"] = "starting"
        self.assertFalse(tacc_live.restore(self.tmp / "session.json"))
        tacc_live.S["phase"] = "idle"

    def test_stop_is_written_down(self):
        self.ready()
        tacc_live.stop()
        self.assertEqual(json.loads((self.tmp / "session.json").read_text())["phase"], "idle")

    def test_cancel_is_verified(self):
        st = self.tmp / "state"
        (st / "ps").write_text("             777      tacc run.slur   yliutz  R      10:00      1 10-0-3-11\n")
        self.assertTrue(tacc_live._cancel("777"))
        (st / "ps").write_text("             778      tacc run.slur   yliutz  R      10:00      1 10-0-3-11\n")
        os.environ["FAKE_CANCEL_STUCK"] = "1"
        self.assertFalse(tacc_live._cancel("778"))
        self.assertIn("tcloud cancel -j 778", tacc_live.status()["log"][-1])     # 告诉人怎么手动收尾


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
