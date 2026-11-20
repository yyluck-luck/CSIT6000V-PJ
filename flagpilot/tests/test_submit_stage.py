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


if __name__ == "__main__":
    unittest.main()
