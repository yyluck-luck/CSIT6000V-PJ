"""Track L 的成本：GPU 秒。

两个口径，报告里必须分开写，不能混成一个数：

  allocated  申请到的卡数 × 批次墙钟。这是集群真正扣你的配额，也是"每个
             verified 花了多少算力"的诚实分母——卡在等工具、等重放时也在计费。
  busy       nvidia-smi 利用率加权：Σ util% × Δt。说明卡有多忙，用来解释
             allocated 里有多少是空转（agent 跑 shell / 重放时 GPU 是闲的）。

只在作业内（检测到 GPU）记账；本地没有 GPU 就不写，报告里显示"—"，
不用 0 冒充"没花钱"。采样文件由 deploy/run.slurm 的后台采样器写，格式：

    epoch_seconds,gpu_index,utilization_percent,memory_used_mib
"""

from __future__ import annotations

import json
import os
from pathlib import Path

LEDGER = "gpu.jsonl"


def detect_gpus(environ=None) -> int:
    """本作业申请到的 GPU 数。显式 FP_GPUS 优先，其次 Slurm，再次 CUDA_VISIBLE_DEVICES。"""
    env = os.environ if environ is None else environ
    raw = (env.get("FP_GPUS") or "").strip()
    if raw.isdigit():
        return int(raw)
    raw = (env.get("SLURM_GPUS_ON_NODE") or "").strip()
    if raw.isdigit():
        return int(raw)
    cvd = env.get("CUDA_VISIBLE_DEVICES")
    if cvd is not None:
        return len([x for x in cvd.split(",")
                    if x.strip() and x.strip() not in ("NoDevFiles", "-1")])
    return 0


def busy_gpu_seconds(csv_path, start: float | None = None,
                     end: float | None = None) -> float | None:
    """利用率加权的 GPU 秒，只统计 [start, end] 窗口内的部分。

    每个采样点的利用率覆盖到同一张卡的下一个采样点（左端点法）。
    文件不存在、或窗口内没有任何采样区间，返回 None——不把"没测到"当成 0。
    """
    path = Path(csv_path) if csv_path else None
    if path is None or not path.exists():
        return None
    by_gpu: dict[str, list[tuple[float, float]]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            ts, util = float(parts[0]), float(parts[2])
        except ValueError:
            continue
        by_gpu.setdefault(parts[1], []).append((ts, util))
    total, covered = 0.0, False
    for samples in by_gpu.values():
        samples.sort()
        for (t0, u0), (t1, _) in zip(samples, samples[1:]):
            a = t0 if start is None else max(t0, start)
            b = t1 if end is None else min(t1, end)
            if b > a:
                covered = True
                total += (u0 / 100.0) * (b - a)
    return round(total, 3) if covered else None


def append_ledger(batch_dir, entry: dict) -> None:
    d = Path(batch_dir)
    d.mkdir(parents=True, exist_ok=True)
    with (d / LEDGER).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_ledger(batch_dir) -> dict | None:
    """汇总一个批次跨多个作业（续跑 / 分片）的 GPU 秒。没有账本返回 None。"""
    path = Path(batch_dir) / LEDGER
    if not path.exists():
        return None
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not rows:
        return None
    busy = [r["busy_gpu_seconds"] for r in rows if r.get("busy_gpu_seconds") is not None]
    return {
        "jobs": len(rows),
        "gpu_seconds": round(sum(float(r.get("gpu_seconds") or 0) for r in rows), 1),
        # busy 只覆盖采到了利用率的作业；busy_jobs < jobs 时报告里要注明
        "busy_gpu_seconds": round(sum(busy), 1) if busy else None,
        "busy_jobs": len(busy),
        "wall_s": round(sum(float(r.get("wall_s") or 0) for r in rows), 1),
    }
