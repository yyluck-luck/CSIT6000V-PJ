"""Import help: draft a challenge's title, statement and category from what was imported.

The model sees the current title, statement and the names and sizes of the attachments, never the answer. It is told not to
guess the flag, and anything flag-shaped in its draft is removed before it is shown. Without a model (mock) or when the call
fails, a rule-based draft is returned instead, marked as such. Nothing is saved here: the page shows the draft in the editor
and the person saves what they want.
"""

from __future__ import annotations

import json
import re

CATS = ("crypto", "forensics", "reverse", "web", "pwn", "misc")
_FLAGISH = re.compile(r"[A-Za-z0-9_]{2,20}\{([^}\n]{1,200})\}")
_FORMAT_ONLY = re.compile(r"^[.…x*_\- ]*$", re.I)              # picoCTF{...} as a format hint is fine
_FROM_CAT = {"cryptography": "crypto", "crypto": "crypto", "forensics": "forensics", "reverse engineering": "reverse",
             "reverse": "reverse", "rev": "reverse", "web exploitation": "web", "web": "web", "binary exploitation": "pwn",
             "pwn": "pwn", "general skills": "misc", "misc": "misc"}
_BY_EXT = [(re.compile(r"\.(pcap(?:ng)?|cap|img|dd|e01|mem|raw|wav|png|jpe?g|bmp|gif|pdf|docx?)$", re.I), "forensics"),
           (re.compile(r"\.(php|js|html?|jsx?|tsx?)$", re.I), "web"), (re.compile(r"\.(so|elf|exe|dll|bin)$", re.I), "reverse")]


def files_of(task) -> list[tuple[str, int]]:
    from flagpilot import config
    out = []
    for p in task.asset_paths(config.TASKS_ROOT):
        try:
            out.append((p.name, p.stat().st_size))
        except OSError:
            continue
    return out


def scrub(text: str) -> str:
    """Remove anything flag-shaped (keep format hints such as picoCTF{...})."""
    return _FLAGISH.sub(lambda m: m.group(0) if _FORMAT_ONLY.match(m.group(1)) else "[flag removed]", str(text or ""))


def guess_category(task, files) -> str:
    c = _FROM_CAT.get((task.category or "").strip().lower())
    if c and c != "misc":          # misc / general (the upload default) says little: the files may say more
        return c
    for name, _ in files:
        for rx, cat in _BY_EXT:
            if rx.search(name):
                return cat
    text = f"{task.title}\n{task.query}".lower()
    if re.search(r"\b(rsa|cipher|aes|xor|modulus|decrypt)", text):
        return "crypto"
    if getattr(task, "service", False) or re.search(r"\b(heap|overflow|rop|shellcode|libc)\b", text):
        return "pwn"
    return "misc"


def _kb(n: int) -> str:
    return f"{n} B" if n < 1024 else f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1048576:.1f} MB"


def rules_draft(task) -> dict:
    files = files_of(task)
    body = re.sub(r"\n*\(Imported from [^)\n]+\)\s*", "\n", task.query or "").strip()
    body = body.replace("Note: in the original CTF this challenge ran as a network service.", "").strip()
    first = re.split(r"\n\s*\n", re.sub(r"^#+\s*.*\n", "", body).strip() or body)[0].strip()
    service = bool(getattr(task, "service", False))
    lines = [first or f"Challenge {task.title}."]
    if files:
        lines.append("Files: " + ", ".join(f"`{n}` ({_kb(s)})" for n, s in files[:12]) + ".")
    if service:
        lines.append("In the original CTF this ran as a network service; its real flag is not in these files, so it cannot "
                     "be solved offline.")
    return {"title": (task.title or "").strip()[:60] or task.name, "statement": scrub("\n\n".join(lines))[:4000],
            "category": guess_category(task, files), "offline": "no" if service else "unclear",
            "why": "the real flag was only on the original server" if service else "a rule-based draft cannot tell",
            "by": "rules"}


def messages(task) -> list[dict]:
    files = files_of(task)
    listing = "\n".join(f"- {n} ({_kb(s)})" for n, s in files[:40]) or "(no attachments)"
    return [{"role": "system", "content": (
        "You help set up CTF challenges for a solver that works offline in a sandbox (no network, no remote services). "
        "Rewrite the challenge so a solver knows what it is, which files matter and what to find. Never guess, invent "
        "or reveal a flag; if the original text contains one, leave it out. Reply with one JSON object only.")},
        {"role": "user", "content": (
            f"Title: {task.title}\nCategory given: {task.category}\nOriginally a network service: {'yes' if getattr(task, 'service', False) else 'no'}\n\n"
            f"Statement as imported:\n{(task.query or '')[:5000]}\n\nAttachments:\n{listing}\n\n"
            'Return JSON: {"title": "<= 60 characters", "statement": "clear Markdown, <= 1200 characters: what it is, '
            'which files to look at and how, the flag format if known, and what cannot be done offline", '
            f'"category": one of {list(CATS)}, "offline": "yes" | "no" | "unclear", "why": "one sentence"}}')}]


def parse(text: str) -> dict | None:
    t = str(text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    m = re.search(r"\{[\s\S]*\}", t)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict) or not str(d.get("statement") or "").strip():
        return None
    cat = str(d.get("category") or "").strip().lower()
    off = str(d.get("offline") or "").strip().lower()
    return {"title": scrub(" ".join(str(d.get("title") or "").split()))[:60], "statement": scrub(str(d["statement"]).strip())[:4000],
            "category": cat if cat in CATS else "misc", "offline": off if off in ("yes", "no", "unclear") else "unclear",
            "why": scrub(" ".join(str(d.get("why") or "").split()))[:200]}


def draft(task, client=None, label: str = "") -> dict:
    """A draft from the model when there is one; the rule-based draft otherwise (and when the model fails)."""
    if client is None:
        return rules_draft(task)
    try:
        comp = client.complete(messages(task), max_tokens=1200)
    except Exception as e:                           # a draft is a convenience: never let it break the page
        return {**rules_draft(task), "note": f"the model call failed ({type(e).__name__}); this is a rule-based draft"}
    d = None if getattr(comp, "error", "") else parse(getattr(comp, "text", ""))
    if d is None:
        why = getattr(comp, "error", "") or "the reply was not usable"
        return {**rules_draft(task), "note": f"{why[:120]}; this is a rule-based draft"}
    if not d["title"]:
        d["title"] = (task.title or task.name)[:60]
    if getattr(task, "service", False):          # 模型常说"能离线利用"，可真 flag 只在原比赛的服务器上
        d["offline"], d["why"] = "no", "it ran as a network service: its real flag was only on that server"
    return {**d, "by": label or "model"}
