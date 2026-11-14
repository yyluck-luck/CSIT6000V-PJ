"""重放验证器。

判据（proposal 第 2 节）：
    1. 提交的 flag 必须与 gold_sha256 一致            → 否则 wrong
    2. solve.py 里不能出现 flag 的字面量及其变体        → 否则 unverified:literal_flag
    3. 在一个全新目录（只含题目附件）里重跑 solve.py    → 输出含该 flag 才算 verified

注意一个刻意的设计：本模块**永远不需要明文 gold**。
比对用哈希，重放判定用"提交的 flag 是否出现在输出里"——因为第一步已经
证明它就是 gold。这样明文答案可以完全不进集群。
"""

from __future__ import annotations

import base64
import codecs
import hashlib
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .config import SandboxLimits
from .sandbox import Sandbox, probe_capabilities
from .tasks import Task, stage_assets


def sha256(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()


def flag_matches(submitted: str, gold_sha256: str) -> bool:
    s = (submitted or "").strip()
    if not s or len(s) > 256:
        return False
    return sha256(s) == gold_sha256


def literal_variants(flag: str) -> dict[str, str]:
    """脚本里不该出现的形态。逐条给名字，报告里可以说清是怎么被抓的。

    TODO(你): 加一种你认为模型真会用上的混淆形态（比如拆成整数列表、gzip 后 base64）。
    注意这是启发式，不是证明——加完在报告里如实说明它的边界。
    """
    raw = flag.strip()
    b = raw.encode()
    out = {"exact": raw}
    try:
        out["hex"] = b.hex()
    except Exception:
        pass
    try:
        out["base64"] = base64.b64encode(b).decode()
    except Exception:
        pass
    out["reversed"] = raw[::-1]
    try:
        out["rot13"] = codecs.encode(raw, "rot13")
    except Exception:
        pass
    # 只保留长度足够、不至于误伤的形态
    return {k: v for k, v in out.items() if len(v) >= 12}


def material_text(task: Task, tasks_root: Path, max_bytes: int = 2_000_000) -> str:
    """题目材料的全文（题面 + 附件）。

    用来消掉一类假阳性：ROT13 这类"自反"变换下，flag 的密文形态**就是题目给的输入**。
    如果脚本里出现的形态在材料里本来就有，那它是在引用题目，不是在抄答案。
    """
    parts = [task.query or "", task.title or ""]
    base = Path(tasks_root) / task.assets_dir
    if base.exists():
        for f in base.rglob("*"):
            if not f.is_file() or f.name == "task.json":
                continue
            try:
                if f.stat().st_size > max_bytes:
                    continue
                parts.append(f.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
    return "\n".join(parts)


def scan_script(script: str, flag: str, material: str = "") -> str | None:
    """返回命中的形态名；没有命中返回 None。

    这是启发式，不是证明。它挡的是"背出答案再硬编码"，挡不住刻意混淆
    （把 flag 拆成整数数组之类）。真正的防线是 flag 不在沙箱里 + 记忆探针。

    material 非空时做一次豁免：某些形态本来就写在题目里（如 ROT13 题的密文），
    此时命中不算作弊——否则会把完全正确的解法判成作弊。
    """
    if not script:
        return None
    norm = script.replace("\\n", "\n")
    for name, variant in literal_variants(flag).items():
        hit = variant in norm or (name in ("hex", "base64") and variant.lower() in norm.lower())
        if not hit:
            continue
        if material and variant in material:
            continue
        return name
    return None


@dataclass
class ReplayResult:
    verdict: str          # verified | reproduced | unverified | wrong
    reason: str           # match | reproduced_no_gold | wrong_flag | literal_flag | timeout | script_error | no_output
    detail: str = ""
    stdout: str = ""
    exit_code: int | None = None
    duration_ms: int = 0
    # 这道题有没有 gold 可校验。自建题通常为 False：
    # 此时最好结果只能是 reproduced，永远不会是 verified。
    gold_checked: bool = True

    @property
    def ok(self) -> bool:
        return self.verdict == "verified"


def evaluate(
    task: Task,
    submitted_flag: str,
    script: str,
    tasks_root: Path,
    timeout_s: float = 300.0,
    limits: SandboxLimits | None = None,
    keep_dir: Path | None = None,
) -> ReplayResult:
    gold_known = task.has_gold

    # 第一道：flag 对不对。没有 gold 就跳过——但会体现在 gold_checked 里，
    # 上游据此把结论降级成 reproduced，绝不冒充 verified。
    if gold_known and not flag_matches(submitted_flag, task.gold_sha256):
        return ReplayResult("wrong", "wrong_flag", "flag 与 gold 哈希不一致")

    if not (submitted_flag or "").strip():
        return ReplayResult("wrong", "empty_flag", "没有提交任何 flag", gold_checked=gold_known)

    # 第二道：脚本里有没有直接写答案（题目材料里本来就有的形态予以豁免）
    hit = scan_script(script, submitted_flag, material_text(task, tasks_root))
    if hit:
        return ReplayResult("unverified", "literal_flag", f"脚本含 flag 的 {hit} 形态",
                            gold_checked=gold_known)

    if not (script or "").strip():
        return ReplayResult("unverified", "no_output", "没有提供 solve.py",
                            gold_checked=gold_known)

    # 第三道：干净目录重跑
    if keep_dir:
        replay_dir = Path(keep_dir)
        if replay_dir.exists():
            shutil.rmtree(replay_dir, ignore_errors=True)
        replay_dir.mkdir(parents=True, exist_ok=True)
    else:
        # 没给 keep_dir（直接调用 evaluate 的场景）也不能依赖 tempfile：
        # 受限环境下"创建后立刻 unlink"会失败，整条验证链就断了。
        base = Path(tasks_root) / ".replay-tmp"
        base.mkdir(parents=True, exist_ok=True)
        replay_dir = base / f"{task.name}-{os.getpid()}-{int(time.time() * 1000)}"
        replay_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 只从题库根目录拷附件，不用 agent 工作目录里的副本——
        # 防止 agent 自己"加工"过题目文件来让脚本跑通
        stage_assets(task, replay_dir / "challenge", root=tasks_root)

        solve_path = replay_dir / "solve.py"
        solve_path.write_text(script, encoding="utf-8")

        sb = Sandbox(replay_dir, limits=limits or SandboxLimits(), caps=probe_capabilities())
        res = sb.run_shell(f"{sb.caps.python} solve.py", timeout=timeout_s)

        if res.timed_out:
            return ReplayResult("unverified", "timeout", f"重放超时（{timeout_s}s）",
                                res.stdout, res.exit_code, res.duration_ms,
                                gold_checked=gold_known)

        target = submitted_flag.strip()
        if target and target in res.stdout:
            if gold_known:
                return ReplayResult("verified", "match", "重放复现出同一个 flag",
                                    res.stdout, res.exit_code, res.duration_ms)
            return ReplayResult(
                "reproduced", "reproduced_no_gold",
                "重放复现成功；但该题没有标准答案（gold），无法判定答案是否正确",
                res.stdout, res.exit_code, res.duration_ms, gold_checked=False,
            )

        if not res.stdout.strip():
            detail = f"重放无输出（exit={res.exit_code}）"
            if res.stderr.strip():
                detail += "；stderr: " + res.stderr.strip()[:300]
            # 崩了 != 没打印。exit≠0 且 stderr 有内容，是脚本本身跑挂了
            # （比如引用了题目里不存在的字段），语义上属于 script_error。
            # 把它记成 no_output 会让报告的失败分布失真：
            # "写不出复现脚本" 和 "脚本写错" 是两种完全不同的失败。
            reason = "script_error" if (res.exit_code not in (0, None) and res.stderr.strip()) else "no_output"
            return ReplayResult("unverified", reason, detail, res.stdout,
                                res.exit_code, res.duration_ms, gold_checked=gold_known)

        return ReplayResult("unverified", "script_error",
                            f"输出里没有该 flag（exit={res.exit_code}）",
                            res.stdout, res.exit_code, res.duration_ms,
                            gold_checked=gold_known)
    finally:
        # replay_dir 不删：要么是 keep_dir（留作证据），要么在题库的
        # .replay-tmp 下（供事后核对）。删不删都不影响判定结果。
        pass
