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
import os
import re
import shutil
import time
import zipfile
from pathlib import Path, PurePosixPath

from .tasks import Task, web_url

SERVICE_NOTE = ("Note: in the original CTF this challenge ran as a network service. The sandbox here has no network, so the "
                "service cannot be reached; study the files, and do not try to install packages or connect anywhere.")
_SERVICE_PHRASE = "ran as a network service"

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
    source: str = "",
) -> Task:
    """落盘一道自建题并返回 Task。

    files 是 [(原始文件名, 字节)]。若只传了一个 .zip，会**额外**解一份到
    challenge/ 下（zip 本身保留），因为多数 CTF 附件就是压缩包，
    让模型先解压纯属浪费轮次。
    """
    root = root or store_root()
    files = list(files or [])
    title = (title or "").strip()[:200] or "Untitled challenge"
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
        "source": web_url(source),   # 原题网页（从网上导入时有）
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


def _meta_file(name: str, root: Path | None) -> Path:
    base = (root or store_root()) / name
    f = base / "task.json"
    if not f.is_file() or not base.resolve().is_relative_to((root or store_root()).resolve()):
        raise ValueError(f"No uploaded challenge called {name}")
    return f


def _write_meta(f: Path, meta: dict) -> Task:
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, f)
    return _to_task(meta, f.parent / "challenge")


def set_gold(name: str, gold: str, root: Path | None = None, source: str = "you") -> Task:
    """给自建 / 导入的题补上标准答案（只存哈希）。之后的运行就能判 verified / wrong，而不只是"能不能复现"。

    source 记下答案从哪来（you = 事后在网页上补的）：界面据此说"与保存的答案一致"，而不是"flag 正确"。
    """
    gold = (gold or "").strip()
    if not gold or len(gold) > 300 or "\n" in gold:
        raise ValueError("The answer must be one line of at most 300 characters")
    f = _meta_file(name, root)
    meta = json.loads(f.read_text(encoding="utf-8"))
    meta["gold_sha256"] = hashlib.sha256(gold.encode()).hexdigest()
    meta["gold_provided"] = True
    meta["gold_source"] = source
    return _write_meta(f, meta)


def update(name: str, title: str | None = None, query: str | None = None, category: str | None = None,
           root: Path | None = None) -> Task:
    """改导入 / 上传题的标题、题面、类别（网页上"Customise"，可以先让 AI 起草）。

    原来是网络服务的题，改题面时把这一点记在 meta 里：题面里那句说明被删掉，界面的提示和给模型的说明也还在。
    """
    f = _meta_file(name, root)
    meta = json.loads(f.read_text(encoding="utf-8"))
    if title is not None:
        t = " ".join(str(title).split())[:200]
        if not t:
            raise ValueError("The title cannot be empty")
        meta["title"] = t
    if query is not None:
        if _SERVICE_PHRASE in (meta.get("query") or ""):
            meta["service"] = True
        meta["query"] = str(query).strip()[:6000]
    if category is not None:
        meta["category"] = " ".join(str(category).split())[:40] or "custom"
    return _write_meta(f, meta)


def clear_gold(name: str, root: Path | None = None) -> Task:
    """删掉保存的答案（存错了，比如存成了模型自己编的 flag）。之后的运行回到"无答案可比"。"""
    f = _meta_file(name, root)
    meta = json.loads(f.read_text(encoding="utf-8"))
    meta["gold_sha256"], meta["gold_provided"] = "", False
    meta.pop("gold_source", None)
    return _write_meta(f, meta)


def _source_from_query(query: str) -> str:
    """加来源字段之前导入的题：题面末尾有 "(Imported from …)"，从这里还原原题网页。"""
    import urllib.parse
    m = re.search(r"\(Imported from (?:sajjadium/ctf-archives: (ctfs/[^)\n]+)|(https?://[^)\s]+))\)", query)
    if not m:
        return ""
    if m.group(1):
        return web_url("https://github.com/sajjadium/ctf-archives/tree/main/" + urllib.parse.quote(m.group(1).strip(), safe="/"))
    return web_url(m.group(2))


def _to_task(meta: dict, chal: Path) -> Task:
    # 老记录只有 files，新记录有 assets；两者取并，缺哪个都不会漏附件
    files = list(meta.get("assets") or meta.get("files") or [])
    title = meta.get("title") or ""
    query = meta.get("query") or ""
    service = bool(meta.get("service")) or _SERVICE_PHRASE in query
    brief = title
    if query and query.strip() != title.strip():
        brief = f"{title}\n\n{query}"
    if service and _SERVICE_PHRASE not in query:
        brief += "\n\n" + SERVICE_NOTE            # 题面被改写过：模型仍要知道服务连不上
    return Task(
        service=service,
        source=web_url(meta.get("source")) or _source_from_query(meta.get("query") or ""),
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
