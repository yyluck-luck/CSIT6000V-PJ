"""模型客户端。

只依赖标准库：
  * POST {base_url}/chat/completions，stream=true，逐块解析 SSE
  * 计量 TTFT、总时延、prompt/completion token
  * 可选：抓 vLLM 的 Prometheus 指标，拿 prefix cache 命中率（E3 要用）

mock 模式不需要任何 API：用一个脚本化的策略跑通整条流水线，
用于自测与"会场断网也能演示"的离线回放。
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from .config import ModelSpec

FLAG_RE = re.compile(r"[A-Za-z0-9_]{2,20}\{[^}\n]{1,120}\}")

# "输入 + max_tokens 超过上下文"的 400。vLLM / OpenAI / SGLang / DeepSeek 都用这句话报。
_CTX_LIMIT = re.compile(r"maximum context length (?:is|of) (\d+)", re.I)
# 输入 token 数，各家原文：
#   vLLM 0.26（课程 TACC 上的版本，renderers/params.py）："... you requested 4096 output tokens and
#     your prompt contains 29000 input tokens, for a total of 33096 tokens."
#     写成 "contains at least N" 时 prompt 已被分词器截断，N 只是下限，不能拿来算 → 不匹配，走减半；
#   vLLM 0.6–0.1x："your request has 29000 input tokens (4096 > 32768 - 29000)"；
#   更早的 vLLM："(29000 in the messages, 4096 in the completion)" / "33000 tokens in the messages"；
#   SGLang："29000 tokens from the input"；OpenAI："your messages resulted in 29000 tokens"。
_CTX_INPUT = re.compile(r"prompt contains (\d+) input tokens|request has (\d+) input tokens|"
                        r"(\d+) (?:tokens )?in the messages|(\d+) tokens from the input|"
                        r"messages resulted in (\d+) tokens", re.I)
MIN_FIT_TOKENS = 256        # 比这还小就不值得再要一轮了：上下文已经满了
MAX_FIT_RETRIES = 2


def fit_after_overflow(detail: str, asked: int) -> int | None:
    """这个 400 是不是"上下文放不下这么多输出"。不是 → None；是 → 本轮还能要多少 token。

    读得出上限与输入 token 就精确算（留 8 个余量），读不出或算出来不比原值小就减半，
    保证每次重试都严格变小。结果可能低于 MIN_FIT_TOKENS，由调用方决定放弃。
    """
    m = _CTX_LIMIT.search(detail or "")
    if not m:
        return None
    i = _CTX_INPUT.search(detail)
    if i:
        fit = int(m.group(1)) - int(next(g for g in i.groups() if g)) - 8
        if fit < asked:
            return fit
    return asked // 2


@dataclass
class Completion:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    ttft_ms: int = 0
    latency_ms: int = 0
    finish_reason: str = ""
    usage_source: str = "reported"  # reported | estimated
    error: str = ""
    note: str = ""
    # 这次请求实际发出的 max_tokens。上下文放不下时客户端会把它调小再试，
    # 截断判定要按这个值，而不是配置里的值。0 = 未知（mock 等）。
    max_tokens: int = 0
    # Ollama 等运行时会单独返回思维链，正文可能为空。留档用于诊断。
    reasoning: str = ""
    # 服务端回报的前缀缓存命中 / 未命中 prompt token（DeepSeek：prompt_cache_hit_tokens /
    # prompt_cache_miss_tokens；OpenAI 风格：prompt_tokens_details.cached_tokens）。
    # 没回报就是 None——Track H 的缓存命中率只能从这里来，不能从 /metrics 来。
    cache_hit_tokens: int | None = None
    cache_miss_tokens: int | None = None
    # 思维链 token（completion_tokens_details.reasoning_tokens）。关了思维链应为 0。
    reasoning_tokens: int | None = None

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class ServerMetrics:
    """从 /metrics 抓到的量。取不到就是空 dict，不编数字。"""

    values: dict = field(default_factory=dict)
    error: str = ""


def _cache_split(usage: dict, prompt_tokens: int) -> tuple[int | None, int | None]:
    """从 usage 里取前缀缓存的命中 / 未命中数。取不到返回 (None, None)，不编数字。"""
    hit = usage.get("prompt_cache_hit_tokens")
    miss = usage.get("prompt_cache_miss_tokens")
    if hit is None and miss is None:
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
        if cached is None:
            return None, None
        hit = int(cached)
        miss = max(0, int(prompt_tokens) - hit) if prompt_tokens else None
        return hit, miss
    return (int(hit) if hit is not None else None,
            int(miss) if miss is not None else None)


class ChatClient:
    def __init__(self, spec: ModelSpec):
        self.spec = spec
        self._url = spec.base_url.rstrip("/") + "/chat/completions"
        # 有些运行时（Ollama / llama.cpp / 老版本 vLLM）不认识 chat_template_kwargs，
        # 会直接 400。第一次撞上就永久降级，避免每次请求都白撞一遍。
        self._no_template_kwargs = spec.provider == "openai-compatible"

    # ---------- 主路径 ----------

    def complete(
        self,
        messages: list[dict],
        max_tokens: int | None = None,
        on_delta: Callable[[str], None] | None = None,
        *,
        _fit_retries: int = 0,
    ) -> Completion:
        """发一轮请求。

        on_delta 用于答疑模式逐字推送；agent 求解走整段返回，不传它。
        """
        payload = {
            "model": self.spec.name,
            "messages": messages,
            "temperature": self.spec.temperature,
            "max_tokens": max_tokens or self.spec.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if self.spec.provider == "deepseek":
            # DeepSeek 的开关是 thinking.type（官方文档 Thinking Mode 一节）。
            # 它默认开思维链；reasoning_effort 只认 low/high/max，没有 "none"，
            # 所以下面那套 vLLM 写法在这里关不掉思维链。
            payload["thinking"] = {"type": "enabled" if self.spec.enable_thinking else "disabled"}
        # vLLM 会把 chat_template_kwargs 透传给 tokenizer 的 chat template。
        # 只在服务端支持时才带——不支持会在下面自动降级。
        elif not self._no_template_kwargs:
            payload["chat_template_kwargs"] = {"enable_thinking": self.spec.enable_thinking}
            if not self.spec.enable_thinking:
                # Ollama 的 Qwen3 模板认这个字段，vLLM 也支持；不支持会 400 后降级。
                # 实测只有它真能关掉思维链（/no_think 与 think:false 在 Ollama 上都被忽略）。
                payload["reasoning_effort"] = "none"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "FlagPilot/0.1.0",
                "Authorization": f"Bearer {self.spec.api_key}",
            },
            method="POST",
        )

        t0 = time.monotonic()
        chunks: list[str] = []
        thinks: list[str] = []
        ttft = 0
        usage = {}
        finish = ""
        try:
            with urllib.request.urlopen(req, timeout=self.spec.request_timeout_s) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("usage"):
                        usage = obj["usage"]
                    for ch in obj.get("choices") or []:
                        d = ch.get("delta") or {}
                        delta = d.get("content") or ""
                        # Ollama 把思维链放在 reasoning 里，正文可能是空的。
                        # 不记下来的话，表现就是"2048 token 全用满但回复为空"。
                        think = d.get("reasoning") or d.get("reasoning_content") or ""
                        if think:
                            thinks.append(think)
                        if delta and ttft == 0:
                            ttft = int((time.monotonic() - t0) * 1000)
                        if delta:
                            chunks.append(delta)
                            if on_delta is not None:
                                try:
                                    on_delta(delta)
                                except Exception:
                                    # 前端断开不该影响推理，静默忽略
                                    pass
                        if ch.get("finish_reason"):
                            finish = ch["finish_reason"]
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:500]
            except Exception:
                pass
            finally:
                # HTTPError 自己持有响应体（3.12 起是临时文件包装），不关就漏一个句柄。
                e.close()
            fit = fit_after_overflow(detail, payload["max_tokens"]) if e.code == 400 else None
            if fit is not None:
                # 输入 + max_tokens 超过上下文：和 chat_template_kwargs 无关，绝不能走下面的降级
                # （那会永久关掉思考开关，这一轮照样失败）。按服务端给的数字把本轮上限调小再试。
                if fit >= MIN_FIT_TOKENS and _fit_retries < MAX_FIT_RETRIES:
                    retry = self.complete(messages, fit, on_delta=on_delta, _fit_retries=_fit_retries + 1)
                    retry.note = (retry.note + " " if retry.note else "") + \
                        f"上下文放不下 max_tokens={payload['max_tokens']}，本轮降到 {fit}"
                    return retry
                return Completion(text="", latency_ms=int((time.monotonic() - t0) * 1000),
                                  error=f"HTTP {e.code}: {detail}", max_tokens=payload["max_tokens"])
            if e.code == 400 and not self._no_template_kwargs and self.spec.provider != "deepseek":
                # 大概率是 chat_template_kwargs 不被接受：降级后重试一次
                self._no_template_kwargs = True
                retry = self.complete(messages, max_tokens, on_delta=on_delta)
                retry.note = (retry.note + " " if retry.note else "") + \
                    "服务端拒绝 chat_template_kwargs，已降级（thinking 需靠 prompt 控制）"
                return retry
            return Completion(text="", latency_ms=int((time.monotonic() - t0) * 1000),
                              error=f"HTTP {e.code}: {detail}")
        except Exception as e:
            return Completion(text="", latency_ms=int((time.monotonic() - t0) * 1000),
                              error=f"{type(e).__name__}: {e}")

        text = "".join(chunks)
        reasoning = "".join(thinks)
        note = ""
        if not text.strip() and reasoning.strip():
            # 兜底：有些运行时/版本忽略关闭参数，只会返回 reasoning。
            # 与其把这轮判成"空回复"，不如把动作从思维链里捞出来。
            text = reasoning
            note = "正文为空，已从 reasoning 字段取回内容"
        p_tok = int(usage.get("prompt_tokens") or 0)
        c_tok = int(usage.get("completion_tokens") or 0)
        hit, miss = _cache_split(usage, p_tok)
        r_tok = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
        src = "reported"
        if not p_tok:
            p_tok = sum(len(m.get("content", "")) for m in messages) // 4
            src = "estimated"
        if not c_tok:
            c_tok = max(1, len(text) // 4)
            src = "estimated"

        return Completion(
            text=text,
            prompt_tokens=p_tok,
            completion_tokens=c_tok,
            ttft_ms=ttft,
            latency_ms=int((time.monotonic() - t0) * 1000),
            finish_reason=finish,
            usage_source=src,
            note=note,
            max_tokens=payload["max_tokens"],
            reasoning=reasoning[:2000],
            cache_hit_tokens=hit,
            cache_miss_tokens=miss,
            reasoning_tokens=int(r_tok) if r_tok is not None else None,
        )

    # ---------- 可选：服务端指标 ----------

    def scrape_metrics(self) -> ServerMetrics:
        if not self.spec.metrics_url:
            return ServerMetrics(error="metrics_url 未配置")
        try:
            with urllib.request.urlopen(self.spec.metrics_url, timeout=5) as resp:
                text = resp.read().decode("utf-8", "replace")
        except Exception as e:
            return ServerMetrics(error=f"{type(e).__name__}: {e}")

        keep = (
            "gpu_prefix_cache_hit_rate",
            "prefix_cache_hits_total",
            "prefix_cache_queries_total",
            "num_requests_running",
            "num_requests_waiting",
            "time_to_first_token_seconds_sum",
            "prompt_tokens_total",
            "generation_tokens_total",
        )
        vals: dict[str, float] = {}
        for line in text.splitlines():
            if line.startswith("#") or " " not in line:
                continue
            name_part, _, value = line.rpartition(" ")
            metric = name_part.split("{")[0].strip()
            if any(k in metric for k in keep):
                try:
                    vals[metric] = vals.get(metric, 0.0) + float(value)
                except ValueError:
                    continue
        return ServerMetrics(values=vals)


class MockClient(ChatClient):
    """离线策略模型。只做两件事：列目录、把文件内容打出来，然后提交。

    它不会凭空生成 flag——能找到就交，找不到就交一个明显错误的字符串，
    这样 pipeline 的两条分支（verified / wrong）都能被测到。
    """

    def __init__(self):  # noqa: D107
        super().__init__(ModelSpec(name="mock"))
        self.calls = 0

    def complete(
        self,
        messages: list[dict],
        max_tokens: int | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> Completion:
        self.calls += 1
        last = messages[-1].get("content", "") if messages else ""
        obs = messages[-1].get("content", "") if messages and messages[-1]["role"] == "user" else ""

        # 答疑模式：给一段"先跑命令、再讲解"的示例，否则 mock 下前端只有一坨 JSON。
        sys_txt = next((m.get("content", "") for m in messages if m["role"] == "system"), "")
        if "答疑助教" in sys_txt:
            has_obs = any(
                m["role"] == "user" and str(m.get("content", "")).startswith("OBSERVATION")
                for m in messages
            )
            if "challenge/" in sys_txt and not has_obs:
                text = ("<run>ls -la challenge</run>\n"
                        "First I check which attachments the challenge ships, then pick a method.")
            else:
                text = ("This is the mock tutor's sample answer. A real model explains here: "
                        "conclusion first, then why, running commands with <run> when it needs evidence.")
            if on_delta is not None:
                on_delta(text)
            return Completion(text=text, prompt_tokens=len(last) // 4,
                              completion_tokens=len(text) // 4, ttft_ms=1,
                              latency_ms=2, finish_reason="stop", usage_source="estimated")

        # 记忆探针：mock 没有任何记忆，如实答 UNKNOWN。
        # 这样 probe 的链路（解析 → 比对 → 统计）在没有真模型时也能被测到。
        if "what is the flag for this challenge" in last.lower():
            text = '{"flag": "UNKNOWN"}'
            if on_delta is not None:
                on_delta(text)
            return Completion(text=text, prompt_tokens=len(last) // 4,
                              completion_tokens=len(text) // 4, ttft_ms=1,
                              latency_ms=2, finish_reason="stop", usage_source="estimated")

        n_obs = sum(1 for m in messages if m["role"] == "user") - 1  # 减去题面
        if n_obs <= 0:
            action = {"thought": "List the files first", "action": "shell", "cmd": "ls -la challenge"}
        elif n_obs == 1:
            action = {
                "thought": "Print every file to see what is inside",
                "action": "shell",
                "cmd": "for f in challenge/*; do echo \"== $f ==\"; head -c 400 \"$f\"; echo; done",
            }
        else:
            found = FLAG_RE.findall(obs)
            if found:
                flag = found[-1]
                script = (
                    "import glob\n"
                    "for p in glob.glob('challenge/*'):\n"
                    "    try:\n"
                    "        t = open(p, 'r', errors='replace').read()\n"
                    "    except Exception:\n"
                    "        continue\n"
                    "    for line in t.splitlines():\n"
                    "        if '{' in line and '}' in line and 'pico' in line.lower():\n"
                    "            print(line.strip())\n"
                )
                action = {"thought": "Found it; submit with a script that reproduces it", "action": "submit",
                          "flag": flag, "script": script}
            else:
                action = {"thought": "Nothing found; submit a placeholder so the pipeline completes", "action": "submit",
                          "flag": "picoCTF{mock_not_found}", "script": "print('nothing')"}

        text = "```json\n" + json.dumps(action, ensure_ascii=False) + "\n```"
        if on_delta is not None:
            on_delta(text)
        return Completion(text=text, prompt_tokens=len(last) // 4,
                          completion_tokens=len(text) // 4, ttft_ms=1,
                          latency_ms=2, finish_reason="stop", usage_source="estimated")


def build_client(spec: ModelSpec) -> ChatClient:
    if spec.name == "mock":
        return MockClient()
    return ChatClient(spec)
