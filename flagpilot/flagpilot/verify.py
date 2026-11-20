"""重放验证器。

判据（proposal 第 2 节）：
    1. 提交的 flag 必须与 gold_sha256 一致            → 否则 wrong
    2. solve.py 里不能出现 flag 的字面量及其变体        → 否则 unverified:literal_flag
    3. 在一个全新目录（只含题目附件）里重跑 solve.py    → 输出含该 flag 才算 verified
    4. 把题目附件清空再跑一次                          → 还能打印出该 flag 就是没在用材料

第 4 条是"字面量检查"的补丁。字面量检查只能抓住它认识的那几种编码
（原文 / hex / base64 / 反转 / rot13），换一种写法就漏了；而"删掉材料
还能答对"是与编码无关的判据——答案要么来自材料，要么来自模型自己的记忆。

注意一个刻意的设计：本模块**永远不需要明文 gold**。
比对用哈希，重放判定用"提交的 flag 是否出现在输出里"——因为第一步已经
证明它就是 gold。这样明文答案可以完全不进集群。
"""

from __future__ import annotations

import base64
import codecs
import hashlib
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .config import SandboxLimits
from .sandbox import Sandbox, probe_capabilities
from .tasks import Task, stage_assets

# "长得像 flag"的形状。只用于提交前自检的提示，不用于判定——
# 判定用的是"输出里有没有提交的那个 flag"。
_FLAG_SHAPE = re.compile(r"[A-Za-z0-9_]{2,20}\{[^}\n]{1,120}\}")


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
class DryRunResult:
    """提交前自检的结果。

    它**不判对错**：既没有 gold 也不知道模型打算交哪个 flag，只回答
    "这个脚本在干净目录里能不能跑起来、有没有打印出像 flag 的东西"。
    判对错仍然是 evaluate() 的事。

    reason: printed | no_flag | no_output | script_error | timeout
    """

    ok: bool
    reason: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_ms: int = 0
    duration_hint: str = ""

    def as_observation(self) -> str:
        """给模型看的一段话。只讲事实 + 一句可操作建议。"""
        if self.reason == "printed":
            n = len(_FLAG_SHAPE.findall(self.stdout or ""))
            return (f"脚本在干净目录里跑通了，打印出 {n} 个 flag 形态。"
                    f"如果这就是你要提交的答案，可以 submit 了。")
        if self.reason == "timeout":
            return "脚本在自检里超时。重放时会用同样的超时，先把耗时降下来。"
        if self.reason == "script_error":
            return ("脚本在干净目录里跑挂了。注意：重放时它只能看到 ./challenge/，"
                    "你之前写的临时文件都不在。")
        if self.reason == "no_output":
            return "脚本跑完了但什么都没打印。它必须把 flag 打到 stdout。"
        return "脚本有输出，但里面没有像 flag 的东西。确认它打印的是最终答案。"


@dataclass
class ReplayResult:
    verdict: str          # verified | reproduced | unverified | wrong
    reason: str           # match | reproduced_no_gold | wrong_flag | empty_flag
                          # literal_flag | hardcoded_no_material | timeout
                          # script_error | no_output
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


def _make_run_dir(task: Task, tasks_root: Path, keep_dir: Path | None,
                  tag: str) -> Path:
    """给一次脚本执行准备目录。

    给了 keep_dir 就用它（留作证据）；否则放到题库的 .replay-tmp 下。
    刻意不用 tempfile：受限环境下"创建后立刻 unlink"会失败，整条链就断了。
    """
    if keep_dir:
        d = Path(keep_dir)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        return d
    base = Path(tasks_root) / f".{tag}-tmp"
    base.mkdir(parents=True, exist_ok=True)
    d = base / f"{task.name}-{os.getpid()}-{int(time.time() * 1000)}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _stage(dest: Path, task: Task, tasks_root: Path, with_material: bool) -> None:
    """准备执行目录。

    with_material=False 时**保留一个空的 challenge/ 目录**而不是把它删掉：
    删掉会让 `os.listdir('challenge')` 抛 FileNotFoundError，而空目录让它
    返回 []。两者都能让依赖材料的脚本失败，但空目录更接近"材料恰好是空的"，
    不会把"路径判断写错"误判成"没用材料"。
    """
    if with_material:
        stage_assets(task, dest / "challenge", root=tasks_root)
    else:
        (dest / "challenge").mkdir(parents=True, exist_ok=True)


def _run_script(dest: Path, script: str, timeout_s: float,
                limits: SandboxLimits | None):
    (dest / "solve.py").write_text(script, encoding="utf-8")
    sb = Sandbox(dest, limits=limits or SandboxLimits(), caps=probe_capabilities())
    # python_cmd 而不是 caps.python：容器模式下宿主绝对路径不存在，
    # 拼进去会变成 "no such file or directory"，重放必然判死。
    return sb.run_shell(f"{sb.python_cmd} solve.py", timeout=timeout_s)


def dry_run(
    task: Task,
    script: str,
    tasks_root: Path,
    timeout_s: float = 120.0,
    limits: SandboxLimits | None = None,
    keep_dir: Path | None = None,
) -> DryRunResult:
    """提交前自检：把脚本放进只含 challenge/ 的干净目录跑一遍。

    为什么要这个动作：原先模型只能通过 submit 拿到重放反馈，而 submit 是
    有代价的（消耗 flag/脚本修复额度，甚至可能在最后一轮才提交、根本用不上
    额度）。自检把"脚本能不能跑通"这一半的信息提前到提交之前，代价只是一轮。
    """
    if not (script or "").strip():
        return DryRunResult(False, "no_output", stderr="没有提供脚本")

    dest = _make_run_dir(task, tasks_root, keep_dir, "dryrun")
    _stage(dest, task, tasks_root, with_material=True)
    res = _run_script(dest, script, timeout_s, limits)
    stdout = res.stdout or ""
    tail = _stderr_tail(res.stderr, dest)

    if res.timed_out:
        return DryRunResult(False, "timeout", stdout, tail, res.exit_code,
                            res.duration_ms, f"超时（{timeout_s}s）")
    if _FLAG_SHAPE.search(stdout):
        return DryRunResult(True, "printed", stdout, tail, res.exit_code,
                            res.duration_ms)
    if not stdout.strip():
        reason = "script_error" if (res.exit_code not in (0, None) and tail) else "no_output"
        return DryRunResult(False, reason, stdout, tail, res.exit_code, res.duration_ms)
    return DryRunResult(False, "no_flag", stdout, tail, res.exit_code, res.duration_ms)


_DECOY_FILLER = b"material replaced for the dependence check\n"


def _decoy_bytes(n: int) -> bytes:
    """与原文等长的假内容。

    保持长度是为了不触发"文件大小不对"这类断言——那类断言本身就在依赖材料，
    应该让它通过（从而被判据抓住），而不是让它崩掉（从而蒙混过关）。
    """
    if n <= 0:
        return b""
    return (_DECOY_FILLER * (n // len(_DECOY_FILLER) + 1))[:n]


def _stage_decoy(dest: Path, task: Task, tasks_root: Path) -> None:
    """照原样摆出同名附件，但把内容换成等长的假材料。

    比"清空"强在：脚本里那行 `open('./challenge/x')` 会成功返回，
    于是"读了但没用"的脚本照样打印出硬编码的 flag，被这条抓住。
    """
    target = dest / "challenge"
    target.mkdir(parents=True, exist_ok=True)
    base = Path(tasks_root) / task.assets_dir
    if not base.exists():
        return
    for f in base.rglob("*"):
        if not f.is_file() or f.name == "task.json":
            continue
        out = target / f.relative_to(base)
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            n = f.stat().st_size
        except OSError:
            n = 0
        out.write_bytes(_decoy_bytes(n))


def survives_without_material(
    task: Task,
    script: str,
    submitted_flag: str,
    tasks_root: Path,
    timeout_s: float = 300.0,
    limits: SandboxLimits | None = None,
    parent: Path | None = None,
) -> tuple[bool, str]:
    """两段检验：答案是否真的从材料推出来。

    第一段**清空** challenge/，抓"根本不读文件"的脚本。
    第二段摆出**同名但内容被替换**的假材料，抓"读了但没用"的脚本。

    第二段是 30052 实跑暴露的漏洞：模型被拒一次后补了一行
    `open('./challenge/rot13.py')` 却从不使用读到的内容，仍打印硬编码的
    flag；清空能挡住它（open 抛 FileNotFoundError），换成假材料就挡不住了。
    反过来，真正从材料算答案的脚本在假材料上会算出别的东西或直接报错，
    所以两段都过才算"答案来自材料"。

    返回 (是否可疑, 说明)。只在题目**确实有附件**时调用——没有附件的题
    本来就不该读材料，拿这条去判它是误伤。
    """
    flag = (submitted_flag or "").strip()
    if not flag:
        return False, ""
    parent = Path(parent) if parent is not None else None

    def _dir(name: str) -> Path:
        if parent is not None:
            d = parent / name
            d.mkdir(parents=True, exist_ok=True)
            return d
        return _make_run_dir(task, tasks_root, None, name.replace("-", ""))

    # 第一段：材料清空
    empty = _dir("no-material")
    _stage(empty, task, tasks_root, with_material=False)
    res = _run_script(empty, script, timeout_s, limits)
    if not res.timed_out and flag in (res.stdout or ""):
        return True, "清空 challenge/ 后仍打印出同一个 flag：答案不是从题目材料推出来的"

    # 第二段：材料在，但内容被换掉
    decoy = _dir("decoy-material")
    _stage_decoy(decoy, task, tasks_root)
    res2 = _run_script(decoy, script, timeout_s, limits)
    if not res2.timed_out and flag in (res2.stdout or ""):
        return True, ("把 challenge/ 换成同名的假材料后仍打印出同一个 flag："
                      "脚本读了材料却没有用它，答案仍是硬编码的")
    return False, ""


def _stderr_tail(stderr: str, replay_dir: Path, limit: int = 1200) -> str:
    """stderr 只回传尾部，供模型修复脚本用。

    两个刻意的选择：
    1. **取尾部不取头部**——异常类型和消息在最后，头部只有 "Traceback" 和调用栈。
       原来 `[:300]` 取头部，正好把 `AttributeError: 'NoneType' object has no
       attribute 'group'` 切成 `AttributeError: 'N`，模型拿到这种残句没法修。
    2. **抹掉重放目录的绝对路径**——本机是 `/private/var/folders/...` 这种
       几十字符的临时路径，对修复没有信息量，却会把长度预算吃光。
    """
    text = (stderr or "").strip()
    if not text:
        return ""
    # macOS 上 /var 与 /private/var 是同一处的两种写法：replay_dir 由
    # tempfile 给出的是 /var/...，而 traceback 里打印的是 /private/var/...。
    # 只替换一种会留下 "/private<replay>" 这种半截路径，长的先换。
    for prefix in sorted({str(replay_dir), str(Path(replay_dir).resolve())},
                         key=len, reverse=True):
        text = text.replace(prefix, "<replay>")
    return text[-limit:]


def evaluate(
    task: Task,
    submitted_flag: str,
    script: str,
    tasks_root: Path,
    timeout_s: float = 300.0,
    limits: SandboxLimits | None = None,
    keep_dir: Path | None = None,
    material_free: bool = True,
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
    replay_dir = _make_run_dir(task, tasks_root, keep_dir, "replay")
    # 只从题库根目录拷附件，不用 agent 工作目录里的副本——
    # 防止 agent 自己"加工"过题目文件来让脚本跑通
    _stage(replay_dir, task, tasks_root, with_material=True)
    res = _run_script(replay_dir, script, timeout_s, limits)

    if res.timed_out:
        return ReplayResult("unverified", "timeout", f"重放超时（{timeout_s}s）",
                            res.stdout, res.exit_code, res.duration_ms,
                            gold_checked=gold_known)

    target = submitted_flag.strip()
    if target and target in res.stdout:
        # 第四道：材料缺失重放。只在这一步做——它比正常重放更严格，
        # 放在前面会把"脚本跑不起来"和"脚本不需要材料"两种失败混在一起。
        if material_free and task.assets:
            suspect, why = survives_without_material(
                task, script, target, tasks_root, timeout_s, limits,
                parent=replay_dir.parent)
            if suspect:
                return ReplayResult("unverified", "hardcoded_no_material", why,
                                    res.stdout, res.exit_code, res.duration_ms,
                                    gold_checked=gold_known)
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
        tail = _stderr_tail(res.stderr, replay_dir)
        if tail:
            detail += "；stderr: " + tail
        # 崩了 != 没打印。exit≠0 且 stderr 有内容，是脚本本身跑挂了
        # （比如引用了题目里不存在的字段），语义上属于 script_error。
        # 把它记成 no_output 会让报告的失败分布失真：
        # "写不出复现脚本" 和 "脚本写错" 是两种完全不同的失败。
        reason = "script_error" if (res.exit_code not in (0, None) and tail) else "no_output"
        return ReplayResult("unverified", reason, detail, res.stdout,
                            res.exit_code, res.duration_ms, gold_checked=gold_known)

    # 有输出但没有该 flag：脚本跑完了，逻辑不对。stderr 里可能是
    # warning 或部分失败的线索，一并带上——原先这条路径完全不给 stderr。
    detail = f"输出里没有该 flag（exit={res.exit_code}）"
    tail = _stderr_tail(res.stderr, replay_dir)
    if tail:
        detail += "；stderr: " + tail
    return ReplayResult("unverified", "script_error", detail,
                        res.stdout, res.exit_code, res.duration_ms,
                        gold_checked=gold_known)
