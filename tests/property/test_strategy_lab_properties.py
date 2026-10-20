"""Properties of the walk-forward confirmation offer (W11-B, D292): whatever studies a family made by the rules, the
newest untouched window the lab offers touches none of their days, ends at today, and the protected store accepts a
new study that confirms on it. Synthetic studies only; no network, model or order.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.practice_range.store import PracticeRangeStore
from trade_graph.contracts.practice_range import ReplayWindow, StudyPlan
from trade_graph.domain.clock import FrozenClock

START = date(2026, 1, 1)
FAMILY = "walk-forward"
spans = st.lists(st.tuples(st.integers(0, 20), st.integers(1, 30), st.integers(1, 30)), min_size=0, max_size=5)


@settings(max_examples=25, deadline=None)
@given(spans=spans, ahead=st.integers(-10, 60))
def test_the_offer_is_untouched_ends_today_and_the_store_accepts_a_confirmation_on_it(spans, ahead) -> None:
    with tempfile.TemporaryDirectory() as folder:
        database = Database(Path(folder) / "state.sqlite")
        try:
            _check(database, spans, ahead)
        finally:
            database.close()


def _check(database, spans, ahead) -> None:
    store = PracticeRangeStore(database, FrozenClock(datetime(2026, 12, 31, tzinfo=UTC)))
    cursor, plans = START, []
    for number, (gap, tune_days, confirm_days) in enumerate(spans):
        tune = ReplayWindow(start=cursor + timedelta(days=gap), end=cursor + timedelta(days=gap + tune_days))
        confirm = ReplayWindow(start=tune.end, end=tune.end + timedelta(days=confirm_days))
        plan = StudyPlan(study_id=f"study-{number}", strategy_id=FAMILY, tune=tune, confirm=confirm)
        store.create_study(plan)
        plans.append(plan)
        cursor = confirm.end
    today = cursor + timedelta(days=ahead)
    offer = store.confirmation_offer(FAMILY, today=today)
    if not plans:
        assert offer.window is None and offer.untouched_from is None
        return
    assert offer.untouched_from == cursor and offer.until == today
    if ahead <= 0:
        assert offer.window is None
        return
    assert offer.window == ReplayWindow(start=cursor, end=today)
    assert not any(offer.window.overlaps(plan.tune) or offer.window.overlaps(plan.confirm) for plan in plans)
    accepted, created = store.create_study(StudyPlan(study_id="study-next", strategy_id=FAMILY,
                                                     tune=plans[0].tune, confirm=offer.window))
    assert created and accepted.confirm == offer.window
