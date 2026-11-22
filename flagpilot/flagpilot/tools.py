"""动作适配器：把模型输出的 JSON 动作翻译成沙箱里的执行。

两套动作集，用于 E1 的"工具箱"这一档：

    基础（B0/B1）
        shell   —— 在沙箱工作目录执行一条 shell 命令
        python  —— 写一个临时脚本并执行
        submit  —— 交 flag + 独立复现脚本

    结构化（B2 起，额外五个）
        file    —— 判定文件类型（等价 file(1)）
        strings —— 抽取可打印串（等价 strings(1)）
        decode  —— rot13 / base64 / hex / url 就地解码，不落子进程
        smt     —— 在 z3 环境里跑一段 z3py（proposal 承诺的 SMT solver）
        disasm  —— objdump -d（proposal 承诺的 binutils / 反汇编器）

为什么这算一个变量：基础档里模型必须自己拼出等价的 shell；结构化档把这个
过程压成一次动作。它改变的是"每步的信息密度"，进而改变轮次与 token ——
这正是 E3 成本曲线要量的东西。动作集越窄，小模型的合法输出率越高，
所以默认档（B3）也只放宽到 8 个，不做通用工具注册表。
"""

from __future__ import annotations

import base64
import binascii
import codecs
import json
import re
import shlex
import shutil
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from .sandbox import ExecResult, Sandbox

BASE_ACTIONS = ("shell", "python", "submit")
# run_script 放在结构化集合里而不是基础集合：它需要"干净目录 + 重放同款沙箱"
# 这套装置，属于装置能力而不是通用 shell 能力。放进结构化集合也意味着
# B0/B1 两档没有它，四档消融仍然只差一个变量。
STRUCTURED_ACTIONS = ("file", "strings", "decode", "smt", "disasm", "run_script")
ACTIONS = BASE_ACTIONS  # 向后兼容的别名

# z3 是可选依赖：装了 smt 动作才有意义，没装就明确说"不可用"，
# 而不是让模型在 python 动作里撞 ImportError 再自己猜。
_Z3_CACHE: bool | None = None


def z3_available(force: bool = False) -> bool:
    global _Z3_CACHE
    if _Z3_CACHE is None or force:
        try:
            import z3  # noqa: F401

            _Z3_CACHE = True
        except Exception:
            _Z3_CACHE = False
    return bool(_Z3_CACHE)


def disasm_binary() -> str | None:
    """反汇编器。Linux 上 objdump 属于 binutils，macOS 自带的是 LLVM 版。"""
    for name in ("objdump", "llvm-objdump", "gobjdump"):
        found = shutil.which(name)
        if found:
            return found
    return None

_FENCE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.S)
# Qwen3 等模型的思维链块，解析前先剥掉；否则 JSON 会被埋在里面
_THINK = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.S | re.I)

_PRINTABLE = re.compile(rb"[\x20-\x7e]{4,}")
# 反汇编动辄上万行，留前 400 行够判断入口和关键跳转
_DISASM_MAX_LINES = 400


def actions_for(structured: bool) -> tuple[str, ...]:
    return BASE_ACTIONS + STRUCTURED_ACTIONS if structured else BASE_ACTIONS


@dataclass
class Action:
    thought: str
    action: str
    cmd: str = ""
    code: str = ""
    flag: str = ""
    script: str = ""
    path: str = ""
    scheme: str = ""
    text: str = ""
    min_len: int = 6

    @classmethod
    def from_obj(cls, obj: dict, allowed: tuple[str, ...] = BASE_ACTIONS) -> "Action":
        a = str(obj.get("action", "")).strip().lower()
        if a not in allowed:
            raise ValueError(f"action 必须是 {allowed} 之一，收到 {a!r}")
        act = cls(
            thought=str(obj.get("thought", "") or "")[:800],
            action=a,
            cmd=str(obj.get("cmd", "") or ""),
            code=str(obj.get("code", "") or ""),
            flag=str(obj.get("flag", "") or ""),
            script=str(obj.get("script", "") or ""),
            path=str(obj.get("path", "") or ""),
            scheme=str(obj.get("scheme", "") or "").strip().lower(),
            text=str(obj.get("text", "") or ""),
            min_len=int(obj.get("min_len", 6) or 6),
        )
        if a == "shell" and not act.cmd.strip():
            raise ValueError("action=shell 时必须给 cmd")
        if a == "python" and not act.code.strip():
            raise ValueError("action=python 时必须给 code")
        if a == "submit":
            if not act.flag.strip():
                raise ValueError("action=submit 时必须给 flag")
            if not act.script.strip():
                raise ValueError("action=submit 时必须给 script（独立复现脚本）")
        if a in ("file", "strings", "disasm") and not act.path.strip():
            raise ValueError(f"action={a} 时必须给 path")
        if a == "run_script" and not act.script.strip():
            raise ValueError("action=run_script 时必须给 script（候选的 solve.py 源码）")
        if a == "smt" and not act.code.strip():
            raise ValueError("action=smt 时必须给 code（z3py 源码）")
        if a == "decode":
            if not act.text:
                raise ValueError("action=decode 时必须给 text")
            if act.scheme not in ("rot13", "base64", "hex", "url"):
                raise ValueError("action=decode 的 scheme 必须是 rot13/base64/hex/url")
        return act


def parse_action(text: str, allowed: tuple[str, ...] = BASE_ACTIONS) -> tuple[Action | None, str]:
    """从模型输出里抠出动作。返回 (action, error)。

    小模型常见的三种坏输出：包了 markdown 围栏、前后带解释、JSON 截断。
    前两种能救，第三种救不了——救不了就返回错误，由循环喂回一次纠正（Policy.continue_cut 打开时先请模型接着写完）。
    """
    if not text or not text.strip():
        return None, "空回复"
    obj = json_object(text)
    if obj is None:
        return None, "没有解析到合法的 JSON 动作"
    try:
        return Action.from_obj(obj, allowed), None
    except ValueError as e:
        return None, str(e)


def json_object(text: str) -> dict | None:
    """回复里的 JSON 对象：先看 markdown 围栏里的、再看整段，最后从第一个 { 开始做括号配平。找不到返回 None。"""
    text = _THINK.sub("", text or "")
    for cand in [*_FENCE.findall(text), text.strip()]:
        obj = _try_json(cand)
        if isinstance(obj, dict):
            return obj
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    obj = _try_json(text[start : i + 1])
                    return obj if isinstance(obj, dict) else None
    return None


def json_cut(text: str) -> bool:
    """回复是不是在一个 JSON 对象中间就结束了：从第一个 { 起，到结尾时字符串没闭合或括号没配平。

    v6 / v7 的协议错误几乎都是这样（finish_reason 是 stop，不是 token 上限）；引号没转义这类错误括号是配平的，不算。
    """
    text = _THINK.sub("", text or "")
    start = text.find("{")
    if start < 0:
        return False
    depth, in_str, esc = 0, False, False
    for ch in text[start:]:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return False
    return depth > 0


def _try_json(s: str):
    s = s.strip()
    if not s.startswith("{") and not s.startswith("["):
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------- 结构化动作

def guess_type(head: bytes) -> str:
    """按 magic 判类型。够用即可，不追求 file(1) 的覆盖率。"""
    if head.startswith(b"\x7fELF"):
        return "ELF executable"
    if head.startswith(b"PK\x03\x04"):
        return "Zip archive"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG image"
    if head.startswith(b"%PDF"):
        return "PDF document"
    if head.startswith(b"GIF8"):
        return "GIF image"
    if head.startswith(b"\xff\xd8\xff"):
        return "JPEG image"
    if head.startswith(b"\x1f\x8b"):
        return "gzip compressed data"
    if head.startswith(b"BZh"):
        return "bzip2 compressed data"
    if head.startswith(b"SQLite format 3\x00"):
        return "SQLite database"
    if head.startswith(b"\xd4\xc3\xb2\xa1") or head.startswith(b"\xa1\xb2\xc3\xd4"):
        return "pcap capture"
    sample = head[:4096]
    if not sample:
        return "empty"
    printable = sum(1 for b in sample if 32 <= b < 127 or b in (9, 10, 13))
    return "ASCII text" if printable / len(sample) > 0.9 else "data"


class ToolBox:
    def __init__(self, sandbox: Sandbox, structured: bool = False,
                 dry_runner=None):
        self.sb = sandbox
        self.structured = structured
        self.allowed = actions_for(structured)
        # run_script 需要题目 + 题库根目录才能搭出干净目录，ToolBox 本身
        # 不持有这些，所以由 agent 传一个闭包进来。没传就退化成"不可用"，
        # 而不是静默地什么都不做。
        self.dry_runner = dry_runner

    def execute(self, act: Action) -> ExecResult | None:
        """submit 返回 None（它不产生 observation，由循环处理）。"""
        if act.action == "shell":
            return self.sb.run_shell(act.cmd)
        if act.action == "python":
            return self.sb.run_python(act.code)
        if act.action == "file":
            return self._file(act)
        if act.action == "strings":
            return self._strings(act)
        if act.action == "decode":
            return self._decode(act)
        if act.action == "smt":
            return self._smt(act)
        if act.action == "disasm":
            return self._disasm(act)
        if act.action == "run_script":
            return self._run_script(act)
        return None

    # ---------- 路径守卫 ----------

    def _resolve(self, rel: str) -> Path:
        p = Path(rel)
        if p.is_absolute():
            raise ValueError("path 必须是相对路径（相对于当前工作目录）")
        if any(part == ".." for part in p.parts):
            raise ValueError("path 不能包含 ..")
        full = (self.sb.workdir / p).resolve()
        # 这里必须比较路径分量，不能比字符串前缀：/work 与 /work2 同前缀，
        # 一个指向外面的软链（work/x -> /work2/secret）就能骗过 startswith。
        if not full.is_relative_to(self.sb.workdir):
            raise ValueError("path 越出工作目录")
        if not full.exists():
            raise ValueError(f"文件不存在：{rel}")
        return full

    def _fail(self, cmd: str, msg: str, t0: float) -> ExecResult:
        return ExecResult(cmd=cmd, ok=False, exit_code=1, timed_out=False,
                          duration_ms=int((time.monotonic() - t0) * 1000),
                          stdout="", stderr=msg)

    # ---------- 三个动作 ----------

    def _file(self, act: Action) -> ExecResult:
        t0 = time.monotonic()
        try:
            p = self._resolve(act.path)
        except ValueError as e:
            return self._fail(f"file {act.path}", str(e), t0)
        try:
            with p.open("rb") as fh:
                head = fh.read(4096)
        except OSError as e:
            return self._fail(f"file {act.path}", f"读取失败：{e}", t0)
        size = p.stat().st_size
        out = f"{act.path}: {guess_type(head)}, {size} bytes"
        return ExecResult(cmd=f"file {act.path}", ok=True, exit_code=0, timed_out=False,
                          duration_ms=int((time.monotonic() - t0) * 1000),
                          stdout=out, stderr="")

    def _strings(self, act: Action) -> ExecResult:
        t0 = time.monotonic()
        try:
            p = self._resolve(act.path)
        except ValueError as e:
            return self._fail(f"strings {act.path}", str(e), t0)
        n = max(4, min(int(act.min_len), 32))
        pat = re.compile(rb"[\x20-\x7e]{%d,}" % n)
        try:
            data = p.read_bytes()[:4_000_000]
        except OSError as e:
            return self._fail(f"strings {act.path}", f"读取失败：{e}", t0)
        hits = [m.group().decode("ascii", "replace") for m in pat.finditer(data)]
        total = len(hits)
        shown = hits[:200]
        body = "\n".join(shown)
        if total > len(shown):
            body += f"\n...[truncated {total - len(shown)} strings]..."
        return ExecResult(cmd=f"strings -n {n} {act.path}", ok=True, exit_code=0,
                          timed_out=False, duration_ms=int((time.monotonic() - t0) * 1000),
                          stdout=body, stderr="")

    # ---------- SMT / 反汇编 ----------

    def _smt(self, act: Action) -> ExecResult:
        """在 z3 环境里跑一段 z3py。

        自动注入 `from z3 import *`：模型不必自己写 import，也少一类
        "Int 写错成 int" 的失败。没装 z3 就明说，而不是让它在 python
        动作里撞 ImportError 再自己猜。
        """
        t0 = time.monotonic()
        if not z3_available():
            return self._fail(
                "smt",
                "z3 不可用（import z3 失败）。可改用 python 动作手工枚举或推导；"
                "要启用 SMT，请安装 z3-solver 后重试。", t0)
        res = self.sb.run_python("from z3 import *\n" + act.code)
        res.cmd = "smt (z3py)"
        return res

    def _disasm(self, act: Action) -> ExecResult:
        t0 = time.monotonic()
        try:
            p = self._resolve(act.path)
        except ValueError as e:
            return self._fail(f"disasm {act.path}", str(e), t0)
        if not disasm_binary():
            return self._fail(
                "disasm",
                "未找到 objdump / llvm-objdump。可改用 shell 动作调用 gdb、"
                "radare2，或用 python 自行解析字节。", t0)
        # 容器里没有宿主那个绝对路径，交给镜像的 PATH 解析裸名。
        exe = "objdump" if self.sb.use_container else disasm_binary()
        cmd = [exe, "-d"]
        section = act.text.strip()
        if section:
            cmd += ["-j", section]
        cmd.append(str(p))
        res = self.sb.run_shell(" ".join(shlex.quote(c) for c in cmd))
        res.cmd = f"disasm {act.path}" + (f" -j {section}" if section else "")
        lines = res.stdout.splitlines()
        if len(lines) > _DISASM_MAX_LINES:
            res.stdout = ("\n".join(lines[:_DISASM_MAX_LINES])
                          + f"\n...[truncated {len(lines) - _DISASM_MAX_LINES} lines]...")
        return res

    def _run_script(self, act: Action) -> ExecResult:
        """提交前自检：在干净目录里跑一遍候选 solve.py。

        这是**只读**动作——它不改工作目录、不写 runs/、不消耗提交额度，
        只回答"脚本能不能跑通、有没有打印出像 flag 的东西"。
        """
        t0 = time.monotonic()
        if self.dry_runner is None:
            return self._fail("run_script",
                              "自检不可用：本次运行没有配置自检装置（用 --no-self-check 关的）",
                              t0)
        r = self.dry_runner(act.script)
        return ExecResult(
            cmd="run_script",
            ok=bool(r.ok),
            exit_code=r.exit_code,
            timed_out=(r.reason == "timeout"),
            duration_ms=r.duration_ms or int((time.monotonic() - t0) * 1000),
            stdout=(r.stdout or "").strip(),
            stderr=(r.stderr or "").strip(),
            note=r.as_observation(),
        )

    def _decode(self, act: Action) -> ExecResult:
        t0 = time.monotonic()
        cmd = f"decode --{act.scheme}"
        try:
            if act.scheme == "rot13":
                out = codecs.decode(act.text, "rot13")
            elif act.scheme == "base64":
                raw = act.text.strip()
                pad = "=" * (-len(raw) % 4)
                out = base64.b64decode(raw + pad, validate=False).decode("utf-8", "replace")
            elif act.scheme == "hex":
                out = bytes.fromhex(re.sub(r"[\s,]", "", act.text)).decode("utf-8", "replace")
            else:
                out = urllib.parse.unquote(act.text)
        except (binascii.Error, ValueError) as e:
            return self._fail(cmd, f"解码失败：{type(e).__name__}: {e}", t0)
        return ExecResult(cmd=cmd, ok=True, exit_code=0, timed_out=False,
                          duration_ms=int((time.monotonic() - t0) * 1000),
                          stdout=out, stderr="")
