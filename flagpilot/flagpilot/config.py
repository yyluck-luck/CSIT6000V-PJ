"""全局配置。所有路径与预算集中在这里，环境变量可覆盖。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default


# 题库：只挂 no-solutions 版本（题解已剥离、gold 只剩 sha256）。
# 默认找仓库内的 tasks/；题库放在别处时用 FLAGPILOT_TASKS 指过去。
# 需要的结构见 docs/TASKS.md。
TASKS_ROOT = _env_path("FLAGPILOT_TASKS", PROJECT_ROOT / "tasks")

# 运行产物
RUNS_ROOT = _env_path("FLAGPILOT_RUNS", PROJECT_ROOT / "runs")

# 跨 run 的经验记忆（不含 flag，只记结论）
MEMORY_ROOT = _env_path("FLAGPILOT_MEMORY", RUNS_ROOT / "memory")


@dataclass
class SandboxLimits:
    """单次动作的资源上限。默认值按 picoCTF 题目的量级设定。"""

    action_timeout_s: float = 60.0
    # 单次动作最多占多少 CPU 秒（与 wall timeout 分开：防止 fork 炸弹式忙等）
    cpu_seconds: int = 60
    # 地址空间上限，MB。None = 不限制（macOS 上 RLIMIT_AS 行为不可靠）
    memory_mb: int | None = 2048
    # 单文件最大写入，MB
    file_size_mb: int = 64
    # 进程数上限（含线程）
    max_procs: int = 256


@dataclass
class Budget:
    """一次 attempt 的预算。三种上限任一触发即终止。"""

    # TODO(你): 跑完 20 题冒烟后，按真实轮次分布调 max_turns（现在 20 是拍的）。

    max_turns: int = 20
    max_prompt_tokens: int = 200_000
    max_completion_tokens: int = 32_000
    max_wall_s: int = 1500


@dataclass
class Policy:
    """上下文策略。对应 proposal 的 append-time truncation 那一格。"""

    # 观察截断：保留头尾，中间折叠。None = 不截断（用作 baseline）
    truncate_head_chars: int | None = 1500
    truncate_tail_chars: int | None = 1500
    # 单次观察的硬上限（字符），超长先截再入库，保证历史字节稳定
    hard_cap_chars: int = 12_000
    # 上下文压缩（compaction）阈值：messages 总字符超过它就折叠中段。
    # None = 关闭。为什么按字符不按 token：这里没有 tokenizer，
    # 按字符是确定性的、可复现的；换算比例写进报告再说明。
    compact_at_chars: int | None = 48_000
    # 压缩时保留最近多少条消息（user/assistant 混计）。
    # 6 条 ≈ 最近 3 轮，够模型接上"我正在干什么"。
    compact_keep_recent: int = 6


@dataclass
class Ablation:
    """E1 的加法阶梯。每一档只比前一档多一个变量，其余保持一致。

    这样做是为了让"提升来自哪一层"可归因。如果一档改两个变量，
    拿到 +8pp 也说不清是截断带来的还是工具箱带来的。
    """

    name: str = "B3"
    truncate: bool = True        # B1 起：追加时截断
    structured_tools: bool = True  # B2 起：file / strings / decode 三个结构化动作
    prompt: str = "full"         # B3 起：完整规则；minimal = 只给 schema

    @property
    def desc(self) -> str:
        parts = [
            "截断" if self.truncate else "无截断",
            "结构化工具" if self.structured_tools else "仅 shell/python",
            "完整 prompt" if self.prompt == "full" else "最小 prompt",
        ]
        return " + ".join(parts)


# 注册表刻意写死在代码里，而不是读配置文件：
# 消融的档位一旦能被随手改，报告里的"B2"就不再是可复现的同一件事。
ABLATIONS: dict[str, Ablation] = {
    "B0": Ablation(name="B0", truncate=False, structured_tools=False, prompt="minimal"),
    "B1": Ablation(name="B1", truncate=True, structured_tools=False, prompt="minimal"),
    "B2": Ablation(name="B2", truncate=True, structured_tools=True, prompt="minimal"),
    "B3": Ablation(name="B3", truncate=True, structured_tools=True, prompt="full"),
}


def get_ablation(name: str | None) -> Ablation:
    """取档位。None / 空 / 未知都回落到 B3（当前默认系统）。"""
    if not name:
        return Ablation(name="custom")
    key = str(name).strip().upper()
    if key in ABLATIONS:
        return ABLATIONS[key]
    raise ValueError(f"未知消融档位 {name!r}，可选：{', '.join(ABLATIONS)}")


@dataclass
class ModelSpec:
    """模型端点。mock 用于无 API 的离线自测。"""

    name: str = "mock"
    base_url: str = "http://127.0.0.1:8000/v1"
    api_key: str = "EMPTY"
    temperature: float = 0.2
    # 每轮输出上限。2048 → 4096 是 dev 上定的（2026-11-20，qwen3.8-27b，B3，同 20 题）：
    # 2048 时 3 题共 5 条回复被截断（截断算一次无效动作，连着 3 次整题出局），4096 时 0 条；
    # verified 12 → 13（crypto-079），原来解出的一题没丢；输出 token +14%、墙钟 +9%。
    # n=20 时差异不显著（McNemar p=1.0），改它是为去掉截断这个失败机制，不是为涨分。
    # 上下文余量：实测单次最大 prompt 24.3k，加 4096 仍在 32k 之内；万一放不下，
    # 客户端按服务端报错把本轮上限调小重试（client.fit_after_overflow）。
    max_tokens: int = 4096
    request_timeout_s: float = 180.0
    # vLLM 的 Prometheus 端点，用于读 prefix cache 命中率；留空则不采
    metrics_url: str = ""
    # 计价，仅 Track H 有意义（美元 / 每百万 token）
    price_in_per_mtok: float = 0.0
    price_out_per_mtok: float = 0.0
    # 服务商。决定"关思维链"用哪个参数：vLLM/Ollama 认 chat_template_kwargs，
    # DeepSeek 只认 {"thinking": {"type": ...}}——发错了要么 400，要么被静默忽略，
    # 后者更糟：思维链照开，temperature 失效，还按输出 token 计费。
    provider: str = "openai"          # openai | deepseek | openai-compatible
    # 价目表名（见 pricing.PRICE_TABLES）。给了就按"命中/未命中/输出 + 高峰"计价，
    # 覆盖上面两个平价字段。
    price_table: str = ""
    # Qwen3 系列默认开 thinking，会吐出 <think>...</think> 并把预算吃光。
    # Agent 每轮只要一个 JSON 动作，所以默认关掉；要做"思考 vs 不思考"消融再打开。
    enable_thinking: bool = False
    # 有些运行时（Ollama）不接受 chat_template_kwargs，此时用 Qwen3 的软开关
    # ——在用户消息末尾加 /no_think。由 make_model_spec() 按模型名自动打开。
    soft_no_think: bool = False


def make_model_spec(name: str, base_url: str = "", thinking: bool = False, **kw) -> ModelSpec:
    """构造 ModelSpec 的唯一入口。

    为什么要有这个函数：CLI 和 web 曾经各写一份默认值，结果 web 那份漏了
    soft_no_think，Qwen3 的思维链把每轮 2048 token 全吃掉，表现成"空回复 →
    invalid_action → 失败率奇高"。这类 bug 不会报错，只会让数字变难看。
    所有入口必须走这里，默认值才不会再漂。
    """
    lower = str(name).lower()
    spec = ModelSpec(name=name, base_url=base_url or "http://127.0.0.1:8000/v1", **kw)
    if urlparse(spec.base_url).hostname == "opencode.ai":
        spec.provider = "openai-compatible"
    spec.enable_thinking = bool(thinking)
    # Ollama 常忽略 chat_template_kwargs（会 400 后降级），所以再压一道软开关。
    spec.soft_no_think = (not thinking) and "qwen3" in lower
    # DeepSeek：换成它认的 thinking 开关，并挂上官方价目表。
    # 默认按 host 判定（web 和 CLI 走同一个入口，判定不能只在一处做）；
    # --track H 另外显式传 provider="deepseek"，端点被改到代理 / 网关时也不会悄悄退回 vLLM 写法。
    if "api.deepseek.com" in spec.base_url.lower() or kw.get("provider") == "deepseek":
        spec.provider = kw.get("provider") or "deepseek"
        if not kw.get("price_table") and lower in DEEPSEEK_FLASH_NAMES \
                and not (spec.price_in_per_mtok or spec.price_out_per_mtok):
            spec.price_table = "deepseek-flash"
    return spec


# ---------------------------------------------------------------- Track H

# DeepSeek V4.1 = deepseek-flash（官方文档：DeepSeek-V4.1-Flash-1017）。
# 旧名仍被接受、按 Flash 计价（文档原话），所以一起认。
DEEPSEEK_FLASH_NAMES = ("deepseek-flash", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp")

TRACK_H = {
    "model": "deepseek-flash",
    "base_url": "https://api.deepseek.com",
    # 密钥按顺序找：本项目 .env 约定的名字，再是 DeepSeek 文档里的名字。
    # 刻意不读 FLAGPILOT_API_KEY——deploy/env.sh 把它设成 vLLM 用的 "EMPTY"。
    "key_env": ("DSV41_API_KEY", "DEEPSEEK_API_KEY"),
}


def track_h_api_key(environ=None) -> str:
    env = os.environ if environ is None else environ
    for k in TRACK_H["key_env"]:
        v = (env.get(k) or "").strip()
        if v:
            return v
    return ""


@dataclass
class Hybrid:
    """Track L → H 的自动升级（proposal 的 optional hybrid mode）。

    触发条件是 stall：连续 k 轮没有新证据。"证据"取工具观测的指纹——
    同一份输出重复出现不算新证据，这正好对应 proposal 的 "no new evidence
    for k turns"。升级后仍受 token 预算约束，免得 H 轨把成本吃穿。
    """

    enabled: bool = False
    stall_turns: int = 3
    max_escalations: int = 1
    max_hosted_tokens: int = 20_000
    # 升级目标的模型规格。None = 用 TRACK_H 的默认端点与模型名。
    model: "ModelSpec | None" = None


@dataclass
class RunConfig:
    """一次批跑的完整配置。会随 trace 一起落盘，保证可复现。"""

    batch: str = "dev"
    track: str = "L"  # L | H
    model: ModelSpec = field(default_factory=ModelSpec)
    budget: Budget = field(default_factory=Budget)
    policy: Policy = field(default_factory=Policy)
    limits: SandboxLimits = field(default_factory=SandboxLimits)
    ablation: Ablation = field(default_factory=Ablation)
    hybrid: Hybrid = field(default_factory=Hybrid)
    replay_timeout_s: float = 300.0
    seeds: int = 1
    # 提交前自检：给模型一个 run_script 动作，在干净目录里先跑一遍候选脚本。
    # 关掉它才能复现"没有自检"的旧行为，所以必须进指纹。
    self_check: bool = True
    # 材料缺失重放：把 challenge/ 清空再跑一次，抓"答案不是从材料推出来的"。
    # 每成功重放一次就多跑一次脚本，是最贵的一道判据，可单独关。
    material_free: bool = True
    # 提交被拒触发的修复轮是否占用轮次预算。True = 不占（更贴 proposal
    # "被拒会给一次反馈"的承诺），代价是实际模型调用数可能超过 max_turns。
    repair_extends_budget: bool = True
    # 是否在 prompt 里注入"这道题过去的尝试结论"。
    # 批跑（E1）默认关：B0 的记录会污染 B1 的对照，四档就不再是只差一个变量。
    # 交互式 web 由 server 打开——那里要的正是"别每次都从零开始"。
    memory: bool = False
    # 题库根目录随配置走，便于测试与在集群上换路径
    tasks_root: Path = field(default_factory=lambda: TASKS_ROOT)

    def fingerprint(self) -> dict:
        """写进 trace 的配置快照（不含密钥）。"""
        return {
            "batch": self.batch,
            "track": self.track,
            "model": {
                "name": self.model.name,
                "base_url": self.model.base_url,
                "temperature": self.model.temperature,
                "max_tokens": self.model.max_tokens,
            },
            "budget": self.budget.__dict__,
            "policy": self.policy.__dict__,
            "ablation": self.ablation.__dict__,
            # 升级策略必须进指纹：它改变的是模型能力上限，
            # 不记下来的话 hybrid 批次和纯 L 批次会被当成同一配置。
            "hybrid": {
                "enabled": self.hybrid.enabled,
                "stall_turns": self.hybrid.stall_turns,
                "max_escalations": self.hybrid.max_escalations,
                "max_hosted_tokens": self.hybrid.max_hosted_tokens,
                "model": self.hybrid.model.name if self.hybrid.model else None,
            },
            "limits": {k: v for k, v in self.limits.__dict__.items()},
            "replay_timeout_s": self.replay_timeout_s,
            "seeds": self.seeds,
            "memory": self.memory,
            # 这三项改变判据强度与预算口径，不记进指纹就没法解释两次跑为什么不一样
            "self_check": self.self_check,
            "material_free": self.material_free,
            "repair_extends_budget": self.repair_extends_budget,
            "version": __import__("flagpilot").__version__,
        }
