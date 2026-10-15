"""Alarm-level comparison: exact, Decimal against the float's shortest decimal form, never from an unready value."""

from __future__ import annotations

import math
from decimal import Decimal

import pytest
from tests.indicator_results import RESULT_END as _END
from tests.indicator_results import make_result as result

from trade_graph.application.indicators.engine import (
    RELATIONS,
    LevelComparison,
    compare_to_level,
)
from trade_graph.application.indicators.library import SNAPSHOT_DIGITS, compute_raw, rounded_decimal
from trade_graph.domain.errors import ValidationFailure


def test_above_and_below_are_strict_and_report_the_two_numbers_used() -> None:
    outcome = compare_to_level(result([69.5, 70.5]), Decimal("70"), "above")
    assert outcome.satisfied is True and type(outcome.satisfied) is bool
    assert outcome.value == Decimal("70.5") and outcome.level == Decimal("70")
    assert outcome.evaluated is True and outcome.relation == "above"
    assert compare_to_level(result([70.0]), Decimal("70"), "above").satisfied is False
    assert compare_to_level(result([70.0]), Decimal("70"), "below").satisfied is False
    assert compare_to_level(result([69.99]), Decimal("70"), "below").satisfied is True
    assert compare_to_level(result([69.99]), Decimal("70"), "above").satisfied is False


def test_only_the_newest_point_is_compared_for_above_and_below() -> None:
    assert compare_to_level(result([80.0, 60.0]), Decimal("70"), "above").satisfied is False
    assert compare_to_level(result([60.0, 80.0]), Decimal("70"), "above").satisfied is True


def test_crossing_needs_the_previous_point_at_or_on_the_far_side_and_the_newest_strictly_across() -> None:
    assert compare_to_level(result([69.0, 71.0]), Decimal("70"), "crosses_above").satisfied is True
    assert compare_to_level(result([70.0, 71.0]), Decimal("70"), "crosses_above").satisfied is True  # left the level
    assert compare_to_level(result([71.0, 72.0]), Decimal("70"), "crosses_above").satisfied is False  # already above
    assert compare_to_level(result([69.0, 70.0]), Decimal("70"), "crosses_above").satisfied is False  # only touched
    assert compare_to_level(result([71.0, 69.0]), Decimal("70"), "crosses_below").satisfied is True
    assert compare_to_level(result([70.0, 69.0]), Decimal("70"), "crosses_below").satisfied is True
    assert compare_to_level(result([69.0, 68.0]), Decimal("70"), "crosses_below").satisfied is False
    assert compare_to_level(result([71.0, 70.0]), Decimal("70"), "crosses_below").satisfied is False
    outcome = compare_to_level(result([69.0, 71.0]), Decimal("70"), "crosses_above")
    assert (outcome.previous_value, outcome.value, outcome.level) == (Decimal("69"), Decimal("71"), Decimal("70"))


def test_a_crossing_over_a_touch_fires_exactly_once() -> None:
    series = [68.0, 70.0, 71.0, 72.0]
    fired = [compare_to_level(result(series[: end + 1][-2:]), Decimal("70"), "crosses_above").satisfied
             for end in range(1, len(series))]
    assert fired == [False, True, False]


def test_a_crossing_needs_two_points_and_says_so() -> None:
    outcome = compare_to_level(result([75.0]), Decimal("70"), "crosses_above")
    assert outcome.satisfied is False and outcome.evaluated is False
    assert "two" in outcome.reason and outcome.previous_value is None


@pytest.mark.parametrize("status", ["warming_up", "gapped"])
@pytest.mark.parametrize("relation", RELATIONS)
def test_an_unready_indicator_can_never_satisfy_a_comparison(status, relation) -> None:
    outcome = compare_to_level(result([], status=status), Decimal("0"), relation)
    assert outcome.satisfied is False and outcome.evaluated is False
    assert outcome.value is None and status.replace("_", " ") in outcome.reason
    assert not outcome  # truth value follows the verdict, so an `if` on it cannot misfire


def test_the_comparison_object_is_truthy_only_when_satisfied() -> None:
    assert bool(compare_to_level(result([71.0]), Decimal("70"), "above")) is True
    assert bool(compare_to_level(result([69.0]), Decimal("70"), "above")) is False


def test_multi_output_indicators_need_a_named_output() -> None:
    macd = result({"macd": [1.0, -1.0], "signal": [0.0, 0.0], "histogram": [1.0, -1.0]},
                  outputs=("macd", "signal", "histogram"), indicator="MACD")
    with pytest.raises(ValidationFailure):
        compare_to_level(macd, Decimal("0"), "above")
    assert compare_to_level(macd, Decimal("0"), "crosses_below", output="macd").satisfied is True
    assert compare_to_level(macd, Decimal("0"), "above", output="signal").satisfied is False
    with pytest.raises(ValidationFailure):
        compare_to_level(macd, Decimal("0"), "above", output="nonsense")


def test_the_level_must_be_a_finite_decimal_and_the_relation_a_known_one() -> None:
    for bad in (70, 70.0, "70", None, Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"), True):
        with pytest.raises(ValidationFailure):
            compare_to_level(result([71.0]), bad, "above")  # type: ignore[arg-type]
    for relation in ("greater", "ABOVE", "", None, "crosses"):
        with pytest.raises(ValidationFailure):
            compare_to_level(result([71.0]), Decimal("70"), relation)  # type: ignore[arg-type]


def test_a_level_with_an_absurd_number_of_digits_is_refused() -> None:
    with pytest.raises(ValidationFailure):
        compare_to_level(result([71.0]), Decimal("1." + "0" * 80 + "1"), "above")


def test_value_and_level_are_compared_at_twelve_significant_digits() -> None:
    """The library's running sums add noise in the last digits, so the comparison is made at the digits it can vouch
    for (the rule the stored snapshots use): a value that equals the level to twelve digits is equal to it."""
    assert SNAPSHOT_DIGITS == 12
    assert compare_to_level(result([0.1]), Decimal("0.1"), "above").satisfied is False
    assert compare_to_level(result([0.1]), Decimal("0.1"), "below").satisfied is False
    # 0.1 + 0.2 is the float 0.30000000000000004: equal to 0.3 at twelve digits, so neither above nor below it.
    assert compare_to_level(result([0.1 + 0.2]), Decimal("0.3"), "above").satisfied is False
    assert compare_to_level(result([70.00000000000001]), Decimal("70"), "above").satisfied is False
    assert compare_to_level(result([69.99999999999999]), Decimal("70"), "below").satisfied is False
    # A real difference at the twelfth digit is a difference.
    assert compare_to_level(result([70.0000000001]), Decimal("70"), "above").satisfied is True
    assert compare_to_level(result([69.9999999999]), Decimal("70"), "below").satisfied is True
    # The level is rounded the same way, so a level written with more digits than that is read at twelve.
    assert compare_to_level(result([70.0]), Decimal("70.00000000000004"), "below").satisfied is False
    outcome = compare_to_level(result([69.99999999999999, 70.00000000000001]), Decimal("70"), "crosses_above")
    assert outcome.satisfied is False  # the path 69.99999999999999 -> 70.00000000000001 only touched 70
    assert outcome.value == Decimal("70") and outcome.previous_value == Decimal("70") and outcome.level == Decimal("70")


@pytest.mark.parametrize("price", ["63000.1", "0.5", "1.2345", "2599.99", "31.07", "0.0123457", "4.4", "63000.7"])
@pytest.mark.parametrize("period", [3, 7, 20, 50])
def test_a_real_library_average_of_equal_closes_is_not_above_or_below_that_price(price, period) -> None:
    """TA-Lib's running sum gives 63000.09999999999 or 63000.10000000006 for a flat 63000.1; the alarm must not fire."""
    closes = {"close": (Decimal(price),) * (period + 5)}
    value = float(compute_raw("SMA", {"timeperiod": period}, closes)["value"][-1])
    flat = result([value])
    for relation in ("above", "below"):
        assert compare_to_level(flat, Decimal(price), relation).satisfied is False, (price, period, value)
    assert compare_to_level(result([value, value]), Decimal(price), "crosses_above").satisfied is False


def test_the_fixture_for_the_flat_average_really_contains_floating_point_noise() -> None:
    noisy = [price for price in ("63000.1", "0.5", "1.2345", "2599.99", "31.07", "0.0123457", "4.4", "63000.7")
             for period in (3, 7, 20, 50)
             if float(compute_raw("SMA", {"timeperiod": period}, {"close": (Decimal(price),) * (period + 5)})
                      ["value"][-1]) != float(price)]
    assert noisy, "no flat-price average differs from the price, so the test above proves nothing"


def test_huge_and_tiny_levels_and_values_compare_without_raising() -> None:
    for number in (1e22, 1.5e-9, 123456789.123456789, -0.0, 5e-324, 1.7976931348623157e308):
        for level in (Decimal("1E+999999999"), Decimal("-1E+999999999"), Decimal("1E-999999999"), Decimal("0")):
            outcome = compare_to_level(result([number]), level, "above")
            assert outcome.evaluated is True and isinstance(outcome.satisfied, bool)
    assert compare_to_level(result([-0.0]), Decimal("0"), "below").satisfied is False
    assert compare_to_level(result([-0.0]), Decimal("0"), "above").satisfied is False


def test_a_non_finite_value_in_a_ready_result_is_never_compared() -> None:
    ready_with_nan = result([math.nan])
    outcome = compare_to_level(ready_with_nan, Decimal("0"), "below")
    assert outcome.satisfied is False and outcome.evaluated is False
    with pytest.raises(ValidationFailure):
        rounded_decimal(math.inf)


def test_comparison_records_when_the_value_belongs() -> None:
    outcome = compare_to_level(result([69.0, 71.0]), Decimal("70"), "crosses_above")
    assert isinstance(outcome, LevelComparison) and outcome.close_time_utc == _END
    assert outcome.indicator == "RSI" and outcome.output == "value"
