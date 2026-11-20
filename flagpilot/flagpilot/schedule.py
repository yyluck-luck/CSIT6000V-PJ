"""批跑调度：最长预计优先（LPT, longest processing time first）。

为什么要排：runner 用固定 4 个并发槽（课程服务 --max-num-seqs 4），题目按提交顺序进槽。
一道慢题（取证题常要翻十几轮）如果排在最后才开跑，前面三个槽早就空了，整批要干等它
一个——批次墙钟被这一道题的长度决定。LPT 把预计最慢的先放进去，短题去填后面的空隙，
整批墙钟的上界从"最慢那题 + 其余之和/4"收紧到经典的 4/3 近似比以内。

只改"什么时候开跑"，不改任何一个 attempt 的输入（prompt、预算、种子都不变），所以
结论与 FIFO 完全可比；排序是确定性的（同分保持原顺序），重跑得到同一个顺序。

预计耗时按可得信息逐级退化：
  1. 同一道题在历史批次里的 wall_s 中位数
  2. 同类别题在历史里的中位数
  3. 类别先验（下表，取自 TACC 作业 29323 的量级）
"""

from __future__ import annotations

import heapq
import statistics
from pathlib import Path

from . import storage

# 没有任何历史时的类别先验（秒）。只用于排序，数值大小只要相对关系对就行。
CATEGORY_PRIOR_S = {
    "forensics": 40.0,
    "reverse engineering": 45.0,
    "reverse": 45.0,
    "web exploitation": 35.0,
    "web": 35.0,
    "binary exploitation": 45.0,
    "cryptography": 30.0,
    "general skills": 15.0,
}
DEFAULT_PRIOR_S = 30.0


def _cat(c: str | None) -> str:
    return str(c or "").strip().lower()


def load_history(roots: list[Path]) -> list[dict]:
    """收集 roots 下各批次的 runs.jsonl（<root>/runs.jsonl 与 <root>/*/runs.jsonl）。

    只取有 task 与正数 wall_s 的行；读不了的文件（缺密钥、半截写入）直接跳过——
    调度拿不到历史只是退回先验，不能因此让批次起不来。
    """
    rows: list[dict] = []
    seen: set[Path] = set()
    for root in roots:
        root = Path(root)
        if not root.exists():
            continue
        for p in [root / "runs.jsonl", *sorted(root.glob("*/runs.jsonl"))]:
            if p in seen or not p.exists():
                continue
            seen.add(p)
            try:
                recs = storage.read_records(p)
            except Exception:
                continue
            for r in recs:
                w = r.get("wall_s")
                if r.get("task") and isinstance(w, (int, float)) and w > 0:
                    rows.append({"task": r["task"], "category": _cat(r.get("category")), "wall_s": float(w)})
    return rows


class Estimator:
    """task → 预计秒数。"""

    def __init__(self, history: list[dict]):
        self.by_task: dict[str, list[float]] = {}
        self.by_cat: dict[str, list[float]] = {}
        for r in history:
            self.by_task.setdefault(r["task"], []).append(r["wall_s"])
            self.by_cat.setdefault(r["category"], []).append(r["wall_s"])
        self.n = len(history)

    def seconds(self, task) -> tuple[float, str]:
        xs = self.by_task.get(task.name)
        if xs:
            return statistics.median(xs), "task"
        c = _cat(getattr(task, "category", ""))
        xs = self.by_cat.get(c)
        if xs:
            return statistics.median(xs), "category"
        return CATEGORY_PRIOR_S.get(c, DEFAULT_PRIOR_S), "prior"


def lpt_order(jobs: list[tuple], est: Estimator) -> list[tuple]:
    """jobs = [(task, seed), ...] → 预计耗时降序；Python 的 sorted 是稳定的，同分保持原顺序。"""
    return sorted(jobs, key=lambda j: -est.seconds(j[0])[0])


def makespan(durations: list[float], workers: int) -> float:
    """按给定顺序把任务塞进最早空出来的槽（ThreadPoolExecutor 的行为），返回整批墙钟。"""
    slots = [0.0] * max(1, int(workers))
    heapq.heapify(slots)
    for d in durations:
        t = heapq.heappop(slots)
        heapq.heappush(slots, t + max(0.0, float(d)))
    return max(slots) if slots else 0.0
