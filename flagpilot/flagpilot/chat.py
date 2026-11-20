"""答疑 / 讲解模式：让 agent 从"解题机"变回"能对话的助教"。

为什么单独一条链路，而不是复用 agent.py：
  求解循环强制模型每轮只回一个 JSON 动作——那是为了可归因、可统计。
  但讲解需要成段的自然语言，逼它吐 JSON 只会把话憋成一句话。
  所以这里换成另一套协议：**正文自由写，命令用 <run></run> 包起来。**

协议设计（一句话）：能读的文字归你，要跑的东西包起来。

    <run>strings -n 6 challenge/a.bin</run>
    这串里有一段 base64，解出来是……

循环只在出现 <run> 时才继续：跑完把输出喂回去，模型接着说。
没有 <run> 就是终稿，直接结束。最多 max_rounds 轮，防止它一直跑命令不讲话。

与求解模式的分工：
  * 求解模式的产物进 runs/*/runs.jsonl，参与指标；
  * 答疑模式的产物进 runs/chat/，**永不参与任何评分**。
    这道边界很重要：讲解时给出答案是完全合理的，但它不能污染 verified 率。
"""

from __future__ import annotations

from . import storage

import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .agent import truncate
from .client import build_client
from .config import RunConfig
from .sandbox import Sandbox, probe_capabilities
from .tasks import Task

_RUN = re.compile(r"<run>(.*?)</run>", re.S | re.I)
_FENCE_RUN = re.compile(r"```(?:run|bash|sh)\s*\n(.*?)```", re.S | re.I)

MAX_CMDS_PER_ROUND = 3
MAX_OBS_CHARS = 4000

# 用户要"提示"而不是"答案"时，本轮额外加一条约束。
# 为什么不用开关而用识别：答疑是对话，用户不会每次都去点开关，
# 但"别告诉我答案"这句话他们一定会说。
_COACH = re.compile(
    r"不要直接(给|告诉|说)|别(直接)?(给|告诉|说)|只(讲|说)(方法|思路)|"
    r"给我.{0,3}(思路|提示|方向)|(讲|说).{0,3}(思路|方法)|提示一下|"
    r"怎么(想|下手|入手)|hint",
    re.I,
)
# 提示模式下在数据流上脱敏，而不是靠模型自觉。
# 27B 实测会无视"不要把结果贴出来"——它执行 cat 之后看到 flag，就照抄了。
FLAG_MASK = re.compile(r"[A-Za-z0-9_]{2,20}\{[^}\n]{1,120}\}")
MASK = "picoCTF{***hidden in hint mode***}"


def mask_flags(text: str) -> str:
    return FLAG_MASK.sub(MASK, text or "")


_COACH_RULE = (
    "\n\n【本轮约束】用户要的是提示而不是答案：不要输出 flag，也不要给出"
    "可直接复制的最终结果。只给判断依据、方法与下一步；如果必须验证，"
    "只展示验证方式，不要把结果贴出来。"
)


SYSTEM = """你是 FlagPilot 的答疑助教。Always answer in English, even when the user writes in Chinese.

你的角色是讲解，不是刷分。回答遵循这几条：
- 先给结论，再给推理过程。不要用"让我想想"开头。
- 需要看文件或跑命令时，把命令用 <run></run> 包起来，一次最多 3 条。
  系统会把真实输出贴回来，然后你再接着讲。**绝不能编造命令输出。**
- 题目附件在 ./challenge/ 下，只读；没有外网；单条命令有超时。
- 解释要落到"为什么"，而不只是"是什么"。可以指出这类题的通用套路。
- 用户直接问 flag 时，可以给，但要注明"这是直接给答案，不代表模型能独立解出"。
- **用户说"不要直接给答案/只讲方法"时，这条优先于上一条**：不要输出 flag，
  也不要给出可直接复制的最终结果，只给判断依据和下一步。
- 和题目无关的问题（概念、工具用法、报告怎么写）照常回答，不必强行跑命令。
- 不要输出 JSON，正常说话即可。
"""


def parse_commands(text: str) -> tuple[list[str], str]:
    """从一段回复里分离出要执行的命令与要展示的正文。

    支持两种写法：<run>cmd</run>，以及 ```run 围栏（小模型有时更习惯后者）。
    """
    raw = text or ""
    cmds = [c.strip() for c in _RUN.findall(raw) if c.strip()]
    cmds += [c.strip() for c in _FENCE_RUN.findall(raw) if c.strip()]
    body = _RUN.sub("", raw)
    body = _FENCE_RUN.sub("", body)
    # 命令被摘掉后常留下连续空行，压一下，前端不至于出现大片空白
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return cmds, body


@dataclass
class ToolStep:
    round: int
    cmd: str
    exit_code: int | None
    duration_ms: int
    timed_out: bool
    output: str = ""
    # 同一条命令重复出现时复用上次输出（小模型很爱把解码跑两遍）。
    # 标出来是为了让前端如实显示"这是复用，不是又跑了一次"。
    cached: bool = False


@dataclass
class ChatReply:
    answer: str
    rounds: int = 0
    steps: list[ToolStep] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    wall_s: float = 0.0
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "answer": self.answer,
            "rounds": self.rounds,
            "steps": [s.__dict__ for s in self.steps],
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "wall_s": self.wall_s,
            "error": self.error,
        }


def read_log(path: Path) -> list[dict]:
    """读会话日志。半行（进程被杀/磁盘满）跳过，不让整段历史失效。"""
    rows: list[dict] = []
    try:
        with Path(path).open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = storage.decode_record(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    rows.append(rec)
    except OSError:
        return []
    return rows


class ChatSession:
    """一次答疑会话。工作目录跨轮保留，模型可以接着上一轮的文件继续。

    会话是**持久**的：每条 user / reply 都追加到 `workdir/chat.jsonl`。
    之前只存在内存里，服务一重启（哪怕只是热更新代码）历史就没了——
    对"助教"这种连续对话的场景，那等于每次重启都要重新交代一遍背景。
    """

    def __init__(
        self,
        cfg: RunConfig,
        task: Task | None = None,
        workdir: Path | None = None,
        use_tools: bool = True,
        max_rounds: int = 3,
    ):
        self.cfg = cfg
        self.task = task
        self.use_tools = bool(use_tools)
        self.max_rounds = max(0, int(max_rounds))
        self.client = build_client(cfg.model)
        self.caps = probe_capabilities()
        self.workdir = Path(workdir or (Path(cfg.tasks_root).parent / "chat")).resolve()
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.sandbox = Sandbox(self.workdir, limits=cfg.limits, caps=self.caps,
                               tasks_root=cfg.tasks_root)
        self.n_assets = 0
        if task is not None:
            chal = self.sandbox.stage(task)
            self.n_assets = sum(1 for p in chal.rglob("*") if p.is_file())

        self.messages: list[dict] = [{"role": "system", "content": self._system()}]
        self.turns = 0
        self.total_steps = 0
        # 命令 → 上次输出。答疑里重复跑同一条命令纯属浪费轮次。
        self._ran: dict[str, str] = {}

        # 落盘：workdir/chat.jsonl，一行一条。和求解的 trace.jsonl 分开，
        # 因为答疑永不参与评分，混进 runs/*/trace 会把口径搞乱。
        self.path = self.workdir / "chat.jsonl"
        self._io = threading.Lock()
        if not self.path.exists():
            self._log({"type": "meta", "task": task.name if task else "",
                       "model": cfg.model.name, "tools": self.use_tools})

    # ---------- 落盘 / 恢复 ----------

    def _log(self, rec: dict) -> None:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **rec}
        try:
            with self._io:
                storage.append_record(self.path, rec)
        except OSError:
            pass      # 落盘失败不该让对话中断

    def history(self) -> list[dict]:
        return read_log(self.path)

    @classmethod
    def resume(
        cls,
        cfg: RunConfig,
        task: Task | None,
        workdir: Path,
        use_tools: bool = True,
    ) -> "ChatSession | None":
        """从 chat.jsonl 恢复会话。没有历史就返回 None，交给调用方新建。"""
        rows = read_log(Path(workdir) / "chat.jsonl")
        if not any(r.get("type") in ("user", "reply") for r in rows):
            return None
        sess = cls(cfg, task=task, workdir=workdir, use_tools=use_tools)
        sess.messages = [{"role": "system", "content": sess._system()}]
        for r in rows:
            if r.get("type") == "user" and str(r.get("text") or "").strip():
                sess.messages.append({"role": "user", "content": str(r["text"])})
            elif r.get("type") == "reply" and str(r.get("answer") or "").strip():
                sess.messages.append({"role": "assistant", "content": str(r["answer"])})
                sess.turns += 1
                sess.total_steps += len(r.get("steps") or [])
        return sess

    def _system(self) -> str:
        parts = [SYSTEM]
        if self.task is not None:
            parts.append(f"\n当前题目：{self.task.name} [{self.task.category}]")
            parts.append(self.task.brief)
            parts.append(
                f"\n本题共 {self.n_assets} 个附件，位于 ./challenge/。"
                + ("本题没有标准答案（gold），所以只能说'复现成功/不成功'，别说'验证通过'。"
                   if not self.task.has_gold else "")
            )
        else:
            parts.append("\n当前没有挂载题目，用户可能会问概念或工具问题。")
        if not self.use_tools:
            parts.append("\n本轮不允许执行命令，请不要输出 <run>，直接讲解。")
        return "\n".join(parts)

    # ---------- 主循环 ----------

    def ask(
        self,
        text: str,
        on_delta: Callable[[str], None] | None = None,
        hook: Callable[[dict], None] | None = None,
    ) -> ChatReply:
        self.turns += 1
        reply = ChatReply(answer="")
        t0 = time.monotonic()
        nt = "\n/no_think" if self.cfg.model.soft_no_think else ""
        coach = _COACH.search(text) is not None
        if hook:
            hook({"type": "chat_coach", "on": coach})
        self.messages.append({"role": "user",
                              "content": text + (_COACH_RULE if coach else "") + nt})
        self._log({"type": "user", "turn": self.turns, "text": text[:4000],
                   "coach": coach})

        for rnd in range(self.max_rounds + 1):
            reply.rounds = rnd + 1
            comp = self.client.complete(self.messages, on_delta=on_delta)
            reply.prompt_tokens += comp.prompt_tokens
            reply.completion_tokens += comp.completion_tokens
            if comp.error:
                reply.error = comp.error
                reply.answer = reply.answer or f"Model call failed: {comp.error}"
                break

            body_text = comp.text or ""
            if coach:
                body_text = mask_flags(body_text)
            cmds, body = parse_commands(body_text)
            last_round = rnd >= self.max_rounds

            if not self.use_tools:
                cmds = []
            if not cmds or last_round:
                reply.answer = body or ("(the model only issued commands this turn and gave no explanation)" if cmds else "")
                self.messages.append({"role": "assistant", "content": body_text})
                break

            # ---- 执行命令并回灌 ----
            self.messages.append({"role": "assistant", "content": body_text})
            blocks: list[str] = []
            for cmd in cmds[:MAX_CMDS_PER_ROUND]:
                if cmd in self._ran:
                    out = self._ran[cmd]
                    step = ToolStep(round=rnd + 1, cmd=cmd, exit_code=0,
                                    duration_ms=0, timed_out=False,
                                    output=out[:MAX_OBS_CHARS], cached=True)
                    reply.steps.append(step)
                    if hook:
                        hook({"type": "chat_tool", "round": rnd + 1, "cmd": cmd,
                              "exit_code": 0, "timed_out": False, "duration_ms": 0,
                              "cached": True, "output": out[:MAX_OBS_CHARS]})
                    blocks.append(f"[命令] {cmd}\n（这条命令刚跑过，下面是上次的真实输出）\n{out[:MAX_OBS_CHARS]}")
                    continue
                res = self.sandbox.run_shell(cmd, timeout=self.cfg.limits.action_timeout_s)
                out = res.combined
                if coach:
                    # 连缓存一起脱敏：否则下一轮复用缓存又把明文放出来了
                    out = mask_flags(out)
                self._ran[cmd] = out
                step = ToolStep(round=rnd + 1, cmd=cmd, exit_code=res.exit_code,
                                duration_ms=res.duration_ms, timed_out=res.timed_out,
                                output=out[:MAX_OBS_CHARS])
                reply.steps.append(step)
                self.total_steps += 1
                if hook:
                    hook({"type": "chat_tool", "round": rnd + 1, "cmd": cmd,
                          "exit_code": res.exit_code, "timed_out": res.timed_out,
                          "duration_ms": res.duration_ms,
                          "output": out[:MAX_OBS_CHARS]})
                shown, was = truncate(out, self.cfg.policy)
                blocks.append(
                    f"[命令] {cmd}\n[退出码] {res.exit_code}"
                    + ("（超时）" if res.timed_out else "")
                    + f"\n[输出{'，已截断' if was else ''}]\n{shown}"
                )
            self.messages.append({
                "role": "user",
                "content": "OBSERVATION（真实输出，直接引用，不要编造）\n\n"
                           + "\n\n".join(blocks) + nt,
            })

        reply.wall_s = round(time.monotonic() - t0, 2)
        if not reply.answer:
            reply.answer = "(no content)"
        if coach:
            reply.answer = mask_flags(reply.answer)
        # 答案落盘时保留完整文本（提示模式的脱敏已在上面做完）；
        # 命令只记"命令 + 退出码 + 是否复用"，输出本身不进日志——
        # 里面有可能是 flag，答疑日志不该成为第二个答案库。
        self._log({"type": "reply", "turn": self.turns,
                   "answer": reply.answer[:4000], "rounds": reply.rounds,
                   "steps": [[s.cmd[:200], s.exit_code, bool(s.cached)] for s in reply.steps],
                   "prompt_tokens": reply.prompt_tokens,
                   "completion_tokens": reply.completion_tokens,
                   "wall_s": reply.wall_s, "error": reply.error})
        return reply

    def reset(self) -> None:
        """新会话：内存清空，日志另起一份（旧的留档，不删）。"""
        self.messages = [{"role": "system", "content": self._system()}]
        self.turns = 0
        self.total_steps = 0

    def forget(self) -> None:
        """删除本会话的日志文件（前端点"新会话"且不再需要历史时用）。"""
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError:
            pass
