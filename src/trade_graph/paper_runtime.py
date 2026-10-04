"""Single-host paper assembly from protected local configuration and installed artifacts."""

from __future__ import annotations

import json
import os
import stat
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict, Field

from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.adapters.engineering.artifact_files import write_file
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.activation import VersionController
from trade_graph.application.artifact_runtime import ArtifactRuntime
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.engineer import ArtifactEngineer
from trade_graph.application.execution import Execution
from trade_graph.application.leader import LeaderOffice
from trade_graph.application.ledger import Ledger
from trade_graph.application.scheduler import Scheduler
from trade_graph.application.secretary import Secretary
from trade_graph.contracts.models import PriceCard
from trade_graph.domain.clock import SystemClock
from trade_graph.domain.errors import AuthorityDenied


class PaperRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    models: dict | None = None
    price_cards: list[PriceCard] = Field(default_factory=list, max_length=50)
    public_data_enabled: bool = False
    paper_symbols: list[str] = Field(default_factory=lambda: ["BTC/USD", "ETH/USD"], min_length=1, max_length=10)
    tick_interval_seconds: float = Field(default=1, gt=0, le=60, allow_inf_nan=False)
    public_poll_interval_seconds: int = Field(default=10, ge=1, le=3600)


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


def _private_directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("private runtime directory cannot be a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_mode & 0o077:
        raise ValueError("runtime storage requires a private directory with mode 0700")


def assemble_paper_runtime(path: Path, *, portfolio_id: str | None = None,
                           config: PaperRuntimeConfig | None = None, clock=None,
                           api_keys=None, transport=None, protected_owner: Path | None = None):
    """Construct paper services, never fund budgets or enable persisted owner permissions."""
    config = config or PaperRuntimeConfig()
    if path.is_symlink() or not path.is_file():
        raise ValueError("database does not exist; initialize a paper account first")
    path = path.resolve()
    _private_directory(path.parent)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("paper database must be a regular file")
    database = Database(path)
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
        if not set(config.paper_symbols) <= set(policy.allowed_symbols):
            raise ValueError("public-data symbols exceed the persisted owner policy")
        feed = None
        if config.public_data_enabled:
            from trade_graph.adapters.market.paper_feed import PublicPaperFeed

            maintenance_ids = [item[0] for item in database.execute(
                "SELECT portfolio_id FROM portfolios WHERE mode = 'paper'",
            )]
            feed = PublicPaperFeed(execution, maintenance_ids, config.paper_symbols,
                                   interval_seconds=config.public_poll_interval_seconds)
        runtime = SimpleNamespace(database=database, clock=clock, portfolio_id=pid, ledger=ledger,
                               execution=execution, scheduler=scheduler, versions=versions,
                               artifact_runtime=artifact_runtime, budget=budget, secretary=secretary,
                               office=office, engineer=engineer, handlers=handlers, public_feed=feed,
                               model_handlers=model_handlers,
                               model_config=model_config if paid else None,
                               prepare_runtime=None, runtime_ready=None,
                               deployment_id=model_config.deployment_id if model_config else "deployment",
                               paid_calls_enabled=paid, live_enabled=False, config=config)
        if protected_owner is not None:
            from trade_graph.application.deployment_runtime import ProtectedDeploymentBinding

            protected_keys = api_keys if api_keys is not None else {
                "openai": os.environ.get("OPENAI_API_KEY", ""),
                "anthropic": os.environ.get("ANTHROPIC_API_KEY", ""),
            }
            binding = ProtectedDeploymentBinding(runtime, protected_owner, api_keys=protected_keys, transport=transport)
            runtime.protected_deployment = binding
            runtime.prepare_runtime, runtime.runtime_ready = binding.prepare, binding.ready
        return runtime
    except BaseException:
        database.close()
        raise
