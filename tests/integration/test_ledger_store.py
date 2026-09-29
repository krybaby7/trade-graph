from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trade_graph.adapters.persistence.backup import backup_database, restore_database
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import FillRecord
from trade_graph.domain.clock import FrozenClock, utc_iso
from trade_graph.domain.errors import DuplicateRecord


def _fill(trade_id: str, **kwargs) -> FillRecord:
    payload = dict(
        venue="sim",
        account_id="paper",
        trade_id=trade_id,
        intent_id=None,
        symbol="TEST/EUR",
        side="buy",
        quantity="1",
        price="40",
        fee_amount="0.40",
        fee_asset="EUR",
        liquidity="taker",
        filled_at_utc=datetime(2026, 1, 1, tzinfo=UTC),
    )
    payload.update(kwargs)
    return FillRecord.model_validate(payload)


def test_durable_fixture_restart_duplicate_and_backup(tmp_path) -> None:
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    path = tmp_path / "books.sqlite"
    database = Database(path)
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="EUR")
    opened = utc_iso(clock.now())
    ledger.deposit(portfolio, "EUR", Decimal("100"), "open")
    clock.advance(60)
    ledger.apply_fill(portfolio, _fill("buy"), base_asset="TEST", quote_asset="EUR")
    clock.advance(60)
    ledger.observe_mark(portfolio, "TEST", Decimal("44"), "EUR", source="fixture")
    marked = utc_iso(clock.now())
    view = ledger.equity(portfolio, marked)
    assert view.equity == Decimal("103.60")
    assert view.unrealized == Decimal("3.60")
    clock.advance(60)
    ledger.apply_fill(
        portfolio,
        _fill("sell", side="sell", price="44", fee_amount="0.44"),
        base_asset="TEST",
        quote_asset="EUR",
    )
    sold = utc_iso(clock.now())
    result = ledger.performance(portfolio, opened, sold)
    assert result.trading_pnl == Decimal("3.16")
    assert ledger.journal_balanced(portfolio)
    with pytest.raises(DuplicateRecord):
        ledger.apply_fill(portfolio, _fill("buy"), base_asset="TEST", quote_asset="EUR")
    assert ledger.equity(portfolio, sold).equity == Decimal("103.16")
    database.close()

    reopened = Ledger(Database(path), clock)
    assert reopened.equity(portfolio, sold).equity == Decimal("103.16")
    assert reopened.performance(portfolio, opened, sold).trading_pnl == Decimal("3.16")
    assert reopened.activity_intact()
    reopened.database.close()

    backup = tmp_path / "books.backup"
    backup_database(path, backup)
    restored = tmp_path / "restored.sqlite"
    restore_database(backup, restored)
    again = Ledger(Database(restored), clock)
    assert again.equity(portfolio, sold).equity == Decimal("103.16")
    again.database.close()

    raw = backup.read_bytes()
    backup.write_bytes(raw[:-20])
    with pytest.raises(ValueError, match="checksum"):
        restore_database(backup, tmp_path / "bad.sqlite")
