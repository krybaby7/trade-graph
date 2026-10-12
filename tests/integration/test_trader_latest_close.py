"""The Trader sees the latest completed close and recent candles available at as_of (D114)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.integration.test_history_contexts import HistoryFlow, _save

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.model_invocations import MAX_REQUEST_BYTES
from trade_graph.application.price_history import HistoryStore
from trade_graph.contracts.price_history import HourlyCandle
from trade_graph.domain.clock import parse_utc, utc_iso
from trade_graph.kernel.department_gateway import PUBLIC_FIELDS, public_context

CANDLE_KEYS = {"open_time_utc", "close_time_utc", "available_at_utc", "open", "high", "low", "close", "volume"}
# A steady climb, then a pullback that holds the slower average: sma_50 62348 < close 63300 < sma_20 63695.
CLOSES = [Decimal(60000 + 100 * index) for index in range(45)] + [
    Decimal(value) for value in ("64200", "63900", "63600", "63400", "63300")]


def _hours(end, *, symbol="BTC/USD", latest_available_after=timedelta(minutes=20)):
    result = []
    for index, close in enumerate(CLOSES):
        close_time = end - timedelta(hours=len(CLOSES) - 1 - index)
        opened = CLOSES[index - 1] if index else close
        after = latest_available_after if index == len(CLOSES) - 1 else timedelta(minutes=20)
        result.append(HourlyCandle(
            symbol=symbol, open_time_utc=close_time - timedelta(hours=1), close_time_utc=close_time,
            available_at_utc=close_time + after, open=opened, high=max(opened, close) + 50,
            low=min(opened, close) - 50, close=close, vwap=close, volume=Decimal("12.34567891"), trade_count=9,
            source="kraken_public_ohlc", source_ref=f"urn:synthetic-test-ohlc:latest-close:{symbol}:{index}",
            source_hash=hashlib.sha256(f"synthetic-latest-close-{symbol}-{index}".encode()).hexdigest()))
    return result


def _end(flow):
    return flow.clock.now().replace(minute=0, second=0, microsecond=0)


@pytest.mark.parametrize("role", ["trader", "research"])
def test_actual_request_contains_latest_completed_close_and_recent_candles(tmp_path, role):
    flow = HistoryFlow(tmp_path, strategy_ids=["slow-trend-pullback"])
    try:
        end = _end(flow)
        candles = _hours(end)
        _save(flow, candles)
        _save(flow, _hours(end, symbol="ETH/USD"))
        _, (request, snapshot) = flow.run(role)
        context = request.context
        market = context["market"]["BTC/USD"]
        history = market["history"]
        assert history["latest_close"] == {
            "close_time_utc": utc_iso(end), "close": "63300", "available_at_utc": utc_iso(end + timedelta(minutes=20))}
        rows = history["recent_candles"]
        assert len(rows) == 24 and all(set(row) == CANDLE_KEYS for row in rows)
        assert [row["close_time_utc"] for row in rows] == [utc_iso(candle.close_time_utc) for candle in candles[-24:]]
        assert rows[-1] == {
            "open_time_utc": utc_iso(end - timedelta(hours=1)), "close_time_utc": utc_iso(end),
            "available_at_utc": utc_iso(end + timedelta(minutes=20)),
            "open": "63400", "high": "63450", "low": "63250", "close": "63300", "volume": "12.34567891"}
        assert all(type(value) is str for row in rows for value in row.values())
        assert all(parse_utc(row["available_at_utc"]) <= parse_utc(context["as_of"]) for row in rows)
        assert history["recent_missing_close_times_utc"] == []
        # The rule is now evaluable from the supplied data; software does not decide it.
        assert (Decimal(market["features"]["sma_50"]) < Decimal(history["latest_close"]["close"])
                < Decimal(market["features"]["sma_20"]))
        assert snapshot["market"]["BTC/USD"]["history"] == history
        assert history["source_ref"] in context["evidence_refs"]
        public = public_context({key: value for key, value in context.items() if key in PUBLIC_FIELDS})
        assert public["market"]["BTC/USD"]["history"]["recent_candles"] == rows
        assert len(request.model_dump_json().encode()) < MAX_REQUEST_BYTES
        assert len(json.dumps(context).encode()) < 80_000
    finally:
        flow.db.close()


@pytest.mark.parametrize("role", ["trader", "research"])
def test_close_stored_after_as_of_is_not_supplied_and_retained_input_never_changes(tmp_path, role):
    flow = HistoryFlow(tmp_path, strategy_ids=["slow-trend-pullback"])
    try:
        end = _end(flow)  # The turn runs at :30; the newest candle is received only at :40, as in the first run.
        candles = _hours(end, latest_available_after=timedelta(minutes=40))
        previous = candles[-2]
        correction = HourlyCandle(**(previous.model_dump() | {
            "close": Decimal("63399.5"), "vwap": Decimal("63399.5"), "available_at_utc": end + timedelta(minutes=35),
            "source_ref": "urn:synthetic-test-ohlc:latest-close:correction",
            "source_hash": hashlib.sha256(b"synthetic-latest-close-correction").hexdigest()}))
        _save(flow, [*candles[:-1], correction])
        task_id, (request, _) = flow.run(role)
        retained = request.model_dump_json()
        history = request.context["market"]["BTC/USD"]["history"]
        assert history["latest_close"] == {"close_time_utc": utc_iso(previous.close_time_utc), "close": "63400",
                                           "available_at_utc": utc_iso(previous.available_at_utc)}
        assert len(history["recent_candles"]) == 23
        assert history["recent_candles"][-1]["close_time_utc"] == utc_iso(previous.close_time_utc)
        assert history["recent_missing_close_times_utc"] == [utc_iso(end)]
        assert history["stale"] is True
        _save(flow, [candles[-1]])
        assert flow.retained(task_id)[0].model_dump_json() == retained
        flow.clock.advance(15 * 60)
        _, (later, _) = flow.run(role)
        current = later.context["market"]["BTC/USD"]["history"]
        assert current["latest_close"]["close"] == "63300"
        assert current["recent_candles"][-2]["close"] == "63399.5"
        assert current["recent_missing_close_times_utc"] == []
        assert current["source_ref"] != history["source_ref"]
        assert flow.retained(task_id)[0].model_dump_json() == retained
    finally:
        flow.db.close()


@pytest.mark.parametrize("role", ["trader", "research"])
def test_missing_history_is_explicit_not_zero(tmp_path, role):
    flow = HistoryFlow(tmp_path, strategy_ids=["slow-trend-pullback"])
    try:
        _, (request, _) = flow.run(role)
        for symbol in ("BTC/USD", "ETH/USD"):
            history = request.context["market"][symbol]["history"]
            assert history["latest_close"] is None
            assert history["recent_candles"] == []
            assert len(history["recent_missing_close_times_utc"]) == 24
            assert history["recent_missing_close_times_utc"][-1] == history["latest_expected_close_time_utc"]
    finally:
        flow.db.close()


def test_recent_series_is_independent_of_requested_feature_windows(tmp_path):
    from trade_graph.application import price_history

    assert price_history.RECENT_CANDLES == 24
    end = datetime(2026, 10, 10, 1, tzinfo=UTC)
    store = HistoryStore(Database(tmp_path / "recent.sqlite"))
    store.save(_hours(end, latest_available_after=timedelta(minutes=40)))
    as_of = datetime(2026, 10, 10, 1, 56, tzinfo=UTC)
    snapshots = [store.snapshot("BTC/USD", as_of, requested) for requested in (["hourly_return"], [], ["sma_50"])]
    assert snapshots[0]["recent_candles"] == snapshots[1]["recent_candles"] == snapshots[2]["recent_candles"]
    assert len(snapshots[0]["recent_candles"]) == 24
    assert all(snapshot["latest_close"] == {"close_time_utc": "2026-10-10T01:00:00.000000Z", "close": "63300",
                                            "available_at_utc": "2026-10-10T01:40:00.000000Z"}
               for snapshot in snapshots)
    # The retained candle manifest commits to every candle whose values the snapshot supplies.
    assert [len(snapshot["candle_ids"]) for snapshot in snapshots] == [24, 24, 50]
    for snapshot in snapshots:
        supplied = {item["close_time_utc"] for item in snapshot["provenance"]}
        assert supplied >= {row["close_time_utc"] for row in snapshot["recent_candles"]}
        assert len(supplied) == len(snapshot["candle_ids"])
    early = store.snapshot("BTC/USD", datetime(2026, 10, 10, 1, 13, tzinfo=UTC), ["sma_50"])
    assert early["latest_close"]["close_time_utc"] == "2026-10-10T00:00:00.000000Z"
    assert early["recent_missing_close_times_utc"] == ["2026-10-10T01:00:00.000000Z"]


def test_unsupported_symbol_history_has_explicit_empty_recent_series():
    from trade_graph.application.market_context import _unsupported_history

    history = _unsupported_history("TEST/EUR", datetime(2026, 10, 10, 1, 56, tzinfo=UTC), ["sma_20"], "synthetic")
    assert history["latest_close"] is None
    assert history["recent_candles"] == [] and history["recent_missing_close_times_utc"] == []


def test_decision_evidence_view_keeps_latest_close_and_recent_candles_only_as_scalars():
    from trade_graph.api import evidence

    candle = {"open_time_utc": "2026-10-10T00:00:00.000000Z", "close_time_utc": "2026-10-10T01:00:00.000000Z",
              "available_at_utc": "2026-10-10T01:40:00.000000Z", "open": "63400", "high": "63450",
              "low": "63250", "close": "63300", "volume": "12.34567891"}
    latest = {"close_time_utc": candle["close_time_utc"], "close": "63300",
              "available_at_utc": candle["available_at_utc"]}
    rows = [{**candle, "close": str(index), "vwap": "1", "prompt": "private row", "messages": ["private"]}
            for index in range(60)]
    payload = {"market": {"BTC/USD": {"history": {
        "latest_close": {**latest, "prompt": "private close"}, "recent_candles": rows,
        "recent_missing_close_times_utc": ["2026-10-10T01:00:00.000000Z"], "messages": ["private history"]}}},
        "prompt": "private prompt"}
    history = evidence._retained_inputs(payload, None)["market"]["BTC/USD"]["history"]
    assert history["latest_close"] == latest
    assert len(history["recent_candles"]) == 50
    assert all(set(row) == CANDLE_KEYS for row in history["recent_candles"])
    assert history["recent_candles"][-1] == {**candle, "close": "59"}
    assert history["recent_missing_close_times_utc"] == ["2026-10-10T01:00:00.000000Z"]
    encoded = json.dumps(history)
    assert "private" not in encoded and '"vwap"' not in encoded
    nested = {"market": {"BTC/USD": {"history": {"latest_close": "63300", "recent_candles": {"close": "1"}}}}}
    unexpected = evidence._retained_inputs(nested, None)["market"]["BTC/USD"]["history"]
    assert unexpected["latest_close"] is None and unexpected["recent_candles"] == []
