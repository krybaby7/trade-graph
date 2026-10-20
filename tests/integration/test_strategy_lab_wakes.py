"""The Trading desk asks the strategy lab, is woken with ``lab_results`` and plans on what it confirmed (W11-B, C11).

A scripted installation: the synthetic day-plan account (USD10,000, synthetic money limits), the real event engine,
the real lab queue and worker on a synthetic candle file (``synthetic_fixture``) and a scripted desk that reads the
``lab`` context the real Trader handler builds. No network, model, key, paid call or real order.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from tests.day_plan_support import plan
from tests.strategy_lab_support import (
    CONFIRM,
    STRATEGY,
    TUNE,
    candle_file,
    confirm,
    register,
    study,
    submit,
    tune,
    worker_for,
)
from tests.wake_support import engine_account, run_one, scripted, wake_input

from trade_graph.application.practice_range.store import PracticeRangeStore
from trade_graph.application.strategy_lab.context import LAB_CONTEXT_BYTES, lab_context
from trade_graph.application.strategy_lab.queue import LabQueue
from trade_graph.contracts.practice_range import ReplayWindow, StudyPlan

NAMED = "2026-10-16T14:00:00Z"


@pytest.fixture
def desk(tmp_path):
    account, scheduler, engine = engine_account(tmp_path, departments=("trader",), daily_cycle=False)
    market, candles = candle_file(tmp_path / "market_data.sqlite")
    yield account, scheduler, engine, candles
    market.close()


def _wake(account, engine, note: str = "Synthetic: look at the lab.") -> None:
    engine.request_wake(account.portfolio, "trader", note, requested_by="leader")


def _lab_answer(requests, *, check_in: str | None = NAMED) -> dict:
    answer = {"summary": "Ask the lab.", "lab_requests": [item.model_dump(mode="json") for item in requests]}
    if check_in is not None:
        answer["check_in"] = {"at_utc": check_in, "note": "after the lab"}
    return answer


def test_the_desk_registers_tunes_and_confirms_is_woken_with_the_results_and_its_next_plan_is_accepted(desk) -> None:
    account, scheduler, engine, candles = desk
    seen: list = []
    _wake(account, engine)
    engine.step([account.portfolio])
    first = _lab_answer([register(), study(), tune(window=(TUNE[0], TUNE[0].replace(day=5))), tune(), confirm()])
    run_one(scheduler, engine, {"trader": scripted(first, seen=seen)})
    (asked,) = engine.wakes(account.portfolio)
    assert asked.status == "completed" and [item.status for item in asked.outcome.lab] == ["queued"] * 5
    assert engine.step([account.portfolio]).created == 0  # nothing to wake for while the lab works

    worker = worker_for(account, candles)
    assert [step["status"] for step in worker.run_until_idle()] == ["ran"] * 5 + ["idle"]
    report = engine.step([account.portfolio])
    assert report.created == 1 and report.triggers == 1

    def desk_reads_the_lab(task: dict) -> dict:
        lab = lab_context(account.database, account.clock, task["portfolio_id"])
        confirmed = [item for item in lab["results"] if item["kind"] == "confirm" and item["status"] == "done"]
        ref = confirmed[0]["brief"]["strategy_ref"]
        assert len(confirmed[0]["reports"]) == 2 and confirmed[0]["reports"][0]["lines"]
        return {"summary": "The confirmation is in; rest one bid on its version.",
                "day_plans": [plan(strategy_ref=ref).model_dump(mode="json")]}

    run_one(scheduler, engine, {"trader": scripted(desk_reads_the_lab, seen=seen)})
    woken = engine.wake(wake_input(seen[-1])["wake_id"])
    assert woken.reasons == ("lab_results",) and woken.purpose == "event"
    (trigger,) = woken.triggers
    assert trigger.source_ref == f"lab-results:wake:{asked.wake_id}" and "5 requests" in trigger.note
    (applied,) = woken.outcome.plans
    assert applied.status == "installed", applied.reason
    assert account.database.execute("SELECT strategy_ref FROM day_plans").fetchone()[0] == "cross-hundred@1"
    # Like a news alarm, lab results keep the desk's own check-in when its answer names none (D291).
    assert engine.next_check_ins(account.portfolio)["trader"]["basis"] == "named"
    assert engine.step([account.portfolio]).created == 0  # one wake per finished batch, never again


def test_refusals_reach_the_desk_with_their_reasons_in_the_next_wake(desk) -> None:
    account, scheduler, engine, candles = desk
    _wake(account, engine)
    engine.step([account.portfolio])
    run_one(scheduler, engine, {"trader": scripted(_lab_answer([register(), study(), confirm()]))})
    worker_for(account, candles).run_until_idle()
    engine.step([account.portfolio])
    run_one(scheduler, engine, {"trader": scripted({"summary": "Seen."})})

    broken = register(version=2, sizing={"equity_fraction": "3"})
    later = (CONFIRM[1], CONFIRM[1].replace(day=20))
    asked = _lab_answer([
        broken,  # a malformed strategy: refused at once
        register(version=2),
        confirm("cross-hundred@2"),  # confirmation on spent days
        study("cross-touching", tune=CONFIRM, confirm=later),  # tuning on confirmation days
        tune(window=CONFIRM),  # a tuning run on confirmation days
    ])
    seen: list = []
    _wake(account, engine, "Synthetic: try a second version.")
    engine.step([account.portfolio])
    run_one(scheduler, engine, {"trader": scripted(asked)})
    worker_for(account, candles).run_until_idle()
    engine.step([account.portfolio])
    run_one(scheduler, engine, {"trader": scripted({"summary": "Read the refusals."}, seen=seen)})
    assert wake_input(seen[-1])["reasons"] == ["lab_results"]
    lab = lab_context(account.database, account.clock, account.portfolio)
    newest = {item["request_id"].rsplit(":", 1)[1]: item for item in lab["results"][:5]}
    assert newest["0"]["status"] == "refused" and "equity_fraction" in newest["0"]["reason"]
    assert newest["1"]["status"] == "done"
    assert newest["2"]["status"] == "refused" and "spent on cross-hundred@1" in newest["2"]["reason"]
    assert newest["3"]["status"] == "refused" and "never reused for tuning" in newest["3"]["reason"]
    assert newest["4"]["status"] == "refused" and "tuning stays inside" in newest["4"]["reason"]
    family = {item["strategy_id"]: item for item in lab["families"]}["cross-hundred"]
    assert family["versions"] == 2 and family["runs"] == {"tune": 0, "confirm": 2}
    assert family["confirmed_versions"] == ["cross-hundred@1"]
    offer = family["confirmation_offer"]  # walk-forward: the newest untouched days, up to today
    assert offer["start"] == CONFIRM[1].isoformat() and offer["end"] == "2026-10-16"


def test_a_malformed_strategy_alone_still_wakes_the_desk_with_the_reason(desk) -> None:
    account, scheduler, engine, _candles = desk
    _wake(account, engine)
    engine.step([account.portfolio])
    run_one(scheduler, engine, {"trader": scripted(_lab_answer([register(sizing={"equity_fraction": "9"})]))})
    (asked,) = engine.wakes(account.portfolio)
    assert asked.outcome.lab[0].status == "refused" and "equity_fraction" in asked.outcome.lab[0].reason
    assert engine.step([account.portfolio]).created == 1  # no worker needed: the batch ended at once


def test_only_the_desk_asks_the_lab(tmp_path) -> None:
    account, scheduler, engine = engine_account(tmp_path, departments=("research",), daily_cycle=False)
    engine.request_wake(account.portfolio, "research", "Synthetic.", requested_by="leader")
    engine.step([account.portfolio])
    run_one(scheduler, engine, {"research": scripted(_lab_answer([register()], check_in=None))})
    (wake,) = engine.wakes(account.portfolio)
    assert wake.outcome.lab == () and "Trading desk" in wake.outcome.refused
    assert account.database.execute("SELECT COUNT(*) FROM strategy_lab_requests").fetchone()[0] == 0


def test_the_lab_context_stays_inside_its_share_of_the_request_bound(desk) -> None:
    """Many long results: the newest are shown in full, older ones shorter, the rest counted (D116: none dropped)."""
    account, scheduler, engine, candles = desk
    requests = [register(), study()]
    for day in range(2, 8):
        requests.append(tune(window=(TUNE[0], TUNE[0].replace(day=day)), profiles=("all",)))
    requests.append(confirm(profiles=("all",)))
    for number in range(40):
        requests.append(register(version=100 + number, note="x" * 480))
    submit(account, requests)
    worker_for(account, candles).run_until_idle()
    lab = lab_context(account.database, account.clock, account.portfolio)
    size = len(json.dumps(lab, separators=(",", ":")).encode())
    assert size <= LAB_CONTEXT_BYTES
    shown = len(lab["results"])
    assert shown + lab["results_omitted"] == 49 and lab["results_omitted"] > 0
    assert lab["results_total"] == {"done": 49}
    assert lab["queue"] == {"queued": 0, "running": 0, "next": []}


def test_the_walk_forward_offer_moves_with_new_days(desk) -> None:
    account, _scheduler, _engine, _candles = desk
    store = PracticeRangeStore(account.database, account.clock)
    today = datetime(2026, 10, 16, tzinfo=UTC).date()
    assert store.confirmation_offer("cross-hundred", today=today).window is None  # no study: every day untouched
    store.register(STRATEGY)
    store.create_study(StudyPlan(study_id="cross-a", strategy_id="cross-hundred",
                                 tune=ReplayWindow(start=TUNE[0], end=TUNE[1]),
                                 confirm=ReplayWindow(start=CONFIRM[0], end=CONFIRM[1])))
    offer = store.confirmation_offer("cross-hundred", today=today)
    assert offer.window == ReplayWindow(start=CONFIRM[1], end=today)
    assert offer.studies[0]["confirmed_by"] is None  # set aside, not spent yet
    tomorrow = today.replace(day=17)
    assert store.confirmation_offer("cross-hundred", today=tomorrow).window.end == tomorrow
    assert store.confirmation_offer("cross-hundred", today=CONFIRM[1]).window is None  # nothing untouched yet
    assert LabQueue(account.database, account.clock).counts() == {"queued": 0, "running": 0}
