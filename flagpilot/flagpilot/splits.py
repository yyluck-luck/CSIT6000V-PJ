"""dev / test 划分，以及"测试集每个最终配置只跑一次"的守卫。

proposal 的承诺是：所有设计决策只看 dev，test 只在配置定稿后跑一次。
靠人守会漏——改一个 prompt 再在 test 上"顺手看一眼"，test 就变成了第二个 dev。
所以这里做成硬约束：

  1. 跑 test 必须显式 --confirm-test；
  2. 同一个最终配置（指纹相同）只能对应一个 test 批次——换个批次名重跑会被拒；
  3. 同一个 test 批次续跑（2h 作业到点后重交）允许，但配置变了不行，
     否则 runs.jsonl 里会混进两套配置的结果。

划分本身是团队决策（影响 E1/E2），所以这里只提供机制和一个可复现的生成器；
默认 --split all，不改变任何现有行为。划分文件里记录题库索引的 sha256，
题库变了会被发现，而不是悄悄套用旧划分。
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
from pathlib import Path

from . import config

LEDGER = "test_runs.jsonl"


def index_sha256(tasks_root: Path | None = None) -> str:
    root = Path(tasks_root or config.TASKS_ROOT)
    return hashlib.sha256((root / "index" / "tasks.jsonl").read_bytes()).hexdigest()


def default_split_path(tasks_root: Path | None = None) -> Path:
    raw = os.environ.get("FLAGPILOT_SPLITS")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(tasks_root or config.TASKS_ROOT) / "index" / "splits.json"


def make_split(tasks, seed: int = 0, test_frac: float = 0.5, index_sha: str = "") -> dict:
    """按类别分层的确定性划分：同一份题库 + 同一个 seed 永远得到同一个划分。"""
    if not 0.0 < test_frac < 1.0:
        raise ValueError("test_frac 必须在 (0, 1) 之间")
    rng = random.Random(seed)
    by_cat: dict[str, list[str]] = {}
    for t in tasks:
        by_cat.setdefault(t.category, []).append(t.name)
    dev, test, counts = [], [], {}
    for cat in sorted(by_cat):
        names = sorted(by_cat[cat])
        rng.shuffle(names)
        k = round(len(names) * test_frac)
        test += names[:k]
        dev += names[k:]
        counts[cat] = {"dev": len(names) - k, "test": k}
    return {
        "seed": seed,
        "test_frac": test_frac,
        "index_sha256": index_sha,
        "counts": counts,
        "dev": sorted(dev),
        "test": sorted(test),
        "status": "proposed",   # 团队确认后改成 "frozen"，并把文件和提交哈希写进报告
    }


def load_split(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"找不到划分文件：{path}\n"
            f"先生成：python3 scripts/make_split.py --root <题库根> --seed 0（见 docs/TACC.md 第 3 节）")
    data = json.loads(path.read_text(encoding="utf-8"))
    dev, test = set(data.get("dev") or []), set(data.get("test") or [])
    if dev & test:
        raise ValueError(f"划分文件 dev 与 test 有重叠：{sorted(dev & test)[:5]}")
    return {**data, "dev": dev, "test": test, "path": str(path)}


def filter_tasks(tasks, which: str, split: dict):
    if which == "all":
        return list(tasks)
    if which not in ("dev", "test"):
        raise ValueError(f"未知划分 {which!r}")
    keep = split[which]
    return [t for t in tasks if t.name in keep]


def config_hash(fingerprint: dict) -> str:
    """最终配置的指纹：去掉批次名（换个名字重跑仍是同一个配置）。"""
    fp = {k: v for k, v in fingerprint.items() if k != "batch"}
    blob = json.dumps(fp, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def guard_test_run(runs_root: Path, batch: str, fp_hash: str, confirm: bool) -> str:
    """通过返回说明文字；拒绝抛 PermissionError（CLI 转成非零退出）。"""
    if not confirm:
        raise PermissionError(
            "这是 test 划分：只在配置定稿后跑一次。确认后加 --confirm-test。"
            "（调参、改 prompt、看效果一律用 --split dev）")
    path = Path(runs_root) / LEDGER
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    for r in rows:
        if r.get("fp") == fp_hash and r.get("batch") != batch:
            raise PermissionError(
                f"同一个最终配置已经在 test 上跑过（批次 {r.get('batch')}，指纹 {fp_hash}）。"
                "换批次名重跑等于在 test 上挑结果，已拒绝。")
        if r.get("batch") == batch and r.get("fp") != fp_hash:
            raise PermissionError(
                f"test 批次 {batch} 已用另一套配置跑过（指纹 {r.get('fp')}），"
                "继续跑会让 runs.jsonl 混入两套配置。请换批次名并确认这是新的最终配置。")
    if any(r.get("batch") == batch and r.get("fp") == fp_hash for r in rows):
        return f"续跑 test 批次 {batch}（指纹 {fp_hash}）"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"batch": batch, "fp": fp_hash,
                             "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                            ensure_ascii=False) + "\n")
    return f"登记 test 批次 {batch}（指纹 {fp_hash}）"
