"""测试包的全局隔离。

`run_attempt` 现在会往"跨 run 记忆"里写一条记录（这是特性，不是副作用）。
如果不隔离，跑一次单测就会把 mock 模型的结论写进项目真实的
`runs/memory/tasks/`，之后真正解题时 prompt 里会混进一堆 mock 记录。

放在包初始化里而不是每个用例里，是因为项目用 `unittest discover`，
没有 conftest 这种统一钩子；`tests/__init__.py` 会在任何测试模块 import
flagpilot 之前执行，正好能赶在 config 读取环境变量之前。
"""

import os
import tempfile
from pathlib import Path

# 这台机器的命令沙箱会拒绝删除 /tmp 下的探测文件，tempfile 的
# "创建再删掉"探测因此失败（表现为 No usable temporary directory）。
# 直接把临时根指到工作区内，跳过探测；测试产物也更好找。
_TMP_ROOT = Path(__file__).resolve().parents[1] / "runs" / "tmp" / "unittest"
_TMP_ROOT.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_TMP_ROOT)
os.environ.setdefault("TMPDIR", str(_TMP_ROOT))
os.environ.setdefault("FLAGPILOT_MEMORY", str(_TMP_ROOT / "memory"))
