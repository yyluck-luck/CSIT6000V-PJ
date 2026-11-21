"""Look for API keys where they must never be: files git tracks, files about to be added, the whole git history
(every branch, commit messages included) and any extra folders you name.

shared.env at the repository root is the one place keys are kept on purpose (the private repository ships the
owner's keys for one-click reproduction); it is skipped, in the files and in the history. A key anywhere else counts.

The keys come from the repository's .env (every *_KEY / *_TOKEN / *_SECRET / *_PASSWORD value of 12+ characters)
plus a few well-known key shapes (Google, OpenAI-style sk-, GitHub, AWS, private-key blocks). Only variable names,
key shapes and places are printed, never a value.

  python3 scripts/scan_secrets.py                    # tracked + to-be-added files and all history
  python3 scripts/scan_secrets.py --paths runs ~/x   # also these folders (logs, run records ...)
  python3 scripts/scan_secrets.py --text-file f      # also a text dump (e.g. a pull request body)

Exit status 1 when anything is found, so it can guard a commit or a push.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)$", re.I)
SHAPES = {
    "google-api-key": re.compile(rb"AIza[0-9A-Za-z_\-]{35}"),
    "sk-style-key": re.compile(rb"\bsk-[A-Za-z0-9_\-]{20,}"),
    "github-token": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{36,}"),
    "aws-access-key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    "private-key-block": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}
MAX_FILE = 20 * 1024 * 1024
ALLOWED = {"shared.env"}           # paths relative to the repository root


def repo_root() -> Path:
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                         cwd=Path(__file__).resolve().parent)
    return Path(out.stdout.strip() or Path(__file__).resolve().parents[2])


def load_secrets(dotenv: Path) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    sources = []
    if dotenv.is_file():
        sources += dotenv.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in sources:
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip().strip('"').strip("'")
        if SECRET_NAME.search(name) and len(value) >= 12:
            found[name] = value.encode()
    for name, value in os.environ.items():           # keys exported in the shell count too
        if SECRET_NAME.search(name) and len(value) >= 12 and name not in found:
            found[name] = value.encode()
    return found


def hits_in(data: bytes, secrets: dict[str, bytes]) -> list[str]:
    out = [name for name, value in secrets.items() if value in data]
    known = set(secrets.values())
    for shape, rx in SHAPES.items():
        for m in rx.finditer(data):
            if m.group(0) not in known and not any(m.group(0) in v or v in m.group(0) for v in known):
                out.append("shape:" + shape)
                break
    return out


def scan_files(paths: list[Path], secrets: dict[str, bytes], label: str, report: list[str], root: Path | None = None) -> int:
    n = 0
    for p in paths:
        if root is not None and p.resolve().relative_to(root.resolve()).as_posix() in ALLOWED:
            continue
        try:
            if not p.is_file() or p.stat().st_size > MAX_FILE:
                continue
            data = p.read_bytes()
        except OSError:
            continue
        n += 1
        for h in hits_in(data, secrets):
            report.append(f"{label}: {p} -> {h}")
    return n


def git_files(root: Path) -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                         capture_output=True, cwd=root)
    return [root / f.decode() for f in out.stdout.split(b"\0") if f]


def scan_history(root: Path, secrets: dict[str, bytes], report: list[str]) -> int:
    proc = subprocess.Popen(["git", "log", "--all", "-p", "--text", "--no-color", "--format=@@commit %H%n%B"],
                            stdout=subprocess.PIPE, cwd=root)
    commit, n, flagged, skip = "", 0, set(), False
    buf: list[bytes] = []

    def flush() -> None:
        if commit and buf:
            for h in hits_in(b"".join(buf), secrets):
                if (commit, h) not in flagged:
                    flagged.add((commit, h))
                    report.append(f"history: commit {commit[:12]} -> {h}")

    assert proc.stdout is not None
    for line in proc.stdout:
        if line.startswith(b"@@commit "):
            flush()
            commit, buf, skip = line.split()[1].decode(), [], False
            n += 1
        elif line.startswith(b"diff --git "):
            path = line.rstrip().split(b" b/", 1)[-1].decode("utf-8", "replace")
            skip = path in ALLOWED                     # the sanctioned keys file: its own lines are not a leak
            buf.append(line)
        elif not skip:
            buf.append(line)
    flush()
    proc.wait()
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--paths", nargs="*", default=[], help="extra folders or files to scan")
    ap.add_argument("--text-file", action="append", default=[], help="extra text dumps to scan")
    ap.add_argument("--no-history", action="store_true")
    args = ap.parse_args(argv)
    root = repo_root()
    secrets = load_secrets(root / ".env")
    report: list[str] = []
    print(f"keys checked: {', '.join(sorted(secrets)) or '(none in .env)'} + {len(SHAPES)} key shapes")
    n = scan_files(git_files(root), secrets, "repo", report, root=root)
    print(f"repo files scanned: {n}")
    if not args.no_history:
        print(f"commits scanned: {scan_history(root, secrets, report)}")
    for extra in args.paths:
        base = Path(extra).expanduser()
        files = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file()]
        print(f"{extra}: {scan_files(files, secrets, 'extra', report)} files scanned")
    for t in args.text_file:
        scan_files([Path(t)], secrets, "text", report)
    if report:
        print("\nFOUND (names and places only):")
        for line in report:
            print("  " + line)
        return 1
    print("\nno keys found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
