"""What the scanner knows about one pair, and why it is or is not ranked (D170-D172).

For a pair the catalog does not already exclude, every input is read point-in-time (or by event time in a
replay) from the candle store and the ticker answer, and kept with its source time:

- the anchor: the newest completed hour that is stored, at most ``max_candle_lag_hours`` behind the scan hour;
- traded volume in USD over the last ``volume_hours`` hours: the exact sum of volume x vwap per candle (volume x
  close when a traded candle has no vwap, as in Kraken's archive), labelled with that basis;
- swing: NATR from the indicator menu, the typical hourly true range as a percentage of the close, and that
  percentage relative to the cost profile's round-trip rate (the D120 measure);
- trend: the menu entry the rules name (ROCP over 24 hours by default);
- for a coin the pair catalog never reviewed, the price-peg check: the menu's highest high and lowest low over the
  peg window, compared with the USD band and with each other;
- in a live scan, the spread from the ticker's best bid and ask.

Every comparison is a comparison of exact decimals; library floats are first rounded to the twelve significant
digits the library can vouch for (``rounded_decimal``). No indicator formula is written here (D118), and a missing
input excludes the pair with a reason instead of being guessed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import MAX_EMAX, MIN_EMIN, ROUND_HALF_EVEN, Context, Decimal, DivisionByZero, Inexact, InvalidOperation
from decimal import Overflow as DecimalOverflow
from types import MappingProxyType
from typing import Any

from trade_graph.adapters.market.kraken_ticker import TickerEntry
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.coin_scanner.ranking import Candidate
from trade_graph.application.indicators.engine import IndicatorResult, compute_indicator
from trade_graph.application.indicators.library import rounded_decimal
from trade_graph.application.pair_classification import class_label
from trade_graph.contracts.candles import Candle, ReadMode, align_close_time
from trade_graph.contracts.coin_scanner import IndicatorChoice, ScanMode, ScannerRules
from trade_graph.contracts.pair_catalog import EXCLUSION_FIAT_CURRENCY, PairRecord
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import TradeGraphError
from trade_graph.domain.money import canonical_decimal

# Sums and products of stored decimals are exact; an inexact result raises instead of rounding quietly.
_EXACT = Context(prec=400, Emax=MAX_EMAX, Emin=MIN_EMIN,
                 traps=[Inexact, InvalidOperation, DecimalOverflow, DivisionByZero])
# Ratios are rounded once, half-even, to the twelve significant digits stored snapshots and alarms use.
_TWELVE = Context(prec=12, rounding=ROUND_HALF_EVEN, Emax=MAX_EMAX, Emin=MIN_EMIN,
                  traps=[InvalidOperation, DecimalOverflow, DivisionByZero])
_PEG_REASON_REVIEWED = "base asset reviewed in the pair catalog"
_REPLAY_SPREAD = {"ranked": False, "reason": "no historical bid and ask in a replay"}


@dataclass(frozen=True, slots=True)
class TickerState:
    """Whether the scan has a usable bid and ask, and if not, why. ``quotes`` is keyed by engine symbol."""

    usable: bool
    reason: str | None = None
    quotes: Mapping[str, TickerEntry] = field(default_factory=dict)
    received_at: datetime | None = None
    evidence_label: str | None = None


@dataclass(frozen=True, slots=True)
class ScanSettings:
    rules: ScannerRules
    as_of: datetime
    mode: ScanMode
    source: str
    round_trip_rate: Decimal
    cost: Mapping[str, Any]
    catalog: Mapping[str, Any]
    ticker: TickerState

    @property
    def read_mode(self) -> ReadMode:
        """Live scans read point-in-time (receipt time at or before as-of); replays read by event time only."""
        return "point_in_time" if self.mode == "live" else "replay"


@dataclass(frozen=True, slots=True)
class PairEvaluation:
    symbol: str
    reasons: tuple[str, ...]
    flags: tuple[str, ...]
    inputs: Mapping[str, Any]
    candidate: Candidate | None
    run_ids: tuple[int, ...] = ()


def _text(value: Decimal) -> str:
    return canonical_decimal(value)


def catalog_reasons(record: PairRecord, rules: ScannerRules) -> list[str]:
    """Reasons that need no candle: the trading status, the catalog's marks (D131, D127) and the hold list."""
    reasons = []
    if not record.in_normal_status:
        reasons.append("not_normal_status")
    if record.stablecoin:
        reasons.append("stablecoin")
    if record.yield_token:
        reasons.append("yield_token")
    if EXCLUSION_FIAT_CURRENCY in record.exclusion_reasons:
        reasons.append("fiat_currency")
    if rules.held(record.symbol.partition("/")[0]) is not None:
        reasons.append("held_for_review")
    return reasons


def _catalog_document(record: PairRecord, catalog: Mapping[str, Any], rules: ScannerRules) -> dict[str, Any]:
    hold = rules.held(record.symbol.partition("/")[0])
    return {
        "source_pair": record.source_pair, "status": record.status, "class": class_label(record),
        "review": record.base_asset_review, "rules_version": record.rules_version,
        "observation_id": catalog["observation_id"], "observed_at_utc": catalog["observed_at_utc"],
        **({"hold": hold.model_dump()} if hold is not None else {}),
    }


def _indicator(store: CandleStore, choice: IndicatorChoice, symbol: str, settings: ScanSettings,
               end_close: datetime) -> IndicatorResult:
    return compute_indicator(store, choice.indicator, dict(choice.parameters), symbol=symbol,
                             interval_minutes=settings.rules.interval_minutes, source=settings.source,
                             end_close_time=end_close, as_of=settings.as_of, count=1, mode=settings.read_mode)


def _indicator_document(result: IndicatorResult) -> dict[str, Any]:
    """The engine's provenance, with the one value as exact decimal text instead of the float list."""
    document = result.to_document()
    document.pop("values", None)
    document.pop("close_times_utc", None)
    document["value"] = _text(rounded_decimal(result.latest())) if result.is_ready else None
    return document


def _newest(store: CandleStore, symbol: str, settings: ScanSettings) -> tuple[Candle, int] | None:
    """The newest stored completed candle within the allowed lag, and how many hours behind the scan hour it is."""
    rules = settings.rules
    hour = align_close_time(settings.as_of, rules.interval_minutes)
    window = store.window(symbol, rules.interval_minutes, settings.source, end_close_time=hour,
                          count=rules.max_candle_lag_hours + 1, as_of=settings.as_of, mode=settings.read_mode)
    present = [slot for slot in window.slots if slot.stored is not None]
    if not present:
        return None
    candle = present[-1].stored.candle
    return candle, int((hour - candle.close_time_utc) / timedelta(minutes=rules.interval_minutes))


def _volume(store: CandleStore, symbol: str, settings: ScanSettings, end_close: datetime,
            ) -> tuple[Decimal | None, dict[str, Any], tuple[int, ...]]:
    rules = settings.rules
    window = store.window(symbol, rules.interval_minutes, settings.source, end_close_time=end_close,
                          count=rules.volume_hours, as_of=settings.as_of, mode=settings.read_mode)
    document: dict[str, Any] = {
        "hours": rules.volume_hours, "first_close_utc": utc_iso(window.slots[0].close_time_utc),
        "last_close_utc": utc_iso(window.slots[-1].close_time_utc)}
    if not window.complete:
        missing = window.missing_close_times
        return None, {**document, "status": "missing_candles", "missing_count": len(missing),
                      "missing_close_times_utc": [utc_iso(at) for at in missing[:24]]}, ()
    candles = window.candles
    traded = [candle for candle in candles if candle.volume > 0]
    basis = "vwap" if all(candle.vwap is not None for candle in traded) else "close"
    base = sum((candle.volume for candle in candles), Decimal(0))
    quote = Decimal(0)
    for candle in traded:
        price = candle.vwap if basis == "vwap" else candle.close
        quote = _EXACT.add(quote, _EXACT.multiply(candle.volume, price))
    runs = tuple(sorted({slot.stored.run_id for slot in window.slots if slot.stored is not None}))
    return quote, {**document, "status": "complete", "value_usd": _text(quote), "base_volume": _text(base),
                   "price_basis": basis, "traded_hours": len(traded), "candle_runs": list(runs),
                   "newest_received_at_utc": utc_iso(max(candle.available_at_utc for candle in candles))}, runs


def _spread(record: PairRecord, settings: ScanSettings) -> tuple[Decimal | None, dict[str, Any]]:
    ticker = settings.ticker
    if not ticker.usable:
        return None, {"reason": ticker.reason}
    entry = ticker.quotes.get(record.symbol)
    if entry is None:
        return None, {"reason": "pair_not_in_ticker_answer", "received_at_utc": utc_iso(ticker.received_at)}
    document = {"key": entry.key, "bid": _text(entry.bid), "ask": _text(entry.ask), "last": _text(entry.last),
                "received_at_utc": utc_iso(ticker.received_at), "evidence_label": ticker.evidence_label}
    if entry.ask <= entry.bid:
        return None, {**document, "reason": "ask_not_above_bid"}
    mid = _EXACT.divide(_EXACT.add(entry.ask, entry.bid), Decimal(2))
    spread = _TWELVE.divide(_EXACT.subtract(entry.ask, entry.bid), mid)
    return spread, {**document, "mid": _text(mid), "value": _text(spread)}


def _peg_check(store: CandleStore, record: PairRecord, settings: ScanSettings, end_close: datetime,
               ) -> tuple[list[str], dict[str, Any]]:
    rules = settings.rules
    period = {"timeperiod": rules.peg_hours}
    high = _indicator(store, IndicatorChoice(indicator="HIGHEST_HIGH", parameters=period), record.symbol, settings,
                      end_close)
    low = _indicator(store, IndicatorChoice(indicator="LOWEST_LOW", parameters=period), record.symbol, settings,
                     end_close)
    document: dict[str, Any] = {"applied": True, "hours": rules.peg_hours,
                                "usd_band": [_text(rules.usd_peg_low), _text(rules.usd_peg_high)],
                                "pinned_range": _text(rules.pinned_range),
                                "highest_high": _indicator_document(high), "lowest_low": _indicator_document(low)}
    if not (high.is_ready and low.is_ready):
        return ["peg_check_unavailable"], {**document, "result": "unavailable"}
    top, bottom = rounded_decimal(high.latest()), rounded_decimal(low.latest())
    reasons = []
    if bottom >= rules.usd_peg_low and top <= rules.usd_peg_high:
        reasons.append("price_peg")
    # A comparison of two library outputs, with the range written as a factor on the low: no ratio is computed.
    if top <= _EXACT.multiply(bottom, _EXACT.add(Decimal(1), rules.pinned_range)):
        reasons.append("price_pinned")
    return reasons, {**document, "result": "flagged" if reasons else "passed"}


def evaluate_pair(store: CandleStore, record: PairRecord, settings: ScanSettings) -> PairEvaluation:
    rules = settings.rules
    inputs: dict[str, Any] = {"catalog": _catalog_document(record, settings.catalog, rules)}
    reasons = catalog_reasons(record, rules)
    if reasons:
        return PairEvaluation(record.symbol, tuple(sorted(reasons)), (), MappingProxyType(inputs), None)
    flags: list[str] = []
    unreviewed = record.base_asset_review != "reviewed"
    if unreviewed:
        flags.append("not_individually_reviewed")
    try:
        return _evaluate_candles(store, record, settings, inputs, flags, unreviewed)
    except TradeGraphError as error:
        inputs["error"] = {"code": getattr(error, "code", None) or type(error).__name__}
        return PairEvaluation(record.symbol, ("evaluation_failed",), tuple(sorted(flags)), MappingProxyType(inputs),
                              None)


def _evaluate_candles(store: CandleStore, record: PairRecord, settings: ScanSettings, inputs: dict[str, Any],
                      flags: list[str], unreviewed: bool) -> PairEvaluation:
    rules = settings.rules
    symbol = record.symbol
    candle_section: dict[str, Any] = {
        "source": settings.source, "interval_minutes": rules.interval_minutes, "mode": settings.read_mode,
        "availability_basis": "receipt_time" if settings.read_mode == "point_in_time" else "event_time_only",
        "scan_hour_close_utc": utc_iso(align_close_time(settings.as_of, rules.interval_minutes)),
        "max_lag_hours": rules.max_candle_lag_hours}
    inputs["candles"] = candle_section
    newest = _newest(store, symbol, settings)
    if newest is None:
        candle_section["status"] = "no_candle_within_lag"
        return PairEvaluation(symbol, ("missing_candles",), tuple(sorted(flags)), MappingProxyType(inputs), None)
    anchor, lag = newest
    end_close = anchor.close_time_utc
    candle_section.update({"status": "present", "end_close_time_utc": utc_iso(end_close), "lag_hours": lag,
                           "newest_received_at_utc": utc_iso(anchor.available_at_utc),
                           "newest_close": _text(anchor.close)})
    if lag:
        flags.append("candles_lagging")
    reasons: list[str] = []

    volume, inputs["volume"], runs = _volume(store, symbol, settings, end_close)
    if volume is None:
        reasons.append("missing_volume")
    elif volume < rules.min_volume_usd:
        reasons.append("thin_volume")

    swing_result = _indicator(store, rules.swing, symbol, settings, end_close)
    swing_document = _indicator_document(swing_result)
    swing_document["round_trip_rate"] = _text(settings.round_trip_rate)
    swing_to_cost = None
    if swing_result.is_ready:
        natr = rounded_decimal(swing_result.latest())  # a percentage of the close (TA-Lib NATR)
        # D120's measure: the swing against the cost of one buy and one sell, the fee written in the same unit.
        swing_to_cost = _TWELVE.divide(natr, _EXACT.multiply(settings.round_trip_rate, Decimal(100)))
        swing_document["swing_to_cost"] = _text(swing_to_cost)
    else:
        reasons.append("missing_swing")
    inputs["swing"] = swing_document

    trend_result = _indicator(store, rules.trend, symbol, settings, end_close)
    inputs["trend"] = _indicator_document(trend_result)
    trend = rounded_decimal(trend_result.latest()) if trend_result.is_ready else None
    if trend is None:
        reasons.append("missing_trend")

    if unreviewed:
        peg_reasons, inputs["peg_check"] = _peg_check(store, record, settings, end_close)
        reasons.extend(peg_reasons)
    else:
        inputs["peg_check"] = {"applied": False, "reason": _PEG_REASON_REVIEWED}

    spread = None
    if settings.mode == "live":
        spread, inputs["spread"] = _spread(record, settings)
        if spread is None:
            reasons.append("missing_spread")
        elif spread > rules.max_spread:
            reasons.append("wide_spread")
    else:
        inputs["spread"] = dict(_REPLAY_SPREAD)
    inputs["cost"] = dict(settings.cost)

    candidate = None
    if not reasons:
        candidate = Candidate(symbol=symbol, volume_usd_24h=volume, swing_to_cost=swing_to_cost, spread=spread,
                              trend=trend)
    return PairEvaluation(symbol, tuple(sorted(set(reasons))), tuple(sorted(flags)), MappingProxyType(inputs),
                          candidate, runs)


__all__ = ["PairEvaluation", "ScanSettings", "TickerState", "catalog_reasons", "evaluate_pair"]
