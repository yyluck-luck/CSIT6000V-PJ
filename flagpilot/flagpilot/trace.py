"""轨迹记录。

一条 attempt 一个 JSONL 文件：第一行是 meta，之后每步一行事件。
行内字段固定，便于后续 pandas/jq 直接聚合；不做嵌套美化。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from . import storage

EventHook = Callable[[dict], None]


class Trace:
    def __init__(self, path: Path, meta: dict, hook: EventHook | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        storage._check_existing_mode(self.path)
        storage.atomic_write(self.path, b"")
        self._fh = self.path.open("a", encoding="utf-8")
        self._hook = hook
        self._t0 = time.monotonic()
        self._write({"type": "meta", "ts": time.time(), **meta})

    def _write(self, obj: dict) -> None:
        self._fh.write(storage.encode_record(obj) + "\n")
        self._fh.flush()
        if self._hook:
            try:
                self._hook(obj)
            except Exception:
                pass

    def event(self, etype: str, **fields) -> None:
        self._write({
            "type": etype,
            "t_ms": int((time.monotonic() - self._t0) * 1000),
            "ts": time.time(),
            **fields,
        })

    def close(self, summary: dict) -> None:
        self._write({"type": "summary", "t_ms": int((time.monotonic() - self._t0) * 1000), **summary})
        self._fh.close()


def read_trace(path: Path) -> list[dict]:
    return storage.read_records(path)


def read_jsonl(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    return read_trace(path)
