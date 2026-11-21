"""Web 层的提速件：静态页缓存 + gzip + ETag、按需压缩 JSON、短 TTL 缓存。

单独成模块，是为了不把 server.py 继续撑大；server 只调这里的纯函数与小类。

为什么值得做：
  * 首页 ~80KB。以前每次请求都从磁盘读、原样发、`no-store`，经 SSH 隧道看演示时
    每次刷新都要整页重传。现在内存里各存一份原文与 gzip，带 ETag：未改动只回 304。
  * `/api/result` 带整段事件历史，40 轮的运行有几十 KB；浏览器声明支持 gzip 时压缩。
  * `/api/models` 每次打开页面都要去问模型服务（最长 5 s 超时），30 s 内复用结果。
"""

from __future__ import annotations

import gzip
import hashlib
import threading
import time
from pathlib import Path
from typing import Callable, Hashable

GZIP_MIN_BYTES = 2048       # 太小的响应压缩不划算（头部开销 + CPU）


def accepts_gzip(header: str | None) -> bool:
    """Accept-Encoding 里有 gzip（或 *）且没被 q=0 显式拒绝。"""
    for part in (header or "").split(","):
        name, _, params = part.strip().partition(";")
        if name.strip().lower() not in ("gzip", "*"):
            continue
        q = 1.0
        for p in params.split(";"):
            k, _, v = p.strip().partition("=")
            if k.strip().lower() == "q":
                try:
                    q = float(v)
                except ValueError:
                    q = 0.0
        if q > 0:
            return True
    return False


def maybe_gzip(body: bytes, accept: str | None) -> tuple[bytes, bool]:
    """够大且客户端接受时压缩；返回 (body, 是否已压缩)。"""
    if len(body) >= GZIP_MIN_BYTES and accepts_gzip(accept):
        return gzip.compress(body, 5), True
    return body, False


class StaticFile:
    """按 (mtime, size) 失效的单文件缓存：改了 index.html 不用重启服务。"""

    def __init__(self, path: Path):
        self.path = path
        self._key: tuple[int, int] | None = None
        self._lock = threading.Lock()
        self.raw = b""
        self.gz = b""
        self.etag = ""

    def load(self) -> "StaticFile":
        st = self.path.stat()
        key = (st.st_mtime_ns, st.st_size)
        if key != self._key:
            with self._lock:
                if key != self._key:
                    raw = self.path.read_bytes()
                    self.raw, self.gz = raw, gzip.compress(raw, 6)
                    self.etag = '"' + hashlib.sha1(raw).hexdigest()[:20] + '"'
                    self._key = key
        return self


class TTLCache:
    """一个键一个值的短缓存；键变了（换模型 / 换端点）立即失效。"""

    def __init__(self, ttl_s: float):
        self.ttl_s = ttl_s
        self._key: Hashable = None
        self._at = 0.0
        self._val = None
        self._lock = threading.Lock()

    def get(self, key: Hashable, compute: Callable[[], object]):
        now = time.monotonic()
        with self._lock:
            if self._key == key and now - self._at < self.ttl_s:
                return self._val
        val = compute()                      # 不持锁调用：慢请求不该卡住别的线程
        with self._lock:
            self._key, self._at, self._val = key, time.monotonic(), val
        return val

    def clear(self) -> None:
        with self._lock:
            self._key, self._at, self._val = None, 0.0, None


def secret_tag(value: str) -> str:
    """把密钥变成不可逆的短标签，只用来区分缓存键，不在内存里多存一份原文。"""
    return hashlib.sha256(value.encode()).hexdigest()[:12] if value else ""
