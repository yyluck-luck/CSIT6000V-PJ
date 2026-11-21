"""deploy/tcloud/serve/supervise.sh: however the serve job ends, no vLLM process may outlive it.

A fake vLLM stands in for the course's serve script: an API process with two GPU workers, one of which ignores
SIGTERM, and a grandchild. The supervisor must stop all of them when the job is cancelled (SIGTERM) and when the API
process dies on its own, and must log the GPU memory afterwards (a fake nvidia-smi stands in for the real one).
"""

from __future__ import annotations

import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SUPERVISE = Path(__file__).resolve().parent.parent / "deploy" / "tcloud" / "serve" / "supervise.sh"

FAKE_VLLM = r"""#!/usr/bin/env bash
# API process: start two workers (the second ignores SIGTERM) and a grandchild, record their pids, then serve
d="$1"; mode="${2:-serve}"
python3 -c 'import time; time.sleep(600)' & echo $! > "$d/worker1"
python3 -c 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(600)' & echo $! > "$d/worker2"
bash -c 'python3 -c "import time; time.sleep(600)" & echo $! > "$1/grandchild"; wait' _ "$d" &
echo $$ > "$d/api"
if [ "$mode" = crash ]; then sleep 1; exit 3; fi      # the API process dies and leaves its workers behind
sleep 600
"""

FAKE_NVIDIA_SMI = "#!/usr/bin/env bash\necho '0, 1 MiB'\necho '1, 1 MiB'\n"


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # a zombie still answers kill(0): ask ps for its state
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(out) and not out.startswith("Z")


@unittest.skipUnless(shutil.which("bash") and sys.platform != "win32", "needs bash")
class TestServeSupervisor(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-supervise-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "bin").mkdir()
        for name, body in (("fake_vllm.sh", FAKE_VLLM), ("bin/nvidia-smi", FAKE_NVIDIA_SMI)):
            f = self.tmp / name
            f.write_text(body)
            f.chmod(f.stat().st_mode | stat.S_IEXEC)
        self.env = {**os.environ, "PATH": f"{self.tmp / 'bin'}:{os.environ['PATH']}", "FP_STOP_GRACE": "2"}

    def start(self, mode: str) -> subprocess.Popen:
        p = subprocess.Popen(["bash", str(SUPERVISE), "bash", str(self.tmp / "fake_vllm.sh"), str(self.tmp), mode],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=self.env,
                             start_new_session=True)
        self.addCleanup(self.kill_all)
        t0 = time.time()
        while time.time() - t0 < 10 and not all((self.tmp / n).exists() for n in ("api", "worker1", "worker2", "grandchild")):
            time.sleep(0.05)
        return p

    def pids(self) -> dict[str, int]:
        out = {}
        for n in ("api", "worker1", "worker2", "grandchild"):
            try:
                out[n] = int((self.tmp / n).read_text().strip())
            except (OSError, ValueError):
                pass
        return out

    def kill_all(self):
        for pid in self.pids().values():
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def test_cancel_stops_every_vllm_process_and_logs_the_gpus(self):
        p = self.start("serve")
        pids = self.pids()
        self.assertEqual(len(pids), 4)
        self.assertTrue(all(alive(x) for x in pids.values()))
        p.send_signal(signal.SIGTERM)                      # tcloud cancel / the time limit
        out, _ = p.communicate(timeout=20)
        self.assertEqual(p.returncode, 143)
        time.sleep(0.3)
        self.assertEqual([n for n, x in pids.items() if alive(x)], [], out)
        self.assertIn("SIGKILL", out, "the worker that ignores SIGTERM needed the hard stop")
        self.assertIn("SUPERVISE stopped group", out)
        self.assertIn("SHUTDOWN gpu", out)
        self.assertNotIn("SUPERVISE_FAIL", out)

    def test_a_crashed_api_process_does_not_leave_its_workers_behind(self):
        p = self.start("crash")
        pids = self.pids()
        out, _ = p.communicate(timeout=20)
        self.assertEqual(p.returncode, 3, out)              # the job reports vLLM's own exit status
        time.sleep(0.3)
        self.assertEqual([n for n, x in pids.items() if alive(x)], [], out)
        self.assertIn("SUPERVISE stopped group", out)


if __name__ == "__main__":
    unittest.main()
