"""The one coverage arithmetic for filled day-plan buys (W10-C): ``kernel/day_plan_coverage.py``.

``DayPlanProtection`` (which places safety exits), HQ's day plans panel and HQ's "no safety exit" card all use it, so
the three never disagree about how much of a buy is covered. Plain values only; no database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from trade_graph.kernel import day_plan_coverage as coverage
from trade_graph.kernel.day_plan_coverage import Cover

D = Decimal
T0 = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


@pytest.mark.parametrize("state", sorted(coverage.OUTSTANDING) + ["FILLED"])
def test_an_order_still_out_or_filled_covers_its_whole_quantity(state) -> None:
    assert coverage.covers(Cover(state, D("5"), sold=D("1"))) == D("5")
    assert coverage.counts_whole(state)


@pytest.mark.parametrize("state", ["CANCELLED", "REJECTED", None, "SOMETHING_NEW"])
def test_an_order_that_ended_unsold_covers_only_what_it_sold(state) -> None:
    assert coverage.covers(Cover(state, D("5"), sold=D("1.5"))) == D("1.5")
    assert coverage.covers(Cover(state, D("5"))) == D("0")
    assert not coverage.counts_whole(state)


def test_an_unknown_status_is_never_read_as_ended() -> None:
    # An exit whose status is unknown covers its whole quantity, so it never leads to a second exit (D188).
    assert {"UNKNOWN", "CANCEL_PENDING", "SUBMITTING"} <= coverage.OUTSTANDING


def test_what_is_uncovered_is_the_fill_less_what_its_orders_cover_never_below_zero() -> None:
    orders = [Cover("OPEN", D("2")), Cover("CANCELLED", D("3"), sold=D("0.5")), Cover("FILLED", D("1"))]
    assert coverage.covered(orders) == D("3.5")
    assert coverage.uncovered(D("5"), orders) == D("1.5")
    assert coverage.uncovered(D("3"), orders) == D("0")
    assert coverage.uncovered(D("0"), []) == D("0")


def test_a_close_sell_is_shared_out_over_its_parts_in_their_order() -> None:
    parts = [(1, D("2")), (2, D("3")), (3, D("1"))]
    assert [coverage.shared_part(D("4"), parts, sequence) for sequence in (1, 2, 3)] == [D("2"), D("2"), D("0")]
    assert [coverage.shared_part(D("6"), parts, sequence) for sequence in (1, 2, 3)] == [D("2"), D("3"), D("1")]
    assert coverage.shared_part(D("4"), parts, 9) == D("0")  # not one of the sale's parts


def test_a_coins_unprotected_part_never_exceeds_what_is_held_and_not_protected() -> None:
    assert coverage.coin_unprotected(D("5"), held=D("5"), protected=D("0")) == D("5")
    assert coverage.coin_unprotected(D("5"), held=D("0"), protected=D("0")) == D("0")  # sold another way
    assert coverage.coin_unprotected(D("5"), held=D("7"), protected=D("4")) == D("3")
    assert coverage.coin_unprotected(D("1"), held=D("7"), protected=D("4")) == D("1")
    assert coverage.coin_unprotected(D("2"), held=D("3"), protected=D("4")) == D("0")


# --- since when -------------------------------------------------------------------------------------------------


def test_a_fill_whose_exit_was_never_placed_is_uncovered_from_the_fill() -> None:
    assert coverage.uncovered_since([(at(0), D("5"))], []) == at(0)


def test_a_fill_whose_exit_was_placed_at_once_is_covered() -> None:
    assert coverage.uncovered_since([(at(0), D("5"))], [Cover("OPEN", D("5"), placed_at=at(0))]) is None


def test_a_gap_that_closed_and_reopened_counts_from_the_reopening() -> None:
    fills = [(at(0), D("5"))]
    # The exit was put on 3 minutes late, then refused by the broker at minute 30: the gap is from minute 30.
    orders = [Cover("REJECTED", D("5"), placed_at=at(3), ended_at=at(30))]
    assert coverage.uncovered_since(fills, orders) == at(30)
    # Put on again at minute 31: covered now.
    assert coverage.uncovered_since(fills, [*orders, Cover("OPEN", D("5"), placed_at=at(31))]) is None


def test_a_second_bare_fill_does_not_hide_how_long_the_first_has_been_bare() -> None:
    fills = [(at(0), D("2")), (at(20), D("3"))]
    assert coverage.uncovered_since(fills, []) == at(0)
    # With the first part covered from the start, only the second part is bare: from minute 20.
    assert coverage.uncovered_since(fills, [Cover("OPEN", D("2"), placed_at=at(0))]) == at(20)


def test_an_exit_that_partly_sold_before_it_ended_leaves_the_rest_bare_from_its_end() -> None:
    orders = [Cover("CANCELLED", D("5"), sold=D("2"), placed_at=at(0), ended_at=at(45))]
    assert coverage.uncovered_since([(at(0), D("5"))], orders) == at(45)
    sold_out = [Cover("CANCELLED", D("5"), sold=D("5"), placed_at=at(0), ended_at=at(45))]
    assert coverage.uncovered_since([(at(0), D("5"))], sold_out) is None


def test_an_order_without_an_end_time_ended_when_it_was_placed() -> None:
    orders = [Cover("REJECTED", D("5"), placed_at=at(4))]
    assert coverage.uncovered_since([(at(0), D("5"))], orders) == at(0)


def test_an_order_without_a_placing_time_cannot_be_put_on_the_timeline() -> None:
    with pytest.raises(ValueError, match="placed"):
        coverage.uncovered_since([(at(0), D("5"))], [Cover("OPEN", D("5"))])


# --- one arithmetic, not three copies -------------------------------------------------------------------------


@pytest.mark.parametrize("module", ["src/trade_graph/application/day_plan_protection.py",
                                    "src/trade_graph/api/hq_plans.py", "src/trade_graph/api/hq_protection.py"])
def test_every_reader_uses_the_shared_arithmetic(module) -> None:
    source = (ROOT / module).read_text(encoding="utf-8")
    assert "day_plan_coverage" in source
    # The rule itself ("outstanding or filled covers the whole quantity") lives only in the kernel module.
    assert 'or state == "FILLED"' not in source and "== \"FILLED\" else" not in source
    assert '.get("state") == "FILLED"' not in source


@pytest.mark.parametrize("module", ["src/trade_graph/application/day_plan_actions.py",
                                    "src/trade_graph/api/hq_protection.py"])
def test_the_traders_position_view_and_hqs_card_cap_a_coin_the_same_way(module) -> None:
    source = (ROOT / module).read_text(encoding="utf-8")
    assert "coin_unprotected(" in source and "min(exposure, max(" not in source


def test_the_shared_arithmetic_is_protected_execution_control() -> None:
    from trade_graph.kernel.authority import path_is_protected

    assert path_is_protected("src/trade_graph/kernel/day_plan_coverage.py")
