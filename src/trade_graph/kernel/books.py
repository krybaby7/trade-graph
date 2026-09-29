"""Native-asset ledger math. Each transaction balances per asset. No binary floats."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from trade_graph.contracts.models import FillRecord


class BooksError(ValueError):
    pass


@dataclass
class Posting:
    account: str
    asset: str
    amount: Decimal


@dataclass
class Flow:
    kind: str
    asset: str
    amount: Decimal
    at: str
    ref: str


@dataclass
class Expense:
    expense_id: str
    native_amount: Decimal
    native_currency: str
    reporting_amount: Decimal
    reporting_currency: str
    embedded: bool
    source: str
    status: str
    at: str
    settles: str | None = None


@dataclass
class Disposal:
    quantity: Decimal
    cost_released: Decimal
    proceeds: Decimal
    proceeds_currency: str
    realized: Decimal
    at: str
    source_ref: str


@dataclass
class Lot:
    lot_id: str
    asset: str
    quantity_original: Decimal
    cost_original: Decimal
    cost_currency: str
    opened_at: str
    source_ref: str
    disposals: list[Disposal] = field(default_factory=list)

    def open_quantity(self, at: str | None = None) -> Decimal:
        used = sum(item.quantity for item in self.disposals if at is None or item.at <= at)
        return self.quantity_original - used

    def open_cost(self, at: str | None = None) -> Decimal:
        used = sum(
            item.cost_released for item in self.disposals if at is None or item.at <= at
        )
        return self.cost_original - used


@dataclass
class Mark:
    asset: str
    price: Decimal
    quote: str
    at: str
    stale: bool
    source: str


@dataclass
class FxRate:
    base: str
    quote: str
    rate: Decimal
    at: str
    stale: bool
    source: str
    rate_id: str


@dataclass
class Books:
    cash: dict[str, Decimal] = field(default_factory=dict)
    lots: list[Lot] = field(default_factory=list)
    flows: list[Flow] = field(default_factory=list)
    expenses: list[Expense] = field(default_factory=list)
    groups: list[list[Posting]] = field(default_factory=list)

    def cash_amount(self, asset: str) -> Decimal:
        return self.cash.get(asset, Decimal("0"))


def _require_balance(group: list[Posting]) -> None:
    totals: dict[str, Decimal] = {}
    for posting in group:
        totals[posting.asset] = totals.get(posting.asset, Decimal("0")) + posting.amount
    for asset, total in totals.items():
        if total != 0:
            raise BooksError(f"unbalanced {asset}: {total}")


def _apply_group(books: Books, group: list[Posting]) -> None:
    _require_balance(group)
    books.groups.append(group)
    for posting in group:
        if posting.account == "cash" or posting.account.startswith("cash:"):
            books.cash[posting.asset] = books.cash.get(posting.asset, Decimal("0")) + posting.amount


def _post(group: list[Posting], account: str, asset: str, amount: Decimal) -> None:
    if amount == 0:
        return
    group.append(Posting(account, asset, amount))


def deposit(books: Books, asset: str, amount: Decimal, at: str, ref: str) -> None:
    if amount <= 0:
        raise BooksError("deposit must be positive")
    group: list[Posting] = []
    _post(group, "cash", asset, amount)
    _post(group, "external", asset, -amount)
    _apply_group(books, group)
    books.flows.append(Flow("deposit", asset, amount, at, ref))


def withdraw(books: Books, asset: str, amount: Decimal, at: str, ref: str) -> None:
    if amount <= 0:
        raise BooksError("withdrawal must be positive")
    if books.cash_amount(asset) < amount:
        raise BooksError("insufficient cash")
    group: list[Posting] = []
    _post(group, "cash", asset, -amount)
    _post(group, "external", asset, amount)
    _apply_group(books, group)
    books.flows.append(Flow("withdrawal", asset, amount, at, ref))


def internal_transfer(
    books: Books, asset: str, amount: Decimal, at: str, ref: str, *, to_account: str = "cash:reserve"
) -> None:
    if amount <= 0:
        raise BooksError("transfer must be positive")
    if books.cash_amount(asset) < amount:
        raise BooksError("insufficient cash")
    group: list[Posting] = []
    _post(group, "cash", asset, -amount)
    _post(group, to_account, asset, amount)
    _apply_group(books, group)
    books.flows.append(Flow("internal", asset, amount, at, ref))


def _fifo_consume(
    books: Books,
    asset: str,
    quantity: Decimal,
    proceeds: Decimal,
    proceeds_currency: str,
    at: str,
    source_ref: str,
) -> Decimal:
    if quantity <= 0:
        raise BooksError("quantity must be positive")
    remaining = quantity
    proceeds_left = proceeds
    cost_released = Decimal("0")
    lots = sorted(books.lots, key=lambda item: (item.opened_at, item.lot_id))
    for lot in lots:
        if lot.asset != asset:
            continue
        open_qty = lot.open_quantity()
        if open_qty <= 0:
            continue
        take = open_qty if open_qty <= remaining else remaining
        rem_cost = lot.open_cost()
        released = rem_cost if take == open_qty else rem_cost * take / open_qty
        portion = proceeds if take == remaining else proceeds * take / quantity
        if lot.cost_currency != proceeds_currency:
            raise BooksError("cost currency does not match proceeds")
        realized = portion - released
        lot.disposals.append(
            Disposal(take, released, portion, proceeds_currency, realized, at, source_ref)
        )
        cost_released += released
        remaining -= take
        proceeds_left -= portion
        if remaining == 0:
            break
    if remaining != 0:
        raise BooksError("insufficient inventory")
    return cost_released


def apply_fill(
    books: Books,
    fill: FillRecord,
    *,
    base_asset: str,
    quote_asset: str,
    lot_id: str,
    at: str,
) -> None:
    quantity = fill.quantity
    notional = fill.price * quantity
    fee = fill.fee_amount
    group: list[Posting] = []
    if fill.side == "buy":
        if fill.fee_asset == quote_asset:
            cash_out = notional + fee
            if books.cash_amount(quote_asset) < cash_out:
                raise BooksError("insufficient cash")
            _post(group, "cash", quote_asset, -cash_out)
            _post(group, "clearing", quote_asset, cash_out)
            _post(group, "inventory", base_asset, quantity)
            _post(group, "clearing", base_asset, -quantity)
            _apply_group(books, group)
            books.lots.append(
                Lot(lot_id, base_asset, quantity, cash_out, quote_asset, at, fill.trade_id)
            )
            return
        if fill.fee_asset == base_asset:
            net = quantity - fee
            if net <= 0:
                raise BooksError("base fee consumes the fill")
            if books.cash_amount(quote_asset) < notional:
                raise BooksError("insufficient cash")
            _post(group, "cash", quote_asset, -notional)
            _post(group, "clearing", quote_asset, notional)
            _post(group, "inventory", base_asset, net)
            _post(group, "clearing", base_asset, -net)
            _apply_group(books, group)
            books.lots.append(Lot(lot_id, base_asset, net, notional, quote_asset, at, fill.trade_id))
            return
        if fill.fee_identified_rate is None:
            raise BooksError("third-asset fee requires an identified rate")
        identified = fee * fill.fee_identified_rate
        if books.cash_amount(quote_asset) < notional:
            raise BooksError("insufficient cash")
        _fifo_consume(
            books,
            fill.fee_asset,
            fee,
            identified,
            quote_asset,
            at,
            f"{fill.trade_id}:fee",
        )
        _post(group, "cash", quote_asset, -notional)
        _post(group, "clearing", quote_asset, notional)
        _post(group, "inventory", base_asset, quantity)
        _post(group, "clearing", base_asset, -quantity)
        _post(group, "inventory", fill.fee_asset, -fee)
        _post(group, "clearing", fill.fee_asset, fee)
        _apply_group(books, group)
        books.lots.append(
            Lot(lot_id, base_asset, quantity, notional + identified, quote_asset, at, fill.trade_id)
        )
        return
    if fill.side != "sell":
        raise BooksError("side")
    if fill.fee_asset == quote_asset:
        proceeds = notional - fee
        _post(group, "cash", quote_asset, proceeds)
        _post(group, "clearing", quote_asset, -proceeds)
        _post(group, "inventory", base_asset, -quantity)
        _post(group, "clearing", base_asset, quantity)
        _fifo_consume(books, base_asset, quantity, proceeds, quote_asset, at, fill.trade_id)
        _apply_group(books, group)
        return
    if fill.fee_asset == base_asset:
        proceeds = notional
        total_qty = quantity + fee
        _post(group, "cash", quote_asset, proceeds)
        _post(group, "clearing", quote_asset, -proceeds)
        _post(group, "inventory", base_asset, -total_qty)
        _post(group, "clearing", base_asset, total_qty)
        _fifo_consume(books, base_asset, total_qty, proceeds, quote_asset, at, fill.trade_id)
        _apply_group(books, group)
        return
    if fill.fee_identified_rate is None:
        raise BooksError("third-asset fee requires an identified rate")
    identified = fee * fill.fee_identified_rate
    proceeds = notional - identified
    _fifo_consume(books, base_asset, quantity, proceeds, quote_asset, at, fill.trade_id)
    _fifo_consume(
        books, fill.fee_asset, fee, identified, quote_asset, at, f"{fill.trade_id}:fee"
    )
    _post(group, "cash", quote_asset, proceeds)
    _post(group, "clearing", quote_asset, -proceeds)
    _post(group, "inventory", base_asset, -quantity)
    _post(group, "clearing", base_asset, quantity)
    _post(group, "inventory", fill.fee_asset, -fee)
    _post(group, "clearing", fill.fee_asset, fee)
    _apply_group(books, group)


def add_expense(books: Books, expense: Expense) -> None:
    if expense.settles:
        prior = next(item for item in books.expenses if item.expense_id == expense.settles)
        prior.status = "settled"
        books.expenses.append(expense)
        return
    if expense.embedded:
        group: list[Posting] = []
        _post(group, "cash", expense.reporting_currency, -expense.reporting_amount)
        _post(group, "clearing", expense.reporting_currency, expense.reporting_amount)
        _apply_group(books, group)
    books.expenses.append(expense)


def _convert(
    amount: Decimal,
    currency: str,
    reporting: str,
    rates: list[FxRate],
    at: str,
) -> tuple[Decimal | None, bool]:
    if currency == reporting:
        return amount, False
    chosen = [
        rate
        for rate in rates
        if rate.base == currency and rate.quote == reporting and rate.at <= at
    ]
    if not chosen:
        return None, True
    rate = max(chosen, key=lambda item: item.at)
    return amount * rate.rate, rate.stale


@dataclass
class EquityView:
    equity: Decimal | None
    reporting_currency: str
    provisional: bool
    unrealized: Decimal | None
    cash_reporting: Decimal | None
    inventory_reporting: Decimal | None
    baseline: Decimal | None
    stale: bool


def mark_equity(
    books: Books,
    *,
    reporting: str,
    marks: list[Mark],
    rates: list[FxRate],
    at: str,
) -> EquityView:
    provisional = False
    stale = False
    cash_total = Decimal("0")
    for asset, amount in books.cash.items():
        converted, bad = _convert(amount, asset, reporting, rates, at)
        stale = stale or bad
        if converted is None:
            provisional = True
            cash_total_missing = True
            break
        cash_total += converted
    else:
        cash_total_missing = False
    inventory_total = Decimal("0")
    cost_total = Decimal("0")
    for lot in books.lots:
        qty = lot.open_quantity()
        if qty == 0:
            continue
        mark = _latest_mark(marks, lot.asset, at)
        if mark is None:
            provisional = True
            continue
        stale = stale or mark.stale
        value_quote = qty * mark.price
        value, bad_value = _convert(value_quote, mark.quote, reporting, rates, at)
        cost, bad_cost = _convert(lot.open_cost(), lot.cost_currency, reporting, rates, at)
        stale = stale or bad_value or bad_cost
        if value is None or cost is None:
            provisional = True
            continue
        inventory_total += value
        cost_total += cost
    baseline_cash: dict[str, Decimal] = {}
    for flow in books.flows:
        if flow.at > at or flow.kind == "internal":
            continue
        sign = Decimal("1") if flow.kind == "deposit" else Decimal("-1")
        baseline_cash[flow.asset] = baseline_cash.get(flow.asset, Decimal("0")) + sign * flow.amount
    baseline = Decimal("0")
    baseline_ok = True
    for asset, amount in baseline_cash.items():
        converted, bad = _convert(amount, asset, reporting, rates, at)
        stale = stale or bad
        if converted is None:
            baseline_ok = False
            provisional = True
            break
        baseline += converted
    if provisional or cash_total_missing:
        return EquityView(None, reporting, True, None, None, None, None, True)
    unrealized = inventory_total - cost_total
    return EquityView(
        cash_total + inventory_total,
        reporting,
        stale,
        unrealized,
        cash_total,
        inventory_total,
        baseline if baseline_ok else None,
        stale,
    )


def _latest_mark(marks: list[Mark], asset: str, at: str) -> Mark | None:
    chosen = [mark for mark in marks if mark.asset == asset and mark.at <= at]
    if not chosen:
        return None
    return max(chosen, key=lambda item: item.at)


@dataclass
class Performance:
    equity_start: Decimal | None
    equity_end: Decimal | None
    external_flow: Decimal
    embedded_operating: Decimal
    all_operating: Decimal
    trading_pnl: Decimal | None
    economic_pnl: Decimal | None
    provisional: bool
    baseline_end: Decimal | None
    alpha: Decimal | None


def _flow_reporting(books: Books, start: str, end: str, reporting: str, rates: list[FxRate]) -> tuple[Decimal, bool]:
    total = Decimal("0")
    provisional = False
    for flow in books.flows:
        if flow.kind == "internal" or not (start < flow.at <= end):
            continue
        sign = Decimal("1") if flow.kind == "deposit" else Decimal("-1")
        converted, bad = _convert(flow.amount, flow.asset, reporting, rates, flow.at)
        if converted is None:
            provisional = True
            continue
        provisional = provisional or bad
        total += sign * converted
    return total, provisional


def _operating(books: Books, start: str, end: str) -> tuple[Decimal, Decimal]:
    embedded = Decimal("0")
    total = Decimal("0")
    for expense in books.expenses:
        if expense.settles or not (start < expense.at <= end):
            continue
        if expense.status == "void":
            continue
        total += expense.reporting_amount
        if expense.embedded:
            embedded += expense.reporting_amount
    return embedded, total


def performance(
    books: Books,
    *,
    reporting: str,
    marks: list[Mark],
    rates: list[FxRate],
    start: str,
    end: str,
    start_view: EquityView,
    end_view: EquityView,
) -> Performance:
    flows, flow_provisional = _flow_reporting(books, start, end, reporting, rates)
    embedded, all_operating = _operating(books, start, end)
    provisional = start_view.provisional or end_view.provisional or flow_provisional
    trading = None
    economic = None
    alpha = None
    if (
        start_view.equity is not None
        and end_view.equity is not None
        and not provisional
    ):
        trading = end_view.equity - start_view.equity - flows + embedded
        economic = trading - all_operating
        if end_view.baseline is not None:
            alpha = end_view.equity - end_view.baseline
    return Performance(
        start_view.equity,
        end_view.equity,
        flows,
        embedded,
        all_operating,
        trading,
        economic,
        provisional,
        end_view.baseline,
        alpha,
    )
