"""替网页在 TACC 上跑的作业用的小工具（本机运行不经过这里）。

课程平台只允许经 SDK 看日志（`tcloud cat`），没有别的回传通道。所以作业把网页需要的东西
按行打印进 Slurm 日志，本机按前缀解析：

  @@ev <json>     一次 attempt 的 trace 事件（模型一轮、工具一次、提交、重放、结论），字段裁剪过
  @@delta <json>  答疑回复的增量文字
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
        out[k] = v
    return out


def _emit(tag: str, obj) -> None:
    sys.stdout.write(f"{tag} {json.dumps(obj, ensure_ascii=False, default=str)}\n")
    sys.stdout.flush()


def events_printer(ev: dict) -> None:
    """BatchRunner 的 hook：一条 trace 事件打印成一行 @@ev。"""
    if ev.get("type") in ("meta", "start", "model", "tool", "invalid_action", "repeat_warning",
                          "submit", "replay", "summary", "done"):
        _emit("@@ev", _compact(ev))


def chat_once(cfg, message: str, history: list[dict], task=None, use_tools: bool = True,
              workdir: Path | None = None) -> dict:
    """答一句：先把历史问答恢复成上下文，再走 ChatSession.ask（命令照常在本作业的沙箱里执行）。"""
    from .chat import ChatSession

    sess = ChatSession(cfg, task=task, workdir=workdir, use_tools=use_tools)
    for h in history[-12:]:
        q, a = str(h.get("user") or ""), str(h.get("answer") or "")
        if q and a:
            sess.messages.append({"role": "user", "content": q})
            sess.messages.append({"role": "assistant", "content": a})
            sess.turns += 1
    reply = sess.ask(message, on_delta=lambda d: _emit("@@delta", d),
                     hook=lambda ev: _emit("@@ev", _compact(ev)) if ev.get("type") in ("chat_tool", "chat_check") else None)
    out = reply.as_dict()
    _emit("@@reply", out)
    return out
