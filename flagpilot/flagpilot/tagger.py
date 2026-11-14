"""失败分类。

为什么需要它：43 题里有 60% 没 verified，如果报告只写"失败 26 题"，
评审第一个问题就是"为什么失败"。而失败原因是异质的——

    模型不会算        → 能力问题，可以讨论模型规模
    模型不守 JSON     → prompt / schema 问题，工程可修
    环境缺工具        → 环境问题，**不该记在模型头上**
    答案对但不可复现  → 判据边界问题，是这个课题的发现本身

不分开算，四件事会被混成一个"成功率"。所以这里按可观测证据打标，
每条都带出处，避免人工复核时只能靠回忆。

打标只做一次（在出报告时扫 trace），不写回 runs.jsonl —— 因为判据可能改，
标签要能跟着改，而不能固化在历史记录里。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

# 顺序即优先级：越靠前越"根本"，报告里 primary 取第一个命中的。
PRIORITY = [
    "ok",
    "reproduced",
    "model_error",
    "thinking_overflow",
    "sandbox_timeout",
    "tool_missing",
    "tool_denied",
    "network_blocked",
    "protocol",
    "hardcoded",
    "not_reproducible",
    "wrong_flag",
    "no_submit",
    "budget",
]

_PATTERNS = {
    "tool_missing": re.compile(
        r"command not found|: not found\b|No such file or directory|"
        r"is not recognized as an internal", re.I),
    "tool_denied": re.compile(
        r"Permission denied|Operation not permitted|must be run as root|"
        r"sudo: |cannot open .* for writing", re.I),
    "network_blocked": re.compile(
        r"Could not resolve|Temporary failure in name resolution|"
        r"Network is unreachable|couldn't connect|Connection refused", re.I),
}

LABELS = {
    "ok": "verified 解出",
    "reproduced": "重放成功（无 gold，不计入评测）",
    "model_error": "模型调用失败",
    "thinking_overflow": "空回复（思维链占满预算）",
    "sandbox_timeout": "工具/沙箱超时",
    "tool_missing": "环境缺工具",
    "tool_denied": "权限被拒",
    "network_blocked": "误试联网",
    "protocol": "不守 JSON 协议",
    "hardcoded": "脚本硬编码答案",
    "not_reproducible": "答案对但不可复现",
    "wrong_flag": "答案错误",
    "no_submit": "轮次耗尽未提交",
    "budget": "预算耗尽（其他）",
}


def _iter_events(trace_path: Path):
    try:
        with trace_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def classify_run(run_dir: Path | str | None, record: dict) -> dict:
    """返回 {"primary": tag, "tags": [...], "evidence": {...}}。"""
    tags: list[str] = []
    evidence: dict[str, str] = {}

    verdict = str(record.get("verdict") or "")
    reason = str(record.get("reason") or "")
    replay = record.get("replay") or {}
    replay_reason = str(replay.get("reason") or "")

    if verdict == "verified":
        tags.append("ok")
    elif verdict == "reproduced":
        # 自建题没有 gold：重放成功只能说明"可复现"，不能说明"正确"。
        # 它不是失败，但也不能混进 verified——所以单列一档。
        tags.append("reproduced")

    if verdict == "error" and reason.startswith("model:"):
        tags.append("model_error")
        evidence["model_error"] = reason[:160]

    if replay_reason == "literal_flag":
        tags.append("hardcoded")
        evidence["hardcoded"] = str(replay.get("detail") or "")[:160]
    elif replay_reason in ("script_error", "no_output", "timeout"):
        tags.append("not_reproducible")
        evidence["not_reproducible"] = f"{replay_reason}: {str(replay.get('detail') or '')[:140]}"

    if verdict == "wrong":
        tags.append("wrong_flag")

    if verdict == "budget":
        if reason == "invalid_action":
            tags.append("protocol")
        elif reason == "max_turns":
            tags.append("no_submit")
        else:
            tags.append("budget")

    # 注意：只有"因为协议问题而没跑完"时，protocol 才算主因。
    # 一道题解出来了、只是脚本不能复现，却因为中途有两次 invalid_action
    # 被标成"不守协议"，会把真正的结论（不可复现）盖掉——这是归因的错误优先级。
    if int(record.get("invalids") or 0) >= 2 and verdict in ("budget", "error"):
        if "protocol" not in tags:
            tags.append("protocol")
            evidence["protocol"] = f"invalid_action × {record.get('invalids')}"

    # ---- 以下需要读 trace：环境类证据只在工具输出里 ----
    trace_path = Path(run_dir) / "trace.jsonl" if run_dir else None
    if trace_path and trace_path.exists():
        seen = {k: False for k in _PATTERNS}
        for ev in _iter_events(trace_path):
            t = ev.get("type")
            if t == "tool" and ev.get("timed_out"):
                if "sandbox_timeout" not in tags:
                    tags.append("sandbox_timeout")
                    evidence["sandbox_timeout"] = f"turn {ev.get('turn')} {ev.get('action')}"
                continue
            if t == "invalid_action":
                msg = str(ev.get("reason") or "")
                if "空回复" in msg:
                    if "thinking_overflow" not in tags:
                        tags.append("thinking_overflow")
                        evidence["thinking_overflow"] = msg[:160]
                elif "protocol" not in tags:
                    tags.append("protocol")
                    evidence["protocol"] = msg[:140]
                continue
            if t != "tool":
                continue
            # 环境类证据在 stderr 里；cmd 只作为补充（模型可能自己写错命令）
            blob = f"{ev.get('cmd') or ''}\n{ev.get('stderr_head') or ''}"
            for name, pat in _PATTERNS.items():
                if not seen[name] and pat.search(blob):
                    seen[name] = True
                    if name not in tags:
                        tags.append(name)
                        evidence[name] = blob.strip().replace("\n", " ")[:160]

    tags = [t for t in PRIORITY if t in tags] or tags
    return {
        "primary": tags[0] if tags else "unknown",
        "tags": tags,
        "evidence": evidence,
    }


def classify_batch(results: list[dict]) -> tuple[dict[str, int], list[dict]]:
    """给一批结果打标。返回 (计数, 每条的标签)。"""
    counter: Counter[str] = Counter()
    rows: list[dict] = []
    for r in results:
        c = classify_run(r.get("run_dir"), r)
        counter.update(c["tags"] or ["unknown"])
        rows.append({"run_id": r.get("run_id"), "verdict": r.get("verdict"), **c})
    return dict(counter), rows


def render(counter: dict[str, int], rows: list[dict], limit: int = 8) -> str:
    if not counter:
        return ""
    fails = {k: v for k, v in counter.items() if k not in ("ok", "reproduced")}
    if not fails:
        return "失败归因：全部 verified，无失败可归因。"
    total = sum(fails.values())
    lines = [f"失败归因（{total} 条失败，同一条可命中多个标签）："]
    for k, v in sorted(fails.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {LABELS.get(k, k):<14} {v:>3}  ({v / total:.0%})")
    detail = [r for r in rows if r["primary"] not in ("ok", "reproduced")]
    if detail and limit:
        lines.append("")
        lines.append(f"逐条（取前 {min(limit, len(detail))} 条）：")
        for r in detail[:limit]:
            ev = r["evidence"]
            why = ev.get(r["primary"], "")
            lines.append(f"  {r['run_id']:<22} {LABELS.get(r['primary'], r['primary']):<12} {why[:90]}")
    return "\n".join(lines)
