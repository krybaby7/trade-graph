"""Synthetic wake records for the HQ wake log tests (T39), written through the event engine's own store.

``Wakes`` records triggers, wakes, check-ins and focus selections with ``application/events/store.py`` (state
migration 0035), as the engine would, on the day-plan paper account (``tests/day_plan_support.py``: USD10,000, synthetic
money limits). Nothing here runs the engine, a department, a model or an order; the scripted-day test does that with
the real engine. Every value is synthetic.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from trade_graph.application.events.store import WakeStore, new_id
from trade_graph.contracts.wakes import (
    AllowanceReading,
    FocusRecord,
    FocusSelection,
    ScanReference,
    WakeContext,
    WakeOutcomeRecord,
    purpose_of,
    reasons_of,
)
from trade_graph.kernel.money_limits import trading_day

INSTRUCTION = "Synthetic HQ wake log test: everything here is data."

# The fields each trigger kind needs (contracts/wakes.py), with synthetic values; a test overrides what it checks.
TRIGGER_DEFAULTS: dict[str, dict[str, Any]] = {
    "day_start": {},
    "focus_step": {},
    "day_plan_step": {},
    "day_end_review": {},
    "check_in": {"check_in_id": "check-in-synthetic"},
    "alarm_fired": {"delivery_id": "delivery-synthetic", "alarm_id": "alarm-synthetic"},
    "feed_lost": {"delivery_id": "delivery-feed-lost"},
    "feed_restored": {"delivery_id": "delivery-feed-back"},
    "entry_filled": {"fill_id": "fill-entry", "intent_id": "intent-entry", "plan_id": "plan-1", "entry_id": "entry-1",
                     "symbol": "SOL/USD", "quantity": "5", "price": "99.5", "side": "buy"},
    "exit_filled": {"fill_id": "fill-exit", "intent_id": "intent-exit", "plan_id": "plan-1", "entry_id": "entry-1",
                    "symbol": "SOL/USD", "quantity": "5", "price": "104", "side": "sell", "exit_leg": "take_profit"},
    "order_filled": {"fill_id": "fill-order", "intent_id": "intent-order", "symbol": "BTC/USD", "quantity": "0.01",
                     "price": "50000", "side": "buy"},
    "loss_limit_reached": {"event_id": "event-loss-limit"},
    "focus_updated": {"focus_id": "focus-synthetic"},
    "wake_request": {"requested_by": "leader", "note": "Synthetic: Kraken posted SOL maintenance at 14:00."},
    "news_alarm": {"requested_by": "software", "note": "Synthetic headline: an exchange outage.", "symbol": "SOL/USD"},
    "task_created": {"task_id": "task-outside", "objective": "Incorporate completed departmental consultation"},
}
DAY_KINDS = {"day_start", "focus_step", "day_plan_step", "day_end_review", "loss_limit_reached", "focus_updated"}


def hq_runtime(account) -> SimpleNamespace:
    """What HQ's wake log reads: the state database, the clock and the portfolio (like the dashboard runtime)."""
    return SimpleNamespace(database=account.database, clock=account.clock, portfolio_id=account.portfolio)


def allowance(now: datetime, status: str = "unknown", *, percent: str | None = None,
              observed: datetime | None = None) -> AllowanceReading:
    notes = {"unknown": "No allowance reading has been recorded; unknown is not plenty.",
             "available": "The newest recorded allowance reading.",
             "paused": "New AI work waits (allowance floor or provider pause)."}
    return AllowanceReading(read_at=now, status=status, ai_paused=status == "paused" if status != "unknown" else None,
                            lowest_remaining_percent=Decimal(percent) if percent is not None else None,
                            observed_at=observed, note=notes[status])


def focus_document(symbols: list[str], *, summary: str = "Synthetic focus.") -> FocusSelection:
    return FocusSelection(coins=tuple({"symbol": symbol, "note": f"Synthetic: {symbol} is liquid and moving.",
                                       "evidence_refs": ["scan-synthetic-1"]} for symbol in symbols),
                          summary=summary)


def canonical_sha256(document: Any) -> str:
    """The focus record's check value, computed the way the store computes it (``store.sha256_of``)."""
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


class Wakes:
    """Write wake records for one account the way the event engine does, one wake at a time."""

    def __init__(self, account) -> None:
        self.account = account
        self.store = WakeStore(account.database, account.clock)
        self.sequence = 0

    def now(self) -> datetime:
        return self.account.clock.now()

    def day(self):
        return trading_day(self.now(), "00:00")

    def trigger(self, kind: str, department: str = "trader", **fields: Any) -> dict[str, Any]:
        self.sequence += 1
        values = {**TRIGGER_DEFAULTS[kind], **fields}
        if kind in DAY_KINDS:
            values.setdefault("trading_day_start", self.day().start)
        return {"kind": kind, "department": department, "portfolio_id": self.account.portfolio,
                "source_ref": values.pop("source_ref", f"synthetic:{kind}:{self.sequence}"),
                "occurred_at": values.pop("occurred_at", self.now()), **values}

    def check_in(self, department: str, due_at: datetime | None, *, note: str | None = None,
                 wake_id: str | None = None):
        day = self.day()
        return self.store.record_check_in(
            portfolio_id=self.account.portfolio, department=department, due_at=due_at or day.end,
            basis="named" if due_at is not None else "day_start", note=note, wake_id=wake_id, task_id=None)

    def focus(self, symbols: list[str], *, wake_id: str | None = None, scan_id: str | None = "scan-synthetic-1",
              summary: str = "Synthetic focus.") -> FocusRecord:
        day = self.day()
        return self.store.record_focus(portfolio_id=self.account.portfolio, day_start=day.start, day_end=day.end,
                                       scan_id=scan_id, selection=focus_document(symbols, summary=summary),
                                       wake_id=wake_id, task_id=None)

    def step(self, step: str, status: str = "done", detail: dict[str, Any] | None = None) -> None:
        day = self.day()
        if status == "started":
            self.store.begin_step(self.account.portfolio, day.start, day.end, step, detail=detail)
        elif not self.store.begin_step(self.account.portfolio, day.start, day.end, step, status=status,
                                       detail=detail):
            self.store.finish_step(self.account.portfolio, day.start, step, status, detail)

    def wake(self, department: str, triggers: list[dict[str, Any]], *, status: str = "completed",
             outcome: WakeOutcomeRecord | dict | None = None, reading: AllowanceReading | None = None,
             origin: str = "engine", task_id: str | None = None, close_after: float = 120,
             alarms: tuple = (), scan: ScanReference | None = None, focus: FocusRecord | None = None,
             reason: str | None = None) -> str:
        """One wake holding ``triggers`` (dicts from ``trigger``), closed as ``status`` after ``close_after`` s."""
        stored = [self.store.add_trigger(**item)[0].trigger for item in triggers]
        now = self.now()
        day = self.day()
        wake_id = new_id()
        context = WakeContext(
            wake_id=wake_id, portfolio_id=self.account.portfolio, department=department,
            purpose="task" if origin == "task" else purpose_of(stored), trading_day_start=day.start,
            trading_day_end=day.end, created_at=now, reasons=reasons_of(stored), triggers=tuple(stored),
            alarms=alarms, scan=scan, focus=focus, allowance=reading or allowance(now), instruction=INSTRUCTION)
        self.store.insert_wake(context=context, origin=origin, task_id=task_id or f"task-{wake_id}")
        self.store.claim(wake_id, [trigger.trigger_id for trigger in stored])
        if status == "queued":
            return wake_id
        self.account.clock.advance(close_after)
        record = outcome if isinstance(outcome, WakeOutcomeRecord) or outcome is None else \
            WakeOutcomeRecord.model_validate(outcome)
        if status == "applying":
            self.store.set_applying(wake_id, record or WakeOutcomeRecord(received=True))
        elif status == "completed":
            self.store.deliver(wake_id)
            self.store.close_wake(wake_id, "completed", outcome=record or WakeOutcomeRecord(received=True),
                                  reason=None)
        else:
            self.store.release(wake_id, expire_kinds=("day_start", "focus_step", "day_plan_step"))
            self.store.close_wake(wake_id, status, outcome=None, reason=reason or f"synthetic {status} turn")
        return wake_id


def dump_sha256(database) -> str:
    """Every row of every table of the state database, hashed: equal before and after means nothing was written."""
    connection = database.execute("SELECT 1").connection
    return hashlib.sha256("\n".join(connection.iterdump()).encode("utf-8")).hexdigest()


__all__ = ["INSTRUCTION", "TRIGGER_DEFAULTS", "Wakes", "allowance", "canonical_sha256", "dump_sha256",
           "focus_document", "hq_runtime"]
