"""人在回路：网页上看着一次运行的人可以单步批准 / 修改 / 跳过每条命令，暂停、继续，或者插一句提示。

agent.run_attempt 只在两处碰它：
  * 每轮调模型之前 take_hints()：用户的提示作为一条消息加进对话，轨迹里记 hint 事件；
  * 每条命令执行之前 gate()：单步或暂停时发 await 事件并等用户决定，等待时间不计入墙钟预算。
不传 control（批量评测、CLI）时行为与原来完全一致。
"""

from __future__ import annotations

import threading
from typing import Callable

OPS = ("step", "pause", "resume", "approve", "edit", "skip", "stop", "hint")


class RunControl:
    def __init__(self) -> None:
        self._c = threading.Condition()
        self.step = False           # 单步：每条命令都等批准
        self.paused = False         # 暂停：下一条命令之前停住
        self.stopped = False
        self.waiting: dict | None = None
        self._decision: tuple[str, str | None] | None = None
        self._hints: list[str] = []
        self.hints_used = 0
        self.decisions = 0

    def set(self, op: str, value=None, turn: int | None = None) -> bool:
        """网页发来的操作；没有意义的操作（没在等时批准、空提示）返回 False。

        turn：批准 / 修改 / 跳过针对的是哪一步。给了就必须和正在等的那一步一致——
        人点下去的那一刻，运行可能已经走到下一步了，不能把决定套到没看过的命令上。
        """
        with self._c:
            if op == "step":
                self.step = bool(value)
            elif op == "pause":
                self.paused = True
            elif op == "resume":
                self.paused = False
            elif op in ("approve", "edit", "skip"):
                if self.waiting is None or (turn is not None and self.waiting["turn"] != turn):
                    return False
                self._decision = (op, None if value is None else str(value)[:4000])
            elif op == "stop":
                self.stopped = True
            elif op == "hint":
                text = " ".join(str(value or "").split())[:500]
                if not text:
                    return False
                self._hints.append(text)
            else:
                return False
            self._c.notify_all()
            return True

    def state(self) -> dict:
        with self._c:
            return {"step": self.step, "paused": self.paused, "stopped": self.stopped,
                    "waiting": dict(self.waiting) if self.waiting else None, "hints_queued": len(self._hints)}

    def take_hints(self) -> list[str]:
        with self._c:
            hints, self._hints = self._hints, []
            self.hints_used += len(hints)
            return hints

    def gate(self, turn: int, action: str, field: str, text: str,
             emit: Callable[[dict], None]) -> tuple[str, str | None, bool]:
        """执行前的闸门。返回 (操作, 改过的内容, 是否等过人)；操作 ∈ approve / edit / skip / stop。"""
        with self._c:
            if self.stopped:
                return ("stop", None, False)
            if not (self.step or self.paused):
                return ("approve", None, False)
            self.waiting = {"turn": turn, "action": action, "field": field, "text": text[:4000]}
            self._decision = None
            info = {**self.waiting, "step": self.step, "paused": self.paused}
        emit(info)                  # 锁外发：hook 里可能要拿别的锁
        with self._c:
            while not self.stopped and self._decision is None and (self.step or self.paused):
                self._c.wait(timeout=1.0)
            op, value = ("stop", None) if self.stopped else (self._decision or ("approve", None))
            self.waiting, self._decision = None, None
            self.decisions += 1
            return (op, value, True)
