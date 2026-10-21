"""Live market-type labels: the hourly scan labels its coins and the newest label per coin reads back (W11-A; D287).

- ``label_after_scan`` labels every shortlisted coin and every coin of the day (focus, held, planned, alarmed),
  stores each label with its inputs, rules version and scan, and turns a coin it cannot read into an unclear label
  with its reason instead of stopping;
- ``read_latest_market_types`` (the read W12-A shows the desk) gives the newest label per coin at or before a moment,
  on any connection, read-only included, refuses an edited record and gives nothing for a database before 0050;
- the installed service's hourly scan labels its coins after it records the scan, and a labelling failure never
  stops the scan.

Synthetic candles (``synthetic_fixture``) and synthetic scans; no network.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest
from tests.focus_research_support import record_scan
from tests.market_type_support import (
    FIRST_LABELLED,
    HOUR,
    closes_falling,
    closes_range,
    closes_rising,
    hourly_moment,
    series,
    store_series,
)
from tests.practice_range_support import open_store

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.coin_scanner.store import CoinScanStore
from trade_graph.application.market_types.hourly import coins_to_label, label_after_scan
from trade_graph.application.market_types.store import (
    DEFAULT_LABEL_AGE,
    MarketTypeStore,
    read_latest_market_types,
)
from trade_graph.domain.clock import FrozenClock, utc_iso
from trade_graph.domain.errors import StaleState
from trade_graph.kernel.market_type import CURRENT_MARKET_TYPE_RULES

SOURCE = "kraken_public_ohlc"
MOMENT = hourly_moment(FIRST_LABELLED + 40)


@pytest.fixture
def setup(tmp_path):
    market, candles = open_store(tmp_path / "market" / "market_data.sqlite")
    for symbol, closes in (("SOL/USD", closes_rising(950)), ("ETH/USD", closes_falling(950)),
                           ("ADA/USD", closes_range(950))):
        store_series(candles, series(symbol, closes, source=SOURCE))
    # A young coin: its first hundred hours end at the labelling moment.
    store_series(candles, series("NEW/USD", closes_rising(100), first_index=FIRST_LABELLED + 40 - 99, source=SOURCE))
    clock = FrozenClock(MOMENT + timedelta(seconds=30))
    database = Database(tmp_path / "state" / "trade_graph.sqlite")
    yield database, candles, clock
    database.close()
    market.close()


def test_the_scan_labels_its_shortlist_and_the_days_coins_with_their_inputs(setup) -> None:
    database, candles, clock = setup
    scan_id = record_scan(database, ["SOL/USD", "ADA/USD"], as_of=utc_iso(MOMENT))
    stored = CoinScanStore(database, clock).get(scan_id)
    assert coins_to_label(stored, {"ETH/USD", "NEW/USD", "SOL/USD"}) == ("ADA/USD", "ETH/USD", "NEW/USD", "SOL/USD")
    labels = MarketTypeStore(database, clock)
    done = label_after_scan(candles=candles, labels=labels, scan=stored, day_symbols={"ETH/USD", "NEW/USD"},
                            as_of=MOMENT)
    assert {item.symbol: item.market_type for item in done.stored} == {
        "ADA/USD": "sideways", "ETH/USD": "falling", "NEW/USD": "unclear", "SOL/USD": "rising"}
    note = done.note()
    assert note["job"] == "market_types" and note["status"] == "ok" and note["scan_id"] == scan_id
    assert note["by_type"] == {"rising": 1, "sideways": 1, "falling": 1, "unclear": 1}
    assert note["rules"] == "market-type-rules@2" and note["model_calls"] == 0
    newest = labels.latest()
    sol = newest["SOL/USD"]
    assert (sol.scan_id, sol.as_of, sol.source, sol.interval_minutes) == (scan_id, MOMENT, SOURCE, 60)
    assert (sol.rules_ref, sol.rules_sha256) == ("market-type-rules@2", CURRENT_MARKET_TYPE_RULES.content_sha256)
    assert sol.candle_close == hourly_moment(FIRST_LABELLED + 40) - timedelta(minutes=5) and sol.lag_bars == 0
    assert sol.evidence == "synthetic_fixture" and sol.document["values"]["strength"] == "100"
    assert sol.document["candles"]["candles_read"] == 887 and sol.document["candles"]["read_mode"] == "point_in_time"
    assert newest["NEW/USD"].reason == "not_enough_candles" and newest["NEW/USD"].candle_close is not None
    assert "SOL/USD is rising" in sol.sentence()
    assert sol.is_fresh(MOMENT + DEFAULT_LABEL_AGE) and not sol.is_fresh(MOMENT + DEFAULT_LABEL_AGE + HOUR)
    assert not sol.is_fresh(MOMENT - HOUR)  # a label from after the moment is not a label at that moment


def test_a_coin_that_cannot_be_read_is_unclear_and_the_others_carry_on(setup, monkeypatch) -> None:
    from trade_graph.application.market_types import hourly
    from trade_graph.domain.errors import ValidationFailure

    database, candles, clock = setup
    real = hourly.label_coin

    def refusing(store, *, symbol, **kwargs):
        if symbol == "BAD/USD":
            raise ValidationFailure("the candle store refused the read")
        return real(store, symbol=symbol, **kwargs)

    monkeypatch.setattr(hourly, "label_coin", refusing)
    labels = MarketTypeStore(database, clock)
    done = label_after_scan(candles=candles, labels=labels, scan=None, day_symbols={"SOL/USD", "BAD/USD"},
                            as_of=MOMENT)
    by_symbol = {item.symbol: item for item in done.stored}
    assert by_symbol["SOL/USD"].market_type == "rising"
    broken = by_symbol["BAD/USD"]
    assert (broken.market_type, broken.reason, broken.candle_close, broken.evidence) == (
        "unclear", "evaluation_failed", None, "no_candles")
    assert broken.document["candles"]["error"] == "ValidationFailure" and broken.scan_id is None
    assert "could not be read" in broken.sentence()
    assert done.note()["failed"] == ["BAD/USD"]
    nothing = by_symbol.get("NONE/USD")
    assert nothing is None  # only the coins asked for are labelled


def test_the_newest_label_per_coin_at_a_moment_on_any_connection(setup, tmp_path) -> None:
    database, candles, clock = setup
    labels = MarketTypeStore(database, clock)
    first = label_after_scan(candles=candles, labels=labels, scan=None, day_symbols={"SOL/USD"}, as_of=MOMENT)
    later = MOMENT + HOUR
    label_after_scan(candles=candles, labels=labels, scan=None, day_symbols={"SOL/USD", "ETH/USD"}, as_of=later)
    assert {symbol: item.as_of for symbol, item in labels.latest().items()} == {"SOL/USD": later, "ETH/USD": later}
    then = labels.latest(as_of=MOMENT + timedelta(minutes=59))
    assert list(then) == ["SOL/USD"] and then["SOL/USD"].label_id == first.stored[0].label_id
    assert labels.latest(symbols=["ETH/USD", "XRP/USD"]).keys() == {"ETH/USD"}
    assert labels.latest(as_of=MOMENT - timedelta(seconds=1)) == {}
    assert [item.as_of for item in labels.history("SOL/USD")] == [MOMENT, later]
    path = tmp_path / "state" / "trade_graph.sqlite"
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        assert read_latest_market_types(connection) == labels.latest()
    finally:
        connection.close()


def test_an_edited_label_is_refused_and_an_older_database_has_none(setup, tmp_path) -> None:
    database, candles, clock = setup
    label_after_scan(candles=candles, labels=MarketTypeStore(database, clock), scan=None,
                     day_symbols={"SOL/USD"}, as_of=MOMENT)
    with database.transaction() as connection:
        connection.execute("DROP TRIGGER market_type_labels_no_update")
        connection.execute("UPDATE market_type_labels SET document_json = json_set(document_json, "
                           "'$.market_type', 'falling')")
    with pytest.raises(StaleState, match="no longer matches"):
        MarketTypeStore(database, clock).latest()
    older = sqlite3.connect(tmp_path / "older.sqlite")
    older.row_factory = sqlite3.Row
    assert read_latest_market_types(older) == {}
    older.close()


def test_the_installed_hourly_scan_labels_its_coins_and_a_failure_never_stops_it(setup, tmp_path,
                                                                               monkeypatch) -> None:
    database, candles, clock = setup
    from tests.integration.test_price_collector import Exchange

    from trade_graph.application import service_market_data
    from trade_graph.application.price_collector import CollectorTransports, HotCoins

    path = tmp_path / "installed" / "market_data.sqlite"
    monkeypatch.setattr(service_market_data, "day_coins",
                        lambda *args, **kwargs: HotCoins(symbols=frozenset({"SOL/USD", "ETH/USD"}),
                                                         one_minute=frozenset()))
    events: list[dict] = []
    exchange = Exchange(clock)
    market = service_market_data.open_service_market_data(
        path, database=database, clock=clock, portfolio_ids=(), record=events.append,
        transports=CollectorTransports(catalog=exchange, ohlc=exchange, ticker=exchange))
    try:
        # The collector's own candle file, holding the hours it collected (here: written straight in).
        store_series(market.store, series("SOL/USD", closes_rising(950), source=SOURCE))
        store_series(market.store, series("ETH/USD", closes_falling(950), source=SOURCE))
        stored = market.collector.scan(MOMENT, None, "request_failed")  # no pair list yet: a failed scan
        assert stored.status == "failed"
        newest = MarketTypeStore(database, clock).latest()
        assert {symbol: item.market_type for symbol, item in newest.items()} == {"SOL/USD": "rising",
                                                                                 "ETH/USD": "falling"}
        assert {item.scan_id for item in newest.values()} == {stored.scan_id}
        assert events[-1]["job"] == "market_types" and events[-1]["labelled"] == 2

        def broken(**kwargs):
            raise RuntimeError("disk full")

        monkeypatch.setattr(service_market_data, "label_after_scan", broken)
        again = market.collector.scan(MOMENT + HOUR, None, "request_failed")
        assert again.status == "failed" and again.scan_id != stored.scan_id  # the scan is recorded all the same
        assert events[-1] == {"job": "market_types", "status": "failed", "reason": "RuntimeError",
                              "scan_id": again.scan_id, "model_calls": 0}
    finally:
        market.close()
