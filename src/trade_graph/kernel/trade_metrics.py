"""Result sums for practice-range replays (T29) and the paper scorecard (T42): one set of sums for both (D119, D169).

Pure functions over exact decimals in the protected kernel, because they decide what a result is said to be and
agents must not edit them (AGENTS.md). Nothing here imports a provider SDK or an array library.

- A *trade* is one round trip on one symbol, from flat to flat: every buy and sell fill in between, partial fills
  included, and every fee. Its result is ``sold - bought - fees`` in the quote currency. A position still held at
  the end is an *open position*, not a trade.
- A trade *wins* when its result after fees is above zero and *loses* when it is below; exactly zero is a flat
  trade, neither a win nor a loss, and it ends a losing streak.
- *Worst drawdown* is the largest fall from a running peak of the equity curve, as a fraction of that peak, with its
  amount and times.
- *Holding cash* earns nothing: no interest, earn or lending (D127). *Holding a coin* (for example BTC) spends the
  starting capital on it at the first price with the taker fee, floored to the lot size, and sells it at the last
  price with the taker fee.

Ratios and averages are rounded half-even to ten decimal places (``QUANTUM``); every sum is exact. The account is
long only (D127): a sell larger than the position held is refused, never turned into a short. The practice range
feeds simulated fills (``TradeFill.from_sim_fill``); the paper scorecard feeds the ledger's fills
(``TradeFill.from_fill_record``, which puts fees in the coin or a third asset into the quote currency, D201).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Any, Literal

from trade_graph.contracts.fills import SimFill
from trade_graph.contracts.models import FillRecord
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import ValidationFailure
from trade_graph.domain.money import canonical_decimal

METRICS_VERSION = "trade-metrics-v1"
QUANTUM = Decimal("0.0000000001")
_EXACT = Context(prec=160)
Outcome = Literal["win", "loss", "flat"]
Side = Literal["buy", "sell"]


def _decimal(label: str, value: object) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise ValidationFailure(f"{label} must be a finite Decimal")
    return value


def _aware(label: str, value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationFailure(f"{label} must be a timezone-aware datetime")
    return value


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    with localcontext(_EXACT):
        return (numerator / denominator).quantize(QUANTUM, rounding=ROUND_HALF_EVEN)


def _text(value: Decimal | None) -> str | None:
    return None if value is None else canonical_decimal(value)


def _time(value: datetime | None) -> str | None:
    return None if value is None else utc_iso(value)


@dataclass(frozen=True, slots=True)
class TradeFill:
    """One fill as the sums see it: principal and fee in the symbol's quote currency."""

    fill_ref: str
    symbol: str
    side: Side
    quantity: Decimal
    quote_amount: Decimal  # principal, fees excluded
    fee: Decimal  # in the quote currency
    at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.fill_ref, str) or not self.fill_ref:
            raise ValidationFailure("a fill needs a reference")
        if not isinstance(self.symbol, str) or self.symbol.count("/") != 1:
            raise ValidationFailure("a fill symbol is BASE/QUOTE")
        if self.side not in ("buy", "sell"):
            raise ValidationFailure("a fill side is buy or sell")
        if _decimal("quantity", self.quantity) <= 0 or _decimal("quote amount", self.quote_amount) <= 0:
            raise ValidationFailure("a fill has a positive quantity and principal")
        if _decimal("fee", self.fee) < 0:
            raise ValidationFailure("a fee is never negative")
        _aware("fill time", self.at)

    @classmethod
    def from_sim_fill(cls, fill: SimFill, *, fill_ref: str) -> TradeFill:
        if fill.fee_asset != fill.symbol.split("/")[1]:
            raise ValidationFailure("the sums need fees in the quote currency")
        with localcontext(_EXACT):
            principal = fill.quantity * fill.price
        return cls(fill_ref=fill_ref, symbol=fill.symbol, side=fill.side, quantity=fill.quantity,
                   quote_amount=principal, fee=fill.fee, at=fill.filled_at_utc)

    @classmethod
    def from_fill_record(cls, fill: FillRecord, *, fill_ref: str, base_asset: str, quote_asset: str) -> TradeFill:
        """A ledger fill (the paper scorecard, T42, D201) in the quote currency, with every fee leg the books posted.

        A fee in the quote currency is the fee. A fee in the coin changes what is held, as the books post it: a buy
        holds ``quantity - fee`` and a sell gives up ``quantity + fee``; the fee is valued at the fill price and the
        principal moves with it, so ``sold - bought - fees`` stays the cash the trade made. A fee in a third asset
        is valued at the rate identified with it, and without one the fill is refused. A net rebate (fees below
        zero) lowers the principal of a buy or raises that of a sell instead, because fees paid are never negative.
        """
        if not isinstance(fill, FillRecord):
            raise ValidationFailure("a ledger fill is a FillRecord")
        if fill.symbol != f"{base_asset}/{quote_asset}":
            raise ValidationFailure("the fill's symbol does not name the base and quote assets it was booked with")
        with localcontext(_EXACT):
            principal, quantity = fill.quote_principal, fill.quantity
            fee = coin_fee = Decimal("0")
            for leg in fill.fee_legs():
                if leg.asset == quote_asset:
                    fee += leg.amount
                elif leg.asset == base_asset:
                    coin_fee += leg.amount
                    fee += leg.amount * fill.price
                elif leg.identified_rate is not None:
                    fee += leg.amount * leg.identified_rate
                else:
                    raise ValidationFailure("a fee in a third asset needs its identified rate to be valued")
            if fill.side == "buy":
                quantity, principal = quantity - coin_fee, principal - coin_fee * fill.price
            else:
                quantity, principal = quantity + coin_fee, principal + coin_fee * fill.price
            if fee < 0:
                principal = principal + fee if fill.side == "buy" else principal - fee
                fee = Decimal("0")
        return cls(fill_ref=fill_ref, symbol=fill.symbol, side=fill.side, quantity=quantity, quote_amount=principal,
                   fee=fee, at=fill.filled_at_utc)


@dataclass(frozen=True, slots=True)
class RoundTrip:
    symbol: str
    opened_at: datetime
    closed_at: datetime
    quantity: Decimal  # bought, and sold again
    bought: Decimal
    sold: Decimal
    fees: Decimal
    fill_refs: tuple[str, ...]

    @property
    def result(self) -> Decimal:
        with localcontext(_EXACT):
            return self.sold - self.bought - self.fees

    @property
    def outcome(self) -> Outcome:
        result = self.result
        return "win" if result > 0 else "loss" if result < 0 else "flat"

    def to_document(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "opened_at_utc": utc_iso(self.opened_at),
                "closed_at_utc": utc_iso(self.closed_at), "quantity": _text(self.quantity),
                "bought": _text(self.bought), "sold": _text(self.sold), "fees": _text(self.fees),
                "result": _text(self.result), "outcome": self.outcome, "fill_refs": list(self.fill_refs)}


@dataclass(frozen=True, slots=True)
class OpenPosition:
    symbol: str
    opened_at: datetime
    quantity: Decimal  # still held
    bought: Decimal
    sold: Decimal
    fees: Decimal
    fill_refs: tuple[str, ...]

    def to_document(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "opened_at_utc": utc_iso(self.opened_at), "quantity": _text(self.quantity),
                "bought": _text(self.bought), "sold": _text(self.sold), "fees": _text(self.fees),
                "fill_refs": list(self.fill_refs)}


@dataclass(slots=True)
class _Building:
    opened_at: datetime
    held: Decimal = Decimal("0")
    bought_quantity: Decimal = Decimal("0")
    bought: Decimal = Decimal("0")
    sold: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    refs: list[str] = field(default_factory=list)


def round_trips(fills: Sequence[TradeFill]) -> tuple[tuple[RoundTrip, ...], tuple[OpenPosition, ...]]:
    """Completed round trips in closing order, and the positions still open, per symbol."""
    trips: list[RoundTrip] = []
    building: dict[str, _Building] = {}
    previous: datetime | None = None
    with localcontext(_EXACT):
        for fill in fills:
            if not isinstance(fill, TradeFill):
                raise ValidationFailure("round trips are built from TradeFill records")
            if previous is not None and fill.at < previous:
                raise ValidationFailure("fills must be given in time order")
            previous = fill.at
            current = building.get(fill.symbol)
            if fill.side == "buy":
                if current is None:
                    current = building[fill.symbol] = _Building(opened_at=fill.at)
                current.held += fill.quantity
                current.bought_quantity += fill.quantity
                current.bought += fill.quote_amount
            else:
                if current is None or fill.quantity > current.held:
                    raise ValidationFailure("a sell larger than the position held is refused: the account is long "
                                            "only (D127)")
                current.held -= fill.quantity
                current.sold += fill.quote_amount
            current.fees += fill.fee
            current.refs.append(fill.fill_ref)
            if current.held == 0:
                trips.append(RoundTrip(symbol=fill.symbol, opened_at=current.opened_at, closed_at=fill.at,
                                       quantity=current.bought_quantity, bought=current.bought, sold=current.sold,
                                       fees=current.fees, fill_refs=tuple(current.refs)))
                del building[fill.symbol]
    still_open = tuple(OpenPosition(symbol=symbol, opened_at=item.opened_at, quantity=item.held, bought=item.bought,
                                    sold=item.sold, fees=item.fees, fill_refs=tuple(item.refs))
                       for symbol, item in sorted(building.items()))
    return tuple(trips), still_open


def worst_losing_streak(trips: Sequence[RoundTrip]) -> int:
    """The most consecutive losing trades; a win or a flat trade ends a streak."""
    worst = run = 0
    for trip in trips:
        run = run + 1 if trip.outcome == "loss" else 0
        worst = max(worst, run)
    return worst


@dataclass(frozen=True, slots=True)
class EquityPoint:
    at: datetime
    equity: Decimal

    def __post_init__(self) -> None:
        _aware("equity time", self.at)
        if _decimal("equity", self.equity) < 0:
            raise ValidationFailure("a long-only spot account without borrowing never has negative equity")


@dataclass(frozen=True, slots=True)
class Drawdown:
    fraction: Decimal
    amount: Decimal
    peak_at: datetime | None
    peak_equity: Decimal | None
    trough_at: datetime | None
    trough_equity: Decimal | None

    def to_document(self) -> dict[str, Any]:
        return {"fraction": _text(self.fraction), "amount": _text(self.amount), "peak_at_utc": _time(self.peak_at),
                "peak_equity": _text(self.peak_equity), "trough_at_utc": _time(self.trough_at),
                "trough_equity": _text(self.trough_equity)}


NO_DRAWDOWN = Drawdown(fraction=Decimal("0"), amount=Decimal("0"), peak_at=None, peak_equity=None, trough_at=None,
                       trough_equity=None)


def worst_drawdown(points: Sequence[EquityPoint]) -> Drawdown:
    """The largest fall from a running peak, as a fraction of that peak; the earliest wins a tie."""
    worst = NO_DRAWDOWN
    worst_exact = Decimal("0")
    peak: EquityPoint | None = None
    previous: datetime | None = None
    with localcontext(_EXACT):
        for point in points:
            if not isinstance(point, EquityPoint):
                raise ValidationFailure("drawdowns are measured on EquityPoint records")
            if previous is not None and point.at < previous:
                raise ValidationFailure("equity points must be given in time order")
            previous = point.at
            if peak is None or point.equity > peak.equity:
                peak = point
                continue
            if peak.equity <= 0:
                continue
            fall = peak.equity - point.equity
            exact = fall / peak.equity
            if exact > worst_exact:
                worst_exact = exact
                worst = Drawdown(fraction=_ratio(fall, peak.equity), amount=fall, peak_at=peak.at,
                                 peak_equity=peak.equity, trough_at=point.at, trough_equity=point.equity)
    return worst


@dataclass(frozen=True, slots=True)
class HoldBenchmark:
    """Spend the capital on one coin at the first price with the taker fee, sell it at the last price with the fee."""

    symbol: str
    capital: Decimal
    fee_rate: Decimal
    quantity: Decimal
    bought_at: datetime
    buy_price: Decimal
    buy_fee: Decimal
    cash_left: Decimal
    sold_at: datetime
    sell_price: Decimal
    sell_fee: Decimal

    @property
    def ending_value(self) -> Decimal:
        with localcontext(_EXACT):
            return self.cash_left + self.quantity * self.sell_price - self.sell_fee

    @property
    def result(self) -> Decimal:
        with localcontext(_EXACT):
            return self.ending_value - self.capital

    def to_document(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "capital": _text(self.capital), "fee_rate": _text(self.fee_rate),
                "quantity": _text(self.quantity), "bought_at_utc": utc_iso(self.bought_at),
                "buy_price": _text(self.buy_price), "buy_fee": _text(self.buy_fee), "cash_left": _text(self.cash_left),
                "sold_at_utc": utc_iso(self.sold_at), "sell_price": _text(self.sell_price),
                "sell_fee": _text(self.sell_fee), "ending_value": _text(self.ending_value),
                "result": _text(self.result)}


def hold_benchmark(*, symbol: str, capital: Decimal, buy_price: Decimal, sell_price: Decimal, fee_rate: Decimal,
                   quantity_increment: Decimal, bought_at: datetime, sold_at: datetime) -> HoldBenchmark:
    for label, value in (("capital", capital), ("buy price", buy_price), ("sell price", sell_price),
                         ("lot size", quantity_increment)):
        if _decimal(label, value) <= 0:
            raise ValidationFailure(f"the {label} must be positive")
    if not Decimal("0") <= _decimal("fee rate", fee_rate) < 1:
        raise ValidationFailure("a fee rate lies between 0 and 1")
    if _aware("buy time", bought_at) > _aware("sell time", sold_at):
        raise ValidationFailure("the coin is sold after it is bought")
    with localcontext(_EXACT):
        steps = (capital / (buy_price * (1 + fee_rate)) / quantity_increment).to_integral_value(rounding=ROUND_FLOOR)
        quantity = steps * quantity_increment
        principal = quantity * buy_price
        buy_fee = principal * fee_rate
        cash_left = capital - principal - buy_fee
        sell_fee = quantity * sell_price * fee_rate
    if quantity <= 0:
        raise ValidationFailure("the capital buys less than one lot of the coin")
    return HoldBenchmark(symbol=symbol, capital=capital, fee_rate=fee_rate, quantity=quantity, bought_at=bought_at,
                         buy_price=buy_price, buy_fee=buy_fee, cash_left=cash_left, sold_at=sold_at,
                         sell_price=sell_price, sell_fee=sell_fee)


@dataclass(frozen=True, slots=True)
class TradeMetrics:
    currency: str
    starting_equity: Decimal
    ending_equity: Decimal
    trades: int
    wins: int
    losses: int
    flat_trades: int
    total_won: Decimal
    total_lost: Decimal
    fees_paid: Decimal
    worst_drawdown: Drawdown
    worst_losing_streak: int
    round_trips: tuple[RoundTrip, ...]
    open_positions: tuple[OpenPosition, ...]
    benchmarks: Mapping[str, HoldBenchmark]

    @property
    def result(self) -> Decimal:
        with localcontext(_EXACT):
            return self.ending_equity - self.starting_equity

    @property
    def result_fraction(self) -> Decimal:
        return _ratio(self.result, self.starting_equity)

    @property
    def win_rate(self) -> Decimal | None:
        return None if not self.trades else _ratio(Decimal(self.wins), Decimal(self.trades))

    @property
    def average_win(self) -> Decimal | None:
        return None if not self.wins else _ratio(self.total_won, Decimal(self.wins))

    @property
    def average_loss(self) -> Decimal | None:
        return None if not self.losses else _ratio(self.total_lost, Decimal(self.losses))

    @property
    def versus_cash(self) -> Decimal:
        """Cash earns nothing (D127), so beating cash means a result above zero after fees."""
        return self.result

    @property
    def versus_benchmarks(self) -> dict[str, Decimal]:
        with localcontext(_EXACT):
            return {name: self.result - benchmark.result for name, benchmark in sorted(self.benchmarks.items())}

    def to_document(self) -> dict[str, Any]:
        versus = self.versus_benchmarks
        return {
            "metrics_version": METRICS_VERSION, "currency": self.currency,
            "starting_equity": _text(self.starting_equity), "ending_equity": _text(self.ending_equity),
            "result": _text(self.result), "result_fraction": _text(self.result_fraction),
            "trades": self.trades, "wins": self.wins, "losses": self.losses, "flat_trades": self.flat_trades,
            "win_rate": _text(self.win_rate), "total_won": _text(self.total_won), "total_lost": _text(self.total_lost),
            "average_win": _text(self.average_win), "average_loss": _text(self.average_loss),
            "fees_paid": _text(self.fees_paid), "worst_drawdown": self.worst_drawdown.to_document(),
            "worst_losing_streak": self.worst_losing_streak,
            "versus_cash": {"cash_result": "0", "versus": _text(self.versus_cash),
                            "note": "Cash earns no interest (D127)."},
            "benchmarks": {name: {**benchmark.to_document(), "versus": _text(versus[name])}
                           for name, benchmark in sorted(self.benchmarks.items())},
            "round_trips": [trip.to_document() for trip in self.round_trips],
            "open_positions": [position.to_document() for position in self.open_positions],
        }


def summarize(*, currency: str, starting_equity: Decimal, ending_equity: Decimal, fills: Sequence[TradeFill],
              equity_points: Sequence[EquityPoint],
              benchmarks: Mapping[str, HoldBenchmark] | None = None) -> TradeMetrics:
    """The report's sums. ``ending_equity`` is the caller's valuation, open positions included."""
    if _decimal("starting equity", starting_equity) <= 0:
        raise ValidationFailure("starting equity must be positive")
    if _decimal("ending equity", ending_equity) < 0:
        raise ValidationFailure("ending equity is never negative")
    trips, still_open = round_trips(fills)
    with localcontext(_EXACT):
        won = sum((trip.result for trip in trips if trip.outcome == "win"), Decimal("0"))
        lost = sum((trip.result for trip in trips if trip.outcome == "loss"), Decimal("0"))
        fees = sum((fill.fee for fill in fills), Decimal("0"))
    return TradeMetrics(
        currency=currency, starting_equity=starting_equity, ending_equity=ending_equity, trades=len(trips),
        wins=sum(1 for trip in trips if trip.outcome == "win"),
        losses=sum(1 for trip in trips if trip.outcome == "loss"),
        flat_trades=sum(1 for trip in trips if trip.outcome == "flat"), total_won=won, total_lost=lost,
        fees_paid=fees, worst_drawdown=worst_drawdown(equity_points), worst_losing_streak=worst_losing_streak(trips),
        round_trips=trips, open_positions=still_open, benchmarks=dict(benchmarks or {}))


__all__ = [
    "METRICS_VERSION", "NO_DRAWDOWN", "QUANTUM", "Drawdown", "EquityPoint", "HoldBenchmark", "OpenPosition",
    "RoundTrip", "TradeFill", "TradeMetrics", "hold_benchmark", "round_trips", "summarize", "worst_drawdown",
    "worst_losing_streak",
]
