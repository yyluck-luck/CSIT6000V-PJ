"""上下文压缩：一次 attempt 内的"还记得前面做过什么"。

三个层次要说清，否则很容易以为"加了记忆"就等于"模型变聪明了"：

  1. **追加时截断**（agent.truncate）：单条 observation 太长就砍头尾。
     它保证单条消息不炸，但消息条数还是线性增长。
  2. **上下文压缩**（本模块）：messages 总字符超阈值时，把中段折成一条摘要，
     只留 system、题面、摘要、最近几轮。解决"20 轮之后 prompt 太大"。
  3. **长期记忆**（memory.py）：跨 attempt 的经验，落盘。解决"这题昨天试过"。

为什么压缩是确定性的、不调模型：
  * 多一次模型调用就多一个失败点，也让同一 seed 不可复现；
  * 压缩本身要花 token，而它存在的理由就是省 token；
  * 这里需要的只是"第几步、什么动作、结果如何"，正则足够。

代价：压缩会改写历史前缀，vLLM 的 prefix cache 从这一刻起失效。
所以阈值设得偏高（默认 48k 字符），只在长 run 里触发，并逐次记进 trace
（`compact` 事件），报告里能算出"为了腾空间牺牲了多少缓存命中"。
"""

from __future__ import annotations

import json

_MARK = "[COMPACTED CONTEXT]"


def total_chars(messages: list[dict]) -> int:
    return sum(len(str(m.get("content") or "")) for m in messages)


def _action_brief(text: str) -> tuple[str, str]:
    """从模型的原始回复里抠出 action 与参数。抠不出就退化成第一行。"""
    raw = str(text or "")
    start = raw.find("{")
    end = raw.rfind("}")
    if 0 <= start < end:
        try:
            obj = json.loads(raw[start:end + 1])
            if isinstance(obj, dict):
                act = str(obj.get("action") or "?")
                detail = (obj.get("cmd") or obj.get("path") or obj.get("code")
                          or obj.get("scheme") or obj.get("flag") or "")
                detail = " ".join(str(detail).split())
                return act, detail[:110]
        except Exception:
            pass
    first = next((ln.strip() for ln in raw.splitlines() if ln.strip()), "")
    return "text", first[:110]


def _obs_brief(text: str) -> str:
    """观察里最有信息量的是"退出码 + 输出的第一行"，不是全文。"""
    raw = str(text or "")
    body = raw
    if "--- output ---" in body:
        body = body.split("--- output ---", 1)[1]
    head = body.strip()

    # 状态行可能在第 1 行（[step 1] shell）也可能是第 2 行（exit=0  time=…），
    # 只看第一行会漏掉退出码——摘要里没有 exit=0，模型就分不清成功失败。
    status = ""
    for ln in raw.splitlines()[:2]:
        if "exit=" in ln or "timeout" in ln:
            status = " ".join(ln.split())[:40]
            break

    if not head:
        return status or "(无输出)"
    for ln in head.splitlines():
        if ln.strip():
            line = " ".join(ln.split())[:120]
            return (status + " · " if status else "") + line
    return status or "(无输出)"


def digest_of(messages: list[dict], max_chars: int = 2400) -> str:
    """把一串中段消息压成"第几步 → 什么动作 → 结果"的清单。"""
    lines: list[str] = []
    step = 0
    i = 0
    while i < len(messages):
        msg = messages[i]
        role = msg.get("role")
        content = str(msg.get("content") or "")
        if role == "assistant":
            step += 1
            act, detail = _action_brief(content)
            obs = ""
            if i + 1 < len(messages) and messages[i + 1].get("role") == "user":
                obs = _obs_brief(str(messages[i + 1].get("content") or ""))
                i += 1
            line = f"- step {step}: {act}"
            if detail:
                line += f" `{detail}`"
            if obs:
                line += f" → {obs}"
            lines.append(line[:200])
        elif role == "user" and content.startswith("REPEAT WARNING"):
            lines.append(f"- step {step}: 系统警告过同一个动作重复执行")
        i += 1

    if not lines:
        return ""
    out: list[str] = [f"{_MARK} 更早的 {step} 步已折叠（原文为省上下文删除，下面是摘要）："]
    used = len(out[0])
    for ln in lines:
        if used + len(ln) > max_chars:
            out.append(f"- …另有 {len(lines) - len(out) + 1} 步未展开")
            break
        out.append(ln)
        used += len(ln) + 1
    return "\n".join(out)


def compact(
    messages: list[dict],
    keep_recent: int = 6,
    max_digest_chars: int = 2400,
) -> tuple[list[dict], dict]:
    """折叠中段。返回 (新 messages, 统计信息)。

    结构恒为：system + 题面 + [摘要] + 最近 keep_recent 条。
    摘要用 user 角色（模型更容易把它当"系统给的现状说明"）；
    如果最近一条恰好也是 user（观察），就和摘要合并，避免连续两条 user。
    """
    before = total_chars(messages)
    keep_recent = max(2, int(keep_recent))
    if len(messages) <= keep_recent + 3:
        return messages, {"dropped_msgs": 0, "before_chars": before,
                          "after_chars": before, "digest_chars": 0}

    head = messages[:2]
    middle = messages[2:-keep_recent]
    tail = messages[-keep_recent:]

    # 保证 role 交替：滑窗第一条可能是 user（观察），而摘要也是 user，
    # 直接拼会连出两条 user，部分模板会因此报错。把它并进中段一起折叠。
    if tail and tail[0].get("role") == "user":
        middle = middle + [tail[0]]
        tail = tail[1:]

    digest = digest_of(middle, max_digest_chars)
    if not digest:
        return messages, {"dropped_msgs": 0, "before_chars": before,
                          "after_chars": before, "digest_chars": 0}

    # 摘要直接接在题面那条 user 后面（而不是新插一条）：这样
    # system → user(题面+摘要) → assistant … 的交替不会被打破。
    lead = dict(head[1])
    lead["content"] = str(lead.get("content") or "") + "\n\n" + digest
    new = [head[0], lead] + tail

    after = total_chars(new)
    return new, {
        "dropped_msgs": len(middle),
        "before_chars": before,
        "after_chars": after,
        "digest_chars": len(digest),
    }
