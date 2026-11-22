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
if [[ -n "${FAKE_SUBMIT_SLEEP:-}" ]]; then sleep "$FAKE_SUBMIT_SLEEP"; fi
if [[ -n "${FAKE_SESSION_FAILS:-}" ]]; then c=$(( $(cat "$st/sessfail" 2>/dev/null || echo 0) + 1 )); echo "$c" > "$st/sessfail"
  if (( c <= FAKE_SESSION_FAILS )); then echo "[Tcloud Error] 2026/11/20 tcloudcli.go:140: Failed to create remote session" >&2; exit 1; fi; fi
if [[ -n "${FLAGPILOT_TASKS:-}" ]]; then cat "$FLAGPILOT_TASKS/index/tasks.jsonl" >> "$st/staged"; (cd "$FLAGPILOT_TASKS" && find tasks -type f) >> "$st/staged"; cat "$FLAGPILOT_TASKS"/tasks/*/* >> "$st/staged" 2>/dev/null; fi
n=$(( $(cat "$st/jobn" 2>/dev/null || echo 500) + 1 )); echo "$n" > "$st/jobn"
printf '%18s      tacc run.slur   yliutz  R       0:01      1 10-0-9-9\n' "$n" >> "$st/ps"
echo "$1" > "$st/job-$n.mode"; echo "$1 job=$n url=${2:-} tasks=${FP_TASKS:-} events=${FP_EVENTS:-} chat=${FP_CHAT_TASK:-} excl=${FP_EXCLUDE:-}" >> "$st/record"
echo "$1 job=$n turns=${FP_TURNS:-} time=${FP_TIME:-}" >> "$st/budget"
printf 'Submitted batch job %s\n' "$n"
"""
TCLOUD = r"""#!/usr/bin/env bash
st="$FAKE_STATE"
case "$1" in
  cat) if [[ -n "${FAKE_CAT_HANG:-}" ]]; then h=$(( $(cat "$st/hang" 2>/dev/null || echo 0) + 1 )); echo "$h" > "$st/hang"
          if (( h == 1 )); then sleep "$FAKE_CAT_HANG"; echo "[Tcloud Error] tcloudcli.go:140: Failed to create remote session"; exit 1; fi; fi
       if [[ -n "${FAKE_CAT_FLAKE:-}" ]]; then k=$(( $(cat "$st/flake" 2>/dev/null || echo 0) + 1 )); echo "$k" > "$st/flake"
          if (( k % 2 == 0 )); then echo "[Tcloud Error] 2026/11/21 tcloudcli.go:140: Failed to create remote session"; exit 1; fi; fi
    f=$(basename "$2"); n=$(echo "$f" | sed 's/[^0-9]//g'); mode=$(cat "$st/job-$n.mode" 2>/dev/null)
    case "$mode" in
      serve) c=$(( $(cat "$st/cats-$n" 2>/dev/null || echo 0) + 1 )); echo "$c" > "$st/cats-$n"
             if (( c <= ${FAKE_QUEUE:-0} )); then echo "cat: slurm_log/$f: No such file or directory"; exit 0; fi
             if [[ "$n" == "${FAKE_PRECHECK_JOB:-}" ]]; then printf 'SDK_BOOTSTRAP=/env\r\nPRECHECK_FAIL node=10-0-1-12: port 8000 is already in use\r\n'; exit 0; fi
             if [[ "$n" == "${FAKE_BUSY_JOB:-}" ]]; then
               printf 'SDK_BOOTSTRAP=/env\r\nSERVICE_BASE_URL=http://10-0-9-8:8000/v1\r\n(EngineCore pid=1) ValueError: Free memory on device (5.10/23.59 GiB) on startup is less than desired GPU memory utilization (0.9, 21.23 GiB).\r\nsrun: error: 10-0-9-8: task 0: Exited with exit code 1\r\n'; exit 0; fi
             if [[ "$n" == "${FAKE_SERVE_VANISH_JOB:-}" ]]; then grep -v "^ *$n " "$st/ps" > "$st/ps.new"; mv "$st/ps.new" "$st/ps"
               printf 'SDK_BOOTSTRAP=/env\r\nSERVICE_BASE_URL=http://10-0-9-9:8000/v1\r\n(Worker_TP0 pid=9) Loading safetensors checkpoint shards:  40%% Completed | 2/5\r\n'; exit 0; fi
             # every real vLLM start-up prints a harmless warning with a traceback (deep_gemm), and an INFO line about free memory
             printf 'SDK_BOOTSTRAP=/env\r\nSERVICE_BASE_URL=http://10-0-9-9:8000/v1\r\n(APIServer pid=8) INFO 11-21 16:24:18 [api_utils.py:345]  version 0.26.0\r\n'
             printf '(Worker_TP0 pid=9) WARNING 11-21 16:27:34 [import_utils.py:408] Traceback (most recent call last):\r\n'
             printf '(Worker_TP0 pid=9) INFO 11-21 16:27:34 [gpu_worker.py:857] Free memory on device (23.19/23.59 GiB) on startup. Desired GPU memory utilization is (0.9, 21.23 GiB).\r\n'
             if (( c > ${FAKE_QUEUE:-0} + ${FAKE_SERVE_WARN:-0} )); then printf 'INFO:     Application startup complete.\r\n'; fi ;;
      batch) c=$(( $(cat "$st/cats-$n" 2>/dev/null || echo 0) + 1 )); echo "$c" > "$st/cats-$n"
             case "${FAKE_BATCH:-ok}" in
               nomatch) printf 'SDK_BOOTSTRAP=/env\n[fp] job=%s\n[fp] tier B3 -> x-B3 (761s left)\n没有匹配到题目\n[fp] batch x-B3 exited non-zero\n[fp] done: /x\n' "$n"; exit 0 ;;
               vanish) printf 'SDK_BOOTSTRAP=/env\n[fp] job=%s\n[fp] tier B3 -> x-B3 (761s left)\n' "$n"; grep -v "^ *$n " "$st/ps" > "$st/ps.new"; mv "$st/ps.new" "$st/ps"; exit 0 ;;
               slow) printf 'SDK_BOOTSTRAP=/env\n[fp] job=%s\n@@ev {"type": "meta", "budget": {"max_turns": 20}}\n' "$n"; exit 0 ;;
               partial) if (( c == 1 )); then printf 'SDK_BOOTSTRAP=/env\n[fp] job=%s\n@@ev {"type": "meta", "budget": {"max_turns": 20}}\n@@ev {"type": "summary", "verdict": "veri' "$n"; exit 0; fi ;;
             esac
             printf 'SDK_BOOTSTRAP=/env\n[fp] job=%s\n@@ev {"type": "meta", "budget": {"max_turns": 20}}\n@@pd {"turn": 1, "d": "Let me "}\n@@pd {"turn": 1, "d": "look."}\n@@ev {"type": "model", "turn": 1, "text": "{}", "prompt_tokens": 5, "completion_tokens": 3}\n' "$n"
             printf '@@ev {"type": "summary", "verdict": "verified", "reason": "match", "turns": 1, "wall_s": 2.0}\n[fp] done: /x\n' ;;
      chat) if [[ -n "${FAKE_QUEUE_CHAT:-}" ]]; then echo "cat: slurm_log/$f: No such file or directory"; exit 0; fi
            printf 'SDK_BOOTSTRAP=/env\n[fp] job=%s node=10-0-9-9 mode=chat batch=\n' "$n"; printf '@@delta "Hello "\n@@delta "from Qwen"\n@@ev {"type": "chat_tool", "cmd": "ls", "exit_code": 0, "output": "a"}\n@@reply {"answer": "Hello from Qwen", "rounds": 1, "steps": []}\n[fp] done: chat\n' ;;
    esac ;;
  download) d="${@: -1}/$(basename "${@: -2:1}")"; mkdir -p "$d"; echo "download $*" >> "$st/record"
            k=$(( $(cat "$st/dl" 2>/dev/null || echo 0) + 1 )); echo "$k" > "$st/dl"
            if [[ -z "${FAKE_DL_PARTIAL:-}" || "$k" -gt 1 ]]; then echo '{}' > "$d/runs.jsonl"; fi ;;
  cancel) echo "cancel $3" >> "$st/record"
          if [[ -n "${FAKE_CANCEL_FAILS:-}" ]]; then k=$(( $(cat "$st/cfail" 2>/dev/null || echo 0) + 1 )); echo "$k" > "$st/cfail"
            if (( k <= FAKE_CANCEL_FAILS )); then echo "[Tcloud Error] tcloudcli.go:140: Failed to create remote session"; exit 1; fi; fi
          if [[ -z "${FAKE_CANCEL_STUCK:-}" && -f "$st/ps" ]]; then grep -v "^ *$3 " "$st/ps" > "$st/ps.new"; mv "$st/ps.new" "$st/ps"; fi ;;
  ps) echo "             JOBID PARTITION     NAME     USER ST       TIME  NODES NODELIST(REASON)"; cat "$st/ps" 2>/dev/null ;;
esac
"""


class TestFlakySession(unittest.TestCase):
    """tcloud 偶尔建不起会话（"Failed to create remote session"）：自动重试；一直不行就说清楚是临时故障。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-tacc-flaky-"))
        (self.tmp / "state").mkdir()
        f = self.tmp / "submit.sh"
        f.write_text(SUBMIT, encoding="utf-8")
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
        self.env = mock.patch.dict(os.environ, {"FLAGPILOT_TACC_SUBMIT": str(f), "FAKE_STATE": str(self.tmp / "state"),
                                               "FLAGPILOT_TACC_RETRY_WAIT": "0"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_session_that_fails_twice_is_retried(self):
        os.environ["FAKE_SESSION_FAILS"] = "2"
        jid, why = tacc_live._submit("batch", {})
        self.assertEqual((jid, why), ("501", ""))
        self.assertEqual((self.tmp / "state" / "sessfail").read_text().strip(), "3")

    def test_a_session_that_never_opens_says_it_is_temporary(self):
        os.environ["FAKE_SESSION_FAILS"] = "99"
        jid, why = tacc_live._submit("batch", {})
        self.assertEqual(jid, "")
        self.assertIn("could not open a session", why)
        self.assertIn("try again in a minute", why)
        self.assertEqual((self.tmp / "state" / "sessfail").read_text().strip(), str(tacc_live.SUBMIT_TRIES))

    def test_other_failures_are_not_retried(self):
        os.environ["FAKE_SUBMIT_FAIL"] = "unknown challenge: nope"
        jid, why = tacc_live._submit("batch", {})
        self.assertEqual((jid, why), ("", "unknown challenge: nope"))


class TaccHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-tacc-live-"))
        (self.tmp / "state").mkdir()
        for name, body in (("submit.sh", SUBMIT), ("tcloud", TCLOUD)):
            f = self.tmp / name
            f.write_text(body, encoding="utf-8")
            f.chmod(f.stat().st_mode | stat.S_IEXEC)
        self.env = mock.patch.dict(os.environ, {
            "FLAGPILOT_TACC_SUBMIT": str(self.tmp / "submit.sh"), "TCLOUD_BIN": str(self.tmp / "tcloud"),
            "FAKE_STATE": str(self.tmp / "state"), "FLAGPILOT_TACC_POLL": "0.02", "FLAGPILOT_TACC_TICK": "0.02",
            "FLAGPILOT_TACC_CANCEL_EVERY": "0.05", "FLAGPILOT_TACC_CANCEL_LIMIT": "1"})
        self.env.start()
        os.environ["FLAGPILOT_TASKS"] = str(Path(__file__).resolve().parent.parent / "demo-tasks")   # 核对题目用的本机题库（固定，不受 verify.sh 影响）
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
        try:
            return (self.tmp / "state" / "record").read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""


class TaccLiveCase(TaccHarness):

    def test_session_solve_chat_and_stop(self):
        self.ready()
        evs = []
        said = []
        d = tacc_live.run_task("demo-001", "B3", self.tmp / "runs", evs.append, on_delta=lambda t, x: said.append((t, x)))
        self.assertEqual(said, [(1, "Let me "), (1, "look.")], "the model's text streams in before its turn ends")
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
        # 集群上的作业带 --scope core 选题：这道题必须选得中（2026-11-21 babyrop 两次都"没有匹配到题目"）
        from flagpilot.tasks import load_index, select
        idx = self.tmp / "staged-set"
        (idx / "index").mkdir(parents=True)
        (idx / "index" / "tasks.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
        self.assertEqual([x.name for x in select(load_index(idx), names=[alias], scope="core")], [alias])

    def test_batch_names_stay_unique_for_long_challenge_names(self):
        names = {tacc_live.batch_name("custom-20261120-230626-babyrop-97be") for _ in range(20)}
        self.assertEqual(len(names), 20, "the random suffix must survive the 40-character limit")
        for n in names:
            self.assertLessEqual(len(n), 40)
            self.assertRegex(n, r"^web-custom-20261120-230626-babyr[a-z0-9-]*-[0-9a-f]{5}$")

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
            def fake_chat(m, h, t, tools, on_delta, on_tool, on_stage=None, cancelled=None):
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


class TestRobustness(TaccHarness):
    """2026-11-21 on the real cluster: every failure here once reached the page as "no result", a dead start or a hang."""

    def events(self, task="demo-001", cancel_after=None, **env):
        os.environ.update(env)
        if tacc_live.S["phase"] != "ready":
            self.ready()
        evs, said, flag = [], [], {"x": False}
        if cancel_after is not None:
            threading.Timer(cancel_after, lambda: flag.update(x=True)).start()
        d = tacc_live.run_task(task, "B3", self.tmp / "runs", evs.append, on_delta=lambda t, x: said.append(x),
                               cancelled=lambda: flag["x"])
        return d, evs, said

    def test_the_harmless_vllm_traceback_is_not_a_crash(self):
        os.environ["FAKE_SERVE_WARN"] = "3"            # three looks land between the warning and "ready"
        self.ready()
        self.assertNotIn("cancel 501", self.record(), "the healthy server must not be cancelled")
        self.assertEqual(self.record().count("serve job="), 1)

    def test_a_node_whose_gpus_are_taken_is_excluded_and_the_start_retried(self):
        os.environ["FAKE_BUSY_JOB"] = "501"
        self.ready()
        subs = [l for l in self.record().splitlines() if l.startswith("serve job=")]
        self.assertEqual(len(subs), 2)
        self.assertTrue(subs[1].endswith(",10-0-9-8"))
        self.assertIn("cancel 501", self.record())

    def test_a_serve_job_that_vanishes_fails_the_start_with_a_reason(self):
        os.environ.update(FAKE_SERVE_VANISH_JOB="501", FLAGPILOT_TACC_ALIVE_EVERY="0")
        tacc_live.start()
        t = time.time()
        while time.time() - t < 5 and tacc_live.status()["phase"] == "starting":
            time.sleep(0.02)
        s = tacc_live.status()
        self.assertEqual(s["phase"], "failed")
        self.assertIn("serve job 501 stopped", s["error"])
        self.assertEqual(s["progress"]["detail"].get("weights_pct"), 40)

    def test_start_up_progress_is_reported_step_by_step(self):
        self.ready()
        p = tacc_live.status()["progress"]
        self.assertEqual(p["step"], "ready")
        self.assertEqual([x["id"] for x in p["steps"]][:3], ["queue", "node", "vllm"])
        self.assertTrue(all(x["state"] in ("done", "todo") for x in p["steps"]), p["steps"])
        self.assertEqual(p["detail"]["node"], "10-0-9-9")
        self.assertEqual(p["eta"], 0)
        self.assertGreater(tacc_live.watch()["progress"]["typical"], 200)

    def test_a_job_that_finds_no_challenge_says_so(self):
        d, evs, _ = self.events(FAKE_BATCH="nomatch")
        self.assertIsNone(d)
        self.assertEqual(evs[-1]["type"], "error")
        self.assertIn("not in the job's task set", evs[-1]["message"])

    def test_a_half_written_line_is_read_once_it_is_complete(self):
        d, evs, _ = self.events(FAKE_BATCH="partial")
        self.assertIsNotNone(d)
        self.assertEqual([e["type"] for e in evs].count("summary"), 1)
        self.assertEqual(evs[-1]["verdict"], "verified")

    def test_a_failed_log_read_neither_loses_nor_repeats_events(self):
        d, evs, said = self.events(FAKE_CAT_FLAKE="1")
        self.assertIsNotNone(d)
        types = [e["type"] for e in evs if e["type"] != "tacc"]
        self.assertEqual(types, ["meta", "model", "summary"])
        self.assertEqual(said, ["Let me ", "look."])

    def test_a_job_that_vanishes_without_finishing_is_noticed(self):
        with mock.patch.object(tacc_live, "REASON_EVERY_S", 0):
            t = time.time()
            d, evs, _ = self.events(FAKE_BATCH="vanish")
        self.assertIsNone(d)
        self.assertLess(time.time() - t, 10, "noticed at once, not after 30 minutes")
        self.assertIn("ran no attempt", evs[-1]["message"])

    def test_finish_cancels_the_job_and_frees_the_slot_once_it_has_stopped(self):
        d, evs, _ = self.events(FAKE_BATCH="slow", cancel_after=0.3)
        self.assertIsNone(d)
        self.assertNotIn("error", [e["type"] for e in evs], "finishing is not an error")
        t = time.time()
        while time.time() - t < 5 and tacc_live.S["busy"]:
            time.sleep(0.02)
        self.assertEqual(tacc_live.S["busy"], "")
        self.assertIn("cancel 502", self.record())

    def test_a_session_about_to_end_asks_for_a_new_one_instead_of_submitting(self):
        self.ready()
        tacc_live.S["t0"] = time.time() - tacc_live.SESSION_S + 60          # one minute left
        evs = []
        self.assertIsNone(tacc_live.run_task("demo-001", "B3", self.tmp / "runs", evs.append))
        self.assertTrue(evs[-1].get("renew"))
        self.assertRegex(evs[-1]["message"], r"ends in (59|60) s")
        rep = tacc_live.chat("hi", [], "", True, lambda d: None, lambda e: None)
        self.assertTrue(rep.get("renew"))
        self.assertNotIn("batch job=", self.record())
        self.assertNotIn("chat job=", self.record())

    def test_a_challenge_outside_the_job_scope_is_staged_with_it(self):
        root = self.tmp / "set"
        (root / "index").mkdir(parents=True)
        (root / "extra-001").mkdir()
        (root / "extra-001" / "data.txt").write_text("x")
        (root / "index" / "tasks.jsonl").write_text(json.dumps({"task_id": 1, "name": "extra-001", "category": "misc",
            "title": "t", "query": "q", "gold_sha256": "0" * 64, "assets": ["data.txt"], "assets_dir": "extra-001",
            "scope": "extra-gs"}) + "\n")
        os.environ["FLAGPILOT_TASKS"] = str(root)
        d, evs, _ = self.events("extra-001")
        self.assertIsNotNone(d)
        staged = (self.tmp / "state" / "staged").read_text(encoding="utf-8")
        row = [json.loads(l) for l in staged.splitlines() if l.startswith("{") and "extra-001" in l][-1]   # the batch job's copy
        self.assertEqual((row["name"], row["scope"]), ("extra-001", "core"))
        self.assertIn("tasks=extra-001 ", self.record())

    def test_an_unknown_challenge_is_stopped_before_submitting(self):
        d, evs, _ = self.events("no-such-challenge")
        self.assertIsNone(d)
        self.assertIn("cannot run on TACC", evs[-1]["message"])
        self.assertNotIn("batch job=", self.record())

    def test_recalling_a_question_cancels_its_job(self):
        self.ready()
        os.environ["FAKE_QUEUE_CHAT"] = "1"
        flag = {"x": False}
        threading.Timer(0.3, lambda: flag.update(x=True)).start()
        rep = tacc_live.chat("hi", [], "", True, lambda d: None, lambda e: None, cancelled=lambda: flag["x"])
        self.assertTrue(rep.get("cancelled"))
        t = time.time()
        while time.time() - t < 5 and "cancel 502" not in self.record():
            time.sleep(0.02)
        self.assertIn("cancel 502", self.record())

    def test_submission_retries_are_shown_while_they_happen(self):
        self.ready()
        os.environ.update(FAKE_SESSION_FAILS="2", FLAGPILOT_TACC_RETRY_WAIT="0")
        (self.tmp / "state" / "sessfail").write_text("0")
        stages = []
        rep = tacc_live.chat("hi", [], "", True, lambda d: None, lambda e: None, stages.append)
        self.assertEqual(rep["answer"], "Hello from Qwen")
        notes = [s for s in stages if s["stage"] == "retrying"]
        self.assertEqual(len(notes), 2)
        self.assertIn("trying again", notes[0]["message"])

    def test_a_rerun_hint_reaches_the_tacc_job_in_the_statement(self):
        self.ready()
        d = tacc_live.run_task("demo-001", "B3", self.tmp / "runs", lambda e: None, hint="look at the header")
        self.assertIsNotNone(d)
        staged = (self.tmp / "state" / "staged").read_text(encoding="utf-8")
        row = [json.loads(l) for l in staged.splitlines() if l.startswith("{") and '"demo-001"' in l][-1]
        self.assertTrue(row["query"].endswith("Hint from the user: look at the header"), row["query"][-80:])
        self.assertEqual(row["scope"], "core")
        self.assertIn("tasks=demo-001 ", self.record())

    # 2026-11-21 on the real cluster: a question recalled while TACC's gateway kept dropping its submission went on
    # being retried for minutes, even after the session had been stopped, into the working directory the next job uses
    def flaky_gateway(self):
        os.environ.update(FAKE_SESSION_FAILS="99", FLAGPILOT_TACC_RETRY_WAIT="0.3")   # pauses 0.3, 0.6, 1.2, 1.8, 2.7 s
        (self.tmp / "state" / "sessfail").write_text("0")

    def tries(self):
        return int((self.tmp / "state" / "sessfail").read_text().strip())

    def test_a_recalled_question_stops_retrying_its_submission(self):
        self.ready()
        self.flaky_gateway()
        flag = {"x": False}

        def stage(ev):                                  # recalled while the submission is being retried
            if ev.get("stage") == "retrying":
                flag["x"] = True
        t = time.time()
        rep = tacc_live.chat("hi", [], "", True, lambda d: None, lambda e: None, stage, cancelled=lambda: flag["x"])
        self.assertTrue(rep.get("cancelled"), rep)
        self.assertLess(time.time() - t, 2.0)
        self.assertEqual(self.tries(), 1, "no try after the recall")
        self.assertEqual(tacc_live.S["busy"], "")

    def test_a_finished_run_stops_retrying_its_submission(self):
        self.ready()
        self.flaky_gateway()
        flag, evs = {"x": False}, []

        def on_event(ev):
            evs.append(ev)
            if ev.get("stage") == "retrying":           # Finish while the submission is being retried
                flag["x"] = True
        t = time.time()
        d = tacc_live.run_task("demo-001", "B3", self.tmp / "runs", on_event, cancelled=lambda: flag["x"])
        self.assertIsNone(d)
        self.assertLess(time.time() - t, 2.0)
        self.assertNotIn("error", [e["type"] for e in evs], "finishing is not an error")
        self.assertEqual(self.tries(), 1, "no try after Finish")
        self.assertEqual(tacc_live.S["busy"], "")

    def test_stopping_the_session_stops_a_submission_being_retried(self):
        self.ready()
        self.flaky_gateway()

        def stage(ev):
            if ev.get("stage") == "retrying" and tacc_live.S["phase"] == "ready":
                tacc_live.stop()
        t = time.time()
        rep = tacc_live.chat("hi", [], "", True, lambda d: None, lambda e: None, stage)
        self.assertIn("session ended", rep.get("error", ""), rep)
        self.assertLess(time.time() - t, 2.0)
        self.assertEqual(self.tries(), 1, "no try after the session was stopped")
        self.assertEqual(tacc_live.S["busy"], "")

    def test_a_job_submitted_after_a_recall_is_cancelled_at_once(self):
        self.ready()
        os.environ["FAKE_SUBMIT_SLEEP"] = "1"           # the try in flight finishes after the recall
        flag, stages = {"x": False}, []

        def stage(ev):
            stages.append(ev)
            if ev.get("stage") == "uploading":
                threading.Timer(0.3, lambda: flag.update(x=True)).start()
        rep = tacc_live.chat("hi", [], "", True, lambda d: None, lambda e: None, stage, cancelled=lambda: flag["x"])
        self.assertTrue(rep.get("cancelled"), rep)
        self.assertNotIn("queued", [s["stage"] for s in stages], "a recalled question is never shown as queued")
        t = time.time()
        while time.time() - t < 5 and (tacc_live.S["busy"] or "cancel 502" not in self.record()):
            time.sleep(0.02)
        self.assertIn("cancel 502", self.record())
        self.assertEqual(tacc_live.S["busy"], "", "the slot is freed once the job has been cancelled")

    def test_stopping_a_starting_session_stops_its_submission_retries(self):
        self.flaky_gateway()
        self.assertTrue(tacc_live.start()["ok"])
        t = time.time()
        while time.time() - t < 5 and not any("trying again" in l for l in tacc_live.status()["log"]):
            time.sleep(0.01)
        tacc_live.stop()
        t = time.time()
        while time.time() - t < 5 and tacc_live.status()["phase"] != "idle":
            time.sleep(0.02)
        self.assertEqual(tacc_live.status()["phase"], "idle", "stopped, not failed")
        self.assertLess(time.time() - t, 1.5)
        time.sleep(0.5)
        self.assertEqual(self.tries(), 1, "no try after the stop")
        self.assertEqual(tacc_live.status()["error"], "")

    def test_a_session_started_again_is_not_taken_over_by_the_old_submission(self):
        os.environ["FAKE_SUBMIT_SLEEP"] = "0.6"         # each serve submission takes 0.6 s
        self.assertTrue(tacc_live.start()["ok"])        # submits job 501 ...
        time.sleep(0.2)
        tacc_live.stop()                                # ... stopped while that try is in flight ...
        self.assertTrue(tacc_live.start()["ok"])        # ... and started again at once: job 502
        t = time.time()
        while time.time() - t < 8 and not ("cancel 501" in self.record() and tacc_live.status()["phase"] == "ready"):
            time.sleep(0.02)
        s = tacc_live.status()
        self.assertEqual((s["phase"], s["serve"]), ("ready", "502"), s.get("log"))
        self.assertIn("cancel 501", self.record(), "the old submission's job is cancelled")
        self.assertNotIn("cancel 502", self.record())
        time.sleep(0.3)
        self.assertEqual(tacc_live.status()["serve"], "502", "the old loop never writes over the new session")

    def test_a_log_read_that_hangs_is_given_up_quickly(self):
        # 2026-11-21: 1 in 38 `tcloud cat` calls hung for 142 s (the gateway never opened the SSH session); with a 120 s
        # timeout the page showed "queued" while short jobs ran to the end, then every event at once
        self.ready()
        os.environ.update(FAKE_CAT_HANG="4", FLAGPILOT_TACC_READ_TIMEOUT="0.5")
        (self.tmp / "state" / "hang").write_text("0")
        t = time.time()
        d, evs, _ = self.events()
        self.assertIsNotNone(d)
        self.assertEqual(evs[-1]["type"], "summary")
        self.assertLess(time.time() - t, 3.0, "the hung read was abandoned, not waited out")

    def test_a_half_finished_download_is_tried_again(self):
        os.environ.update(FAKE_DL_PARTIAL="1", FLAGPILOT_TACC_RETRY_WAIT="0")
        dest = self.tmp / "dl"
        dest.mkdir()
        self.assertTrue(tacc_live._download("flagpilot-runs/web-x-B3", dest, expect="runs.jsonl"))
        self.assertEqual(self.record().count("download -r"), 2, "the folder without its ledger did not count")
        self.assertTrue((dest / "web-x-B3" / "runs.jsonl").exists())

    # 2026-11-21, job 30516: a question recalled while TACC's gateway was dropping calls replied anyway. The loop only
    # looked at the recall between reads (each can hang up to the read timeout), and each cancel waited for three
    # Slurm listings with up to three tries each before the next cancel.
    def test_a_recall_cancels_the_job_at_once_even_while_a_read_hangs(self):
        self.ready()
        os.environ.update(FAKE_CAT_HANG="3", FLAGPILOT_TACC_READ_TIMEOUT="10", FLAGPILOT_TACC_TICK="0.02")
        (self.tmp / "state" / "hang").write_text("0")
        flag = {"x": False}

        def stage(ev):
            if ev.get("stage") == "queued":             # recalled while the first log read hangs
                threading.Timer(0.4, lambda: flag.update(x=True)).start()
        t = {}
        threading.Thread(target=lambda: t.update(rep=tacc_live.chat("hi", [], "", True, lambda d: None, lambda e: None, stage,
                                                                   cancelled=lambda: flag["x"])), daemon=True).start()
        t0 = time.time()
        while time.time() - t0 < 5 and not flag["x"]:
            time.sleep(0.01)
        t_rec = time.time()
        while time.time() - t_rec < 5 and "cancel 502" not in self.record():
            time.sleep(0.01)
        self.assertIn("cancel 502", self.record())
        self.assertLess(time.time() - t_rec, 1.5, "cancelled without waiting for the hung read")
        t0 = time.time()
        while time.time() - t0 < 8 and ("rep" not in t or tacc_live.S["busy"]):
            time.sleep(0.02)
        self.assertTrue(t["rep"].get("cancelled"), t.get("rep"))
        self.assertEqual(tacc_live.S["busy"], "", "the slot is freed once, after the job is gone")

    def test_a_finished_run_is_cancelled_at_once_even_while_a_read_hangs(self):
        self.ready()
        os.environ.update(FAKE_CAT_HANG="3", FLAGPILOT_TACC_READ_TIMEOUT="10", FLAGPILOT_TACC_TICK="0.02")
        (self.tmp / "state" / "hang").write_text("0")
        flag, evs = {"x": False}, []

        def on_event(ev):
            evs.append(ev)
            if ev.get("stage") == "queued":
                threading.Timer(0.4, lambda: flag.update(x=True)).start()
        threading.Thread(target=lambda: tacc_live.run_task("demo-001", "B3", self.tmp / "runs", on_event,
                                                           cancelled=lambda: flag["x"]), daemon=True).start()
        t0 = time.time()
        while time.time() - t0 < 5 and not flag["x"]:
            time.sleep(0.01)
        t_fin = time.time()
        while time.time() - t_fin < 5 and "cancel 502" not in self.record():
            time.sleep(0.01)
        self.assertIn("cancel 502", self.record())
        self.assertLess(time.time() - t_fin, 1.5, "cancelled without waiting for the hung read")
        t0 = time.time()
        while time.time() - t0 < 8 and tacc_live.S["busy"]:
            time.sleep(0.02)
        self.assertEqual(tacc_live.S["busy"], "")
        self.assertNotIn("error", [e["type"] for e in evs])

    def test_a_cancel_the_gateway_drops_is_sent_again(self):
        st = self.tmp / "state"
        (st / "ps").write_text("             779      tacc run.slur   yliutz  R      10:00      1 10-0-3-11\n")
        os.environ["FAKE_CANCEL_FAILS"] = "2"
        self.assertTrue(tacc_live._cancel("779"))
        self.assertEqual(self.record().count("cancel 779"), 3)

    def test_a_run_on_tacc_gets_the_turns_chosen_on_the_page_and_time_for_them(self):
        self.ready()
        self.assertIsNotNone(tacc_live.run_task("demo-001", "B3", self.tmp / "runs", lambda e: None))
        self.assertIsNotNone(tacc_live.run_task("demo-001", "B3", self.tmp / "runs", lambda e: None, turns=100))
        rows = [l for l in (self.tmp / "state" / "budget").read_text().splitlines() if l.startswith("batch ")]
        self.assertTrue(rows[0].endswith("turns=20 time=00:30:00"), rows)
        self.assertTrue(rows[1].endswith("turns=100 time=00:50:00"), rows)

    def test_the_watchdog_notices_a_service_that_died(self):
        self.ready()
        os.environ.update(FLAGPILOT_TACC_WATCH="0.02", FLAGPILOT_TACC_ALIVE_EVERY="0")
        threading.Thread(target=tacc_live._watchdog, args=("501",), daemon=True).start()
        (self.tmp / "state" / "ps").write_text("")
        t = time.time()
        while time.time() - t < 5 and tacc_live.status()["phase"] == "ready":
            time.sleep(0.02)
        self.assertEqual(tacc_live.status()["phase"], "failed")
        self.assertIn("has ended on TACC", tacc_live.status()["error"])


if __name__ == "__main__":
    unittest.main()
