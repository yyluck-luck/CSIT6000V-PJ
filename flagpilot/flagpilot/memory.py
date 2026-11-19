"""跨 run 的记忆。

要把两件事分开看，它们是不同层次的问题：

  * **上下文**（context）：一次 attempt 里的 messages。超预算就压缩，见
    `context.py`。解决的是"这一轮前面做过什么，别忘"。
  * **长期记忆**（memory）：跨 attempt / 跨天的经验，落盘成 jsonl。
    解决的是"同一道题昨天试过、失败了，今天别从零开始"。

存储位置与格式（append-only，坏了也能手工看）：

    runs/memory/tasks/<task>.jsonl     一行一条 attempt 的结论
    runs/chat/<sid>/chat.jsonl         答疑会话的逐轮记录（见 chat.py）

一条记录只放"结果元数据"——verdict / reason / 轮次 / 耗时 / 模型 / 档位。
**不放 flag，也不放 solve.py**：记忆是提示，不是答案库；一旦存了明文，
"verified" 就变成"查表命中"，判据自己就先烂了。
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from . import config, storage

_LOCK = threading.Lock()
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe(name: str) -> str:
    """题目名 → 文件名。自建题目里可能带中文/空格，统一折成下划线。"""
    s = _SAFE.sub("_", str(name or "")).strip("._")
    return s[:80] or "unknown"


class MemoryStore:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root or config.MEMORY_ROOT)

    # ---------- 路径 ----------

    def task_file(self, task_name: str) -> Path:
        return self.root / "tasks" / (_safe(task_name) + ".jsonl")

    # ---------- 写 ----------

    def record(self, task_name: str, summary: dict, model: str | None = None) -> dict | None:
        """把一次 attempt 的结论追加进记忆。失败不抛异常——记忆坏了不该拖垮求解。"""
        verdict = str((summary or {}).get("verdict") or "")
        if not verdict:
            return None
        failure = summary.get("failure") or {}
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "task": task_name,
            "model": model or summary.get("model") or "",
            "ablation": summary.get("ablation") or "",
            "verdict": verdict,
            "reason": str(summary.get("reason") or "")[:200],
            "turns": summary.get("turns"),
            "wall_s": summary.get("wall_s"),
            "prompt_tokens": summary.get("prompt_tokens"),
            "completion_tokens": summary.get("completion_tokens"),
            "failure": failure.get("primary") if isinstance(failure, dict) else "",
            "compactions": summary.get("compactions", 0),
            "run_dir": summary.get("run_dir") or "",
        }
        try:
            p = self.task_file(task_name)
            p.parent.mkdir(parents=True, exist_ok=True)
            with _LOCK:
                storage.append_record(p, rec)
        except storage.StorageError:
            raise
        except Exception:
            return None
        return rec

    # ---------- 读 ----------

    def records(self, task_name: str, limit: int | None = None) -> list[dict]:
        p = self.task_file(task_name)
        if not p.exists():
            return []
        out: list[dict] = []
        try:
            with p.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(storage.decode_record(line))
                    except json.JSONDecodeError:
                        continue      # 半行（进程被杀）跳过，不让整个记忆失效
        except OSError:
            return []
        return out[-limit:] if limit else out

    def count(self, task_name: str) -> int:
        return len(self.records(task_name))

    def digest(self, task_name: str, limit: int = 3) -> str:
        """给 prompt 用的一小段经验。没有记录就返回空串——别塞"暂无记录"占 token。"""
        rows = self.records(task_name)
        if not rows:
            return ""
        tail = rows[-limit:]
        lines = [f"MEMORY: 这道题在本机跑过 {len(rows)} 次，"
                 f"最近 {len(tail)} 次（只记结论，不含 flag）："]
        for r in reversed(tail):
            ts = str(r.get("ts") or "")[5:16].replace("T", " ")
            seg = f"- {ts} · {r.get('ablation') or '?'} · {r.get('model') or '?'} → {r.get('verdict')}"
            if r.get("reason"):
                seg += f"（{r['reason']}）"
            if r.get("turns") is not None:
                seg += f"，{r['turns']} 轮"
            if r.get("failure"):
                seg += f"，归因 {r['failure']}"
            lines.append(seg)
        lines.append("把上面当提示：失败的路径别再走一遍；但这不代表答案已知，"
                     "仍要从附件重新验证。")
        return "\n".join(lines)

    def stats(self, task_name: str | None = None) -> dict:
        if task_name:
            rows = self.records(task_name)
            return {"task": task_name, "records": len(rows),
                    "last": rows[-1] if rows else None}
        n = 0
        files = 0
        if self.root.exists():
            for p in (self.root / "tasks").glob("*.jsonl"):
                files += 1
                try:
                    n += sum(1 for line in p.open(encoding="utf-8") if line.strip())
                except OSError:
                    pass
        return {"tasks": files, "records": n}
