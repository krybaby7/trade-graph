"""A pending newest hourly candle must not withhold every feature (D114)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.integration.test_history_contexts import HistoryFlow, _candles, _save

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.price_history import HistoryStore
from trade_graph.contracts.price_history import HourlyCandle
from trade_graph.domain.clock import utc_iso

END = datetime(2026, 10, 5, 12, tzinfo=UTC)
WAKE = END + timedelta(minutes=13)
ALL = ["sma_20", "sma_50", "highest_high_20", "range_high", "range_low", "midpoint", "hourly_return",
       "four_hour_drift"]
EXPECTED = {
    "sma_20": "40.5", "sma_50": "25.5", "highest_high_20": "51",
    "range_high": "51", "range_low": "26.5", "midpoint": "38.75",
    "hourly_return": "0.0204081632653", "four_hour_drift": "0.0869565217391",
}


def _candle(index, *, available_after=timedelta(seconds=10)):
    close_time = END - timedelta(hours=51 - index)
    value = Decimal(index)
    return HourlyCandle(
        symbol="BTC/USD", open_time_utc=close_time - timedelta(hours=1), close_time_utc=close_time,
        available_at_utc=close_time + available_after, open=value, high=value + 1, low=value - Decimal("0.5"),
        close=value, vwap=value, volume=Decimal("0.125"), trade_count=2, source="synthetic",
        source_ref=f"urn:synthetic-test-ohlc:pending-tail:{index}",
        source_hash=hashlib.sha256(f"synthetic-pending-tail-{index}".encode()).hexdigest())


def _store(tmp_path, *, skip=(), pending=True):
    """Closes END-50h..END-1h are known; the END candle is stored at :40, as in the first run."""
    store = HistoryStore(Database(tmp_path / "pending.sqlite"))
    candles = [_candle(index) for index in range(1, 51) if index not in skip]
    if pending:
        candles.append(_candle(51, available_after=timedelta(minutes=40)))
    assert store.save(candles) == {"inserted": len(candles), "duplicates": 0}
    return store


def test_pending_newest_candle_keeps_every_feature_from_the_latest_available_close(tmp_path):
    store = _store(tmp_path)
    lagged = store.snapshot("BTC/USD", WAKE, ALL, source="synthetic")
    prior = store.snapshot("BTC/USD", END - timedelta(minutes=30), ALL, source="synthetic")
    assert lagged["feature_version"] == "hourly-price-features-v3"
    assert lagged["stale"] is True
    assert lagged["latest_close_time_utc"] == utc_iso(END - timedelta(hours=1))
    assert lagged["latest_expected_close_time_utc"] == utc_iso(END)
    assert lagged["values"] == prior["values"] == EXPECTED
    assert lagged["candle_ids"] == prior["candle_ids"] and len(lagged["candle_ids"]) == 50
    for name, feature in lagged["features"].items():
        lookback = feature["lookback_candles"]
        assert feature["status"] == "stale", name
        assert feature["available_candles"] == lookback
        assert feature["window_end_close_time_utc"] == utc_iso(END - timedelta(hours=1))
        assert feature["window_start_close_time_utc"] == utc_iso(END - timedelta(hours=lookback))
        assert feature["missing_close_times_utc"] == [utc_iso(END)]
        assert feature["event_time_utc"] == utc_iso(END - timedelta(hours=1))
        assert feature["candle_ids"] == prior["features"][name]["candle_ids"]
    assert all(feature["status"] == "ready" and feature["missing_close_times_utc"] == []
               for feature in prior["features"].values())
    assert store.snapshot("BTC/USD", WAKE, ALL, source="synthetic") == lagged
    after = store.snapshot("BTC/USD", END + timedelta(minutes=45), ALL, source="synthetic")
    assert after["stale"] is False and after["values"]["sma_20"] == "41.5"
    assert all(feature["status"] == "ready" and feature["window_end_close_time_utc"] == utc_iso(END)
               for feature in after["features"].values())


def test_internal_gap_is_reported_only_by_features_whose_window_covers_it(tmp_path):
    store = _store(tmp_path, skip={25})
    result = store.snapshot("BTC/USD", WAKE, ALL, source="synthetic")
    sma_50 = result["features"]["sma_50"]
    assert sma_50["status"] == "gapped_history" and sma_50["available_candles"] == 49
    assert sma_50["missing_close_times_utc"] == [utc_iso(END - timedelta(hours=26)), utc_iso(END)]
    assert result["values"] == {name: value for name, value in EXPECTED.items() if name != "sma_50"}
    assert all(feature["status"] == "stale" for name, feature in result["features"].items() if name != "sma_50")


def test_tail_beyond_anchor_lag_bound_still_withholds_every_feature(tmp_path):
    from trade_graph.application.price_history import MAX_ANCHOR_LAG_HOURS

    store = _store(tmp_path, pending=False)
    expected_close = END + timedelta(hours=MAX_ANCHOR_LAG_HOURS)
    result = store.snapshot("BTC/USD", expected_close + timedelta(minutes=13), ALL, source="synthetic")
    assert result["values"] == {} and result["stale"] is True
    unavailable = [END + timedelta(hours=step) for step in range(MAX_ANCHOR_LAG_HOURS + 1)]
    for name, feature in result["features"].items():
        start = expected_close - timedelta(hours=feature["lookback_candles"] - 1)
        assert feature["status"] == "gapped_history", name
        assert feature["window_end_close_time_utc"] == utc_iso(expected_close)
        assert feature["missing_close_times_utc"] == [utc_iso(at) for at in unavailable if at >= start]


@pytest.mark.parametrize("role", ["research", "trader"])
def test_role_context_receives_stale_features_while_newest_candle_is_pending(tmp_path, role):
    flow = HistoryFlow(tmp_path)
    try:
        candles = _candles(flow)
        last = candles[-1]
        close_time = last.close_time_utc + timedelta(hours=1)
        pending = HourlyCandle(**(last.model_dump() | {
            "open_time_utc": last.close_time_utc, "close_time_utc": close_time,
            "available_at_utc": close_time + timedelta(minutes=40), "source_ref": "urn:synthetic-test-ohlc:pending",
            "source_hash": hashlib.sha256(b"synthetic-pending").hexdigest()}))
        _save(flow, [*candles, pending])
        flow.clock.advance(43 * 60)
        _, (request, snapshot) = flow.run(role)
        market = request.context["market"]["BTC/USD"]
        assert market["features"]["sma_20"] == "139.5"
        assert market["features"]["sma_50"] == "124.5"
        assert market["features"]["range_high"] == "150"
        assert market["features"]["midpoint"] == "137.5"
        history = market["history"]
        assert history["stale"] is True and history["feature_version"] == "hourly-price-features-v3"
        assert all(feature["status"] == "stale" for feature in history["features"].values())
        assert all(feature["window_end_close_time_utc"] == utc_iso(last.close_time_utc)
                   and feature["missing_close_times_utc"] == [utc_iso(close_time)]
                   for feature in history["features"].values())
        assert history["source_ref"] in request.context["evidence_refs"]
        assert snapshot["market"] == request.context["market"]
    finally:
        flow.db.close()
