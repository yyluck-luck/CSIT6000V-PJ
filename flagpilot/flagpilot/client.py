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
    # Ollama 等运行时会单独返回思维链，正文可能为空。留档用于诊断。
    reasoning: str = ""

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class ServerMetrics:
    """从 /metrics 抓到的量。取不到就是空 dict，不编数字。"""

    values: dict = field(default_factory=dict)
    error: str = ""


class ChatClient:
    def __init__(self, spec: ModelSpec):
        self.spec = spec
        self._url = spec.base_url.rstrip("/") + "/chat/completions"
        # 有些运行时（Ollama / llama.cpp / 老版本 vLLM）不认识 chat_template_kwargs，
        # 会直接 400。第一次撞上就永久降级，避免每次请求都白撞一遍。
        self._no_template_kwargs = False

    # ---------- 主路径 ----------

    def complete(
        self,
        messages: list[dict],
        max_tokens: int | None = None,
        on_delta: Callable[[str], None] | None = None,
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
        # vLLM 会把 chat_template_kwargs 透传给 tokenizer 的 chat template。
        # 只在服务端支持时才带——不支持会在下面自动降级。
        if not self._no_template_kwargs:
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
            if e.code == 400 and not self._no_template_kwargs:
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
            reasoning=reasoning[:2000],
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
                        "我先看题目给了哪些附件，再决定用什么手法。")
            else:
                text = ("这是 mock 助教的示例回答。真实模型会在这里给出成段讲解："
                        "先给结论，再说明为什么，必要时先用 <run> 跑命令验证。")
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
            action = {"thought": "先看有哪些文件", "action": "shell", "cmd": "ls -la challenge"}
        elif n_obs == 1:
            action = {
                "thought": "把每个文件的内容打出来看看",
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
                action = {"thought": "找到了，提交并附上复现脚本", "action": "submit",
                          "flag": flag, "script": script}
            else:
                action = {"thought": "没找到，交一个占位答案让流程走完", "action": "submit",
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
