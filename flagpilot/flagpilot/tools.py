"""动作适配器：把模型输出的 JSON 动作翻译成沙箱里的执行。

两套动作集，用于 E1 的"工具箱"这一档：

    基础（B0/B1）
        shell   —— 在沙箱工作目录执行一条 shell 命令
        python  —— 写一个临时脚本并执行
        submit  —— 交 flag + 独立复现脚本

    结构化（B2 起，额外三个）
        file    —— 判定文件类型（等价 file(1)）
        strings —— 抽取可打印串（等价 strings(1)）
        decode  —— rot13 / base64 / hex / url 就地解码，不落子进程

为什么这算一个变量：基础档里模型必须自己拼出等价的 shell；结构化档把这个
过程压成一次动作。它改变的是"每步的信息密度"，进而改变轮次与 token ——
这正是 E3 成本曲线要量的东西。动作集越窄，小模型的合法输出率越高，
所以默认档（B3）也只放宽到 6 个，不做通用工具注册表。
"""

from __future__ import annotations

import base64
import binascii
import codecs
import json
import re
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from .sandbox import ExecResult, Sandbox

BASE_ACTIONS = ("shell", "python", "submit")
STRUCTURED_ACTIONS = ("file", "strings", "decode")
ACTIONS = BASE_ACTIONS  # 向后兼容的别名

_FENCE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.S)
# Qwen3 等模型的思维链块，解析前先剥掉；否则 JSON 会被埋在里面
_THINK = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.S | re.I)

_PRINTABLE = re.compile(rb"[\x20-\x7e]{4,}")


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
        if a in ("file", "strings") and not act.path.strip():
            raise ValueError(f"action={a} 时必须给 path")
        if a == "decode":
            if not act.text:
                raise ValueError("action=decode 时必须给 text")
            if act.scheme not in ("rot13", "base64", "hex", "url"):
                raise ValueError("action=decode 的 scheme 必须是 rot13/base64/hex/url")
        return act


def parse_action(text: str, allowed: tuple[str, ...] = BASE_ACTIONS) -> tuple[Action | None, str]:
    """从模型输出里抠出动作。返回 (action, error)。

    小模型常见的三种坏输出：包了 markdown 围栏、前后带解释、JSON 截断。
    前两种能救，第三种救不了——救不了就返回错误，由循环喂回一次纠正。
    """
    if not text or not text.strip():
        return None, "空回复"
    text = _THINK.sub("", text)

    candidates: list[str] = []
    for m in _FENCE.findall(text):
        candidates.append(m)
    candidates.append(text.strip())

    for cand in candidates:
        obj = _try_json(cand)
        if isinstance(obj, dict):
            try:
                return Action.from_obj(obj, allowed), None
            except ValueError as e:
                return None, str(e)

    # 最后再试一次：从第一个 { 开始做括号配平
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
                    if isinstance(obj, dict):
                        try:
                            return Action.from_obj(obj, allowed), None
                        except ValueError as e:
                            return None, str(e)
                    break
    return None, "没有解析到合法的 JSON 动作"


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
    def __init__(self, sandbox: Sandbox, structured: bool = False):
        self.sb = sandbox
        self.structured = structured
        self.allowed = actions_for(structured)

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
