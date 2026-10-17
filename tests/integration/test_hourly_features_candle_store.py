"""The hourly features read the candle store (W6-D, D220; D136 "later work").

The fields, their TA-Lib functions, rounding and statuses stay ``hourly-price-features-v3``; only where the candles
come from changes. With the same completed hours stored in both places, the candle store gives the same numbers as
``hourly_candles``. It also serves any coin (not only BTC/USD and ETH/USD), reads point in time and says which store
it read. An installation without a candle file keeps reading ``hourly_candles``. Synthetic candles only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.integration.test_execution import _stack
from tests.integration.test_price_history import _candle

from trade_graph.adapters.persistence.db import Database
from trade_graph.adapters.persistence.market_data import MarketDataDatabase
from trade_graph.adapters.persistence.market_data_readonly import ReadOnlyMarketData
from trade_graph.application.candle_readiness import candle_wait, expected_candle_status, missing_expected_candles
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.market_context import market_context
from trade_graph.application.price_history import FEATURE_VERSION, CandleStoreHistory, HistoryStore
from trade_graph.contracts.candles import Candle
from trade_graph.contracts.price_history import HourlyCandle
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import ValidationFailure

END = datetime(2026, 10, 5, 12, tzinfo=UTC)
AS_OF = END + timedelta(minutes=30)
FIELDS = ["sma_20", "sma_50", "highest_high_20", "range_high", "range_low", "midpoint", "hourly_return",
          "four_hour_drift", "pullback_from_high", "not_a_feature"]
PAIRS = {"BTC/USD": "XBTUSD", "ETH/USD": "ETHUSD", "SOL/USD": "SOLUSD"}


def store_candle(hourly: HourlyCandle, *, symbol: str | None = None, source: str | None = None,
                 available: datetime | None = None, **changes) -> Candle:
    """The same completed hour as the candle store keeps it."""
    symbol = symbol or hourly.symbol
    values = dict(symbol=symbol, source_pair=PAIRS[symbol], interval_minutes=60, open_time_utc=hourly.open_time_utc,
                  close_time_utc=hourly.close_time_utc, available_at_utc=available or hourly.available_at_utc,
                  open=hourly.open, high=hourly.high, low=hourly.low, close=hourly.close, vwap=hourly.vwap,
                  volume=hourly.volume, trade_count=hourly.trade_count, source=source or hourly.source)
    values.update(changes)
    return Candle(**values)


class Stores:
    def __init__(self, tmp_path) -> None:
        self.financial = Database(tmp_path / "state" / "trade_graph.sqlite")
        self.path = tmp_path / "market" / "market_data.sqlite"
        self.market = MarketDataDatabase(self.path)
        self.candles = CandleStore(self.market)
        self.run_id = self.candles.begin_run(kind="public_collect", source="synthetic",
                                             evidence_label="synthetic_fixture", started_at=END - timedelta(days=9),
                                             parameters={})
        self.old = HistoryStore(self.financial)
        self.new = CandleStoreHistory(self.candles)

    def save(self, hourly: list[HourlyCandle], **options) -> None:
        self.old.save(hourly)
        self.candles.save([store_candle(item, **options) for item in hourly], run_id=self.run_id)

    def close(self) -> None:
        self.market.close()
        self.financial.close()


@pytest.fixture
def stores(tmp_path):
    made = Stores(tmp_path)
    yield made
    made.close()


def _comparable(snapshot: dict) -> dict:
    """What the Trader reads: values, statuses, windows, the latest close and the recent candles."""
    features = {name: {key: value for key, value in feature.items() if key != "candle_ids"}
                for name, feature in snapshot["features"].items()}
    keep = ("symbol", "as_of_utc", "source", "interval_minutes", "feature_version", "latest_close_time_utc",
            "latest_expected_close_time_utc", "stale", "values", "latest_close", "recent_candles",
            "recent_missing_close_times_utc", "event_time_utc", "available_at_utc")
    return {**{key: snapshot[key] for key in keep}, "features": features}


@pytest.mark.parametrize("as_of", [AS_OF, END + timedelta(hours=2, minutes=5), END - timedelta(hours=3)])
def test_the_candle_store_gives_the_same_v3_numbers_and_statuses_as_hourly_candles(stores, as_of) -> None:
    stores.save([_candle(index, count=60) for index in range(1, 61) if index != 40])
    old = stores.old.snapshot("BTC/USD", as_of, FIELDS, source="synthetic")
    new = stores.new.snapshot("BTC/USD", as_of, FIELDS, source="synthetic")
    assert _comparable(new) == _comparable(old)
    assert new["feature_version"] == FEATURE_VERSION and new["history_store"] == "candle_store"
    assert "history_store" not in old  # retained snapshots from the older store keep their shape
    assert new["values"] and new["source_ref"].startswith("hourly-features:") and new["source_ref"] != old["source_ref"]
    first = new["provenance"][0]
    assert first["candle_id"].startswith("candle-store:synthetic:BTC/USD:60:") and first["revision"] == 1


def test_any_coin_has_history_in_the_candle_store(stores) -> None:
    stores.candles.save([store_candle(_candle(index, count=60), symbol="SOL/USD") for index in range(1, 61)],
                        run_id=stores.run_id)
    snapshot = stores.new.snapshot("SOL/USD", AS_OF, ["sma_20", "hourly_return"], source="synthetic")
    assert snapshot["symbol"] == "SOL/USD" and set(snapshot["values"]) == {"sma_20", "hourly_return"}
    assert stores.new.supports("SOL/USD") and not stores.old.supports("SOL/USD")
    with pytest.raises(ValueError):
        stores.old.snapshot("SOL/USD", AS_OF, ["sma_20"])
    for bad in ("sol/usd", "SOLUSD", ""):
        assert not stores.new.supports(bad)
        with pytest.raises(ValidationFailure):
            stores.new.snapshot(bad, AS_OF, ["sma_20"])


def test_a_candle_received_after_as_of_is_not_seen_and_the_newest_revision_received_by_then_is_used(stores) -> None:
    hours = [_candle(index, count=60) for index in range(1, 61)]
    stores.save(hours)
    last = hours[-1]
    revised = store_candle(last, available=last.available_at_utc + timedelta(hours=1), close=Decimal("61"),
                           high=Decimal("62"))
    stores.candles.save([revised], run_id=stores.run_id)
    before = stores.new.snapshot("BTC/USD", last.available_at_utc - timedelta(seconds=1), ["hourly_return"],
                                 source="synthetic")
    assert before["latest_close_time_utc"] == utc_iso(last.close_time_utc - timedelta(hours=1))
    first = stores.new.snapshot("BTC/USD", last.available_at_utc + timedelta(minutes=1), ["hourly_return"],
                                source="synthetic")
    later = stores.new.snapshot("BTC/USD", last.available_at_utc + timedelta(hours=2), ["hourly_return"],
                                source="synthetic")
    assert first["latest_close"]["close"] == "60" and later["latest_close"]["close"] == "61"
    assert later["provenance"][-1]["revision"] == 2 and first["provenance"][-1]["revision"] == 1


def test_a_quiet_hour_that_kraken_filled_flat_is_read_as_the_candle_kraken_sent(stores) -> None:
    hours = [_candle(index, count=60) for index in range(1, 61)]
    quiet = hours[-2]
    stores.candles.save([store_candle(item) for item in hours[:-2]] + [
        store_candle(quiet, open=Decimal("58"), high=Decimal("58"), low=Decimal("58"), close=Decimal("58"),
                     vwap=Decimal("0"), volume=Decimal("0"), trade_count=0), store_candle(hours[-1])],
        run_id=stores.run_id)
    snapshot = stores.new.snapshot("BTC/USD", AS_OF, ["sma_20"], source="synthetic")
    assert snapshot["features"]["sma_20"]["status"] == "ready"
    assert [candle["volume"] for candle in snapshot["recent_candles"]][-2] == "0"


def test_market_context_reads_the_candle_store_when_the_execution_has_one(tmp_path) -> None:
    clock, ledger, execution, _broker, _pid = _stack(tmp_path)
    guard = {"mandate": {"symbols": ["BTC/USD", "SOL/USD"], "strategy_ids": ["t"], "max_quote_age_seconds": 60},
             "policy": {"maximum_quote_age_seconds": 60}}
    templates = {"t": {"features": ["sma_20"]}}
    now = clock.now().replace(minute=30)
    hours = [_candle(index, count=60, end=now.replace(minute=0), source="kraken_public_ohlc") for index in range(1, 61)]
    HistoryStore(ledger.database).save(hours)
    market = MarketDataDatabase(tmp_path / "market" / "market_data.sqlite")
    try:
        candles = CandleStore(market)
        run = candles.begin_run(kind="public_collect", source="kraken_public_ohlc", evidence_label="live_public",
                                started_at=now - timedelta(days=5), parameters={})
        candles.save([store_candle(hour, symbol=symbol, close=hour.close + 1, high=hour.high + 1)
                      for symbol in ("BTC/USD", "SOL/USD") for hour in hours], run_id=run)
        old = market_context(execution, guard, templates, as_of=now)
        assert "history_store" not in old["BTC/USD"]["history"]
        assert old["SOL/USD"]["history"]["features"]["sma_20"]["status"] == "unsupported"
        execution.hourly_history = CandleStoreHistory(CandleStore(ReadOnlyMarketData(market.path)))
        new = market_context(execution, guard, templates, as_of=now)
    finally:
        market.close()
    for symbol in ("BTC/USD", "SOL/USD"):
        history = new[symbol]["history"]
        assert history["history_store"] == "candle_store" and history["features"]["sma_20"]["status"] == "ready"
        assert history["expected_candle"]["status"] == "present"
    assert Decimal(new["BTC/USD"]["history"]["values"]["sma_20"]) == \
        Decimal(old["BTC/USD"]["history"]["values"]["sma_20"]) + 1


def test_the_decision_candle_wait_and_expected_candle_read_the_same_store(stores) -> None:
    hours = [_candle(index, count=60, source="kraken_public_ohlc") for index in range(1, 58)]
    run = stores.candles.begin_run(kind="public_collect", source="kraken_public_ohlc", evidence_label="live_public",
                                   started_at=END - timedelta(days=9), parameters={})
    stores.candles.save([store_candle(hour) for hour in hours], run_id=run)
    now = END - timedelta(hours=2) + timedelta(minutes=1)  # the hour that closed at 10:00 is not stored yet
    assert missing_expected_candles(stores.new, ["BTC/USD"], now) == ["BTC/USD"]
    assert missing_expected_candles(stores.financial, ["BTC/USD"], now) == ["BTC/USD"]  # the older store, as before
    waiting = candle_wait(stores.new, ["BTC/USD"], now, wait_seconds=300, previous_output_json=None)
    assert waiting is not None and waiting[1]["_candle_wait"]["missing_symbols"] == ["BTC/USD"]
    status = expected_candle_status(stores.new, "BTC/USD", now, waiting[1]["_candle_wait"])
    assert status["status"] == "missing_after_wait"
    assert status["latest_stored_close_time_utc"] == utc_iso(END - timedelta(hours=3))
    stores.candles.save([store_candle(_candle(58, count=60, source="kraken_public_ohlc"),
                                      available=now + timedelta(seconds=30))], run_id=run)
    later = now + timedelta(minutes=1)
    assert missing_expected_candles(stores.new, ["BTC/USD"], later) == []
    assert candle_wait(stores.new, ["BTC/USD"], later, wait_seconds=300, previous_output_json=None) is None
    assert expected_candle_status(stores.new, "BTC/USD", later, None)["status"] == "present"


def test_hqs_trend_label_reads_the_candle_file_when_hq_has_one(tmp_path) -> None:
    from tests.chart_support import candles, market_file, read_only, runtime, write_candles
    from tests.day_plan_support import day_plan_account

    from trade_graph.api import hq_money

    account = day_plan_account(tmp_path)
    end = account.clock.now().replace(minute=0, second=0, microsecond=0)
    path = market_file(tmp_path)
    write_candles(path, candles("BTC/USD", 60, end, 60, source="kraken_public_ohlc"))
    without = hq_money._market(runtime(account, None))
    assert without["BTC/USD"] == {"sma_20": None, "sma_50": None}  # nothing in hourly_candles
    store = read_only(path)
    try:
        with_file = hq_money._market(runtime(account, store))
    finally:
        store.database.close()
    assert with_file["BTC/USD"]["sma_20"] is not None and with_file["BTC/USD"]["sma_50"] is not None
    assert with_file["ETH/USD"] == {"sma_20": None, "sma_50": None}
