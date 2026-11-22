"""tests/__init__.py 的临时目录约定。

这一层没法在同一个进程里测：清理挂在 atexit 上，只有进程退出才触发。
所以每条用例都开一个子进程，跑 `import tests`（= 执行包初始化），看它
退出之后 `runs/tmp/unittest/` 变成什么样。
"""

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]          # flagpilot/
_TMP_ROOT = _ROOT / "runs" / "tmp" / "unittest"


def _spawn(code: str, **env: str) -> subprocess.CompletedProcess:
    merged = {**os.environ, "PYTHONPATH": str(_ROOT), **env}
    return subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, cwd=str(_ROOT), env=merged)


class TestTempDirOwnership(unittest.TestCase):
    def test_a_finished_run_leaves_no_directory_behind(self):
        """跑完一次测试，它自己的临时目录要被删掉。"""
        r = _spawn("import tests, tempfile; print(tempfile.gettempdir())")
        self.assertEqual(r.returncode, 0, r.stderr[-600:])
        own = Path(r.stdout.strip())
        self.assertEqual(own.parent, _TMP_ROOT, f"临时根应落在 {_TMP_ROOT}，实际 {own}")
        self.assertFalse(own.exists(), f"{own} 在进程退出后仍在")

    def test_each_process_gets_its_own_subdirectory(self):
        """两个进程不能共用一个目录，否则谁先退出谁就把别人的现场删了。"""
        code = "import tests, tempfile; print(tempfile.gettempdir())"
        a = _spawn(code)
        b = _spawn(code)
        self.assertEqual(a.returncode, 0, a.stderr[-600:])
        self.assertEqual(b.returncode, 0, b.stderr[-600:])
        self.assertNotEqual(a.stdout.strip(), b.stdout.strip())

    def test_keep_env_var_preserves_the_directory(self):
        """排查失败用例时要能留住现场。"""
        r = _spawn("import tests, tempfile; print(tempfile.gettempdir())",
                   FLAGPILOT_KEEP_TEST_TMP="1")
        self.assertEqual(r.returncode, 0, r.stderr[-600:])
        own = Path(r.stdout.strip())
        try:
            self.assertTrue(own.exists(), "FLAGPILOT_KEEP_TEST_TMP=1 时目录应保留")
        finally:
            shutil.rmtree(own, ignore_errors=True)

    def test_a_neighbouring_process_directory_is_not_touched(self):
        """并发跑两遍 discover 时，退出的一方只能删自己的。"""
        neighbour = _TMP_ROOT / "pid-999999"
        neighbour.mkdir(parents=True, exist_ok=True)
        marker = neighbour / "still-here.txt"
        marker.write_text("x", encoding="utf-8")
        try:
            r = _spawn("import tests")
            self.assertEqual(r.returncode, 0, r.stderr[-600:])
            self.assertTrue(marker.exists(), "别的进程的临时目录被删掉了")
        finally:
            shutil.rmtree(neighbour, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
