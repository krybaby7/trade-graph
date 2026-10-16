"""Replay one registered strategy version on stored candles under one cost profile (T29, D119, D165).

The replay reads candles only through ``CandleStore.window``, point-in-time as of a fixed data cut-off, so a rerun
with the same cut-off reads exactly the same candle revisions (the store is append-only and never backdates a
receipt). Indicator values come from the T27 engine through ``rolling_latest``: at each candle the number the
engine itself would give there. Orders and protective exits fill only through the shared kernel fill engine (T28).

Each candle of the window is handled in this order:

1. at its open: a pending market sell (a rule exit placed at the previous close) and then
2. the protective exit armed before this candle (stop and take-profit, the stop assumed first);
3. a pending market entry at the open, or a resting limit entry during the candle. A new fill re-arms the exit on
   everything held and checks it against the same candle as the fill engine prescribes (only the stop counts);
4. at its close: equity is marked at the close; orders whose life has run out are cancelled (lives count candle
   slots, present or missing: a missing candle fills nothing); and then
5. the decision: with a position, a rule exit or the holding limit places a market sell for the next open; when
   flat with no order, an entry whose conditions all hold places a buy for the next candle. No order is placed at the
   window's last close, because nothing after the window may be read.

One position at a time, long only, no borrowing (D127): an entry spends at most ``equity_fraction`` of the cash,
sized at the taker fee and entry slippage so a fill never needs more cash than there is. A position still held at
the end is valued as if sold at the last close, less exit slippage and the taker fee; it is reported as open, not
counted as a trade.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Context, Decimal, localcontext
from typing import Any, Literal

from trade_graph.application.candle_store import MAX_WINDOW_SLOTS, CandleStore
from trade_graph.application.indicators.engine import plan_history
from trade_graph.application.indicators.library import library_versions, rounded_decimal, rounded_significant
from trade_graph.application.indicators.rolling import RollingSeries, rolling_latest
from trade_graph.contracts.candles import CANDLE_SOURCES, Candle, CandleSlot, CandleWindow, to_micros, to_unix
from trade_graph.contracts.fills import CostProfile, FillPolicy, ProtectiveExit, SimFill, SimOrder
from trade_graph.contracts.models import InstrumentRules
from trade_graph.contracts.practice_range import (
    CandleOperand,
    Condition,
    IndicatorOperand,
    LevelOperand,
    Operand,
    PracticeStrategy,
    ReplayWindow,
)
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import ValidationFailure
from trade_graph.domain.money import canonical_decimal
from trade_graph.kernel.fills import check_order_rules, fill_order_on_candle, trigger_exit_on_candle
from trade_graph.kernel.trade_metrics import (
    EquityPoint,
    HoldBenchmark,
    TradeFill,
    TradeMetrics,
    hold_benchmark,
    summarize,
)

REPLAY_ENGINE_VERSION = "practice-range-replay-v1"
# Kraken quotes most coins in lots of 0.00000001 (lot_decimals 8); without instrument rules quantities floor to it.
DEFAULT_QUANTITY_INCREMENT = Decimal("0.00000001")
Purpose = Literal["entry", "protective_exit", "rule_exit", "holding_limit_exit"]
_EXACT = Context(prec=160)


def _text(value: Decimal | None) -> str | None:
    return None if value is None else canonical_decimal(value)


def _round(price: Decimal, increment: Decimal | None, rounding: str) -> Decimal:
    if increment is None:
        return price
    with localcontext(_EXACT):
        return (price / increment).to_integral_value(rounding=rounding) * increment


@dataclass(frozen=True, slots=True)
class ReplayRequest:
    strategy: PracticeStrategy
    symbol: str
    source: str
    window: ReplayWindow
    data_as_of: datetime
    profile: CostProfile
    policy: FillPolicy
    capital: Decimal
    rules: InstrumentRules | None = None
    benchmark_symbol: str | None = None
    benchmark_source: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.strategy, PracticeStrategy):
            raise ValidationFailure("a replay needs a registered PracticeStrategy")
        if not isinstance(self.symbol, str) or self.symbol.count("/") != 1:
            raise ValidationFailure("the symbol is BASE/QUOTE")
        if self.source not in CANDLE_SOURCES:
            raise ValidationFailure("unsupported candle source")
        if type(self.capital) is not Decimal or not self.capital.is_finite() or self.capital <= 0:
            raise ValidationFailure("the starting capital must be a positive Decimal")
        if not isinstance(self.data_as_of, datetime) or self.data_as_of.tzinfo is None:
            raise ValidationFailure("the data cut-off must be a timezone-aware time")
        if self.data_as_of < self.window.end_utc:
            raise ValidationFailure("the data cut-off must be at or after the window's end; a window still running "
                                    "cannot be replayed")
        if self.rules is not None and self.rules.symbol != self.symbol:
            raise ValidationFailure("the instrument rules are for another symbol")

    @property
    def quote(self) -> str:
        return self.symbol.split("/")[1]

    @property
    def hold_btc_symbol(self) -> str:
        return self.benchmark_symbol or f"BTC/{self.quote}"

    @property
    def hold_btc_source(self) -> str:
        return self.benchmark_source or self.source

    @property
    def lot(self) -> Decimal:
        return self.rules.quantity_increment if self.rules is not None else DEFAULT_QUANTITY_INCREMENT

    def to_document(self) -> dict[str, Any]:
        policy = self.policy
        return {
            "strategy": {"ref": self.strategy.ref, "sha256": self.strategy.content_sha256},
            "symbol": self.symbol, "source": self.source, "interval_minutes": self.strategy.interval_minutes,
            "window": self.window.to_document(), "data_as_of_utc": utc_iso(self.data_as_of),
            "cost_profile": {"ref": self.profile.ref, "sha256": self.profile.content_sha256,
                             "venue": self.profile.venue, "maker_rate": _text(self.profile.maker_rate),
                             "taker_rate": _text(self.profile.taker_rate)},
            "fill_policy": {"ref": policy.ref, "sha256": policy.content_sha256,
                            "participation": _text(policy.participation),
                            "entry_slippage_rate": _text(policy.entry_slippage_rate),
                            "exit_slippage_rate": _text(policy.exit_slippage_rate)},
            "capital": _text(self.capital), "currency": self.quote,
            "instrument_rules": None if self.rules is None else self.rules.model_dump(mode="json"),
            "quantity_increment": _text(self.lot),
            "benchmark": {"hold_btc_symbol": self.hold_btc_symbol, "hold_btc_source": self.hold_btc_source},
        }


@dataclass(frozen=True, slots=True)
class ReplayFill:
    fill_ref: str
    purpose: Purpose
    fill: SimFill

    def to_document(self) -> dict[str, Any]:
        return {"fill_ref": self.fill_ref, "purpose": self.purpose, **self.fill.model_dump(mode="json")}


@dataclass(frozen=True, slots=True)
class EndValuation:
    cash: Decimal
    held: Decimal
    price: Decimal | None
    fee: Decimal
    value: Decimal
    valued_at: datetime | None

    @property
    def equity(self) -> Decimal:
        with localcontext(_EXACT):
            return self.cash + self.value

    def to_document(self) -> dict[str, Any]:
        return {"cash": _text(self.cash), "held": _text(self.held), "price": _text(self.price),
                "fee": _text(self.fee), "value": _text(self.value), "equity": _text(self.equity),
                "valued_at_utc": None if self.valued_at is None else utc_iso(self.valued_at),
                "basis": "last close less exit slippage and the taker fee; an open position is not a trade"}


@dataclass(frozen=True, slots=True)
class DataFacts:
    symbol: str
    interval_minutes: int
    source: str
    source_pair: str | None
    read_mode: str
    data_as_of: datetime
    slots_read: int
    warmup_slots: int
    window_bars: int
    window_present: int
    missing_ranges: tuple[dict[str, Any], ...]
    candles_read: int
    run_ids: tuple[int, ...]
    evidence_labels: tuple[str, ...]
    revised_candles: int
    input_sha256: str

    def to_document(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "interval_minutes": self.interval_minutes, "source": self.source,
                "source_pair": self.source_pair, "read_mode": self.read_mode,
                "availability": "candles received by the data cut-off; decisions use event time only",
                "data_as_of_utc": utc_iso(self.data_as_of), "slots_read": self.slots_read,
                "warmup_slots": self.warmup_slots, "window_bars": self.window_bars,
                "window_present": self.window_present, "window_missing": self.window_bars - self.window_present,
                "missing_ranges": list(self.missing_ranges), "candles_read": self.candles_read,
                "run_ids": list(self.run_ids), "evidence_labels": list(self.evidence_labels),
                "revised_candles": self.revised_candles, "input_sha256": self.input_sha256}


@dataclass(frozen=True, slots=True)
class ReplayResult:
    request: ReplayRequest
    fills: tuple[ReplayFill, ...]
    equity: tuple[EquityPoint, ...]
    metrics: TradeMetrics
    ending: EndValuation
    events: dict[str, int]
    data: DataFacts
    benchmark_data: dict[str, dict[str, Any]]
    indicator_versions: dict[str, str]
    indicator_spans: dict[str, int]
    library: dict[str, str]
    bars_in_market: int

    def equity_curve_sha256(self) -> str:
        document = [[to_micros(point.at), canonical_decimal(point.equity)] for point in self.equity]
        return hashlib.sha256(json.dumps(document, separators=(",", ":")).encode("ascii")).hexdigest()

    def daily_equity(self) -> list[dict[str, str]]:
        """The last equity point of each UTC day, for charts; the full curve is pinned by its hash."""
        by_day: dict[str, EquityPoint] = {}
        for point in self.equity:
            by_day[point.at.date().isoformat()] = point
        return [{"day": day, "at_utc": utc_iso(point.at), "equity": canonical_decimal(point.equity)}
                for day, point in sorted(by_day.items())]

    def to_document(self) -> dict[str, Any]:
        strategy = self.request.strategy
        return {
            "engine": REPLAY_ENGINE_VERSION, "request": self.request.to_document(), "data": self.data.to_document(),
            "benchmark_data": self.benchmark_data,
            "indicators": {key: {"indicator": use.indicator, "parameters": use.resolved_parameters,
                                 "spec_version": self.indicator_versions[key], "span": self.indicator_spans[key]}
                           for key, use in sorted(strategy.indicator_uses().items())},
            "library": dict(sorted(self.library.items())), "events": dict(sorted(self.events.items())),
            "bars_in_market": self.bars_in_market,
            "fills": [item.to_document() for item in self.fills], "ending": self.ending.to_document(),
            "equity_curve": {"points": len(self.equity), "sha256": self.equity_curve_sha256(),
                             "daily": self.daily_equity(), "marked_at": "each candle's close"},
            "metrics": self.metrics.to_document(),
        }


@dataclass(slots=True)
class _Entry:
    order: SimOrder
    placed_index: int
    filled: Decimal = Decimal("0")


@dataclass(slots=True)
class _Simulation:
    request: ReplayRequest
    slots: tuple[CandleSlot, ...]
    series: dict[str, RollingSeries]
    first: int
    cash: Decimal = Decimal("0")
    held: Decimal = Decimal("0")
    entry: _Entry | None = None
    exit: ProtectiveExit | None = None
    exit_armed_index: int | None = None
    stop: Decimal | None = None
    target: Decimal | None = None
    sell: SimOrder | None = None
    sell_purpose: Purpose = "rule_exit"
    entered_index: int | None = None
    fills: list[ReplayFill] = field(default_factory=list)
    equity: list[EquityPoint] = field(default_factory=list)
    events: Counter = field(default_factory=Counter)
    bars_in_market: int = 0

    # --- setup -----------------------------------------------------------------------------------------------

    @property
    def tick(self) -> Decimal | None:
        return None if self.request.rules is None else self.request.rules.price_increment

    @property
    def prefix(self) -> str:
        return f"{self.request.strategy.ref}:{self.request.symbol}"

    def _engine(self) -> dict[str, Any]:
        return {"profile": self.request.profile, "policy": self.request.policy, "price_increment": self.tick}

    # --- the loop --------------------------------------------------------------------------------------------

    def run(self) -> EndValuation:
        self.cash = self.request.capital
        self.equity.append(EquityPoint(self.request.window.start_utc, self.cash))
        last = len(self.slots) - 1
        for index in range(self.first, last + 1):
            stored = self.slots[index].stored
            candle = None if stored is None else stored.candle
            if candle is not None:
                self._at_open(index, candle)
                self._entry_fill(index, candle, market=False)
                if self.held > 0:
                    self.bars_in_market += 1
                with localcontext(_EXACT):
                    marked = self.cash + self.held * candle.close
                self.equity.append(EquityPoint(candle.close_time_utc, marked))
            else:
                self.events["missing_bars"] += 1
            self._expire(index)
            if candle is not None and index < last:
                self._decide(index, candle)
        return self._finish()

    def _at_open(self, index: int, candle: Candle) -> None:
        if self.sell is not None:
            fill = fill_order_on_candle(self.sell, candle, **self._engine())
            if fill is not None:
                self._apply_sell(fill, self.sell_purpose)
        self._protect(index, candle)
        self._entry_fill(index, candle, market=True)

    def _protect(self, index: int, candle: Candle) -> None:
        if self.exit is None or self.exit_armed_index == index:
            return
        fill = trigger_exit_on_candle(self.exit, candle, armed_in_this_candle=False, **self._engine())
        if fill is not None:
            self._apply_sell(fill, "protective_exit")

    def _entry_fill(self, index: int, candle: Candle, *, market: bool) -> None:
        if self.entry is None or (self.entry.order.kind == "market") != market:
            return
        fill = fill_order_on_candle(self.entry.order, candle, **self._engine())
        if fill is not None:
            self._apply_entry(index, candle, fill)

    # --- fills -----------------------------------------------------------------------------------------------

    def _record(self, fill: SimFill, purpose: Purpose) -> None:
        self.fills.append(ReplayFill(fill_ref=f"{fill.order_id}#{len(self.fills) + 1}", purpose=purpose, fill=fill))

    def _apply_entry(self, index: int, candle: Candle, fill: SimFill) -> None:
        assert self.entry is not None
        with localcontext(_EXACT):
            cost = fill.quantity * fill.price + fill.fee
        if cost > self.cash:  # sizing leaves room for this; refuse rather than borrow (D127)
            self.events["entries_cancelled_for_cash"] += 1
            self.entry = None
            return
        order = self.entry.order
        with localcontext(_EXACT):
            self.cash -= cost
            self.held += fill.quantity
            remaining = order.remaining - fill.quantity
            self.entry.filled += fill.quantity
        self._record(fill, "entry")
        if self.entered_index is None:
            self.entered_index = index
        if self.stop is None:
            reference = order.limit_price if order.limit_price is not None else fill.price
            exits = self.request.strategy.exit
            with localcontext(_EXACT):
                self.stop = _round(reference * (1 - exits.stop_loss_fraction), self.tick, ROUND_FLOOR)
                self.target = None if exits.take_profit_fraction is None else _round(
                    reference * (1 + exits.take_profit_fraction), self.tick, ROUND_CEILING)
        if remaining > 0:
            self.entry.order = order.model_copy(update={"remaining": remaining})
        else:
            self.events["entries_filled"] += 1
            self.entry = None
        self.exit = ProtectiveExit(exit_id=f"{order.order_id}:exit", symbol=order.symbol, quantity=self.held,
                                   stop_price=self.stop, take_profit_price=self.target,
                                   eligible_after_utc=fill.filled_at_utc)
        self.exit_armed_index = index
        hit = trigger_exit_on_candle(self.exit, candle, armed_in_this_candle=True, **self._engine())
        if hit is not None:
            self._apply_sell(hit, "protective_exit")

    def _apply_sell(self, fill: SimFill, purpose: Purpose) -> None:
        if fill.quantity > self.held:
            raise ValidationFailure("a replay sell exceeded the position; the account is long only")
        with localcontext(_EXACT):
            self.cash += fill.quantity * fill.price - fill.fee
            self.held -= fill.quantity
        self._record(fill, purpose)
        self.events[f"{purpose}_fills"] += 1
        if self.sell is not None and fill.order_id == self.sell.order_id:
            with localcontext(_EXACT):
                remaining = self.sell.remaining - fill.quantity
            self.sell = self.sell.model_copy(update={"remaining": remaining}) if remaining > 0 else None
        if self.held == 0:
            if self.entry is not None:
                self.events["entry_remainders_cancelled"] += 1
            self.entry = self.exit = self.sell = None
            self.stop = self.target = None
            self.entered_index = self.exit_armed_index = None
        elif self.exit is not None:
            self.exit = self.exit.model_copy(update={"quantity": self.held})

    # --- decisions -------------------------------------------------------------------------------------------

    def _expire(self, index: int) -> None:
        entry = self.entry
        if entry is None or index - entry.placed_index < self.request.strategy.entry.order.expires_after_bars:
            return
        self.events["entries_expired_partly_filled" if entry.filled > 0 else "entries_expired_unfilled"] += 1
        self.entry = None

    def _decide(self, index: int, candle: Candle) -> None:
        strategy = self.request.strategy
        self.events["decision_bars"] += 1
        if self.held > 0:
            if self.sell is not None:
                return
            if any(self._holds(condition, index) is True for condition in strategy.exit.any_of):
                self._place_sell(candle, "rule_exit")
            elif (strategy.exit.max_holding_bars is not None and self.entered_index is not None
                  and index - self.entered_index >= strategy.exit.max_holding_bars):
                self._place_sell(candle, "holding_limit_exit")
            return
        if self.entry is not None:
            return
        verdicts = [self._holds(condition, index) for condition in strategy.entry.all_of]
        if any(verdict is None for verdict in verdicts):
            self.events["decision_bars_without_values"] += 1
        if all(verdict is True for verdict in verdicts):
            self.events["entry_signals"] += 1
            self._place_entry(index, candle)

    def _place_sell(self, candle: Candle, purpose: Purpose) -> None:
        if self.entry is not None:
            self.events["entry_remainders_cancelled"] += 1
            self.entry = None
        self.sell = SimOrder(order_id=f"{self.prefix}:{to_unix(candle.close_time_utc)}:sell", symbol=candle.symbol,
                             side="sell", kind="market", quantity=self.held, remaining=self.held,
                             placement_price=candle.close, eligible_after_utc=candle.close_time_utc)
        self.sell_purpose = purpose
        self.events[f"{purpose}_orders"] += 1

    def _place_entry(self, index: int, candle: Candle) -> None:
        request = self.request
        spec = request.strategy.entry.order
        limit: Decimal | None = None
        reference = candle.close
        if spec.kind == "limit":
            assert spec.price is not None
            base = self._price(spec.price, index)
            if base is None:
                self.events["entries_refused_no_price"] += 1
                return
            with localcontext(_EXACT):
                limit = _round(base * (1 - spec.offset_fraction), self.tick, ROUND_FLOOR)
            if limit <= 0:
                self.events["entries_refused_no_price"] += 1
                return
            reference = limit
        with localcontext(_EXACT):
            budget = self.cash * request.strategy.sizing.equity_fraction
            unit = reference * (1 + request.policy.entry_slippage_rate) * (1 + request.profile.taker_rate)
            quantity = (budget / unit / request.lot).to_integral_value(rounding=ROUND_FLOOR) * request.lot
        if quantity <= 0:
            self.events["entries_refused_too_small"] += 1
            return
        order = SimOrder(order_id=f"{self.prefix}:{to_unix(candle.close_time_utc)}:entry", symbol=candle.symbol,
                         side="buy", kind=spec.kind, quantity=quantity, remaining=quantity, limit_price=limit,
                         placement_price=candle.close, eligible_after_utc=candle.close_time_utc)
        if request.rules is not None:
            try:
                check_order_rules(order, request.rules, reference_price=candle.close)
            except ValidationFailure:
                self.events["entries_refused_by_venue_rules"] += 1
                return
        self.entry = _Entry(order=order, placed_index=index)
        self.events["entries_placed"] += 1

    def _candle(self, index: int) -> Candle | None:
        if index < 0:
            return None
        stored = self.slots[index].stored
        return None if stored is None else stored.candle

    def _price(self, operand: Operand, index: int) -> Decimal | None:
        """An order price: a candle's exact price, or an indicator value at the 12 digits the library vouches for."""
        if isinstance(operand, CandleOperand):
            candle = self._candle(index)
            return None if candle is None else getattr(candle, operand.candle)
        if isinstance(operand, IndicatorOperand):
            value = self.series[operand.indicator].at(index, operand.output)
            return None if value is None else rounded_decimal(value)
        return operand.level

    def _value(self, operand: Operand, index: int) -> Decimal | None:
        """A comparison value: both sides rounded half-even to 12 significant digits, as alarms compare (D136)."""
        if isinstance(operand, LevelOperand):
            return rounded_significant(operand.level)
        price = self._price(operand, index)
        return None if price is None else rounded_significant(price)

    def _holds(self, condition: Condition, index: int) -> bool | None:
        """True or False, or None when a value is missing (an unready value never satisfies anything)."""
        left, right = self._value(condition.left, index), self._value(condition.right, index)
        if left is None or right is None:
            return None
        if condition.relation == "above":
            return left > right
        if condition.relation == "below":
            return left < right
        before_left, before_right = self._value(condition.left, index - 1), self._value(condition.right, index - 1)
        if before_left is None or before_right is None:
            return None
        if condition.relation == "crosses_above":
            return before_left <= before_right and left > right
        return before_left >= before_right and left < right

    def _finish(self) -> EndValuation:
        window_end = self.request.window.end_utc
        if self.entry is not None:
            self.events["entries_open_at_end"] += 1
        if self.sell is not None:
            self.events["sells_open_at_end"] += 1
        price = valued_at = None
        fee = value = Decimal("0")
        if self.held > 0:
            last = next(self._candle(index) for index in range(len(self.slots) - 1, self.first - 1, -1)
                        if self._candle(index) is not None)
            with localcontext(_EXACT):
                price = _round(last.close * (1 - self.request.policy.exit_slippage_rate), self.tick, ROUND_FLOOR)
                fee = price * self.held * self.request.profile.taker_rate
                value = price * self.held - fee
            valued_at = last.close_time_utc
        ending = EndValuation(cash=self.cash, held=self.held, price=price, fee=fee, value=value, valued_at=valued_at)
        final = EquityPoint(window_end, ending.equity)
        if self.equity and self.equity[-1].at == window_end:
            self.equity[-1] = final
        else:
            self.equity.append(final)
        return ending


def _missing_ranges(window: CandleWindow, first: int) -> tuple[dict[str, Any], ...]:
    """Runs of consecutive missing slots inside the replayed window (the warm-up before it is not listed)."""
    step = timedelta(minutes=window.interval_minutes)
    ranges: list[dict[str, Any]] = []
    previous: datetime | None = None
    for slot in window.slots[first:]:
        if slot.stored is not None:
            continue
        opened = slot.close_time_utc - step
        if ranges and previous is not None and opened - previous == step:
            ranges[-1].update(last_open_utc=utc_iso(opened), slots=ranges[-1]["slots"] + 1)
        else:
            ranges.append({"first_open_utc": utc_iso(opened), "last_open_utc": utc_iso(opened), "slots": 1})
        previous = opened
    return tuple(ranges)


def _candle_digest(candles: list[tuple[datetime, int, int, str]]) -> str:
    """The same scheme as the indicator engine's input hash: which revision of which slot, from which run."""
    document = [[to_micros(at), revision, run_id, content_hash] for at, revision, run_id, content_hash in candles]
    return hashlib.sha256(json.dumps(document, separators=(",", ":")).encode("ascii")).hexdigest()


def _data_facts(store: CandleStore, request: ReplayRequest, read: CandleWindow, first: int) -> DataFacts:
    present = [slot.stored for slot in read.slots if slot.stored is not None]
    run_ids = tuple(sorted({stored.run_id for stored in present}))
    labels = {run.run_id: run.evidence_label for run in store.runs()}
    window_present = sum(1 for slot in read.slots[first:] if slot.stored is not None)
    return DataFacts(
        symbol=request.symbol, interval_minutes=request.strategy.interval_minutes, source=request.source,
        source_pair=present[0].candle.source_pair if present else None, read_mode=read.mode,
        data_as_of=request.data_as_of, slots_read=len(read.slots), warmup_slots=first,
        window_bars=len(read.slots) - first, window_present=window_present,
        missing_ranges=_missing_ranges(read, first), candles_read=len(present), run_ids=run_ids,
        evidence_labels=tuple(sorted({labels.get(run_id, "unknown") for run_id in run_ids})),
        revised_candles=sum(1 for stored in present if stored.revision > 1),
        input_sha256=_candle_digest([(stored.candle.close_time_utc, stored.revision, stored.run_id,
                                      stored.candle.content_hash) for stored in present]))


def _hold(request: ReplayRequest, candles: list, *, lot: Decimal, name: str
          ) -> tuple[HoldBenchmark | None, dict[str, Any]]:
    """Buy at the window's first open, sell at its last close, both at the profile's taker fee."""
    if not candles:
        return None, {"status": "unavailable", "reason": f"no {name} candles in the window"}
    first, last = candles[0], candles[-1]
    try:
        benchmark = hold_benchmark(symbol=first.candle.symbol, capital=request.capital, buy_price=first.candle.open,
                                   sell_price=last.candle.close, fee_rate=request.profile.taker_rate,
                                   quantity_increment=lot, bought_at=first.candle.open_time_utc,
                                   sold_at=last.candle.close_time_utc)
    except ValidationFailure as error:
        return None, {"status": "unavailable", "reason": str(error)}
    return benchmark, {
        "status": "ok", "symbol": first.candle.symbol, "source": first.candle.source,
        "first_open_utc": utc_iso(first.candle.open_time_utc), "last_close_utc": utc_iso(last.candle.close_time_utc),
        "run_ids": sorted({first.run_id, last.run_id}),
        "input_sha256": _candle_digest([(item.candle.close_time_utc, item.revision, item.run_id,
                                         item.candle.content_hash) for item in (first, last)]),
    }


def _benchmarks(store: CandleStore, request: ReplayRequest, read: CandleWindow, first: int
                ) -> tuple[dict[str, HoldBenchmark], dict[str, dict[str, Any]]]:
    coin = [slot.stored for slot in read.slots[first:] if slot.stored is not None]
    benchmarks: dict[str, HoldBenchmark] = {}
    documents: dict[str, dict[str, Any]] = {}
    held, documents["hold_coin"] = _hold(request, coin, lot=request.lot, name=request.symbol)
    if held is not None:
        benchmarks["hold_coin"] = held
    if (request.hold_btc_symbol, request.hold_btc_source) == (request.symbol, request.source):
        if held is not None:
            benchmarks["hold_btc"] = held
        documents["hold_btc"] = {**documents["hold_coin"], "same_as": "hold_coin"}
        return benchmarks, documents
    try:
        btc_read = store.window(request.hold_btc_symbol, request.strategy.interval_minutes, request.hold_btc_source,
                                end_close_time=request.window.end_utc, count=len(read.slots) - first,
                                as_of=request.data_as_of, mode="point_in_time")
        btc = [slot.stored for slot in btc_read.slots if slot.stored is not None]
    except ValidationFailure as error:
        btc, documents["hold_btc"] = [], {"status": "unavailable", "reason": str(error)}
    if "hold_btc" not in documents:
        name = f"{request.hold_btc_symbol} ({request.hold_btc_source})"
        btc_hold, documents["hold_btc"] = _hold(request, btc, lot=DEFAULT_QUANTITY_INCREMENT, name=name)
        if btc_hold is not None:
            benchmarks["hold_btc"] = btc_hold
    return benchmarks, documents


def replay(store: CandleStore, request: ReplayRequest) -> ReplayResult:
    """Replay ``request.strategy`` on ``request.window``; deterministic for the same store contents and cut-off."""
    strategy = request.strategy
    interval = strategy.interval_minutes
    uses = strategy.indicator_uses()
    spans = {key: plan_history(use.indicator, use.resolved_parameters, 1).span for key, use in uses.items()}
    # Enough earlier candles for every indicator's span at the window's first candle, and one more for crossings.
    first = max([1, *spans.values()])
    bars = request.window.bars(interval)
    if first + bars > MAX_WINDOW_SLOTS:
        raise ValidationFailure(f"one replay reads at most {MAX_WINDOW_SLOTS} candles; this window and its warm-up "
                                f"need {first + bars}. Use a shorter window or a longer candle interval")
    read = store.window(request.symbol, interval, request.source, end_close_time=request.window.end_utc,
                        count=first + bars, as_of=request.data_as_of, mode="point_in_time")
    series = {key: rolling_latest(use.indicator, use.resolved_parameters, read) for key, use in uses.items()}
    simulation = _Simulation(request=request, slots=read.slots, series=series, first=first)
    ending = simulation.run()
    benchmarks, benchmark_data = _benchmarks(store, request, read, first)
    fills = tuple(simulation.fills)
    metrics = summarize(
        currency=request.quote, starting_equity=request.capital, ending_equity=ending.equity,
        fills=[TradeFill.from_sim_fill(item.fill, fill_ref=item.fill_ref) for item in fills],
        equity_points=simulation.equity, benchmarks=benchmarks)
    return ReplayResult(
        request=request, fills=fills, equity=tuple(simulation.equity), metrics=metrics, ending=ending,
        events=dict(simulation.events), data=_data_facts(store, request, read, first), benchmark_data=benchmark_data,
        indicator_versions={key: item.spec_version for key, item in series.items()}, indicator_spans=spans,
        library=library_versions(), bars_in_market=simulation.bars_in_market)


__all__ = ["DEFAULT_QUANTITY_INCREMENT", "REPLAY_ENGINE_VERSION", "DataFacts", "EndValuation", "ReplayFill",
           "ReplayRequest", "ReplayResult", "replay"]
