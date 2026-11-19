"""整批 prefix cache 命中率：服务端计数器的首尾差。

并发 > 1 时，单个 attempt 的前后差值里混着别的 attempt 的流量，metrics 因此不报命中率。
但整批看，所有 trace 里最早一次 before 与最晚一次 after 之间的差值，就是这一批的总量——
前提是服务只被这一批使用（课程平台上每人自起服务，满足；共享服务上不成立，报告里写明）。

TACC 作业 29323：15680 / 29320 = 53.5%，而按块对齐估的理论上限约 69%——qwen3.8-27b 是
混合注意力模型，vLLM 把缓存块设成 784 token，命中只能按整块发生。
"""

from __future__ import annotations

from pathlib import Path

from .trace import read_jsonl

HITS = "vllm:prefix_cache_hits_total"
QUERIES = "vllm:prefix_cache_queries_total"


def batch_cache(results: list[dict]) -> dict | None:
    before: list[dict] = []
    after: list[dict] = []
    for r in results:
        d = r.get("run_dir")
        if not d:
            continue
        try:
            events = read_jsonl(Path(d) / "trace.jsonl")
        except Exception:                    # 加密 trace 缺密钥等：跳过这一条，不拖垮报告
            continue
        for ev in events:
            if ev.get("type") == "server_metrics" and QUERIES in ev:
                (before if ev.get("phase") == "before" else after).append(ev)
    if not before or not after:
        return None
    b = min(before, key=lambda e: e.get("ts") or 0)
    a = max(after, key=lambda e: e.get("ts") or 0)
    queries = (a.get(QUERIES) or 0) - (b.get(QUERIES) or 0)
    hits = (a.get(HITS) or 0) - (b.get(HITS) or 0)
    if queries <= 0 or hits < 0:
        return None
    return {"hit_rate": round(hits / queries, 4), "hits": int(hits), "queries": int(queries)}
