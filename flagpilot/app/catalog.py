"""在线题库：从 GitHub 上公开的 CTF 赛题归档里搜题、预览、导入；也能贴一个链接导入。

数据源：sajjadium/ctf-archives（ctfs/<比赛>/<年份>/<类别>/<题目>/，各比赛布局不完全一样）。
GitHub 匿名 API 每小时 60 次，所以按比赛懒加载：比赛列表 1 次（缓存一天），打开一个比赛 1 次
（git trees 递归，连文件大小一起，缓存在 runs/catalog/）；预览和下载走 raw.githubusercontent.com，不占 API 额度。
有 GITHUB_TOKEN（环境变量或仓库根目录 .env）就带上，额度变成每小时 5000 次。

导入时两件事防"抄答案"：解题脚本 / writeup 一律不进附件；flag.txt 内容像真 flag（不像占位符）时
当作 gold（只存哈希）并且不进附件。贴链接导入只接受 http(s)，并拒绝解析到内网 / 本机的地址（含重定向）。
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from flagpilot import config, custom, storage

REPO, BRANCH = "sajjadium/ctf-archives", "main"
API, RAW = "https://api.github.com", "https://raw.githubusercontent.com"
UA = "FlagPilot/0.1 (challenge import)"
CTF_TTL_S = 24 * 3600
CACHE_V = 2         # 题目识别规则的版本：缓存里存原始目录树，规则一变就地重算，不必再联网
README_MAX = 64 * 1024
CATS = {"crypto": "Cryptography", "cryptography": "Cryptography", "forensics": "Forensics", "forensic": "Forensics",
        "stego": "Forensics", "steganography": "Forensics", "rev": "Reverse Engineering", "reverse": "Reverse Engineering",
        "reversing": "Reverse Engineering", "re": "Reverse Engineering", "pwn": "Binary Exploitation",
        "binary": "Binary Exploitation", "exploitation": "Binary Exploitation", "web": "Web Exploitation",
        "misc": "General Skills", "general": "General Skills", "osint": "General Skills", "blockchain": "Blockchain",
        "hardware": "Hardware", "ppc": "General Skills", "programming": "General Skills"}
_STRUCT = re.compile(r"^((19|20)\d\d|challenges|chals|tasks|files|quals?|qualifiers?|qualification|finals?|prelims?|preliminar(y|ies)|"
                     r"(round|day|stage|wave|part)[ _-]?\d+)$", re.I)
_DOC = re.compile(r"^(readme|notes?|description|info)(\.(md|txt|markdown|rst))?$|\.(md|markdown)$", re.I)
_SOLUTION = re.compile(r"^(solve|solver|solution|sol|writeup|write-up|exploit|exp)([^a-z].*)?$", re.I)
_SOLUTION_DIR = re.compile(r"^(solutions?|writeups?|write-ups?|solve|solver|exploits?|sol)$", re.I)
_FLAG = re.compile(r"^[A-Za-z0-9_]{2,20}\{[^}\n]{2,200}\}$")
# 原比赛里是网络服务的题（pwn / web 常见）：沙箱没有网络，智能体只能看文件、连不上服务，基本拿不到 flag
_SERVICE_FILE = re.compile(r"^(dockerfile|docker-compose\.ya?ml|xinetd.*|.*\.xinetd|ctf\.xinetd)$", re.I)
_SERVICE_TEXT = re.compile(r"\bnc\s+[\w.-]+\s+\d{2,5}\b|\bhttps?://[\w.-]+:\d{2,5}\b|\bconnect(ion)? to\b.{0,40}\b(port|server|service)\b", re.I)
SERVICE_NOTE = ("Note: in the original CTF this challenge ran as a network service. The sandbox here has no network, so the "
                "service cannot be reached; study the files, and do not try to install packages or connect anywhere.")


_GENERIC = re.compile(r"^(description|challenge|readme|flag|solution|writeup|write-up|overview|task|problem|statement)s?$", re.I)


def readme_title(text: str) -> str:
    """题面 README 里的题目名：前 15 行里第一个 markdown 标题（去掉强调符号）；泛称（Description 之类）不算。"""
    for line in (text or "").splitlines()[:15]:
        m = re.match(r"^\s{0,3}#{1,3}\s+(.+?)\s*#*\s*$", line)
        if not m:
            continue
        t = re.sub(r"[*_`\[\]]|<[^>]+>", "", m.group(1)).strip(" :-")
        if 2 <= len(t) <= 120 and not _GENERIC.match(t):
            return t
    return ""


def is_service(files: list[list], readme: str) -> bool:
    return any(_SERVICE_FILE.match(rel.rpartition("/")[2]) for rel, _ in files) or bool(_SERVICE_TEXT.search(readme or ""))
_FAKE = re.compile(r"fake|placeholder|dummy|redacted|example|test|xxx|not_?the|real_?flag|local|sample|flag_?here|your_?flag", re.I)


class CatalogError(Exception):
    """说给用户听的错误（英文，界面直接显示）。"""


def _root() -> Path:
    return Path(config.RUNS_ROOT) / "catalog"


def _token() -> str:
    for k in ("GITHUB_TOKEN", "GH_TOKEN"):
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    if os.environ.get("FLAGPILOT_NO_DOTENV"):
        return ""
    try:
        for line in (config.PROJECT_ROOT.parent / ".env").read_text(encoding="utf-8").splitlines():
            for k in ("GITHUB_TOKEN", "GH_TOKEN"):
                if line.startswith(k + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


# ---------------------------------------------------------------- 网络

class _NoPrivateRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def check_url(url: str) -> None:
    """只接受 http(s)，主机必须解析到公网地址（挡住 localhost、内网、链路本地）。"""
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise CatalogError("Only http(s) links can be imported")
    try:
        addrs = {a[4][0] for a in socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80))}
    except OSError:
        raise CatalogError(f"Could not resolve {u.hostname}")
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%", 1)[0])
        if not ip.is_global:
            raise CatalogError("Links to local or private addresses are not allowed")


def _get(url: str, limit: int | None = None, accept: str = "application/vnd.github+json") -> bytes:
    headers = {"User-Agent": UA, "Accept": accept}
    tok = _token()
    if tok and url.startswith(API):
        headers["Authorization"] = "Bearer " + tok
    opener = urllib.request.build_opener(_NoPrivateRedirects)
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=40) as r:
            data = r.read((limit + 1) if limit else -1)
    except urllib.error.HTTPError as e:
        if e.code in (403, 429) and e.headers.get("X-RateLimit-Remaining") == "0":
            reset = int(e.headers.get("X-RateLimit-Reset") or 0)
            raise CatalogError("GitHub's hourly limit for anonymous requests is used up; it resets at "
                               f"{time.strftime('%H:%M', time.localtime(reset))}. Set GITHUB_TOKEN to raise it.")
        raise CatalogError(f"The server answered HTTP {e.code}")
    except urllib.error.URLError as e:
        raise CatalogError(f"Could not reach {urllib.parse.urlsplit(url).hostname}: {e.reason}")
    if limit and len(data) > limit:
        raise CatalogError(f"The file is larger than {limit // (1024 * 1024)} MB")
    return data


def _raw(path: str) -> str:
    return f"{RAW}/{REPO}/{BRANCH}/" + urllib.parse.quote(path)


# ---------------------------------------------------------------- 归档：比赛与题目

def _save(f: Path, obj) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    storage.atomic_write(f, json.dumps(obj, ensure_ascii=False).encode())


def ctfs(refresh: bool = False) -> list[dict]:
    """归档里的比赛（缓存一天）。"""
    f = _root() / "ctfs.json"
    if not refresh and f.exists() and time.time() - f.stat().st_mtime < CTF_TTL_S:
        return json.loads(f.read_text(encoding="utf-8"))
    rows = json.loads(_get(f"{API}/repos/{REPO}/contents/ctfs?ref={BRANCH}"))
    out = [{"name": x["name"], "sha": x["sha"]} for x in rows if x.get("type") == "dir"]
    _save(f, out)
    return out


def _ctf_file(ctf: str) -> Path:
    return _root() / "ctf" / (re.sub(r"[^\w.-]", "_", ctf) + ".json")


def challenge_dirs(items: list[dict]) -> dict[str, list[list]]:
    """git tree 条目 → {题目目录: [[相对路径, 大小], ...]}。

    题目目录 = 直接含文件的目录里最靠上的那个；年份 / 类别 / challenges 这类结构目录
    只要底下还有子目录就不算（它们常带一个 README.md）。
    """
    dirs = {x["path"] for x in items if x.get("type") == "tree"}
    parents = {d.rpartition("/")[0] for d in dirs}
    direct: dict[str, list] = {}
    for x in items:
        if x.get("type") == "blob":
            direct.setdefault(x["path"].rpartition("/")[0], []).append(x)

    def structural(d: str) -> bool:
        """年份 / 类别 / 赛段之类的目录；或者底下还有子目录、自己只放了说明文档的目录（一组题的容器，不是一道题）。"""
        name = d.rpartition("/")[2]
        only_docs = all(_DOC.search(x["path"].rpartition("/")[2]) for x in direct.get(d, []))
        return bool(_STRUCT.match(name)) or name.lower() in CATS or (d in parents and only_docs)

    cands = sorted((d for d in direct if d and not (structural(d) and d in parents)), key=lambda d: d.count("/"))
    keep: list[str] = []
    for d in cands:
        if not any(d.startswith(k + "/") for k in keep):
            keep.append(d)
    out: dict[str, list[list]] = {}
    for d in keep:
        out[d] = [[x["path"][len(d) + 1:], int(x.get("size") or 0)] for x in items
                  if x.get("type") == "blob" and x["path"].startswith(d + "/")]
    return out


def challenges(ctf: str, refresh: bool = False) -> list[dict]:
    """一个比赛的所有题（打开时 1 次 API，之后读缓存；识别规则升级时用缓存里的目录树重算）。"""
    f = _ctf_file(ctf)
    old = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    if old and not refresh and old.get("v") == CACHE_V:
        return old["rows"]
    if old and not refresh and old.get("tree"):
        tree = {"tree": old["tree"], "truncated": old.get("truncated", False)}
    else:
        hit = next((c for c in ctfs() if c["name"] == ctf), None)
        if hit is None:
            raise CatalogError(f"No CTF called {ctf} in the archive")
        tree = json.loads(_get(f"{API}/repos/{REPO}/git/trees/{hit['sha']}?recursive=1"))
    items = [{"path": x["path"], "type": x.get("type"), **({"size": x.get("size", 0)} if x.get("type") == "blob" else {})}
             for x in tree.get("tree") or []]
    files = challenge_dirs(items)
    rows = []
    for d, fl in files.items():
        parts = d.split("/")
        year = next((p for p in parts if re.fullmatch(r"(19|20)\d\d", p)), "")
        cat = next((CATS[p.lower()] for p in parts[:-1] if p.lower() in CATS), "")
        rows.append({"path": f"ctfs/{ctf}/{d}", "ctf": ctf, "year": year, "category": cat, "name": parts[-1],
                     "files": len(fl), "bytes": sum(s for _, s in fl),
                     "readme": any(n.lower() == "readme.md" for n, _ in fl)})
    rows.sort(key=lambda r: (r["year"], r["category"], r["name"].lower()), reverse=False)
    _save(f, {"v": CACHE_V, "rows": rows, "files": {f"ctfs/{ctf}/{d}": fl for d, fl in files.items()},
              "tree": items, "truncated": bool(tree.get("truncated")), "at": time.time()})
    return rows


def _cached(ctf: str) -> dict | None:
    f = _ctf_file(ctf)
    if not f.exists():
        return None
    data = json.loads(f.read_text(encoding="utf-8"))
    if data.get("v") != CACHE_V:          # 旧规则算出来的：重算（有目录树就不联网）
        challenges(ctf)
        data = json.loads(f.read_text(encoding="utf-8"))
    return data


def search(q: str, limit: int = 40) -> dict:
    """按词搜：比赛名（全部）+ 已打开过的比赛里的题（名字、年份、类别）。"""
    words = [w for w in re.split(r"\s+", (q or "").lower().strip()) if w]
    names = [c["name"] for c in ctfs()]
    match = (lambda s: all(w in s for w in words)) if words else (lambda s: False)
    hit_ctfs = [n for n in names if match(n.lower())][:12]
    rows = []
    for f in sorted((_root() / "ctf").glob("*.json")) if (_root() / "ctf").exists() else []:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("v") != CACHE_V:
                continue                       # 旧缓存：打开这个比赛时会按新规则重算
            for r in data["rows"]:
                if match(f"{r['ctf']} {r['year']} {r['category']} {r['name']}".lower()):
                    rows.append(r)
        except Exception:
            continue
    opened = len(list((_root() / "ctf").glob("*.json"))) if (_root() / "ctf").exists() else 0
    return {"ctfs": hit_ctfs, "challenges": rows[:limit], "opened": opened, "total_ctfs": len(names)}


# ---------------------------------------------------------------- 预览 / 导入

def _split(rel: str) -> tuple[str, str]:
    return rel.rpartition("/")[0], rel.rpartition("/")[2]


def _plan(files: list[list]) -> tuple[list[list], list[dict], str | None]:
    """哪些文件进附件、哪些不进（为什么）、哪个是 flag.txt。"""
    keep, skip, flag_file = [], [], None
    for rel, size in files:
        d, name = _split(rel)
        if any(_SOLUTION_DIR.match(p) for p in d.split("/") if p) or _SOLUTION.match(name):
            skip.append({"name": rel, "why": "looks like a solution"})
        elif name.lower() == "readme.md" and not d:
            skip.append({"name": rel, "why": "used as the description"})
        elif name.lower() in ("flag.txt", "flag") and not d and size <= 300:
            flag_file = rel
        elif size > custom.MAX_FILE_BYTES:
            skip.append({"name": rel, "why": "larger than 32 MB"})
        else:
            keep.append([rel, size])
    if len(keep) > custom.MAX_FILES:
        for rel, _ in keep[custom.MAX_FILES:]:
            skip.append({"name": rel, "why": f"more than {custom.MAX_FILES} files"})
        keep = keep[:custom.MAX_FILES]
    return keep, skip, flag_file


def gold_from(text: str) -> str:
    """flag.txt 的内容像真 flag 才当 gold（pwn / web 题常附一个本地用的假 flag）。"""
    t = (text or "").strip()
    return t if _FLAG.match(t) and not _FAKE.search(t) else ""


def _archive_entry(path: str) -> tuple[str, list[list]]:
    m = re.match(r"^ctfs/([^/]+)/.+", path or "")
    if not m or ".." in path.split("/"):
        raise CatalogError("Not a challenge in the archive")
    data = _cached(m.group(1))
    if data is None:
        challenges(m.group(1))
        data = _cached(m.group(1)) or {}
    files = (data.get("files") or {}).get(path)
    if files is None:
        raise CatalogError("That challenge is not in the archive index")
    return m.group(1), files


def preview(path: str) -> dict:
    ctf, files = _archive_entry(path)
    keep, skip, flag_file = _plan(files)
    readme = ""
    if any(rel.lower() == "readme.md" for rel, _ in files):
        rel = next(rel for rel, _ in files if rel.lower() == "readme.md")
        readme = _get(_raw(f"{path}/{rel}"), limit=README_MAX, accept="*/*").decode("utf-8", "replace")
    gold = gold_from(_get(_raw(f"{path}/{flag_file}"), limit=400, accept="*/*").decode("utf-8", "replace")) if flag_file else ""
    return {"path": path, "ctf": ctf, "readme": readme[:4000], "files": [{"name": r, "size": s} for r, s in keep],
            "title": readme_title(readme) or path.rstrip("/").rpartition("/")[2],
            "source": f"https://github.com/{REPO}/tree/{BRANCH}/{urllib.parse.quote(path)}",
            "service": is_service(files, readme),
            "skipped": skip + ([{"name": flag_file, "why": "kept out; used as the gold flag" if gold else "kept out; does not look like a real flag"}] if flag_file else []),
            "gold": bool(gold)}


def _unique(names: list[str]) -> list[str]:
    out, seen = [], set()
    for n in names:
        base = n.replace("/", "__")
        cand, i = base, 2
        while cand in seen:
            stem, dot, ext = base.rpartition(".")
            cand = f"{stem}-{i}.{ext}" if dot else f"{base}-{i}"
            i += 1
        seen.add(cand)
        out.append(cand)
    return out


def _create(title: str, query: str, category: str, got: list[tuple[str, bytes]], gold: str, source: str = ""):
    if not got:
        raise CatalogError("Nothing to import: no attachments left after leaving out solutions")
    if sum(len(b) for _, b in got) > custom.MAX_TOTAL_BYTES:
        raise CatalogError("The attachments add up to more than 128 MB")
    return custom.create(title=title[:200], query=query[:6000], files=got, category=category or "custom", gold=gold, source=source)


def import_archive(path: str) -> dict:
    ctf, files = _archive_entry(path)
    keep, skip, flag_file = _plan(files)
    pv = preview(path)
    got = [(n, _get(_raw(f"{path}/{rel}"), limit=custom.MAX_FILE_BYTES, accept="*/*"))
           for n, (rel, _) in zip(_unique([r for r, _ in keep]), keep)]
    gold = gold_from(_get(_raw(f"{path}/{flag_file}"), limit=400, accept="*/*").decode("utf-8", "replace")) if flag_file else ""
    parts = path.split("/")
    year = next((p for p in parts if re.fullmatch(r"(19|20)\d\d", p)), "")
    cat = next((CATS[p.lower()] for p in parts[2:-1] if p.lower() in CATS), "")
    query = (pv["readme"].strip() or f"Challenge {parts[-1]} from {ctf} {year}".strip()) + f"\n\n(Imported from {REPO}: {path})"
    if pv["service"]:
        query += "\n\n" + SERVICE_NOTE
    task = _create(pv["title"], query, cat, got, gold, pv["source"])
    return {"task": task, "gold": bool(gold), "service": pv["service"], "skipped": pv["skipped"], "source": pv["source"]}


_GH_TREE = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+)/tree/([^/]+)/(.+?)/?$")
_GH_BLOB = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+)/blob/([^/]+)/(.+)$")


def import_url(url: str) -> dict:
    """贴链接导入：GitHub 目录 / GitHub 文件 / 任意 http(s) 文件。"""
    url = (url or "").strip()
    check_url(url)
    m = _GH_TREE.match(url)
    if m:
        owner, repo, ref, path = m.groups()
        tree = json.loads(_get(f"{API}/repos/{owner}/{repo}/git/trees/{urllib.parse.quote(ref + ':' + path, safe=':')}?recursive=1"))
        files = [[x["path"], int(x.get("size") or 0)] for x in tree.get("tree") or [] if x.get("type") == "blob"]
        keep, skip, flag_file = _plan(files)
        raw = lambda rel: f"{RAW}/{owner}/{repo}/{ref}/" + urllib.parse.quote(f"{path}/{rel}")    # noqa: E731
        readme = next((rel for rel, _ in files if rel.lower() == "readme.md"), None)
        text = _get(raw(readme), limit=README_MAX, accept="*/*").decode("utf-8", "replace") if readme else ""
        got = [(n, _get(raw(rel), limit=custom.MAX_FILE_BYTES, accept="*/*")) for n, (rel, _) in zip(_unique([r for r, _ in keep]), keep)]
        gold = gold_from(_get(raw(flag_file), limit=400, accept="*/*").decode("utf-8", "replace")) if flag_file else ""
        name = path.rstrip("/").rpartition("/")[2]
        cat = next((CATS[p.lower()] for p in path.split("/")[:-1] if p.lower() in CATS), "")
        service = is_service(files, text)
        task = _create(readme_title(text) or name, (text.strip() or f"Challenge {name}") + f"\n\n(Imported from {url})" + ("\n\n" + SERVICE_NOTE if service else ""), cat, got, gold, url)
        return {"task": task, "gold": bool(gold), "service": service, "skipped": skip, "source": url}
    m = _GH_BLOB.match(url)
    if m:
        owner, repo, ref, path = m.groups()
        url = f"{RAW}/{owner}/{repo}/{ref}/{path}"
    name = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rpartition("/")[2]) or "download.bin"
    if _SOLUTION.match(name):
        raise CatalogError("That file looks like a solution, not a challenge")
    data = _get(url, limit=custom.MAX_FILE_BYTES, accept="*/*")
    task = _create(name, f"Challenge file {name}.\n\n(Imported from {url})", "", [(name, data)], "", url)
    return {"task": task, "gold": False, "skipped": [], "source": url}
