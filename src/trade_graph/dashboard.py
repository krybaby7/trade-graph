"""Owner dashboard assembly; serving never starts a service or external effect."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.adapters.persistence.db import Database
from trade_graph.api.auth import issue_session, role_for_token
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.domain.clock import SystemClock


def dashboard_runtime(path: Path, portfolio_id: str | None = None, *, config_path: Path | None = None,
                      mode: str = "paper", protected_owner: Path | None = None):
    from trade_graph.application.service_controller import ServiceController

    if mode == "live":
        from trade_graph.live_runtime import (
            assemble_live_dashboard_runtime,
            live_startup_prerequisites,
            load_live_runtime_config,
        )

        runtime = assemble_live_dashboard_runtime(
            path, portfolio_id=portfolio_id, protected_owner=protected_owner,
            config=load_live_runtime_config(config_path) if config_path else None,
        )

        subscription = _subscription_prerequisites(protected_owner)

        def prerequisites():
            result = live_startup_prerequisites(path, config_path=config_path, protected_owner=protected_owner)
            return {**subscription(), "paper_available": False, "live_available": result["ready"],
                    "live_reasons": [] if result["ready"] else [result["reason"]]}

        runtime.service_controller = ServiceController(runtime, config_path=config_path,
                                                       protected_owner=protected_owner, prerequisites=prerequisites)
        return runtime
    if mode != "paper":
        raise ValueError("choose paper or a separately commissioned protected live dashboard")
    prerequisites = None
    config = None
    if protected_owner is not None:
        if config_path is not None:
            raise ValueError("protected dashboard loads paper-config.json only from its owner directory")
        from trade_graph.application.deployment_runtime import _owner_bundle
        from trade_graph.kernel.deployment_image import read_owner_file
        from trade_graph.paper_runtime import PaperRuntimeConfig

        manifest, _, capability_key = _owner_bundle(protected_owner)
        manifest.assert_current()
        try:
            config = PaperRuntimeConfig.model_validate_json(
                read_owner_file(protected_owner, "paper-config.json", 262144))
        except FileNotFoundError:
            config = PaperRuntimeConfig()
        if config.models or config.price_cards or (protected_owner / "funded-paper-profile.json").exists():
            raise ValueError("subscription dashboard rejects API model routing, price cards and funded API profiles")
        subscription = _subscription_prerequisites(protected_owner)

        def prerequisites():
            from trade_graph.adapters.models.subscription import SubscriptionJournal

            return {**subscription(journal=SubscriptionJournal(database, clock)),
                    "paper_available": True, "live_available": False,
                    "live_reasons": ["Live startup requires separately commissioned protected owner configuration."]}
    path = path.resolve()
    if not path.is_file():
        raise ValueError("database does not exist; initialize a paper account first")
    if path.parent.stat().st_mode & 0o077:
        raise ValueError("dashboard database requires a private directory with mode 0700")
    database = Database(path)
    if portfolio_id:
        row = database.execute("SELECT portfolio_id, mode FROM portfolios WHERE portfolio_id = ?",
                               (portfolio_id,)).fetchone()
    else:
        row = database.execute(
            "SELECT portfolio_id, mode FROM portfolios WHERE mode = 'paper' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
    if row is None or row["mode"] != "paper":
        database.close()
        raise ValueError("a persisted paper portfolio is required")
    clock = SystemClock()
    ledger = Ledger(database, clock)
    execution = Execution(database, ledger, clock, PaperBroker(database, clock))
    runtime = SimpleNamespace(database=database, clock=clock, ledger=ledger, execution=execution,
                              portfolio_id=row["portfolio_id"], deployment_id="deployment", config=config,
                              subscription_provider=subscription().get("selected_provider")
                                  if protected_owner is not None else None)
    if protected_owner is not None:
        runtime.recovery_history = lambda: _recovery_history_projection(runtime, manifest, capability_key)
    runtime.service_controller = ServiceController(runtime, config_path=config_path,
                                                   protected_owner=protected_owner, prerequisites=prerequisites)
    return runtime



def _recovery_history_projection(runtime, manifest, capability_key):
    """Only authenticated protected receipts can label historical observation gaps."""
    from trade_graph.domain.errors import TradeGraphError
    from trade_graph.kernel.financial_service import ProtectedFinancialService
    from trade_graph.kernel.recovery_history import verified_recovery_history

    try:
        financial = ProtectedFinancialService(runtime.database, runtime.clock, runtime.execution,
            manifest=manifest, capability_key=capability_key)
        return verified_recovery_history(financial, runtime.portfolio_id)
    except (OSError, ValueError, TradeGraphError):
        return {"status": "UNAVAILABLE", "account_reset": False, "old_run_retained": True}


def _subscription_prerequisites(protected_owner):
    """Retain immutable admission while refreshing supported account metadata."""
    from trade_graph.application.subscription_profile import load_subscription_profile, subscription_profile_unchanged

    admission = load_subscription_profile(protected_owner)
    declared = admission.config is not None or admission.profile_sha256 is not None
    # A refused or unreadable existing profile still declares subscription use.
    # Capture existence once without opening the profile or following symlinks.
    try:
        (protected_owner / "subscription-profile.json").lstat()
        declared = True
    except FileNotFoundError:
        pass
    except OSError:
        declared = True

    def status(*, journal=None):
        from trade_graph.domain.errors import TradeGraphError

        current = subscription_profile_unchanged(protected_owner, admission.profile_sha256)
        observed = admission.status
        if current and admission.adapter is not None:
            try:
                observed = {**admission.status, **admission.adapter.public_status(journal=journal)}
            except (OSError, ValueError, RuntimeError, TradeGraphError):
                observed = {**admission.status, "ready": False, "available_subscription_routes": [],
                    "blockers": ["Subscription readiness refresh unavailable; management continues."]}
        ready = bool(current and admission.adapter and observed.get("ready"))
        reasons = [] if ready else list(observed.get("blockers", []))
        if not ready and observed.get("quota_admission_blocked"):
            for route in observed.get("quota_policy_routes", []):
                reasons.extend(reason for reason in route.get("blockers", []) if reason not in reasons)
                if route.get("provider_admission", {}).get("occupied"):
                    reasons.append("Subscription inference admission is active or unresolved; management continues.")
        if not current:
            reasons = ["Protected subscription profile changed; restart after owner review. Management continues."]
        return {"ai_available": ready, "selected_provider": admission.status.get("selected_provider"),
                "subscription_declared": declared,
                "available_subscription_routes": observed.get("available_subscription_routes", []),
                "reasons": reasons, "subscription": observed}

    return status

def owner_session_file(runtime, path: Path) -> Path:
    """Write an owner session once to a restricted local file, never to stdout."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise ValueError("owner session file must be private with mode 0600")
        try:
            record = json.loads(path.read_text())
        except (ValueError, OSError) as exc:
            raise ValueError("invalid private owner session file") from exc
        if role_for_token(runtime.database, record.get("session_token")) != "owner":
            raise ValueError("session file does not authenticate this database; choose a fresh private file")
        return path
    # Exclusive creation and no-follow prevent overwriting a file or a symlink.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        token, csrf = issue_session(runtime.database, runtime.clock, "owner")
        with os.fdopen(descriptor, "w") as target:
            json.dump({"session_token": token, "csrf_token": csrf, "role": "owner"}, target)
            target.write("\n")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path
