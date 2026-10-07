"""Retained local operations facts, never owner/host/provider authority by declaration."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from trade_graph.adapters.engineering.artifact_files import ensure_directory, open_directory
from trade_graph.adapters.engineering.process import run_bounded
from trade_graph.application.authority import AuthorityRecord
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.operations import (
    _configuration,
    _private_directory,
    _read_database,
    _runtime,
    _schema,
    financial_report,
    offline_restore,
    private_backup,
)
from trade_graph.application.service_binding import ServiceUnitBinding, private_source
from trade_graph.application.service_proof import LocalRestartSource
from trade_graph.domain.clock import Clock, FrozenClock, SystemClock, parse_utc, utc_iso
from trade_graph.domain.errors import TradeGraphError, ValidationFailure
from trade_graph.kernel.runtime_manifest import canonical_json, protected_package_sha256

MAX_DATABASE_BYTES = 16_777_216
MAX_REPORT_BYTES = 1_048_576
MODULE = Path(__file__).resolve()
_SCOPE_FIELDS = (
    "deployment_id",
    "portfolio_id",
    "policy_revision",
    "policy_sha256",
    "artifact_sha256",
    "database_snapshot_sha256",
    "config_sha256",
)


def _small_file(path: Path, limit: int) -> bytes:
    parent = open_directory(path.parent)
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise ValueError("bounded regular evidence source required")
            chunks, total = [], 0
            while chunk := os.read(descriptor, min(65536, limit + 1 - total)):
                chunks.append(chunk)
                total += len(chunk)
                if total > limit:
                    raise ValueError("evidence source exceeded byte bound")
            current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
                current.st_dev,
                current.st_ino,
                current.st_size,
                current.st_mtime_ns,
            ):
                raise ValueError("evidence source changed during read")
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _host_sha256() -> str | None:
    try:
        identity = _small_file(Path("/etc/machine-id"), 256).strip()
        if len(identity) != 32 or any(value not in b"0123456789abcdef" for value in identity):
            return None
        return _sha(b"linux-installation-identity-v1\0" + identity + b"\0" + os.uname().machine.encode())
    except (OSError, ValueError):
        return None


def _fact(status: str, **facts) -> dict:
    return {"status": status, "facts": facts}


def _public_failure(exc: Exception) -> dict:
    """Useful network diagnosis without exception strings, request URLs or proxy values."""
    import httpx

    facts = {"failure_type": type(exc).__name__}
    if isinstance(exc, httpx.ProxyError):
        facts["failure_category"] = "proxy_connection_failed"
    elif isinstance(exc, httpx.TimeoutException):
        facts["failure_category"] = "network_timeout"
    elif isinstance(exc, httpx.HTTPStatusError):
        facts.update(failure_category="http_status_refused", http_status=exc.response.status_code)
    elif isinstance(exc, httpx.TransportError):
        facts["failure_category"] = "network_transport_failed"
    elif isinstance(exc, (ValueError, TradeGraphError)):
        facts["failure_category"] = "response_validation_refused"
    else:
        facts["failure_category"] = "observation_unavailable"
    return facts


def _no_duplicates(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate host evidence field")
        result[name] = value
    return result


@dataclass(frozen=True)
class RetainedHostObservation:
    path: Path
    sha256: str


@dataclass(frozen=True)
class VerifiedHostObservation:
    """Immutable bytes authenticated locally; no external authority is implied."""

    path: Path
    sha256: str
    document_json: str

    @property
    def document(self) -> dict:
        return json.loads(self.document_json)


class HostObservationCollector:
    def __init__(
        self,
        database_path: Path | str | None,
        *,
        signing_key: bytes,
        config_path: Path | str | None = None,
        clock: Clock | None = None,
        service_binding: ServiceUnitBinding | None = None,
        restart_source: LocalRestartSource | None = None,
    ) -> None:
        if type(signing_key) is not bytes or not 32 <= len(signing_key) <= 64:
            raise ValueError("protected local observation key requires 32..64 bytes")
        self.database_path = None if database_path is None else Path(database_path)
        self.config_path = None if config_path is None else Path(config_path)
        self.clock = clock or SystemClock()
        self._key = signing_key
        if service_binding is not None and type(service_binding) is not ServiceUnitBinding:
            raise ValueError("exact protected service unit binding required")
        if restart_source is not None and type(restart_source) is not LocalRestartSource:
            raise ValueError("exact protected restart source required")
        if service_binding is not None and (self.database_path != service_binding.database_path
                                           or self.config_path != service_binding.config_path):
            raise ValueError("service unit and host observation must bind the same database/configuration")
        self.service_binding, self.restart_source = service_binding, restart_source

    def _source_identities(self) -> dict:
        result = {}
        for name, path in (("database", self.database_path), ("config", self.config_path)):
            if path is None:
                result[name] = None
                continue
            try:
                info, parent = path.stat(follow_symlinks=False), path.parent.stat(follow_symlinks=False)
                result[name] = {
                    "regular": stat.S_ISREG(info.st_mode),
                    "device": info.st_dev,
                    "inode": info.st_ino,
                    "bytes": info.st_size,
                    "mtime_ns": info.st_mtime_ns,
                    "mode": stat.S_IMODE(info.st_mode),
                    "directory_mode": stat.S_IMODE(parent.st_mode),
                    "directory_inode": parent.st_ino,
                    "directory_device": parent.st_dev,
                }
            except OSError:
                result[name] = None
        return result

    def _sources(self) -> tuple[dict, dict]:
        scope = dict.fromkeys(_SCOPE_FIELDS)
        checks = {
            "storage": _fact("pending", reason="private paper database is not configured"),
            "funded_setup": _fact(
                "pending",
                missing=[
                    "private_runtime_config",
                    "paper_database",
                    "owner_budget",
                    "owner_permission",
                    "provider_credential",
                ],
            ),
        }
        if self.config_path is not None:
            try:
                from trade_graph.paper_runtime import PaperRuntimeConfig

                payload = private_source(self.config_path, 262144)
                scope["config_sha256"] = _sha(payload)
                PaperRuntimeConfig.model_validate_json(payload)
            except (OSError, ValueError):
                checks["funded_setup"] = _fact("refused", reason="protected runtime configuration is invalid")
        if self.database_path is None or not self.database_path.is_file():
            return scope, checks
        try:
            if self.database_path.is_symlink():
                raise ValueError("operations database must not be a symlink")
            private_source(self.database_path, MAX_DATABASE_BYTES, private=False)
            frozen = FrozenClock(self.clock.now())
            with _read_database(self.database_path) as database:
                deadline = monotonic() + 5
                database.connection.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
                pages = database.execute("PRAGMA page_count").fetchone()[0]
                size = database.execute("PRAGMA page_size").fetchone()[0]
                if pages * size > MAX_DATABASE_BYTES:
                    raise ValueError("database exceeds bounded operations evidence size")
                schema = _schema(database)
                if schema["status"] != "current":
                    raise ValueError("operations evidence requires current schema")
                if [row[0] for row in database.execute("PRAGMA integrity_check(1)")] != ["ok"]:
                    raise ValueError("operations database integrity is unavailable")
                if database.execute("SELECT 1 FROM portfolios WHERE mode != 'paper' LIMIT 1").fetchone():
                    raise ValueError("operations collector accepts paper-only databases")
                runtime = _runtime(database, frozen, None)
                config = _configuration(self.config_path, runtime)
                policy = database.execute(
                    "SELECT * FROM owner_policy_revisions ORDER BY created_at DESC,rowid DESC LIMIT 1"
                ).fetchone()
                if policy is not None and _sha(policy["document_json"].encode()) != policy["content_hash"]:
                    raise ValueError("owner policy content binding is invalid")
                snapshot = database.connection.serialize()
                if len(snapshot) > MAX_DATABASE_BYTES:
                    raise ValueError("database exceeded operations evidence size during snapshot")
                artifact = database.execute(
                    "SELECT artifact_hash FROM active_versions WHERE portfolio_id=?", (runtime.portfolio_id,)
                ).fetchone()
                scope.update(
                    deployment_id=runtime.deployment_id,
                    portfolio_id=runtime.portfolio_id,
                    policy_revision=None if policy is None else policy["revision_id"],
                    policy_sha256=None if policy is None else policy["content_hash"],
                    artifact_sha256=None if artifact is None else artifact["artifact_hash"],
                    database_snapshot_sha256=_sha(snapshot),
                )
                private_file = not bool(self.database_path.stat().st_mode & 0o077)
                private_parent = not bool(self.database_path.resolve().parent.stat().st_mode & 0o077)
                private_sidecars = all(
                    not sidecar.is_symlink() and not bool(sidecar.stat().st_mode & 0o077)
                    for suffix in ("-wal", "-shm", "-journal")
                    if (sidecar := Path(str(self.database_path) + suffix)).exists()
                )
                checks["storage"] = _fact(
                    "observed" if private_file and private_parent and private_sidecars else "refused",
                    private_file=private_file,
                    private_directory=private_parent,
                    private_sidecars=private_sidecars,
                    current_schema=True,
                    integrity_verified=True,
                    paper_only=True,
                    persistent_mount_verified=False,
                    database_snapshot_bytes=len(snapshot),
                )
                missing = []
                if config["status"] != "valid":
                    missing.append("private_model_configuration")
                if not config.get("public_data_configured"):
                    missing.append("public_data_opt_in")
                if not config.get("paid_calls_configured"):
                    missing.append("private_paid_permission")
                if policy is None:
                    missing.append("persisted_owner_policy")
                if policy is None or not AuthorityRecord(database, frozen).active_policy().paid_calls_enabled:
                    missing.append("persisted_owner_paid_permission")
                if config.get("routing", {}).get("status") != "ready":
                    missing.append("approved_current_six_role_price_routes")
                roles = config.get("routing", {}).get("roles", [])
                required = {role["provider"] for role in roles if role["provider"] in {"openai", "anthropic"}}
                presence = {
                    provider: bool(os.getenv({"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}[provider]))
                    for provider in sorted(required)
                }
                if not required or not all(presence.values()) or any(role["provider"] == "scripted" for role in roles):
                    missing.append("selected_runtime_provider_credentials")
                try:
                    from decimal import Decimal

                    budget = BudgetGateway(database, frozen)
                    limits = budget._budget(runtime.deployment_id)
                    positive = all(
                        Decimal(limits[name]) > 0 for name in ("total_allowance", "daily_limit", "root_limit")
                    )
                    if not positive or budget.remaining(runtime.deployment_id) <= 0:
                        missing.append("available_separate_real_operating_allowance")
                except (ValueError, KeyError, RuntimeError, TradeGraphError):
                    missing.append("available_separate_real_operating_allowance")
                pause = database.execute(
                    "SELECT profile FROM pause_states WHERE portfolio_id=?", (runtime.portfolio_id,)
                ).fetchone()
                if pause is not None and pause["profile"] != "RUNNING":
                    missing.append("explicit_owner_resume_and_reconciliation")
                checks["funded_setup"] = _fact(
                    "pending" if missing else "observed",
                    missing=missing,
                    provider_variable_presence=presence,
                    credentialed_probe_verified=False,
                    invoice_verified=False,
                    spending_authorized_by_capture=False,
                )
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, TradeGraphError) as exc:
            checks["storage"] = _fact("unavailable", failure_type=type(exc).__name__)
        return scope, checks

    def _service(self) -> dict:
        if self.service_binding is not None:
            try:
                return self.service_binding.observe()
            except (OSError, ValueError, KeyError, UnicodeError) as exc:
                return _fact("refused", failure_type=type(exc).__name__, unit_configuration_verified=False)
        unit = Path("/run/systemd/system")
        binary = Path("/usr/bin/systemctl")
        if not unit.is_dir() or not binary.is_file():
            return _fact("pending", reason="running systemd installation is unavailable")
        try:
            result = run_bounded(
                [
                    str(binary),
                    "show",
                    "trade-graph-paper.service",
                    "--no-pager",
                    "--property=LoadState,ActiveState,SubState",
                ],
                b"",
                cwd="/",
                wall_seconds=5,
            )
            if result["exit_code"] != 0 or len(result["stdout"]) > 4096 or len(result["stderr"]) > 4096:
                return _fact("unavailable", reason="bounded service status probe failed")
            values = dict(line.split("=", 1) for line in result["stdout"].splitlines() if "=" in line)
            loaded, active = values.get("LoadState") == "loaded", values.get("ActiveState") == "active"
            return _fact(
                "observed" if loaded and active else "pending",
                unit_loaded=loaded,
                unit_active=active,
                unit="trade-graph-paper.service",
                unit_configuration_verified=False,
                service_binary_sha256=_sha(_small_file(binary, 1_048_576)),
            )
        except (OSError, ValueError, UnicodeError) as exc:
            return _fact("unavailable", failure_type=type(exc).__name__)

    def _restart_rehearsal(self) -> dict:
        if self.restart_source is None:
            return _fact("pending", reason="local subprocess restart rehearsal was not retained")
        proof = self.restart_source.verify()
        document = proof.document
        return _fact("observed", evidence_sha256=proof.sha256,
                     protected_package_sha256=document["protected_package_sha256"],
                     config_sha256=document["config_sha256"], scope=document["scope"],
                     synthetic_lost_ack_reconciled=True, owner_pause_preserved=True,
                     financial_continuity_verified=True, actual_systemd_verified=False,
                     operating_database_restart_verified=False, funded_acceptance_verified=False)

    def _public(self) -> dict:
        from trade_graph.adapters.market.public import FrankfurterClient, HttpxTextTransport, KrakenPublicRest

        class RetainingTransport:
            def __init__(self):
                self.response_sha256 = None
                self.request_sha256 = None

            def get_text(inner, url):
                inner.request_sha256 = _sha(url.encode())
                text = HttpxTextTransport(timeout=5).get_text(url)
                inner.response_sha256 = _sha(text.encode())
                return text

        outcomes = []
        for name in ("kraken_time", "kraken_metadata", "frankfurter_ecb"):
            transport = RetainingTransport()
            attempted_at = utc_iso(self.clock.now())
            try:
                facts = {}
                if name == "kraken_time":
                    payload = json.loads(transport.get_text("https://api.kraken.com/0/public/Time"))
                    if (
                        not isinstance(payload, dict)
                        or payload.get("error") != []
                        or not isinstance(payload.get("result"), dict)
                        or type(payload["result"].get("unixtime")) is not int
                        or payload["result"]["unixtime"] <= 0
                        or not isinstance(payload["result"].get("rfc1123"), str)
                    ):
                        raise ValidationFailure("Kraken time response is invalid")
                elif name == "kraken_metadata":
                    instruments = KrakenPublicRest(transport).fetch_instruments(["XBTUSD", "ETHUSD"])
                    if {instrument.symbol for instrument in instruments} != {"BTC/USD", "ETH/USD"}:
                        raise ValidationFailure("Kraken metadata pair scope is invalid")
                    facts["symbols"] = sorted(instrument.symbol for instrument in instruments)
                else:
                    rate = FrankfurterClient(transport).reference_rate("USD", "EUR")
                    if rate.rate_date > self.clock.now().date().isoformat():
                        raise ValidationFailure("reference FX follows retrieval date")
                    facts.update(
                        rate_date=rate.rate_date,
                        carried_prior_date=rate.rate_date < self.clock.now().date().isoformat(),
                    )
                outcomes.append(
                    {
                        "endpoint": name,
                        "status": "observed",
                        **facts,
                        "decoded_response_sha256": transport.response_sha256,
                        "request_url_sha256": transport.request_sha256,
                        "attempted_at": attempted_at,
                        "received_at": utc_iso(self.clock.now()),
                        "external_authority_verified": False,
                    }
                )
            except Exception as exc:
                outcomes.append(
                    {
                        "endpoint": name,
                        "status": "refused" if isinstance(exc, (ValueError, TradeGraphError)) else "unavailable",
                        **_public_failure(exc),
                        "attempted_at": attempted_at,
                        "completed_at": utc_iso(self.clock.now()),
                        "request_url_sha256": transport.request_sha256,
                        "decoded_response_sha256": transport.response_sha256,
                    }
                )
        return _fact(
            "observed" if all(item["status"] == "observed" for item in outcomes) else "unavailable",
            outcomes=outcomes,
            portfolio_observations_created=False,
            funded_provider_verified=False,
        )

    def _drill(self, evidence_path: Path, scope: dict) -> dict:
        if self.database_path is None or scope["database_snapshot_sha256"] is None:
            return _fact("pending", reason="configured valid private paper database is required")
        directory = evidence_path.parent / (evidence_path.name + ".artifacts")
        backup, restored = directory / "snapshot.sqlite", directory / "restored.sqlite"
        try:
            directory.mkdir(mode=0o700)
            digest = private_backup(self.database_path, backup, maximum_bytes=MAX_DATABASE_BYTES, wall_seconds=10)
            offline_restore(backup, restored, offline_confirmed=True)
            frozen = FrozenClock(self.clock.now())
            expected = financial_report(backup, portfolio_id=scope["portfolio_id"], clock=frozen)
            actual = financial_report(restored, portfolio_id=scope["portfolio_id"], clock=frozen)
            if actual != expected:
                return _fact("refused", reason="restored financial projection differs from retained backup")
            return _fact(
                "observed",
                backup_sha256=digest,
                backup_path=str(backup),
                restored_path=str(restored),
                restored_sha256=_sha(_small_file(restored, MAX_DATABASE_BYTES)),
                projection_as_of=utc_iso(frozen.now()),
                financial_projection_sha256=_sha(canonical_json(expected).encode()),
                restored_projection_matches_backup=True,
                original_database_mutated=False,
                restart_verification="pending",
                off_host_copy_verified=False,
            )
        except Exception as exc:
            return _fact("unavailable", failure_type=type(exc).__name__)

    def capture(
        self, evidence_path: Path | str, *, backup_restore: bool = False, public_data: bool = False
    ) -> RetainedHostObservation:
        if type(backup_restore) is not bool or type(public_data) is not bool:
            raise ValueError("explicit boolean local operations opt-ins required")
        path = Path(evidence_path)
        ensure_directory(path.parent)
        _private_directory(path.parent)
        parent = open_directory(path.parent)
        try:
            if os.fstat(parent).st_uid != os.geteuid():
                raise ValueError("host report directory requires the current owner")
            descriptor = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        except BaseException:
            os.close(parent)
            raise
        try:
            started = utc_iso(self.clock.now())
            package = protected_package_sha256()
            host = _host_sha256()
            source_identities = self._source_identities()
            scope, checks = self._sources()
            source_checks = {name: checks[name] for name in ("storage", "funded_setup")}
            checks.update(
                service=self._service(),
                backup_restore=self._drill(path, scope)
                if backup_restore
                else _fact("pending", reason="drill not requested"),
                off_host_backup=_fact("pending", reason="no owner-controlled off-host recovery evidence"),
                alerts=_fact("pending", reason="no authorized actual delivery verification"),
                public_data=self._public() if public_data else _fact("pending", reason="public probe not requested"),
                immutable_image=_fact("pending"),
                immutable_mounts=_fact("pending"),
                local_restart_rehearsal=self._restart_rehearsal(),
            )
            current, current_checks = self._sources()
            consistent = (
                current == scope
                and protected_package_sha256() == package
                and self._source_identities() == source_identities
                and _host_sha256() == host
                and {name: current_checks[name] for name in source_checks} == source_checks
            )
            if not consistent:
                checks["storage"] = _fact("refused", reason="protected sources changed during collection")
            document = {
                "schema_version": 1,
                "kind": "host_observation",
                "started_at": started,
                "ended_at": utc_iso(self.clock.now()),
                "collector_artifact_sha256": _sha(_small_file(MODULE, MAX_REPORT_BYTES)),
                "protected_package_sha256": package,
                "observed_host_sha256": host,
                "platform": sys.platform,
                "architecture": os.uname().machine,
                "scope": scope,
                "source_identities": source_identities,
                "source_checks": source_checks,
                "collection_consistent": consistent,
                "checks": checks,
                "owner_intended_host_verified": False,
                "immutable_image_verified": False,
                "immutable_mounts_verified": False,
                "verification_basis": "local_collector_facts",
                "paid_calls": False,
                "private_venue_calls": False,
                "live_enabled": False,
            }
            signature = hmac.new(self._key, canonical_json(document).encode(), hashlib.sha256).hexdigest()
            document["authentication"] = {"scheme": "hmac-sha256", "signature": signature}
            payload = (canonical_json(document) + "\n").encode()
            if len(payload) > MAX_REPORT_BYTES:
                raise ValueError("host report exceeded bounded output size")
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.fsync(parent)
            return RetainedHostObservation(path, _sha(payload))
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(parent)

    def verify(self, evidence_path: Path | str, expected_sha256: str) -> VerifiedHostObservation:
        path = Path(evidence_path)
        payload = private_source(path, MAX_REPORT_BYTES)
        if type(expected_sha256) is not str or not hmac.compare_digest(_sha(payload), expected_sha256):
            raise ValueError("retained host evidence byte digest mismatch")
        try:
            document = json.loads(payload, object_pairs_hook=_no_duplicates)
            authentication = document.pop("authentication")
            expected = hmac.new(self._key, canonical_json(document).encode(), hashlib.sha256).hexdigest()
            if authentication != {"scheme": "hmac-sha256", "signature": expected}:
                raise ValueError("host observation authentication mismatch")
            if document["schema_version"] != 1 or document["kind"] != "host_observation":
                raise ValueError("unsupported host observation")
            if (
                document["collection_consistent"] is not True
                or document["verification_basis"] != "local_collector_facts"
                or any(
                    document[name] is not False
                    for name in (
                        "owner_intended_host_verified",
                        "immutable_image_verified",
                        "immutable_mounts_verified",
                        "paid_calls",
                        "private_venue_calls",
                        "live_enabled",
                    )
                )
            ):
                raise ValueError("unsupported host evidence authority or inconsistent collection")
            current, checks = self._sources()
            if any(document["checks"][name] != document["source_checks"][name]
                   for name in ("storage", "funded_setup")):
                raise ValueError("host observation check differs from its measured source")
            if any(document["checks"][name]["status"] != "pending"
                   for name in ("off_host_backup", "alerts", "immutable_image", "immutable_mounts")):
                raise ValueError("unavailable deployment evidence cannot be promoted by local authentication")
            if (
                document["collector_artifact_sha256"] != _sha(_small_file(MODULE, MAX_REPORT_BYTES))
                or document["protected_package_sha256"] != protected_package_sha256()
                or document["observed_host_sha256"] != _host_sha256()
                or document["source_identities"] != self._source_identities()
                or document["source_checks"] != {name: checks[name] for name in ("storage", "funded_setup")}
                or document["scope"] != current
            ):
                raise ValueError("host observation sources changed")
            if (
                document["checks"]["service"]["status"] == "observed"
                and document["checks"]["service"] != self._service()
            ):
                raise ValueError("observed service state changed")
            if document["checks"].get("local_restart_rehearsal") != self._restart_rehearsal():
                raise ValueError("local service restart rehearsal source changed")
            drill = document["checks"]["backup_restore"]
            if drill["status"] == "observed":
                facts = drill["facts"]
                directory = path.parent / (path.name + ".artifacts")
                backup, restored = directory / "snapshot.sqlite", directory / "restored.sqlite"
                if (
                    facts["backup_path"] != str(backup)
                    or facts["restored_path"] != str(restored)
                    or directory.is_symlink()
                    or directory.stat().st_mode & 0o077
                    or backup.stat().st_mode & 0o077
                    or restored.stat().st_mode & 0o077
                    or facts["backup_sha256"] != _sha(private_source(backup, MAX_DATABASE_BYTES))
                    or facts["restored_sha256"] != _sha(private_source(restored, MAX_DATABASE_BYTES))
                    or private_source(backup.with_suffix(".sqlite.sha256"), 128).strip().decode()
                    != facts["backup_sha256"]
                ):
                    raise ValueError("retained restore drill artifact changed")
                frozen = FrozenClock(parse_utc(facts["projection_as_of"]))
                for source in (backup, restored):
                    projection = financial_report(source, portfolio_id=current["portfolio_id"], clock=frozen)
                    if _sha(canonical_json(projection).encode()) != facts["financial_projection_sha256"]:
                        raise ValueError("retained restore projection changed")
            document["authentication"] = authentication
            return VerifiedHostObservation(path, expected_sha256, canonical_json(document))
        except (KeyError, TypeError, RecursionError) as exc:
            raise ValueError("invalid retained host observation") from exc
