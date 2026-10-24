"""The shared day-plan coverage arithmetic (W10-C) agrees with the rule it replaced and with its own timeline.

- ``covered`` is the T34/W8-B rule that ``DayPlanProtection.coverage`` and HQ's day plans panel each wrote out before:
  an order outstanding (including an unknown status) or filled covers its whole quantity, any other what it sold.
- ``uncovered_since`` says the uncovered part began at a moment exactly when one is uncovered now, and that moment is
  the start of the last stretch during which the records never covered the whole fill.

Generated plain values only (derandomized); no database, clock or network.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from trade_graph.kernel import day_plan_coverage as coverage
from trade_graph.kernel.day_plan_coverage import Cover

D = Decimal
T0 = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)
STATES = ["SUBMISSION_PENDING", "SUBMITTING", "OPEN", "PARTIALLY_FILLED", "UNKNOWN", "CANCEL_PENDING", "FILLED",
          "CANCELLED", "REJECTED", None]
AMOUNTS = st.sampled_from([D("0.0001"), D("0.5"), D("1"), D("2"), D("2.5"), D("5")])
MINUTES = st.integers(0, 120)

fills = st.lists(st.tuples(MINUTES, AMOUNTS), max_size=4)
orders = st.lists(st.tuples(st.sampled_from(STATES), AMOUNTS, st.sampled_from([D("0"), D("0.5"), D("2")]), MINUTES,
                            st.one_of(st.none(), MINUTES)), max_size=5)


def _old_rule(filled: Decimal, items: list[Cover]) -> Decimal:
    """``DayPlanProtection.coverage`` and ``hq_plans.entry_view`` as they were written out before W10-C."""
    outstanding = {"SUBMISSION_PENDING", "SUBMITTING", "OPEN", "PARTIALLY_FILLED", "UNKNOWN", "CANCEL_PENDING"}
    covered = D("0")
    for item in items:
        covered += item.quantity if item.state in outstanding or item.state == "FILLED" else item.sold
    return max(D("0"), filled - covered)


def _build(fill_rows, order_rows):
    timed_fills = [(T0 + timedelta(minutes=minute), amount) for minute, amount in fill_rows]
    covers = [Cover(state, amount, sold=sold, placed_at=T0 + timedelta(minutes=placed),
                    ended_at=None if ended is None else T0 + timedelta(minutes=ended))
              for state, amount, sold, placed, ended in order_rows]
    return timed_fills, covers


def _gap_at(moment: datetime, timed_fills, covers) -> Decimal:
    """Brute force: filled less covered as the records stood just after ``moment``."""
    filled = sum((amount for when, amount in timed_fills if when <= moment), D("0"))
    covered = D("0")
    for item in covers:
        if item.placed_at > moment:
            continue
        end = max(item.ended_at or item.placed_at, item.placed_at)
        whole = coverage.counts_whole(item.state) or end > moment
        covered += item.quantity if whole else item.sold
    return filled - covered


@settings(max_examples=300, derandomize=True, deadline=None)
@given(fills, orders)
def test_covered_is_the_rule_it_replaced(fill_rows, order_rows) -> None:
    timed_fills, covers = _build(fill_rows, order_rows)
    filled = sum((amount for _when, amount in timed_fills), D("0"))
    assert coverage.uncovered(filled, covers) == _old_rule(filled, covers)


@settings(max_examples=300, derandomize=True, deadline=None)
@given(fills, orders)
def test_since_marks_the_start_of_the_current_uncovered_stretch(fill_rows, order_rows) -> None:
    timed_fills, covers = _build(fill_rows, order_rows)
    filled = sum((amount for _when, amount in timed_fills), D("0"))
    since = coverage.uncovered_since(timed_fills, covers)
    assert (since is not None) == (coverage.uncovered(filled, covers) > 0)
    if since is None:
        return
    moments = sorted({when for when, _ in timed_fills} | {item.placed_at for item in covers}
                     | {max(item.ended_at or item.placed_at, item.placed_at) for item in covers})
    assert since in moments
    # Uncovered at every recorded moment from ``since`` on, and covered (or nothing bought) just before it.
    assert all(_gap_at(moment, timed_fills, covers) > 0 for moment in moments if moment >= since)
    earlier = [moment for moment in moments if moment < since]
    assert not earlier or _gap_at(earlier[-1], timed_fills, covers) <= 0
