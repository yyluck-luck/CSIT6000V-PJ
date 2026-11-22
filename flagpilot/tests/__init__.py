"""测试包的全局隔离。

`run_attempt` 现在会往"跨 run 记忆"里写一条记录（这是特性，不是副作用）。
如果不隔离，跑一次单测就会把 mock 模型的结论写进项目真实的
`runs/memory/tasks/`，之后真正解题时 prompt 里会混进一堆 mock 记录。

放在包初始化里而不是每个用例里，是因为项目用 `unittest discover`，
没有 conftest 这种统一钩子；`tests/__init__.py` 会在任何测试模块 import
flagpilot 之前执行，正好能赶在 config 读取环境变量之前。
"""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

# 这台机器的命令沙箱会拒绝删除 /tmp 下的探测文件，tempfile 的
# "创建再删掉"探测因此失败（表现为 No usable temporary directory）。
# 直接把临时根指到工作区内，跳过探测；测试产物也更好找。
_TMP_ROOT = Path(__file__).resolve().parents[1] / "runs" / "tmp" / "unittest"
_TMP_ROOT.mkdir(parents=True, exist_ok=True)

# 每个测试进程用自己的子目录。这样退出时能整棵删掉，不会碰到同时在跑的另一个
# 进程（`unittest discover` 偶尔会被并行跑两遍），崩了也能一眼看出是谁留下的。
_RUN_TMP = _TMP_ROOT / f"pid-{os.getpid()}"
_RUN_TMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_RUN_TMP)
os.environ.setdefault("TMPDIR", str(_RUN_TMP))
os.environ.setdefault("FLAGPILOT_MEMORY", str(_TMP_ROOT / "memory"))


def _clean_up() -> None:
    """退出时删掉本进程的临时目录。

    没有这一步，跑一次全量就在 runs/tmp/unittest 下留几百个目录：11-17 起
    积了 5,000 多个、385 MB，全是 gitignore 的垃圾，而且只会继续涨。
    `FLAGPILOT_KEEP_TEST_TMP=1` 保留现场，便于查某个失败用例。
    """
    if os.environ.get("FLAGPILOT_KEEP_TEST_TMP"):
        return
    if _RUN_TMP.parent != _TMP_ROOT:      # 万一 tmp 根被改过，绝不往上删
        return
    shutil.rmtree(_RUN_TMP, ignore_errors=True)


atexit.register(_clean_up)

# Tests use explicit dummy credentials and isolated encryption keys. Never inherit
# a developer's live model configuration or production storage key.
for _name in ("FLAGPILOT_ENCRYPTION_KEY_FILE", "FLAGPILOT_MODEL",
              "FLAGPILOT_API_KEY", "DEEPSEEK_API_KEY", "DSV41_API_KEY",
              "GEMINI_API_KEY", "GOOGLE_API_KEY",
              "FLAGPILOT_BASE_URL", "FLAGPILOT_H_BASE_URL"):
    os.environ.pop(_name, None)
os.environ["FLAGPILOT_NO_DOTENV"] = "1"     # 也别从仓库根目录的 .env 里读出真实的 Gemini 密钥
