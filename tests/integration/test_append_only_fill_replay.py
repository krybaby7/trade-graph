"""Actual durable synthetic restatements never rewrite earlier financial knowledge."""

import copy
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.ledger import REPLAY_KIND, Ledger, _projection_sha256
from trade_graph.contracts.models import FillFeeRecord, FillRecord
from trade_graph.domain.clock import FrozenClock, utc_iso
from trade_graph.domain.errors import ValidationFailure

START = datetime(2026, 1, 1, tzinfo=UTC)


def _fill(name, *, minute, side="buy", price="100", quantity="1", fees=None):
    at = START + timedelta(minutes=minute)
    return FillRecord(
        venue="kraken",
        account_id="synthetic-account",
        trade_id=name,
        intent_id=name,
        symbol="BTC/USD",
        side=side,
        quantity=quantity,
        price=price,
        quote_cost=price,
        fee_amount="0",
        fee_asset="USD",
        liquidity="taker",
        filled_at_utc=at,
        fee_components=fees,
    )


def _base(path):
    database = Database(path)
    clock = FrozenClock(START)
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="USD", mode="live")
    ledger.deposit(portfolio, "USD", Decimal("1000"), "opening")
    clock.advance(4 * 60)
    buy = _fill("known-buy", minute=2, price="200")
    sale = _fill("known-sale", minute=3, side="sell", price="300")
    ledger.apply_fill(portfolio, buy, base_asset="BTC", quote_asset="USD")
    ledger.apply_fill(portfolio, sale, base_asset="BTC", quote_asset="USD")
    return database, clock, ledger, portfolio


def test_late_earlier_fill_appends_projection_and_balanced_delta_preserving_historical_view(tmp_path):
    database, clock, ledger, portfolio = _base(tmp_path / "late.sqlite")
    recorded_at = utc_iso(clock.now())
    before = ledger.books(portfolio)
    original_events = [dict(row) for row in database.execute("SELECT * FROM ledger_events ORDER BY sequence")]
    original_postings = [dict(row) for row in database.execute("SELECT * FROM journal_postings ORDER BY rowid")]
    assert before.cash_amount("USD") == Decimal("1100")
    assert before.lots[0].disposals[0].realized == Decimal("100")
    clock.advance(1)
    ledger.apply_late_fill(portfolio, _fill("late-buy", minute=1), base_asset="BTC", quote_asset="USD")
    after = ledger.books(portfolio)
    assert after.cash_amount("USD") == Decimal("1000")
    assert [lot.source_ref for lot in after.lots] == ["late-buy", "known-buy"]
    assert after.lots[0].disposals[0].realized == Decimal("200")
    assert after.lots[1].open_quantity() == Decimal("1")
    assert ledger.books(portfolio, recorded_at) == before
    events = [dict(row) for row in database.execute("SELECT * FROM ledger_events ORDER BY sequence")]
    assert events[: len(original_events)] == original_events
    assert [row["kind"] for row in events[-2:]] == ["fill", REPLAY_KIND]
    assert json.loads(events[-2]["payload_json"])["projection_deferred"] == "chronological_replay_v1"
    receipt = json.loads(events[-1]["payload_json"])
    assert receipt["previous_projection_sha256"] == _projection_sha256(before)
    assert receipt["current_projection_sha256"] == _projection_sha256(after)
    assert [item["event_id"] for item in receipt["source_manifest"]] == [row["event_id"] for row in events[:-1]]
    assert [dict(row) for row in database.execute("SELECT * FROM journal_postings ORDER BY rowid")][
        : len(original_postings)
    ] == original_postings
    assert ledger.journal_balanced(portfolio)
    assert len(after.groups) == len(before.groups) + 1
    database.close()
    database = Database(tmp_path / "late.sqlite")
    ledger = Ledger(database, clock)
    assert ledger.books(portfolio) == after
    assert ledger.books(portfolio, recorded_at) == before
    database.close()


def test_following_normal_and_second_late_fill_replay_complete_original_prefix(tmp_path):
    database, clock, ledger, portfolio = _base(tmp_path / "many.sqlite")
    clock.advance(1)
    ledger.apply_late_fill(portfolio, _fill("late-buy", minute=1), base_asset="BTC", quote_asset="USD")
    first_time = utc_iso(clock.now())
    first = ledger.books(portfolio)
    clock.advance(60)
    ledger.apply_fill(
        portfolio, _fill("second-sale", minute=5, side="sell", price="400"), base_asset="BTC", quote_asset="USD"
    )
    clock.advance(1)
    ledger.apply_late_fill(portfolio, _fill("earliest", minute=0.5, price="50"), base_asset="BTC", quote_asset="USD")
    books = ledger.books(portfolio)
    assert books.cash_amount("USD") == Decimal("1350")
    assert [lot.source_ref for lot in books.lots] == ["earliest", "late-buy", "known-buy"]
    assert [sum((item.realized for item in lot.disposals), Decimal("0")) for lot in books.lots] == [
        Decimal("250"),
        Decimal("300"),
        Decimal("0"),
    ]
    assert books.lots[-1].open_quantity() == 1
    assert ledger.books(portfolio, first_time) == first
    assert ledger.journal_balanced(portfolio)
    database.close()


def test_late_sell_refuses_impossible_native_cashflow_inventory_chronology_atomically(tmp_path):
    database, clock, ledger, portfolio = _base(tmp_path / "invalid.sqlite")
    before = copy.deepcopy(ledger.books(portfolio))
    rows = [tuple(row) for row in database.execute("SELECT * FROM ledger_events")]
    clock.advance(1)
    with pytest.raises(ValidationFailure, match="valid native sources"):
        ledger.apply_late_fill(
            portfolio, _fill("impossible", minute=1, side="sell"), base_asset="BTC", quote_asset="USD"
        )
    assert ledger.books(portfolio) == before
    assert [tuple(row) for row in database.execute("SELECT * FROM ledger_events")] == rows
    database.close()


def test_late_native_tie_refuses_without_finer_order_evidence(tmp_path):
    database, clock, ledger, portfolio = _base(tmp_path / "tie.sqlite")
    before = ledger.books(portfolio)
    clock.advance(1)
    with pytest.raises(ValidationFailure, match="precise tie"):
        ledger.apply_late_fill(portfolio, _fill("tied", minute=2), base_asset="BTC", quote_asset="USD")
    assert ledger.books(portfolio) == before
    database.close()


@pytest.mark.parametrize("tamper", ["manifest", "prior", "current", "delta", "version", "extra"])
def test_replay_reader_independently_refuses_changed_source_projection_or_adjustment(tmp_path, tamper):
    database, clock, ledger, portfolio = _base(tmp_path / "tamper.sqlite")
    clock.advance(1)
    ledger.apply_late_fill(portfolio, _fill("late", minute=1), base_asset="BTC", quote_asset="USD")
    rows = [dict(row) for row in database.execute("SELECT * FROM ledger_events ORDER BY sequence")]
    payload = json.loads(rows[-1]["payload_json"])
    if tamper == "manifest":
        payload["source_manifest"][0]["sha256"] = "0" * 64
    elif tamper == "prior":
        payload["previous_projection_sha256"] = "0" * 64
    elif tamper == "current":
        payload["current_projection_sha256"] = "0" * 64
    elif tamper == "delta":
        payload["postings"][0]["amount"] = "999"
    elif tamper == "version":
        payload["projection_version"] = "new-unapproved"
    else:
        payload["extra"] = True
    rows[-1]["payload_json"] = json.dumps(payload)
    with pytest.raises(ValidationFailure):
        ledger._replay_rows(rows)
    database.close()


def test_dangling_or_interrupted_deferred_fill_is_never_silently_ignored(tmp_path):
    database, clock, ledger, portfolio = _base(tmp_path / "dangling.sqlite")
    clock.advance(1)
    ledger.apply_late_fill(portfolio, _fill("late", minute=1), base_asset="BTC", quote_asset="USD")
    rows = [dict(row) for row in database.execute("SELECT * FROM ledger_events ORDER BY sequence")]
    with pytest.raises(ValidationFailure, match="missing.*receipt"):
        ledger._replay_rows(rows[:-1])
    with pytest.raises(ValidationFailure, match="immediate"):
        ledger._replay_rows(rows[:-1] + [rows[0]])
    database.close()


def test_signed_fee_late_fill_is_replayed_with_original_sources_and_no_extra_cash_fee(tmp_path):
    database, clock, ledger, portfolio = _base(tmp_path / "fee-late.sqlite")
    clock.advance(1)
    at = START + timedelta(minutes=1)
    components = (
        FillFeeRecord(asset="USD", amount="-1", source_ref="native-credit", effective_at_utc=at),
        FillFeeRecord(asset="BTC", amount="0.01", source_ref="native-base-charge", effective_at_utc=at),
    )
    fill = _fill("late", minute=1, fees=components)
    ledger.apply_late_fill(portfolio, fill, base_asset="BTC", quote_asset="USD")
    books = ledger.books(portfolio)
    assert books.cash_amount("USD") == Decimal("1001")
    assert sum((lot.open_quantity() for lot in books.lots), Decimal("0")) == Decimal("0.99")
    assert ledger.journal_balanced(portfolio)
    retained = json.loads(
        database.execute(
            "SELECT payload_json FROM ledger_events WHERE external_ref='kraken:synthetic-account:late'"
        ).fetchone()[0]
    )
    assert retained["fill"]["fee_components"][0]["source_ref"] == "native-credit"
    database.close()


@pytest.mark.parametrize("precision", [3, 50])
def test_correction_uses_protected_context_and_not_ambient_rounding(tmp_path, precision):
    database, clock, ledger, portfolio = _base(tmp_path / "precision.sqlite")
    clock.advance(1)
    with localcontext() as context:
        context.prec = precision
        ledger.apply_late_fill(portfolio, _fill("late", minute=1), base_asset="BTC", quote_asset="USD")
        assert ledger.books(portfolio).cash_amount("USD") == Decimal("1000")
    database.close()


def test_complete_execution_history_replays_late_fact_and_restart_never_resubmits(tmp_path):
    import asyncio

    from tests.integration.test_execution_reconciliation_ordering import HistoryBroker, _intent

    from trade_graph.application.execution import Execution
    from trade_graph.contracts.models import OrderLookupResult

    database = Database(tmp_path / "execution.sqlite")
    clock = FrozenClock(START)
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="USD")
    ledger.deposit(portfolio, "USD", Decimal("1000"), "opening")
    clock.advance(60 * 60)
    broker = HistoryBroker()
    execution = Execution(database, ledger, clock, broker)
    buy = _intent(database, clock, portfolio, "known-buy")
    sale = _intent(database, clock, portfolio, "known-sale", side="sell")
    late = _intent(database, clock, portfolio, "late", status="CANCELLED")

    def owned(name, minute, side="buy", price="100"):
        return _fill(name, minute=minute, side=side, price=price).model_copy(
            update={"venue": "paper", "account_id": "paper"}
        )

    broker.fills = [owned(buy.intent_id, 2, price="200"), owned(sale.intent_id, 3, side="sell", price="300")]
    broker.statuses = {
        buy.client_order_id: OrderLookupResult(status="filled", filled_quantity="1"),
        sale.client_order_id: OrderLookupResult(status="filled", filled_quantity="1"),
    }
    asyncio.run(execution.reconcile())
    before = ledger.books(portfolio)
    clock.advance(1)
    broker.fills.insert(0, owned(late.intent_id, 1))
    asyncio.run(execution.reconcile())
    after = ledger.books(portfolio)
    assert after.cash_amount("USD") == Decimal("1000")
    assert after.lots[0].disposals[0].realized == Decimal("200")
    assert before.lots[0].disposals[0].realized == Decimal("100")
    assert database.execute("SELECT count(*) FROM fills").fetchone()[0] == 3
    assert database.execute("SELECT count(*) FROM ledger_events WHERE kind=?", (REPLAY_KIND,)).fetchone()[0] == 1
    database.close()
    database = Database(tmp_path / "execution.sqlite")
    ledger = Ledger(database, clock)
    execution = Execution(database, ledger, clock, broker)
    broker.calls.clear()
    asyncio.run(execution.startup())
    assert ledger.books(portfolio) == after
    assert not any(method == "submit" for method, _ in broker.calls)
    assert database.execute("SELECT count(*) FROM ledger_events WHERE kind=?", (REPLAY_KIND,)).fetchone()[0] == 1
    database.close()


def test_complete_source_prefix_bound_is_refused_without_partial_restatement(tmp_path, monkeypatch):
    import trade_graph.application.ledger as module

    database, clock, ledger, portfolio = _base(tmp_path / "bounded.sqlite")
    before = ledger.books(portfolio)
    monkeypatch.setattr(module, "MAX_REPLAY_ROWS", 3)
    with pytest.raises(ValidationFailure, match="protected source bounds"):
        ledger.apply_late_fill(portfolio, _fill("late", minute=1), base_asset="BTC", quote_asset="USD")
    assert ledger.books(portfolio) == before
    assert database.execute("SELECT count(*) FROM ledger_events").fetchone()[0] == 3
    database.close()


def test_failed_adjustment_write_rolls_back_both_new_original_and_correction(tmp_path):
    import sqlite3

    database, clock, ledger, portfolio = _base(tmp_path / "crash.sqlite")
    before = ledger.books(portfolio)
    rows = [tuple(row) for row in database.execute("SELECT * FROM ledger_events")]
    database.execute("""CREATE TRIGGER synthetic_replay_write_failure BEFORE INSERT ON journal_postings
                     BEGIN SELECT RAISE(ABORT,'synthetic correction failure'); END""")
    clock.advance(1)
    with pytest.raises(sqlite3.IntegrityError, match="synthetic correction failure"):
        ledger.apply_late_fill(portfolio, _fill("late", minute=1), base_asset="BTC", quote_asset="USD")
    assert ledger.books(portfolio) == before
    assert [tuple(row) for row in database.execute("SELECT * FROM ledger_events")] == rows
    database.close()


def test_late_future_fill_and_regressed_record_clock_refuse_before_financial_mutation(tmp_path):
    database, clock, ledger, portfolio = _base(tmp_path / "time.sqlite")
    before = ledger.books(portfolio)
    with pytest.raises(ValidationFailure, match="bounded native time"):
        ledger.apply_late_fill(portfolio, _fill("future", minute=5), base_asset="BTC", quote_asset="USD")
    clock.advance(-60)
    with pytest.raises(ValidationFailure, match="strictly follow"):
        ledger.apply_late_fill(portfolio, _fill("late", minute=1), base_asset="BTC", quote_asset="USD")
    clock.advance(60)
    assert ledger.books(portfolio) == before
    database.close()


def test_equal_record_time_refuses_and_preserves_previous_asof_reference(tmp_path):
    database, clock, ledger, portfolio = _base(tmp_path / "equal.sqlite")
    reference_at = utc_iso(clock.now())
    original = ledger.books(portfolio, reference_at)
    with pytest.raises(ValidationFailure, match="strictly follow"):
        ledger.apply_late_fill(portfolio, _fill("late", minute=1), base_asset="BTC", quote_asset="USD")
    assert ledger.books(portfolio, reference_at) == original
    assert database.execute("SELECT count(*) FROM ledger_events").fetchone()[0] == 3
    database.close()


@pytest.mark.parametrize("bound", ["rows", "bytes"])
def test_overbound_source_refuses_before_any_full_fetch_or_reconstruction(tmp_path, monkeypatch, bound):
    import trade_graph.application.ledger as module

    database, clock, ledger, portfolio = _base(tmp_path / "preflight.sqlite")
    clock.advance(1)
    if bound == "rows":
        monkeypatch.setattr(module, "MAX_REPLAY_ROWS", 3)
    else:
        monkeypatch.setattr(module, "MAX_REPLAY_SOURCE_BYTES", 1)

    def refuse_reconstruction(*_args):
        raise AssertionError("oversized source must be refused before replay")

    monkeypatch.setattr(ledger, "_replay_rows", refuse_reconstruction)
    with pytest.raises(ValidationFailure, match="protected source bounds"):
        ledger.apply_late_fill(portfolio, _fill("late", minute=1), base_asset="BTC", quote_asset="USD")
    assert database.execute("SELECT count(*) FROM ledger_events").fetchone()[0] == 3
    database.close()
