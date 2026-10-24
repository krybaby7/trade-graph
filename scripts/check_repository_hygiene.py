#!/usr/bin/env python3
"""Scan tracked or staged bytes before publishing source or CI artifacts."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

_CREDENTIALS = [
    ("provider credential", re.compile(rb"\bsk-(?:proj-|ant-api\d+-)?[A-Za-z0-9_-]{36,}\b")),
    ("GitHub credential", re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})\b")),
    ("cloud access key", re.compile(rb"\bAKIA[A-Z0-9]{16}\b")),
    ("private key", re.compile(rb"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
]


def inspect_bytes(path: str, payload: bytes) -> list[tuple[str, int, str]]:
    violations = []
    for category, pattern in _CREDENTIALS:
        for match in pattern.finditer(payload):
            violations.append((path, payload[:match.start()].count(b"\n") + 1, category))
    return violations


def _git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args])


def _private_file(path: str) -> bool:
    parts = Path(path).parts
    name = Path(path).name
    return (
        any(part in {"runtime", ".private", ".venv", "__pycache__", "node_modules"} for part in parts)
        or (name.startswith(".env") and name != ".env.example")
        or name in {"id_rsa", "id_ed25519", "credentials.json", "owner-session.json"}
        or name.endswith((".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".pem", ".key"))
    )


def scan(root: Path, *, staged: bool = False) -> list[tuple[str, int, str]]:
    """Only path, line and category leave this function, never a matched value."""
    args = ("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z") if staged else ("ls-files", "-z")
    paths = [path.decode("utf-8", "surrogateescape") for path in _git(root, *args).split(b"\0") if path]
    violations = []
    for path in paths:
        if _private_file(path):
            violations.append((path, 0, "private runtime or credential file"))
            continue
        try:
            payload = _git(root, "show", f":{path}") if staged else (root / path).read_bytes()
        except (OSError, subprocess.CalledProcessError):
            violations.append((path, 0, "unreadable tracked file"))
            continue
        violations.extend(inspect_bytes(path, payload))
    return violations


def scan_artifacts(paths: list[Path]) -> list[tuple[str, int, str]]:
    """Scan the exact test/evidence files selected for upload, never whole runtime directories."""
    violations = []
    for path in paths:
        label = path.name
        if path.is_symlink() or _private_file(label):
            violations.append((label, 0, "private runtime or credential artifact"))
            continue
        try:
            payload = path.read_bytes()
        except OSError:
            violations.append((label, 0, "unreadable artifact"))
            continue
        if payload.startswith(b"SQLite format 3\x00"):
            violations.append((label, 0, "private SQLite runtime artifact"))
        violations.extend(inspect_bytes(label, payload))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="Scan index bytes instead of working files")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--artifact", type=Path, action="append", default=[],
                        help="Also scan an exact generated test/evidence file before upload (repeatable)")
    args = parser.parse_args()
    violations = scan(args.root, staged=args.staged) + scan_artifacts(args.artifact)
    for path, line, category in violations:
        print(f"{path}:{line}: {category}")
    if violations:
        return 1
    print("Repository hygiene passed: no private runtime files or recognized credentials.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
