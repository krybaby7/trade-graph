"""The candle file inside the service (W6-D, D222 and D224): one writer, one reader, the collector and its coins.

An installation keeps its candle file beside the financial database (``<state>/market_data.sqlite``, D224), private
like it but never part of the financial books or their recovery (D132). The service opens it twice:

- a **writer** (``MarketDataDatabase``, which creates and upgrades the file) used only by the price collector's own
  thread (``application/price_collector.py``);
- a **read-only** handle (``ReadOnlyMarketData``) for everything else in the service: the Trader's and Research's
  hourly features (``CandleStoreHistory``, D220), the price watcher's candle and indicator alarms (T33) and the coin
  scanner's reads in the day start. None of them can change a candle.

``service_hot_coins`` names the coins the team works with now, read from saved records only: BTC/USD and ETH/USD
(reference and HQ's charts), the quote feed's coins, the newest live shortlist, today's focus coins, held coins,
coins with an armed alarm and coins with a day plan that has not ended. The collector reads them before each chunk.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from trade_graph.adapters.market.rate_limit import KrakenRestLimiter, service_limiter
from trade_graph.adapters.persistence.db import Database
from trade_graph.adapters.persistence.market_data import MarketDataDatabase
from trade_graph.adapters.persistence.market_data_readonly import ReadOnlyMarketData
from trade_graph.application.alarms.book import AlarmBook
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.coin_scanner.scanner import CoinScanner
from trade_graph.application.coin_scanner.store import CoinScanStore
from trade_graph.application.price_collector import (
    CollectorSettings,
    CollectorTransports,
    HotCoins,
    InterruptibleSleeper,
    PriceCollector,
)
from trade_graph.application.price_history import CandleStoreHistory
from trade_graph.contracts.wakes import FocusSelection
from trade_graph.domain.clock import Clock, parse_utc, utc_iso

MARKET_DATABASE_NAME = "market_data.sqlite"
REFERENCE_COINS = ("BTC/USD", "ETH/USD")
_FIAT = frozenset({"USD", "EUR", "ZUSD", "ZEUR"})


def market_database_beside(database_path: Path) -> Path:
    """The installation's candle file: next to the financial database, in the same private state folder (D224)."""
    return Path(database_path).parent / MARKET_DATABASE_NAME


def _quietly(read: Callable[[], Any], default: Any) -> Any:
    try:
        return read()
    except Exception:  # noqa: BLE001 - a missing record never stops collection; the coin is simply not named
        return default


def service_hot_coins(database: Database, clock: Clock, portfolio_ids: Sequence[str], *, ledger=None,
                      paper_symbols: Sequence[str] = ()) -> HotCoins:
    """The coins the team works with now, from saved records only (no request, no model)."""
    now = clock.now()
    at = utc_iso(now)
    symbols: set[str] = set(REFERENCE_COINS) | set(paper_symbols)
    one_minute: set[str] = set()
    shortlist = _quietly(lambda: [row["symbol"] for row in database.execute(
        "SELECT symbol FROM coin_shortlist_entries WHERE scan_sequence = "
        "(SELECT MAX(sequence) FROM coin_scans WHERE mode = 'live' AND as_of <= ?)", (at,)).fetchall()], [])
    symbols.update(shortlist)
    focus_rows = _quietly(lambda: database.execute(
        "SELECT document_json FROM focus_selections WHERE trading_day_start <= ? AND trading_day_end > ? "
        "ORDER BY sequence DESC LIMIT 16", (at, at)).fetchall(), [])
    for row in focus_rows:
        focus = _quietly(lambda row=row: FocusSelection.model_validate_json(row["document_json"]).symbols(), ())
        symbols.update(focus)
        one_minute.update(focus)
    plans = _quietly(lambda: [row["symbol"] for row in database.execute(
        "SELECT DISTINCT symbol FROM day_plans WHERE trading_day_end > ?", (at,)).fetchall()], [])
    symbols.update(plans)
    for alarm in _quietly(lambda: AlarmBook(database, clock).armed(), []):
        condition = alarm.condition
        symbols.add(condition.symbol)
        if getattr(condition, "interval_minutes", None) == 1:
            one_minute.add(condition.symbol)
    if ledger is not None:
        for portfolio_id in portfolio_ids:
            books = _quietly(lambda portfolio_id=portfolio_id: ledger.books(portfolio_id, at), None)
            for lot in getattr(books, "lots", ()):
                if lot.asset not in _FIAT and lot.open_quantity() > Decimal("0"):
                    symbols.add(f"{lot.asset}/USD")
    usd = {symbol for symbol in symbols if isinstance(symbol, str) and symbol.endswith("/USD")}
    return HotCoins(symbols=frozenset(usd), one_minute=frozenset(one_minute & usd))


def _latest_live_scan(database: Database) -> datetime | None:
    row = database.execute("SELECT MAX(as_of) AS newest FROM coin_scans WHERE mode = 'live'").fetchone()
    return None if row is None or row["newest"] is None else parse_utc(row["newest"])


@dataclass
class ServiceMarketData:
    """What the service holds of the candle file; ``close`` releases both handles."""

    path: Path
    writer: MarketDataDatabase
    reader: ReadOnlyMarketData
    store: CandleStore            # the collector's writer view
    candles: CandleStore          # the read-only view for the features, the watcher and the day start
    history: CandleStoreHistory
    limiter: KrakenRestLimiter
    collector: PriceCollector | None
    stop: threading.Event

    def close(self) -> None:
        if self.collector is not None:
            self.collector.request_stop()
        for handle in (self.reader, self.writer):
            try:
                handle.close()
            except Exception:  # noqa: BLE001 - closing one handle must not keep the other open
                pass


def open_service_market_data(path: Path, *, database: Database, clock: Clock, portfolio_ids: Sequence[str],
                             ledger=None, paper_symbols: Sequence[str] = (),
                             transports: CollectorTransports | None = None,
                             settings: CollectorSettings | None = None, limiter: KrakenRestLimiter | None = None,
                             evidence_label: str = "live_public",
                             record: Callable[[dict[str, Any]], None] | None = None) -> ServiceMarketData:
    """Open (and create, private) the candle file; build the collector when ``transports`` are given."""
    writer = MarketDataDatabase(path)
    try:
        reader = ReadOnlyMarketData(path)
    except BaseException:
        writer.close()
        raise
    store, candles = CandleStore(writer), CandleStore(reader)
    stop = threading.Event()
    limiter = limiter or service_limiter(sleeper=InterruptibleSleeper(stop))
    collector = None
    if transports is not None:
        scans = CoinScanStore(database, clock)

        def scan(as_of: datetime, ticker, ticker_error: str | None):
            # The scanner reads the collector's own view, so it sees the hour the pass just stored.
            result = CoinScanner(store, clock=clock).scan(as_of=as_of, ticker=ticker, ticker_error=ticker_error)
            return scans.record(result)

        collector = PriceCollector(
            store, clock, transports, limiter, settings=settings, scan=scan, stop=stop, record=record,
            last_scan_at=lambda: _latest_live_scan(database), evidence_label=evidence_label,
            coins=lambda: service_hot_coins(database, clock, portfolio_ids, ledger=ledger,
                                            paper_symbols=paper_symbols))
    return ServiceMarketData(path=Path(path), writer=writer, reader=reader, store=store, candles=candles,
                             history=CandleStoreHistory(candles), limiter=limiter, collector=collector, stop=stop)


def activity_recorder(ledger, portfolio_ids: Sequence[str]) -> Callable[[dict[str, Any]], None]:
    """Record a collector event as ``price_collection`` activity for each portfolio (HQ's activity log reads it)."""

    def record(event: dict[str, Any]) -> None:
        payload = json.loads(json.dumps(event, default=str))
        for portfolio_id in portfolio_ids:
            ledger._activity(portfolio_id, "price_collection", payload)

    return record


__all__ = ["MARKET_DATABASE_NAME", "REFERENCE_COINS", "ServiceMarketData", "activity_recorder",
           "market_database_beside", "open_service_market_data", "service_hot_coins"]
