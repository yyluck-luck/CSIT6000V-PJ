"""deploy/tcloud/pipeline.sh against a fake SDK: the cluster behaviours that broke evaluation runs on 2026-11-21.

  * every vLLM start-up prints a harmless "Traceback (most recent call last)" warning about 12 s before it is ready:
    the pipeline must keep waiting, not cancel a healthy server;
  * a server that really fails because its node's GPUs are taken is cancelled and retried on another node;
  * an SDK error in place of a log (TACC's login gateway dropping the SSH handshake) is not a log.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent.parent / "deploy" / "tcloud" / "pipeline.sh"

SUB = r"""#!/usr/bin/env bash
st="$FAKE_STATE"; n=$(( $(cat "$st/jobn" 2>/dev/null || echo 600) + 1 )); echo "$n" > "$st/jobn"
echo "$1" > "$st/mode-$n"; echo "submit $1 $n excl=${FP_EXCLUDE:-}" >> "$st/record"
printf '%18s      tacc run.slur   yliutz  R       0:01      1 10-0-9-9\n' "$n" >> "$st/ps"
echo "Submitted batch job $n"
"""

TCLOUD = r"""#!/usr/bin/env bash
st="$FAKE_STATE"
case "$1" in
  ps) k=$(( $(cat "$st/psn" 2>/dev/null || echo 0) + 1 )); echo "$k" > "$st/psn"
      if [[ -n "${FAKE_PS_FLAKE:-}" ]] && (( k % 2 == 0 )); then echo "[Tcloud Error] tcloudcli.go:140: Failed to create remote session"; exit 1; fi
      echo "             JOBID PARTITION     NAME     USER ST       TIME  NODES NODELIST(REASON)"; cat "$st/ps" 2>/dev/null ;;
  cat) n=$(echo "$2" | sed 's/[^0-9]//g'); c=$(( $(cat "$st/cat-$n" 2>/dev/null || echo 0) + 1 )); echo "$c" > "$st/cat-$n"
       if [[ -n "${FAKE_CAT_FLAKE:-}" ]] && (( c == 2 )); then echo "[Tcloud Error] tcloudcli.go:140: Failed to create remote session"; exit 1; fi
       case "$(cat "$st/mode-$n")" in
         serve) if [[ "$n" == "${FAKE_BUSY:-}" ]]; then
                  printf 'SDK_BOOTSTRAP=/env\nSERVICE_BASE_URL=http://10-0-9-8:8000/v1\nValueError: Free memory on device (5.1/23.59 GiB) on startup is less than desired GPU memory utilization (0.9, 21.23 GiB).\nsrun: error: 10-0-9-8: task 0: Exited with exit code 1\n'; exit 0; fi
                printf 'SDK_BOOTSTRAP=/env\nSERVICE_BASE_URL=http://10-0-9-9:8000/v1\n'
                printf '(Worker_TP0 pid=9) WARNING 11-21 16:27:34 [import_utils.py:408] Traceback (most recent call last):\n'
                printf '(Worker_TP0 pid=9) INFO 11-21 16:27:34 [gpu_worker.py:857] Free memory on device (23.19/23.59 GiB) on startup.\n'
                (( c > ${FAKE_WARN_LOOKS:-2} )) && printf 'INFO:     Application startup complete.\n' ;;
         batch) printf 'SDK_BOOTSTRAP=/env\n[fp] job=%s\n[fp] tier B3 -> b-B3 (3000s left)\n[1/2] t-001 ok\n[fp] tier-done b-B3 12s\n[fp] done: /runs/b\n' "$n" ;;
       esac ;;
  cancel) echo "cancel $3" >> "$st/record"; grep -v "^ *$3 " "$st/ps" > "$st/ps.new"; mv "$st/ps.new" "$st/ps" ;;
  download) if [[ "$2" == "-r" ]]; then mkdir -p "$4/$(basename "$3")"; else : > "$3/$(basename "$2")"; fi
            echo "download $*" >> "$st/record" ;;
esac
"""


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-pipeline-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "state").mkdir()
        for name, body in (("sub.sh", SUB), ("tcloud", TCLOUD)):
            f = self.tmp / name
            f.write_text(body, encoding="utf-8")
            f.chmod(f.stat().st_mode | stat.S_IEXEC)

    def run_pipeline(self, **env):
        e = {**os.environ, "SUB": str(self.tmp / "sub.sh"), "TCLOUD_BIN": str(self.tmp / "tcloud"), "POLL": "0", "PIPE_WAIT": "0",
             "FAKE_STATE": str(self.tmp / "state"), "FP_BATCH": "b", "FP_ABLATIONS": "B3", "FP_LIMIT": "0", **env}
        r = subprocess.run(["bash", str(PIPELINE), str(self.tmp / "out"), "10-0-1-10"], capture_output=True, text=True,
                           env=e, timeout=120)
        return r, (self.tmp / "state" / "record").read_text(encoding="utf-8")

    def test_the_harmless_traceback_warning_does_not_kill_a_healthy_server(self):
        r, rec = self.run_pipeline(FAKE_WARN_LOOKS="3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(rec.count("submit serve"), 1, rec)
        lines = rec.splitlines()
        self.assertLess(lines.index("submit batch 602 excl=10-0-9-9"), lines.index("cancel 601"),
                        "the server is cancelled only after the batch, by the cleanup")
        out = self.tmp / "out"
        self.assertTrue((out / "b-B3").is_dir())
        self.assertIn("[fp] done:", (out / "client-602.log").read_text())

    def test_a_node_whose_gpus_are_taken_is_skipped(self):
        r, rec = self.run_pipeline(FAKE_BUSY="601")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("cancel 601", rec)
        self.assertIn("submit serve 602 excl=10-0-1-10,10-0-9-8", rec)

    def test_sdk_errors_in_place_of_logs_and_listings_are_ignored(self):
        r, rec = self.run_pipeline(FAKE_CAT_FLAKE="1", FAKE_PS_FLAKE="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(rec.count("submit serve"), 1, rec)
        self.assertNotIn("Tcloud Error", (self.tmp / "out" / "client-602.log").read_text())


if __name__ == "__main__":
    unittest.main()
