"""A published reference date cannot backdate information learned by the runtime."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from trade_graph.adapters.persistence.db import Database
from trade_graph.api import financial
from trade_graph.application.ledger import Ledger
from trade_graph.domain.clock import FrozenClock, utc_iso
from trade_graph.kernel.books import FxRate, _convert, _flow_reporting


def _runtime(tmp_path):
    clock = FrozenClock(datetime(2026, 1, 2, tzinfo=UTC))
    database = Database(tmp_path / "fx-availability.sqlite")
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="EUR")
    return SimpleNamespace(database=database, ledger=ledger, clock=clock, portfolio_id=portfolio,
                           deployment_id="deployment")


def _reference(runtime, *, rate="0.90", rate_id="later-reference", observed=None, valid=None, retrieved=None):
    """Persist the same three distinct timestamps as the public FX adapter."""
    observed = observed or utc_iso(runtime.clock.now() - timedelta(days=1))
    valid = valid or observed
    retrieved = retrieved or utc_iso(runtime.clock.now())
    runtime.database.execute(
        """INSERT INTO fx_rates
        (rate_id, base, quote, rate, source, observed_at, valid_as_of, retrieved_at, kind, stale)
        VALUES (?, 'USD', 'EUR', ?, 'synthetic-reference-publication', ?, ?, ?, 'reference', 0)""",
        (rate_id, rate, observed, valid, retrieved),
    )
    return observed, valid, retrieved


def test_later_retrieved_reference_does_not_rewrite_historical_ledger_equity_or_performance(tmp_path):
    runtime = _runtime(tmp_path)
    opened = utc_iso(runtime.clock.now())
    runtime.ledger.deposit(runtime.portfolio_id, "USD", Decimal("10000"), "opening")
    runtime.clock.advance(60)
    observed, valid, retrieved = _reference(runtime)
    ended = utc_iso(runtime.clock.now())

    assert runtime.ledger._rates(opened) == []
    historical = runtime.ledger.equity(runtime.portfolio_id, opened)
    assert historical.equity is None and historical.provisional
    current = runtime.ledger.equity(runtime.portfolio_id, ended)
    assert current.equity == current.baseline == Decimal("9000")
    assert not current.provisional
    performance = runtime.ledger.performance(runtime.portfolio_id, opened, ended)
    assert performance.equity_start is None
    assert performance.equity_end == Decimal("9000")
    assert performance.provisional and performance.trading_pnl is None
    persisted = runtime.database.execute("SELECT * FROM fx_rates").fetchone()
    assert (persisted["observed_at"], persisted["valid_as_of"], persisted["retrieved_at"]) == (
        observed, valid, retrieved,
    )


def test_end_time_rate_list_cannot_value_a_flow_before_rate_availability(tmp_path):
    runtime = _runtime(tmp_path)
    started = utc_iso(runtime.clock.now())
    runtime.ledger.deposit(runtime.portfolio_id, "EUR", Decimal("100"), "opening")
    runtime.clock.advance(10)
    deposited = utc_iso(runtime.clock.now())
    runtime.ledger.deposit(runtime.portfolio_id, "USD", Decimal("100"), "later-external-flow")
    runtime.clock.advance(10)
    _reference(runtime)
    ended = utc_iso(runtime.clock.now())
    rates = runtime.ledger._rates(ended)

    assert len(rates) == 1 and rates[0].available_at == ended
    assert _convert(Decimal("100"), "USD", "EUR", rates, deposited) == (None, True)
    assert _flow_reporting(runtime.ledger.books(runtime.portfolio_id), started, ended, "EUR", rates) == (
        Decimal("0"), True,
    )
    result = runtime.ledger.performance(runtime.portfolio_id, started, ended)
    assert result.equity_start == Decimal("100")
    assert result.equity_end == Decimal("190")
    assert result.provisional and result.trading_pnl is None and result.economic_pnl is None
    dashboard = financial.overview(runtime)
    flow = dashboard["external_flows"]["items"][1]
    assert flow["reporting_amount"] is None and flow["provisional"]
    assert dashboard["external_flows"]["net_reporting"] is None


def test_dashboard_current_cash_valuation_retains_missing_opening_fx_and_historical_provisional_state(tmp_path):
    runtime = _runtime(tmp_path)
    opened = utc_iso(runtime.clock.now())
    runtime.ledger.deposit(runtime.portfolio_id, "USD", Decimal("10000"), "opening")
    runtime.clock.advance(60)
    observed, valid, retrieved = _reference(runtime)
    overview = financial.overview(runtime)
    opening = overview["external_flows"]["items"][0]

    assert opening["opening"] and opening["reporting_amount"] is None and opening["provisional"]
    assert financial._fx(runtime, "USD", "EUR", opened) is None
    valuation = financial._valuation(runtime, utc_iso(runtime.clock.now()))
    assert valuation["fx"][0]["retrieved_at"] == retrieved
    current = financial._equity(runtime.ledger.books(runtime.portfolio_id), "EUR", retrieved, valuation)
    assert current.equity == current.baseline == Decimal("9000")
    # Even an end-time valuation list cannot be reused to imply knowledge at opening.
    historical = financial._equity(runtime.ledger.books(runtime.portfolio_id, opened), "EUR", opened, valuation)
    assert historical.equity is None and historical.provisional
    assert (valuation["fx"][0]["observed_at"], valuation["fx"][0]["valid_as_of"]) == (observed, valid)


def test_future_validity_is_not_backdated_by_an_end_time_rate_list(tmp_path):
    runtime = _runtime(tmp_path)
    started = utc_iso(runtime.clock.now())
    runtime.ledger.deposit(runtime.portfolio_id, "EUR", Decimal("100"), "opening")
    runtime.clock.advance(10)
    retrieved = utc_iso(runtime.clock.now())
    valid = utc_iso(runtime.clock.now() + timedelta(seconds=10))
    _reference(runtime, observed=started, valid=valid, retrieved=retrieved)
    assert runtime.ledger._rates(retrieved) == []
    runtime.clock.advance(5)
    flow_at = utc_iso(runtime.clock.now())
    runtime.ledger.deposit(runtime.portfolio_id, "USD", Decimal("100"), "before-validity")
    runtime.clock.advance(10)
    end_rates = runtime.ledger._rates(utc_iso(runtime.clock.now()))
    assert end_rates[0].at == valid
    assert _convert(Decimal("100"), "USD", "EUR", end_rates, flow_at) == (None, True)
    result = runtime.ledger.performance(runtime.portfolio_id, started, utc_iso(runtime.clock.now()))
    assert result.provisional and result.trading_pnl is None


def test_same_date_reference_corrections_use_only_the_latest_available_revision(tmp_path):
    runtime = _runtime(tmp_path)
    source_date = utc_iso(runtime.clock.now() - timedelta(days=1))
    runtime.clock.advance(10)
    first_available = utc_iso(runtime.clock.now())
    _reference(runtime, rate_id="first", rate="0.90", observed=source_date)
    runtime.clock.advance(10)
    later_available = utc_iso(runtime.clock.now())
    _reference(runtime, rate_id="later", rate="0.91", observed=source_date)
    rates = runtime.ledger._rates(later_available)
    assert _convert(Decimal("100"), "USD", "EUR", rates, first_available) == (Decimal("90"), False)
    assert _convert(Decimal("100"), "USD", "EUR", rates, later_available) == (Decimal("91"), False)
    assert financial._fx(runtime, "USD", "EUR", first_available)["rate_id"] == "first"
    assert financial._fx(runtime, "USD", "EUR", later_available)["rate_id"] == "later"


@pytest.mark.parametrize("available", [None, "2026-01-02T00:00:00.000000Z"])
def test_legacy_positional_fx_fixtures_keep_event_time_behavior(available):
    reference_date = "2026-01-01T00:00:00.000000Z"
    queried = "2026-01-02T00:00:00.000000Z"
    rate = FxRate("USD", "EUR", Decimal("0.90"), reference_date, False, "fixture", "fixture-rate", available)
    assert _convert(Decimal("100"), "USD", "EUR", [rate], queried) == (Decimal("90"), False)
