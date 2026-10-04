"""Installed, credential-free entry point for retained private operations diagnostics."""

from __future__ import annotations

import argparse
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from trade_graph.application.operations import _fsync_directory, _private_directory
from trade_graph.application.operations_evidence import (
    HostObservationCollector,
    VerifiedHostObservation,
    _small_file,
)


@dataclass(frozen=True)
class PreflightResult:
    exit_code: int
    summary: dict


def _key(path: Path, *, create: bool) -> bytes:
    if create:
        _private_directory(path.parent)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(os.urandom(32))
                handle.flush()
                os.fsync(handle.fileno())
            _fsync_directory(path.parent)
    if path.parent.is_symlink() or path.parent.stat().st_mode & 0o077:
        raise ValueError("observation key requires private directory storage")
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("observation key requires a private regular file")
    payload = _small_file(path, 64)
    if path.stat(follow_symlinks=False) != info:
        raise ValueError("observation key changed during read")
    if not 32 <= len(payload) <= 64:
        raise ValueError("observation key requires 32..64 bytes")
    return payload


def _summary(observation: VerifiedHostObservation, *, backup_restore: bool, public_data: bool) -> PreflightResult:
    document = observation.document
    checks = {name: value["status"] for name, value in document["checks"].items()}
    required = ["storage", "funded_setup"]
    if backup_restore:
        required.append("backup_restore")
    if public_data:
        required.append("public_data")
    refused = any(checks[name] in {"refused", "unavailable"} for name in required)
    pending = any(checks[name] == "pending" for name in required)
    return PreflightResult(
        2 if refused else 3 if pending else 0,
        {
            "status": "recorded",
            "evidence_sha256": observation.sha256,
            "checks": checks,
            "funded_setup_missing": document["checks"]["funded_setup"]["facts"].get("missing", []),
            "paid_calls": False,
            "private_venue_calls": False,
            "live_enabled": False,
            "funded_acceptance_verified": False,
        },
    )


def capture_preflight(
    database_path: Path | str | None,
    evidence_path: Path | str,
    key_path: Path | str,
    *,
    config_path: Path | str | None = None,
    backup_restore: bool = False,
    public_data: bool = False,
) -> PreflightResult:
    """Retain/verify local facts; this never authorizes model spending or service writes."""
    path, key = Path(evidence_path), Path(key_path)
    sources = [Path(item).resolve() for item in (database_path, config_path) if item is not None]
    if path.resolve() == key.resolve() or any(item.resolve() in sources for item in (path, key)):
        raise ValueError("evidence and observation key must use separate new paths")
    if path.exists() or path.is_symlink():
        raise ValueError("operations evidence already exists; use a new path")
    collector = HostObservationCollector(database_path, signing_key=_key(key, create=True), config_path=config_path)
    retained = collector.capture(path, backup_restore=backup_restore, public_data=public_data)
    return _summary(collector.verify(path, retained.sha256), backup_restore=backup_restore, public_data=public_data)


def verify_preflight(
    database_path: Path | str | None,
    evidence_path: Path | str,
    key_path: Path | str,
    expected_sha256: str,
    *,
    config_path: Path | str | None = None,
) -> PreflightResult:
    collector = HostObservationCollector(database_path, signing_key=_key(Path(key_path), create=False),
                                         config_path=config_path)
    observation = collector.verify(evidence_path, expected_sha256)
    checks = observation.document["checks"]
    return _summary(observation, backup_restore=checks["backup_restore"]["status"] != "pending",
                    public_data=checks["public_data"]["status"] != "pending")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    capture = subcommands.add_parser("capture")
    verify = subcommands.add_parser("verify")
    for command in (capture, verify):
        command.add_argument("--database", type=Path)
        command.add_argument("--config", type=Path)
        command.add_argument("--report", type=Path, required=True)
        command.add_argument("--key", type=Path, required=True)
    capture.add_argument("--backup-restore", action="store_true")
    capture.add_argument("--public-data", action="store_true")
    verify.add_argument("--sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "capture":
            result = capture_preflight(args.database, args.report, args.key, config_path=args.config,
                                       backup_restore=args.backup_restore, public_data=args.public_data)
        else:
            result = verify_preflight(args.database, args.report, args.key, args.sha256, config_path=args.config)
        print(json.dumps(result.summary, sort_keys=True))
        return result.exit_code
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"status": "refused", "failure_type": type(exc).__name__}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
