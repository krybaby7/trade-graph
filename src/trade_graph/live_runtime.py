"""Owner-commissioned Kraken assembly; defaults fail before credential access.

Loading a config is never live authorization. Production assembly requires an
independently protected owner distribution, actual container confinement,
current signed readiness and independent source reviews. No request is made by
assembly; the exclusive service reconciles before starting discretionary work.
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from trade_graph.adapters.brokers.kraken_live import KrakenLiveBroker
from trade_graph.adapters.brokers.kraken_transport import KrakenRestTransport
from trade_graph.adapters.engineering.artifact_files import write_file
from trade_graph.adapters.market.live_feed import LivePublicTextTransport, PublicLiveFeed
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.activation import VersionController
from trade_graph.application.artifact_runtime import ArtifactRuntime
from trade_graph.application.broker_identity import DurableBrokerIdentity
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.engineer import ArtifactEngineer
from trade_graph.application.execution import Execution
from trade_graph.application.leader import LeaderOffice
from trade_graph.application.ledger import Ledger
from trade_graph.application.protected_live import ProtectedLiveDeploymentBinding
from trade_graph.application.scheduler import Scheduler
from trade_graph.application.secretary import Secretary
from trade_graph.domain.clock import SystemClock
from trade_graph.domain.errors import AuthorityDenied, LiveDisabled
from trade_graph.kernel.deployment_image import read_owner_file
from trade_graph.kernel.live_commission import PinnedLiveCommission
from trade_graph.kernel.runtime_manifest import canonical_json
from trade_graph.live_evidence import LiveUpstreamSources
from trade_graph.live_gate import LivePilotScope, PinnedReadinessSource, evaluate_live_readiness
from trade_graph.live_pilot import ProtectedPilotLifecycle
from trade_graph.paper_runtime import _private_directory, installed_artifacts


class LiveRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    live_enabled: StrictBool = False
    scope: LivePilotScope | None = None
    readiness_path: Path | None = None
    readiness_bundle_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    commission_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    models: dict | None = None
    tick_interval_seconds: float = Field(default=1, gt=0, le=60, allow_inf_nan=False)
    public_poll_interval_seconds: int = Field(default=10, ge=1, le=3600)


def live_configuration_digest(config: LiveRuntimeConfig) -> str:
    """Bind substantive owner configuration without a circular commission pin.

    The root-distributed config independently pins the final commission bytes;
    that commission binds these normalized settings excluding its own hash.
    """
    return hashlib.sha256(canonical_json(config.model_dump(mode="json", exclude={"commission_sha256"}))
                          .encode()).hexdigest()


def load_live_runtime_config(path: Path | None) -> LiveRuntimeConfig:
    if path is None:
        return LiveRuntimeConfig()
    try:
        raw = read_owner_file(path.parent, path.name, 262144)
        return LiveRuntimeConfig.model_validate_json(raw)
    except (ValueError, OSError):
        raise AuthorityDenied("live startup blocked: protected_live_configuration_invalid") from None


class _AdmissionDatabase:
    """A consistent read-only SQLite view; no migration or financial writes."""

    def __init__(self, path):
        self.connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, isolation_level=None)
        self.connection.row_factory = sqlite3.Row

    def execute(self, sql, args=()):
        return self.connection.execute(sql, args)

    @contextmanager
    def snapshot(self):
        self.connection.execute("BEGIN")
        try:
            yield self.connection
        finally:
            self.connection.rollback()

    def close(self):
        self.connection.close()


def assemble_live_runtime(path: Path, *, portfolio_id: str | None = None, config: LiveRuntimeConfig | None = None,
                          protected_owner: Path | None = None, clock=None):
    """Create no transport, read no exchange key, and mutate no DB on refusal."""
    if protected_owner is None:
        raise AuthorityDenied("live startup blocked: protected_owner_commission_required")
    config = config or load_live_runtime_config(protected_owner / "live-config.json")
    if not config.live_enabled:
        raise AuthorityDenied("live startup blocked: owner_live_enablement_missing")
    if (config.scope is None or config.readiness_path is None or config.readiness_bundle_sha256 is None
            or config.commission_sha256 is None or config.scope.venue != "kraken"
            or portfolio_id is not None and portfolio_id != config.scope.portfolio_id):
        raise AuthorityDenied("live startup blocked: exact_owner_live_scope_required")
    commission = PinnedLiveCommission(protected_owner, config.commission_sha256)
    try:
        profile = commission.load()
        protected_raw = read_owner_file(protected_owner, "live-config.json", 262144)
        if (LiveRuntimeConfig.model_validate_json(protected_raw) != config
                or live_configuration_digest(config) != profile.live_config_sha256):
            raise AuthorityDenied("live startup blocked: owner_configuration_pin_mismatch")
        issuer_keys = {issuer: read_owner_file(protected_owner, f"readiness-{issuer}.key", 64)
                       for issuer in ("owner", "eligibility", "venue", "operations", "economics")}
        readiness = PinnedReadinessSource(config.readiness_path, config.readiness_bundle_sha256, issuer_keys)
        clock = clock or SystemClock()
        if path.is_symlink() or not path.is_file():
            raise AuthorityDenied("live startup blocked: separate_live_database_required")
        path = path.resolve()
        if path.parent.is_symlink() or path.parent.stat().st_mode & 0o077:
            raise AuthorityDenied("live startup blocked: private_live_storage_required")
        database = _AdmissionDatabase(path)
    except (ValueError, OSError):
        raise AuthorityDenied("live startup blocked: protected_commission_unprovisioned_or_invalid") from None
    try:
        if database.execute("SELECT 1 FROM portfolios WHERE mode!='live' LIMIT 1").fetchone():
            raise AuthorityDenied("live startup blocked: dedicated_live_database_required")
        upstream = LiveUpstreamSources(commission=commission)
        admission = evaluate_live_readiness(database, clock, scope=config.scope, source=readiness, upstream=upstream)
        previously_admitted = commission.admitted(database, config.scope,
                                                  readiness.load().owner_authorization.payload.authorization_id)
        if admission["ready"] is not True and not previously_admitted:
            raise AuthorityDenied("live startup blocked: actual_host_account_economics_or_owner_evidence_missing")
        if previously_admitted:
            commission.verify(config.scope, readiness.load(), clock, database=database, management_only=True)
        model_config = None
        if config.models is not None:
            from trade_graph.application.runtime_models import RuntimeModelConfig

            model_config = RuntimeModelConfig.model_validate(config.models)
            if model_config.paid_calls_enabled:
                raise AuthorityDenied("live startup blocked: separately_billed_model_calls_forbidden")
        database.close()
        database = Database(path)
        # Exchange credentials are read only after verified production admission.
        key, secret = commission.credentials()
        transport = KrakenRestTransport(api_key=key, api_secret=secret, allow_order_writes=True,
                                        proxy=profile.proxy_url)
        identity = DurableBrokerIdentity(database, venue="kraken", account_id=config.scope.account_id, mode="live")
        broker = KrakenLiveBroker(transport, live_enabled=True, key_present=True, account_id=config.scope.account_id,
                                  clock=clock, symbols=[config.scope.symbol], intent_resolver=identity)
        lifecycle = ProtectedPilotLifecycle(database, clock, scope=config.scope, source=readiness, upstream=upstream)
        authorization_id = readiness.load().owner_authorization.payload.authorization_id
        ledger = Ledger(database, clock)
        execution = Execution(database, ledger, clock, broker, venue="kraken", account_id=config.scope.account_id,
                              mode="live", pilot_lifecycle=lifecycle, pilot_authorization_id=authorization_id)
        scheduler, versions = Scheduler(database, clock), VersionController(database, clock)
        artifact_runtime = ArtifactRuntime(versions, scheduler)
        budget = BudgetGateway(database, clock)
        secretary = Secretary(execution, scheduler, artifact_runtime=artifact_runtime)
        office = LeaderOffice(execution, scheduler, budget)
        source_root = path.parent / "installed-artifacts"
        _private_directory(source_root)
        for name, text in installed_artifacts().items():
            target = source_root / name
            if target.exists():
                if target.is_symlink() or target.read_text() != text:
                    raise AuthorityDenied("live startup blocked: installed_artifact_pin_changed")
            else:
                write_file(source_root, name, text)
        engineer = ArtifactEngineer(database, clock, source_root, ledger)
        runtime = SimpleNamespace(database=database, clock=clock, portfolio_id=config.scope.portfolio_id,
                                  ledger=ledger, execution=execution, scheduler=scheduler, versions=versions,
                                  artifact_runtime=artifact_runtime, budget=budget, secretary=secretary,
                                  office=office, engineer=engineer, handlers={}, model_config=model_config,
                                  model_handlers=None, public_feed=None, private_transport=transport,
                                  commission=commission, lifecycle=lifecycle, authorization_id=authorization_id,
                                  deployment_id=config.scope.deployment_id, live_enabled=True,
                                  paid_calls_enabled=False, config=config)
        binding = ProtectedLiveDeploymentBinding(runtime, protected_owner, commission=commission)
        runtime.protected_deployment = binding
        runtime.prepare_runtime, runtime.runtime_ready = binding.prepare, binding.ready
        runtime.public_feed = PublicLiveFeed(execution, [runtime.portfolio_id], [config.scope.symbol],
                                            transport=LivePublicTextTransport(profile.proxy_url),
                                            interval_seconds=config.public_poll_interval_seconds)
        return runtime
    except BaseException:
        database.close()
        raise


def live_startup_prerequisites(path: Path, *, config_path: Path | None = None,
                               protected_owner: Path | None = None) -> dict:
    """Static dashboard prerequisite projection; never reads exchange credentials.

    Full native/readiness/financial admission is repeated by the actual CLI
    before transport construction and by the exclusive live service afterward.
    """
    try:
        if protected_owner is None:
            raise AuthorityDenied("protected_owner_commission_required")
        config = load_live_runtime_config(config_path or protected_owner / "live-config.json")
        if not config.live_enabled:
            raise AuthorityDenied("owner_live_enablement_missing")
        if not config.commission_sha256 or not config.scope:
            raise AuthorityDenied("exact_owner_live_scope_required")
        commission = PinnedLiveCommission(protected_owner, config.commission_sha256)
        profile = commission.load()
        from trade_graph.kernel.live_commission import assert_live_process_boundary

        assert_live_process_boundary(protected_owner, profile.runtime_manifest_sha256)
        if path.is_symlink() or not path.is_file():
            raise AuthorityDenied("separate_live_database_required")
        return {"ready": True, "mode": "live", "status": "requires_final_service_admission",
                "credentialed_verification": "independently reviewed; repeated before startup"}
    except (AuthorityDenied, ValueError, OSError):
        return {"ready": False, "mode": "live", "status": "blocked",
                "reason": "protected owner commission, live allocation/authorization and actual host/account "
                          "evidence required"}


class _DashboardBroker:
    """Serving a dashboard cannot acquire native transport capabilities."""

    fee_reserve_rate = Decimal("0.008")

    def __getattr__(self, name):
        if name in {"submit", "cancel", "balances", "open_orders", "fills_since", "order_status", "capabilities"}:
            async def refuse(*_args, **_kwargs):
                raise LiveDisabled("private effects belong to the commissioned exclusive live service")
            return refuse
        raise AttributeError(name)


def assemble_live_dashboard_runtime(path: Path, *, portfolio_id: str | None = None,
                                    config: LiveRuntimeConfig | None = None,
                                    protected_owner: Path | None = None):
    """Project the scoped live financial journal without keys, clients or workers.

    Owner controls can persist their explicit pause/management requests. The
    exclusive protected service, when separately commissioned, applies native
    reconciliation/cancellation. Merely viewing this dashboard performs none.
    """
    if protected_owner is None:
        raise AuthorityDenied("live dashboard requires protected owner scope")
    config = config or load_live_runtime_config(protected_owner / "live-config.json")
    if config.scope is None or portfolio_id not in {None, config.scope.portfolio_id}:
        raise AuthorityDenied("live dashboard requires exact owner portfolio scope")
    protected = load_live_runtime_config(protected_owner / "live-config.json")
    if protected != config or config.scope.venue != "kraken":
        raise AuthorityDenied("live dashboard configuration differs from owner scope")
    if path.is_symlink() or not path.is_file() or path.parent.stat().st_mode & 0o077:
        raise AuthorityDenied("live dashboard requires existing private financial storage")
    database = Database(path.resolve())
    try:
        row = database.execute("SELECT mode FROM portfolios WHERE portfolio_id=?",
                               (config.scope.portfolio_id,)).fetchone()
        if row is None or row[0] != "live" or database.execute(
            "SELECT 1 FROM portfolios WHERE mode!='live' LIMIT 1",
        ).fetchone():
            raise AuthorityDenied("live dashboard requires a dedicated live financial journal")
        clock = SystemClock()
        ledger = Ledger(database, clock)
        execution = Execution(database, ledger, clock, _DashboardBroker(), venue="kraken",
                              account_id=config.scope.account_id, mode="live")
        return SimpleNamespace(database=database, clock=clock, ledger=ledger, execution=execution,
                               portfolio_id=config.scope.portfolio_id, deployment_id=config.scope.deployment_id,
                               config=config, protected_owner=protected_owner)
    except BaseException:
        database.close()
        raise
