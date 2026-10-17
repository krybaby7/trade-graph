"""Single-host paper assembly from protected local configuration and installed artifacts."""

from __future__ import annotations

import json
import os
import stat
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.adapters.engineering.artifact_files import write_file
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.activation import VersionController
from trade_graph.application.artifact_runtime import ArtifactRuntime
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.candle_readiness import (
    DEFAULT_DECISION_CANDLE_WAIT_SECONDS,
    MAX_DECISION_CANDLE_WAIT_SECONDS,
)
from trade_graph.application.engineer import ArtifactEngineer
from trade_graph.application.execution import Execution
from trade_graph.application.leader import LeaderOffice
from trade_graph.application.ledger import Ledger
from trade_graph.application.scheduler import Scheduler
from trade_graph.application.secretary import Secretary
from trade_graph.contracts.models import PriceCard
from trade_graph.domain.clock import SystemClock
from trade_graph.domain.errors import AuthorityDenied
from trade_graph.kernel.authority import owner_scope_covers


class PriceCollectorConfig(BaseModel):
    """The in-service price collector (W6-D, D222). It runs only with ``public_data_enabled``; ``enabled`` false keeps
    the older behaviour (the hourly BTC/ETH refresh into ``hourly_candles`` and no candle file in the service)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    one_minute_symbols: list[str] = Field(default_factory=lambda: ["BTC/USD", "ETH/USD"], max_length=50)
    one_minute_every_seconds: int = Field(default=300, ge=60, le=43_200)
    ranking_every_hours: int = Field(default=1, ge=1, le=24)
    wide_every_hours: int = Field(default=24, ge=1, le=60)
    hourly_scan: bool = True
    chunk_requests: int = Field(default=30, ge=1, le=500)

    def settings(self):
        from trade_graph.application.price_collector import CollectorSettings

        return CollectorSettings(one_minute_symbols=tuple(self.one_minute_symbols),
                                 one_minute_every_seconds=self.one_minute_every_seconds,
                                 ranking_every_hours=self.ranking_every_hours, wide_every_hours=self.wide_every_hours,
                                 hourly_scan=self.hourly_scan, chunk_requests=self.chunk_requests)


class PaperRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    automatic_schedule: bool = True
    optimisation_manual_only: bool = False
    schedule_intervals: dict[str, int] | None = None
    optimisation_deadline_seconds: int | None = Field(default=None, gt=0)
    models: dict | None = None
    price_cards: list[PriceCard] = Field(default_factory=list, max_length=50)
    public_data_enabled: bool = False
    public_history_enabled: bool = True
    public_history_hours: int = Field(default=168, ge=1, le=719)
    public_history_interval_seconds: int = Field(default=3600, ge=1)
    # 0 never waits for the just-closed hour; snapshots still say whether it is stored.
    decision_candle_wait_seconds: int = Field(default=DEFAULT_DECISION_CANDLE_WAIT_SECONDS, ge=0,
                                              le=MAX_DECISION_CANDLE_WAIT_SECONDS)
    paper_symbols: list[str] = Field(default_factory=lambda: ["BTC/USD", "ETH/USD"], min_length=1, max_length=10)
    tick_interval_seconds: float = Field(default=1, gt=0, le=60, allow_inf_nan=False)
    public_poll_interval_seconds: int = Field(default=10, ge=1, le=3600)
    # W6-D: the price collector keeps the candle file beside the database current (D222, D224).
    price_collector: PriceCollectorConfig = Field(default_factory=PriceCollectorConfig)
    # W6-D (D223): the event engine (T35) replaces the fixed role timers. Off until the owner turns it on at step L3.
    event_engine: bool = False

    @model_validator(mode="after")
    def _engine_needs_fresh_candles(self):
        # The price watcher's candle and indicator alarms and the day start's scan read the collector's candle file.
        if self.event_engine and not (self.public_data_enabled and self.price_collector.enabled):
            raise ValueError("the event engine needs public data and the price collector")
        return self

    @field_validator("schedule_intervals", mode="before")
    @classmethod
    def _schedule_intervals(cls, value):
        if value is None:
            return value
        allowed = {"trader", "research", "learning", "leader", "optimisation"}
        if (type(value) is not dict or not set(value) <= allowed
                or any(type(interval) is not int or interval <= 0 for interval in value.values())):
            raise ValueError("supported departments and positive integer schedule intervals required")
        return value


def load_runtime_config(path: Path | None) -> PaperRuntimeConfig:
    if path is None:
        return PaperRuntimeConfig()
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("runtime configuration must be a private regular file with mode 0600")
    if path.stat().st_size > 262144:
        raise ValueError("runtime configuration exceeds the bounded input size")
    try:
        return PaperRuntimeConfig.model_validate_json(path.read_text())
    except (ValueError, OSError):
        # Pydantic errors include input values. Private configuration must not
        # leak those values into operator logs or model-visible error contexts.
        raise ValueError("invalid protected runtime configuration") from None


def installed_artifacts() -> dict[str, str]:
    return json.loads(files("trade_graph").joinpath("runtime_artifacts.json").read_text())


def subscription_market_transport(protected_owner: Path):
    from trade_graph.adapters.market.public import HttpxTextTransport
    from trade_graph.kernel.subscription_network import load_subscription_network_profile

    profile = load_subscription_network_profile(protected_owner)
    return HttpxTextTransport(timeout=5, proxy=profile.market_proxy_url, trust_env=False)


def collector_transports(protected_owner: Path | None):
    """The price collector's public transports (D222): pair list, OHLC and Ticker with their own response limits; in
    a protected deployment through the owner-pinned public-market proxy, like the quote feed."""
    from trade_graph.adapters.market.kraken_candles import OHLC_MAXIMUM_RESPONSE_BYTES, OHLC_TIMEOUT_SECONDS
    from trade_graph.adapters.market.kraken_catalog import CATALOG_MAXIMUM_RESPONSE_BYTES, CATALOG_TIMEOUT_SECONDS
    from trade_graph.adapters.market.kraken_ticker import TICKER_MAXIMUM_RESPONSE_BYTES, TICKER_TIMEOUT_SECONDS
    from trade_graph.adapters.market.public import HttpxTextTransport
    from trade_graph.application.price_collector import CollectorTransports

    proxy, trust_env = None, True
    if protected_owner is not None:
        from trade_graph.kernel.subscription_network import load_subscription_network_profile

        proxy, trust_env = load_subscription_network_profile(protected_owner).market_proxy_url, False

    def transport(timeout: float, size: int) -> HttpxTextTransport:
        return HttpxTextTransport(timeout, maximum_response_bytes=size, proxy=proxy, trust_env=trust_env)

    return CollectorTransports(catalog=transport(CATALOG_TIMEOUT_SECONDS, CATALOG_MAXIMUM_RESPONSE_BYTES),
                               ohlc=transport(OHLC_TIMEOUT_SECONDS, OHLC_MAXIMUM_RESPONSE_BYTES),
                               ticker=transport(TICKER_TIMEOUT_SECONDS, TICKER_MAXIMUM_RESPONSE_BYTES))


def _private_directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("private runtime directory cannot be a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_mode & 0o077:
        raise ValueError("runtime storage requires a private directory with mode 0700")


def assemble_paper_runtime(path: Path, *, portfolio_id: str | None = None,
                           config: PaperRuntimeConfig | None = None, clock=None,
                           api_keys=None, transport=None, protected_owner: Path | None = None,
                           market_database: Path | None = None, market_transports=None,
                           market_limiter=None, market_evidence_label: str = "live_public"):
    """Construct paper services, never fund budgets or enable persisted owner permissions.

    ``market_database`` is the installation's candle file (D224). With public data and the price collector enabled it
    is opened (created private when missing), the collector is built (``runtime.price_collector``) and the hourly
    features read it (D220). ``market_transports`` and ``market_limiter`` replace the real public transports and the
    process limiter (tests)."""
    config = config or PaperRuntimeConfig()
    if path.is_symlink() or not path.is_file():
        raise ValueError("database does not exist; initialize a paper account first")
    path = path.resolve()
    _private_directory(path.parent)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("paper database must be a regular file")
    database = Database(path)
    market_data = None
    try:
        if database.execute("SELECT 1 FROM portfolios WHERE mode != 'paper' LIMIT 1").fetchone():
            raise ValueError("paper service requires a database containing paper accounts only")
        if portfolio_id:
            row = database.execute("SELECT * FROM portfolios WHERE portfolio_id = ?", (portfolio_id,)).fetchone()
        else:
            row = database.execute(
                "SELECT * FROM portfolios WHERE mode = 'paper' ORDER BY created_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        if row is None or row["mode"] != "paper":
            raise ValueError("a persisted paper portfolio is required")
        clock = clock or SystemClock()
        ledger = Ledger(database, clock)
        execution = Execution(database, ledger, clock, PaperBroker(database, clock))
        scheduler = Scheduler(database, clock)
        versions = VersionController(database, clock)
        artifact_runtime = ArtifactRuntime(versions, scheduler)
        budget = BudgetGateway(database, clock)
        secretary = Secretary(execution, scheduler, artifact_runtime=artifact_runtime)
        office = LeaderOffice(execution, scheduler, budget)
        pid = row["portfolio_id"]
        baseline = installed_artifacts()
        versions.register_baseline(pid, "installed-r1-baseline", baseline)
        # The Engineer stages only installed allowlisted data, rather than
        # requiring access to an operator's source checkout or its credentials.
        source_root = path.parent / "installed-artifacts"
        _private_directory(source_root)
        for name, text in baseline.items():
            target = source_root / name
            if target.exists():
                if target.is_symlink() or target.read_text() != text:
                    raise ValueError("installed artifact staging differs from the protected package")
            else:
                write_file(source_root, name, text)
        engineer = ArtifactEngineer(database, clock, source_root, ledger)
        handlers = {}
        model_handlers = None
        paid = False
        model_config = None
        if config.models is not None:
            from trade_graph.application.runtime_models import RuntimeModelConfig, assemble_handlers

            model_config = RuntimeModelConfig.model_validate(config.models)
            with database.immediate():
                for card in config.price_cards:
                    if card.price_card_id not in model_config.approved_price_card_ids:
                        raise ValueError("runtime price card is outside the protected approved registry")
                    budget.seed_card(card)
            policy = execution.authority.active_policy()
            paid = model_config.paid_calls_enabled and policy.paid_calls_enabled
            if paid and protected_owner is None:
                model_handlers = assemble_handlers(
                    office, secretary, engineer, artifact_runtime, model_config,
                    workspace_root=path.parent / "engineering", api_keys=api_keys if api_keys is not None else {
                        "openai": os.environ.get("OPENAI_API_KEY", ""),
                        "anthropic": os.environ.get("ANTHROPIC_API_KEY", ""),
                    }, transport=transport,
                )
                handlers = model_handlers.handlers
        elif config.price_cards:
            raise ValueError("runtime price cards require a protected model registry")
        try:
            policy = execution.authority.active_policy()
        except AuthorityDenied:
            raise ValueError("persisted owner policy is required") from None
        # Under the owner's coin rule (T32) the feed may watch any pair the rule can include.
        if not all(owner_scope_covers(policy, symbol) for symbol in config.paper_symbols):
            raise ValueError("public-data symbols exceed the persisted owner policy")
        feed = None
        if config.public_data_enabled:
            from trade_graph.adapters.market.paper_feed import PublicPaperFeed

            maintenance_ids = [item[0] for item in database.execute(
                "SELECT portfolio_id FROM portfolios WHERE mode = 'paper'",
            )]
            extra = None
            if config.event_engine:
                # D223: day plans rest on the day's coins, so the feed quotes them too (one extra request a poll).
                from trade_graph.application.service_market_data import quoted_day_coins

                extra = quoted_day_coins(database, clock, maintenance_ids, execution=execution, ledger=ledger)
            feed = PublicPaperFeed(execution, maintenance_ids, config.paper_symbols,
                                   interval_seconds=config.public_poll_interval_seconds,
                                   transport=subscription_market_transport(protected_owner)
                                   if protected_owner is not None else None, extra_symbols=extra)
        if market_database is not None and config.public_data_enabled and config.price_collector.enabled:
            from trade_graph.application.service_market_data import activity_recorder, open_service_market_data

            portfolios = [item[0] for item in database.execute(
                "SELECT portfolio_id FROM portfolios WHERE mode = 'paper'")]
            market_data = open_service_market_data(
                Path(market_database), database=database, clock=clock, portfolio_ids=portfolios, ledger=ledger,
                paper_symbols=config.paper_symbols, settings=config.price_collector.settings(),
                transports=market_transports or collector_transports(protected_owner), limiter=market_limiter,
                evidence_label=market_evidence_label, record=activity_recorder(ledger, [pid]))
            execution.hourly_history = market_data.history
        runtime = SimpleNamespace(database=database, clock=clock, portfolio_id=pid, ledger=ledger,
                               execution=execution, scheduler=scheduler, versions=versions,
                               artifact_runtime=artifact_runtime, budget=budget, secretary=secretary,
                               office=office, engineer=engineer, handlers=handlers, public_feed=feed,
                               model_handlers=model_handlers,
                               model_config=model_config if paid else None,
                               prepare_runtime=None, runtime_ready=None,
                               deployment_id=model_config.deployment_id if model_config else "deployment",
                               paid_calls_enabled=paid, live_enabled=False, config=config,
                               market_data=market_data,
                               price_collector=market_data.collector if market_data is not None else None)
        if protected_owner is not None:
            from trade_graph.application.deployment_runtime import ProtectedDeploymentBinding

            # Installed protected deployments obtain credentials from exact
            # owner-pinned readonly files. Ambient shell credentials cannot
            # expand the protected image's offline profile.
            protected_keys = api_keys if api_keys is not None else {}
            binding = ProtectedDeploymentBinding(runtime, protected_owner, api_keys=protected_keys, transport=transport)
            runtime.protected_deployment = binding
            runtime.prepare_runtime, runtime.runtime_ready = binding.prepare, binding.ready
        return runtime
    except BaseException:
        if market_data is not None:
            market_data.close()
        database.close()
        raise
