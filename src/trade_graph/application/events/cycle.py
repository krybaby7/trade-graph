"""The daily cycle (D115, D123, D138 item 6): day start and day end at the owner's trading-day start.

Day end: the trading day that ended gets its Learning review (``day_end_review``), late if the service was down, but
only for a day whose start ran; steps of that day still unfinished are recorded as failed ("the day ended first").
Day start, once per portfolio and trading day, in order:

1. ``day_start``: recorded, with the departments whose next check-in is the day start (none named, D123) woken with
   a ``day_start`` trigger: the Leader, Optimisation and Learning. Research and the Trader take part through steps 3
   and 4, which are their day-start check-ins.
2. ``scan``: the coin scanner's shortlist: one live scan run by the configured step, or the newest recorded scan.
3. ``focus``: Research is woken to name the day's focus coins from that shortlist (``focus_step``).
4. ``day_plan``: once the focus step has finished, whatever its result, the Trader is woken to write its day plans
   (``day_plan_step``). Without Research the plans follow the scan; without the Trader the step is skipped.

Each step is recorded once (SQL keeps one row per portfolio, day and step), so a restart resumes where it stopped and
never runs a step twice. A missed day is not replayed: after downtime only today's start runs.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING

from trade_graph.application.events.scan import latest_recorded
from trade_graph.contracts.wakes import ScanReference
from trade_graph.domain.clock import utc_iso
from trade_graph.kernel.money_limits import TradingDay

if TYPE_CHECKING:
    from trade_graph.application.events.engine import EventEngine

# Departments whose day-start check-in is the ``day_start`` trigger itself (Research and the Trader have steps).
CHECK_IN_AT_DAY_START = ("leader", "learning", "optimisation")


class DayCycle:
    def __init__(self, engine: EventEngine, *, scan=None) -> None:
        self.engine = engine
        self.store = engine.store
        self.scan = scan

    def run(self, portfolios: Sequence[str]) -> int:
        added = 0
        for portfolio_id in portfolios:
            added += self._day_end(portfolio_id)
            added += self._day_start(portfolio_id)
        return added

    def _day_end(self, portfolio_id: str) -> int:
        now = self.engine.clock.now()
        for day_start, step in self.store.unfinished_steps(portfolio_id, ended_before=now):
            self.store.finish_step(portfolio_id, day_start, step, "failed",  # type: ignore[arg-type]
                                   {"reason": "the trading day ended before this step finished"})
        added = 0
        for start, end in self.store.started_days(portfolio_id):
            if end > now or self.store.step(portfolio_id, start, "day_end_review") is not None:
                continue
            if "learning" not in self.engine.departments:
                self.store.begin_step(portfolio_id, start, end, "day_end_review", status="skipped",
                                      detail={"reason": "no Learning department is running"})
                continue
            with self.engine.database.immediate():
                if self.store.begin_step(portfolio_id, start, end, "day_end_review"):
                    _stored, new = self.store.add_trigger(
                        kind="day_end_review", department="learning", portfolio_id=portfolio_id,
                        source_ref=f"day-end:{portfolio_id}:{utc_iso(start)}", occurred_at=end,
                        trading_day_start=start)
                    added += int(new)
        return added

    def _day_start(self, portfolio_id: str) -> int:
        now = self.engine.clock.now()
        day = self.engine.trading_day(now)
        added = 0
        if self.store.step(portfolio_id, day.start, "day_start") is None:
            with self.engine.database.immediate():
                if self.store.begin_step(portfolio_id, day.start, day.end, "day_start", status="done", detail={
                        "started_at": utc_iso(now), "late_seconds": int((now - day.start).total_seconds())}):
                    added += self._check_ins_at_day_start(portfolio_id, day, now)
        if self.store.step(portfolio_id, day.start, "scan") is None:
            reference = self._scan(portfolio_id, now)
            self.store.begin_step(portfolio_id, day.start, day.end, "scan",
                                  status="done" if reference.status in {"ok", "no_candidates"} else "failed",
                                  detail={"scan": reference.model_dump(mode="json")})
        if self.store.step(portfolio_id, day.start, "focus") is None:
            added += self._step_trigger(portfolio_id, day, "focus", "research", "focus_step")
        focus = self.store.step(portfolio_id, day.start, "focus")
        if (focus is not None and focus["status"] != "started"
                and self.store.step(portfolio_id, day.start, "day_plan") is None):
            added += self._step_trigger(portfolio_id, day, "day_plan", "trader", "day_plan_step")
        return added

    def _check_ins_at_day_start(self, portfolio_id: str, day: TradingDay, now: datetime) -> int:
        added = 0
        for department in CHECK_IN_AT_DAY_START:
            if department not in self.engine.departments:
                continue
            latest = self.store.latest_check_in(portfolio_id, department)
            if latest is not None and latest.basis == "named" and latest.due_at >= day.start:
                continue  # its own time today or later: that check-in wakes it, not the day start
            _stored, new = self.store.add_trigger(
                kind="day_start", department=department, portfolio_id=portfolio_id,
                source_ref=f"day-start:{portfolio_id}:{department}:{utc_iso(day.start)}", occurred_at=day.start,
                trading_day_start=day.start)
            added += int(new)
        return added

    def _step_trigger(self, portfolio_id: str, day: TradingDay, step: str, department: str, kind: str) -> int:
        scan = self.engine.day_scan(portfolio_id, day.start)
        focus = self.store.focus(portfolio_id, day.start)
        if department not in self.engine.departments:
            self.store.begin_step(portfolio_id, day.start, day.end, step, status="skipped",  # type: ignore[arg-type]
                                  detail={"reason": f"no {department.capitalize()} department is running"})
            return 0
        with self.engine.database.immediate():
            if not self.store.begin_step(portfolio_id, day.start, day.end, step):  # type: ignore[arg-type]
                return 0
            _stored, new = self.store.add_trigger(
                kind=kind, department=department, portfolio_id=portfolio_id,
                source_ref=f"{step.replace('_', '-')}:{portfolio_id}:{utc_iso(day.start)}", occurred_at=day.start,
                trading_day_start=day.start, scan_id=scan.scan_id if scan else None,
                focus_id=focus.focus_id if focus is not None and kind == "day_plan_step" else None)
            return int(new)

    def _scan(self, portfolio_id: str, now: datetime) -> ScanReference:
        if self.scan is None:
            return latest_recorded(self.engine.scan_store, now)
        try:
            return self.scan(portfolio_id=portfolio_id, as_of=now)
        except Exception as error:  # noqa: BLE001 - a failed scan is recorded; the day goes on without a shortlist
            return ScanReference(status="failed", source="day_start_scan",
                                 error=f"{type(error).__name__}: {error}"[:500])


__all__ = ["CHECK_IN_AT_DAY_START", "DayCycle"]
