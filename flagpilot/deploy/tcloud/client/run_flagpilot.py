"""课程 Python 入口：qwen-python run_flagpilot.py <flagpilot 子命令与参数>。

qwen-python 经 PRoot 启动公共 Python；这里不依赖它对 `-m` 的转发，直接按模块运行。
"""
import os
import runpy
import sys

here = os.path.dirname(os.path.abspath(__file__))
pkg = os.path.join(here, "flagpilot.zip")     # submit.sh 把包打成一个 zip（少传 27 个文件）；没有就用目录
sys.path.insert(0, pkg if os.path.exists(pkg) else here)
sys.argv = ["flagpilot"] + sys.argv[1:]
runpy.run_module("flagpilot", run_name="__main__", alter_sys=True)
