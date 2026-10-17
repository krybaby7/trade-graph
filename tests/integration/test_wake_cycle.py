"""The daily cycle (T35, D115, D123, D138 item 6): the day start runs the scan, Research's focus step and the
Trader's day plans; the day end runs the Learning review. Synthetic account, scripted scan and departments; no
network, model, key or real order.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.day_plan_support import day_plan_account, entry
from tests.wake_support import engine_for, plan, run_one, scripted, wake_input

from trade_graph.application.day_plans import DayPlans
from trade_graph.contracts.wakes import ScanReference, ShortlistCoin

DAY = datetime(2026, 10, 16, 0, 0, tzinfo=UTC)
NEXT = DAY + timedelta(days=1)
JUST_AFTER = DAY + timedelta(seconds=30)


class ScriptedScan:
    """A day-start scan step that returns a fixed shortlist (labelled synthetic) and records what it was asked."""

    def __init__(self, *symbols: str, fail: bool = False) -> None:
        self.symbols, self.fail, self.calls = symbols, fail, []

    def __call__(self, *, portfolio_id: str, as_of: datetime) -> ScanReference:
        self.calls.append((portfolio_id, as_of))
        if self.fail:
            raise ConnectionError("synthetic: the ticker could not be reached")
        return ScanReference(status="ok", source="day_start_scan", scan_id=f"scan-{len(self.calls)}", as_of=as_of,
                             evidence="synthetic", shortlist=tuple(ShortlistCoin(symbol=symbol, position=index + 1)
                                                                   for index, symbol in enumerate(self.symbols)))


def _focus(*symbols: str) -> dict:
    return {"focus": {"coins": [{"symbol": symbol, "note": f"Synthetic reason for {symbol}."} for symbol in symbols],
                      "summary": "Synthetic focus."}}


def _setup(tmp_path, *, departments=("trader", "research", "learning", "leader"), scan=None, now=JUST_AFTER):
    account = day_plan_account(tmp_path, now=now)
    scheduler, engine = engine_for(account, departments=departments, scan=scan or ScriptedScan("SOL/USD", "BTC/USD"))
    return account, scheduler, engine


def test_the_day_start_runs_scan_focus_and_plans_in_order_and_the_day_end_runs_the_review(tmp_path) -> None:
    scan = ScriptedScan("SOL/USD", "BTC/USD")
    account, scheduler, engine = _setup(tmp_path, scan=scan)
    pid = account.portfolio
    engine.step([pid])
    assert scan.calls == [(pid, JUST_AFTER)]
    steps = engine.cycle(pid, DAY)
    assert {name: item["status"] for name, item in steps.items()} == {
        "day_start": "done", "scan": "done", "focus": "started"}
    assert steps["day_start"]["detail"]["late_seconds"] == 30
    by_department = {wake.department: wake for wake in engine.wakes(pid)}
    assert set(by_department) == {"research", "leader", "learning"}  # the Trader waits for the focus coins
    assert by_department["research"].purpose == "focus"
    assert by_department["leader"].reasons == by_department["learning"].reasons == ("day_start",)
    seen: list = []
    run_one(scheduler, engine, {"research": scripted(_focus("SOL/USD"), seen=seen)})
    handed = wake_input(seen[0])
    assert [coin["symbol"] for coin in handed["scan"]["shortlist"]] == ["SOL/USD", "BTC/USD"]
    focus = engine.focus(pid)
    assert focus.selection.symbols() == ("SOL/USD",) and focus.scan_id == "scan-1" and focus.revision == 1
    assert engine.cycle(pid, DAY)["focus"]["status"] == "done"
    engine.step([pid])
    (trader,) = engine.wakes(pid, department="trader")
    assert trader.purpose == "day_plan" and trader.reasons == ("day_plan_step",)
    seen.clear()
    run_one(scheduler, engine, {"trader": scripted({"day_plans": [plan().model_dump(mode="json")],
                                                    "summary": "One SOL plan."}, seen=seen)})
    assert wake_input(seen[0])["focus"]["selection"]["coins"][0]["symbol"] == "SOL/USD"
    record = engine.wake(trader.wake_id)
    (applied,) = record.outcome.plans
    assert (applied.symbol, applied.status, applied.request_id) == ("SOL/USD", "installed",
                                                                   f"wake:{trader.wake_id}:SOL/USD")
    (active,) = DayPlans(account.execution).active(pid)
    assert active.plan_id == applied.plan_id and active.task_id == trader.task_id
    assert engine.cycle(pid, DAY)["day_plan"]["status"] == "done"
    # The next day: the review of the day that ended, then the next day start.
    account.clock.advance(timedelta(days=1).total_seconds())
    account.quote("SOL/USD", "99.9", "100.1")
    engine.step([pid])
    review = next(wake for wake in engine.wakes(pid, department="learning") if wake.purpose == "day_end_review")
    assert review.trading_day_start == DAY and set(review.reasons) == {"day_end_review", "day_start"}
    # Yesterday's day-start wakes of the Leader and Learning never ran: they were retired, not left blocking.
    assert {wake.status for wake in engine.wakes(pid) if wake.trading_day_start == DAY
            and wake.reasons == ("day_start",)} == {"obsolete"}
    assert engine.cycle(pid, DAY)["day_end_review"]["status"] == "started"
    assert engine.cycle(pid, NEXT)["focus"]["status"] == "started"
    seen.clear()
    run_one(scheduler, engine, {"learning": scripted({"summary": "Synthetic review."}, seen=seen)})
    assert wake_input(seen[0])["plans"][0]["plan_id"] == applied.plan_id  # the reviewed day's plans
    assert engine.cycle(pid, DAY)["day_end_review"]["status"] == "done"


def test_focus_coins_come_from_the_shortlist_and_the_trader_plans_either_way(tmp_path) -> None:
    account, scheduler, engine = _setup(tmp_path)
    engine.step([account.portfolio])
    run_one(scheduler, engine, {"research": scripted(_focus("ADA/USD"))})
    (research,) = engine.wakes(account.portfolio, department="research")
    assert "not on the scanner's shortlist" in research.outcome.focus_refused
    assert engine.focus(account.portfolio) is None
    assert engine.cycle(account.portfolio, DAY)["focus"]["status"] == "failed"
    engine.step([account.portfolio])
    (trader,) = engine.wakes(account.portfolio, department="trader")
    assert trader.purpose == "day_plan"
    seen: list = []
    run_one(scheduler, engine, {"trader": scripted(seen=seen)})
    assert wake_input(seen[0])["focus"] is None and wake_input(seen[0])["scan"]["scan_id"] == "scan-1"


def test_a_failed_scan_is_recorded_and_the_day_goes_on_without_a_shortlist(tmp_path) -> None:
    account, scheduler, engine = _setup(tmp_path, scan=ScriptedScan(fail=True))
    report = engine.step([account.portfolio])
    assert report.failures == ()
    scan = engine.cycle(account.portfolio, DAY)["scan"]
    assert scan["status"] == "failed" and "ConnectionError" in scan["detail"]["scan"]["error"]
    seen: list = []
    run_one(scheduler, engine, {"research": scripted(_focus("SOL/USD"), seen=seen)})
    assert wake_input(seen[0])["scan"]["status"] == "failed"
    # T37 (D206): focus coins come only from a shortlist, so without one the answer is refused and recorded; the
    # Trader is still woken for its day plans and sees why there is no focus.
    (research,) = engine.wakes(account.portfolio, department="research")
    assert "no shortlist" in research.outcome.focus_refused
    assert engine.focus(account.portfolio) is None
    assert engine.cycle(account.portfolio, DAY)["focus"]["status"] == "failed"
    engine.step([account.portfolio])
    trader: list = []
    run_one(scheduler, engine, {"trader": scripted(seen=trader)})
    assert wake_input(trader[0])["purpose"] == "day_plan" and wake_input(trader[0])["focus"] is None
    assert wake_input(trader[0])["focus_step"]["status"] == "failed"


def test_without_research_the_trader_plans_from_the_scan_and_without_a_trader_the_step_is_skipped(tmp_path) -> None:
    account, scheduler, engine = _setup(tmp_path, departments=("trader",))
    engine.step([account.portfolio])
    steps = engine.cycle(account.portfolio, DAY)
    assert steps["focus"]["status"] == "skipped" and steps["day_plan"]["status"] == "started"
    (trader,) = engine.wakes(account.portfolio)
    assert trader.purpose == "day_plan"
    (tmp_path / "other").mkdir()
    other = day_plan_account(tmp_path / "other", now=JUST_AFTER)
    _scheduler, engine = engine_for(other, departments=("research",), scan=ScriptedScan("SOL/USD"))
    engine.step([other.portfolio])
    run_one(_scheduler, engine, {"research": scripted(_focus("SOL/USD"))})
    engine.step([other.portfolio])
    assert engine.cycle(other.portfolio, DAY)["day_plan"]["status"] == "skipped"


def test_a_refused_day_plan_is_recorded_and_installs_nothing(tmp_path) -> None:
    account, scheduler, engine = _setup(tmp_path, departments=("trader",))
    engine.step([account.portfolio])
    above = plan(entries=[entry("5", "101", "97")])  # a limit above the ask would not rest
    run_one(scheduler, engine, {"trader": scripted({"day_plans": [above.model_dump(mode="json")]})})
    (wake,) = engine.wakes(account.portfolio)
    (applied,) = wake.outcome.plans
    assert applied.status == "refused" and applied.error_code == "validation"
    assert "rests below the market" in applied.reason
    assert account.database.execute("SELECT COUNT(*) FROM day_plans").fetchone()[0] == 0
    assert account.database.execute(
        "SELECT COUNT(*) FROM activity_events WHERE kind = 'day_plan_refused'").fetchone()[0] == 1
    assert engine.cycle(account.portfolio, DAY)["day_plan"]["status"] == "done"


def test_after_downtime_only_today_starts_and_the_missed_review_runs_late(tmp_path) -> None:
    account, scheduler, engine = _setup(tmp_path)
    engine.step([account.portfolio])  # day D starts; Research never answers before the service stops
    account.clock.advance(timedelta(days=2, hours=10).total_seconds())
    account.quote("SOL/USD", "99.9", "100.1")
    _scheduler, engine = engine_for(account, departments=("trader", "research", "learning", "leader"),
                                    scan=ScriptedScan("SOL/USD"))
    engine.step([account.portfolio])
    assert engine.cycle(account.portfolio, DAY)["focus"]["status"] == "failed"  # the day ended first
    assert engine.cycle(account.portfolio, DAY)["day_end_review"]["status"] == "started"
    assert engine.cycle(account.portfolio, NEXT) == {}  # a missed day is not replayed
    today = NEXT + timedelta(days=1)
    assert engine.cycle(account.portfolio, today)["day_start"]["detail"]["late_seconds"] == 10 * 3600 + 30
    # The old focus wake never runs: it is obsolete, and its day step expires instead of being delivered.
    run_one(_scheduler, engine, {"research": scripted(_focus("SOL/USD"))})
    old = next(wake for wake in engine.wakes(account.portfolio, department="research")
               if wake.trading_day_start == DAY)
    assert old.status == "obsolete"


def test_research_refreshing_the_focus_during_the_day_wakes_the_trader(tmp_path) -> None:
    account, scheduler, engine = _setup(tmp_path, departments=("trader", "research"))
    engine.step([account.portfolio])
    run_one(scheduler, engine, {"research": scripted(_focus("SOL/USD"))})
    engine.step([account.portfolio])
    run_one(scheduler, engine, {"trader": scripted()})
    engine.request_wake(account.portfolio, "research", "Look at BTC again.", requested_by="leader")
    engine.step([account.portfolio])
    run_one(scheduler, engine, {"research": scripted(_focus("SOL/USD", "BTC/USD"))})
    assert engine.focus(account.portfolio).revision == 2
    engine.step([account.portfolio])
    latest = engine.wakes(account.portfolio, department="trader")[-1]
    assert latest.reasons == ("focus_updated",) and latest.status == "queued"
    seen: list = []
    run_one(scheduler, engine, {"trader": scripted(seen=seen)})
    assert wake_input(seen[0])["focus"]["revision"] == 2
