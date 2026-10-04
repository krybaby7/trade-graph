"""Conservative conditional arithmetic; assumptions never establish venue guarantees."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, Inexact, localcontext

from trade_graph.domain.money import parse_decimal


@dataclass(frozen=True)
class ConditionalPartialFillReserve:
    maximum_fill_count: int
    principal_upper_bound: Decimal
    quote_fee_upper_bound: Decimal
    total_quote_reserve: Decimal

    @property
    def venue_evidence_verified(self) -> bool:
        return False

    @property
    def diagnostic(self) -> str:
        return "conditional_arithmetic_only_native_execution_facts_missing"


def conservative_partial_fill_reserve(
    *,
    order_quantity: Decimal,
    price_ceiling: Decimal,
    assumed_quantity_quantum: Decimal,
    assumed_maximum_fill_count: int,
    quote_rounding_quantum: Decimal,
    quote_fee_rate_ceiling: Decimal,
    quote_fee_rounding_quantum: Decimal,
) -> ConditionalPartialFillReserve:
    """Upper bound if every assumed native fact holds; no broker/admission consumes it.

    Every fill's native quote rounding and fee rounding can increase its debit by
    less than one declared quantum. Reserve a whole quantum per possible fill and
    charge the maximum fee rate on the enlarged principal. Expected rebates never
    reduce the hold. This bounds quote fees only, never base/third-asset vectors.
    """
    if type(assumed_maximum_fill_count) is not int or not 1 <= assumed_maximum_fill_count <= 1_000_000:
        raise ValueError("conditional native fill count must be a bounded positive integer")
    values = []
    for item in (
        order_quantity,
        price_ceiling,
        assumed_quantity_quantum,
        quote_rounding_quantum,
        quote_fee_rate_ceiling,
        quote_fee_rounding_quantum,
    ):
        if isinstance(item, str) and len(item) > 96:
            raise ValueError("conditional native reserve operand is oversized")
        value = parse_decimal(item)
        shape = value.as_tuple()
        if not -18 <= shape.exponent <= 28 or len(shape.digits) + shape.exponent > 28:
            raise ValueError("conditional native reserve operand exceeds bounded native width")
        values.append(value)
    quantity, price, quantity_quantum, quote_quantum, fee_rate, fee_quantum = values
    if min(quantity, price, quantity_quantum, quote_quantum, fee_quantum) <= 0 or fee_rate < 0:
        raise ValueError("conditional native quantities/quantums require positive values and nonnegative fee rate")
    with localcontext(Context(prec=28)) as context:
        context.traps[Inexact] = True
        try:
            for value in values:
                context.plus(value)
            if quantity % quantity_quantum:
                raise ValueError("order quantity must align to the assumed execution quantum")
            implied_count = quantity / quantity_quantum
            count = assumed_maximum_fill_count if implied_count >= assumed_maximum_fill_count else int(implied_count)
            if count < 1:
                raise ValueError("assumed execution quantum exceeds the order quantity")
            principal = quantity * price + count * quote_quantum
            fees = principal * fee_rate + count * fee_quantum
            total = principal + fees
        except ArithmeticError:
            raise ValueError(
                "conditional native reserve cannot be represented exactly at protected precision"
            ) from None
    return ConditionalPartialFillReserve(count, principal, fees, total)
