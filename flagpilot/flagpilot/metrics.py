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

from .cachestats import batch_cache


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def percentile(values, q: float) -> float | None:
    """线性插值分位数（与 numpy 默认口径一致）。空列表返回 None，不编一个 0。"""
    xs = sorted(float(v) for v in values)
    if not xs:
        return None
    pos = (len(xs) - 1) * q / 100.0
    lo, hi = math.floor(pos), math.ceil(pos)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (pos - lo), 3)


RAW = ("verified", "unverified")


def summarize(results: list[dict], with_tags: bool = True, gpu: dict | None = None) -> dict:
    """gpu：gpu.load_ledger() 的汇总；给了才算 GPU 秒（只有 Track L 作业里才有）。"""
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

    # vLLM 的 prefix cache / token 计数器是全服务端的。并发 > 1 时，每个 attempt 的
    # 前后差值里混着别的 attempt 的流量，加总出来的命中率是错数——宁可不报。
    concurrent = max((int(r.get("workers") or 1) for r in results), default=1) > 1

    # ---- E3：延迟分位（带样本量）----
    # wall：全部 attempt 的 ReAct 循环墙钟；solve：verified 的端到端时长（含重放，
    # 旧记录没有 total_s 时退回 wall_s）。分位数小样本下极不稳定，n 必须一起报。
    walls = [float(r["wall_s"]) for r in results if r.get("wall_s") is not None]
    solves = [float(r.get("total_s") if r.get("total_s") is not None else r.get("wall_s") or 0)
              for r in results if r["verdict"] == "verified"]

    # ---- Track H：缓存命中率只能来自 API usage（托管服务没有 /metrics）----
    api_rows = [r for r in results
                if r.get("api_cache_hit_tokens") is not None
                or r.get("api_cache_miss_tokens") is not None]
    a_hit = sum(r.get("api_cache_hit_tokens") or 0 for r in api_rows)
    a_miss = sum(r.get("api_cache_miss_tokens") or 0 for r in api_rows)
    total_cost = sum(r.get("cost_usd") or 0 for r in results)

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
        # 6 位：DeepSeek 一题冒烟只有几十微美元，4 位会舍成 0，成本行整行消失
        "total_cost_usd": round(total_cost, 6),
        "cost_per_verified_usd": round(total_cost / k_verified, 6)
        if (k_verified and total_cost) else None,
        "mean_ttft_ms_p50": round(sum(ttfts) / len(ttfts), 1) if ttfts else 0,
        "wall_n": len(walls),
        "wall_s_p50": percentile(walls, 50),
        "wall_s_p95": percentile(walls, 95),
        "solve_n": len(solves),
        "solve_s_p50": percentile(solves, 50),
        "solve_s_p95": percentile(solves, 95),
        "cache_hit_rate": None if concurrent else (round(hits / queries, 4) if queries else None),
        "server_counters_note": "并发>1：服务端计数器被多个 attempt 分摊，不报命中率/prefill 占比"
        if (concurrent and (queries or prefill)) else None,
        # 并发时单 attempt 口径不报；整批首尾差仍然成立（前提：服务只被本批使用）
        "cache_batch": batch_cache(results) if concurrent else None,
        "api_cache_hit_rate": round(a_hit / (a_hit + a_miss), 4) if (a_hit + a_miss) else None,
        "prefill_share": None if concurrent else (
            round(prefill / (prefill + decode), 4) if (prefill + decode) else None),
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

    if gpu:
        g_s = gpu.get("gpu_seconds")
        b_s = gpu.get("busy_gpu_seconds")
        out["gpu_seconds"] = g_s
        out["busy_gpu_seconds"] = b_s
        out["gpu_jobs"] = gpu.get("jobs")
        out["gpu_s_per_verified"] = round(g_s / k_verified, 1) if (g_s and k_verified) else None
        out["busy_gpu_s_per_verified"] = round(b_s / k_verified, 1) \
            if (b_s is not None and k_verified) else None

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
    if summary.get("wall_s_p50") is not None:
        lines.append(f"wall p50/p95    {summary['wall_s_p50']:.1f} / {summary['wall_s_p95']:.1f} s"
                     f"（n={summary['wall_n']}）")
    if summary.get("solve_s_p50") is not None:
        lines.append(f"solve p50/p95   {summary['solve_s_p50']:.1f} / {summary['solve_s_p95']:.1f} s"
                     f"（verified 端到端，n={summary['solve_n']}）")
    if summary.get("cache_hit_rate") is not None:
        lines.append(f"prefix cache    命中率 {summary['cache_hit_rate']:.1%}")
    if summary.get("server_counters_note"):
        lines.append(f"prefix cache    — （{summary['server_counters_note']}）")
    cb = summary.get("cache_batch")
    if cb:
        lines.append(f"prefix cache    整批 {cb['hit_rate']:.1%}（{cb['hits']}/{cb['queries']} token；服务只被本批使用时成立）")
    if summary.get("api_cache_hit_rate") is not None:
        lines.append(f"API cache       命中率 {summary['api_cache_hit_rate']:.1%}（Track H usage，按 token）")
    if summary.get("gpu_seconds"):
        busy = summary.get("busy_gpu_seconds")
        lines.append(f"GPU seconds     {summary['gpu_seconds']:.0f} 申请"
                     + (f" / {busy:.0f} 忙" if busy is not None else "")
                     + f"（{summary.get('gpu_jobs')} 个作业）")
        if summary.get("gpu_s_per_verified") is not None:
            lines.append(f"GPU-s/verified  {summary['gpu_s_per_verified']:.0f}"
                         + (f"（忙 {summary['busy_gpu_s_per_verified']:.0f}）"
                            if summary.get("busy_gpu_s_per_verified") is not None else ""))
    if summary.get("prefill_share") is not None:
        lines.append(f"prefill share   {summary['prefill_share']:.1%}（prefill / (prefill+decode)）")
    if summary["total_cost_usd"]:
        lines.append(f"total cost      ${summary['total_cost_usd']:.6f}")
        if summary.get("cost_per_verified_usd") is not None:
            lines.append(f"cost/verified   ${summary['cost_per_verified_usd']:.6f}")
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
    if summary.get("wall_s_p50") is not None:
        lines.append(f"| 墙钟 p50 / p95（全部 attempt） | {summary['wall_s_p50']:.1f} / "
                     f"{summary['wall_s_p95']:.1f} s（n={summary['wall_n']}） |")
    if summary.get("solve_s_p50") is not None:
        lines.append(f"| 解题耗时 p50 / p95（verified，端到端） | {summary['solve_s_p50']:.1f} / "
                     f"{summary['solve_s_p95']:.1f} s（n={summary['solve_n']}） |")
    if summary.get("api_cache_hit_rate") is not None:
        lines.append(f"| API 缓存命中率（Track H） | {summary['api_cache_hit_rate']:.1%} |")
    if summary.get("gpu_seconds"):
        busy = summary.get("busy_gpu_seconds")
        lines.append(f"| GPU 秒（申请 / 忙） | {summary['gpu_seconds']:.0f} / "
                     + (f"{busy:.0f}" if busy is not None else "—")
                     + f"（{summary.get('gpu_jobs')} 个作业） |")
    if summary.get("gpu_s_per_verified") is not None:
        lines.append(f"| GPU 秒 / verified（申请口径） | {summary['gpu_s_per_verified']:.0f} |")
    if summary.get("cache_hit_rate") is not None:
        lines.append(f"| prefix cache 命中率 | {summary['cache_hit_rate']:.1%} |")
    if summary.get("server_counters_note"):
        lines.append(f"| prefix cache 命中率 | —（{summary['server_counters_note']}） |")
    cb = summary.get("cache_batch")
    if cb:
        lines.append(f"| prefix cache 命中率（整批） | {cb['hit_rate']:.1%}（{cb['hits']}/{cb['queries']} token；独占服务时成立） |")
    if summary.get("prefill_share") is not None:
        lines.append(f"| prefill 占比 | {summary['prefill_share']:.1%} |")
    if summary["total_cost_usd"]:
        lines.append(f"| 成本（Track H 计价） | ${summary['total_cost_usd']:.6f} |")
        if summary.get("cost_per_verified_usd") is not None:
            lines.append(f"| 成本 / verified | ${summary['cost_per_verified_usd']:.6f} |")

    lines += ["", "| 类别 | n | verified | raw | v_rate | r_rate |", "|---|---|---|---|---|---|"]
    for cat, v in summary["by_category"].items():
        lines.append(f"| {cat} | {v['n']} | {v['verified']} | {v['raw']} | "
                     f"{v['verified_rate']:.0%} | {v['raw_rate']:.0%} |")

    if summary.get("failure_tags"):
        from . import tagger

        # 与 tagger.render 同一口径：只数失败的 attempt，分母是失败条数（不是标签数）。
        n_fail, fails = tagger.failure_counts(summary["failure_tags"],
                                              summary.get("tag_rows") or [])
        if fails:
            lines += ["", "| 失败标签 | 计数 | 占失败比 |", "|---|---|---|"]
            for k, v in sorted(fails.items(), key=lambda kv: -kv[1]):
                lines.append(f"| {tagger.LABELS.get(k, k)} | {v} | {v / n_fail:.0%} |")
            lines += ["", f"共 {n_fail} 条失败；同一条可命中多个标签，占比之和可超过 100%。"]
    return "\n".join(lines) + "\n"


# E3 出表用的列：延迟分位 + 缓存 + 两条轨道各自的成本口径。
E3_KEYS = ["verified_rate", "solve_s_p50", "solve_s_p95", "solve_n", "mean_turns",
           "cache_hit_rate", "api_cache_hit_rate", "prefill_share",
           "gpu_s_per_verified", "cost_per_verified_usd"]

_LABEL = {
    "verified_rate": "verified", "raw_rate": "raw", "gap": "gap",
    "mean_turns": "turns", "mean_tokens": "tokens",
    "cache_hit_rate": "cache hit", "mean_wall_s": "wall s",
    "mean_ttft_ms_p50": "TTFT p50",
    "solve_s_p50": "solve p50 s", "solve_s_p95": "solve p95 s", "solve_n": "solve n",
    "wall_s_p50": "wall p50 s", "wall_s_p95": "wall p95 s",
    "api_cache_hit_rate": "API cache", "prefill_share": "prefill",
    "gpu_s_per_verified": "GPU-s/verified", "cost_per_verified_usd": "$/verified",
}
_PCT = ("verified_rate", "raw_rate", "gap", "cache_hit_rate", "api_cache_hit_rate", "prefill_share")


def _cell(k: str, v) -> str:
    if v is None:
        return "—"
    if k in _PCT:
        return f"{v:.1%}"
    if k in ("mean_tokens", "mean_ttft_ms_p50", "gpu_s_per_verified", "solve_n"):
        return f"{v:.0f}"
    if k.endswith("_s_p50") or k.endswith("_s_p95"):
        return f"{v:.1f}"
    if k == "cost_per_verified_usd":
        return f"${v:.6f}"
    return f"{v}"


def compare(batches: dict[str, dict], keys: list[str] | None = None,
            ref: str | None = None, guard_pts: float = 5.0) -> str:
    """多批次并排。E1 出结果表就用这个，不做任何合并计算。

    ref：参照批次名。给了就多一列"Δ vs ref"（verified 率差，百分点）和护栏判定：
    E3 的提速只有在 verified 率不比参照格低超过 guard_pts 个点时才算数。
    """
    keys = keys or ["verified_rate", "raw_rate", "gap", "mean_turns",
                    "mean_tokens", "cache_hit_rate"]
    base = (batches.get(ref) or {}).get("verified_rate") if ref else None
    extra = ["Δ vs ref"] if ref else []
    head = "| batch | n | " + " | ".join([_LABEL.get(k, k) for k in keys] + extra) + " |"
    lines = [head, "|" + "---|" * (len(keys) + 2 + len(extra))]
    for name, s in batches.items():
        if not s.get("n"):
            lines.append(f"| {name} | 0 |" + " — |" * (len(keys) + len(extra)))
            continue
        cells = [_cell(k, s.get(k)) for k in keys]
        if ref:
            if base is None or name == ref:
                cells.append("ref" if name == ref else "—")
            else:
                d = (s["verified_rate"] - base) * 100
                cells.append(f"{d:+.1f} pt {'✓' if d >= -guard_pts else '✗'}")
        lines.append(f"| {name} | {s['n']} | " + " | ".join(cells) + " |")
    if ref:
        lines.append("")
        lines.append(f"护栏：verified 率比 {ref} 低不超过 {guard_pts:g} 个点记 ✓，否则 ✗（提速不计）。")
    return "\n".join(lines)


# ---------------------------------------------------------------- 配对比较

def _success(r: dict, key: str) -> bool:
    return r["verdict"] == "verified" if key == "verified" else r["verdict"] in RAW


def _mcnemar_exact(b: int, c: int) -> float:
    """精确 McNemar（不一致对上的双侧二项检验）。小样本下比卡方近似靠谱。"""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return round(min(1.0, 2 * tail), 6)


def paired(results_a: list[dict], results_b: list[dict], key: str = "verified") -> dict:
    """同一道题、同一个 seed 在两个配置下的逐对变化。

    并排的两个百分比会掩盖"修好 5 题、弄坏 4 题"这种情况；
    配对把它拆开，并给出精确 McNemar p 值。按 run_id（task-sN）配对。
    """
    a = {r["run_id"]: r for r in results_a if r.get("run_id")}
    b = {r["run_id"]: r for r in results_b if r.get("run_id")}
    common = sorted(set(a) & set(b))
    out = {"key": key, "n_pairs": len(common), "fixed": 0, "broken": 0,
           "both_ok": 0, "both_fail": 0, "unpaired": len(set(a) ^ set(b)),
           "fixed_ids": [], "broken_ids": []}
    for rid in common:
        sa, sb = _success(a[rid], key), _success(b[rid], key)
        if sa and sb:
            out["both_ok"] += 1
        elif not sa and not sb:
            out["both_fail"] += 1
        elif sb:
            out["fixed"] += 1
            out["fixed_ids"].append(rid)
        else:
            out["broken"] += 1
            out["broken_ids"].append(rid)
    out["p_value"] = _mcnemar_exact(out["fixed"], out["broken"])
    return out


def render_paired(p: dict, name_a: str, name_b: str) -> str:
    lines = [
        f"配对比较（{p['key']}）：{name_a} → {name_b}，{p['n_pairs']} 对",
        f"  修好（A 失败 → B 成功）  {p['fixed']}",
        f"  弄坏（A 成功 → B 失败）  {p['broken']}",
        f"  都成功 / 都失败          {p['both_ok']} / {p['both_fail']}",
        f"  精确 McNemar p           {p['p_value']}",
    ]
    if p["unpaired"]:
        lines.append(f"  未配对 run_id            {p['unpaired']}（只在一边出现，不计入检验）")
    if p["fixed_ids"]:
        lines.append("  修好：" + ", ".join(p["fixed_ids"][:10]))
    if p["broken_ids"]:
        lines.append("  弄坏：" + ", ".join(p["broken_ids"][:10]))
    return "\n".join(lines)
