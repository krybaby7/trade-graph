"""Generated checks that recurring occurrences stay on their persisted grid."""

from datetime import UTC, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from trade_graph.application.scheduler import next_occurrence

START = datetime(2026, 1, 1, tzinfo=UTC)


@settings(max_examples=200, derandomize=True, deadline=None)
@given(
    due_microseconds=st.integers(0, 10 ** 13),
    lateness_microseconds=st.integers(0, 10 ** 13),
    interval_seconds=st.integers(1, 10 ** 6),
)
def test_next_occurrence_is_the_first_grid_point_after_now(due_microseconds, lateness_microseconds, interval_seconds):
    due = START + timedelta(microseconds=due_microseconds)
    now = due + timedelta(microseconds=lateness_microseconds)
    step = timedelta(seconds=interval_seconds)
    result = next_occurrence(due, now, interval_seconds)
    assert now < result <= now + step
    assert (result - due) % step == timedelta(0)
    assert result - due >= step


@settings(max_examples=50, derandomize=True, deadline=None)
@given(early_microseconds=st.integers(1, 10 ** 12), interval_seconds=st.integers(1, 10 ** 6))
def test_a_future_occurrence_is_unchanged(early_microseconds, interval_seconds):
    due = START + timedelta(days=30)
    assert next_occurrence(due, due - timedelta(microseconds=early_microseconds), interval_seconds) == due
