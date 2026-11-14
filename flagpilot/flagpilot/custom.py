"""自建题目：把用户上传的题面 + 附件变成一条可跑的 Task。

为什么单独一个模块：
  内置题库是只读的评测集（有 gold_sha256、有 scope、有固定数量）。
  用户上传的题**没有 gold**，因此它不能进评测统计，只能用于自测与讲解。
  这两类东西混在一个列表里，报告的分母就会被悄悄改掉——所以类型上分开，
  只在界面上并列展示。

三个刻意的约束：
  * **没有 gold 就永不判 verified**。用户不填标准答案时，最多只能判
    `reproduced`（重放复现成功），因为它到底对不对无从校验。否则用户随手
    传一道题、随手填个 flag，就能刷出 100% 的 verified 率。
  * **附件目录用绝对路径**。Task.assets_dir 一般是相对题库根的路径，
    `root / abs_path` 在 pathlib 下会直接返回 abs_path，所以沙箱、重放、
    材料扫描三处都不需要为自建题加分支。
  * **zip 解压必须防穿越**。上传的 zip 里写 `../../.ssh/authorized_keys`
    是很常见的攻击面，这里逐条校验成员路径，失败就整包拒绝。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import zipfile
from pathlib import Path, PurePosixPath

from .tasks import Task

MAX_FILES = 20
MAX_FILE_BYTES = 32 * 1024 * 1024          # 单文件 32MB
MAX_TOTAL_BYTES = 128 * 1024 * 1024        # 合计 128MB
MAX_ZIP_ENTRIES = 500

_BAD_CHARS = re.compile(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+")


def store_root() -> Path:
    """自建题的落盘位置。放在 runs/ 下，因为它本来就属于运行产物。"""
    from . import config

    return config.RUNS_ROOT / "custom"


# ---------------------------------------------------------------- 命名与校验


def _safe_name(name: str) -> str:
    """只保留 basename，并清掉危险字符。空名退回 upload.bin。"""
    base = PurePosixPath(str(name).replace("\\", "/")).name
    base = _BAD_CHARS.sub("_", base).strip("._") or "upload.bin"
    return base[:120]


def _safe_member(name: str) -> Path | None:
    """zip 成员 → 相对路径。绝对路径、..、空路径一律拒绝。"""
    raw = str(name).replace("\\", "/")
    p = PurePosixPath(raw)
    if p.is_absolute() or raw.startswith("/"):
        return None
    parts = [x for x in p.parts if x not in ("", ".")]
    if not parts or any(x == ".." for x in parts):
        return None
    return Path(*parts)


def _slug(title: str) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    tail = hashlib.sha1(f"{title}{time.time_ns()}".encode()).hexdigest()[:4]
    stem = _BAD_CHARS.sub("-", title).strip("-")[:24] or "task"
    return f"custom-{stamp}-{stem}-{tail}"


# ---------------------------------------------------------------- 创建


def create(
    title: str,
    query: str,
    files: list[tuple[str, bytes]] | None = None,
    category: str = "custom",
    gold: str = "",
    root: Path | None = None,
) -> Task:
    """落盘一道自建题并返回 Task。

    files 是 [(原始文件名, 字节)]。若只传了一个 .zip，会**额外**解一份到
    challenge/ 下（zip 本身保留），因为多数 CTF 附件就是压缩包，
    让模型先解压纯属浪费轮次。
    """
    root = root or store_root()
    files = list(files or [])
    title = (title or "").strip()[:200] or "未命名题目"
    query = (query or "").strip()
    if not files and not query:
        raise ValueError("至少要给题面或附件之一")

    if len(files) > MAX_FILES:
        raise ValueError(f"文件太多（{len(files)} > {MAX_FILES}）")
    total = sum(len(b) for _, b in files)
    if total > MAX_TOTAL_BYTES:
        raise ValueError(f"附件合计 {total / 1e6:.1f}MB，超过 {MAX_TOTAL_BYTES / 1e6:.0f}MB 上限")
    for name, data in files:
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"{name} 超过单文件 {MAX_FILE_BYTES / 1e6:.0f}MB 上限")

    slug = _slug(title)
    base = Path(root) / slug
    chal = base / "challenge"
    chal.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    seen: dict[str, int] = {}
    try:
        for name, data in files:
            safe = _safe_name(name)
            if safe in seen:
                seen[safe] += 1
                stem, dot, ext = safe.partition(".")
                safe = f"{stem}-{seen[safe]}{dot}{ext}"
            else:
                seen[safe] = 1
            (chal / safe).write_bytes(data)
            written.append(safe)

            if safe.lower().endswith(".zip"):
                _extract_zip(chal / safe, chal)
    except Exception:
        # zip 解到一半失败/超限时别留半个目录，否则下次 load() 会把它当题
        shutil.rmtree(base, ignore_errors=True)
        raise

    assets = sorted(
        str(p.relative_to(chal)) for p in chal.rglob("*") if p.is_file()
    )

    gold_sha = hashlib.sha256(gold.strip().encode()).hexdigest() if gold.strip() else ""
    meta = {
        "slug": slug,
        "title": title,
        "query": query,
        "category": (category or "custom").strip()[:40],
        "gold_sha256": gold_sha,
        "gold_provided": bool(gold_sha),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "files": written,     # 用户上传的原始文件名
        "assets": assets,     # challenge/ 下的实际文件（含 zip 解出来的）
    }
    (base / "task.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return _to_task(meta, chal)


def _extract_zip(zip_path: Path, dest: Path) -> int:
    """解压到 dest，逐条校验路径与大小。返回解出的文件数。

    注意：`info.file_size` 来自 zip 中央目录，是可以伪造的。只按它记账的
    结果是"声明 1KB、实际解出 10GB"的压缩炸弹能绕过 128MB 上限。所以这里
    一边写一边数真实字节，超了立刻删掉半成品并整包拒绝。
    """
    n = 0
    total_written = 0
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise ValueError(f"zip 内文件过多（{len(infos)} > {MAX_ZIP_ENTRIES}）")
        for info in infos:
            if info.is_dir():
                continue
            rel = _safe_member(info.filename)
            if rel is None:
                raise ValueError(f"zip 含非法路径：{info.filename!r}（绝对路径或 ..）")
            if any(part == "__MACOSX" for part in rel.parts):
                continue
            if info.file_size > MAX_FILE_BYTES or total_written + info.file_size > MAX_TOTAL_BYTES:
                raise ValueError(f"zip 内 {info.filename} 解压后过大")
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            try:
                with zf.open(info) as src, target.open("wb") as out:
                    while True:
                        chunk = src.read(1 << 20)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > MAX_FILE_BYTES or total_written + written > MAX_TOTAL_BYTES:
                            raise ValueError(
                                f"zip 内 {info.filename} 实际解压超过上限"
                                "（中央目录声明的大小可能是伪造的）")
                        out.write(chunk)
            except Exception:
                try:
                    target.unlink()
                except OSError:
                    pass
                raise
            total_written += written
            n += 1
    return n


def _to_task(meta: dict, chal: Path) -> Task:
    # 老记录只有 files，新记录有 assets；两者取并，缺哪个都不会漏附件
    files = list(meta.get("assets") or meta.get("files") or [])
    title = meta.get("title") or ""
    query = meta.get("query") or ""
    brief = title
    if query and query.strip() != title.strip():
        brief = f"{title}\n\n{query}"
    return Task(
        # 负数 id：与内置题库（正整数）不可能撞
        task_id=-abs(hash(meta["slug"])) % 900000 - 1000,
        name=meta["slug"],
        category=meta.get("category") or "custom",
        title=title,
        query=query,
        gold_sha256=meta.get("gold_sha256") or "",
        assets=files,
        assets_dir=str(chal.resolve()),
        suggested_tools=[],
        scope="custom",
        brief_override=brief,
    )


# ---------------------------------------------------------------- 读取


def load(root: Path | None = None) -> list[Task]:
    """按创建时间倒序列出自建题。坏掉的目录跳过，不影响整体。"""
    root = root or store_root()
    if not root.exists():
        return []
    out: list[tuple[float, Task]] = []
    for meta_path in sorted(root.glob("*/task.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            chal = meta_path.parent / "challenge"
            if not chal.exists():
                continue
            out.append((meta_path.stat().st_mtime, _to_task(meta, chal)))
        except Exception:
            continue
    out.sort(key=lambda x: -x[0])
    return [t for _, t in out]


def get(name: str, root: Path | None = None) -> Task | None:
    for t in load(root):
        if t.name == name:
            return t
    return None


def delete(name: str, root: Path | None = None) -> bool:
    """删一道自建题。只允许删 store 下的目录，不碰别的东西。"""
    root = (root or store_root()).resolve()
    target = (root / name).resolve()
    if root not in target.parents or not target.is_dir():
        return False
    # challenge 里的附件被设过只读，先恢复权限再删
    for p in target.rglob("*"):
        try:
            p.chmod(0o755)
        except Exception:
            pass
    shutil.rmtree(target, ignore_errors=True)
    return not target.exists()
