"""Private operator diagnostics, financial snapshots and offline restore checks.

Diagnostics never initialize or migrate a database and never probe credentials.
An idle WAL database is read without creating SQLite sidecars. A running WAL
database uses its existing shared-memory read coordination and one read snapshot.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import date
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

from trade_graph.adapters.models.providers import lookup_capabilities
from trade_graph.adapters.persistence.backup import restore_database
from trade_graph.adapters.persistence.migrate import STATEMENTS, applied_versions
from trade_graph.api import financial
from trade_graph.api.health import health
from trade_graph.api.security import redact
from trade_graph.application.artifact_store import ArtifactStore
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import PriceCard
from trade_graph.domain.clock import Clock, FrozenClock, SystemClock, parse_utc, utc_iso
from trade_graph.domain.errors import TradeGraphError

ROLES = ("research", "trader", "learning", "optimisation", "leader", "engineer")


class _ReadDatabase:
    """The projections only need execute and nested read transactions."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.connection.execute(sql, params)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        yield self.connection

    @contextmanager
    def snapshot(self) -> Iterator[sqlite3.Connection]:
        if self.connection.in_transaction:
            yield self.connection
            return
        self.connection.execute("BEGIN")
        try:
            yield self.connection
        finally:
            self.connection.execute("ROLLBACK")


def _identity(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


@contextmanager
def _read_database(path: Path | str) -> Iterator[_ReadDatabase]:
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError("database does not exist; initialize a paper account first")
    wal = Path(str(path) + "-wal")
    shm = Path(str(path) + "-shm")
    journal = Path(str(path) + "-journal")
    if journal.exists() or (wal.exists() and not shm.exists()):
        raise ValueError("SQLite recovery is pending; recover the database before reporting")
    idle = not wal.exists() and not shm.exists()
    before = _identity(path)
    uri = path.as_uri() + ("?mode=ro&immutable=1" if idle else "?mode=ro")
    try:
        with closing(sqlite3.connect(uri, uri=True, isolation_level=None, timeout=5)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            database = _ReadDatabase(connection)
            with database.snapshot():
                yield database
            if idle and (before != _identity(path) or wal.exists() or shm.exists() or journal.exists()):
                raise ValueError("database changed during the idle read; retry diagnostics")
    except sqlite3.DatabaseError as exc:
        raise ValueError("database could not be read safely; inspect integrity and schema") from exc


def _schema(database: _ReadDatabase) -> dict:
    applied = applied_versions(database.connection)
    required = {version for version, _ in STATEMENTS}
    missing, unknown = required - applied, applied - required
    return {
        "status": "current" if not missing and not unknown else "unsupported",
        "applied": sorted(applied), "required": sorted(required),
        "missing": sorted(missing), "unknown": sorted(unknown),
    }


def _runtime(database: _ReadDatabase, clock: Clock, portfolio_id: str | None):
    if portfolio_id is None:
        row = database.execute(
            "SELECT portfolio_id, mode FROM portfolios WHERE mode = 'paper' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
    else:
        row = database.execute(
            "SELECT portfolio_id, mode FROM portfolios WHERE portfolio_id = ?", (portfolio_id,),
        ).fetchone()
    if row is None or row["mode"] != "paper":
        raise ValueError("a persisted paper portfolio is required")
    deployments = database.execute("SELECT deployment_id FROM deployment_budget").fetchall()
    deployment = deployments[0]["deployment_id"] if len(deployments) == 1 else "deployment"
    return SimpleNamespace(
        database=database, clock=clock, ledger=Ledger(database, clock),
        portfolio_id=row["portfolio_id"], deployment_id=deployment, paid_calls_enabled=False,
    )


def _all_pages(projection, runtime, field: str, *, reservation_field: str | None = None) -> dict:
    result = projection(runtime, limit=200)
    for offset in range(200, result["pagination"]["total"], 200):
        page = projection(runtime, limit=200, offset=offset)
        result[field].extend(page[field])
    result["pagination"] = {"total": len(result[field]), "complete": True}
    if reservation_field:
        total = result["reservation_pagination"]["total"]
        for offset in range(200, total, 200):
            page = projection(runtime, limit=200, offset=offset)
            result[reservation_field].extend(page[reservation_field])
        result["reservation_pagination"] = {"total": len(result[reservation_field]), "complete": True}
    return result


def financial_report(
    database_path: Path | str, *, portfolio_id: str | None = None, clock: Clock | None = None,
) -> dict:
    """Return complete, redacted financial projections in one authoritative snapshot."""
    clock = FrozenClock((clock or SystemClock()).now())
    with _read_database(database_path) as database:
        schema = _schema(database)
        if schema["status"] != "current":
            raise ValueError("database schema is unsupported; an operating upgrade is required")
        runtime = _runtime(database, clock, portfolio_id)
        result = {
            "schema_version": 1, "read_only": True, "mode": "paper", "live_enabled": False,
            "paid_calls_enabled": False, "as_of": utc_iso(clock.now()), "portfolio_id": runtime.portfolio_id,
            "overview": financial.overview(runtime),
            "costs": _all_pages(financial.costs, runtime, "receipts", reservation_field="reservations"),
            "positions": _all_pages(financial.positions, runtime, "positions"),
            "orders": _all_pages(financial.orders, runtime, "orders"),
            "health": health(runtime, authenticated=True),
            "verification": {
                "public_data_probe": "pending", "credentialed_providers": "pending",
                "funded_paper_soak": "pending", "economic_evidence": "insufficient_evidence",
                "basis": "local persisted records; no network or credential probe performed",
            },
        }
        return redact(result)


def _configuration(config_path: Path | str | None, runtime) -> dict:
    if config_path is None:
        return {"status": "unconfigured", "routing": {"status": "pending", "roles": []}}
    try:
        from trade_graph.application.runtime_models import RuntimeModelConfig
        from trade_graph.paper_runtime import load_runtime_config

        wrapper = load_runtime_config(Path(config_path))
        if wrapper.models is None:
            return {"status": "unconfigured", "public_data_configured": wrapper.public_data_enabled,
                    "routing": {"status": "pending", "roles": []}}
        config = RuntimeModelConfig.model_validate(wrapper.models)
        paid = config.paid_calls_enabled
        routes = config.role_routes
        approved = config.approved_price_card_ids
        runtime.deployment_id = config.deployment_id
        database = runtime.database
        cards = {}
        for row in database.execute("SELECT price_card_id, document_json FROM price_cards"):
            cards[row["price_card_id"]] = PriceCard.model_validate_json(row["document_json"])
        active = database.execute("SELECT artifact_hash FROM active_versions WHERE portfolio_id = ?",
                                  (runtime.portfolio_id,)).fetchone()
        overrides = {}
        if active:
            bundle = ArtifactStore(database, runtime.clock).get(active["artifact_hash"])
            text = bundle["files"].get("artifacts/model_routing.json")
            if text:
                overrides = json.loads(text)["routes"]
        roles = []
        for role in ROLES:
            card_id = overrides.get(role, routes.get(role))
            card = cards.get(card_id)
            caps = lookup_capabilities(card.provider, card.model) if card else None
            ready = bool(card_id and card_id in approved and card and caps and caps.structured_output)
            if card and card.provider != "scripted":
                ready = ready and bool(card.verified_at and card.source_id and card.tier == "standard")
            age = None
            if card and card.provider != "scripted":
                age = (runtime.clock.now().date() - date.fromisoformat(card.verified_at[:10])).days
                effective = date.fromisoformat(card.effective_at[:10])
                ready = ready and 0 <= age <= 30 and effective <= runtime.clock.now().date()
            roles.append({
                "role": role, "ready": ready, "price_card_id": card_id,
                "provider": None if card is None else card.provider,
                "model": None if card is None else card.model,
                "source": "active artifact" if role in overrides else "owner runtime configuration",
                "credential_probe": "pending" if card and card.provider != "scripted" else "not_required",
                "price_verification_age_days": age, "maximum_price_verification_age_days": 30,
                "reason": None if ready else "an approved persisted capable price card and role route are required",
            })
        return {
            "status": "valid", "paid_calls_configured": paid, "credentials_inspected": False,
            "public_data_configured": wrapper.public_data_enabled, "deployment_id": config.deployment_id,
            "routing": {"status": "ready" if all(role["ready"] for role in roles) else "pending", "roles": roles},
        }
    except (OSError, ValueError, TypeError, KeyError, TradeGraphError):
        return {"status": "invalid", "error": "configuration could not be validated safely"}


def doctor_report(
    database_path: Path | str, *, portfolio_id: str | None = None,
    config_path: Path | str | None = None, clock: Clock | None = None,
) -> dict:
    """Inspect persisted readiness without creating files, migrating or spending."""
    clock = FrozenClock((clock or SystemClock()).now())
    path = Path(database_path)
    result = {
        "schema_version": 1, "as_of": utc_iso(clock.now()), "read_only": True,
        "mode": "paper", "paid_calls_enabled": False, "live_enabled": False,
        "status": "error", "database": {"present": path.is_file()},
        "public_data_probe": {"status": "pending", "performed": False},
        "credentialed_providers": {"status": "pending", "performed": False},
        "funded_paper_soak": {"status": "pending"},
    }
    try:
        with _read_database(path) as database:
            integrity = database.execute("PRAGMA integrity_check").fetchall()
            if [row[0] for row in integrity] != ["ok"]:
                raise ValueError("database integrity check failed")
            schema = _schema(database)
            result["database"].update({"integrity": "ok", "schema": schema, "storage": {
                "private_directory": not bool(path.resolve().parent.stat().st_mode & 0o077),
                "private_file": not bool(path.stat().st_mode & 0o077),
                "available_bytes": shutil.disk_usage(path.parent).free,
            }})
            if schema["status"] != "current":
                raise ValueError("database schema is unsupported; an operating upgrade is required")
            runtime = _runtime(database, clock, portfolio_id)
            result["portfolio_id"] = runtime.portfolio_id
            result["configuration"] = _configuration(config_path, runtime)
            result["health"] = health(runtime, authenticated=True)
            result["costs"] = financial.costs(runtime)
            observation = database.execute(
                "SELECT COUNT(*) AS count, MAX(event_time) AS newest FROM observations "
                "WHERE available_at <= ? AND event_time <= ?",
                (utc_iso(clock.now()), utc_iso(clock.now())),
            ).fetchone()
            policy_row = database.execute(
                "SELECT document_json FROM owner_policy_revisions ORDER BY created_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
            quote_age = json.loads(policy_row["document_json"])["maximum_quote_age_seconds"] if policy_row else 30
            age = (None if observation["newest"] is None
                   else int((clock.now() - parse_utc(observation["newest"])).total_seconds()))
            result["market"] = {"stored_observations": observation["count"],
                                "latest_event_at": observation["newest"], "age_seconds": age,
                                "maximum_age_seconds": quote_age, "stale": age is None or age > quote_age,
                                "network_probe": "pending"}
            result["instruments"] = {"count": database.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]}
            result["task_states"] = {
                row["status"]: row["count"] for row in database.execute(
                    "SELECT status, COUNT(*) AS count FROM tasks WHERE portfolio_id = ? GROUP BY status",
                    (runtime.portfolio_id,),
                )
            }
            warnings = list(result["health"]["degraded_reasons"])
            if not result["database"]["storage"]["private_directory"]:
                warnings.append("database directory is not private")
            if not result["database"]["storage"]["private_file"]:
                warnings.append("database file is not private")
            if result["configuration"]["status"] == "invalid":
                warnings.append("invalid runtime configuration")
            if result["configuration"].get("routing", {}).get("status") != "ready":
                warnings.append("model routing is pending")
            if observation["count"] == 0:
                warnings.append("no persisted market observations")
            elif result["market"]["stale"]:
                warnings.append("persisted market observations are stale")
            result["warnings"] = warnings
            result["status"] = "degraded" if warnings else "ok"
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        result["status"] = "error"
        result["error"] = "database diagnostics unavailable; check file, integrity, schema and paper portfolio"
    return redact(result)


def _private_directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("private storage directory must not be a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_mode & 0o077:
        raise ValueError("private storage directory requires mode 0700")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def private_backup(
    database_path: Path | str, destination: Path | str, *, maximum_bytes: int | None = None,
    wall_seconds: float | None = None,
) -> str:
    """Publish a consistent private backup and checksum without overwriting history."""
    if maximum_bytes is not None and (type(maximum_bytes) is not int or maximum_bytes <= 0):
        raise ValueError("positive backup byte limit required")
    if wall_seconds is not None and (isinstance(wall_seconds, bool) or not isinstance(wall_seconds, (int, float))
                                     or not math.isfinite(wall_seconds) or wall_seconds <= 0):
        raise ValueError("positive finite backup deadline required")
    deadline = None if wall_seconds is None else monotonic() + wall_seconds
    source, destination = Path(database_path), Path(destination)
    checksum = destination.with_suffix(destination.suffix + ".sha256")
    if not source.is_file() or source.is_symlink():
        raise ValueError("backup source must be an existing regular database")
    if source.resolve() == destination.resolve():
        raise ValueError("backup destination must differ from the source")
    _private_directory(destination.parent)
    if destination.exists() or destination.is_symlink() or checksum.exists() or checksum.is_symlink():
        raise ValueError("backup destination and checksum must be new files")
    published = False
    checksum_created = False
    try:
        with tempfile.TemporaryDirectory(prefix=".backup-", dir=destination.parent) as directory:
            staged = Path(directory) / "snapshot.sqlite"
            staged.touch(mode=0o600)
            with _read_database(source) as database, closing(sqlite3.connect(staged)) as target:
                page_size = database.execute("PRAGMA page_size").fetchone()[0]

                def progress(status, remaining, total):
                    if deadline is not None and monotonic() >= deadline:
                        raise ValueError("backup copy deadline exceeded")
                    if maximum_bytes is not None and total * page_size > maximum_bytes:
                        raise ValueError("backup exceeded its byte limit")

                progress(0, 0, database.execute("PRAGMA page_count").fetchone()[0])
                database.connection.backup(target, pages=128, progress=progress, sleep=0.01)
                if deadline is not None:
                    target.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
                target.execute("PRAGMA journal_mode=DELETE")
                if target.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise ValueError("backup integrity check failed")
            if maximum_bytes is not None and staged.stat().st_size > maximum_bytes:
                raise ValueError("backup exceeded its byte limit")
            digest = hashlib.sha256(staged.read_bytes()).hexdigest()
            with staged.open("rb") as completed:
                os.fsync(completed.fileno())
            # Hard-link publication is atomic and refuses an existing destination.
            os.link(staged, destination)
            published = True
            descriptor = os.open(checksum, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            checksum_created = True
            with os.fdopen(descriptor, "w") as handle:
                handle.write(digest + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            _fsync_directory(destination.parent)
            return digest
    except BaseException:
        if checksum_created:
            checksum.unlink(missing_ok=True)
        if published:
            destination.unlink(missing_ok=True)
        raise


def offline_restore(
    backup_path: Path | str, destination: Path | str, *, offline_confirmed: bool = False,
) -> dict:
    """Restore only with explicit offline acknowledgement and no service flock.

    The lock detects the operating service. The caller must also stop dashboard
    and maintenance users; neither absent sidecars nor a free flock proves that.
    """
    if not offline_confirmed:
        raise ValueError("restore requires explicit offline confirmation after stopping every database user")
    backup, destination = Path(backup_path), Path(destination)
    if not backup.is_file() or backup.is_symlink() or destination.is_symlink():
        raise ValueError("restore paths must be regular files, without symlinks")
    if backup.resolve() == destination.resolve():
        raise ValueError("restore destination must differ from the backup")
    _private_directory(destination.parent)
    descriptor = None
    try:
        if destination.exists():
            descriptor = os.open(destination, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("restore destination is held by an operating service") from exc
            if os.fstat(descriptor).st_ino != destination.stat().st_ino:
                raise ValueError("restore destination changed while acquiring its lock")
        restore_database(backup, destination)
        _fsync_directory(destination.parent)
        return {"restored": True, "offline": True, "integrity": "ok", "live_enabled": False}
    finally:
        if descriptor is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
