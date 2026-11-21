"""deploy/tcloud/submit.sh 的本机暂存：交完即删，dry run 与 FP_KEEP_STAGE=1 保留，退出码原样传出。

网页的 TACC 会话每条答疑、每次单题都会交一次作业；以前暂存目录从不删，一天下来 TMPDIR 里能积几百 M。
这里用假的 tcloud（放在 PATH 最前）跑真的 submit.sh，TMPDIR 指到隔离目录，数 fp-tcloud-* 就知道删没删。
题库用临时造的一道题（FLAGPILOT_TASKS），不依赖评测数据集——干净的克隆里也要能跑。
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUBMIT = ROOT / "deploy" / "tcloud" / "submit.sh"
FAKE_TCLOUD = """#!/bin/sh
ls > "$FAKE_DIR/listing"
cp payload.tgz "$FAKE_DIR/" 2>/dev/null
echo "Submitted batch job 999"
exit "${FAKE_RC:-0}"
"""


@unittest.skipUnless(shutil.which("bash"), "需要 bash")
class TestSubmitStageCleanup(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-submit-stage-"))
        self.bin, self.tmpdir = self.tmp / "bin", self.tmp / "tmpdir"
        self.bin.mkdir()
        self.tmpdir.mkdir()
        fake = self.bin / "tcloud"
        fake.write_text(FAKE_TCLOUD, encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.tasks = self.tmp / "tasks"
        (self.tasks / "index").mkdir(parents=True)
        (self.tasks / "t-001").mkdir()
        (self.tasks / "t-001" / "a.txt").write_text("x", encoding="utf-8")
        (self.tasks / "_private").mkdir()
        (self.tasks / "_private" / "gold_private.json").write_text("{}", encoding="utf-8")
        (self.tasks / "index" / "tasks.jsonl").write_text(json.dumps({"name": "t-001", "task_id": 1}) + "\n",
                                                          encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def submit(self, mode="doctor", url="", **extra):
        env = {**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
               "TMPDIR": str(self.tmpdir), "FAKE_DIR": str(self.tmp), "LANG": "en_US.UTF-8",
               "FLAGPILOT_TASKS": str(self.tasks), **extra}
        for k in ("FP_KEEP_STAGE", "FP_DRY_RUN", "FAKE_RC"):
            if k not in extra:
                env.pop(k, None)
        r = subprocess.run(["bash", str(SUBMIT), mode, *([url] if url else [])], capture_output=True, text=True, env=env,
                           cwd=str(ROOT), timeout=120)
        return r, sorted(self.tmpdir.glob("fp-tcloud-*"))

    def test_stage_is_removed_after_submit(self):
        r, left = self.submit()
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn("Submitted batch job 999", r.stdout)
        self.assertTrue((self.tmp / "listing").read_text().strip(), "提交时暂存目录应当还在")
        self.assertEqual(left, [])

    def test_code_ships_as_one_zip_and_runs_from_it(self):
        """SDK 按文件逐个上传（实测答疑作业 37 个文件、提交 35 s）：代码合成一个 flagpilot.zip，作业从 zip 里 import。"""
        import sys
        r, left = self.submit(FP_DRY_RUN="1")
        self.assertEqual((r.returncode, len(left)), (0, 1), r.stderr[-400:])
        stage = left[0] / "flagpilot-client"
        self.assertTrue((stage / "flagpilot.zip").is_file())
        self.assertFalse((stage / "flagpilot").exists())
        probe = ("import runpy, sys; sys.argv = ['run_flagpilot.py', '--help'];"
                 "runpy.run_path('run_flagpilot.py', run_name='__main__')")
        p = subprocess.run([sys.executable, "-I", "-c", probe], cwd=str(stage), capture_output=True, text=True, timeout=60,
                           env={"PATH": os.environ.get("PATH", ""), "HOME": str(self.tmp)})
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        import zipfile
        with zipfile.ZipFile(stage / "flagpilot.zip") as z:
            names = z.namelist()
        self.assertIn("flagpilot/__main__.py", names)
        self.assertFalse(any("__pycache__" in n or n.endswith(".pyc") for n in names))

    def test_upload_is_four_files(self):
        """提交那一刻只剩 SDK 要读的三个文件 + 一个 payload.tgz（以前带随包库的单题作业 200 多个文件、逐个上传）。"""
        import tarfile
        r, left = self.submit()
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        listing = sorted((self.tmp / "listing").read_text().split())
        self.assertEqual(listing, ["job.sh", "payload.tgz", "run.env", "tuxiv.conf"])
        with tarfile.open(self.tmp / "payload.tgz") as tf:
            names = [n.removeprefix("./") for n in tf.getnames()]
        for want in ("flagpilot.zip", "run_flagpilot.py", "bin/xxd", "bin/exiftool"):
            self.assertIn(want, names)
        self.assertFalse(any(n.split("/")[-1].startswith("._") for n in names), "macOS ._ 元数据混进了包")
        self.assertFalse(any("_private" in n or "gold_private" in n for n in names))

    def test_failed_submit_keeps_exit_code_and_still_cleans(self):
        r, left = self.submit(FAKE_RC="3")
        self.assertEqual(r.returncode, 3)
        self.assertEqual(left, [])

    def test_keep_and_dry_run_leave_the_stage(self):
        r, left = self.submit(FP_KEEP_STAGE="1")
        self.assertEqual((r.returncode, len(left)), (0, 1))
        shutil.rmtree(left[0])
        r, left = self.submit(FP_DRY_RUN="1")
        self.assertEqual((r.returncode, len(left)), (0, 1))
        self.assertIn(str(left[0]), r.stdout.replace("//", "/"))

    def test_batch_carries_the_frozen_split_and_no_private_files(self):
        """有团队冻结的划分就原样带上（不再现场重新生成）；题库里的 _private 不出本机。"""
        frozen = ROOT / "docs" / "splits-picoctf-53.json"
        if not frozen.exists():
            self.skipTest("没有冻结的划分文件")
        r, left = self.submit("batch", "http://10-0-0-1:8000/v1", FP_DRY_RUN="1", FP_VENDOR="0")
        self.assertEqual((r.returncode, len(left)), (0, 1), r.stderr[-400:])
        staged = left[0] / "flagpilot-client" / "tasks"
        self.assertEqual((staged / "index" / "splits.json").read_bytes(), frozen.read_bytes())
        self.assertTrue((staged / "t-001" / "a.txt").exists())
        self.assertEqual(list(staged.rglob("_private")) + list(staged.rglob("gold_private*")), [])

    def test_uploaded_challenge_copy_is_accepted_by_the_real_script(self):
        """tacc_live 给上传题造的数据集副本，真的 submit.sh 能按别名挑出来打包（中文标题也行）。"""
        import sys
        sys.path.insert(0, str(ROOT))
        from app import tacc_live
        from flagpilot import config, custom
        saved = config.RUNS_ROOT
        config.RUNS_ROOT = self.tmp / "runs"
        try:
            up = custom.create("未命名题目", "q", files=[("a.txt", b"x")], gold="picoCTF{never_uploaded}")
            alias = tacc_live._stage_upload(up.name, self.tmp / "upload-root")
        finally:
            config.RUNS_ROOT = saved
        chat = self.tmp / "chatdir"
        chat.mkdir()
        (chat / "msg.txt").write_text("你好", encoding="utf-8")
        r, left = self.submit("chat", "http://10-0-0-1:8000/v1", FP_DRY_RUN="1", FP_SPLIT="all", FP_VENDOR="0",
                              FP_CHAT_DIR=str(chat), FP_CHAT_TASK=alias, FLAGPILOT_TASKS=str(self.tmp / "upload-root"))
        self.assertEqual((r.returncode, len(left)), (0, 1), r.stderr[-400:])
        staged = left[0] / "flagpilot-client" / "tasks"
        rows = [json.loads(x) for x in (staged / "index" / "tasks.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([x["name"] for x in rows], [alias])
        self.assertTrue((staged / "tasks" / alias / "a.txt").exists())
        self.assertNotIn("never_uploaded", "".join(f.read_text(errors="replace") for f in left[0].rglob("*") if f.is_file()))


@unittest.skipUnless(shutil.which("bash"), "需要 bash")
class TestJobCleanupTrap(unittest.TestCase):
    """client/job.sh 退出时清本节点 /tmp 的运行目录：答疑沙箱把题目附件设成只读，旧的 trap 删不掉，
    作业被判 exit 1（srun: error），共享节点 /tmp 里还留文件。这里原样取出 job.sh 里那行 trap 来跑。"""

    JOB = ROOT / "deploy" / "tcloud" / "client" / "job.sh"
    OLD = """trap 'rm -rf -- "$QWEN_RUN_DIR"' EXIT"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-job-trap-"))

    def tearDown(self):
        for d, _, _ in os.walk(self.tmp):
            os.chmod(d, 0o700)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_with(self, trap_line, code):
        run = self.tmp / f"run-{code}"
        ro = run / "chat-work" / "challenge"
        ro.mkdir(parents=True)
        (ro / "trace.pcap").write_bytes(b"x")
        ro.chmod(0o555)                                  # 沙箱给的只读附件
        script = f"set -euo pipefail\nQWEN_RUN_DIR={run}\n{trap_line}\nexit {code}\n"
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
        return r.returncode, run.exists()

    def test_trap_removes_read_only_copies_and_keeps_the_exit_code(self):
        line = next(l for l in self.JOB.read_text(encoding="utf-8").splitlines() if l.startswith("trap ") and "EXIT" in l)
        self.assertEqual(self.run_with(line, 0), (0, False))
        self.assertEqual(self.run_with(line, 3), (3, False))

    def test_job_unpacks_the_payload_first(self):
        """job.sh 开头那行原样拿出来跑：解开 payload.tgz 并删掉它（/mnt/home 已满，不留双份）。"""
        import tarfile
        lines = self.JOB.read_text(encoding="utf-8").splitlines()
        line = next(l for l in lines if "payload.tgz" in l and l.startswith("if "))
        self.assertLess(lines.index(line), next(i for i, l in enumerate(lines) if l.startswith("source ./run.env")))
        work = self.tmp / "workdir"
        (work / "src" / "bin").mkdir(parents=True)
        (work / "src" / "bin" / "xxd").write_text("x")
        with tarfile.open(work / "payload.tgz", "w:gz") as tf:
            tf.add(work / "src" / "bin", arcname="./bin")
        shutil.rmtree(work / "src")
        r = subprocess.run(["bash", "-c", f"set -euo pipefail\ncd {work}\n{line}\n"], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((work / "bin" / "xxd").exists())
        self.assertFalse((work / "payload.tgz").exists())

    def test_old_trap_shows_the_bug(self):
        self.assertEqual(self.run_with(self.OLD, 0), (1, True))     # 这就是作业日志里的 srun: error


if __name__ == "__main__":
    unittest.main()
