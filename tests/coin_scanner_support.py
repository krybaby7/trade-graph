"""Helpers for the coin scanner tests: a synthetic catalog, hourly candles and a ticker. No network.

Everything built here is synthetic and labelled so. Prices follow a fixed zig-zag with exact decimals, so every
platform builds the same candles and the indicator library sees the same inputs.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

from tests.candle_support import FIXTURES
from trade_graph.adapters.market.kraken_ticker import TickerEntry, TickerSnapshot
from trade_graph.adapters.persistence.market_data import MarketDataDatabase
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.pair_classification import CLASSIFICATION_RULES_VERSION, classify_pair
from trade_graph.contracts.candles import Candle
from trade_graph.contracts.pair_catalog import ListedPair, PairRecord

# A scan at 12:05 reads the hour that closed at 12:00; candles arrive a few minutes after they close.
AS_OF = datetime(2026, 10, 16, 12, 5, tzinfo=UTC)
LAST_CLOSE = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
HOURS = 760  # NATR 14 on hourly candles reads 710; a little more so the look-behind sees history too
_TEMPLATE = json.loads((FIXTURES / "asset_pairs_2026-10-13.json").read_text(encoding="utf-8"))["result"]["SOLUSD"]


def listed(base: str, *, status: str = "online") -> ListedPair:
    return ListedPair(symbol=f"{base}/USD", source_pair=f"{base}USD", ws_name=f"{base}/USD", base=base, quote="ZUSD",
                      status=status, ordermin=_TEMPLATE["ordermin"], costmin=_TEMPLATE["costmin"],
                      tick_size=_TEMPLATE.get("tick_size"), pair_decimals=_TEMPLATE["pair_decimals"],
                      cost_decimals=_TEMPLATE["cost_decimals"], lot_decimals=_TEMPLATE["lot_decimals"])


def record(base: str, *, status: str = "online") -> PairRecord:
    return classify_pair(listed(base, status=status))


def save_catalog(store: CandleStore, records: Iterable[PairRecord], *, observed_at: datetime) -> None:
    run = store.begin_run(kind="catalog", source="synthetic", evidence_label="synthetic_fixture",
                          started_at=observed_at, parameters={"note": "coin scanner test"})
    store.save_catalog([item.model_dump(mode="json") for item in records], observed_at=observed_at, run_id=run,
                       rules_version=CLASSIFICATION_RULES_VERSION)
    store.finish_run(run, finished_at=observed_at, status="ok", report={"note": "coin scanner test"})


@dataclass(frozen=True)
class Path_:
    """A price path: ``level`` zig-zags by ``swing`` (a fraction) every hour around a straight ``drift`` per hour."""

    level: str
    swing: str = "0.01"
    drift: str = "0"
    volume: str = "100"
    vwap: bool = True


def hourly(symbol: str, path: Path_, *, last_close: datetime = LAST_CLOSE, hours: int = HOURS,
           source: str = "synthetic", delay_seconds: int = 180, skip: Iterable[int] = (),
           received_at: datetime | None = None) -> list[Candle]:
    """``hours`` completed hourly candles ending at ``last_close``. ``skip`` holds offsets from the newest (0)."""
    level, swing, drift = Decimal(path.level), Decimal(path.swing), Decimal(path.drift)
    skipped = set(skip)
    candles = []
    for index in range(hours):
        offset = hours - 1 - index
        if offset in skipped:
            continue
        close_time = last_close - timedelta(hours=offset)
        centre = level * (1 + drift * index)
        close = centre * (1 + swing / 2) if index % 2 else centre * (1 - swing / 2)
        opened = centre
        high, low = max(opened, close) * (1 + swing / 4), min(opened, close) * (1 - swing / 4)
        close, opened, high, low = (value.quantize(Decimal("0.00000001")) for value in (close, opened, high, low))
        vwap = ((high + low) / 2).quantize(Decimal("0.00000001")) if path.vwap else None
        candles.append(Candle(
            symbol=symbol, source_pair=symbol.replace("/", ""), interval_minutes=60,
            open_time_utc=close_time - timedelta(hours=1), close_time_utc=close_time,
            available_at_utc=received_at or close_time + timedelta(seconds=delay_seconds), open=opened, high=high,
            low=low, close=close, vwap=vwap, volume=Decimal(path.volume), trade_count=50, source=source))
    return candles


def save_candles(store: CandleStore, candles: list[Candle], *, label: str = "synthetic_fixture",
                 kind: str = "public_collect") -> None:
    if not candles:
        return
    source = candles[0].source
    run = store.begin_run(kind=kind, source=source, evidence_label=label,
                          started_at=min(item.available_at_utc for item in candles), parameters={"note": "scanner"})
    for start in range(0, len(candles), 40_000):
        store.save(candles[start:start + 40_000], run_id=run)
    store.finish_run(run, finished_at=max(item.available_at_utc for item in candles), status="ok", report={})


def ticker(quotes: Mapping[str, tuple[str, str]], *, received_at: datetime = AS_OF - timedelta(seconds=20),
           label: str = "synthetic_fixture") -> TickerSnapshot:
    """A ticker answer keyed the way Kraken keys plain pairs (``SOLUSD``)."""
    entries = {}
    for symbol, (bid, ask) in quotes.items():
        key = symbol.replace("/", "")
        entries[key] = TickerEntry(key=key, bid=Decimal(bid), ask=Decimal(ask), last=Decimal(bid))
    return TickerSnapshot(requested_at=received_at, received_at=received_at,
                          entries=MappingProxyType(dict(sorted(entries.items()))), rejected=(),
                          entries_in_response=len(entries), evidence_label=label)


def new_store(directory: Path) -> CandleStore:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return CandleStore(MarketDataDatabase(directory / "market_data.sqlite"))


def tight_quote(price: str, spread: str = "0.001") -> tuple[str, str]:
    """A bid and ask around ``price`` that are ``spread`` apart as a fraction of the mid."""
    mid, half = Decimal(price), Decimal(price) * Decimal(spread) / 2
    return str(mid - half), str(mid + half)
