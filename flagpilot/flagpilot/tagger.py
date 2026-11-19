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

import re
import unicodedata
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
    "truncated",
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
    "truncated": "输出被截断（撞 max_tokens）",
    "hardcoded": "脚本硬编码答案",
    "not_reproducible": "答案对但不可复现",
    "wrong_flag": "答案错误",
    "no_submit": "轮次耗尽未提交",
    "budget": "预算耗尽（其他）",
}


def _iter_events(trace_path: Path):
    from .storage import read_records
    try:
        yield from read_records(trace_path)
    except OSError:
        return


def _invalid_kind(ev: dict, tokens_by_turn: dict[int, int], max_tokens: int) -> str:
    """一条 invalid_action 的真实性质：protocol / truncated / thinking_overflow。

    新 trace 直接带 ``kind`` 字段（agent 侧判定，最可靠）。老 trace 没有这个
    字段，就按"这一轮的 completion_tokens 有没有撞上 max_tokens"反推——
    否则那批已经跑完的数据只能永远挂着错标签，而"标签错了多少"恰好是
    这次要说明的事，不能靠重跑才能回答。
    """
    kind = str(ev.get("kind") or "")
    if kind:
        return kind
    msg = str(ev.get("reason") or "")
    if "空回复" in msg:
        return "thinking_overflow"
    turn = ev.get("turn")
    used = tokens_by_turn.get(turn) if isinstance(turn, int) else None
    if max_tokens > 0 and used is not None and used >= max_tokens:
        return "truncated"
    return "protocol"


_KIND_TAG = {
    "truncated": "truncated",
    "thinking_overflow": "thinking_overflow",
}


def _invalid_evidence(kind: str, ev: dict, tokens_by_turn: dict[int, int],
                      max_tokens: int) -> str:
    """逐条表格里的"为什么"。截断要给 token 数，别只抄一句"没有解析到合法 JSON"——
    那句话对三种原因都成立，等于没说。"""
    turn = ev.get("turn")
    if kind == "truncated":
        used = tokens_by_turn.get(turn) if isinstance(turn, int) else None
        return f"turn {turn}: {used if used is not None else '?'}/{max_tokens} tok 用满，JSON 未闭合"
    return str(ev.get("reason") or "")[:160]


def classify_run(run_dir: Path | str | None, record: dict) -> dict:
    """返回 {"primary": tag, "tags": [...], "evidence": {...}}。"""
    tags: list[str] = []
    evidence: dict[str, str] = {}

    verdict = str(record.get("verdict") or "")
    reason = str(record.get("reason") or "")
    replay = record.get("replay") or {}
    replay_reason = str(replay.get("reason") or "")

    # ---- 先读 trace：后面好几处判断要用到"每条 invalid_action 到底是什么性质" ----
    trace_path = Path(run_dir) / "trace.jsonl" if run_dir else None
    events: list[dict] = []
    if trace_path and trace_path.exists():
        events = list(_iter_events(trace_path))
    meta = next((e for e in events if e.get("type") == "meta"), {})
    max_tokens = int(((meta.get("model") or {}).get("max_tokens")) or 0)
    tokens_by_turn = {
        e["turn"]: int(e.get("completion_tokens") or 0)
        for e in events
        if e.get("type") == "model" and isinstance(e.get("turn"), int)
    }
    invalid_kinds: Counter[str] = Counter()

    if verdict == "verified":
        tags.append("ok")
    elif verdict == "reproduced":
        # 自建题没有 gold：重放成功只能说明"可复现"，不能说明"正确"。
        # 它不是失败，但也不能混进 verified——所以单列一档。
        tags.append("reproduced")

    if verdict == "error" and reason.startswith("model:"):
        tags.append("model_error")
        evidence["model_error"] = reason[:160]

    if replay_reason in ("literal_flag", "hardcoded_no_material"):
        # 同一族：答案不是从材料里算出来的。前者是"写下了答案"，
        # 后者是"删掉材料还能答对"——后者抓的是换了个编码的写法。
        tags.append("hardcoded")
        evidence["hardcoded"] = f"{replay_reason}: {str(replay.get('detail') or '')[:140]}"
    elif replay_reason in ("script_error", "no_output", "timeout"):
        tags.append("not_reproducible")
        evidence["not_reproducible"] = f"{replay_reason}: {str(replay.get('detail') or '')[:140]}"

    if verdict == "wrong":
        tags.append("wrong_flag")

    if verdict == "budget":
        if reason == "invalid_action":
            tags.append("protocol")
        elif reason in ("truncated", "thinking_overflow"):
            # agent 已经知道自己是截断而不是乱答：直接采信，别再猜一遍。
            tags.append(reason)
            evidence[reason] = f"agent 判定 {reason}（max_tokens={max_tokens or '?'}）"
        elif reason == "max_turns":
            tags.append("no_submit")
            evidence["no_submit"] = (f"{record.get('turns') or '?'} 轮用满，"
                                     f"始终没走到 submit")
        else:
            tags.append("budget")

    # 注意：只有"因为协议问题而没跑完"时，protocol 才算主因。
    # 一道题解出来了、只是脚本不能复现，却因为中途有两次 invalid_action
    # 被标成"不守协议"，会把真正的结论（不可复现）盖掉——这是归因的错误优先级。
    # reason 已经说清是截断/思维链占满时不再补这一条：那是 harness 的预算问题。
    if (int(record.get("invalids") or 0) >= 2 and verdict in ("budget", "error")
            and reason not in ("truncated", "thinking_overflow")):
        if "protocol" not in tags:
            tags.append("protocol")
            evidence["protocol"] = f"invalid_action × {record.get('invalids')}"

    # ---- 环境类证据只在工具输出里 ----
    if events:
        seen = {k: False for k in _PATTERNS}
        for ev in events:
            t = ev.get("type")
            if t == "tool" and ev.get("timed_out"):
                if "sandbox_timeout" not in tags:
                    tags.append("sandbox_timeout")
                    evidence["sandbox_timeout"] = f"turn {ev.get('turn')} {ev.get('action')}"
                continue
            if t == "invalid_action":
                kind = _invalid_kind(ev, tokens_by_turn, max_tokens)
                invalid_kinds[kind] += 1
                label = _KIND_TAG.get(kind, "protocol")
                if label not in tags:
                    tags.append(label)
                if label in ("truncated", "thinking_overflow") or label not in evidence:
                    # 截断/思维链的证据要带 token 数，比 record 层的占位说明具体
                    evidence[label] = _invalid_evidence(
                        kind, ev, tokens_by_turn, max_tokens)
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

    # 全部 invalid_action 都是截断 → 这不是"模型不守协议"，是预算配小了。
    # 上一步可能因为 record.reason == "invalid_action" 先打了 protocol，
    # 这里按 trace 里的事实推翻它：protocol 意味着模型有问题，截断意味着
    # harness 有问题，两者的责任方不同，不能混着记。
    if invalid_kinds and set(invalid_kinds) == {"truncated"}:
        tags = [t for t in tags if t != "protocol"]
        evidence.pop("protocol", None)
        if "truncated" not in tags:
            tags.append("truncated")

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


def _pad(text: str, width: int) -> str:
    """按终端显示宽度补空格。

    `f"{s:<14}"` 对中文是错的：一个汉字占两格，用字符数补齐会让整列错位，
    报告一错位就没人愿意看第二眼。
    """
    used = sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)
    return text + " " * max(0, width - used)


def render(counter: dict[str, int], rows: list[dict], limit: int = 8) -> str:
    if not counter:
        return ""
    fails = {k: v for k, v in counter.items() if k not in ("ok", "reproduced")}
    if not fails:
        return "失败归因：全部 verified，无失败可归因。"
    detail = [r for r in rows if r["primary"] not in ("ok", "reproduced")]
    hits = sum(fails.values())
    # 分母必须是"失败的题数"，不是"标签数"：一条失败可能同时命中两个标签，
    # 拿标签数当分母会让百分比加起来超过 100%，读的人会以为统计错了。
    n_fail = len(detail) or hits
    lines = [f"失败归因（{n_fail} 条失败，共 {hits} 个标签，同一条可命中多个）："]
    for k, v in sorted(fails.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {_pad(LABELS.get(k, k), 32)}{v:>3}  ({v / n_fail:.0%})")
    if fails.get("truncated"):
        # 这条必须写出来：截断是配置问题，不是模型能力问题。不点明的话，
        # 读者会把"预算配小了"读成"模型不行"，进而得出错误的模型对比结论。
        lines.append("  → 有题是输出被 max_tokens 截断：先调大 --max-tokens 再比较模型，"
                     "否则比的是预算不是能力。")
    if detail and limit:
        lines.append("")
        lines.append(f"逐条（取前 {min(limit, len(detail))} 条）：")
        for r in detail[:limit]:
            ev = r["evidence"]
            why = ev.get(r["primary"], "")
            lines.append(f"  {_pad(str(r['run_id']), 24)}"
                         f"{_pad(LABELS.get(r['primary'], r['primary']), 32)}{why[:90]}")
    return "\n".join(lines)
