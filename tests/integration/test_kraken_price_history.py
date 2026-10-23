"""Scripted Kraken hourly responses; no network or account credentials."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from trade_graph.adapters.persistence.db import Database
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import ValidationFailure

NOW = datetime(2026, 10, 6, 12, 20, tzinfo=UTC)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _unix(value: datetime) -> int:
    delta = value - EPOCH
    return delta.days * 86400 + delta.seconds


def _row(open_time: datetime, *, close: str = "10.25") -> list:
    return [_unix(open_time), "10.10", "11.20", "9.05", close, "10.15", "0.00000003", 2]


def _wire(rows: list, *, key: str = "XXBTZUSD") -> str:
    return json.dumps({"error": [], "result": {key: rows, "last": _unix(NOW.replace(minute=0))}})


class ScriptedTransport:
    def __init__(self, responses: list[str | Exception], clock: FrozenClock, *, delay_seconds: int = 0) -> None:
        self.responses = list(responses)
        self.clock = clock
        self.delay_seconds = delay_seconds
        self.calls: list[str] = []

    def get_text(self, url: str) -> str:
        self.calls.append(url)
        self.clock.advance(self.delay_seconds)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _adapter(transport: ScriptedTransport):
    from trade_graph.adapters.market.kraken_history import KrakenHourlyHistory

    return KrakenHourlyHistory(transport, transport.clock)


def test_completed_candles_preserve_exact_prices_and_receipt_availability() -> None:
    clock = FrozenClock(NOW)
    early = NOW.replace(hour=10, minute=0)
    raw = _wire([_row(early), _row(early + timedelta(hours=1)), _row(early + timedelta(hours=2))])
    transport = ScriptedTransport([raw], clock, delay_seconds=9)
    candles = _adapter(transport).fetch("BTC/USD", hours=2)
    assert len(candles) == 2
    assert candles[0].close == Decimal("10.25")
    assert candles[0].volume == Decimal("0.00000003")
    assert candles[0].available_at_utc == NOW + timedelta(seconds=9)
    assert candles[0].close_time_utc == early + timedelta(hours=1)
    assert candles[0].source == "kraken_public_ohlc"
    assert candles[0].source_ref == f"kraken:/0/public/OHLC:XBTUSD:60:{_unix(early)}"
    assert len(candles[0].source_hash) == 64
    assert transport.calls == [
        f"https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=60&since={_unix(early)}"
    ]


def test_final_row_is_always_dropped_even_when_it_looks_historically_complete() -> None:
    clock = FrozenClock(NOW)
    early = NOW.replace(hour=10, minute=0)
    transport = ScriptedTransport([_wire([_row(early), _row(early - timedelta(days=10))])], clock)
    candles = _adapter(transport).fetch("BTC/USD", hours=2)
    assert [candle.open_time_utc for candle in candles] == [early]


def test_numeric_json_decimals_and_stable_canonical_row_identity() -> None:
    clock = FrozenClock(NOW)
    early = NOW.replace(hour=11, minute=0)
    rows = [_row(early), _row(early + timedelta(hours=1))]
    first = _wire(rows)
    numeric = first.replace('"10.10"', "10.100").replace('"10.25"', "10.250")
    transport = ScriptedTransport([first, numeric], clock)
    original = _adapter(transport).fetch("BTC/USD", hours=1)[0]
    clock.advance(60)
    repeated = _adapter(transport).fetch("BTC/USD", hours=1)[0]
    assert repeated.open == Decimal("10.1")
    assert original.source_hash == repeated.source_hash
    assert original.source_ref == repeated.source_ref


@pytest.mark.parametrize("hours", [0, 720, -1, True, 1.5, "2"])
def test_hour_bounds_reject_before_any_request(hours) -> None:
    clock = FrozenClock(NOW)
    transport = ScriptedTransport([], clock)
    with pytest.raises(ValidationFailure):
        _adapter(transport).fetch("BTC/USD", hours=hours)
    assert transport.calls == []


@pytest.mark.parametrize("symbol", ["BTC/USDT", "EUR/USD", "XBTUSD", "../Balance", "ETH/EUR"])
def test_symbol_allowlist_rejects_before_any_request(symbol: str) -> None:
    clock = FrozenClock(NOW)
    transport = ScriptedTransport([], clock)
    with pytest.raises(ValidationFailure):
        _adapter(transport).fetch(symbol, hours=2)
    assert transport.calls == []


@pytest.mark.parametrize("bad_rows", [
    lambda early: [_row(early), _row(early), _row(early + timedelta(hours=2))],
    lambda early: [_row(early + timedelta(hours=1)), _row(early), _row(early + timedelta(hours=2))],
    lambda early: [_row(early + timedelta(hours=2)), _row(early + timedelta(hours=3))],
    lambda early: [[_unix(early) + 1, *_row(early)[1:]], _row(early + timedelta(hours=2))],
    lambda early: [[str(_unix(early)), *_row(early)[1:]], _row(early + timedelta(hours=2))],
    lambda early: [[float(_unix(early)), *_row(early)[1:]], _row(early + timedelta(hours=2))],
    lambda early: [[_unix(early), "10", "9", "8", "10", "9", "1", 2], _row(early + timedelta(hours=2))],
    lambda early: [[*_row(early)[:-1], True], _row(early + timedelta(hours=2))],
    lambda early: [[*_row(early)[:6], "-1", 2], _row(early + timedelta(hours=2))],
    lambda early: [[*_row(early)[:4], "NaN", *_row(early)[5:]], _row(early + timedelta(hours=2))],
    lambda early: [[*_row(early)[:7], 2.5], _row(early + timedelta(hours=2))],
    lambda early: [[*_row(early), "extra"], _row(early + timedelta(hours=2))],
    lambda early: [[_unix(early), *("1e1000" for _ in range(5)), "1", 2], _row(early + timedelta(hours=2))],
    lambda early: [[*_row(early)[:6], "1e-1000", 2], _row(early + timedelta(hours=2))],
    lambda early: [[253402297200, *_row(early)[1:]], _row(early + timedelta(hours=2))],
])
def test_invalid_retained_rows_are_rejected(bad_rows) -> None:
    clock = FrozenClock(NOW)
    early = NOW.replace(hour=10, minute=0)
    raw = _wire(bad_rows(early))
    transport = ScriptedTransport([raw], clock)
    with pytest.raises(ValidationFailure):
        _adapter(transport).fetch("BTC/USD", hours=2)


@pytest.mark.parametrize("raw", [
    "not-json",
    "[]",
    '{"error":["bad"],"result":{}}',
    '{"error":[],"result":{}}',
    '{"error":[],"result":{"ETHUSD":[],"last":1}}',
    '{"error":[],"result":{"XXBTZUSD":[],"XBTUSD":[],"last":1}}',
    '{"error":[],"result":{"XXBTZUSD":[],"last":true}}',
    '{"error":["rejected"],"error":[],"result":{"XXBTZUSD":[],"last":1}}',
    " " * 1_048_577,
], ids=["json", "object", "errors", "missing", "pair", "ambiguous", "cursor", "duplicate-keys", "bytes"])
def test_invalid_response_is_sanitized_and_bounded(raw: str) -> None:
    clock = FrozenClock(NOW)
    transport = ScriptedTransport([raw], clock)
    with pytest.raises(ValidationFailure):
        _adapter(transport).fetch("BTC/USD", hours=2)


def test_maximum_response_rows_and_old_rows_are_bounded() -> None:
    clock = FrozenClock(NOW)
    early = NOW.replace(hour=10, minute=0)
    too_many = ScriptedTransport([_wire([_row(early)] * 721)], clock)
    with pytest.raises(ValidationFailure):
        _adapter(too_many).fetch("BTC/USD", hours=2)
    normal = ScriptedTransport([
        _wire([_row(early - timedelta(hours=1)), _row(early), _row(early + timedelta(hours=2))])
    ], clock)
    assert len(_adapter(normal).fetch("BTC/USD", hours=2)) == 1


def test_collector_reports_gaps_and_keeps_first_symbol_on_second_failure(tmp_path: Path) -> None:
    from trade_graph.application.collect_price_history import collect_public_hourly_history

    clock = FrozenClock(NOW)
    early = NOW.replace(hour=8, minute=0)
    raw = _wire([_row(early), _row(early + timedelta(hours=2)), _row(NOW.replace(minute=0))])
    transport = ScriptedTransport([raw, RuntimeError("PRIVATE ERROR MUST NOT APPEAR")], clock)
    database = Database(tmp_path / "history.sqlite")
    report = collect_public_hourly_history(database, clock, transport, hours=4)
    btc = report["symbols"]["BTC/USD"]
    assert report["status"] == "partial"
    assert report["request_count"] == 2
    assert report["paid_calls_enabled"] is False
    assert report["live_trading_enabled"] is False
    assert btc["fetched"] == btc["inserted"] == 2
    assert btc["duplicates"] == 0
    assert btc["insufficient_history"] is True
    assert btc["missing_hours"] == 2
    assert [gap["open_time_utc"] for gap in btc["gaps"]] == [
        "2026-10-06T09:00:00.000000Z", "2026-10-06T11:00:00.000000Z"
    ]
    assert report["symbols"]["ETH/USD"]["error"] == "public_history_request_failed"
    assert "PRIVATE ERROR" not in json.dumps(report)
    database.close()
    reopened = Database(tmp_path / "history.sqlite")
    repeated = collect_public_hourly_history(reopened, clock, ScriptedTransport([raw], clock),
                                            hours=4, symbols=("BTC/USD",))
    assert repeated["symbols"]["BTC/USD"]["inserted"] == 0
    assert repeated["symbols"]["BTC/USD"]["duplicates"] == 2
    reopened.close()


def test_collector_accepts_only_unique_bounded_symbols_before_network(tmp_path: Path) -> None:
    from trade_graph.application.collect_price_history import collect_public_hourly_history

    clock = FrozenClock(NOW)
    transport = ScriptedTransport([], clock)
    database = Database(tmp_path / "history.sqlite")
    for symbols in [(), ("BTC/USD", "BTC/USD"), ("BTC/USDT",), ("BTC/USD", "ETH/USD", "EUR/USD")]:
        with pytest.raises(ValidationFailure):
            collect_public_hourly_history(database, clock, transport, symbols=symbols)
    assert transport.calls == []
    database.close()


def test_collector_uses_only_two_fixed_public_requests_without_paid_or_live_effects(tmp_path: Path) -> None:
    from trade_graph.application.collect_price_history import collect_public_hourly_history

    clock = FrozenClock(NOW)
    early = NOW.replace(hour=11, minute=0)
    rows = [_row(early), _row(early + timedelta(hours=1))]
    transport = ScriptedTransport([_wire(rows), _wire(rows, key="XETHZUSD")], clock)
    database = Database(tmp_path / "history.sqlite")
    report = collect_public_hourly_history(database, clock, transport, hours=1)
    assert report["status"] == "ok"
    assert transport.calls == [
        f"https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=60&since={_unix(early)}",
        f"https://api.kraken.com/0/public/OHLC?pair=ETHUSD&interval=60&since={_unix(early)}",
    ]
    for symbol in ("BTC/USD", "ETH/USD"):
        assert report["symbols"][symbol]["inserted"] == 1
        assert report["symbols"][symbol]["insufficient_history"] is False
        assert report["symbols"][symbol]["missing_hours"] == 0
    database.close()
