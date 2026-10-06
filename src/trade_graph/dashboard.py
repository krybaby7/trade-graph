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

        def prerequisites():
            result = live_startup_prerequisites(path, config_path=config_path, protected_owner=protected_owner)
            return {"paper_available": False, "live_available": result["ready"], "ai_available": False,
                    "reasons": ["Subscription model routing and isolation require owner provisioning."],
                    "live_reasons": [] if result["ready"] else [result["reason"]]}

        runtime.service_controller = ServiceController(runtime, config_path=config_path,
                                                       protected_owner=protected_owner, prerequisites=prerequisites)
        return runtime
    if mode != "paper" or protected_owner is not None:
        raise ValueError("choose paper or a separately commissioned protected live dashboard")
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
                              portfolio_id=row["portfolio_id"], deployment_id="deployment")
    runtime.service_controller = ServiceController(runtime, config_path=config_path)
    return runtime


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
