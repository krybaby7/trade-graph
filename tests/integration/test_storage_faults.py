"""A14/A16: native SQLite failures and process death against durable paper execution.

The venue uses a separate database so accepted orders/fills survive a local
storage outage. No application operation is mocked; faults come from SQLite
triggers, its authorizer, query_only, and a real page limit (SQLITE_FULL).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.adapters.persistence.backup import backup_database, restore_database
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.authority import seed_paper_authority
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import (
    AuthorizedOrderIntent,
    Decision,
    FillPage,
    InstrumentRules,
    Observation,
    OrderLookup,
    OrderLookupResult,
    SubmitResult,
)
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import UncertainExternal

NOW = datetime(2026, 1, 1, tzinfo=UTC)
CRASH_EXIT = 73
FAULT = "injected storage failure"


def _quote(clock: FrozenClock, name: str, size: str = "1") -> Observation:
    return Observation(
        observation_id=name, venue="paper", symbol="BTC/USD",
        event_time_utc=clock.now(), available_at_utc=clock.now(),
        bid="99", ask="100", bid_size=size, ask_size=size,
        kind="quote", source="synthetic-storage-fault",
    )


def _decision(stack: _Stack, name: str = "buy", **updates) -> Decision:
    return Decision.model_validate({
        "record_id": name, "created_at_utc": stack.clock.now(), "run_id": "storage-fault",
        "task_id": "task", "root_task_id": "root", "portfolio_id": stack.portfolio,
        "mode": "paper", "system_version_id": "v1", "trace_id": "synthetic",
        "action": "enter", "symbol": "BTC/USD", "quantity": {"amount": "1", "asset": "BTC"},
        "rationale": "synthetic durable execution", "invalidation": "synthetic price below 90",
        "horizon_seconds": 60, "strategy_id": "fixture", "snapshot_id": "obs:seed-quote",
        "mandate_revision": "1", "policy_revision": "1", **updates,
    })


class _Venue(PaperBroker):
    """Real paper matching/history with failures at the external transport boundary."""

    def __init__(self, database: Database, clock: FrozenClock, local_path: Path) -> None:
        super().__init__(database, clock)
        self.local_path = local_path
        self.submit_fault: str | None = None
        self.query_fault: str | None = None
        self.calls: list[str] = []

    async def submit(self, intent: AuthorizedOrderIntent) -> SubmitResult:
        self.calls.append("submit")
        # A separate connection cannot see an uncommitted intent or attempt.
        with sqlite3.connect(self.local_path) as durable:
            assert durable.execute(
                "SELECT state, client_order_id FROM order_intents WHERE intent_id = ?", (intent.intent_id,)
            ).fetchone() == ("SUBMITTING", intent.client_order_id)
            assert durable.execute(
                "SELECT COUNT(*) FROM order_attempts WHERE intent_id = ? AND kind = 'submit'", (intent.intent_id,)
            ).fetchone()[0] == 1
            assert durable.execute(
                "SELECT state FROM position_reservations WHERE intent_id = ?", (intent.intent_id,)
            ).fetchone() == ("held",)
            assert durable.execute(
                "SELECT status FROM outbox WHERE kind = 'submit' AND payload_ref = ?", (intent.intent_id,)
            ).fetchone() == ("started",)
        if self.submit_fault == "outage":
            raise UncertainExternal("broker unavailable before acknowledgement")
        if self.submit_fault == "die-before-accept":
            os._exit(CRASH_EXIT)
        result = await super().submit(intent)
        if self.submit_fault == "die-after-accept":
            os._exit(CRASH_EXIT)
        if self.submit_fault == "die-after-fill":
            self.clock.advance(1)
            assert len(self.match(_quote(self.clock, "venue-fill-before-death"))) == 1
            os._exit(CRASH_EXIT)
        if self.submit_fault == "lost-ack":
            raise UncertainExternal("broker accepted; acknowledgement lost")
        return result

    async def order_status(self, key: OrderLookup) -> OrderLookupResult:
        self.calls.append("status")
        if self.query_fault == "status":
            raise UncertainExternal("order history unavailable")
        return await super().order_status(key)

    async def fills_since(self, cursor: str | None) -> FillPage:
        self.calls.append("fills")
        if self.query_fault == "fills":
            raise UncertainExternal("fill history unavailable")
        if self.query_fault == "empty-fills":
            return FillPage(fills=[], next_cursor=None)
        return await super().fills_since(cursor)


@dataclass
class _Stack:
    database: Database
    venue_database: Database
    clock: FrozenClock
    portfolio: str
    ledger: Ledger
    execution: Execution
    broker: _Venue

    def reopen(self) -> None:
        local_path, venue_path = self.database.path, self.venue_database.path
        self.database.close()
        self.venue_database.close()
        self.database = Database(local_path)
        self.venue_database = Database(venue_path)
        self.ledger = Ledger(self.database, self.clock)
        self.broker = _Venue(self.venue_database, self.clock, local_path)
        self.execution = Execution(self.database, self.ledger, self.clock, self.broker)


@pytest.fixture
def stack(tmp_path: Path) -> Iterator[_Stack]:
    clock = FrozenClock(NOW)
    database = Database(tmp_path / "local.sqlite")
    venue_database = Database(tmp_path / "venue.sqlite")
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="USD")
    ledger.deposit(portfolio, "USD", Decimal("10000"), "opening-capital")
    seed_paper_authority(database, clock, portfolio)
    broker = _Venue(venue_database, clock, database.path)
    execution = Execution(database, ledger, clock, broker)
    execution.register_instrument(InstrumentRules(
        venue="paper", symbol="BTC/USD", base_asset="BTC", quote_asset="USD",
        price_increment="0.1", quantity_increment="0.001", min_quantity="0.001",
        min_notional="1", synthetic=True,
    ))
    execution.save_observation(_quote(clock, "seed-quote"))
    ledger.observe_mark(portfolio, "BTC", Decimal("99.5"), "USD", source="synthetic-storage-fault")
    result = _Stack(database, venue_database, clock, portfolio, ledger, execution, broker)
    yield result
    result.database.close()
    result.venue_database.close()


@contextmanager
def _write_fault(
    database: Database, table: str, *, operation: str = "INSERT", when: str = "1", rollback: bool = False,
) -> Iterator[None]:
    # Only test-controlled identifiers/expressions enter this DDL.
    action = "ROLLBACK" if rollback else "ABORT"
    database.execute(
        f"CREATE TRIGGER storage_fault BEFORE {operation} ON {table} "
        f"WHEN {when} BEGIN SELECT RAISE({action}, '{FAULT}'); END"
    )
    try:
        yield
    finally:
        database.execute("DROP TRIGGER storage_fault")


@contextmanager
def _connection_fault(
    database: Database, monkeypatch, mode: str, *, enabled: Callable[[], bool] = lambda: True,
) -> Iterator[list[int]]:
    """Configure the actual SQLite connections; never replace Database methods."""
    connect = sqlite3.connect
    hits: list[int] = []
    pages = database.execute("PRAGMA page_count").fetchone()[0]

    def authorizer(action, first, second, schema, source):
        if action == sqlite3.SQLITE_TRANSACTION and first == "COMMIT" and enabled():
            hits.append(action)
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def open_connection(path, *args, **kwargs):
        connection = connect(path, *args, **kwargs)
        if Path(path) == database.path:
            if mode == "commit":
                connection.set_authorizer(authorizer)
            elif mode == "readonly":
                connection.execute("PRAGMA query_only=ON")
            elif mode == "full":
                # Force a genuine SQLITE_FULL when a large decision needs new pages.
                assert connection.execute(f"PRAGMA max_page_count={pages + 1}").fetchone()[0] == pages + 1
            else:
                raise AssertionError(mode)
        return connection

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", open_connection)
        yield hits


def _counts(database: Database) -> dict[str, int]:
    tables = (
        "ledger_events", "journal_transactions", "journal_postings", "fills", "order_intents",
        "position_reservations", "outbox", "order_attempts", "decisions",
    )
    return {table: database.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}


def _reservation(stack: _Stack, intent: str) -> tuple[Decimal, str]:
    row = stack.database.execute(
        "SELECT amount, state FROM position_reservations WHERE intent_id = ?", (intent,)
    ).fetchone()
    return Decimal(row["amount"]), row["state"]


def _finances(stack: _Stack, *, cash: str = "10000", owned: str = "0", fills: int = 0) -> None:
    assert stack.ledger.books(stack.portfolio).cash_amount("USD") == Decimal(cash)
    assert stack.execution.owned_quantity(stack.portfolio, "BTC") == Decimal(owned)
    assert stack.ledger.journal_balanced(stack.portfolio)
    rows = stack.database.execute("SELECT document_json FROM fills").fetchall()
    assert len(rows) == fills
    # Every projected fill must have exactly one financial event/transaction, and vice versa.
    for table in ("ledger_events", "journal_transactions"):
        refs = stack.database.execute(f"SELECT external_ref FROM {table} WHERE kind = 'fill'").fetchall()
        assert len(refs) == fills
        assert {row["external_ref"] for row in refs} == {
            f"paper:paper:{json.loads(row['document_json'])['trade_id']}" for row in rows
        }


def _venue_fill(stack: _Stack, name: str = "venue-fill", size: str = "1") -> None:
    stack.clock.advance(1)
    assert len(stack.broker.match(_quote(stack.clock, name, size))) == 1


def _assert_recovered(stack: _Stack, intent: str, *, fills: int = 1) -> None:
    asyncio.run(stack.execution.startup())
    assert stack.execution.intent_state(intent) == "FILLED"
    assert _reservation(stack, intent) == (Decimal("0"), "released")
    _finances(stack, cash="9899.2", owned="1", fills=fills)
    before = _counts(stack.database)
    asyncio.run(stack.execution.startup())
    assert asyncio.run(stack.execution.dispatch()) == 0
    assert _counts(stack.database) == before
    assert stack.broker.submit_count == 0
    assert stack.venue_database.execute("SELECT COUNT(*) FROM broker_orders").fetchone()[0] == 1


@pytest.mark.parametrize("table", ["order_intents", "position_reservations", "outbox", "decisions"])
@pytest.mark.parametrize("rollback", [False, True], ids=["statement-abort", "transaction-rollback"])
def test_intent_transaction_failure_leaves_no_order_or_reservation(stack, table, rollback) -> None:
    before = _counts(stack.database)
    with _write_fault(stack.database, table, rollback=rollback):
        with pytest.raises(sqlite3.IntegrityError, match=FAULT):
            stack.execution.authorize(stack.portfolio, _decision(stack))
    stack.reopen()
    assert _counts(stack.database) == before
    assert asyncio.run(stack.execution.dispatch()) == 0
    assert stack.broker.calls == []
    assert stack.venue_database.execute("SELECT COUNT(*) FROM broker_orders").fetchone()[0] == 0
    _finances(stack)
    # Recovery can use the original decision: no uniqueness debris survived.
    intent = stack.execution.authorize(stack.portfolio, _decision(stack))
    assert _reservation(stack, intent) == (Decimal("100.8"), "held")


@pytest.mark.parametrize("mode,code", [("commit", sqlite3.SQLITE_AUTH), ("readonly", sqlite3.SQLITE_READONLY)])
def test_intent_commit_or_readonly_failure_prevents_external_submission(stack, monkeypatch, mode, code) -> None:
    before = _counts(stack.database)
    with _connection_fault(stack.database, monkeypatch, mode) as hits:
        with pytest.raises(sqlite3.DatabaseError) as error:
            stack.execution.authorize(stack.portfolio, _decision(stack))
        assert error.value.sqlite_errorcode == code
        if mode == "commit":
            assert hits
    stack.reopen()
    assert _counts(stack.database) == before
    assert asyncio.run(stack.execution.startup()) is None
    assert stack.broker.calls == []
    _finances(stack)


def test_real_sqlite_full_rolls_back_the_authorization_and_preserves_error(stack, monkeypatch) -> None:
    before = _counts(stack.database)
    with _connection_fault(stack.database, monkeypatch, "full"):
        with pytest.raises(sqlite3.OperationalError) as error:
            stack.execution.authorize(stack.portfolio, _decision(stack, rationale="synthetic " * 100000))
        assert error.value.sqlite_errorcode == sqlite3.SQLITE_FULL
    stack.reopen()
    assert _counts(stack.database) == before
    assert asyncio.run(stack.execution.dispatch()) == 0
    _finances(stack)
    assert stack.execution.authorize(stack.portfolio, _decision(stack))


@pytest.mark.parametrize("boundary", ["ledger_events", "journal_transactions", "journal_postings", "commit"])
def test_financial_write_failure_cannot_create_unjournaled_capital(stack, monkeypatch, boundary) -> None:
    before = _counts(stack.database)
    fault = (
        _connection_fault(stack.database, monkeypatch, "commit") if boundary == "commit"
        else _write_fault(stack.database, boundary, rollback=True)
    )
    with fault:
        with pytest.raises(sqlite3.DatabaseError):
            stack.ledger.deposit(stack.portfolio, "USD", Decimal("50"), "additional-capital")
    stack.reopen()
    assert _counts(stack.database) == before
    _finances(stack)
    stack.ledger.deposit(stack.portfolio, "USD", Decimal("50"), "additional-capital")
    _finances(stack, cash="10050")


@pytest.mark.parametrize("boundary", ["order_intents", "outbox", "order_attempts", "commit"])
def test_dispatch_failure_cannot_submit_before_the_attempt_commits(stack, monkeypatch, boundary) -> None:
    intent = stack.execution.authorize(stack.portfolio, _decision(stack))
    before = _counts(stack.database)
    fault = (
        _connection_fault(stack.database, monkeypatch, "commit") if boundary == "commit"
        else _write_fault(stack.database, boundary, operation="INSERT" if boundary == "order_attempts" else "UPDATE")
    )
    with fault:
        with pytest.raises(sqlite3.DatabaseError):
            asyncio.run(stack.execution.dispatch())
    assert stack.broker.calls == []
    assert stack.broker.submit_count == 0
    stack.reopen()
    assert _counts(stack.database) == before
    assert stack.execution.intent_state(intent) == "SUBMISSION_PENDING"
    assert _reservation(stack, intent) == (Decimal("100.8"), "held")
    _finances(stack)
    asyncio.run(stack.execution.startup())
    assert stack.broker.submit_count == 1
    _venue_fill(stack)
    stack.reopen()
    _assert_recovered(stack, intent)


@pytest.mark.parametrize("lost_ack", [False, True], ids=["acknowledgement", "unknown"])
@pytest.mark.parametrize("boundary", ["state-write", "commit"])
def test_response_storage_failure_recovers_the_accepted_order_without_resubmission(
    stack, monkeypatch, lost_ack, boundary,
) -> None:
    intent = stack.execution.authorize(stack.portfolio, _decision(stack))
    stack.broker.submit_fault = "lost-ack" if lost_ack else None
    state = "UNKNOWN" if lost_ack else "OPEN"
    fault = (
        _connection_fault(stack.database, monkeypatch, "commit", enabled=lambda: stack.broker.submit_count == 1)
        if boundary == "commit"
        else _write_fault(stack.database, "order_intents", operation="UPDATE", when=f"NEW.state = '{state}'")
    )
    with fault:
        with pytest.raises(sqlite3.DatabaseError):
            asyncio.run(stack.execution.dispatch())
    assert stack.broker.submit_count == 1
    assert stack.execution.intent_state(intent) == "SUBMITTING"
    assert _reservation(stack, intent) == (Decimal("100.8"), "held")
    _finances(stack)
    _venue_fill(stack)
    stack.reopen()
    _assert_recovered(stack, intent)


@pytest.mark.parametrize("boundary", [
    "ledger_events", "journal_transactions", "journal_postings", "fills",
    "position_reservations", "order_intents", "commit",
])
def test_fill_transaction_failure_never_exposes_completion_or_unjournaled_inventory(stack, monkeypatch, boundary):
    intent = stack.execution.authorize(stack.portfolio, _decision(stack))
    asyncio.run(stack.execution.dispatch())
    _venue_fill(stack)
    before = _counts(stack.database)
    fault = (
        _connection_fault(stack.database, monkeypatch, "commit") if boundary == "commit"
        else _write_fault(
            stack.database, boundary,
            operation="UPDATE" if boundary in {"position_reservations", "order_intents"} else "INSERT",
            rollback=True,
        )
    )
    with fault:
        with pytest.raises(sqlite3.DatabaseError) as error:
            asyncio.run(stack.execution.reconcile())
        if boundary != "commit":
            assert FAULT in str(error.value)
    assert _counts(stack.database) == before
    assert stack.execution.intent_state(intent) == "OPEN"
    assert _reservation(stack, intent) == (Decimal("100.8"), "held")
    _finances(stack)
    stack.reopen()
    _assert_recovered(stack, intent)


def test_partial_fill_reservation_survives_failed_remaining_fill_and_reopen(stack) -> None:
    intent = stack.execution.authorize(stack.portfolio, _decision(stack))
    asyncio.run(stack.execution.dispatch())
    stack.clock.advance(1)
    stack.execution.on_observation(_quote(stack.clock, "partial", "0.4"))
    assert stack.execution.intent_state(intent) == "PARTIALLY_FILLED"
    assert _reservation(stack, intent) == (Decimal("60.48"), "held")
    _finances(stack, cash="9959.68", owned="0.4", fills=1)
    _venue_fill(stack, "remaining", "0.6")
    with _write_fault(stack.database, "position_reservations", operation="UPDATE"):
        with pytest.raises(sqlite3.IntegrityError, match=FAULT):
            asyncio.run(stack.execution.reconcile())
    stack.reopen()
    assert stack.execution.intent_state(intent) == "PARTIALLY_FILLED"
    assert _reservation(stack, intent) == (Decimal("60.48"), "held")
    _finances(stack, cash="9959.68", owned="0.4", fills=1)
    _assert_recovered(stack, intent, fills=2)


def test_broker_outage_commits_unknown_and_empty_history_cannot_release_or_retry(stack) -> None:
    intent = stack.execution.authorize(stack.portfolio, _decision(stack))
    stack.broker.submit_fault = "outage"
    asyncio.run(stack.execution.dispatch())
    assert stack.broker.calls == ["submit"]
    assert stack.execution.intent_state(intent) == "UNKNOWN"
    stack.reopen()
    for _ in range(2):
        asyncio.run(stack.execution.startup())
        assert stack.execution.intent_state(intent) == "UNKNOWN"
        assert _reservation(stack, intent) == (Decimal("100.8"), "held")
        _finances(stack)
    assert stack.broker.submit_count == 0
    assert stack.database.execute("SELECT COUNT(*) FROM order_attempts").fetchone()[0] == 1
    assert stack.venue_database.execute("SELECT COUNT(*) FROM broker_orders").fetchone()[0] == 0


def test_terminal_broker_status_without_visible_fills_cannot_complete_or_release_unknown(stack) -> None:
    intent = stack.execution.authorize(stack.portfolio, _decision(stack))
    stack.broker.submit_fault = "lost-ack"
    asyncio.run(stack.execution.dispatch())
    _venue_fill(stack)
    stack.reopen()
    stack.broker.query_fault = "empty-fills"
    asyncio.run(stack.execution.startup())
    assert stack.execution.intent_state(intent) == "UNKNOWN"
    assert _reservation(stack, intent) == (Decimal("100.8"), "held")
    assert stack.broker.submit_count == 0
    _finances(stack)
    stack.reopen()
    _assert_recovered(stack, intent)


@pytest.mark.parametrize("query_fault", ["status", "fills"])
def test_startup_outage_keeps_unknown_and_pending_exposure_unsent_until_reconciliation(stack, query_fault):
    unknown = stack.execution.authorize(stack.portfolio, _decision(stack, "unknown"))
    # Both intents were authorized while execution was known. A newly uncertain
    # first submit must hold the already durable second intent until reconciliation.
    pending = stack.execution.authorize(stack.portfolio, _decision(stack, "pending"))
    stack.broker.submit_fault = "lost-ack"
    asyncio.run(stack.execution.dispatch())
    _venue_fill(stack)
    stack.reopen()
    stack.broker.query_fault = query_fault
    with pytest.raises(UncertainExternal, match="unavailable"):
        asyncio.run(stack.execution.startup())
    assert stack.execution.intent_state(unknown) == "UNKNOWN"
    assert stack.execution.intent_state(pending) == "SUBMISSION_PENDING"
    assert _reservation(stack, unknown) == (Decimal("100.8"), "held")
    assert _reservation(stack, pending) == (Decimal("100.8"), "held")
    assert stack.broker.submit_count == 0
    assert "submit" not in stack.broker.calls
    _finances(stack)
    stack.reopen()
    asyncio.run(stack.execution.startup())
    assert stack.execution.intent_state(unknown) == "FILLED"
    assert stack.execution.intent_state(pending) == "OPEN"
    assert stack.broker.calls.index("status") < stack.broker.calls.index("fills") < stack.broker.calls.index("submit")
    assert stack.broker.submit_count == 1
    assert _reservation(stack, unknown) == (Decimal("0"), "released")
    assert _reservation(stack, pending) == (Decimal("100.8"), "held")
    _finances(stack, cash="9899.2", owned="1", fills=1)


def _dispatch_and_die(local_path: str, venue_path: str, phase: str) -> None:
    clock = FrozenClock(NOW)
    database = Database(local_path)
    broker = _Venue(Database(venue_path), clock, Path(local_path))
    broker.submit_fault = phase
    execution = Execution(database, Ledger(database, clock), clock, broker)
    asyncio.run(execution.dispatch())
    raise AssertionError("child failed to reach the crash boundary")


@pytest.mark.parametrize("phase", ["die-before-accept", "die-after-accept", "die-after-fill"])
def test_process_death_recovers_durable_attempts_and_venue_history(stack, phase) -> None:
    intent = stack.execution.authorize(stack.portfolio, _decision(stack))
    # Abrupt exit skips all Python transaction cleanup, unlike a raised exception.
    child = subprocess.run(
        [sys.executable, "-c",
         "import sys; from tests.integration.test_storage_faults import _dispatch_and_die; "
         "_dispatch_and_die(*sys.argv[1:])",
         str(stack.database.path), str(stack.venue_database.path), phase],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=20,
    )
    assert child.returncode == CRASH_EXIT, child.stdout + child.stderr
    stack.clock.advance(1)
    stack.reopen()
    assert stack.execution.intent_state(intent) == "SUBMITTING"
    assert _reservation(stack, intent) == (Decimal("100.8"), "held")
    assert stack.database.execute("SELECT COUNT(*) FROM order_attempts").fetchone()[0] == 1
    _finances(stack)
    if phase == "die-before-accept":
        asyncio.run(stack.execution.startup())
        assert stack.execution.intent_state(intent) == "UNKNOWN"
        assert _reservation(stack, intent) == (Decimal("100.8"), "held")
        assert stack.broker.submit_count == 0
        assert stack.venue_database.execute("SELECT COUNT(*) FROM broker_orders").fetchone()[0] == 0
    else:
        if phase == "die-after-accept":
            _venue_fill(stack)
        _assert_recovered(stack, intent)


def _backup_and_destination(stack: _Stack, tmp_path: Path) -> tuple[Path, Path]:
    backup = tmp_path / "snapshot.sqlite"
    backup_database(stack.database.path, backup)
    destination = tmp_path / "restored.sqlite"
    prior = Database(destination)
    prior_ledger = Ledger(prior, stack.clock)
    portfolio = prior_ledger.create_portfolio(reporting_currency="USD", portfolio_id="prior-destination")
    prior_ledger.deposit(portfolio, "USD", Decimal("123"), "prior-capital")
    prior.close()
    return backup, destination


@pytest.mark.parametrize("corruption", ["freelist", "truncated"])
def test_restore_rejects_corrupt_backup_with_valid_checksum_and_preserves_destination(stack, tmp_path, corruption):
    backup, destination = _backup_and_destination(stack, tmp_path)
    original = destination.read_bytes()
    damaged = bytearray(backup.read_bytes())
    if corruption == "freelist":
        # Valid SQLite header/schema, but impossible freelist accounting. SQLite
        # returns an integrity diagnostic instead of raising on this corruption.
        damaged[36:40] = (1).to_bytes(4, "big")
        backup.write_bytes(damaged)
        with sqlite3.connect(backup) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
    else:
        backup.write_bytes(damaged[:-4096])
    backup.with_suffix(".sqlite.sha256").write_text(hashlib.sha256(backup.read_bytes()).hexdigest() + "\n")
    with pytest.raises(ValueError, match="integrity"):
        restore_database(backup, destination)
    assert destination.read_bytes() == original
    assert not list(tmp_path.glob(".restore-*"))


def test_restore_sqlite_copy_failure_preserves_destination(stack, tmp_path, monkeypatch) -> None:
    backup, destination = _backup_and_destination(stack, tmp_path)
    original = destination.read_bytes()
    connect = sqlite3.connect

    def readonly_copy_target(path, *args, **kwargs):
        if not kwargs.get("uri", False) and Path(path) != backup:
            return connect(Path(path).resolve().as_uri() + "?mode=ro", *args, uri=True, **kwargs)
        return connect(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", readonly_copy_target)
        with pytest.raises(sqlite3.OperationalError) as error:
            restore_database(backup, destination)
        assert error.value.sqlite_errorcode == sqlite3.SQLITE_READONLY
    assert destination.read_bytes() == original
    assert not list(tmp_path.glob(".restore-*"))


def test_restore_atomic_replace_failure_preserves_destination_and_uses_private_valid_copy(stack, tmp_path, monkeypatch):
    backup, destination = _backup_and_destination(stack, tmp_path)
    original = destination.read_bytes()
    staged: list[Path] = []

    def fail_replace(source, target):
        staged.append(Path(source))
        assert Path(target) == destination
        assert Path(source).stat().st_mode & 0o777 == 0o600
        assert Path(source).parent.stat().st_mode & 0o777 == 0o700
        assert Path(source).parent.parent == destination.parent
        with sqlite3.connect(source) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        raise OSError("injected atomic replacement failure")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_replace)
        with pytest.raises(OSError, match="atomic replacement"):
            restore_database(backup, destination)
    assert len(staged) == 1
    assert not staged[0].exists()
    assert destination.read_bytes() == original
    assert not list(tmp_path.glob(".restore-*"))


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_restore_refuses_destination_sidecars_without_deleting_prior_data(stack, tmp_path, suffix):
    backup, destination = _backup_and_destination(stack, tmp_path)
    original = destination.read_bytes()
    sidecar = Path(str(destination) + suffix)
    sidecar.write_bytes(b"synthetic pre-existing sidecar; offline inspection required")
    with pytest.raises(ValueError, match="sidecar"):
        restore_database(backup, destination)
    assert destination.read_bytes() == original
    assert sidecar.read_bytes() == b"synthetic pre-existing sidecar; offline inspection required"
    assert not list(tmp_path.glob(".restore-*"))


def test_restore_rechecks_sidecars_before_replacing_the_offline_destination(stack, tmp_path, monkeypatch):
    backup, destination = _backup_and_destination(stack, tmp_path)
    original = destination.read_bytes()
    sidecar = Path(str(destination) + "-wal")
    fsync = os.fsync

    def sidecar_appears(descriptor):
        sidecar.write_bytes(b"synthetic sidecar appeared during staging")
        fsync(descriptor)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", sidecar_appears)
        with pytest.raises(ValueError, match="sidecar"):
            restore_database(backup, destination)
    assert destination.read_bytes() == original
    assert sidecar.read_bytes() == b"synthetic sidecar appeared during staging"
    assert not list(tmp_path.glob(".restore-*"))


def test_restore_online_snapshot_is_private_and_survives_reopen_with_reservations(stack, tmp_path):
    intent = stack.execution.authorize(stack.portfolio, _decision(stack))
    backup, destination = _backup_and_destination(stack, tmp_path)
    # The source still has a live WAL connection; the backup is a consistent
    # online snapshot. Restore targets only the closed, offline destination.
    stack.ledger.deposit(stack.portfolio, "USD", Decimal("50"), "after-backup")
    restore_database(backup, destination)
    assert destination.stat().st_mode & 0o777 == 0o600
    assert not any(Path(str(destination) + suffix).exists() for suffix in ("-wal", "-shm", "-journal"))
    restored = Database(destination)
    try:
        ledger = Ledger(restored, stack.clock)
        assert ledger.books(stack.portfolio).cash_amount("USD") == Decimal("10000")
        assert ledger.journal_balanced(stack.portfolio)
        reservation = restored.execute(
            "SELECT amount, state FROM position_reservations WHERE intent_id = ?", (intent,)
        ).fetchone()
        assert (Decimal(reservation["amount"]), reservation["state"]) == (Decimal("100.8"), "held")
        assert restored.execute("SELECT COUNT(*) FROM outbox WHERE status = 'pending'").fetchone()[0] == 1
        assert restored.execute("PRAGMA integrity_check").fetchall()[0][0] == "ok"
    finally:
        restored.close()
    assert not list(tmp_path.glob(".restore-*"))
