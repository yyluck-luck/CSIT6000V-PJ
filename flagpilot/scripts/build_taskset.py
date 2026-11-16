#!/usr/bin/env python3
"""Build an evaluation-ready CTF task set from InterCode-CTF.

Usage:
    python3 scripts/build_taskset.py --src /path/to/intercode/data/ctf --out .

What it does:
  1. keeps only Forensics / Cryptography / General Skills
  2. drops every task whose description needs a live network service
  3. drops the per-task `solution/` directory (reference answers)
  4. copies assets into tasks/<category>-<id>/ and writes task.json
  5. writes index/tasks.jsonl (gold replaced by sha256) and index/manifest.csv
  6. writes _private/gold_private.json (plaintext gold - NEVER ship this to the agent)
"""

import argparse
import csv
import hashlib
import json
import re
import shutil
from pathlib import Path

KEEP = ("Forensics", "Cryptography", "General Skills")
CODE = {"Forensics": "forensics", "Cryptography": "crypto", "General Skills": "general"}

# General Skills mixes encoding/file work with plain coding drills (fix a syntax
# error, read a help flag). Only the former belongs to our two-category scope.
CORE_GENERAL = {4, 6, 11, 17, 18, 19, 21, 22, 23, 24, 25, 37, 38, 40, 41, 60}

# A task needs the network if the prompt or setup asks for nc/ssh/curl/wget, an
# external URL, or phrases like "Connect to host port" (the phrasing that made
# task 41 slip through the first version of this filter).
NET = re.compile(
    r"\bnc\s+\S+\s+\d+|\bconnect to\s+\S+|\bssh\b|\bsocat\b|\bnetcat\b"
    r"|\bcurl\s+http|\bwget\s+http|https?://(?!play\.picoctf)"
    r"|challenges\.picoctf|\.picoctf\.(?:org|net)|\bport\s+\d{3,5}",
    re.I,
)

# Manually excluded after review (kept here so a rebuild stays reproducible).
MANUAL_DROP = {
    41: "requires a live service (Connect to jupiter.challenges.picoctf.org 14291)",
}

TOOL_RULES = [
    ((".pcap", ".pcapng"), "tshark"),
    ((".img", ".dd", ".raw", ".e01"), "sleuthkit"),
    ((".pptm", ".pptx", ".docx", ".xlsx"), "binwalk + unzip"),
    ((".eml",), "text/header inspection"),
    ((".zip", ".tar", ".gz", ".7z"), "binwalk + unzip"),
    ((".png", ".jpg", ".jpeg", ".bmp"), "exiftool + zsteg"),
    ((".py", ".c", ".java", ".js"), "python/strings"),
]


def guess_tools(query: str, names: list[str]) -> list[str]:
    q = query.lower()
    tools = []
    if "rsa" in q or "public-key" in q or "exponent" in q or "modul" in q:
        tools += ["pycryptodome", "gmpy2"]
    if "sleuthkit" in q or "mmls" in q or "srch_strings" in q:
        tools.append("sleuthkit")
    for suffix, tool in TOOL_RULES:
        if any(n.lower().endswith(suffix) for n in names):
            tools.append(tool)
    if not tools:
        tools.append("strings/grep")
    return sorted(set(tools))


def sha256(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="path to intercode/data/ctf")
    ap.add_argument("--out", default=".", help="output root (default: cwd)")
    ap.add_argument("--keep-solutions", action="store_true",
                    help="also copy each task's solution/ walkthrough (team-only version)")
    args = ap.parse_args()

    src = Path(args.src)
    out = Path(args.out)
    tasks_dir = out / "tasks"
    index_dir = out / "index"
    private_dir = out / "_private"
    for d in (tasks_dir, index_dir, private_dir):
        d.mkdir(parents=True, exist_ok=True)

    data = json.loads((src / "ic_ctf.json").read_text())

    kept, dropped_net, dropped_cat = [], [], []
    gold_private = {}
    manifest = []

    for t in data:
        tid = t["task_id"]
        cats = t.get("tags", [])
        cat = cats[0] if cats else "?"
        query = t.get("query", "")
        setup = t.get("setup", "")

        if cat not in KEEP:
            dropped_cat.append((tid, cat))
            continue
        if NET.search(query) or NET.search(setup) or tid in MANUAL_DROP:
            dropped_net.append((tid, cat))
            continue

        asset_src = src / "task_assets" / str(tid)
        if not asset_src.exists():
            print(f"[warn] missing assets for {tid}, skipped")
            continue

        name = f"{CODE[cat]}-{tid:03d}"
        dst = tasks_dir / name
        if dst.exists():
            shutil.rmtree(dst)
        dst.mkdir(parents=True)

        files, size = [], 0
        for item in sorted(asset_src.iterdir()):
            if item.name == "solution":
                if not args.keep_solutions:
                    continue                      # reference walkthrough - never ship
                shutil.copytree(item, dst / "solution")
                continue
            if item.is_dir():
                shutil.copytree(item, dst / item.name)
                for sub in (dst / item.name).rglob("*"):
                    if sub.is_file():
                        size += sub.stat().st_size
                        files.append(str(sub.relative_to(dst)))
            else:
                shutil.copy2(item, dst / item.name)
                size += item.stat().st_size
                files.append(item.name)

        gold = t["gold"].strip()
        gold_private[str(tid)] = gold

        pico_id = ""
        m = re.search(r"/challenge/(\d+)", t.get("source", ""))
        if m:
            pico_id = m.group(1)

        # Upstream metadata is not perfectly clean: a few entries point at a
        # third-party writeup instead of the picoCTF challenge page. Flag them
        # instead of silently "fixing" the URL.
        source = t.get("source", "")
        source_note = None
        if "picoctf" not in source:
            source_note = ("upstream InterCode metadata links a third-party writeup, "
                           "not the original picoCTF page")

        record = {
            "task_id": tid,
            "name": name,
            "category": cat,
            "title": query[:90].replace("\n", " "),
            "query": query,
            "source": t.get("source", ""),
            "source_note": source_note,
            "pico_challenge_id": pico_id,
            "gold_sha256": sha256(gold),
            "assets": files,
            "assets_dir": f"tasks/{name}",
            "suggested_tools": guess_tools(query, files),
            "requires_network": False,
            "scope": "core" if (cat != "General Skills" or tid in CORE_GENERAL) else "extra-gs",
            "solution_dir": "solution" if (dst / "solution").exists() else None,
        }
        (dst / "task.json").write_text(json.dumps(record, indent=2, ensure_ascii=False))
        kept.append(record)
        manifest.append(
            [name, cat, tid, pico_id, ";".join(files), f"{size/1024:.1f}",
             record["gold_sha256"], record["source"]]
        )

    kept.sort(key=lambda r: (list(KEEP).index(r["category"]), r["task_id"]))

    with (index_dir / "tasks.jsonl").open("w") as f:
        for r in kept:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    with (index_dir / "manifest.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "category", "task_id", "pico_challenge_id",
                    "assets", "size_kb", "gold_sha256", "source"])
        w.writerows(manifest)

    (private_dir / "gold_private.json").write_text(
        json.dumps(gold_private, indent=2, ensure_ascii=False)
    )

    # human-readable index
    order = {c: i for i, c in enumerate(KEEP)}
    rows = sorted(kept, key=lambda r: (order[r["category"]], r["task_id"]))
    n_core = sum(1 for r in rows if r["scope"] == "core")
    md = [
        f"# 题目清单（{len(rows)} 题，`scope=core` {n_core} 题）",
        "",
        "| 目录 | 类别 | scope | 附件 | 建议工具 | 题解 | 题面摘要 | 来源 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        assets = "<br>".join(r["assets"]) if r["assets"] else "—"
        md.append("| `{}` | {} | {} | {} | {} | {} | {} | picoCTF #{} |".format(
            r["name"], r["category"], r["scope"], assets,
            ", ".join(r["suggested_tools"]),
            "✅" if r["solution_dir"] else "—",
            r["title"].replace("|", "\\|"), r["pico_challenge_id"]))
    (index_dir / "tasks.md").write_text("\n".join(md) + "\n")

    # ship the verifier so each version is self-contained.
    # (It was renamed verify.py -> verify_taskset.py; copying the old name made every
    #  fresh rebuild crash here with exit 1 after all tasks had already been written.)
    scripts_out = out / "scripts"
    scripts_out.mkdir(exist_ok=True)
    here = Path(__file__).resolve().parent
    shutil.copy2(here / "verify_taskset.py", scripts_out / "verify_taskset.py")

    print(f"kept {len(kept)} tasks")
    for cat in KEEP:
        print(f"  {cat:18s} {sum(1 for r in kept if r['category'] == cat)}")
    print(f"dropped (network): {len(dropped_net)} -> {[i for i, _ in dropped_net]}")
    print(f"dropped (category): {len(dropped_cat)}")
    n_sol = sum(1 for r in kept if r["solution_dir"])
    print(f"solutions kept: {n_sol} tasks" if args.keep_solutions
          else "solutions kept: 0 tasks (stripped)")
    print(f"gold written to _private/gold_private.json ({len(gold_private)} entries)")


if __name__ == "__main__":
    main()
