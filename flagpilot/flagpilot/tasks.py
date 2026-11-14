"""题库接口。

刻意只读 index/tasks.jsonl（只有 gold_sha256，没有明文）。
_private/gold_private.json 只在本地自测时使用，**不得上传到集群**——
这一点由 doctor 命令检查。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import config


@dataclass
class Task:
    task_id: int
    name: str
    category: str
    title: str
    query: str
    gold_sha256: str
    assets: list[str]
    assets_dir: str
    suggested_tools: list[str]
    scope: str
    # 自建题目用：题面比 title 长，两者都要给模型看。
    # 内置题留空，行为与之前完全一致。
    brief_override: str = ""

    @property
    def has_gold(self) -> bool:
        """有没有标准答案。没有就只能判 reproduced，永远判不了 verified。"""
        return bool((self.gold_sha256 or "").strip())

    @property
    def brief(self) -> str:
        """给模型看的题面。注意：这里绝对不能拼接任何 gold 信息。"""
        head = self.brief_override.strip() or self.title.strip() or self.query.strip()
        lines = [head, ""]
        if self.assets:
            lines.append("Files provided (in ./challenge/):")
            for a in self.assets:
                if a == ".placeholder":
                    continue
                lines.append(f"  - {a}")
        else:
            lines.append("No files are provided for this challenge.")
        return "\n".join(lines)

    def asset_paths(self, root: Path) -> list[Path]:
        base = root / self.assets_dir
        out = []
        for a in self.assets:
            if a == ".placeholder":
                continue
            p = base / a
            if p.exists():
                out.append(p)
        return out


def load_index(root: Path | None = None) -> list[Task]:
    root = root or config.TASKS_ROOT
    index = root / "index" / "tasks.jsonl"
    if not index.exists():
        raise FileNotFoundError(
            f"找不到题库索引：{index}\n"
            f"请把题库放到 tasks/，或用 FLAGPILOT_TASKS 指向题库根目录"
            f"（结构见 docs/TASKS.md）。"
        )
    tasks: list[Task] = []
    for line in index.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        tasks.append(
            Task(
                task_id=int(r["task_id"]),
                name=r["name"],
                category=r["category"],
                title=r.get("title") or "",
                query=r.get("query") or "",
                gold_sha256=r["gold_sha256"],
                assets=list(r.get("assets") or []),
                assets_dir=r["assets_dir"],
                suggested_tools=list(r.get("suggested_tools") or []),
                scope=r.get("scope", "core"),
            )
        )
    return tasks


def select(
    tasks: list[Task],
    names: list[str] | None = None,
    ids: list[int] | None = None,
    category: str | None = None,
    scope: str | None = None,
    limit: int | None = None,
    shard: tuple[int, int] | None = None,
) -> list[Task]:
    """筛选题目。shard=(i, n) 表示取第 i 片（0 起）。"""
    out = tasks
    if names:
        want = {n.strip() for n in names}
        out = [t for t in out if t.name in want or str(t.task_id) in want]
    if ids:
        want_ids = set(ids)
        out = [t for t in out if t.task_id in want_ids]
    if category:
        out = [t for t in out if t.category.lower().startswith(category.lower())]
    if scope:
        out = [t for t in out if t.scope == scope]
    out = sorted(out, key=lambda t: t.task_id)
    if shard:
        i, n = shard
        if not (0 <= i < n):
            raise ValueError(f"非法分片 {i}/{n}")
        out = [t for k, t in enumerate(out) if k % n == i]
    if limit:
        out = out[:limit]
    return out


def stage_assets(task: Task, dest: Path, root: Path | None = None) -> int:
    """把题目附件拷进沙箱工作目录的 challenge/ 下。返回文件数。

    只从 no-solutions 版本复制——题解目录已在构建阶段剥离，这里再兜一层：
    任何名为 solution 的目录一律跳过。
    """
    root = root or config.TASKS_ROOT
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    src_base = root / task.assets_dir
    if not src_base.exists():
        return 0
    for src in sorted(src_base.rglob("*")):
        rel = src.relative_to(src_base)
        if any(part.lower() == "solution" for part in rel.parts):
            continue
        if rel.name == "task.json":
            continue
        target = dest / rel
        if src.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            n += 1
    return n
