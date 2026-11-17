"""计价：只有 Track H（托管 API）有真实的美元成本；Track L 的成本是 GPU 秒，见 gpu.py。

价目表刻意写死在代码里，并注明来源与核对日期——和消融档位一样，
"多少钱"一旦能随手改，报告里的成本就不可复现。服务商改价时，
在这里**加一个新表**（新名字），而不是改旧数字。

两个容易算错的地方：
  * DeepSeek 按"缓存命中 / 未命中"分开计输入价，差 50 倍。按总 prompt token
    乘一个单价，会把成本高估一个数量级（多轮 agent 的前缀几乎全命中）。
  * 高峰 / 非高峰差两倍，按**请求发出时刻**判定，所以成本逐轮累加，
    而不是 attempt 结束后用总 token 一次乘出来。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Rates:
    """美元 / 每百万 token。"""

    in_hit: float
    in_miss: float
    out: float


@dataclass(frozen=True)
class PriceTable:
    name: str
    source: str
    checked: str                         # 核对日期（UTC）
    off_peak: Rates
    peak: Rates
    peak_weekdays: tuple[int, ...]       # Monday = 0
    peak_hours: tuple[tuple[int, int], ...]  # [start, end) UTC 小时
    note: str = ""


PRICE_TABLES: dict[str, PriceTable] = {
    "deepseek-flash": PriceTable(
        name="deepseek-flash（DeepSeek-V4.1-Flash-1017）",
        source="https://api-docs.deepseek.com/quick_start/pricing",
        checked="2026-11-16",
        off_peak=Rates(in_hit=0.003, in_miss=0.15, out=0.60),
        peak=Rates(in_hit=0.006, in_miss=0.30, out=1.20),
        peak_weekdays=(0, 1, 2, 3, 4),
        peak_hours=((1, 4), (6, 10)),
        # 文档写"中国法定节假日全天按非高峰"。标准库没有节假日日历，
        # 这里一律按工作日规则算——节假日会被**高估**，方向是保守的。
        note="中国法定节假日未建模（按工作日计，成本偏保守）",
    ),
}


def is_peak(table: PriceTable, when: datetime) -> bool:
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    when = when.astimezone(timezone.utc)
    if when.weekday() not in table.peak_weekdays:
        return False
    return any(lo <= when.hour < hi for lo, hi in table.peak_hours)


def turn_cost(spec, comp, when: datetime | None = None) -> float:
    """单轮请求的美元成本。

    spec  ModelSpec（price_table 或 price_in/out_per_mtok）
    comp  client.Completion（prompt/completion token，可能带 cache 命中/未命中）

    有价目表时按"命中 / 未命中 / 输出"三档计价；服务端没回报命中数时，
    把全部 prompt 当作未命中——宁可高估，不编一个命中率出来。
    没有价目表时退回旧行为：prompt × price_in + completion × price_out。
    """
    table = PRICE_TABLES.get(getattr(spec, "price_table", "") or "")
    p_tok = int(getattr(comp, "prompt_tokens", 0) or 0)
    c_tok = int(getattr(comp, "completion_tokens", 0) or 0)
    if table is None:
        return (p_tok * float(spec.price_in_per_mtok or 0.0)
                + c_tok * float(spec.price_out_per_mtok or 0.0)) / 1_000_000

    hit = getattr(comp, "cache_hit_tokens", None)
    miss = getattr(comp, "cache_miss_tokens", None)
    if hit is None and miss is None:
        hit, miss = 0, p_tok
    elif miss is None:
        miss = max(0, p_tok - int(hit))
    elif hit is None:
        hit = max(0, p_tok - int(miss))
    rates = table.peak if is_peak(table, when or datetime.now(timezone.utc)) else table.off_peak
    return (int(hit) * rates.in_hit + int(miss) * rates.in_miss + c_tok * rates.out) / 1_000_000
