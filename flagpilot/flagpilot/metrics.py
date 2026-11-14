"""指标口径。

三个数必须分开算，否则报告会被误读：

    raw        flag 与 gold 哈希一致（含硬编码、含背答案）
    verified   raw 且脚本在干净目录里复现出同一个 flag
    gap        raw − verified，即"答案对但没证明是自己算出来的"

gap 不是一个数，它是两个构念的混合：**作弊**（literal_flag）和**不可复现**
（script_error / timeout）。所以这里始终把 gap 拆成按 reason 的计数，
不把 gap 当成单一指标去和别人的论文比。

另一个刻意的选择：所有比率都给 Wilson 区间。小样本下点估计会骗人——
本地 5 题 40% 的区间是 12%–77%，写进报告必须带这个区间。
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


RAW = ("verified", "unverified")


def summarize(results: list[dict], with_tags: bool = True) -> dict:
    if not results:
        return {"n": 0}

    n = len(results)
    k_verified = sum(1 for r in results if r["verdict"] == "verified")
    k_raw = sum(1 for r in results if r["verdict"] in RAW)
    k_wrong = sum(1 for r in results if r["verdict"] == "wrong")
    k_budget = sum(1 for r in results if r["verdict"] == "budget")
    k_error = sum(1 for r in results if r["verdict"] == "error")
    # reproduced：自建题（无 gold）重放成功。**不计入 raw**——
    # 没有标准答案就无从判断对错，混进分子就是自欺。
    k_reproduced = sum(1 for r in results if r["verdict"] == "reproduced")

    lo_v, hi_v = wilson(k_verified, n)
    lo_r, hi_r = wilson(k_raw, n)

    reasons = Counter(
        r.get("replay", {}).get("reason") if r.get("replay") else r.get("reason")
        for r in results
    )

    def _mean(key):
        vals = [r.get(key) or 0 for r in results]
        return sum(vals) / len(vals) if vals else 0.0

    # TODO(你): 在这里加一列你关心的指标，例如“首次提交就正确的比例”
    #       或“平均每题的工具调用数”。加完记得让 render() 也打印出来。
    by_cat: dict[str, dict] = defaultdict(lambda: {"n": 0, "verified": 0, "raw": 0})
    for r in results:
        c = r.get("category", "?")
        by_cat[c]["n"] += 1
        by_cat[c]["verified"] += int(r["verdict"] == "verified")
        by_cat[c]["raw"] += int(r["verdict"] in RAW)

    # ---- E3：成本与服务端指标（只在配了 metrics_url 时才有值）----
    hits = sum((r.get("server") or {}).get("cache_hits") or 0 for r in results)
    queries = sum((r.get("server") or {}).get("cache_queries") or 0 for r in results)
    prefill = sum((r.get("server") or {}).get("prefill_tokens") or 0 for r in results)
    decode = sum((r.get("server") or {}).get("decode_tokens") or 0 for r in results)
    ttfts = [r.get("ttft_ms_p50") or 0 for r in results if r.get("ttft_ms_p50")]

    out = {
        "n": n,
        "verified": k_verified,
        "raw": k_raw,
        "wrong": k_wrong,
        "budget": k_budget,
        "error": k_error,
        "reproduced": k_reproduced,
        "verified_rate": k_verified / n,
        "verified_ci": [round(lo_v, 4), round(hi_v, 4)],
        "raw_rate": k_raw / n,
        "raw_ci": [round(lo_r, 4), round(hi_r, 4)],
        "gap": (k_raw - k_verified) / n,
        "reasons": dict(reasons),
        "mean_turns": round(_mean("turns"), 2),
        "mean_wall_s": round(_mean("wall_s"), 2),
        "mean_tokens": round(_mean("total_tokens"), 0),
        "total_cost_usd": round(sum(r.get("cost_usd") or 0 for r in results), 4),
        "mean_ttft_ms_p50": round(sum(ttfts) / len(ttfts), 1) if ttfts else 0,
        "cache_hit_rate": round(hits / queries, 4) if queries else None,
        "prefill_share": round(prefill / (prefill + decode), 4) if (prefill + decode) else None,
        "ablations": sorted({r.get("ablation") for r in results if r.get("ablation")}),
        "by_category": {
            k: {
                "n": v["n"],
                "verified": v["verified"],
                "raw": v["raw"],
                "verified_rate": round(v["verified"] / v["n"], 4),
                "raw_rate": round(v["raw"] / v["n"], 4),
            }
            for k, v in sorted(by_cat.items())
        },
    }

    if with_tags:
        from . import tagger

        counter, rows = tagger.classify_batch(results)
        out["failure_tags"] = counter
        out["tag_rows"] = rows
    return out


def render(summary: dict) -> str:
    if not summary.get("n"):
        return "（没有已完成的结果）"
    lo, hi = summary["verified_ci"]
    lo_r, hi_r = summary["raw_ci"]
    lines = [
        f"attempts        {summary['n']}"
        + (f"   ablation={','.join(summary['ablations'])}" if summary.get("ablations") else ""),
        f"verified        {summary['verified']}  ({summary['verified_rate']:.1%}, "
        f"95% CI {lo:.1%}–{hi:.1%})",
        f"raw (flag ok)   {summary['raw']}  ({summary['raw_rate']:.1%}, "
        f"95% CI {lo_r:.1%}–{hi_r:.1%})",
        f"gap             {summary['gap']:.1%}",
        f"wrong / budget / error  {summary['wrong']} / {summary['budget']} / {summary['error']}",
    ]
    if summary.get("reproduced"):
        lines.append(f"reproduced      {summary['reproduced']}（自建题无 gold，不计入 raw）")
    lines += [
        f"mean turns      {summary['mean_turns']}",
        f"mean wall       {summary['mean_wall_s']} s",
        f"mean tokens     {summary['mean_tokens']:.0f}",
    ]
    if summary.get("mean_ttft_ms_p50"):
        lines.append(f"TTFT p50        {summary['mean_ttft_ms_p50']:.0f} ms（各题中位数之均值）")
    if summary.get("cache_hit_rate") is not None:
        lines.append(f"prefix cache    命中率 {summary['cache_hit_rate']:.1%}")
    if summary.get("prefill_share") is not None:
        lines.append(f"prefill share   {summary['prefill_share']:.1%}（prefill / (prefill+decode)）")
    if summary["total_cost_usd"]:
        lines.append(f"total cost      ${summary['total_cost_usd']}")
    if summary.get("reasons"):
        top = sorted(summary["reasons"].items(), key=lambda kv: -kv[1])[:6]
        lines.append("reasons         " + ", ".join(f"{k}={v}" for k, v in top))
    lines.append("by category:")
    for cat, v in summary["by_category"].items():
        lines.append(
            f"  {cat:<18} n={v['n']:<3} verified={v['verified']:<3} raw={v['raw']:<3} "
            f"v_rate={v['verified_rate']:.0%} r_rate={v['raw_rate']:.0%}"
        )
    if summary.get("failure_tags"):
        from . import tagger

        lines.append("")
        lines.append(tagger.render(summary["failure_tags"], summary.get("tag_rows") or []))
    return "\n".join(lines)


def to_markdown(summary: dict, title: str = "") -> str:
    """导出报告可直接引用的表。刻意不带结论——结论由人写。"""
    if not summary.get("n"):
        return "（没有已完成的结果）\n"
    lo, hi = summary["verified_ci"]
    lo_r, hi_r = summary["raw_ci"]
    lines: list[str] = []
    if title:
        lines += [f"### {title}", ""]
    lines += [
        "| 指标 | 值 |",
        "|---|---|",
        f"| attempts | {summary['n']} |",
        f"| verified 解出率 | {summary['verified_rate']:.1%}（95% CI {lo:.1%}–{hi:.1%}） |",
        f"| raw（flag 正确） | {summary['raw_rate']:.1%}（95% CI {lo_r:.1%}–{hi_r:.1%}） |",
        f"| raw − verified gap | {summary['gap']:.1%} |",
        f"| wrong / budget / error | {summary['wrong']} / {summary['budget']} / {summary['error']} |",
        f"| 平均轮次 | {summary['mean_turns']} |",
        f"| 平均墙钟 | {summary['mean_wall_s']} s |",
        f"| 平均 token | {summary['mean_tokens']:.0f} |",
    ]
    if summary.get("mean_ttft_ms_p50"):
        lines.append(f"| TTFT p50 | {summary['mean_ttft_ms_p50']:.0f} ms |")
    if summary.get("cache_hit_rate") is not None:
        lines.append(f"| prefix cache 命中率 | {summary['cache_hit_rate']:.1%} |")
    if summary.get("prefill_share") is not None:
        lines.append(f"| prefill 占比 | {summary['prefill_share']:.1%} |")
    if summary["total_cost_usd"]:
        lines.append(f"| 成本（Track H 计价） | ${summary['total_cost_usd']} |")

    lines += ["", "| 类别 | n | verified | raw | v_rate | r_rate |", "|---|---|---|---|---|---|"]
    for cat, v in summary["by_category"].items():
        lines.append(f"| {cat} | {v['n']} | {v['verified']} | {v['raw']} | "
                     f"{v['verified_rate']:.0%} | {v['raw_rate']:.0%} |")

    if summary.get("failure_tags"):
        from . import tagger

        fails = {k: v for k, v in summary["failure_tags"].items()
                 if k not in ("ok", "reproduced")}
        total = sum(fails.values()) or 1
        if fails:
            lines += ["", "| 失败标签 | 计数 | 占失败比 |", "|---|---|---|"]
            for k, v in sorted(fails.items(), key=lambda kv: -kv[1]):
                lines.append(f"| {tagger.LABELS.get(k, k)} | {v} | {v / total:.0%} |")
    return "\n".join(lines) + "\n"


def compare(batches: dict[str, dict], keys: list[str] | None = None) -> str:
    """多批次并排。E1 出结果表就用这个，不做任何合并计算。"""
    keys = keys or ["verified_rate", "raw_rate", "gap", "mean_turns",
                    "mean_tokens", "cache_hit_rate"]
    label = {
        "verified_rate": "verified", "raw_rate": "raw", "gap": "gap",
        "mean_turns": "turns", "mean_tokens": "tokens",
        "cache_hit_rate": "cache hit", "mean_wall_s": "wall s",
        "mean_ttft_ms_p50": "TTFT p50",
    }
    head = "| batch | n | " + " | ".join(label.get(k, k) for k in keys) + " |"
    lines = [head, "|" + "---|" * (len(keys) + 2)]
    for name, s in batches.items():
        if not s.get("n"):
            lines.append(f"| {name} | 0 |" + " — |" * len(keys))
            continue
        cells = []
        for k in keys:
            v = s.get(k)
            if v is None:
                cells.append("—")
            elif k in ("verified_rate", "raw_rate", "gap", "cache_hit_rate"):
                cells.append(f"{v:.1%}")
            elif k == "mean_tokens":
                cells.append(f"{v:.0f}")
            elif k == "mean_ttft_ms_p50":
                cells.append(f"{v:.0f}")
            else:
                cells.append(f"{v}")
        lines.append(f"| {name} | {s['n']} | " + " | ".join(cells) + " |")
    return "\n".join(lines)
