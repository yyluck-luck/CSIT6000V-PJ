"""app/tacc_log.py: what the web app reads out of TACC job logs (real serve log from results/tacc/v6 as the fixture)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import tacc_log  # noqa: E402

SERVE_LOG = Path(__file__).resolve().parent.parent / "results" / "tacc" / "v6" / "serve-30221.log"


class TestMarked(unittest.TestCase):
    def test_a_half_written_last_line_waits_for_the_next_read(self):
        log = 'x\n@@ev {"type": "meta"}\n@@ev {"type": "summary", "verd'
        self.assertEqual(tacc_log.marked(log, "@@ev "), ['{"type": "meta"}'])
        self.assertEqual(len(tacc_log.marked(log + 'ict": "verified"}\n', "@@ev ")), 2)

    def test_a_bad_line_in_the_middle_is_kept_in_the_count(self):
        # only the last line can still be being written; a broken line earlier is skipped by the reader, not re-read forever
        got = tacc_log.marked('@@ev {oops\n@@ev {"a": 1}\n', "@@ev ")
        self.assertEqual(len(got), 2)
        self.assertIsNone(tacc_log.parse(got[0]))
        self.assertEqual(tacc_log.parse(got[1]), {"a": 1})

    def test_is_log_tells_a_job_log_from_an_sdk_error(self):
        self.assertTrue(tacc_log.is_log("SDK_BOOTSTRAP=/env\n[fp] job=1\n"))
        self.assertFalse(tacc_log.is_log("[Tcloud Error] tcloudcli.go:140: Failed to create remote session"))
        self.assertFalse(tacc_log.is_log(""))


class TestWhyNoResult(unittest.TestCase):
    def test_each_known_cause_reads_as_one_plain_sentence(self):
        cases = {
            "SDK_BOOTSTRAP=/e\n[fp] job=1\n没有匹配到题目\n[fp] done: x": "not in the job's task set",
            "SDK_BOOTSTRAP=/e\n[fp] skip B3: 95s left, needs ~240s\n[fp] done: x": "only 95 s of the Qwen session",
            "SDK_BOOTSTRAP=/e\n[fp] service=http://10.0.3.11:8000/v1 (10-0-3-11)\nurllib.error.URLError: <urlopen error [Errno 111] Connection refused>":
                "did not answer",
            "SDK_BOOTSTRAP=/e\nslurmstepd: error: *** JOB 5 CANCELLED AT 2026 DUE TO TIME LIMIT ***": "time limit",
            "SDK_BOOTSTRAP=/e\nslurmstepd: error: *** JOB 5 ON n CANCELLED AT 2026-11-21T17:19:14 ***": "cancelled on TACC",
            "": "ended before it started running",
        }
        for log, want in cases.items():
            got = tacc_log.why_no_result(log, "5")
            self.assertIn(want, got, log)
            self.assertNotRegex(got, r"[　-鿿]", "never Chinese on the page")

    def test_otherwise_the_last_line_and_where_the_full_log_is(self):
        got = tacc_log.why_no_result("SDK_BOOTSTRAP=/e\n[fp] job=5 node=n\nsomething odd happened\n", "5")
        self.assertIn("something odd happened", got)
        self.assertIn("tcloud cat slurm_log/flagpilot-client-5.log", got)


@unittest.skipUnless(SERVE_LOG.exists(), "needs results/tacc/v6/serve-30221.log")
class TestServeProgress(unittest.TestCase):
    def setUp(self):
        self.log = SERVE_LOG.read_text(encoding="utf-8", errors="replace")

    def test_a_full_start_up_reaches_ready_with_its_details(self):
        p = tacc_log.serve_progress(self.log)
        self.assertEqual(p["step"], "ready")
        d = p["detail"]
        self.assertEqual(d["node"], "10-0-3-10")
        self.assertEqual(d["gpus"], "2 GPUs, 24 GB each")
        self.assertAlmostEqual(d["weights_gib"], 19.57)
        self.assertEqual(d["weights_pct"], 100)
        self.assertAlmostEqual(d["compile_s"], 43.1)
        self.assertEqual(d["kv_tokens"], 320170)
        self.assertEqual(d["vllm"], "0.26.0")
        self.assertTrue(p["tail"])
        self.assertFalse(any("█" in l or "pid=" in l for l in p["tail"]))

    def test_each_step_is_recognised_as_the_log_grows(self):
        lines = self.log.splitlines()
        seen = []
        for n in range(1, 190):
            s = tacc_log.serve_progress("\n".join(lines[:n]))["step"]
            if s and (not seen or seen[-1] != s):
                seen.append(s)
        self.assertEqual(seen, [x[0] for x in tacc_log.STEPS], "every step, in order, none skipped")

    def test_the_harmless_traceback_warning_does_not_end_the_start_up(self):
        # line ~90 of every vLLM start-up: "WARNING ... Traceback (most recent call last):" (deep_gemm import), then "ready"
        head = "\n".join(self.log.splitlines()[:100])
        self.assertIn("Traceback (most recent call last)", head)
        self.assertNotEqual(tacc_log.serve_progress(head)["step"], "ready")


class TestEta(unittest.TestCase):
    def test_time_left_falls_as_steps_complete_and_never_hits_zero_early(self):
        ids = [s[0] for s in tacc_log.STEPS]
        left = [tacc_log.eta_s(s, 0) for s in ids]
        self.assertEqual(left, sorted(left, reverse=True))
        self.assertEqual(tacc_log.eta_s("ready", 0), 0)
        self.assertEqual(tacc_log.eta_s("compile", 10_000), 5)
        self.assertEqual(tacc_log.eta_s("", 0), tacc_log.TYPICAL_S)


if __name__ == "__main__":
    unittest.main()
