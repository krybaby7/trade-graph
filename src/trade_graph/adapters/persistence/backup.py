"""Consistent SQLite backups. A truncated copy is rejected."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from contextlib import closing
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
    """Restore to an offline destination; callers must close all target users.

    Sidecars are refused, never deleted: they may hold newer financial state.
    Their absence does not prove that no process has the destination open.
    """
    digest_path = backup.with_suffix(backup.suffix + ".sha256")
    expected = digest_path.read_text(encoding="utf-8").strip()
    payload = backup.read_bytes()
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise ValueError("backup checksum mismatch")
    _require_no_sidecars(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Stage the exact checksummed bytes so validation and copy cannot read
    # different revisions of the supplied backup. All staging stays private
    # and on the destination filesystem for an atomic final rename.
    with tempfile.TemporaryDirectory(
        prefix=".restore-", dir=destination.parent, ignore_cleanup_errors=True,
    ) as directory:
        verified = Path(directory) / "verified.sqlite"
        verified.touch(mode=0o600)
        verified.write_bytes(payload)
        staged = Path(directory) / "restored.sqlite"
        staged.touch(mode=0o600)
        uri = verified.resolve().as_uri() + "?mode=ro&immutable=1"
        with closing(sqlite3.connect(uri, uri=True)) as source:
            _require_integrity(source)
            with closing(sqlite3.connect(staged)) as restored:
                source.backup(restored)
                # A complete main file is required; do not publish a WAL whose
                # sidecars would remain under the temporary filename.
                if restored.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
                    raise ValueError("restored database could not leave WAL mode")
                _require_integrity(restored)
        with staged.open("rb") as completed:
            os.fsync(completed.fileno())
        _require_no_sidecars(destination)
        os.replace(staged, destination)


def _require_integrity(connection: sqlite3.Connection) -> None:
    try:
        result = connection.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.DatabaseError as exc:
        raise ValueError("backup integrity check failed") from exc
    if result != [("ok",)]:
        raise ValueError("backup integrity check failed")


def _require_no_sidecars(destination: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(destination) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            raise ValueError("restore requires an offline destination without SQLite sidecars")
