"""Independent rounded execution examples bound conditional arithmetic, never venue proof."""

from decimal import ROUND_HALF_UP, Decimal, localcontext

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from trade_graph.kernel.native_fill_bounds import conservative_partial_fill_reserve


def _calculate(**updates):
    arguments = dict(
        order_quantity=Decimal("1"),
        price_ceiling=Decimal("100"),
        assumed_quantity_quantum=Decimal("0.1"),
        assumed_maximum_fill_count=10,
        quote_rounding_quantum=Decimal("0.01"),
        quote_fee_rate_ceiling=Decimal("0.008"),
        quote_fee_rounding_quantum=Decimal("0.01"),
    )
    return conservative_partial_fill_reserve(**(arguments | updates))


def test_conditional_bound_accounts_for_each_possible_principal_and_fee_rounding():
    result = _calculate()
    assert result.maximum_fill_count == 10
    assert result.principal_upper_bound == Decimal("100.1")
    assert result.quote_fee_upper_bound == Decimal("0.9008")
    assert result.total_quote_reserve == Decimal("101.0008")
    assert result.venue_evidence_verified is False
    assert result.diagnostic == "conditional_arithmetic_only_native_execution_facts_missing"


def test_quantity_implied_count_cannot_be_raised_by_larger_assumed_count():
    result = _calculate(assumed_maximum_fill_count=100)
    assert result.maximum_fill_count == 10
    assert result.total_quote_reserve == _calculate().total_quote_reserve


@pytest.mark.parametrize(
    "updates",
    [
        {"order_quantity": Decimal("0")},
        {"order_quantity": Decimal("0.95")},
        {"price_ceiling": Decimal("-1")},
        {"quote_fee_rate_ceiling": Decimal("-0.01")},
        {"assumed_maximum_fill_count": True},
        {"assumed_maximum_fill_count": 0},
        {"assumed_maximum_fill_count": 1_000_001},
        {"assumed_quantity_quantum": Decimal("0")},
        {"quote_rounding_quantum": Decimal("0")},
        {"quote_fee_rounding_quantum": Decimal("0")},
        {"price_ceiling": 0.1},
        {"price_ceiling": Decimal("NaN")},
        {"price_ceiling": Decimal("100.1234567890123456789012345678")},
    ],
)
def test_conditional_bound_refuses_invalid_or_inexact_assumptions(updates):
    with pytest.raises(ValueError):
        _calculate(**updates)


@pytest.mark.parametrize("precision", [3, 50])
def test_conditional_bound_does_not_inherit_ambient_precision(precision):
    with localcontext() as context:
        context.prec = precision
        result = _calculate()
    assert result.total_quote_reserve == Decimal("101.0008")


@settings(max_examples=100, derandomize=True, deadline=None)
@given(parts=st.lists(st.integers(1, 20), min_size=1, max_size=10), cents=st.integers(100, 100000))
def test_independent_native_rounding_of_all_fills_never_exceeds_conditional_reserve(parts, cents):
    # Every assumed fill quantity is a positive integer quantum; actual prices
    # are at or below the limit. Independently round each native principal/fee.
    quantum = Decimal("0.01")
    quantity_quantum = Decimal("0.001")
    price = Decimal(cents) / 100
    rate = Decimal("0.008")
    actual_debit = Decimal("0")
    for index, units in enumerate(parts):
        actual_price = price * (Decimal("0.9") if index % 2 else Decimal("1"))
        principal = (units * quantity_quantum * actual_price).quantize(quantum, rounding=ROUND_HALF_UP)
        fee = (principal * rate).quantize(quantum, rounding=ROUND_HALF_UP)
        actual_debit += principal + fee
    result = _calculate(
        order_quantity=sum(parts) * quantity_quantum,
        price_ceiling=price,
        assumed_quantity_quantum=quantity_quantum,
        assumed_maximum_fill_count=len(parts),
    )
    assert actual_debit <= result.total_quote_reserve
    assert result.venue_evidence_verified is False


@pytest.mark.parametrize("operand", [Decimal("1e-100000"), Decimal("1e100000"), "1e-999999999"])
def test_extreme_native_exponents_refuse_without_integer_materialization(operand):
    with pytest.raises(ValueError, match="bounded native width"):
        _calculate(assumed_quantity_quantum=operand)


def test_huge_but_bounded_decimal_count_is_capped_before_integer_conversion():
    result = _calculate(
        order_quantity=Decimal("1"), assumed_quantity_quantum=Decimal("1e-18"), assumed_maximum_fill_count=1_000_000
    )
    assert result.maximum_fill_count == 1_000_000
    assert result.venue_evidence_verified is False
