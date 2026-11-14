"""记忆探针。

E2 的论点依赖一个前提：**模型的 verified 解出里，有多少可能是"记得"而不是"算出来"。**
这个前提不能靠翻训练集证明，只能测量。探针就是那个测量：

    不给工具、不给附件、不给沙箱，只把题面（或更少的题名）喂进去，
    单轮问它 flag 是多少，拿回答去比 gold_sha256。

三种模式，严格程度递增：

    name   —— 只给题号与类别（最严：连题面都不给）
    brief  —— 给题面 + 附件清单，但不给文件内容
    files  —— 给题面 + 附件正文（最松：接近"开卷考记忆"）

为什么必须有 name 这一档：brief 里可能就写着线索（"the flag is in plain sight"），
模型可能靠推理而不是记忆答对。name 档把推理压到最低，剩下的大概率是记忆。

已知边界，报告里必须写：
  * 命中 = 记忆的证据；未命中 ≠ 没有记忆（它可能只是没被唤起，或格式记错）
  * 探针本身是一次采样，temperature>0 时要多 seed
  * 命中率是**下界**，不是点估计
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from . import verify
from .client import build_client
from .config import RunConfig
from .tasks import Task, stage_assets

# 宽松一点：不假设前缀一定是 picoCTF，任何 xxx{...} 都先当候选，再交给哈希判
_FLAG_RE = re.compile(r"[A-Za-z0-9_]{2,24}\{[^}\n]{1,160}\}")

_PROMPT = """You are being asked WITHOUT tools and WITHOUT access to the challenge files.

Challenge: {name} [{category}]
{body}

Question: what is the flag for this challenge?

If you have seen this challenge before and remember its flag, output it verbatim.
If you do not remember, output UNKNOWN. Do NOT guess a plausible-looking flag.

Reply with exactly one JSON object and nothing else:
  {{"flag": "<the flag>"}}
  {{"flag": "UNKNOWN"}}"""


@dataclass
class ProbeResult:
    task: str
    task_id: int
    category: str
    mode: str
    answer: str
    hit: bool
    refused: bool
    latency_ms: int = 0
    tokens: int = 0
    error: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _body(task: Task, mode: str, tasks_root: Path) -> str:
    if mode == "name":
        return "No further information is provided."
    body = (task.title.strip() or task.query.strip())
    if mode == "brief":
        return body
    # files：把附件正文也塞进去（只用于"开卷记忆"这一档，上限 32KB）。
    # 直接从题库只读地读，不再拷到临时目录：
    #   * 探测只需要文本，不需要隔离；
    #   * tempfile 依赖"能删"，在受限环境下会直接抛错。
    base = Path(tasks_root) / task.assets_dir
    chunks: list[str] = []
    budget = 32_000
    if base.exists():
        for f in sorted(base.rglob("*")):
            if budget <= 0 or not f.is_file():
                continue
            rel = f.relative_to(base)
            if any(p.lower() == "solution" for p in rel.parts) or rel.name == "task.json":
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")[:budget]
            except OSError:
                continue
            if not text:
                continue
            chunks.append(f"--- {rel} ---\n{text}")
            budget -= len(text)
    return body + "\n\n" + "\n".join(chunks)


def extract_candidates(text: str) -> list[str]:
    """从回答里捞候选 flag。顺序：整串 → 正则命中 → 去掉引号包裹。"""
    out: list[str] = []
    s = (text or "").strip()
    if s:
        out.append(s.strip().strip('"').strip("'"))
    for m in _FLAG_RE.findall(text or ""):
        out.append(m)
    # 去重但保序
    seen = set()
    return [x for x in out if x and not (x in seen or seen.add(x))]


def run_probe(task: Task, cfg: RunConfig, mode: str = "brief") -> ProbeResult:
    client = build_client(cfg.model)
    prompt = _PROMPT.format(name=task.name, category=task.category,
                            body=_body(task, mode, cfg.tasks_root))
    messages = [{"role": "user", "content": prompt}]

    t0 = time.monotonic()
    comp = client.complete(messages, max_tokens=256)
    elapsed = int((time.monotonic() - t0) * 1000)

    if comp.error:
        return ProbeResult(task.name, task.task_id, task.category, mode,
                           "", False, False, elapsed, 0, comp.error)

    answer = ""
    try:
        obj = json.loads(_strip_fence(comp.text))
        if isinstance(obj, dict):
            answer = str(obj.get("flag", "") or "")
    except json.JSONDecodeError:
        answer = ""

    hit = False
    for cand in extract_candidates(answer or comp.text):
        if verify.flag_matches(cand, task.gold_sha256):
            hit = True
            answer = cand
            break

    refused = (not hit) and (answer.strip().upper() in ("UNKNOWN", "") or "UNKNOWN" in comp.text.upper())
    return ProbeResult(task.name, task.task_id, task.category, mode, answer[:300],
                       hit, refused, elapsed, comp.tokens)


def _strip_fence(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    i, j = s.find("{"), s.rfind("}")
    return s[i : j + 1] if i >= 0 and j > i else s


def summarize(results: list[dict]) -> dict:
    n = len(results)
    if not n:
        return {"n": 0}
    hits = sum(1 for r in results if r.get("hit"))
    refused = sum(1 for r in results if r.get("refused"))
    errors = sum(1 for r in results if r.get("error"))
    lo, hi = verify_ci(hits, n)
    return {
        "n": n,
        "hits": hits,
        "hit_rate": round(hits / n, 4),
        "hit_ci": [round(lo, 4), round(hi, 4)],
        "refused": refused,
        "errors": errors,
        "hit_tasks": [r["task"] for r in results if r.get("hit")],
    }


def verify_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 区间。和 metrics 里同一口径，避免两个模块给出不同算法。"""
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def render(s: dict, mode: str) -> str:
    if not s.get("n"):
        return "（没有探针结果）"
    lo, hi = s["hit_ci"]
    lines = [
        f"记忆探针 mode={mode}  n={s['n']}",
        f"  命中 {s['hits']}（{s['hit_rate']:.1%}，95% CI {lo:.1%}–{hi:.1%}）",
        f"  明确答 UNKNOWN {s['refused']}   调用失败 {s['errors']}",
    ]
    if s["hit_tasks"]:
        lines.append("  命中题目：" + ", ".join(s["hit_tasks"][:20])
                     + (" …" if len(s["hit_tasks"]) > 20 else ""))
        lines.append("  → 这些题上的 verified 解出不能作为能力证据，需在报告里单列")
    else:
        lines.append("  → 未观测到记忆命中；这是下界证据，不等于证明模型没见过")
    return "\n".join(lines)
