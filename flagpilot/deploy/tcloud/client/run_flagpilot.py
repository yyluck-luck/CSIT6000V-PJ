"""课程 Python 入口：qwen-python run_flagpilot.py <flagpilot 子命令与参数>。

qwen-python 经 PRoot 启动公共 Python；这里不依赖它对 `-m` 的转发，直接按模块运行。
"""
import os
import runpy
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.argv = ["flagpilot"] + sys.argv[1:]
runpy.run_module("flagpilot", run_name="__main__", alter_sys=True)
