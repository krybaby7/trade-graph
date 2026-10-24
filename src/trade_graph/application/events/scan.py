"""The day start's scan step: the coin scanner's shortlist that Research reviews (T31, D120).

``CoinScanStep`` runs one live scan at the day start and records it; with no step configured, the day start uses
the newest live scan already recorded, labelled with its source and moment. Scheduling the hourly scan inside the
service is not done here: W6-D (C2) owns the in-service price collector and the hourly scan that follows it.

A desk wake's shortlist carries each coin's market type (W12-A, ``with_market_types``): W11-A's label in force at the
wake's moment, or none with its status (``stale`` or ``missing``). The hourly scan labels coins; the day start's scan
step does not, so a coin's label comes from the hourly round, never from a guess here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Protocol

from trade_graph.application.coin_scanner.store import CoinScanStore, StoredScan
from trade_graph.contracts.trading_desk import CoinMarketType
from trade_graph.contracts.wakes import ScanReference, ShortlistCoin


class DayStartScan(Protocol):
    """Called once per portfolio and trading day at the day start; returns the shortlist for Research."""

    def __call__(self, *, portfolio_id: str, as_of: datetime) -> ScanReference: ...


def scan_reference(scan: StoredScan, *, source: str) -> ScanReference:
    shortlist = tuple(ShortlistCoin(symbol=entry["symbol"], position=int(entry["position"]),
                                    flags=tuple(str(flag) for flag in entry.get("flags", ())))
                      for entry in scan.entries)
    status = scan.status if scan.status in {"ok", "no_candidates", "failed"} else "failed"
    return ScanReference(status=status, source=source, scan_id=scan.scan_id, as_of=scan.as_of,
                         ranking_version=scan.ranking_version, evidence=scan.evidence, optimistic=scan.optimistic,
                         shortlist=shortlist, error=(scan.error or None) and str(scan.error)[:500])


def with_market_types(scan: ScanReference, readings: Mapping[str, CoinMarketType]) -> ScanReference:
    """The shortlist with each coin's market type (W12-A): the type in force, or None with ``stale`` or ``missing``.
    A coin without a reading is shown as missing; the scan itself is unchanged."""
    shortlist = []
    for coin in scan.shortlist:
        reading = readings.get(coin.symbol)
        shortlist.append(coin.model_copy(update={
            "market_type": None if reading is None else reading.market_type,
            "market_type_status": "missing" if reading is None else reading.status}))
    return scan.model_copy(update={"shortlist": tuple(shortlist)})


def latest_recorded(store: CoinScanStore, as_of: datetime) -> ScanReference:
    """The newest live scan at or before ``as_of``, whatever its age (the reference says when it was made)."""
    scan = store.latest(mode="live", as_of=as_of)
    if scan is None:
        return ScanReference(status="none", source="none", error="no live coin scan has been recorded yet")
    return scan_reference(scan, source="latest_recorded")


class CoinScanStep:
    """Run the coin scanner at the day start and record the scan (software only, no AI).

    ``ticker`` supplies the one public Ticker answer the live scan needs (W6-D wires the real request into the
    service; tests pass a recorded or synthetic answer).
    """

    def __init__(self, scanner, store: CoinScanStore, *, ticker: Callable[[], object] | None = None) -> None:
        self.scanner = scanner
        self.store = store
        self.ticker = ticker

    def __call__(self, *, portfolio_id: str, as_of: datetime) -> ScanReference:
        result = self.scanner.scan(as_of=as_of, ticker=self.ticker() if self.ticker else None)
        return scan_reference(self.store.record(result), source="day_start_scan")


__all__ = ["CoinScanStep", "DayStartScan", "latest_recorded", "scan_reference", "with_market_types"]
