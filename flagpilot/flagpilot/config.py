"""全局配置。所有路径与预算集中在这里，环境变量可覆盖。"""

from __future__ import annotations

import os
import re
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
    """一次 attempt 的预算。任一上限触发即终止。

    20 轮是评测口径（v5–v7 都是它）。按 v7 的分布它在评测题上不起作用：通过的尝试中位数 4–5 轮、最多 14 轮，
    160 次尝试没有一次是因为轮数用完而结束的。导入的难题（堆利用之类）20 轮不够：网页单题默认 40 轮，可选更多（for_turns）。
    max_prompt_tokens 管的是单次请求的上下文（每轮取当次的 prompt_tokens），max_completion_tokens 和 max_wall_s 是整次尝试累计。
    """

    max_turns: int = 20
    max_prompt_tokens: int = 200_000
    max_completion_tokens: int = 32_000
    max_wall_s: int = 1500

    @classmethod
    def for_turns(cls, turns: int, wall_s: int | None = None) -> "Budget":
        """turns 轮的预算。累计的输出 token 和墙钟时间按 20 轮时的比例跟着放大（每轮 1,600 token、75 s）：只加轮数，
        长一点的尝试会先撞上它们。20 轮时正好是评测口径（32,000 token、1,500 s）。"""
        turns = int(turns)
        return cls(max_turns=turns, max_completion_tokens=max(32_000, 1_600 * turns),
                   max_wall_s=int(wall_s) if wall_s else max(1_500, 75 * turns))


@dataclass
class Policy:
    """上下文策略。对应 proposal 的 append-time truncation 那一格。"""

    # 观察截断：保留头尾，中间折叠。None = 不截断（用作 baseline）
    truncate_head_chars: int | None = 1500
    truncate_tail_chars: int | None = 1500
    # 截断时把中段里像 flag / 密钥的行原样留下（见 agent.salient_lines）；关掉就是纯头尾截断
    keep_salient: bool = True
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
    # 备援模型（名字）：主模型在接口层面失败（额度、过载、模型不存在、密钥、网络）时这一轮就换它，之后一直用它。
    # 只放名字；带端点和密钥的配置由网页服务在建客户端时生成。实验（Lab / 批跑）不设，免得一组里混进两个模型。
    fallback: str = ""
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
    # gemini-* 没给端点时默认走 Google 的 OpenAI 兼容端点
    default = GEMINI["base_url"] if lower.startswith("gemini") else "http://127.0.0.1:8000/v1"
    spec = ModelSpec(name=name, base_url=base_url or default, **kw)
    if urlparse(spec.base_url).hostname == "opencode.ai":
        spec.provider = "openai-compatible"
    if is_gemini(spec.base_url):
        # 只发标准字段：chat_template_kwargs 不认；reasoning_effort="none" 在 flash-lite 上直接 400
        spec.provider = "gemini"
        if not (spec.api_key or "").strip() or spec.api_key == "EMPTY":
            spec.api_key = gemini_api_key() or spec.api_key
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


# Gemini API（Google AI Studio）的 OpenAI 兼容端点。会员订阅不含 API；密钥在 AI Studio 免费创建，放根目录 .env。
GEMINI = {
    "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
    "model": "gemini-flash-lite-latest",       # 2026-11-20 免费层里高峰时仍可用的那一档
    "key_env": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}


def is_gemini(base_url: str) -> bool:
    return urlparse(base_url or "").hostname == "generativelanguage.googleapis.com"


# 密钥与端点从哪里读（前面的优先）：环境变量 > 仓库根目录 .env（个人的，不进 git）> 仓库根目录 shared.env
# （仓库自带的共享密钥：私有仓库里给组员和评审"克隆后 ./start.sh 一键复现"用；仓库公开之前必须删掉它并作废这些密钥）。
# 测试设 FLAGPILOT_NO_DOTENV，两个文件都不读。
SHARED_ENV = PROJECT_ROOT.parent / "shared.env"


def dotenv_files() -> list[Path]:
    return [PROJECT_ROOT.parent / ".env", SHARED_ENV]


def dotenv_pairs(dotenv: Path | None = None) -> dict[str, str]:
    """NAME=value：给了 dotenv 只读那一个文件；否则先 .env 再 shared.env，先出现的有值的为准（空值不挡后面的）。"""
    out: dict[str, str] = {}
    for path in ([dotenv] if dotenv is not None else dotenv_files()):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v and k not in out:
                out[k] = v
    return out


def dotenv_value(name: str, dotenv: Path | None = None) -> str:
    """.env / shared.env 里某个配置的值（例如 FLAGPILOT_FALLBACK_MODEL）；测试里设 FLAGPILOT_NO_DOTENV 就不读。"""
    if dotenv is None and os.environ.get("FLAGPILOT_NO_DOTENV"):
        return ""
    return dotenv_pairs(dotenv).get(name, "")


def extra_endpoints(environ=None, dotenv: Path | None = None) -> dict[str, dict]:
    """别的 OpenAI 兼容端点：环境变量或仓库根目录 .env 里成对出现的 NAME_BASE_URL + NAME_API_KEY（例如 RAYGO_*）。

    返回 {name: {"base_url", "api_key", "models"}}；可选 NAME_MODELS（逗号分隔）预先声明模型，端点列不出模型时用。
    只认 https 地址；GEMINI_* 另有专门处理。密钥不打印、不进运行指纹。
    """
    env = os.environ if environ is None else environ
    kv: dict[str, str] = {}
    if not (dotenv is None and env.get("FLAGPILOT_NO_DOTENV")):
        kv.update(dotenv_pairs(dotenv))
    kv.update({k: v for k, v in env.items() if k.endswith(("_BASE_URL", "_API_KEY", "_MODELS"))})
    out: dict[str, dict] = {}
    for k, v in kv.items():
        name = k[:-len("_BASE_URL")] if k.endswith("_BASE_URL") else ""
        if not name or name in ("GEMINI", "FLAGPILOT", "OPENAI"):
            continue
        key = kv.get(name + "_API_KEY", "")
        if (v.startswith("https://") or re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?/", v)) and key:   # 明文 http 只许本机
            models = [m.strip() for m in kv.get(name + "_MODELS", "").split(",") if m.strip()]
            out[name.lower()] = {"base_url": v.rstrip("/"), "api_key": key, "models": models}
    return out


def gemini_api_key(environ=None, dotenv: Path | None = None) -> str:
    """Gemini 密钥：先看环境变量，再看 .env，最后 shared.env（只读这两个名字，不打印、不进指纹）。"""
    env = os.environ if environ is None else environ
    for k in GEMINI["key_env"]:
        v = (env.get(k) or "").strip()
        if v:
            return v
    if dotenv is None and env.get("FLAGPILOT_NO_DOTENV"):      # 测试里不碰真实密钥
        return ""
    pairs = dotenv_pairs(dotenv)
    return next((pairs[k] for k in GEMINI["key_env"] if pairs.get(k)), "")


def track_h_api_key(environ=None) -> str:
    env = os.environ if environ is None else environ
    for k in TRACK_H["key_env"]:
        v = (env.get(k) or "").strip()
        if v:
            return v
    if environ is None and not env.get("FLAGPILOT_NO_DOTENV"):
        pairs = dotenv_pairs()
        return next((pairs[k] for k in TRACK_H["key_env"] if pairs.get(k)), "")
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
                **({"fallback": self.model.fallback} if self.model.fallback else {}),
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
