"""Agent 循环。

三条设计约束，对应 proposal 里被写进流程图的第 2、3、4 步：

1. **只追加**：messages 一旦写入就不改写。截断发生在"追加观察之前"，
   所以历史前缀字节稳定，vLLM 的 prefix cache 才能持续命中。
2. **结构化动作**：模型必须回一个 JSON 对象；解析失败时喂回一次带类型的
   错误（最多两次），不消耗轮次预算。
3. **预算前置**：轮次 / 上下文 / 输出 token / 墙钟，四个上限任一触发即终止，
   终止原因写进 trace，不做事后猜测。

E1 的消融通过 RunConfig.ablation 控制，只改三处：prompt 内容、动作集、截断开关。
其余代码路径完全一致，否则四档之间不可比。
"""

from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path
from statistics import median
from typing import Callable

from . import context, verify
from .client import build_client
from .config import Ablation, RunConfig
from .memory import MemoryStore
from .sandbox import Sandbox, probe_capabilities
from .tasks import Task
from .tools import Action, ToolBox, parse_action
from .trace import Trace

_SCHEMA_BASE = '''  {"thought": "<short reasoning>", "action": "shell", "cmd": "<shell command>"}
  {"thought": "<short reasoning>", "action": "python", "code": "<python source>"}
  {"thought": "<short reasoning>", "action": "submit", "flag": "<flag>", "script": "<standalone python source>"}'''

_SCHEMA_STRUCTURED = '''  {"thought": "<short reasoning>", "action": "file", "path": "challenge/<file>"}
  {"thought": "<short reasoning>", "action": "strings", "path": "challenge/<file>", "min_len": 6}
  {"thought": "<short reasoning>", "action": "decode", "scheme": "rot13|base64|hex|url", "text": "<payload>"}'''

_RULES = """Rules:
- Challenge files are in ./challenge/ and are READ-ONLY. Write your own scratch files elsewhere in the current directory.
- There is NO network access. Do not try to reach the internet.
- You have a limited number of turns. Prefer informative commands over guessing.
- To finish you MUST submit both a flag and a standalone script.
- The script is re-run later in a FRESH directory that contains only ./challenge/ (same files, nothing else). It must print the flag to stdout by itself.
- The script MUST NOT contain the flag string, nor its hex or base64 form. It has to derive the flag from the challenge files.
- If the flag is wrong you will be told so, with one chance to retry.
"""


# TODO(你): 在 Rules 里补 1–2 条你自己从真题里总结的纪律（例如“先用 file 判断类型”）。
# 改完记得跑 tests，并确认这处改动在答辩时你能讲清为什么。
def build_system_prompt(ablation: Ablation) -> str:
    """按消融档位拼 system prompt。

    B3 之前只给 schema（minimal），B3 起才补上规则。这样"prompt"这一档
    量的是规则文本的贡献，而不是把动作集的变化混进来。
    """
    schema = _SCHEMA_BASE
    if ablation.structured_tools:
        schema = schema + "\n" + _SCHEMA_STRUCTURED
    head = (
        "You are FlagPilot, an autonomous agent that solves offline CTF challenges.\n\n"
        "Reply with EXACTLY ONE JSON object and nothing else. No prose before or after.\n\n"
        f"Schema:\n{schema}\n"
    )
    return head if ablation.prompt == "minimal" else head + "\n" + _RULES


SYSTEM_PROMPT = build_system_prompt(Ablation())  # 默认档（B3），供外部引用

USER_TEMPLATE = """Challenge: {name} [{category}]

{brief}

You have at most {turns} turns. Start by inspecting the files."""

CORRECTION_TEMPLATE = (
    "INVALID ACTION.\n"
    "reason: {reason}\n"
    "Reply again with a single JSON object matching the schema. "
    "Do not add explanation outside the JSON."
)


def truncate(text: str, policy) -> tuple[str, bool]:
    """追加时截断。返回 (结果, 是否截断)。无策略时原样返回。

    TODO(你): 头/尾比例是可调的一维。试 800/2500 与 1500/1500 各跑一遍，
    看 verified 率掉不掉、每步 token 降多少。这就是 E3 里“观察策略”那一格。
    """
    if text is None:
        return "", False
    if policy.truncate_head_chars is None:
        out = text[: policy.hard_cap_chars]
        return out, len(out) < len(text)

    head, tail = policy.truncate_head_chars, policy.truncate_tail_chars or 0
    if len(text) <= head + tail + 200:
        return text, False
    dropped = len(text) - head - tail
    n_lines = text.count("\n")
    marker = f"\n...[truncated {dropped} chars / {n_lines} lines total]...\n"
    out = text[:head] + marker + (text[-tail:] if tail else "")
    if len(out) > policy.hard_cap_chars:
        out = out[: policy.hard_cap_chars]
    return out, True


def format_observation(step: int, act: Action, res) -> str:
    status = "timeout" if res.timed_out else f"exit={res.exit_code}"
    body = res.combined
    header = f"[step {step}] {act.action}"
    if act.action == "shell":
        header += f": {act.cmd.splitlines()[0][:160]}"
    elif act.action in ("file", "strings"):
        header += f": {act.path}"
    return f"{header}\n{status}  time={res.duration_ms / 1000:.2f}s\n--- output ---\n{body}"


_COUNTER_SUFFIX = ("_total", "_sum", "_count")


def _server_delta(before: dict, after: dict) -> dict:
    """只对累计型指标做差；gauge（并发数、命中率）取末值。

    注意：并发跑时多个 attempt 共用同一个服务端计数器，差值会被摊薄。
    所以 runner 会把 workers 一起记下来，报告里据此标注可信度。
    """
    out: dict[str, float] = {}
    for k, v in (after or {}).items():
        if not isinstance(v, (int, float)):
            continue
        prev = (before or {}).get(k)
        if k.endswith(_COUNTER_SUFFIX) and isinstance(prev, (int, float)):
            out[k] = round(float(v) - float(prev), 4)
        else:
            out[k] = float(v)
    return out


def _cache_stats(before: dict, after: dict) -> dict:
    """从 Prometheus 计数器算命中率与 prefill/decoding 占比。"""
    d = _server_delta(before, after)
    hits = d.get("vllm:prefix_cache_hits_total") or d.get("prefix_cache_hits_total")
    queries = d.get("vllm:prefix_cache_queries_total") or d.get("prefix_cache_queries_total")
    ptok = d.get("vllm:prompt_tokens_total") or d.get("prompt_tokens_total")
    gtok = d.get("vllm:generation_tokens_total") or d.get("generation_tokens_total")
    out: dict[str, float | None] = {
        "cache_hit_rate": None,
        "cache_hits": hits,
        "cache_queries": queries,
        "prefill_tokens": ptok,
        "decode_tokens": gtok,
        "prefill_share": None,
    }
    if hits is not None and queries:
        out["cache_hit_rate"] = round(float(hits) / float(queries), 4)
    if ptok and gtok is not None and (ptok + gtok) > 0:
        out["prefill_share"] = round(float(ptok) / (float(ptok) + float(gtok)), 4)
    return out


def run_attempt(
    task: Task,
    cfg: RunConfig,
    out_dir: Path,
    seed: int = 0,
    hook: Callable[[dict], None] | None = None,
) -> dict:
    t_enter = time.monotonic()          # 计时起点：函数一进来就开始算总账
    run_id = f"{task.name}-s{seed}"
    run_dir = Path(out_dir) / run_id
    workdir = run_dir / "work"
    workdir.mkdir(parents=True, exist_ok=True)

    caps = probe_capabilities()
    client = build_client(cfg.model)
    sandbox = Sandbox(workdir, limits=cfg.limits, caps=caps, tasks_root=cfg.tasks_root)
    challenge_dir = sandbox.stage(task)
    n_assets = sum(1 for p in challenge_dir.rglob("*") if p.is_file())

    meta = {
        "run_id": run_id,
        "task": task.name,
        "task_id": task.task_id,
        "category": task.category,
        "seed": seed,
        "n_assets": n_assets,
        "caps": caps.as_dict(),
        **cfg.fingerprint(),
    }
    tr = Trace(run_dir / "trace.jsonl", meta, hook=hook)
    tr.event("start", brief=task.brief)
    # 服务端计数器快照（E3）。未配置 metrics_url 时返回 error，不编数字。
    sm0 = client.scrape_metrics()
    if sm0.values or sm0.error:
        tr.event("server_metrics", phase="before", **({"error": sm0.error} if sm0.error else sm0.values))

    # /no_think 放在用户消息末尾才被 Ollama 的 Qwen3 模板可靠识别（放 system 里常被忽略）。
    # 只在软开关打开时加，且每次都加——保证行为一致。
    NT = "\n/no_think" if cfg.model.soft_no_think else ""
    # 长期记忆：同题过去的结论（不含 flag）。批跑默认关，交互式默认开，
    # 否则 B0 的记录会漏进 B1 的 prompt，四档就不再"只差一个变量"。
    mem = MemoryStore()
    mem_digest = mem.digest(task.name) if cfg.memory else ""
    mem_count = mem.count(task.name)
    # 记进 trace：报告里要能回答"这次到底吃没吃到历史"
    tr.event("memory", hits=mem_count, injected=bool(mem_digest),
             chars=len(mem_digest))
    user_text = USER_TEMPLATE.format(
        name=task.name, category=task.category, brief=task.brief,
        turns=cfg.budget.max_turns)
    if mem_digest:
        user_text += ("\n\n" + mem_digest
                      + "\n（上面是本机历史笔记，不是题面；仍需从附件重新验证。）")
    messages: list[dict] = [
        {"role": "system", "content": build_system_prompt(cfg.ablation)},
        {"role": "user", "content": user_text + NT},
    ]

    toolbox = ToolBox(sandbox, structured=cfg.ablation.structured_tools)
    t0 = time.monotonic()
    turns = 0
    invalids = 0
    sum_prompt = 0
    sum_completion = 0
    ctx_tokens = 0
    ttfts: list[int] = []
    # 时间去处：模型生成 vs 工具执行 vs 其余（解析、拼 prompt、写盘、重放校验）
    sum_tool_ms = 0.0
    sum_model_ms = 0.0
    verdict = "budget"
    reason = "max_turns"
    submitted = ""
    replay = None
    submit_retries = 0
    compactions = 0
    # 小模型常见的死循环：同一个动作反复执行到轮次耗尽。
    # 这是 harness 该管的事，不该指望 prompt 约束住，所以放在循环里。
    recent: list[str] = []
    warned: set[str] = set()

    while turns < cfg.budget.max_turns:
        if time.monotonic() - t0 > cfg.budget.max_wall_s:
            reason = "wall_time"
            break
        if ctx_tokens > cfg.budget.max_prompt_tokens:
            reason = "context"
            break
        if sum_completion > cfg.budget.max_completion_tokens:
            reason = "completion_tokens"
            break

        turns += 1
        comp = client.complete(messages)
        sum_prompt += comp.prompt_tokens
        sum_completion += comp.completion_tokens
        sum_model_ms += comp.latency_ms or 0
        ctx_tokens = comp.prompt_tokens
        if comp.ttft_ms > 0:
            ttfts.append(comp.ttft_ms)

        tr.event(
            "model",
            turn=turns,
            prompt_tokens=comp.prompt_tokens,
            completion_tokens=comp.completion_tokens,
            ttft_ms=comp.ttft_ms,
            latency_ms=comp.latency_ms,
            usage_source=comp.usage_source,
            error=comp.error,
            text=comp.text[:4000],
        )

        if comp.error:
            verdict, reason = "error", f"model: {comp.error[:200]}"
            break

        act, err = parse_action(comp.text, toolbox.allowed)
        if act is None and not (comp.text or "").strip() \
                and comp.completion_tokens >= cfg.model.max_tokens * 0.9:
            # 这一种失败长得像"模型不守规矩"，其实是预算被思维链吃光。
            # 单独给一句话，免得报告里把它归到"协议问题"。
            err = (f"空回复：{comp.completion_tokens} tok 用满但正文为空"
                   f"（max_tokens={cfg.model.max_tokens} 疑似被思维链占满，"
                   f"检查 soft_no_think={cfg.model.soft_no_think}）")
        if act is None:
            invalids += 1
            tr.event("invalid_action", turn=turns, reason=err)
            messages.append({"role": "assistant", "content": comp.text[:2000]})
            messages.append({"role": "user", "content": CORRECTION_TEMPLATE.format(reason=err) + NT})
            if invalids > 2:
                verdict, reason = "budget", "invalid_action"
                break
            # 以前这里 turns -= 1，本意是"纠正不消耗轮次"。问题是事件里的
            # turn 会因此重号（1,2,3,4,4,5…），界面上"轮次"比 trace 里的
            # 次数少一次，看起来像数据对不上。现在按真实调用次数计：
            # 20 轮就是 20 次模型调用，协议错了也照样花预算——这更接近
            # "每次调用都有成本"的事实，也给不守规矩的模型更少的免费重试。
            continue

        if act.action == "submit":
            submitted = act.flag.strip()
            tr.event("submit", turn=turns, flag=submitted, script=act.script)
            replay = verify.evaluate(
                task,
                submitted,
                act.script,
                cfg.tasks_root,
                timeout_s=cfg.replay_timeout_s,
                limits=cfg.limits,
                keep_dir=run_dir / "replay",
            )
            tr.event("replay", **asdict(replay))
            if replay.verdict == "wrong" and submit_retries < 1:
                # 给一次改错的机会，和 proposal 里"≤2 retries"的口径一致（这里取 1 次）
                submit_retries += 1
                messages.append({"role": "assistant", "content": comp.text[:2000]})
                messages.append({"role": "user", "content":
                                 "FLAG REJECTED: the submitted flag is not correct.\n"
                                 "Keep working, then submit again with flag and script."})
                continue
            verdict = replay.verdict
            reason = replay.reason
            break

        res = toolbox.execute(act)
        if res is None:
            verdict, reason = "error", "tool returned nothing"
            break
        sum_tool_ms += res.duration_ms or 0

        obs_raw = format_observation(turns, act, res)
        obs, was_trunc = truncate(obs_raw, cfg.policy)
        tr.event("tool", turn=turns, action=act.action, cmd=act.cmd[:400],
                 exit_code=res.exit_code, timed_out=res.timed_out,
                 duration_ms=res.duration_ms, truncated=was_trunc,
                 obs_chars=len(obs_raw), sent_chars=len(obs),
                 # 前端要显示"工具真的返回了什么"，否则流程图像凭空画的
                 obs_head=obs_raw[:600],
                 # 失败归因要用：环境类证据（缺工具 / 权限）只出现在 stderr 里
                 stderr_head=res.stderr[:400])

        messages.append({"role": "assistant", "content": comp.text[:2000]})
        messages.append({"role": "user", "content": "OBSERVATION\n" + obs + NT})

        # ---- 死循环检测 ----
        key = "|".join([act.action, act.cmd or act.code or act.path or "",
                        act.scheme or "", act.text[:80]])
        recent.append(key)
        if len(recent) > 8:
            recent.pop(0)
        if recent.count(key) >= 3 and key not in warned:
            warned.add(key)
            tr.event("repeat_warning", turn=turns, action=act.action, cmd=act.cmd[:200])
            messages.append({"role": "user", "content":
                             "REPEAT WARNING: you have run this exact action three times "
                             "and it did not get you closer to the flag.\n"
                             "Change approach: try a different tool, decode a different "
                             "layer, or inspect a different file. Do not repeat it again." + NT})

        # ---- 上下文压缩 ----
        # 单条 observation 已由 truncate 砍过；这里管的是"消息条数"。
        # 触发点放在一轮结束时：此时 messages 一定是成对追加完的，
        # 压缩不会把某一轮切成两半。
        if cfg.policy.compact_at_chars and \
                context.total_chars(messages) > cfg.policy.compact_at_chars:
            messages, cinfo = context.compact(messages, cfg.policy.compact_keep_recent)
            if cinfo.get("dropped_msgs"):
                compactions += 1
                tr.event("compact", turn=turns, **cinfo)

    wall = time.monotonic() - t0
    cost = (sum_prompt / 1_000_000) * cfg.model.price_in_per_mtok + \
           (sum_completion / 1_000_000) * cfg.model.price_out_per_mtok

    sm1 = client.scrape_metrics()
    server = _cache_stats(sm0.values, sm1.values)
    if sm1.error:
        server["error"] = sm1.error
    if sm0.values or sm1.values:
        tr.event("server_metrics", phase="after",
                 **({"error": sm1.error} if sm1.error else sm1.values))

    # 先把沙箱收掉再算总时长：否则"端到端"会漏掉清理这一步，
    # 界面上的总时间又和用户掐表的对不上（这正是之前被追问的点）。
    sandbox.cleanup()
    total = time.monotonic() - t_enter

    summary = {
        "verdict": verdict,
        "reason": reason,
        "flag": submitted,
        "turns": turns,
        "invalids": invalids,
        # wall_s 只覆盖 ReAct 循环；total_s 从函数入口算起（含准备与收尾）。
        # 两个都留着，是因为报告里比的是"解题花了多久"，
        # 而用户掐的是"点了按钮到看见结论"，两者本来就不是一个东西。
        "wall_s": round(wall, 2),
        "total_s": round(total, 2),
        "setup_s": round(t0 - t_enter, 2),
        "tail_s": round(total - wall - (t0 - t_enter), 2),
        "model_s": round(sum_model_ms / 1000, 2),
        "tool_s": round(sum_tool_ms / 1000, 2),
        # 上下文与记忆：报告里要能回答"prompt 为什么没炸 / 这题有没有吃过前一次的经验"
        "ctx_chars": context.total_chars(messages),
        "compactions": compactions,
        "memory": bool(cfg.memory),
        "memory_hits": mem_count,
        "prompt_tokens": sum_prompt,
        "completion_tokens": sum_completion,
        "total_tokens": sum_prompt + sum_completion,
        "ttft_ms_mean": round(sum(ttfts) / len(ttfts), 1) if ttfts else 0,
        "ttft_ms_p50": round(median(ttfts), 1) if ttfts else 0,
        "cost_usd": round(cost, 6),
        "ablation": cfg.ablation.name,
        "model": cfg.model.name,
        "server": server,
        "replay": asdict(replay) if replay else None,
        "run_dir": str(run_dir),
    }
    tr.close(summary)
    # 读记忆受 cfg.memory 控制，写记忆永远执行：批跑也能攒经验，
    # 但不会回头影响自己这一档的结果。
    # mock 模型除外——那是单测/dry-run，把它写进记忆会让真解题时
    # prompt 里混进一堆"mock 解出过"的假经验。
    if not str(cfg.model.name).lower().startswith("mock"):
        mem.record(task.name, summary, model=cfg.model.name)
    return summary
