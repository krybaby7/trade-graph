"""Owner-local bounded Kraken observation, outside graph credential storage.

This command observes a native account under a fresh owner grant. Scope mode
``live`` describes the observed venue; it never changes paper trading authority.
"""

from __future__ import annotations

import asyncio
import getpass
import hashlib
import os
import secrets
import stat
import sys
import uuid
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from trade_graph.adapters.engineering.artifact_files import ensure_directory, open_directory
from trade_graph.application.artifact_store import ArtifactStore
from trade_graph.application.operations import _read_database, _schema
from trade_graph.application.venue_conformance import (
    KrakenReadOnlyConformance,
    PinnedReadOnlyAuthority,
    PinnedVenueObservation,
    ReadOnlyObservationGrant,
    VenueObservationScope,
    _canonical,
    _mac,
    _read,
)
from trade_graph.contracts.models import OwnerPolicy
from trade_graph.domain.clock import SystemClock, utc_iso
from trade_graph.kernel.runtime_manifest import protected_package_sha256

SYMBOL = "BTC/USD"
AUTHORIZATION = "AUTHORIZE BTC/USD READ ONLY"
_KEY_NAMES = ("owner-signing.key", "collector-verification.key")


class OnboardingFailure(RuntimeError):
    """Fixed category; private exception details never cross the CLI boundary."""


@dataclass(frozen=True)
class _Bindings:
    scope: VenueObservationScope
    database_identity: tuple[int, int]
    portfolio_created_at: str
    version_id: str
    generation: int


def _absolute(path: Path) -> Path:
    path = path.expanduser().absolute()
    if ".." in path.parts:
        raise PermissionError("unsafe storage")
    return path


def _private_directory(path: Path, *, exact_mode: int = 0o700) -> None:
    fd = open_directory(path)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != exact_mode:
            raise PermissionError("unsafe storage")
    finally:
        os.close(fd)


def _private_file(path: Path) -> os.stat_result:
    parent = open_directory(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise PermissionError("unsafe storage")
            return info
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def _database_path(path: Path) -> Path:
    path = _absolute(path)
    _private_directory(path.parent)
    _private_file(path)
    for suffix in ("-wal", "-shm", "-journal"):
        sibling = Path(str(path) + suffix)
        if sibling.exists() or sibling.is_symlink():
            _private_file(sibling)
    return path


def _owner_path(path: Path, database: Path) -> Path:
    path = _absolute(path)
    package_checkout = Path(__file__).absolute().parents[3]
    if path.is_relative_to(database.parent) or path.is_relative_to(package_checkout):
        raise PermissionError("unsafe storage")
    for ancestor in (path, *path.parents):
        if (ancestor / ".git").exists():
            raise PermissionError("unsafe storage")
    existing = path
    while not existing.exists() and not existing.is_symlink():
        existing = existing.parent
    descriptor = open_directory(existing)
    os.close(descriptor)
    if path.exists() or path.is_symlink():
        _private_directory(path)
        for name in _KEY_NAMES:
            key = path / name
            if key.exists() or key.is_symlink():
                _private_file(key)
                if len(_read(key, 128)) != 32:
                    raise PermissionError("unsafe storage")
    return path


def _bindings(database: Path, symbol: str = SYMBOL) -> _Bindings:
    if os.name != "posix" or not sys.platform.startswith("linux") or symbol != SYMBOL:
        raise PermissionError("unsupported owner command")
    path = _database_path(database)
    initial = _private_file(path)
    with _read_database(path) as db:
        if _schema(db)["status"] != "current":
            raise ValueError("unsupported paper database")
        deployments = db.execute("SELECT deployment_id FROM deployment_budget").fetchall()
        if any(row["deployment_id"] != "deployment" for row in deployments):
            raise ValueError("unsupported deployment binding")
        portfolio = db.execute(
            "SELECT portfolio_id, created_at FROM portfolios WHERE mode = 'paper' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        policy = db.execute(
            "SELECT revision_id, document_json, content_hash FROM owner_policy_revisions "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        if portfolio is None or policy is None:
            raise ValueError("missing paper scope")
        parsed = OwnerPolicy.model_validate_json(policy["document_json"])
        policy_hash = hashlib.sha256(policy["document_json"].encode()).hexdigest()
        if (parsed.revision_id != policy["revision_id"] or policy_hash != policy["content_hash"]
                or symbol not in parsed.allowed_symbols):
            raise ValueError("invalid paper scope")
        active = db.execute(
            "SELECT version_id, artifact_hash, generation FROM active_versions WHERE portfolio_id = ?",
            (portfolio["portfolio_id"],),
        ).fetchone()
        if active is None:
            raise ValueError("missing paper scope")
        ArtifactStore(db, SystemClock()).get(active["artifact_hash"])
        scope = VenueObservationScope(
            deployment_id="deployment", portfolio_id=portfolio["portfolio_id"], account_id="owner-local-kraken",
            venue="kraken", mode="live", symbol=symbol, policy_revision=policy["revision_id"],
            policy_sha256=policy_hash, deployment_artifact_sha256=protected_package_sha256(),
            system_version_sha256=active["artifact_hash"],
        )
        result = _Bindings(scope, (initial.st_dev, initial.st_ino), portfolio["created_at"],
                           active["version_id"], active["generation"])
    final = _private_file(path)
    if (final.st_dev, final.st_ino) != result.database_identity:
        raise PermissionError("database identity changed")
    return result


def _write_private(directory: Path, name: str, raw: bytes) -> None:
    _private_directory(directory)
    parent = open_directory(directory)
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=parent)
        try:
            view = memoryview(raw)
            while view:
                view = view[os.write(fd, view):]
            os.fchmod(fd, 0o600)
            info = os.fstat(fd)
            if info.st_nlink != 1 or info.st_uid != os.geteuid():
                raise PermissionError("unsafe storage")
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(parent)
    finally:
        os.close(parent)
    _private_file(directory / name)


def _keys(owner: Path) -> tuple[bytes, bytes]:
    result = []
    for name in _KEY_NAMES:
        path = owner / name
        if not path.exists():
            _write_private(owner, name, secrets.token_bytes(32))
        _private_file(path)
        key = _read(path, 128)
        if len(key) != 32:
            raise PermissionError("unsafe storage")
        result.append(key)
    if result[0] == result[1]:
        raise PermissionError("key scopes must differ")
    return result[0], result[1]


def _signed_write(directory: Path, name: str, key: bytes, kind: str, payload: dict) -> bytes:
    raw = _canonical({"payload": payload, "signature": _mac(key, kind, payload)})
    _write_private(directory, name, raw)
    return raw


def _prompt_credentials() -> tuple[str, str]:
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise PermissionError("hidden owner terminal required")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            key = getpass.getpass("Kraken read-only API key (hidden): ")
            secret = getpass.getpass("Kraken read-only API secret (hidden): ")
    except (getpass.GetPassWarning, EOFError, OSError):
        raise PermissionError("hidden owner terminal required") from None
    if any(not isinstance(value, str) or not 0 < len(value) <= 1024 for value in (key, secret)):
        raise PermissionError("bounded credentials required")
    return key, secret


def run_kraken_read_only(database_path: Path, owner_directory: Path, symbol: str = SYMBOL) -> dict:
    """Prompt only on a safe owner terminal; credentials are never persisted."""
    database = _database_path(database_path)
    _bindings(database, symbol)
    owner = _owner_path(owner_directory, database)
    api_key, api_secret = _prompt_credentials()
    print("Read-only observation: BTC/USD, at most 60 seconds, 128 requests, 5 history pages; last 7 days.")
    authorization = input(f"Type {AUTHORIZATION} to authorize this observation: ")
    try:
        return asyncio.run(_collect_owner_read_only(
            database, owner, symbol=symbol, api_key=api_key, api_secret=api_secret, authorization=authorization,
        ))
    finally:
        # Python strings cannot be reliably zeroed. Keep references local and
        # retain neither API value in intents, captures, errors, or summaries.
        del api_key, api_secret


async def _collect_owner_read_only(
    database_path: Path, owner_directory: Path, *, api_key: str, api_secret: str, authorization: str,
    symbol: str = SYMBOL, collector_factory=None, importer=None,
) -> dict:
    """Internal dependency seam for synthetic tests; the command exposes no seam."""
    database = _database_path(database_path)
    original = _bindings(database, symbol)
    owner = _owner_path(owner_directory, database)
    if authorization != AUTHORIZATION:
        raise PermissionError("read-only authorization required")
    if any(not isinstance(value, str) or not 0 < len(value) <= 1024 for value in (api_key, api_secret)):
        raise PermissionError("bounded credentials required")
    ensure_directory(owner)
    _private_directory(owner)
    owner_key, collector_key = _keys(owner)
    run_id = uuid.uuid4().hex
    run = owner / run_id
    parent = open_directory(owner)
    try:
        os.mkdir(run_id, mode=0o700, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)
    now = datetime.now(UTC)
    grant = ReadOnlyObservationGrant(
        schema_version=1, authorization_id=run_id, action="observe_read_only_venue_account", scope=original.scope,
        credential_binding_sha256=hashlib.sha256(api_key.encode()).hexdigest(), not_before=now,
        expires_at=now + timedelta(minutes=2), maximum_requests=128, maximum_history_pages=5,
        maximum_duration_seconds=60, history_start_utc=now - timedelta(days=7),
    )
    grant_raw = _signed_write(run, "grant.json", owner_key, "owner-grant", grant.model_dump(mode="json"))
    grant_hash = hashlib.sha256(grant_raw).hexdigest()
    _signed_write(run, "intent.json", owner_key, "onboarding-intent", {
        "schema_version": 1, "run_id": run_id, "action": grant.action,
        "authorized_at": utc_iso(now), "grant_sha256": grant_hash,
        "scope": original.scope.model_dump(mode="json"),
        "paper_version_id": original.version_id, "paper_generation": original.generation,
        "portfolio_created_at": original.portfolio_created_at,
        "database_identity": list(original.database_identity),
    })
    _signed_write(run, "pin.json", owner_key, "onboarding-pin", {
        "schema_version": 1, "run_id": run_id, "grant_sha256": grant_hash,
        "collector_key_sha256": hashlib.sha256(collector_key).hexdigest(),
    })
    authority = PinnedReadOnlyAuthority(run / "grant.json", grant_hash, owner_key)
    category = "collection_failed"
    try:
        factory = collector_factory or KrakenReadOnlyConformance
        collector = factory(authority, collector_key=collector_key, api_key=api_key, api_secret=api_secret)
        capture = await collector.collect(run)
        category = "verification_failed"
        if type(capture) is not PinnedVenueObservation:
            raise ValueError("unexpected observation source")
        current_time = datetime.now(UTC)
        _signed_write(run, "capture-pin.json", owner_key, "onboarding-capture-pin", {
            "schema_version": 1, "run_id": run_id, "observation_sha256": capture.observation_sha256,
            "retained_at": utc_iso(current_time),
        })
        proof = capture.verify(now=current_time, maximum_age_seconds=60)
        if proof.observation.scope != original.scope or not proof.source_current:
            raise ValueError("observation binding changed")
        category = "binding_changed"
        if _keys(owner) != (owner_key, collector_key):
            raise ValueError("owner key scope changed")
        if _bindings(database, symbol) != original:
            raise ValueError("paper scope changed")
        category = "import_failed"
        if importer is None:
            from trade_graph.api.account_checks import import_observation

            importer = import_observation
        runtime = SimpleNamespace(database=SimpleNamespace(path=database),
                                  portfolio_id=original.scope.portfolio_id, deployment_id="deployment")
        result = importer(runtime, capture, original.scope, maximum_age_seconds=60, now=datetime.now(UTC))
        _signed_write(run, "result.json", owner_key, "onboarding-result", {
            "schema_version": 1, "run_id": run_id, "finished_at": utc_iso(datetime.now(UTC)),
            "observation_sha256": capture.observation_sha256, "status": "imported",
        })
        return result
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
            category = "interrupted"
        _signed_write(run, "failure.json", owner_key, "onboarding-failure", {
            "schema_version": 1, "run_id": run_id, "failed_at": utc_iso(datetime.now(UTC)), "category": category,
        })
        raise OnboardingFailure("Read-only observation failed; private evidence retained.") from None
