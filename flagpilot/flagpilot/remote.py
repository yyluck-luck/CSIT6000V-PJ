"""替网页在 TACC 上跑的作业用的小工具（本机运行不经过这里）。

课程平台只允许经 SDK 看日志（`tcloud cat`），没有别的回传通道。所以作业把网页需要的东西
按行打印进 Slurm 日志，本机按前缀解析：

  @@ev <json>     一次 attempt 的 trace 事件（模型一轮、工具一次、提交、重放、结论），字段裁剪过
  @@delta <json>  答疑回复的增量文字
  @@pd <json>     单题运行时模型逐字输出的增量（约每秒一批，{"turn": n, "d": 文字}），网页边生成边显示
  @@reply <json>  答疑一轮的最终结果

只是"打印一份"，trace.jsonl 照常落盘、评测口径不变；网页拿到的是同一批事件的副本。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_TRIM = {"text": 3000, "obs_head": 1500, "stderr_head": 600, "script": 6000, "brief": 400, "reason": 400}
_DROP = {"caps", "limits", "policy", "server", "ts"}


def _compact(ev: dict) -> dict:
    out = {}
    for k, v in ev.items():
        if k in _DROP:
            continue
        if isinstance(v, str) and k in _TRIM:
            v = v[:_TRIM[k]]
        elif k == "act" and isinstance(v, dict):       # action 事件里的动作：每个字段最多 3000 字，TACC 日志不被长脚本撑大
            v = {kk: (vv[:3000] if isinstance(vv, str) else vv) for kk, vv in v.items()}
        out[k] = v
    return out


def _emit(tag: str, obj) -> None:
    sys.stdout.write(f"{tag} {json.dumps(obj, ensure_ascii=False, default=str)}\n")
    sys.stdout.flush()


# TACC 上的运行只有这些事件回到网页。网页显示的引擎事件都得在这里：tests/test_remote_events.py 对照 app/index.html 的
# onEvent 检查（2026-11-22 新加的 action / repeat_blocked 差点只在本机运行里出现）
PAGE_EVENTS = ("meta", "start", "model", "action", "tool", "invalid_action", "repeat_warning", "repeat_blocked",
               "submit", "replay", "summary", "done")


def events_printer(ev: dict) -> None:
    """BatchRunner 的 hook：一条 trace 事件打印成一行 @@ev。"""
    if ev.get("type") in PAGE_EVENTS:
        _emit("@@ev", _compact(ev))


class DeltaPrinter:
    """run_attempt 的 on_delta：把模型逐字输出攒成约每秒一批打印（@@pd），日志不会被逐 token 撑大。

    只给网页看"正在生成什么"；本轮的 model 事件（@@ev）一到，网页就换成完整文本。不进 trace，不影响评测。
    """

    def __init__(self, every_s: float = 1.0, max_chars: int = 600) -> None:
        import threading
        import time
        self._now, self._lock = time.monotonic, threading.Lock()
        self.every_s, self.max_chars = every_s, max_chars
        self.turn, self.buf, self.t = 0, "", float("-inf")

    def _flush(self) -> None:
        if self.buf:
            _emit("@@pd", {"turn": self.turn, "d": self.buf})
            self.buf = ""
        self.t = self._now()

    def __call__(self, turn: int, chunk: str) -> None:
        with self._lock:
            if turn != self.turn:
                self._flush()                     # 上一轮剩下的先打出去
                self.turn, self.t = turn, float("-inf")    # 新一轮的第一段立刻打：网页马上知道"开始写了"
            self.buf += chunk
            if self._now() - self.t >= self.every_s or len(self.buf) >= self.max_chars:
                self._flush()


HISTORY_MAX = 40         # 只是包大小的保险：带多少问答由网页服务按模型的窗口定


def chat_once(cfg, message: str, history: list[dict], task=None, use_tools: bool = True,
              workdir: Path | None = None) -> dict:
    """答一句：先把历史问答恢复成上下文，再走 ChatSession.ask（命令照常在本作业的沙箱里执行）。"""
    from .chat import ChatSession

    sess = ChatSession(cfg, task=task, workdir=workdir, use_tools=use_tools)
    # 带哪些问答由网页服务按 context_plan 定好了（"不带" / "重新开始" / 按窗口省略）；{"note": ...} 是给模型的说明
    note = next((str(h["note"]) for h in history if h.get("note")), "")
    pairs = [h for h in history[-HISTORY_MAX:] if str(h.get("user") or "") and str(h.get("answer") or "")]
    sess.set_context(pairs, note)
    sess.turns = len(pairs)
    reply = sess.ask(message, on_delta=lambda d: _emit("@@delta", d),
                     hook=lambda ev: _emit("@@ev", _compact(ev)) if ev.get("type") in ("chat_tool", "chat_check") else None)
    out = reply.as_dict()
    _emit("@@reply", out)
    return out
