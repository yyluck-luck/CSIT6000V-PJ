"""Authenticated encryption for persisted records and generated artifacts.

Set FLAGPILOT_ENCRYPTION_KEY_FILE to enable Fernet. Live events stay usable in
memory; readers decrypt persisted records transparently. Legacy JSON is readable.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

MAGIC = b"FLAGPILOT-FERNET-V1\n"
MARKER = "_flagpilot_encrypted"


class StorageError(RuntimeError):
    """An encrypted record cannot be accessed safely."""


def enabled() -> bool:
    return bool(os.environ.get("FLAGPILOT_ENCRYPTION_KEY_FILE"))


def _cipher():
    name = os.environ.get("FLAGPILOT_ENCRYPTION_KEY_FILE")
    if not name:
        raise StorageError("Set FLAGPILOT_ENCRYPTION_KEY_FILE to read encrypted data.")
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise StorageError("Install requirements-encryption.txt to use encrypted storage.") from exc
    try:
        return Fernet(Path(name).expanduser().read_bytes().strip())
    except (OSError, ValueError) as exc:
        raise StorageError("Cannot load the configured encryption key file.") from exc


def _decrypt(token: bytes) -> bytes:
    cipher = _cipher()
    from cryptography.fernet import InvalidToken
    try:
        return cipher.decrypt(token)
    except (InvalidToken, ValueError, TypeError) as exc:
        raise StorageError("Encrypted data could not be authenticated with this key.") from exc


def encode_record(record: dict) -> str:
    raw = json.dumps(record, ensure_ascii=False, default=str)
    if not enabled():
        return raw
    token = _cipher().encrypt(b"record\n" + raw.encode()).decode("ascii")
    return json.dumps({MARKER: "fernet-v1", "token": token})


def decode_record(line: str) -> dict:
    record = json.loads(line)
    if isinstance(record, dict) and MARKER in record:
        if record[MARKER] != "fernet-v1":
            raise StorageError("Unsupported encrypted record version.")
        try:
            raw = _decrypt(record["token"].encode("ascii"))
        except (KeyError, AttributeError, UnicodeError) as exc:
            raise StorageError("Invalid encrypted record envelope.") from exc
        if not raw.startswith(b"record\n"):
            raise StorageError("Unexpected encrypted record payload.")
        return json.loads(raw[7:])
    return record


def read_records(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                rows.append(decode_record(line))
            except json.JSONDecodeError:
                continue
    return rows


def atomic_write(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".fp-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def _check_existing_mode(path: Path) -> None:
    path = Path(path)
    if path.exists():
        with path.open(encoding="utf-8") as stream:
            first = next((line for line in stream if line.strip()), "")
        if first:
            try:
                value = json.loads(first)
            except json.JSONDecodeError:
                return
            if isinstance(value, dict) and MARKER in value:
                decode_record(first)


def append_record(path: Path, record: dict) -> None:
    _check_existing_mode(path)
    line = encode_record(record) + "\n"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(line)


def write_records(path: Path, rows) -> None:
    _check_existing_mode(path)
    atomic_write(path, "".join(encode_record(row) + "\n" for row in rows).encode())


def artifact_path(path: Path) -> Path:
    path = Path(path)
    return path.with_name(path.name + ".enc") if enabled() else path


def write_artifact(path: Path, content: bytes) -> Path:
    path = Path(path)
    encrypted = path.with_name(path.name + ".enc")
    if enabled():
        if encrypted.exists():
            read_artifact(path)
        target = encrypted
        data = MAGIC + _cipher().encrypt(b"artifact\n" + content)
        obsolete = path
    else:
        if encrypted.exists():
            raise StorageError("An encrypted artifact already exists; configure its key first.")
        target, data, obsolete = path, content, None
    atomic_write(target, data)
    if obsolete and obsolete.exists():
        obsolete.unlink()
    return target


def read_artifact(path: Path) -> bytes:
    path = Path(path)
    encrypted = path.with_name(path.name + ".enc")
    if encrypted.exists():
        data = encrypted.read_bytes()
        if not data.startswith(MAGIC):
            raise StorageError("Invalid encrypted artifact header.")
        raw = _decrypt(data[len(MAGIC):])
        if not raw.startswith(b"artifact\n"):
            raise StorageError("Unexpected encrypted artifact payload.")
        return raw[9:]
    return path.read_bytes()


def seal_directory(directory: Path) -> None:
    """Protect completed attempt scratch files; active tool work is plaintext."""
    directory = Path(directory)
    if not enabled() or not directory.exists():
        return
    _cipher()
    if directory.is_symlink():
        raise StorageError("Refusing to seal a workspace directory symlink.")
    # Stage all ciphertext before removing plaintext, including colliding names
    # such as answer and answer.enc. Interrupted staging retains the originals.
    pending = []
    for parent, dirs, files in os.walk(directory, followlinks=False):
        base = Path(parent)
        base.chmod(0o700)
        for name in list(dirs):
            item = base / name
            if item.is_symlink():
                item.unlink()
                dirs.remove(name)
        for name in files:
            item = base / name
            if item.is_symlink():
                item.unlink()
                continue
            data = item.read_bytes()
            if data.startswith(MAGIC):
                if not _decrypt(data[len(MAGIC):]).startswith(b"artifact\n"):
                    raise StorageError("Unexpected sealed workspace payload.")
            else:
                pending.append(item)
    if not pending:
        return
    staging = Path(tempfile.mkdtemp(prefix=".fp-seal-", dir=directory.parent))
    prepared = []
    for number, source in enumerate(pending):
        temp = staging / str(number)
        atomic_write(temp, MAGIC + _cipher().encrypt(b"artifact\n" + source.read_bytes()))
        prepared.append((source, temp, source.with_name(source.name + ".enc")))
    for source, _, _ in prepared:
        source.unlink()
    for _, temp, target in prepared:
        os.replace(temp, target)
    staging.rmdir()


def generate_key(path: Path) -> None:
    from cryptography.fernet import Fernet
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(Fernet.generate_key() + b"\n")


def protect_history(root: Path) -> int:
    """Explicitly migrate saved records and completed attempt files in place."""
    if not enabled():
        raise StorageError("Configure FLAGPILOT_ENCRYPTION_KEY_FILE before migrating history.")
    _cipher()
    root = Path(root).resolve()
    count = 0
    for path in sorted(root.rglob("*.jsonl")):
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            continue
        if path.name not in {"trace.jsonl", "runs.jsonl", "probe.jsonl", "chat.jsonl"} and "memory" not in path.relative_to(root).parts:
            continue
        # Migration refuses malformed lines rather than dropping historical data.
        rows = [decode_record(line) for line in path.read_text().splitlines() if line.strip()]
        write_records(path, rows)
        count += 1
    for trace in root.rglob("trace.jsonl"):
        if trace.is_symlink() or not trace.resolve().is_relative_to(root):
            continue
        events = read_records(trace)
        if not any(e.get("type") in ("summary", "done") for e in events):
            continue
        for name in ("work", "replay"):
            seal_directory(trace.parent / name)
        report = trace.parent / "writeup.md"
        if report.is_file() and not report.is_symlink():
            write_artifact(report, report.read_bytes())
    return count
