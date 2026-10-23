"""Bounded hourly features retain exact arithmetic and point-in-time inputs."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, localcontext

import pytest
from pydantic import ValidationError

from trade_graph.adapters.persistence.db import Database
from trade_graph.adapters.persistence.migrate import apply_migrations
from trade_graph.domain.clock import utc_iso

END = datetime(2026, 10, 5, 12, tzinfo=UTC)
AS_OF = END + timedelta(minutes=30)
FEATURES = ["sma_20", "sma_50", "pullback_from_high", "range_high", "range_low", "midpoint"]


def _contract():
    assert importlib.util.find_spec("trade_graph.contracts.price_history") is not None, "hourly candle contract missing"
    from trade_graph.contracts.price_history import HourlyCandle
    return HourlyCandle


def _api():
    HourlyCandle = _contract()
    assert importlib.util.find_spec("trade_graph.application.price_history") is not None, "hourly history store missing"
    from trade_graph.application.price_history import HistoryStore
    return HourlyCandle, HistoryStore


def _database(tmp_path):
    _api()
    database = Database(tmp_path / "hourly.sqlite")
    return database


def _candle(index=50, *, count=50, end=END, symbol="BTC/USD", source="synthetic", **overrides):
    HourlyCandle = _contract()
    close_time = end - timedelta(hours=count - index)
    value = Decimal(index)
    args = dict(
        symbol=symbol, interval_minutes=60, open_time_utc=close_time - timedelta(hours=1),
        close_time_utc=close_time, available_at_utc=close_time + timedelta(seconds=10),
        open=value, high=value + Decimal("1"), low=value - Decimal("0.5"), close=value,
        vwap=value, volume=Decimal("0.123456789123456789"), trade_count=2,
        source=source, source_ref=f"scripted-hour:{index}:{utc_iso(close_time)}",
        source_hash=hashlib.sha256(f"{symbol}:{index}".encode()).hexdigest(),
    )
    args.update(overrides)
    return HourlyCandle(**args)


def _populate(tmp_path, *, skip=(), count=50):
    _, HistoryStore = _api()
    database = _database(tmp_path)
    store = HistoryStore(database)
    candles = [_candle(index, count=count) for index in range(1, count + 1) if index not in skip]
    assert store.save(candles) == {"inserted": len(candles), "duplicates": 0}
    return database, store, candles


def test_installed_template_features_use_exact_decimal_hourly_windows(tmp_path):
    _, store, candles = _populate(tmp_path)
    with localcontext() as context:
        context.prec = 6
        context.rounding = "ROUND_DOWN"
        result = store.snapshot("BTC/USD", AS_OF, FEATURES, source="synthetic")
    assert result["values"] == {
        "sma_20": "40.5", "sma_50": "25.5",
        "pullback_from_high": "0.01960784313725490196078431372549019607843137254902",
        "range_high": "51", "range_low": "26.5", "midpoint": "38.75",
    }
    assert all(feature["status"] == "ready" for feature in result["features"].values())
    assert result["features"]["sma_50"]["lookback_candles"] == 50
    assert result["features"]["range_high"]["lookback_candles"] == 24
    assert len(result["features"]["sma_20"]["candle_ids"]) == 20
    assert result["latest_close_time_utc"] == utc_iso(END)
    assert result["event_time_utc"] == utc_iso(END)
    assert result["available_at_utc"] == utc_iso(candles[-1].available_at_utc)
    assert result["interval_minutes"] == 60 and result["source"] == "synthetic"
    assert result["stale"] is False
    assert result["source_ref"].startswith("hourly-features:")
    assert result["provenance"][-1]["source_hash"] == candles[-1].source_hash
    json.dumps(result, allow_nan=False)


def test_starter_fallback_features_have_documented_exact_definitions(tmp_path):
    _, store, _ = _populate(tmp_path)
    requested = ["hourly_return", "four_hour_drift", "pullback_depth", "midpoint_distance"]
    result = store.snapshot("BTC/USD", AS_OF, requested, source="synthetic")
    assert result["values"] == {
        "hourly_return": "0.0204081632653061224489795918367346938775510204082",
        "four_hour_drift": "0.0869565217391304347826086956521739130434782608696",
        "pullback_depth": "0.01960784313725490196078431372549019607843137254902",
        "midpoint_distance": "0.29032258064516129032258064516129032258064516129032",
    }
    assert result["features"]["hourly_return"]["lookback_candles"] == 2
    assert result["features"]["four_hour_drift"]["lookback_candles"] == 5
    assert result["features"]["midpoint_distance"]["definition"]


def test_short_prefix_is_insufficient_and_internal_missing_hour_is_a_gap(tmp_path):
    _, store, _ = _populate(tmp_path, skip=[35])
    result = store.snapshot("BTC/USD", AS_OF, FEATURES, source="synthetic")
    assert result["features"]["sma_20"]["status"] == "gapped_history"
    assert result["features"]["sma_20"]["available_candles"] == 19
    assert result["features"]["sma_20"]["missing_close_times_utc"] == [utc_iso(END - timedelta(hours=15))]
    assert "sma_20" not in result["values"]
    assert result["features"]["range_high"]["status"] == "gapped_history"


def test_insufficient_prefix_does_not_invent_history(tmp_path):
    _, store, _ = _populate(tmp_path, count=19)
    result = store.snapshot("BTC/USD", AS_OF, ["sma_20", "sma_50", "hourly_return"], source="synthetic")
    assert result["features"]["sma_20"]["status"] == "insufficient_history"
    assert result["features"]["sma_50"]["available_candles"] == 19
    assert len(result["features"]["sma_50"]["missing_close_times_utc"]) == 31
    assert result["values"] == {"hourly_return": "0.0555555555555555555555555555555555555555555555556"}


def test_stale_tail_is_explicit_and_cannot_extend_lookback_backwards(tmp_path):
    _, store, _ = _populate(tmp_path, skip=[50])
    result = store.snapshot("BTC/USD", AS_OF, ["sma_20"], source="synthetic")
    feature = result["features"]["sma_20"]
    assert result["stale"] and result["latest_close_time_utc"] == utc_iso(END - timedelta(hours=1))
    assert feature["status"] == "gapped_history" and feature["available_candles"] == 19
    assert feature["missing_close_times_utc"] == [utc_iso(END)]
    assert result["values"] == {}


def test_empty_and_unsupported_features_are_explicit_and_sources_never_mix(tmp_path):
    _, store, _ = _populate(tmp_path)
    result = store.snapshot("BTC/USD", AS_OF, ["sma_20", "made_up"])
    assert result["stale"] and result["candle_ids"] == []
    assert result["features"]["sma_20"]["status"] == "insufficient_history"
    assert result["features"]["made_up"]["status"] == "unsupported"
    assert result["values"] == {}
    assert store.snapshot("ETH/USD", AS_OF, FEATURES, source="synthetic")["values"] == {}


def test_future_candles_later_availability_and_revisions_cannot_change_retained_context(tmp_path):
    database, store, candles = _populate(tmp_path)
    before = store.snapshot("BTC/USD", AS_OF, FEATURES, source="synthetic")
    retained = json.dumps(before, sort_keys=True)
    revised = _candle(50, close=Decimal("49"), low=Decimal("48"), available_at_utc=AS_OF + timedelta(hours=1),
                      source_hash="b" * 64)
    later = _candle(50, end=END + timedelta(hours=1))
    delayed = _candle(49, close=Decimal("48"), low=Decimal("47"), available_at_utc=AS_OF + timedelta(hours=2),
                      source_hash="c" * 64)
    assert store.save([revised, later, delayed]) == {"inserted": 3, "duplicates": 0}
    assert json.dumps(store.snapshot("BTC/USD", AS_OF, FEATURES, source="synthetic"), sort_keys=True) == retained
    assert len(database.execute("SELECT * FROM hourly_candles").fetchall()) == 53
    current = store.snapshot("BTC/USD", AS_OF + timedelta(hours=1), ["hourly_return"], source="synthetic")
    assert current["values"]["hourly_return"] == "0.0204081632653061224489795918367346938775510204082"
    assert before["available_at_utc"] == utc_iso(candles[-1].available_at_utc)


def test_new_available_revision_changes_later_context_only(tmp_path):
    _, store, _ = _populate(tmp_path)
    revised = _candle(50, close=Decimal("49"), low=Decimal("48"), available_at_utc=AS_OF + timedelta(minutes=10),
                      source_hash="b" * 64)
    before = store.snapshot("BTC/USD", AS_OF, ["hourly_return"], source="synthetic")
    store.save([revised])
    assert store.snapshot("BTC/USD", AS_OF, ["hourly_return"], source="synthetic") == before
    after = store.snapshot("BTC/USD", AS_OF + timedelta(minutes=10), ["hourly_return"], source="synthetic")
    assert before["values"]["hourly_return"] != after["values"]["hourly_return"] == "0"
    assert after["provenance"][-1]["source_hash"] == "b" * 64


def test_correction_can_return_to_earlier_content_without_erasing_revisions(tmp_path):
    database, store, candles = _populate(tmp_path)
    initial = store.snapshot("BTC/USD", AS_OF, ["hourly_return"], source="synthetic")
    changed_at = AS_OF + timedelta(minutes=5)
    changed = _candle(50, close="49", low="48", available_at_utc=changed_at, source_hash="b" * 64)
    assert store.save([changed]) == {"inserted": 1, "duplicates": 0}
    changed_context = store.snapshot("BTC/USD", changed_at, ["hourly_return"], source="synthetic")
    reverted_at = AS_OF + timedelta(minutes=10)
    reverted = type(candles[-1])(**(candles[-1].model_dump() | {"available_at_utc": reverted_at}))
    assert store.save([reverted]) == {"inserted": 1, "duplicates": 0}
    reverted_context = store.snapshot("BTC/USD", reverted_at, ["hourly_return"], source="synthetic")
    assert reverted_context["values"] == initial["values"]
    assert reverted_context["candle_ids"][-1] != initial["candle_ids"][-1]
    assert store.snapshot("BTC/USD", changed_at, ["hourly_return"], source="synthetic") == changed_context
    assert store.snapshot("BTC/USD", AS_OF, ["hourly_return"], source="synthetic") == initial
    database.close()
    _, HistoryStore = _api()
    reopened = Database(tmp_path / "hourly.sqlite")
    assert HistoryStore(reopened).save([reverted]) == {"inserted": 0, "duplicates": 1}
    assert reopened.execute("SELECT COUNT(*) FROM hourly_candles").fetchone()[0] == 52


def test_restart_deduplicates_canonical_content_preserving_first_receipt(tmp_path):
    database, store, candles = _populate(tmp_path)
    before = store.snapshot("BTC/USD", AS_OF, FEATURES, source="synthetic")
    database.close()
    _, HistoryStore = _api()
    reopened = Database(tmp_path / "hourly.sqlite")
    store = HistoryStore(reopened)
    replay = [type(candle)(**(candle.model_dump() | {"available_at_utc": AS_OF + timedelta(days=1)}))
              for candle in candles]
    assert store.save(replay) == {"inserted": 0, "duplicates": 50}
    assert store.snapshot("BTC/USD", AS_OF, FEATURES, source="synthetic") == before
    assert reopened.execute("SELECT COUNT(*) FROM hourly_candles").fetchone()[0] == 50


def test_storage_is_append_only_and_bounded_ingestion_is_atomic(tmp_path):
    database, store, candles = _populate(tmp_path)
    for sql in ["UPDATE hourly_candles SET source='synthetic'", "DELETE FROM hourly_candles"]:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            database.execute(sql)
    with pytest.raises(ValueError, match="720"):
        store.save([candles[-1]] * 721)
    assert database.execute("SELECT COUNT(*) FROM hourly_candles").fetchone()[0] == 50


@pytest.mark.parametrize("invalid_high", [100.0, Decimal("1e1000000000")])
def test_bypassed_model_copy_is_revalidated_before_any_write(tmp_path, invalid_high):
    database, store, candles = _populate(tmp_path)
    invalid = candles[-1].model_copy(update={"high": invalid_high})
    with pytest.raises(ValidationError):
        store.save([_candle(50, end=END + timedelta(hours=1)), invalid])
    assert database.execute("SELECT COUNT(*) FROM hourly_candles").fetchone()[0] == 50


@pytest.mark.parametrize("overrides", [
    {"symbol": "BTC/EUR"}, {"interval_minutes": 30}, {"interval_minutes": 60.0},
    {"open": 1.1}, {"volume": True}, {"close": "NaN"}, {"high": "Infinity"},
    {"low": "0"}, {"volume": "-1"}, {"trade_count": -1}, {"trade_count": 2.0},
    {"open_time_utc": END.replace(tzinfo=None)}, {"close_time_utc": END + timedelta(minutes=1)},
    {"available_at_utc": END - timedelta(seconds=1)}, {"open_time_utc": END - timedelta(hours=2)},
    {"high": "1"}, {"source_hash": "invalid"}, {"source_ref": "x" * 513},
    {"high": "1e1000000000"}, {"volume": "1e-1000000000"}, {"high": "9" * 129},
])
def test_candle_rejects_invalid_or_incomplete_input(overrides):
    _contract()
    with pytest.raises(ValidationError):
        _candle(**overrides)


def test_candle_is_immutable_and_normalizes_timezone_without_rounding():
    candle = _candle(available_at_utc=(END + timedelta(seconds=10)).astimezone(timezone(timedelta(hours=2))))
    assert candle.available_at_utc.tzinfo == UTC
    assert candle.model_dump(mode="json")["volume"] == "0.123456789123456789"
    with pytest.raises(ValidationError, match="frozen"):
        candle.close = Decimal("1")


def test_snapshot_rejects_naive_time_and_invalid_scope(tmp_path):
    _, store, _ = _populate(tmp_path)
    with pytest.raises(ValueError, match="timezone"):
        store.snapshot("BTC/USD", END.replace(tzinfo=None), FEATURES)
    with pytest.raises(ValueError, match="symbol"):
        store.snapshot("BTC/EUR", AS_OF, FEATURES)
    with pytest.raises(ValueError, match="source"):
        store.snapshot("BTC/USD", AS_OF, FEATURES, source="mixed")


def test_history_schema_migration_preserves_existing_records(tmp_path):
    database = _database(tmp_path)
    database.execute("INSERT INTO activity_events VALUES ('old-event', NULL, 'fixture', '{}', ?, 'old-hash', NULL)",
                     (utc_iso(AS_OF),))
    assert apply_migrations(database.connection, utc_iso(AS_OF)) == []
    assert database.execute("SELECT hash FROM activity_events WHERE event_id='old-event'").fetchone()[0] == "old-hash"
