"""The hourly scan's market-type labels (W11-A, C10; D287).

After each live coin scan is recorded, software labels every coin on its shortlist and every coin of the day (focus
coins, held coins, coins with a day plan or an armed alarm, from saved records), with the live function
(``labeller.label_coin``: the collector's hourly candles, point-in-time at the scan's moment) and stores each label
with its inputs, rules version and scan. A coin whose candles cannot be read is stored as ``unclear``
(``evaluation_failed``) with the error's name, and the other coins carry on. No model is called, no request is made
and nothing is bought or sold. The desk does not see the labels until W12-A.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

from trade_graph.application.candle_store import CandleStore
from trade_graph.application.coin_scanner.store import StoredScan
from trade_graph.application.indicators.library import library_versions
from trade_graph.application.market_types.labeller import (
    LIVE_SOURCE,
    CoinLabel,
    indicator_document,
    label_coin,
    required_history,
)
from trade_graph.application.market_types.store import MarketTypeStore, StoredMarketType
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import TradeGraphError
from trade_graph.domain.market_types import MARKET_TYPES
from trade_graph.kernel.market_type import CURRENT_MARKET_TYPE_RULES, MarketTypeRules, look_back_slots, unclear_label


@dataclass(frozen=True, slots=True)
class LabelRound:
    """One round of labels, as stored."""

    as_of: datetime
    scan_id: str | None
    rules: MarketTypeRules
    stored: tuple[StoredMarketType, ...]
    failed: tuple[str, ...]

    def note(self) -> dict[str, Any]:
        """What HQ's activity log records about the round (no label, only the counts)."""
        counts = Counter(item.market_type for item in self.stored)
        return {"job": "market_types", "status": "ok", "scan_id": self.scan_id, "as_of_utc": utc_iso(self.as_of),
                "labelled": len(self.stored), "by_type": {name: counts.get(name, 0) for name in MARKET_TYPES},
                "failed": list(self.failed), "rules": self.rules.ref, "model_calls": 0}


def coins_to_label(scan: StoredScan | None, day_symbols: Iterable[str] = ()) -> tuple[str, ...]:
    """The scan's shortlist and the day's coins, once each, in symbol order."""
    shortlist = [entry["symbol"] for entry in scan.entries] if scan is not None else []
    return tuple(sorted({symbol for symbol in (*shortlist, *day_symbols) if isinstance(symbol, str) and "/" in symbol}))


def _unreadable(symbol: str, moment: datetime, rules: MarketTypeRules, source: str,
                error: Exception) -> CoinLabel:
    candles = {"symbol": symbol, "source": source, "interval_minutes": rules.interval_minutes,
               "read_mode": "point_in_time", "as_of_utc": utc_iso(moment), "history_candles": required_history(rules),
               "look_back_slots": look_back_slots(rules, history=required_history(rules)), "candles_read": 0,
               "run_ids": [], "evidence_labels": [], "input_sha256": None, "error": type(error).__name__}
    return CoinLabel(symbol=symbol, label=unclear_label(moment, rules, "evaluation_failed"),
                     candles=MappingProxyType(candles), indicators=MappingProxyType(indicator_document(rules)),
                     library=MappingProxyType(library_versions()))


def label_after_scan(*, candles: CandleStore, labels: MarketTypeStore, scan: StoredScan | None, as_of: datetime,
                     day_symbols: Iterable[str] = (), rules: MarketTypeRules = CURRENT_MARKET_TYPE_RULES,
                     source: str = LIVE_SOURCE) -> LabelRound:
    """Label the scan's shortlist and the day's coins at ``as_of`` and store them in one transaction."""
    run_labels = {run.run_id: run.evidence_label for run in candles.runs()}
    results: list[CoinLabel] = []
    failed: list[str] = []
    for symbol in coins_to_label(scan, day_symbols):
        try:
            results.append(label_coin(candles, symbol=symbol, moment=as_of, rules=rules, source=source,
                                      run_labels=run_labels))
        except (TradeGraphError, ValueError, ArithmeticError) as error:
            failed.append(symbol)
            results.append(_unreadable(symbol, as_of, rules, source, error))
    scan_id = None if scan is None else scan.scan_id
    stored = labels.record(results, scan_id=scan_id)
    return LabelRound(as_of=as_of, scan_id=scan_id, rules=rules, stored=tuple(stored), failed=tuple(failed))


__all__ = ["LabelRound", "coins_to_label", "label_after_scan"]
