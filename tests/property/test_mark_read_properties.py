"""Property tests for W10-B (C8): reading only the newest price changes no result.

The ledger used to read every price mark of the portfolio and every reference rate up to the moment asked, and pick
the newest in Python; the scorecard's equity curve used to read every price of every coin held in the period. The
reads as they were at ``f6abbd8`` are kept below as the reference. Over random synthetic records (prices of coins held
and not held, prices quoted in another currency, rates of several pairs, many records stamped with the very same time
and written in any order, rates published for one date and retrieved later), the new reads give exactly the same
valuations, rates, performance and scorecard documents. Every record is synthetic; nothing is fetched or sent.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, Context, Decimal, localcontext
from pathlib import Path
from unittest.mock import patch

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tests.scorecard_support import PORTFOLIO, START, Paper

from trade_graph.application import scorecard
from trade_graph.application.scorecard import ScorecardUnavailable, compute_scorecard
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import StaleState
from trade_graph.kernel.books import (
    Mark,
    Performance,
    _convert,
    convert_native,
    mark_equity,
    native_equity,
    performance,
)
from trade_graph.kernel.trade_metrics import EquityPoint

D = Decimal
COINS = ("BTC", "ETH", "SOL")
PAIRS = (("USD", "EUR"), ("EUR", "USD"), ("GBP", "EUR"), ("USD", "GBP"))
MINUTES = 6
TIMES = [utc_iso(START + timedelta(minutes=minute)) for minute in range(MINUTES)]
QUERIES = sorted(TIMES + [utc_iso(START + timedelta(minutes=minute, seconds=30)) for minute in range(MINUTES)])


# --- The reads as they were at f6abbd8 ------------------------------------------------------------------------------


def old_marks(ledger, portfolio_id: str, at: str) -> list[Mark]:
    rows = ledger.database.execute(
        """SELECT asset, quote_currency, mark, observed_at, stale, source FROM valuation_marks
        WHERE portfolio_id = ? AND observed_at <= ?""",
        (portfolio_id, at),
    ).fetchall()
    return [Mark(row["asset"], D(row["mark"]), row["quote_currency"], row["observed_at"], bool(row["stale"]),
                 row["source"]) for row in rows]


def old_native_equity(ledger, portfolio_id: str, currency: str, at: str):
    return native_equity(ledger._books(portfolio_id, at), currency=currency, marks=old_marks(ledger, portfolio_id, at),
                         rates=ledger._rates(at), at=at)


def old_equity(ledger, portfolio_id: str, at: str):
    return mark_equity(ledger._books(portfolio_id, at), reporting=ledger._reporting(portfolio_id),
                       marks=old_marks(ledger, portfolio_id, at), rates=ledger._rates(at), at=at)


def old_fx_rate(ledger, base: str, quote: str, at: str):
    return _convert(D("1"), base, quote, ledger._rates(at), at)


def old_performance(ledger, portfolio_id: str, start: str, end: str) -> Performance:
    reporting = ledger._reporting(portfolio_id)
    start_books, end_books = ledger._books(portfolio_id, start), ledger._books(portfolio_id, end)
    start_view = mark_equity(start_books, reporting=reporting, marks=old_marks(ledger, portfolio_id, start),
                             rates=ledger._rates(start), at=start)
    end_view = mark_equity(end_books, reporting=reporting, marks=old_marks(ledger, portfolio_id, end),
                           rates=ledger._rates(end), at=end)
    return performance(end_books, reporting=reporting, marks=old_marks(ledger, portfolio_id, end),
                       rates=ledger._rates(end), start=start, end=end, start_view=start_view, end_view=end_view)


def old_reporting_value(ledger, portfolio_id: str, amount: Decimal, currency: str) -> Decimal:
    at = ledger.now()
    value, stale = _convert(amount, currency, ledger._reporting(portfolio_id), ledger._rates(at), at)
    if value is None or stale:
        raise StaleState(f"missing or stale reporting FX for {currency}")
    return value


def old_native_value(ledger, amount: Decimal, currency: str, native: str) -> Decimal:
    at = ledger.now()
    value, stale = convert_native(amount, currency, native, ledger._rates(at), at)
    if value is None or stale:
        raise StaleState(f"missing or stale {currency} to {native} FX for native exposure")
    return value


def old_curve(connection, portfolio_id: str, books, marks, moments, currency: str, start: datetime, start_iso: str,
              end_iso: str, starting_equity: Decimal, assets: list[str]) -> tuple[list[EquityPoint], int]:
    """``application/scorecard.py`` ``_curve`` at f6abbd8: reads every price of every coin held in the period."""
    prices: dict[str, list[tuple[str, Decimal, bool]]] = {}
    if assets:
        holes = ",".join("?" for _ in assets)
        other = connection.execute(
            f"""SELECT asset, quote_currency FROM valuation_marks WHERE portfolio_id = ? AND observed_at > ?
            AND observed_at <= ? AND asset IN ({holes}) AND quote_currency != ? LIMIT 1""",
            (portfolio_id, start_iso, end_iso, *assets, currency)).fetchone()
        if other is not None:
            raise ScorecardUnavailable(f"the {other['asset']} price is recorded in {other['quote_currency']}, not "
                                       f"{currency}; the scorecard does not guess a rate")
        for asset, mark, observed_at, stale in connection.execute(
                f"""SELECT asset, mark, observed_at, stale FROM valuation_marks
                WHERE portfolio_id = ? AND observed_at > ? AND observed_at <= ? AND asset IN ({holes})
                ORDER BY observed_at, rowid""", (portfolio_id, start_iso, end_iso, *assets)):
            prices.setdefault(observed_at, []).append((asset, D(mark), bool(stale)))
    held = books.holdings()
    points = [EquityPoint(start, starting_equity)]
    unvalued = 0
    with localcontext(Context(prec=160)):
        for moment in sorted(moments.keys() | prices.keys()):
            postings = moments.get(moment)
            if postings:
                for account, asset, amount in postings:
                    books.post(account, asset, amount)
                    if account == "inventory":
                        quantity = books.held[asset]
                        if quantity:
                            held[asset] = quantity
                        else:
                            held.pop(asset, None)
                books.only_cash_in(currency, moment)
            priced = False
            for asset, price, stale in prices.get(moment, ()):
                current = marks[asset]
                if current is not None and current.at == moment:
                    continue  # the ledger uses the first mark recorded at one time
                marks[asset] = scorecard._Mark(price, currency, moment, stale)
                priced = priced or asset in held
            if moment == end_iso or not (postings or priced):
                continue
            value = books.cash.get(currency, D("0"))
            for asset, quantity in held.items():
                mark = marks[asset]
                if mark is None:
                    value = None
                    break
                value += quantity * mark.price
            if value is None:
                unvalued += 1
                continue
            points.append(EquityPoint(datetime.fromisoformat(moment), value))
    return points, unvalued


# --- Helpers ------------------------------------------------------------------------------------------------------


def _outcome(call):
    """What a read gives: its value written out in full (exponents included), or the refusal and its words."""
    try:
        return ("value", repr(call()))
    except (StaleState, ScorecardUnavailable, KeyError) as error:
        return ("refused", type(error).__name__, str(error))


def _insert_mark(paper: Paper, number: int, asset: str, quote: str, price: int, observed: str, stale: bool) -> None:
    paper.database.execute(
        """INSERT INTO valuation_marks
        (mark_id, portfolio_id, asset, quote_currency, mark, convention, observed_at, stale, source)
        VALUES (?, ?, ?, ?, ?, 'mid', ?, ?, 'synthetic:property')""",
        (f"mark-{number}", PORTFOLIO, asset, quote, str(D(price)), observed, int(stale)))


def _insert_rate(paper: Paper, number: int, pair: tuple[str, str], rate: int, observed: str, valid: str,
                 retrieved: str, stale: bool) -> None:
    paper.database.execute(
        """INSERT INTO fx_rates
        (rate_id, base, quote, rate, source, observed_at, valid_as_of, retrieved_at, kind, stale)
        VALUES (?, ?, ?, ?, 'synthetic-reference', ?, ?, ?, 'reference', ?)""",
        (f"rate-{number}", pair[0], pair[1], str(D(rate) / 100), observed, valid, retrieved, int(stale)))


# --- The ledger: valuations, rates and performance ------------------------------------------------------------------

TRADE = st.one_of(st.none(), st.tuples(st.sampled_from(["buy", "sell"]), st.sampled_from(COINS),
                                       st.integers(min_value=1, max_value=5)))
MARK = st.tuples(st.sampled_from(COINS), st.sampled_from(["USD", "USD", "USD", "EUR"]),
                 st.integers(min_value=50, max_value=150), st.integers(min_value=0, max_value=MINUTES - 1),
                 st.booleans(), st.booleans())
RATE = st.tuples(st.sampled_from(PAIRS), st.integers(min_value=50, max_value=150),
                 st.integers(min_value=0, max_value=MINUTES - 1), st.integers(min_value=0, max_value=MINUTES - 1),
                 st.integers(min_value=0, max_value=MINUTES - 1), st.integers(min_value=0, max_value=3))


@settings(max_examples=60, deadline=None, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
@given(trades=st.lists(TRADE, min_size=MINUTES - 1, max_size=MINUTES - 1), marks=st.lists(MARK, max_size=24),
       rates=st.lists(RATE, max_size=12), euros=st.booleans())
def test_the_ledger_values_reads_rates_and_performance_exactly_as_before(trades, marks, rates, euros) -> None:
    with tempfile.TemporaryDirectory() as folder:
        paper = Paper(Path(folder) / "state.sqlite", at=START)
        try:
            ledger = paper.ledger
            paper.deposit("10000")
            if euros:
                paper.deposit("500", asset="EUR", ref="opening-eur")
            held = dict.fromkeys(COINS, D("0"))
            for minute, trade in enumerate(trades, start=1):
                paper.at(START + timedelta(minutes=minute))
                if trade is None:
                    continue
                side, coin, tenths = trade
                quantity = D(tenths) / 10
                if side == "sell":
                    quantity = min(quantity, held[coin])
                    if quantity <= 0:
                        continue
                paper.fill(f"{coin}/USD", side, str(quantity), "100", fee="0.1")
                held[coin] += quantity if side == "buy" else -quantity
            for number, (coin, quote, price, minute, half, stale) in enumerate(marks):
                observed = START + timedelta(minutes=minute, seconds=30 if half else 0)
                _insert_mark(paper, number, coin, quote, price, utc_iso(observed), stale)
            for number, (pair, rate, observed, valid, late, stale_kind) in enumerate(rates):
                retrieved = min(MINUTES - 1, max(observed, valid) + late)
                _insert_rate(paper, number, pair, rate, TIMES[observed], TIMES[valid], TIMES[retrieved],
                             stale_kind == 0)
            paper.at(START + timedelta(minutes=MINUTES))

            for at in QUERIES:
                for currency in ("USD", "EUR"):
                    assert _outcome(lambda: ledger.native_equity(PORTFOLIO, currency, at)) == _outcome(
                        lambda: old_native_equity(ledger, PORTFOLIO, currency, at)), (currency, at)
                assert _outcome(lambda: ledger.equity(PORTFOLIO, at)) == _outcome(
                    lambda: old_equity(ledger, PORTFOLIO, at)), at
                for base, quote in (*PAIRS, ("USD", "USD")):
                    assert repr(ledger.fx_rate(base, quote, at)) == repr(old_fx_rate(ledger, base, quote, at)), (
                        base, quote, at)
                for start in (TIMES[0], TIMES[2]):
                    if start <= at:
                        assert _outcome(lambda: ledger.performance(PORTFOLIO, start, at)) == _outcome(
                            lambda: old_performance(ledger, PORTFOLIO, start, at)), (start, at)
            for currency in ("USD", "EUR", "GBP"):
                assert _outcome(lambda: ledger.reporting_value(PORTFOLIO, D("10"), currency)) == _outcome(
                    lambda: old_reporting_value(ledger, PORTFOLIO, D("10"), currency)), currency
                for native in ("USD", "EUR"):
                    assert _outcome(lambda: ledger.native_value(D("10"), currency, native)) == _outcome(
                        lambda: old_native_value(ledger, D("10"), currency, native)), (currency, native)
        finally:
            paper.close()


# --- The scorecard: the same document from fewer prices ------------------------------------------------------------

STEP = st.tuples(
    st.sampled_from(["mark", "mark", "mark", "buy", "sell", "sell_all", "other_quote"]),
    st.sampled_from(COINS),
    st.integers(min_value=-5, max_value=5),  # price move in percent from the base price
    st.integers(min_value=1, max_value=10),  # tenths: of a fifth of the cash to spend, or of the coins held to sell
    st.booleans(),  # a buy's fee is charged in the coin
    st.sampled_from([0, 1, 1, 1, 2]),  # minutes the clock moves first: 0 stamps it with the same time as before
)
BASE = {"BTC": D("60000"), "ETH": D("3000"), "SOL": D("150")}
LOT = D("0.0001")


@settings(max_examples=80, deadline=None, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(STEP, min_size=1, max_size=24), st.one_of(st.none(), st.integers(min_value=0, max_value=24)),
       st.sampled_from([0, 1, 5]))
def test_the_scorecard_document_is_the_same_as_reading_every_price(steps, start_after, end_after) -> None:
    with tempfile.TemporaryDirectory() as folder:
        paper = Paper(Path(folder) / "state.sqlite", at=START)
        try:
            paper.deposit("10000")
            for coin in ("BTC", "ETH"):
                paper.mark(coin, str(BASE[coin]))
            cash, held = D("10000"), dict.fromkeys(COINS, D("0"))
            start = None
            for index, (kind, coin, move, tenths, coin_fee, minutes) in enumerate(steps):
                paper.at(paper.clock.now() + timedelta(minutes=minutes))
                if index == start_after:
                    start = paper.clock.now()
                price = BASE[coin] * (100 + move) / 100
                if kind == "mark":
                    paper.mark(coin, str(price), stale=move == 5)
                elif kind == "other_quote" and move > 3:
                    paper.mark(coin, str(price), quote="EUR")
                elif kind == "buy":
                    quantity = (cash / 5 * tenths / 10 / price).quantize(LOT, rounding=ROUND_FLOOR)
                    if quantity <= 0:
                        continue
                    if coin_fee:
                        paper.fill(f"{coin}/USD", "buy", str(quantity), str(price), fee=str(quantity * D("0.004")),
                                   fee_asset=coin)
                        cash, held[coin] = cash - quantity * price, held[coin] + quantity * D("0.996")
                    else:
                        fee = quantity * price * D("0.004")
                        paper.fill(f"{coin}/USD", "buy", str(quantity), str(price), fee=str(fee))
                        cash, held[coin] = cash - quantity * price - fee, held[coin] + quantity
                elif kind in ("sell", "sell_all"):
                    quantity = held[coin] if kind == "sell_all" else (held[coin] * tenths / 10).quantize(
                        LOT, rounding=ROUND_FLOOR)
                    if quantity <= 0:
                        continue
                    fee = quantity * price * D("0.008")
                    paper.fill(f"{coin}/USD", "sell", str(quantity), str(price), fee=str(fee), liquidity="taker")
                    cash, held[coin] = cash + quantity * price - fee, held[coin] - quantity
            end = paper.clock.now() + timedelta(minutes=end_after)
            if start is not None and start >= end:
                start = None

            def card():
                return compute_scorecard(paper.database, portfolio_id=PORTFOLIO, end=end, start=start).document()

            new = _outcome(card)
            with patch.object(scorecard, "_curve", old_curve):
                old = _outcome(card)
            assert new == old
        finally:
            paper.close()
