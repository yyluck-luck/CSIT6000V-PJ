"""NYU CTF Bench 加载器。

proposal 的承诺是 "Data is fetched at run time, not redistributed"，所以这里
只做两件事：从上游 clone，再转成本项目的题库格式。转换产物**不含明文 flag**
（只留 sha256），仓库里也不分发任何题目文件。

筛选口径对齐 proposal：只取 crypto + rev 两类，且只保留离线可跑的题
（``compose: false``）。需要 docker 起服务的题会被排除并记进 EXCLUDED.md——
proposal 原文是 "We keep only offline challenges and report every exclusion"。

上游目录形如 ``<split>/<year>/<event>/<category>/<challenge>/``，每道题一个
``challenge.json``，里面同时有题面和明文 flag。这个文件**不会**被复制到
题库目录：``stage_assets`` 只跳过 ``task.json`` 和 ``solution*``，挡不住它。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPO_URL = "https://github.com/NYU-LLM-CTF/NYU_CTF_Bench.git"

# proposal 只承诺 crypto 与 RE
CATEGORIES = {"crypto": "Cryptography", "rev": "Reverse Engineering"}
SPLITS = {"dev": "development", "test": "test"}


def default_cache() -> Path:
    return Path.home() / ".cache" / "flagpilot" / "nyu-ctf-bench"


def fetch(cache: Path | None = None, force: bool = False) -> Path:
    """把上游仓库克隆/更新到本地缓存。返回仓库根目录。

    用 --depth 1：只需要最新一份快照，历史对本项目没有价值，而完整历史
    会让首次下载从几百 MB 变成几 GB。
    """
    cache = Path(cache or default_cache()).expanduser()
    if (cache / ".git").exists():
        if force:
            subprocess.run(["git", "-C", str(cache), "pull", "--ff-only"],
                           check=True, capture_output=True, text=True)
        return cache
    cache.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--depth", "1", REPO_URL, str(cache)],
                   check=True, capture_output=True, text=True)
    return cache


def _repo_commit(repo: Path) -> str:
    try:
        r = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def iter_challenges(repo: Path, split: str):
    """遍历一个 split 下的所有题目，按题目目录名排序保证可复现。"""
    root = Path(repo) / SPLITS.get(split, split)
    if not root.is_dir():
        raise FileNotFoundError(f"找不到 split 目录：{root}")
    for path in sorted(root.rglob("challenge.json")):
        try:
            meta = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        yield path.parent, meta


def _safe_name(rel: Path) -> str:
    """题目目录名可能带空格和单引号，统一压成可当路径用的形式。"""
    parts = [p for p in rel.parts if p not in ("development", "test")]
    raw = "-".join(parts).lower()
    out = []
    for ch in raw:
        out.append(ch if ch.isalnum() else "-")
    name = "".join(out)
    while "--" in name:
        name = name.replace("--", "-")
    return name.strip("-")


def _copy_files(src_dir: Path, meta: dict, dest: Path) -> list[str]:
    """只拷 challenge.json 里声明的 files，其余一律不带。

    不能整目录复制：题目目录里常有 Dockerfile、node_modules、writeup、
    test_solver——既占空间，也可能把解法带进沙箱。
    """
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for rel in meta.get("files") or []:
        rel = str(rel).strip()
        if not rel:
            continue
        src = src_dir / rel
        if not src.exists():
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, target, dirs_exist_ok=True)
        else:
            shutil.copyfile(src, target)
        copied.append(rel)
    return copied


def convert(
    repo: Path,
    split: str,
    out_root: Path,
    include_compose: bool = False,
) -> dict:
    """把上游转成本项目题库格式，返回统计。

    产出：``index/tasks.jsonl`` + ``tasks/<name>/<files>`` + ``EXCLUDED.md``
    + ``SOURCE.json``。明文 flag 只在这里被读一次，随后只留 sha256。
    """
    out_root = Path(out_root)
    (out_root / "index").mkdir(parents=True, exist_ok=True)
    (out_root / "tasks").mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    excluded: list[tuple[str, str]] = []
    used: set[str] = set()
    next_id = 1

    for src_dir, meta in iter_challenges(repo, split):
        category = str(meta.get("category") or "").strip().lower()
        rel = src_dir.relative_to(Path(repo) / SPLITS.get(split, split))
        label = f"{rel}"

        if category not in CATEGORIES:
            continue
        flag = str(meta.get("flag") or "").strip()
        if not flag:
            excluded.append((label, "no flag in challenge.json"))
            continue
        if meta.get("compose") and not include_compose:
            # 这类题要起 docker 服务，离线 replay 跑不了
            excluded.append((label, "requires docker compose (not offline)"))
            continue

        name = _safe_name(rel)
        if name in used:
            name = f"{name}-{next_id}"
        used.add(name)

        files = _copy_files(src_dir, meta, out_root / "tasks" / name)
        description = str(meta.get("description") or "").strip()
        if not description:
            description = str(meta.get("name") or name)

        rows.append({
            "task_id": next_id,
            "name": name,
            "category": CATEGORIES[category],
            "title": description,
            "query": description,
            "gold_sha256": hashlib.sha256(flag.encode()).hexdigest(),
            "assets": files,
            "assets_dir": f"tasks/{name}",
            "suggested_tools": [],
            "scope": "core",
        })
        next_id += 1

    index = out_root / "index" / "tasks.jsonl"
    index.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""),
        encoding="utf-8")

    (out_root / "EXCLUDED.md").write_text(_excluded_md(split, excluded), encoding="utf-8")
    (out_root / "SOURCE.json").write_text(json.dumps({
        "dataset": "NYU CTF Bench",
        "repo": REPO_URL,
        "license": "GPL-2.0 (challenges retain their original licences)",
        "split": split,
        "commit": _repo_commit(repo),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "categories": list(CATEGORIES.values()),
        "include_compose": include_compose,
        "tasks": len(rows),
        "excluded": len(excluded),
        "note": "Plaintext flags are never written here; only sha256 digests.",
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"tasks": len(rows), "excluded": len(excluded), "root": str(out_root),
            "index": str(index)}


def _excluded_md(split: str, excluded: list[tuple[str, str]]) -> str:
    lines = [f"# Excluded challenges ({split})", "",
             "proposal: *We keep only offline challenges and report every exclusion.*", ""]
    if not excluded:
        lines.append("None.")
    for label, why in excluded:
        lines.append(f"- `{label}` — {why}")
    return "\n".join(lines) + "\n"
