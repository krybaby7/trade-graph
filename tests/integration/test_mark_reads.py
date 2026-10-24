"""W10-B (C8): money checks and the scorecard stay fast as weeks of prices pile up.

The money-limit step (every service tick), HQ's today's loss, the Trader's equity and the scorecard used to read
every price mark of the portfolio, so each read grew with the price history (about 34,000 marks a day for four
coins). These tests count what each read takes from the database, through ``tests/mark_reads_support.ReadMeter``,
and never time anything: the same reads over ten times the history take exactly the same rows, and every mark read
seeks one coin through ``valuation_marks_newest`` (migration 0044). That the results stay the same is shown by
``tests/property/test_mark_read_properties.py``. Every account, price and rate here is synthetic.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from tests.mark_reads_support import COINS, ReadMeter, add_prices, add_rates, build_weeks, query_plan
from tests.money_limit_support import open_account, synthetic_policy
from tests.scorecard_support import PORTFOLIO, START, Paper

from trade_graph.adapters.persistence.day_plan_action_schema import VALUATION_MARK_INDEX
from trade_graph.api import hq_trading
from trade_graph.application.ledger import Ledger
from trade_graph.application.money_limits import MoneyLimitGuard
from trade_graph.application.scorecard import compute_scorecard
from trade_graph.contracts.models import FillRecord

D = Decimal
POLICY = synthetic_policy("synthetic-owner-1")


def _account(tmp_path) -> Paper:
    """USD10,000 opened the evening before; BTC and ETH bought, SOL and XRP priced but never held."""
    paper = Paper(tmp_path / "state.sqlite", at=START - timedelta(hours=1))
    paper.deposit("10000")
    add_prices(paper, 1, interval_seconds=60)
    paper.fill("BTC/USD", "buy", "0.01", "60000")
    paper.fill("ETH/USD", "buy", "0.5", "3000")
    add_rates(paper, 3)
    return paper


def _native(paper: Paper) -> tuple[ReadMeter, object]:
    meter = ReadMeter(paper.database)
    return meter, Ledger(meter, paper.clock).native_equity(PORTFOLIO, "USD")


def _status(paper: Paper) -> tuple[ReadMeter, object]:
    meter = ReadMeter(paper.database)
    return meter, MoneyLimitGuard(meter, Ledger(meter, paper.clock), paper.clock).status(PORTFOLIO, POLICY)


def _hq_loss(paper: Paper) -> tuple[ReadMeter, dict]:
    meter = ReadMeter(paper.database)
    runtime = SimpleNamespace(database=meter, clock=paper.clock, ledger=Ledger(meter, paper.clock),
                              portfolio_id=PORTFOLIO)
    with pytest.MonkeyPatch.context() as patch:  # the synthetic account has no owner policy installed
        patch.setattr(hq_trading, "_policy", lambda database: POLICY)
        return meter, hq_trading._loss(runtime, paper.clock.now())


def _equity(paper: Paper) -> tuple[ReadMeter, object]:
    meter = ReadMeter(paper.database)
    return meter, Ledger(meter, paper.clock).equity(PORTFOLIO)


def _scorecard(path, **period) -> tuple[ReadMeter, dict]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        meter = ReadMeter(connection)
        return meter, compute_scorecard(meter, portfolio_id=PORTFOLIO, **period).document()
    finally:
        connection.close()


def test_a_valuation_reads_one_mark_per_coin_held_however_long_the_price_history(tmp_path) -> None:
    paper = _account(tmp_path)
    try:
        add_prices(paper, 60)
        short, short_view = _native(paper)
        add_prices(paper, 600)
        long, long_view = _native(paper)
        assert short_view.equity is not None and long_view.equity is not None and not long_view.missing
        # BTC and ETH are held; SOL and XRP are priced but not held, so their prices are not read at all.
        assert short.rows["valuation_marks"] == long.rows["valuation_marks"] == 2
        # USD cash and USD prices in a USD valuation: nothing to convert, so no reference rate is read.
        assert short.rows["fx_rates"] == long.rows["fx_rates"] == 0
        assert len(short.statements) == len(long.statements)
    finally:
        paper.close()


def test_the_money_limit_step_and_hq_today_loss_read_the_same_rows_after_ten_times_the_prices(tmp_path) -> None:
    paper = _account(tmp_path)
    try:
        add_prices(paper, 60, interval_seconds=120)  # past midnight: the day start values coins held then
        (short, short_status), (short_hq, short_loss) = _status(paper), _hq_loss(paper)
        add_prices(paper, 600, interval_seconds=120)
        (long, long_status), (long_hq, long_loss) = _status(paper), _hq_loss(paper)
        for status in (short_status, long_status):
            assert status.day_start is not None and status.value is not None and status.unknown is None
        assert short_loss["document"] == short_status.document()
        assert long_loss["document"] == long_status.document()
        # Two coins held, each valued at the day start and now, and each checked for a fresh price then: at most
        # four mark rows per coin, whatever the length of the history.
        for meter in (short, long, short_hq, long_hq):
            assert meter.rows["valuation_marks"] <= 4 * 2
            assert meter.rows["fx_rates"] == 0
        assert short.rows == long.rows and len(short.statements) == len(long.statements)
        assert short_hq.rows == long_hq.rows and len(short_hq.statements) == len(long_hq.statements)
    finally:
        paper.close()


def test_eur_reporting_reads_only_the_newest_rate_it_needs(tmp_path) -> None:
    paper = _account(tmp_path)
    try:
        add_prices(paper, 60)
        short, short_view = _equity(paper)
        add_rates(paper, 300)
        add_prices(paper, 600)
        long, long_view = _equity(paper)
        assert short_view.equity is not None and long_view.equity is not None
        # One pair to convert (USD to EUR), one rate read; two coins held, one mark each.
        assert short.rows["fx_rates"] == long.rows["fx_rates"] == 1
        assert short.rows["valuation_marks"] == long.rows["valuation_marks"] == 2
    finally:
        paper.close()


def test_every_mark_read_seeks_one_coin_through_the_newest_mark_index(tmp_path) -> None:
    paper = _account(tmp_path)
    try:
        add_prices(paper, 200, interval_seconds=120)
        paper.fill("ETH/USD", "sell", "0.5", "3000", liquidity="taker")
        add_prices(paper, 20)
        meters = [_native(paper)[0], _status(paper)[0], _equity(paper)[0], _hq_loss(paper)[0],
                  _scorecard(paper.database.path, end=paper.clock.now())[0]]
        reads = [read for meter in meters for read in meter.reads_of("valuation_marks")]
        assert reads
        for sql, params in reads:
            plan = query_plan(paper.database.connection, sql, params)
            assert VALUATION_MARK_INDEX in plan and "asset=?" in plan, (sql, plan)
            assert "SCAN valuation_marks" not in plan, plan
    finally:
        paper.close()


def test_the_scorecard_reads_the_prices_of_a_coin_only_while_it_is_held(tmp_path) -> None:
    weeks = build_weeks(tmp_path / "state.sqlite", days=1, interval_seconds=60, trades=6)
    try:
        meter, document = _scorecard(weeks.paper.database.path, end=weeks.end)
        assert document["valuation"]["equity_points"] > weeks.trades
        # Every price of a coin while it is held (each one is a point of the curve), plus a handful of single
        # prices per coin and per trade: the newest when it starts or stops being held, at the start and the end.
        assert weeks.held_marks < meter.rows["valuation_marks"] <= weeks.held_marks + 4 * (weeks.trades + len(COINS))
        assert meter.rows["valuation_marks"] < weeks.marks / 4
    finally:
        weeks.paper.close()


def test_money_limit_reads_break_ties_between_prices_of_one_moment_as_the_ledger_does(tmp_path) -> None:
    account = open_account(tmp_path)
    try:
        account.clock.advance(60)
        fill = FillRecord(venue="paper", account_id="paper", trade_id="synthetic-tie", intent_id=None,
                          symbol="SOL/USD", side="buy", quantity="1", price="100", fee_amount="0", fee_asset="USD",
                          liquidity="maker", filled_at_utc=account.clock.now())
        account.ledger.apply_fill(account.portfolio, fill, base_asset="SOL", quote_asset="USD")
        account.clock.advance(60)
        account.quote("SOL/USD", "109.9", "110.1")  # two prices stamped with the very same time:
        account.quote("SOL/USD", "119.9", "120.1")  # the first recorded is the one the books use
        view = account.ledger.native_equity(account.portfolio, "USD")
        assert view.inventory == D("110")
        assert account.execution._native_mark(account.portfolio, "SOL", "USD") == D("110")
        meter = ReadMeter(account.database)
        MoneyLimitGuard(meter, Ledger(meter, account.clock), account.clock).status(account.portfolio,
                                                                                   account.policy)
        reads = {sql for sql, _ in meter.reads_of("valuation_marks")}
        assert len(reads) == 1 and "ORDER BY observed_at DESC, rowid ASC LIMIT 1" in reads.pop()
    finally:
        account.database.close()


def test_the_synthetic_weeks_and_the_meter_count_what_they_say(tmp_path) -> None:
    weeks = build_weeks(tmp_path / "state.sqlite", days=0.5, interval_seconds=120, trades=4)
    try:
        assert weeks.marks == len(COINS) * (int(0.5 * 86400 // 120) + 1)
        assert weeks.held_at_end == (COINS[3],)
        meter = ReadMeter(weeks.paper.database)
        assert len(meter.execute("SELECT * FROM valuation_marks").fetchall()) == weeks.marks
        assert meter.rows["valuation_marks"] == weeks.marks and meter.steps > 0
    finally:
        weeks.paper.close()
