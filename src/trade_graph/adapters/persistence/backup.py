"""Consistent SQLite backups. A truncated copy is rejected."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path


def backup_database(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(source)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(destination.suffix + ".sha256").write_text(digest + "\n", encoding="utf-8")
    return digest


def restore_database(backup: Path, destination: Path) -> None:
    digest_path = backup.with_suffix(backup.suffix + ".sha256")
    expected = digest_path.read_text(encoding="utf-8").strip()
    actual = hashlib.sha256(backup.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError("backup checksum mismatch")
    src = sqlite3.connect(backup)
    try:
        src.execute("PRAGMA integrity_check").fetchone()
    finally:
        src.close()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    raw = sqlite3.connect(backup)
    restored = sqlite3.connect(destination)
    try:
        raw.backup(restored)
    finally:
        restored.close()
        raw.close()
