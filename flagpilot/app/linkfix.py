"""Pasted challenge links that are a little wrong still import (2026-11-21).

The import box takes a GitHub folder or file, a raw file link, or a direct file link. People paste what they have:
"github.com/..." without https, a link with ?tab=readme-ov-file or #readme, a branch that is master when the repository
uses main, a path in the wrong case or with a typo, the repository's front page, one file of a challenge, a folder that
holds a whole CTF, a picoCTF practice page, or only the challenge's name. resolve() turns each of these into something
importable, cheapest step first, and checks every guess against GitHub before using it:

  1. tidy the text: scheme, host, query, fragment, ".git", trailing punctuation;
  2. GitHub: the branch (one that does not exist becomes the default branch), the path (exact, then case, then
     punctuation, then name, then similarity), and what the path is (a challenge folder; a file, whose challenge
     folder is imported; a folder of challenges, from which the user picks);
  3. a web page or plain words go to the challenge lists: this copy's own task set (a picoCTF practice link matches its
     challenge number exactly) and the online archive;
  4. the configured model, for a path or a page that is still unresolved: it is shown the request and real candidates,
     and its answer counts only if it is one of them (or, for words, if the archive then finds a match).

Every change is reported back as a note ("branch master does not exist; used main"), and the page shows them.
"""

from __future__ import annotations

import difflib
import json
import re
import time
import urllib.parse
import urllib.request
from typing import Callable

from app import catalog
from flagpilot import config

Ask = Callable[[str], str]
_CACHE: dict[str, tuple[float, object]] = {}
CACHE_S = 600
MAX_CHOICES = 40


class Choose(Exception):
    """Several challenges fit: the page lists them and the user picks one."""

    def __init__(self, why: str, choices: list[dict], notes: list[str]):
        super().__init__(why)
        self.why, self.choices, self.notes = why, choices[:MAX_CHOICES], notes


# ---------------------------------------------------------------- 1. tidy

def tidy(text: str) -> tuple[str, list[str]]:
    """(link, notes). The link is "" when the text is not a link at all (then it is searched for as words)."""
    notes: list[str] = []
    t, prev = (text or "").strip(), None
    while t != prev:                                  # "<link>." or "(link)": peel brackets, quotes and punctuation
        prev = t
        t = re.sub(r"[)\].,;:!?]+$", "", t.strip().strip("<>\"'`\u201c\u201d\u2018\u2019(").strip())
    if not t or re.search(r"\s", t):
        return "", notes
    if not re.match(r"^https?://", t, re.I):
        if not re.match(r"^(www\.)?[\w-]+(\.[\w-]+)+/\S*$", t, re.I):
            return "", notes
        t = "https://" + t
        notes.append("added https://")
    u = urllib.parse.urlsplit(t)
    host = (u.hostname or "").lower()
    if host in ("www.github.com", "m.github.com"):
        notes.append(f"used github.com instead of {host}")
        host = "github.com"
    if host not in ("github.com", "raw.githubusercontent.com"):
        return t, notes
    if u.scheme.lower() != "https":
        notes.append("used https")
    if u.query or u.fragment:
        notes.append("dropped " + " and ".join(x for x in (("?" + u.query) if u.query else "", ("#" + u.fragment) if u.fragment else "") if x))
    segs = [s for s in u.path.split("/") if s]
    if host == "github.com" and len(segs) >= 2 and segs[1].lower().endswith(".git"):
        segs[1] = segs[1][:-4]
        notes.append("dropped .git")
    return f"https://{host}/" + "/".join(segs), notes


def parse_github(url: str) -> dict | None:
    """{"owner", "repo", "kind": root | tree | blob, "ref", "rest": [path segments]} or None (not a GitHub repository link)."""
    u = urllib.parse.urlsplit(url)
    segs = [urllib.parse.unquote(s) for s in u.path.split("/") if s]
    if u.hostname == "raw.githubusercontent.com":
        return {"owner": segs[0], "repo": segs[1], "kind": "blob", "ref": segs[2], "rest": segs[3:]} if len(segs) >= 4 else None
    if u.hostname != "github.com" or len(segs) < 2 or segs[0] in ("orgs", "users", "settings", "search", "topics", "marketplace"):
        return None
    owner, repo, rest = segs[0], segs[1], segs[2:]
    if rest[:1] == ["releases"] and "download" in rest:
        return None                                   # a release asset: a direct file
    if len(rest) >= 2 and rest[0] in ("tree", "blob", "raw", "edit", "blame", "commits", "history"):
        return {"owner": owner, "repo": repo, "kind": "tree" if rest[0] in ("tree", "commits", "history") else "blob",
                "ref": rest[1], "rest": rest[2:]}
    if len(rest) >= 2 and rest[0] == "commit":
        return {"owner": owner, "repo": repo, "kind": "tree", "ref": rest[1], "rest": []}
    return {"owner": owner, "repo": repo, "kind": "root", "ref": "", "rest": []}


# ---------------------------------------------------------------- GitHub, cached

def _api(path: str):
    hit = _CACHE.get(path)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    v = json.loads(catalog._get(catalog.API + path))
    _CACHE[path] = (time.time(), v)
    return v


def _missing(e: Exception) -> bool:
    return getattr(e, "code", 0) in (404, 409, 422)


def _tree(owner: str, repo: str, ref: str) -> dict:
    return _api(f"/repos/{owner}/{repo}/git/trees/{urllib.parse.quote(ref, safe='')}?recursive=1")


def _items(tree: dict) -> list[dict]:
    return [{"path": x["path"], "type": x.get("type"), "size": int(x.get("size") or 0)} for x in tree.get("tree") or []
            if x.get("type") in ("tree", "blob")]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _tree_url(owner: str, repo: str, ref: str, path: str) -> str:
    return f"https://github.com/{owner}/{repo}/tree/{ref}" + (f"/{path}" if path else "")


# ---------------------------------------------------------------- 2. GitHub: branch, path, what it is

def _branch(owner: str, repo: str, ref: str, rest: list[str], default: str, notes: list[str]) -> tuple[str, list[str], dict]:
    """The branch and the remaining path. A branch name may contain "/" (feature/x), so longer prefixes are tried
    before giving up on it; a branch that does not exist becomes the default branch, with a note."""
    if not ref:
        return default, rest, _tree(owner, repo, default)
    for k in range(0, min(3, len(rest)) + 1):
        cand = "/".join([ref] + rest[:k])
        try:
            return cand, rest[k:], _tree(owner, repo, cand)
        except catalog.CatalogError as e:
            if not _missing(e):
                raise
    notes.append(f"branch {ref} does not exist; used {default}")
    return default, rest, _tree(owner, repo, default)


def _ask_pick(ask: Ask | None, request: str, pool: list[str], what: str) -> str:
    """The model picks one of pool for request; anything not in pool is ignored."""
    if ask is None or not pool:
        return ""
    prompt = (f"A user pasted a link to import a CTF challenge, but {what} does not exist: {request!r}.\n"
              "These are the real paths. Reply with exactly one of them, the one the user most likely meant, "
              "and nothing else. Reply NONE if none fits.\n\n" + "\n".join(pool[:120]))
    try:
        got = (ask(prompt) or "").strip().strip("`'\" ").splitlines()[0].strip() if ask else ""
    except Exception:
        return ""
    return got if got in pool else ""


def _locate(path: str, items: list[dict], notes: list[str], ask: Ask | None, where: str) -> tuple[str, str]:
    """(kind, path) of the file or folder the user meant: exact, case, punctuation, its name, similarity, the model."""
    dirs = {x["path"] for x in items if x["type"] == "tree"}
    files = {x["path"] for x in items if x["type"] == "blob"}
    kind = lambda p: "tree" if p in dirs else "blob"           # noqa: E731
    if not path or path in dirs or path in files:
        return ("tree" if not path else kind(path)), path
    every = sorted(dirs | files)
    low = {}
    for p in every:
        low.setdefault(p.lower(), []).append(p)
    if len(low.get(path.lower(), [])) == 1:
        p = low[path.lower()][0]
        notes.append(f"path {path} is {p} (letter case)")
        return kind(p), p
    want = "/".join(_norm(s) for s in path.split("/"))
    same = [p for p in every if "/".join(_norm(s) for s in p.split("/")) == want]
    if len(same) == 1:
        notes.append(f"path {path} is {same[0]}")
        return kind(same[0]), same[0]
    leaf = _norm(path.rsplit("/", 1)[-1])
    named = [p for p in sorted(dirs) if _norm(p.rsplit("/", 1)[-1]) == leaf] or \
            [p for p in sorted(files) if _norm(p.rsplit("/", 1)[-1]) == leaf]
    if len(named) == 1:
        notes.append(f"found {path.rsplit('/', 1)[-1]} at {named[0]}")
        return kind(named[0]), named[0]
    if len(named) > 1:
        raise Choose(f"Several folders in {where} are called {path.rsplit('/', 1)[-1]}; pick one",
                     [{"label": p, "path": p} for p in named], notes)
    lows = [p.lower() for p in every]
    close = difflib.get_close_matches(path.lower(), lows, n=5, cutoff=0.6)
    if close:
        scored = sorted(((difflib.SequenceMatcher(None, path.lower(), c).ratio(), c) for c in close), reverse=True)
        best = next(p for p in every if p.lower() == scored[0][1])
        if len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.08:
            notes.append(f"path {path} is not there; the closest is {best}")
            return kind(best), best
        picked = _ask_pick(ask, path, [next(p for p in every if p.lower() == c) for _, c in scored], f"the path in {where}")
        if picked:
            notes.append(f"path {path} is not there; the model picked {picked}")
            return kind(picked), picked
        raise Choose(f"{path} is not in {where}; these are the closest", [{"label": next(p for p in every if p.lower() == c), "path": next(p for p in every if p.lower() == c)} for _, c in scored], notes)
    picked = _ask_pick(ask, path, sorted(dirs) or every, f"the path in {where}")
    if picked:
        notes.append(f"path {path} is not there; the model picked {picked}")
        return kind(picked), picked
    raise catalog.CatalogError(f"There is no file or folder like {path} in {where}")


def _under(items: list[dict], d: str) -> list[dict]:
    """Items below folder d, with paths relative to it."""
    if not d:
        return items
    n = len(d) + 1
    return [{**x, "path": x["path"][n:]} for x in items if x["path"].startswith(d + "/")]


def _is_challenge(sub: list[dict]) -> bool:
    """A folder is one challenge when it holds files of its own that are not just notes."""
    own = [x for x in sub if x["type"] == "blob" and "/" not in x["path"]]
    return any(not catalog._DOC.search(x["path"]) for x in own)


def _what(kind: str, path: str, items: list[dict], notes: list[str], where: str) -> dict:
    """The challenge folder to import (or a single file), or Choose when the folder holds several challenges."""
    if kind == "blob":
        parent, name = path.rpartition("/")[0], path.rpartition("/")[2]
        sub = _under(items, parent)
        blobs = [x for x in sub if x["type"] == "blob"]
        if (parent and _is_challenge(sub) and len(blobs) <= catalog.custom.MAX_FILES) or \
                (not parent and len(blobs) <= 25 and not catalog.challenge_dirs(sub)):
            notes.append(f"imported the whole challenge folder {parent or 'at the top of the repository'}, which holds {name}")
            return {"dir": parent, "files": [[x["path"], x["size"]] for x in blobs]}
        return {"file": path}
    sub = _under(items, path)
    if _is_challenge(sub):
        return {"dir": path, "files": [[x["path"], x["size"]] for x in sub if x["type"] == "blob"]}
    inner = catalog.challenge_dirs(sub)
    if len(inner) == 1:
        d, fl = next(iter(inner.items()))
        full = f"{path}/{d}" if path else d
        notes.append(f"the folder holds one challenge: {full}")
        return {"dir": full, "files": fl}
    if inner:
        raise Choose(f"{path or where} holds {len(inner)} challenges; pick one",
                     [{"label": f"{path}/{d}" if path else d, "path": f"{path}/{d}" if path else d, "files": len(fl)}
                      for d, fl in sorted(inner.items())], notes)
    raise catalog.CatalogError(f"{path or where} holds no challenge files")


def resolve_github(g: dict, notes: list[str], ask: Ask | None = None) -> dict:
    owner, repo = g["owner"], g["repo"]
    try:
        info = _api(f"/repos/{owner}/{repo}")
    except catalog.CatalogError as e:
        if _missing(e):
            raise catalog.CatalogError(f"There is no public GitHub repository {owner}/{repo}")
        raise
    full = str(info.get("full_name") or f"{owner}/{repo}")
    if full != f"{owner}/{repo}" and "/" in full:
        notes.append(f"repository {owner}/{repo} is {full}")
        owner, repo = full.split("/", 1)
    default = str(info.get("default_branch") or "main")
    if g["kind"] == "root" and g["ref"] == "":
        if g.get("rest"):
            notes.append("used the repository itself")
    ref, rest, tree = _branch(owner, repo, g["ref"], list(g["rest"]), default, notes)
    items = _items(tree)
    if tree.get("truncated"):
        notes.append("the repository is very large; only part of its file list could be read")
    where = f"{owner}/{repo} ({ref})"
    files = {x["path"] for x in items if x["type"] == "blob"}
    try:
        kind, path = _locate("/".join(rest), items, notes, ask, where)
        out = _what(kind, path, items, notes, where)
    except Choose as c:                               # the page imports a choice by its exact link
        for ch in c.choices:
            p = ch.pop("path", None)
            if p is not None:
                ch["url"] = (f"https://github.com/{owner}/{repo}/blob/{ref}/{p}" if p in files else _tree_url(owner, repo, ref, p))
        raise
    out.update(owner=owner, repo=repo, ref=ref)
    return out


# ---------------------------------------------------------------- 3. pages and words: the challenge lists

_PICO = re.compile(r"play\.picoctf\.org/practice/challenge/(\d+)", re.I)


def _local_rows() -> list[dict]:
    try:
        return [json.loads(l) for l in (config.TASKS_ROOT / "index" / "tasks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    except (OSError, ValueError):
        return []


def local_match(text: str) -> dict | None:
    """A challenge of this copy's own task set: a picoCTF practice link matches its challenge number exactly."""
    m = _PICO.search(text or "")
    if not m:
        return None
    for r in _local_rows():
        src = str(r.get("source") or "")
        if str(r.get("pico_challenge_id") or "") == m.group(1) or re.search(rf"/challenge/{m.group(1)}(\D|$)", src):
            return {"task": r["name"], "title": r.get("title") or r["name"]}
    return None


def _words(text: str) -> list[str]:
    raw = re.sub(r"https?://[^/\s]+", " ", text or "")
    return [w for w in re.split(r"[\s/_\-.?=&#%+]+", raw.lower()) if len(w) >= 2 and not w.isdigit()
            and w not in ("www", "com", "org", "html", "htm", "php", "index", "practice", "challenge", "challenges", "ctf",
                          "tree", "blob", "main", "master", "writeup", "writeups", "the", "and")]


def archive_match(words: list[str], notes: list[str]) -> dict:
    """Search the online archive: a CTF named by the words is opened (one GitHub request), then its challenges are
    matched by name. Returns {"path"} for one clear match; raises Choose for several, CatalogError for none."""
    if not words:
        raise catalog.CatalogError("Paste a link to the challenge, or type its name")
    names = [c["name"] for c in catalog.ctfs()]
    ctf = next((n for n in sorted(names, key=len, reverse=True) if _norm(n) and _norm(n) in "".join(_norm(w) for w in words)), "")
    rest = [w for w in words if _norm(w) not in _norm(ctf)] if ctf else words
    rows = catalog.challenges(ctf) if ctf else catalog.search(" ".join(words)).get("challenges") or []
    year = next((w for w in rest if re.fullmatch(r"(19|20)\d\d", w)), "")
    want = [w for w in rest if w != year]
    hits = [r for r in rows if (not year or r.get("year") == year)
            and want and all(_norm(w) in _norm(f"{r['name']} {r.get('category', '')}") for w in want)]
    exact = [r for r in hits if _norm(r["name"]) == _norm(" ".join(want))]
    if len(exact) == 1 or len(hits) == 1:
        r = (exact or hits)[0]
        notes.append(f"found {r['name']} ({r['ctf']} {r.get('year', '')}) in the online archive".replace(" )", ")"))
        return {"path": r["path"]}
    if hits:
        raise Choose(f"{len(hits)} challenges in the archive match; pick one",
                     [{"label": f"{r['name']} · {r['ctf']} {r.get('year', '')} {r.get('category', '')}".strip(), "archive": r["path"],
                       "files": r.get("files")} for r in hits], notes)
    raise catalog.CatalogError("No challenge in the online archive matches " + repr(" ".join(words)) +
                               ("" if ctf else ": add the CTF's name, e.g. 'picoCTF 2019 caesar'"))


def _page(url: str) -> tuple[str, str]:
    """(content type, first 64 KB as text) of a non-GitHub link, without downloading a big file in full."""
    catalog.check_url(url)
    opener = urllib.request.build_opener(catalog._NoPrivateRedirects)
    try:
        with opener.open(urllib.request.Request(url, headers={"User-Agent": catalog.UA, "Accept": "*/*"}), timeout=30) as r:
            return (r.headers.get("Content-Type") or "").lower(), r.read(65536).decode("utf-8", "replace")
    except Exception as e:                            # noqa: BLE001 - say why in plain words
        raise catalog.CatalogError(f"Could not open {urllib.parse.urlsplit(url).hostname}: {getattr(e, 'reason', None) or e}")


def _ask_words(ask: Ask | None, text: str, title: str) -> list[str]:
    if ask is None:
        return []
    prompt = ("A user wants to import a CTF challenge and pasted this. Name the challenge as 'CTF year challenge-name' "
              "using words that would appear in an archive of past CTFs, or reply NONE if you cannot tell.\n"
              f"Pasted: {text[:300]}\nPage title: {title[:200]}")
    try:
        got = (ask(prompt) or "").strip().splitlines()[0]
    except Exception:
        return []
    return [] if not got or got.upper().startswith("NONE") else _words(got)


# ---------------------------------------------------------------- 4. all of it

def resolve(text: str, ask: Ask | None = None) -> dict:
    """What to import for this pasted text. One of
      {"kind": "github", "owner", "repo", "ref", "dir", "files"} | {"kind": "github_file", "owner", "repo", "ref", "file"}
      {"kind": "file", "url"} | {"kind": "archive", "path"} | {"kind": "local", "task", "title"}
    each with "notes"; raises Choose (several fit) or catalog.CatalogError (none does), both in plain words."""
    url, notes = tidy(text)
    if not url:                                       # words: the challenge lists
        hit = local_match(text)
        if hit:
            return {"kind": "local", **hit, "notes": notes}
        return {"kind": "archive", **archive_match(_words(text), notes), "notes": notes}
    hit = local_match(url)
    if hit:
        notes.append("this picoCTF challenge is already in your challenge list")
        return {"kind": "local", **hit, "notes": notes}
    if _PICO.search(url):
        raise catalog.CatalogError("That picoCTF challenge is not in this copy's task set, and picoCTF's site does not "
                                   "offer its files without a login. Search the archive for it by name instead")
    g = parse_github(url)
    if g and g["owner"] == "sajjadium" and g["repo"].lower() == "ctf-archives" and g["rest"][:1] == ["ctfs"]:
        return {"kind": "archive", **_archive_path("/".join(g["rest"]), notes, ask), "notes": notes}
    if g:
        out = resolve_github(g, notes, ask)
        if "file" in out:
            return {"kind": "github_file", "owner": out["owner"], "repo": out["repo"], "ref": out["ref"], "file": out["file"], "notes": notes}
        return {"kind": "github", **out, "notes": notes}
    ctype, head = _page(url)
    if "html" not in ctype and not re.match(r"\s*<(!doctype html|html)", head, re.I):
        return {"kind": "file", "url": url, "notes": notes}
    title = re.sub(r"\s+", " ", (re.search(r"<title[^>]*>(.*?)</title>", head, re.I | re.S) or [None, ""])[1]).strip()
    words = _words(urllib.parse.urlsplit(url).path) + _words(title)
    try:
        return {"kind": "archive", **archive_match(words, notes), "notes": notes + ["the link is a web page; matched it by its words"]}
    except catalog.CatalogError:
        more = _ask_words(ask, url, title)
        if more:
            notes.append("the link is a web page; the model named the challenge: " + " ".join(more))
            return {"kind": "archive", **archive_match(more, notes), "notes": notes}
        raise catalog.CatalogError("That link is a web page, not a challenge file, and no challenge in the archive matches it. "
                                   "Paste the challenge's GitHub folder or a direct file link, or search the archive by name")


def _archive_path(path: str, notes: list[str], ask: Ask | None) -> dict:
    """A path inside sajjadium/ctf-archives: corrected against the CTF's challenge list (the repository is too big
    for one file list)."""
    m = re.match(r"^ctfs/([^/]+)(?:/(.*))?$", path)
    if not m:
        raise catalog.CatalogError("Not a challenge in the archive")
    names = {c["name"].lower(): c["name"] for c in catalog.ctfs()}
    ctf = names.get(m.group(1).lower())
    if ctf is None:
        close = difflib.get_close_matches(m.group(1).lower(), list(names), n=1, cutoff=0.75)
        if not close:
            raise catalog.CatalogError(f"No CTF called {m.group(1)} in the archive")
        ctf = names[close[0]]
        notes.append(f"CTF {m.group(1)} is {ctf}")
    elif ctf != m.group(1):
        notes.append(f"CTF {m.group(1)} is {ctf} (letter case)")
    rows = catalog.challenges(ctf)
    paths = [r["path"] for r in rows]
    want = f"ctfs/{ctf}/{m.group(2) or ''}".rstrip("/")
    if want in paths:
        return {"path": want}
    inside = [p for p in paths if p.startswith(want + "/")]
    if len(inside) == 1:
        notes.append(f"the folder holds one challenge: {inside[0]}")
        return {"path": inside[0]}
    if inside:
        raise Choose(f"{want} holds {len(inside)} challenges; pick one",
                     [{"label": p.split("/", 2)[2], "archive": p} for p in inside], notes)
    owner = next((p for p in paths if want.startswith(p + "/")), "")
    if owner:
        notes.append(f"imported the whole challenge folder {owner}")
        return {"path": owner}
    kind, p = _locate(want, [{"path": x, "type": "tree", "size": 0} for x in paths], notes, ask, f"the archive's {ctf}")
    return {"path": p}
