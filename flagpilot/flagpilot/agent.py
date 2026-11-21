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

import hashlib
import re
import time
from dataclasses import asdict
from pathlib import Path
from statistics import median
from typing import Callable

from . import context, pricing, verify, storage, writeup
from .client import build_client
from .config import (Ablation, ModelSpec, RunConfig, TRACK_H,
                     make_model_spec, track_h_api_key)
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
  {"thought": "<short reasoning>", "action": "decode", "scheme": "rot13|base64|hex|url", "text": "<payload>"}
  {"thought": "<short reasoning>", "action": "smt", "code": "<z3py source; `from z3 import *` is injected>"}
  {"thought": "<short reasoning>", "action": "disasm", "path": "challenge/<binary>", "text": "<optional section>"}
  {"thought": "<short reasoning>", "action": "run_script", "script": "<candidate solve.py>"}'''

_RULES = """Rules:
- Challenge files are in ./challenge/ and are READ-ONLY. Write your own scratch files elsewhere in the current directory.
- There is NO network access. Do not try to reach the internet.
- You have a limited number of turns. Prefer informative commands over guessing.
- To finish you MUST submit both a flag and a standalone script.
- The script is re-run later in a FRESH directory that contains only ./challenge/ (same files, nothing else). It must print the flag to stdout by itself.
- Therefore every path in the script must be RELATIVE (./challenge/...). A hard-coded absolute path will not exist on replay.
- The script MUST NOT contain the flag string, nor its hex or base64 form. It has to derive the flag from the challenge files.
- If the flag is wrong, or if your script fails to reproduce the flag on replay, you will be told what went wrong and given one chance to fix it.
- Before you submit, test the script with the run_script action. It runs in a clean directory containing only ./challenge/ — the same condition as replay — and costs one turn instead of a rejected submission.
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

Working directory (absolute): {workdir}
Your shell already starts there. Use paths relative to it (./challenge/<file>);
do NOT cd elsewhere and do NOT search the filesystem.
You have at most {turns} turns. Start by inspecting the files."""

def flag_format_note(task) -> str:
    """题面末尾补一行 flag 格式（已知时）。general-019 就是算对了却把格式猜成 flag{...}。"""
    fmt = getattr(task, "flag_format", "")
    return (f"\nFlag format: {fmt} - submit the complete flag string, wrapper included." if fmt else "")


CORRECTION_TEMPLATE = (
    "INVALID ACTION.\n"
    "reason: {reason}\n"
    "Reply again with a single JSON object matching the schema. "
    "Do not add explanation outside the JSON."
)

# 截断与"不守协议"要分开纠正：前者是"话太多、预算被自己写满"，
# 再喂一句"你的 JSON 格式不对"只会让它继续写更多解释，越修越糟。
CORRECTION_TRUNCATED = (
    "OUTPUT TRUNCATED: your reply hit the {max_tokens}-token limit before the JSON closed.\n"
    "Put the JSON object FIRST, keep `thought` under 20 words, and write no prose outside it.\n"
    "If the action needs long code, split it into two shorter actions instead of one huge one."
)

CORRECTION_THINKING = (
    "EMPTY REPLY: the whole token budget went to hidden reasoning, so no action was emitted.\n"
    "Answer with the JSON object immediately and do not think first."
)

# 提交失败后的修复预算。刻意分成两个独立额度，互不挤占：
# - flag 错：改答案（proposal "最多两次提交"里的那一次）
# - 重放失败：改脚本（不是改答案）
# 分开记的理由很实际：flag 已经算对了、只是脚本跑不起来，不该把"改答案"的
# 额度吃掉；反过来，flag 改对了但脚本还挂，也还能再修一次。
MAX_FLAG_RETRIES = 1
MAX_SCRIPT_REPAIRS = 1

REPAIR_FLAG_TEMPLATE = (
    "FLAG REJECTED: the submitted flag is not correct.\n"
    "Keep working, then submit again with flag and script."
)

REPAIR_SCRIPT_TEMPLATE = (
    "REPLAY FAILED: solve.py did not reproduce the submitted flag in a clean directory.\n"
    "reason: {reason}\n"
    "detail: {detail}\n"
    "replay stdout (tail):\n{stdout}\n"
    "hint: {hint}\n"
    "Fix solve.py so it runs standalone against ./challenge/ and prints the flag "
    "to stdout, then submit again with the flag and the repaired script."
)

# 每种失败给一句可操作的修法。只报 reason 不给方向，模型通常会原样重发。
_SCRIPT_HINTS = {
    "literal_flag": "the script embeds the flag (or an encoded form of it). "
                    "It must derive the answer from the challenge files instead.",
    "script_error": "the script crashed. During replay it can only see ./challenge/; "
                    "scratch files written in earlier turns do not exist there.",
    "timeout": "the script exceeded the replay timeout. Avoid sleeps, unbounded loops, "
               "and brute force over large keyspaces.",
    "no_output": "the script finished but printed nothing. It must print the flag.",
    "hardcoded_no_material": "the script still printed the same flag after ./challenge/ "
                             "was emptied, or after the files were replaced with decoys "
                             "of the same names. Opening a file is not enough: the answer "
                             "has to be computed from what is read.",
}


def _replay_stdout_tail(replay, limit: int = 800) -> str:
    """重放输出只回传尾部。

    头部通常是模型自己写的调试打印，尾部才是 traceback 或最终结果——
    修复脚本需要的正是后者。整段塞回去会白白撑大上下文。
    """
    out = (replay.stdout or "").strip()
    return out[-limit:] if out else "(empty)"


def repair_message(replay) -> tuple[str, str] | None:
    """按重放结论决定要不要给一次修复机会，以及该说什么。

    返回 ``(kind, message)``，kind 取 ``"flag"``（改答案）或 ``"script"``（改脚本）。
    返回 None 表示这个结论已经是终局，再烧一轮也不会有新信息。

    为什么要有这个函数：原先只有 ``verdict == "wrong"`` 才重试，
    ``unverified`` 的四种失败（脚本崩 / 超时 / 含字面量 / 没输出）直接判死。
    但 flag 算对了、脚本跑不起来，恰恰是最该给一次机会的情形——
    模型完全不知道自己挂在哪，因为重放的 stdout/stderr 从来没回传过。
    """
    if replay.verdict == "wrong":
        if replay.reason in ("wrong_flag", "empty_flag"):
            return "flag", REPAIR_FLAG_TEMPLATE
        return None
    if replay.verdict == "unverified" and replay.reason in _SCRIPT_HINTS:
        return "script", REPAIR_SCRIPT_TEMPLATE.format(
            reason=replay.reason,
            detail=replay.detail or "(no detail recorded)",
            stdout=_replay_stdout_tail(replay),
            hint=_SCRIPT_HINTS[replay.reason],
        )
    # verified / reproduced 已经是终局；reproduced 是无 gold 题的最优结果。
    return None


def truncation_signal(comp, max_tokens: int) -> str:
    """这一轮输出是不是被 max_tokens 截断。返回 '' 表示没截断。

    为什么值得单独判一次：截断的**表象**是"没有解析到合法的 JSON 动作"，
    和"模型不守 JSON 协议"一模一样，但两者要采取的动作完全相反——
    协议问题该加强约束，截断该让它少写点。不分开，报告里就会把
    "预算配小了"记成"模型能力差"，这类标签错得毫无痕迹。

    判据优先用 finish_reason（服务端权威信号）；有些运行时不报，退回
    token 计数——但只信服务端回报的 usage，估算值撞上限不算证据。

    max_tokens 必须是**本轮实际生效**的那个值：hybrid 升级后跑的是 H 轨模型，
    它的输出上限与 L 轨的 cfg.model.max_tokens 不同，拿错基准就判不出来。
    """
    fr = (comp.finish_reason or "").strip().lower()
    if fr in ("length", "max_tokens"):
        return "length"
    if (comp.usage_source == "reported"
            and max_tokens > 0
            and comp.completion_tokens >= max_tokens):
        return "tokens"
    return ""


def turn_max_tokens(client, cfg: RunConfig) -> int:
    """本轮实际生效的输出上限。

    为什么不能直接用 cfg.model.max_tokens：hybrid 升级后跑的是 H 轨模型，
    它的输出上限与 L 轨不同；拿错基准，H 轨的截断就会被漏判成协议错误。
    测试替身不一定带 spec，此时退回 L 轨配置——总比抛 AttributeError 好。
    """
    spec = getattr(client, "spec", None)
    return int(getattr(spec, "max_tokens", 0) or 0) or cfg.model.max_tokens


def correction_message(kind: str, reason: str, max_tokens: int) -> str:
    """按失败种类选纠正语。kind 为空即普通协议错误。"""
    if kind == "truncated":
        return CORRECTION_TRUNCATED.format(max_tokens=max_tokens)
    if kind == "thinking_overflow":
        return CORRECTION_THINKING
    return CORRECTION_TEMPLATE.format(reason=reason)


# 头尾截断会把中段静默丢掉，而长 strings / 十六进制 dump / 日志里 flag 常常就在中间。
# 截断时把中段里"像答案"的行原样留下（flag 形状、picoCTF、key / password / secret），有上限。
_SALIENT = re.compile(r"[A-Za-z0-9_]{2,20}\{[^}\n]{2,120}\}|picoctf|flag|passw|secret|\bkey\s*[:=]", re.I)
SALIENT_MAX_LINES, SALIENT_MAX_CHARS = 12, 1200


def salient_lines(mid: str) -> list[str]:
    """中段里值得留下的行（去重、按出现顺序、每行最多 300 字符）。"""
    out: list[str] = []
    used = 0
    for ln in mid.splitlines():
        if not _SALIENT.search(ln):
            continue
        ln = ln.strip()[:300]
        if ln and ln not in out:
            out.append(ln)
            used += len(ln)
            if len(out) >= SALIENT_MAX_LINES or used >= SALIENT_MAX_CHARS:
                break
    return out


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
    keep = salient_lines(text[head:len(text) - tail]) if getattr(policy, "keep_salient", True) else []
    if keep:
        marker = (f"\n...[truncated {dropped} chars / {n_lines} lines total; kept {len(keep)} line(s) from the cut part"
                  " that look like flags or secrets]...\n" + "\n".join(keep) + "\n...[end of kept lines]...\n")
    else:
        marker = f"\n...[truncated {dropped} chars / {n_lines} lines total]...\n"
    out = text[:head] + marker + (text[-tail:] if tail else "")
    if len(out) > policy.hard_cap_chars:
        out = out[: policy.hard_cap_chars]
    return out, True


_READONLY_HINT = (
    "\nhint: ./challenge/ is READ-ONLY. Copy a file out before unpacking it, e.g. "
    "`cp challenge/x.tgz . && tar xf x.tgz`. Write every scratch file to the "
    "current directory (./), never into ./challenge/."
)

_PERMISSION_RE = re.compile(
    r"Permission denied|Read-only file system|Operation not permitted", re.I)


def _readonly_hint(act: Action, res) -> str:
    """往只读目录里写东西失败了，就把"该怎么办"直接写在报错后面。

    规则里其实写了"scratch files 写到当前目录"，但模型不看规则、只看上一条
    报错。实测（27B，NYU crypto）它会在 `tar xzf` 失败后去猜 /tmp、再
    `find /` 扫全盘超时，白烧 3 轮。把提示挂在它一定会读到的地方最省事。
    """
    if res.exit_code == 0 or res.timed_out:
        return ""
    blob = res.combined or ""
    if not _PERMISSION_RE.search(blob):
        return ""
    touched = f"{act.cmd}\n{act.code}\n{act.path}\n{blob}"
    return _READONLY_HINT if "challenge" in touched else ""


def format_observation(step: int, act: Action, res) -> str:
    status = "timeout" if res.timed_out else f"exit={res.exit_code}"
    body = res.combined
    header = f"[step {step}] {act.action}"
    if act.action == "shell":
        header += f": {act.cmd.splitlines()[0][:160]}"
    elif act.action in ("file", "strings"):
        header += f": {act.path}"
    note = f"\nhint: {res.note}" if getattr(res, "note", "") else ""
    return (f"{header}\n{status}  time={res.duration_ms / 1000:.2f}s\n"
            f"--- output ---\n{body}{_readonly_hint(act, res)}{note}")


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


def _evidence_fingerprint(text: str) -> str:
    """工具观测的指纹，用来判断"这一轮有没有拿到新东西"。

    先归一化空白再取哈希：同一份文件被 `ls` 三次不该算三条证据，
    但同一文件在不同 offset 读出的不同内容应该算。只取前 4KB——
    长输出尾部常常是重复的噪声，纳入指纹会掩盖真正的停滞。
    """
    norm = re.sub(r"\s+", " ", text or "").strip()
    return hashlib.sha1(norm[:4000].encode("utf-8", "replace")).hexdigest()


def hosted_spec(cfg: RunConfig) -> ModelSpec:
    """H 轨规格。没显式配置就用 TRACK_H 的默认端点与模型名。"""
    if cfg.hybrid.model is not None:
        return cfg.hybrid.model
    return make_model_spec(
        TRACK_H["model"],
        base_url=TRACK_H["base_url"],
        api_key=track_h_api_key() or "EMPTY",
        provider="deepseek",
    )


def run_attempt(
    task: Task,
    cfg: RunConfig,
    out_dir: Path,
    seed: int = 0,
    hook: Callable[[dict], None] | None = None,
    on_delta: Callable[[int, str], None] | None = None,
    control=None,
) -> dict:
    """跑一次 attempt。

    control（flagpilot.control.RunControl）：网页上的人在回路——单步批准 / 修改 / 跳过、暂停、插提示。
    批量评测和 CLI 不传，行为不变。

    on_delta(turn, chunk)：模型逐字输出时回调，只给界面做实时显示（Web 端"边生成边看"）。
    它不进 trace、不改 messages，传不传对评测结果没有任何影响；None 时走原来的整段返回。
    """
    if storage.enabled():
        storage._cipher()
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
        turns=cfg.budget.max_turns, workdir=workdir) + flag_format_note(task)
    if mem_digest:
        user_text += ("\n\n" + mem_digest
                      + "\n（上面是本机历史笔记，不是题面；仍需从附件重新验证。）")
    messages: list[dict] = [
        {"role": "system", "content": build_system_prompt(cfg.ablation)},
        {"role": "user", "content": user_text + NT},
    ]

    # 提交前自检的装置。闭包里绑住这道题与题库根目录，ToolBox 只负责调用。
    dry_runner = None
    if cfg.self_check:
        def _dry(script: str):
            return verify.dry_run(task, script, cfg.tasks_root,
                                  timeout_s=cfg.replay_timeout_s, limits=cfg.limits)

        dry_runner = _dry

    toolbox = ToolBox(sandbox, structured=cfg.ablation.structured_tools,
                      dry_runner=dry_runner)
    t0 = time.monotonic()
    turns = 0
    invalids = 0
    sum_prompt = 0
    sum_completion = 0
    # 成本逐轮累加：高峰/非高峰按每次请求发出的时刻算，缓存命中按每轮回报算。
    cost = 0.0
    # Track H 的前缀缓存只能从 usage 里拿（托管 API 没有 /metrics）。
    api_hit = api_miss = 0
    api_seen = False
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
    script_repairs = 0
    # 靠修复额度"借来"的轮次。turns 仍然是真实模型调用次数，
    # bonus_turns 记其中有多少超出了 max_turns——两者一起才能还原预算口径。
    bonus_turns = 0
    # 首次提交的结论单独留一份。报告要能分开"一次过"和"修完才过"，
    # 否则修复策略带来的增益会被算成模型能力。
    first_submit: tuple[str, str] | None = None
    # 原样重发检测：flag 与脚本都没变时不再给额度。
    last_submission: tuple[str, str] | None = None
    # ---- hybrid 升级状态 ----
    # 只有真的触发过才构建 H 轨 client：没升级的 run 不该多一条连接。
    hosted_client = None
    escalations = 0
    escalated_at = 0
    hosted_prompt = hosted_completion = 0
    hosted_cost = 0.0
    escalation_blocked = False
    evidence_seen: set[str] = set()
    stall_streak = 0
    compactions = 0
    # 小模型常见的死循环：同一个动作反复执行到轮次耗尽。
    # 这是 harness 该管的事，不该指望 prompt 约束住，所以放在循环里。
    recent: list[str] = []
    warned: set[str] = set()
    last_switch: dict | None = None
    fallback_turn = None

    while turns < cfg.budget.max_turns + bonus_turns:
        if time.monotonic() - t0 > cfg.budget.max_wall_s:
            reason = "wall_time"
            break
        if ctx_tokens > cfg.budget.max_prompt_tokens:
            reason = "context"
            break
        if sum_completion > cfg.budget.max_completion_tokens:
            reason = "completion_tokens"
            break

        # ---- hybrid：连续 k 轮没有新证据就升级到 H 轨 ----
        # 判据只用"有没有新证据"，不看轮次进度：轮次进度在长任务里天然会涨，
        # 拿它当信号会把"正在稳步推进"误判成停滞。
        if (cfg.hybrid.enabled and hosted_client is None and not escalation_blocked
                and stall_streak >= cfg.hybrid.stall_turns
                and escalations < cfg.hybrid.max_escalations
                and hosted_prompt + hosted_completion < cfg.hybrid.max_hosted_tokens):
            spec = hosted_spec(cfg)
            if not spec.api_key or spec.api_key == "EMPTY":
                # 没有 key 就永久关掉升级，别每轮都试一次
                escalation_blocked = True
                tr.event("escalate_skipped", turn=turns, reason="missing_api_key",
                         model=spec.name)
            else:
                hosted_client = build_client(spec)
                escalations += 1
                escalated_at = turns
                tr.event("escalate", turn=turns, from_model=cfg.model.name,
                         to_model=spec.name, stall_streak=stall_streak,
                         hosted_tokens=hosted_prompt + hosted_completion)

        if control is not None:
            # 看着运行的人插的提示：本轮调模型前加进对话，轨迹里标出来（评测批次不会有）
            for h in control.take_hints():
                tr.event("hint", turn=turns + 1, text=h)
                messages.append({"role": "user", "content": "HINT from the person watching this run (use it if it helps): " + h + NT})
        turns += 1
        active = hosted_client or client
        # 截断判定必须跟着 active 走，不能用 cfg.model（见 turn_max_tokens）。
        turn_max = turn_max_tokens(active, cfg)
        if on_delta is None:
            comp = active.complete(messages)
        else:
            # 绑定本轮轮次：回调晚到（流结束后才被调度）也不会记到下一轮头上
            comp = active.complete(messages, on_delta=lambda s, _t=turns: on_delta(_t, s))
        # 上下文放不下时客户端会临时调小本轮上限（client.fit_after_overflow）：
        # 截断判定和纠正语都要按实际发出的那个值，并在 model 事件里留下记录。
        sent_max = int(getattr(comp, "max_tokens", 0) or 0) or turn_max
        sum_prompt += comp.prompt_tokens
        sum_completion += comp.completion_tokens
        # L 与 H 分开记账：E4 要比的是 "verified solves vs hosted tokens and cost"，
        # 混成一个数就再也拆不开了。
        if hosted_client is not None and active is hosted_client:
            hosted_prompt += comp.prompt_tokens
            hosted_completion += comp.completion_tokens
            hosted_cost += pricing.turn_cost(hosted_spec(cfg), comp)
        else:
            cost += pricing.turn_cost(cfg.model, comp)
        if comp.cache_hit_tokens is not None or comp.cache_miss_tokens is not None:
            api_seen = True
            api_hit += comp.cache_hit_tokens or 0
            api_miss += comp.cache_miss_tokens or 0
        sum_model_ms += comp.latency_ms or 0
        ctx_tokens = comp.prompt_tokens
        if comp.ttft_ms > 0:
            ttfts.append(comp.ttft_ms)

        tr.event(
            "model",
            turn=turns,
            prompt_tokens=comp.prompt_tokens,
            completion_tokens=comp.completion_tokens,
            finish_reason=comp.finish_reason,
            reasoning_tokens=comp.reasoning_tokens,
            ttft_ms=comp.ttft_ms,
            latency_ms=comp.latency_ms,
            usage_source=comp.usage_source,
            cache_hit_tokens=comp.cache_hit_tokens,
            cache_miss_tokens=comp.cache_miss_tokens,
            error=comp.error,
            text=comp.text[:4000],
            **({"max_tokens": sent_max} if sent_max != turn_max else {}),
            **({"retries": comp.retries} if getattr(comp, "retries", 0) else {}),
            **({"served_by": active.last_model} if getattr(active, "last_model", cfg.model.name) != cfg.model.name else {}),
        )
        turn_max = sent_max

        sw = getattr(active, "switched", None)
        if sw and sw != last_switch:                    # 模型链换了一家：轨迹里记下（界面不显示，报告和复盘用）
            last_switch = dict(sw)
            fallback_turn = fallback_turn or turns
            tr.event("model_fallback", turn=turns, **sw)
        if comp.error:
            verdict, reason = "error", f"model: {comp.error[:200]}"
            break

        act, err = parse_action(comp.text, toolbox.allowed)
        # 失败原因分三类，各自要给的纠正语完全不同（见 correction_message）。
        # 只有第三类才是真的"不守协议"，前两类是预算配小了。
        kind = ""
        if act is None:
            if not (comp.text or "").strip() \
                    and comp.completion_tokens >= turn_max * 0.9:
                kind = "thinking_overflow"
                err = (f"空回复：{comp.completion_tokens} tok 用满但正文为空"
                       f"（max_tokens={turn_max} 疑似被思维链占满，"
                       f"检查 soft_no_think={cfg.model.soft_no_think}）")
            elif truncation_signal(comp, turn_max):
                kind = "truncated"
                started = "{" in (comp.text or "")
                err = (f"输出被截断：{comp.completion_tokens} tok 撞上 "
                       f"max_tokens={turn_max}"
                       f"（{'JSON 未闭合' if started else '还没开始写 JSON，预算全花在推理上'}）")
            else:
                kind = "protocol"
        if act is None:
            invalids += 1
            tr.event("invalid_action", turn=turns, kind=kind, reason=err)
            messages.append({"role": "assistant", "content": comp.text[:2000]})
            messages.append({"role": "user", "content": correction_message(
                kind, err, turn_max) + NT})
            # 协议错误不产生观测，等于这一轮没有新证据
            if cfg.hybrid.enabled and hosted_client is None:
                stall_streak += 1
            if invalids > 2:
                # 记下最后一次的 kind：截断导致的"轮次耗尽"和真的乱答
                # 在报告里必须能分开，否则又是一条假标签。
                verdict, reason = "budget", (kind if kind != "protocol" else "invalid_action")
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
                material_free=cfg.material_free,
            )
            tr.event("replay", **asdict(replay))
            if first_submit is None:
                first_submit = (replay.verdict, replay.reason)

            # 原样重发（flag 与脚本都没改）不再给额度：同样的输入必然得到
            # 同样的结论，多烧一轮没有信息增益。
            current = (submitted, act.script or "")
            same_as_last = current == last_submission
            last_submission = current

            offer = None if same_as_last else repair_message(replay)
            if offer is not None:
                kind, message = offer
                left = (MAX_FLAG_RETRIES - submit_retries if kind == "flag"
                        else MAX_SCRIPT_REPAIRS - script_repairs)
                # 墙钟是真实资源，任何模式下都不能突破；轮次则看配置：
                # 默认允许修复轮借轮次，否则"最后一轮才提交"的 run 拿不到
                # proposal 承诺的那次反馈（27B 实测里 crypto-093 就是这样）。
                has_time = time.monotonic() - t0 < cfg.budget.max_wall_s
                can_retry = has_time and (cfg.repair_extends_budget
                                          or turns < cfg.budget.max_turns)
                if left > 0 and can_retry:
                    if kind == "flag":
                        submit_retries += 1
                    else:
                        script_repairs += 1
                    if turns >= cfg.budget.max_turns:
                        bonus_turns += 1
                    tr.event("submit_repair", turn=turns, kind=kind,
                             reason=replay.reason,
                             flag_retries=submit_retries,
                             script_repairs=script_repairs,
                             bonus_turns=bonus_turns)
                    messages.append({"role": "assistant", "content": comp.text[:2000]})
                    # 末尾补 NT：这条反馈原先漏了 /no_think，Qwen3 在重试那一轮
                    # 会把思维链重新打开，和其他 user 消息的行为不一致。
                    messages.append({"role": "user", "content": message + NT})
                    continue

            verdict = replay.verdict
            reason = replay.reason
            break

        if control is not None:
            field = "cmd" if act.action == "shell" else "code" if act.action == "python" else ""
            t_wait = time.monotonic()
            shown = getattr(act, field) if field else (act.path or f"{act.scheme} {act.text}".strip() or act.cmd)
            op, edited, waited = control.gate(turns, act.action, field, shown,
                                              lambda info: tr.event("await", **info))
            t0 += time.monotonic() - t_wait          # 人想事情的时间不算进墙钟预算
            if waited:
                tr.event("decision", turn=turns, op=op, **({field: (edited or "")[:400]} if op == "edit" and field else {}))
            if op == "stop":
                verdict, reason = "error", "stopped by the user"
                break
            if op == "skip":
                messages.append({"role": "assistant", "content": comp.text[:2000]})
                messages.append({"role": "user", "content": "OBSERVATION\nSKIPPED: the person watching chose not to run this step. Take a different step." + NT})
                continue
            if op == "edit" and field and edited:
                setattr(act, field, edited)
                if field == "code":
                    act.cmd = edited[:400]
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

        # ---- stall 检测 ----
        # 指纹取工具原始输出，不取 format_observation 的结果：后者带
        # "[step N]" 和耗时，每轮都不同，拿它做指纹会永远判不出停滞。
        if cfg.hybrid.enabled and hosted_client is None:
            fp = _evidence_fingerprint(res.combined)
            if fp in evidence_seen:
                stall_streak += 1
            else:
                evidence_seen.add(fp)
                stall_streak = 0

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
        # 计价依据：价目表名，或 "flat"（--price-in/out），或 None（本地模型不计价）
        "price_table": cfg.model.price_table or (
            "flat" if (cfg.model.price_in_per_mtok or cfg.model.price_out_per_mtok) else None),
        "api_cache_hit_tokens": api_hit if api_seen else None,
        "api_cache_miss_tokens": api_miss if api_seen else None,
        "ablation": cfg.ablation.name,
        "model": cfg.model.name,
        # 主模型中途换成了备援：记下换成了谁、从哪一轮起、为什么（Runs 和界面据此说明是谁答的）
        **({"fallback": {**client.switched, "from_turn": fallback_turn, "answered_by": client.last_model}} if getattr(client, "switched", None) else {}),
        "server": server,
        # 升级账本：E4 要比 "verified solves vs hosted tokens and cost"，
        # 所以 H 轨的 token / 钱必须和 L 轨分开记。
        "hybrid": {
            "enabled": cfg.hybrid.enabled,
            "escalations": escalations,
            "escalated_at_turn": escalated_at,
            "hosted_model": getattr(hosted_client, "spec", None).name
            if hosted_client is not None else None,
            "hosted_prompt_tokens": hosted_prompt,
            "hosted_completion_tokens": hosted_completion,
            "hosted_tokens": hosted_prompt + hosted_completion,
            "hosted_cost_usd": round(hosted_cost, 6),
            "stall_turns": cfg.hybrid.stall_turns,
            "blocked": escalation_blocked,
        },
        "cost_usd_total": round(cost + hosted_cost, 6),
        "replay": asdict(replay) if replay else None,
        # 提交修复的账：报告里据此分开"一次过"与"修完才过"，
        # 也能量出修复策略本身贡献了多少通过率。
        "submit_retries": submit_retries,
        "script_repairs": script_repairs,
        # 借来的轮次数。turns - bonus_turns 才是"预算内"的调用数；
        # 不分开记，report 里的平均轮次就会被修复轮悄悄抬高。
        "bonus_turns": bonus_turns,
        "first_submit_verdict": first_submit[0] if first_submit else None,
        "first_submit_reason": first_submit[1] if first_submit else None,
        "run_dir": str(run_dir),
    }
    summary["writeup"] = storage.artifact_path(run_dir / "writeup.md").name
    storage.seal_directory(workdir)
    storage.seal_directory(run_dir / "replay")
    # 两道材料检验各自留下的脚本副本。这里原来写的是 "replay-no-material"，
    # 但 verify 实际建的是与 replay/ 同级的 no-material/，路径对不上，
    # 于是这两个目录一直没被加密。
    storage.seal_directory(run_dir / "no-material")
    storage.seal_directory(run_dir / "decoy-material")
    tr.close(summary)
    writeup.generate(run_dir)
    # 读记忆受 cfg.memory 控制，写记忆永远执行：批跑也能攒经验，
    # 但不会回头影响自己这一档的结果。
    # mock 模型除外——那是单测/dry-run，把它写进记忆会让真解题时
    # prompt 里混进一堆"mock 解出过"的假经验。
    if not str(cfg.model.name).lower().startswith("mock"):
        mem.record(task.name, summary, model=cfg.model.name)
    return summary
