"""运行历史索引：列出 runs/web 下每一次网页运行的摘要，供「历史」页浏览与一键回放。

只读每条 trace 的头尾：meta 在第一行、summary / done 在最后几行。整份 trace 可能有
几十 KB（加密后更大），历史页一次要列几十条，全读既慢又没必要。结果按 (mtime, size)
缓存，文件没动就不再解析。加密记录用 storage.decode_record 解开；缺密钥时跳过那一行
而不是报错——列表照样出来，只是那条显示为 unknown。
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from flagpilot import storage

_RID = re.compile(r"^[0-9a-f]{12}$")
HEAD_BYTES = 16 * 1024
TAIL_BYTES = 256 * 1024
_CACHE: dict[str, tuple[tuple[int, int], dict]] = {}
_LOCK = threading.Lock()

# 只回列表需要的字段：不带 flag、不带脚本，列表接口没理由把答案再发一遍。
_KEEP = ("verdict", "reason", "turns", "model", "ablation", "e2e_s", "wall_s",
         "total_tokens", "completion_tokens")


def _decode(line: str) -> dict | None:
    if not line.strip():
        return None
    try:
        rec = storage.decode_record(line)
    except (ValueError, storage.StorageError):      # JSONDecodeError 是 ValueError
        return None
    return rec if isinstance(rec, dict) else None


def _head_meta(path: Path) -> dict:
    with path.open("rb") as f:
        first = f.read(HEAD_BYTES).split(b"\n", 1)[0]
    rec = _decode(first.decode("utf-8", "replace"))
    return rec if rec and rec.get("type") == "meta" else {}


def _tail_summary(path: Path, size: int) -> dict:
    with path.open("rb") as f:
        if size > TAIL_BYTES:
            f.seek(size - TAIL_BYTES)
        data = f.read()
    lines = data.decode("utf-8", "replace").splitlines()
    if size > TAIL_BYTES:
        lines = lines[1:]                      # 第一行可能被切在中间
    for line in reversed(lines):
        rec = _decode(line)
        if rec and rec.get("type") in ("done", "summary"):
            return rec
    return {}


def summarize_trace(rid: str, trace: Path) -> dict:
    st = trace.stat()
    key = (st.st_mtime_ns, st.st_size)
    cache_key = str(trace)
    with _LOCK:
        hit = _CACHE.get(cache_key)
    if hit and hit[0] == key:
        return hit[1]
    meta = _head_meta(trace)
    summ = _tail_summary(trace, st.st_size)
    item = {"id": rid, "task": meta.get("task") or trace.parent.name.rsplit("-s", 1)[0],
            "category": meta.get("category") or "", "ts": st.st_mtime, "source": "web",
            "running": False}
    item.update({k: summ.get(k) for k in _KEEP})
    item["verdict"] = item.get("verdict") or "unknown"
    with _LOCK:
        _CACHE[cache_key] = (key, item)
    return item


def list_runs(runs_root: Path, limit: int = 200) -> list[dict]:
    """按时间倒序列出 runs/web/<id>/<task>-s0/trace.jsonl 的摘要。"""
    base = Path(runs_root) / "web"
    if not base.is_dir():
        return []
    dirs = []
    for d in base.iterdir():
        if d.is_dir() and _RID.fullmatch(d.name):
            try:
                dirs.append((d.stat().st_mtime, d))
            except OSError:
                continue
    dirs.sort(key=lambda x: x[0], reverse=True)
    out: list[dict] = []
    for _, d in dirs[:limit]:
        for trace in sorted(d.glob("*/trace.jsonl")):
            try:
                out.append(summarize_trace(d.name, trace))
            except OSError:
                continue
    return out


def item_from_state(rid: str, st: dict) -> dict:
    """内存里的运行（正在跑的、--replay 挂载的）转成同形条目。"""
    summ = st.get("summary") or {}
    evs = st.get("events") or []
    meta = next((e for e in evs if e.get("type") == "meta"), {})
    item = {"id": rid, "task": st.get("task") or meta.get("task") or summ.get("run_id", "?"),
            "category": meta.get("category") or "", "ts": st.get("t0") or meta.get("ts") or 0,
            "source": st.get("kind") or "live", "running": not summ}
    item.update({k: summ.get(k) for k in _KEEP})
    item["verdict"] = item.get("verdict") or ("running" if not summ else "unknown")
    if not summ:
        item["turns"] = sum(1 for e in evs if e.get("type") == "model")
    return item

