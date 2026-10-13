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
    float_as_decimal,
)
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


def test_the_float_is_compared_through_its_shortest_decimal_form_with_no_tolerance() -> None:
    # 0.1 as a float is a hair above one tenth; its shortest decimal form is exactly 0.1, so it equals the level.
    assert float_as_decimal(0.1) == Decimal("0.1")
    assert compare_to_level(result([0.1]), Decimal("0.1"), "above").satisfied is False
    assert compare_to_level(result([0.1]), Decimal("0.1"), "below").satisfied is False
    # 0.1 + 0.2 is the float 0.30000000000000004: really above 0.3, and compared exactly so.
    assert float_as_decimal(0.1 + 0.2) == Decimal("0.30000000000000004")
    assert compare_to_level(result([0.1 + 0.2]), Decimal("0.3"), "above").satisfied is True
    # No tolerance: a hair away is a different side.
    assert compare_to_level(result([70.00000000000001]), Decimal("70"), "above").satisfied is True
    assert compare_to_level(result([69.99999999999999]), Decimal("70"), "below").satisfied is True


def test_large_and_tiny_floats_convert_without_loss() -> None:
    for number in (1e22, 1.5e-9, 123456789.123456789, -0.0, 5e-324, 1.7976931348623157e308):
        assert float(float_as_decimal(number)) == number
    assert float_as_decimal(-0.0) == 0


def test_a_non_finite_value_in_a_ready_result_is_never_compared() -> None:
    ready_with_nan = result([math.nan])
    outcome = compare_to_level(ready_with_nan, Decimal("0"), "below")
    assert outcome.satisfied is False and outcome.evaluated is False
    with pytest.raises(ValidationFailure):
        float_as_decimal(math.inf)


def test_comparison_records_when_the_value_belongs() -> None:
    outcome = compare_to_level(result([69.0, 71.0]), Decimal("70"), "crosses_above")
    assert isinstance(outcome, LevelComparison) and outcome.close_time_utc == _END
    assert outcome.indicator == "RSI" and outcome.output == "value"
