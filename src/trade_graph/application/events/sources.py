"""Where triggers come from: the alarm queue, fills, the daily loss stop, due check-ins and outside tasks (T35).

Each source is read from durable records with a cursor or a unique source reference, inside the same transaction
that records its triggers, so a restart or a repeated read never records an event twice and never skips one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from trade_graph.contracts.alarms import SYMBOL_PATTERN, AlarmDelivery
from trade_graph.contracts.models import FillRecord
from trade_graph.contracts.wakes import DEPARTMENTS, RIDE_ALONG_KINDS, WakeContext, reasons_of
from trade_graph.domain.clock import parse_utc

if TYPE_CHECKING:
    from trade_graph.application.events.engine import EventEngine
    from trade_graph.application.events.store import StoredTrigger

_SYMBOL = re.compile(SYMBOL_PATTERN)
_STOP_REASONS = {"stop", "stop_gap", "stop_both_hit", "stop_entry_bar"}
_BATCH = 500  # rows read per source and step; the rest is read at the next step (a bound on one read, not a cap)


def _symbol(value: Any) -> str | None:
    return value if isinstance(value, str) and _SYMBOL.fullmatch(value) else None


class TriggerSources:
    def __init__(self, engine: EventEngine) -> None:
        self.engine = engine
        self.database = engine.database
        self.store = engine.store

    def collect(self, portfolios: Sequence[str]) -> int:
        added = 0
        added += self._alarms(portfolios)
        added += self._fills(portfolios)
        added += self._loss_stops(portfolios)
        added += self._check_ins(portfolios)
        added += self._tasks(portfolios)
        return added

    # --- the alarm delivery queue (T33) -------------------------------------------------------------------------

    def mirror(self, item: AlarmDelivery, portfolio_id: str) -> StoredTrigger:
        """The trigger of one queued alarm item (created once per delivery)."""
        document = item.document
        symbol = _symbol((document.get("condition") or {}).get("symbol")) or _symbol(document.get("symbol"))
        stored, _ = self.store.add_trigger(
            kind=item.kind, department=item.department, portfolio_id=portfolio_id,
            source_ref=f"alarm-delivery:{item.delivery_id}", occurred_at=parse_utc(item.created_at),
            delivery_id=item.delivery_id, alarm_id=item.alarm_id, symbol=symbol,
            plan_id=document.get("day_plan_ref") if isinstance(document.get("day_plan_ref"), str) else None,
            note=document.get("note") if isinstance(document.get("note"), str) and document.get("note") else None,
            carried=item.kind in RIDE_ALONG_KINDS)
        return stored

    def _alarm_portfolio(self, item: AlarmDelivery, portfolios: Sequence[str]) -> str:
        return item.portfolio_id if item.portfolio_id in portfolios else portfolios[0]

    def _alarms(self, portfolios: Sequence[str]) -> int:
        added = 0
        for department in DEPARTMENTS:
            for item in self.engine.queue.pending(department):
                if self.store.trigger_by_source(f"alarm-delivery:{item.delivery_id}") is None:
                    self.mirror(item, self._alarm_portfolio(item, portfolios))
                    added += 1
        return added

    # --- fills of day-plan entries, protective exits and other orders (T34) -------------------------------------

    def _fills(self, portfolios: Sequence[str]) -> int:
        added = 0
        with self.database.immediate() as connection:
            position = self.store.cursor("fills")
            rows = connection.execute(
                "SELECT rowid AS seq, fill_id, portfolio_id, intent_id, document_json FROM fills WHERE rowid > ? "
                "ORDER BY rowid LIMIT ?", (position, _BATCH)).fetchall()
            for row in rows:
                if row["portfolio_id"] in portfolios and row["intent_id"] is not None:
                    added += self._fill(connection, row)
            if rows:
                self.store.advance("fills", rows[-1]["seq"])
        return added

    def _fill(self, connection, row) -> int:
        fill = FillRecord.model_validate_json(row["document_json"])
        common: dict[str, Any] = {
            "department": "trader", "portfolio_id": row["portfolio_id"], "source_ref": f"fill:{row['fill_id']}",
            "occurred_at": fill.filled_at_utc, "fill_id": row["fill_id"], "intent_id": row["intent_id"],
            "symbol": _symbol(fill.symbol), "side": fill.side, "quantity": fill.quantity, "price": fill.price}
        exit_row = connection.execute(
            """SELECT x.exit_id, x.entry_id, e.plan_id FROM day_plan_exits x JOIN day_plan_entries e
               ON e.entry_id = x.entry_id WHERE x.intent_id = ?""", (row["intent_id"],)).fetchone()
        if exit_row is not None:
            _stored, new = self.store.add_trigger(kind="exit_filled", plan_id=exit_row["plan_id"],
                                                  entry_id=exit_row["entry_id"],
                                                  exit_leg=self._exit_leg(row["intent_id"], fill.trade_id), **common)
            return int(new)
        entry_row = connection.execute("SELECT entry_id, plan_id FROM day_plan_entries WHERE intent_id = ?",
                                       (row["intent_id"],)).fetchone()
        if entry_row is not None:
            _stored, new = self.store.add_trigger(kind="entry_filled", plan_id=entry_row["plan_id"],
                                                  entry_id=entry_row["entry_id"], **common)
            return int(new)
        _stored, new = self.store.add_trigger(kind="order_filled", **common)
        return int(new)

    def _exit_leg(self, intent_id: str, trade_id: str) -> str:
        """Which leg of a protective exit filled: the paper broker records each fill's terms (T34)."""
        row = self.database.execute(
            "SELECT b.document_json FROM broker_orders b JOIN order_intents i ON i.client_order_id = b.client_order_id "
            "WHERE i.intent_id = ?", (intent_id,)).fetchone()
        if row is None:
            return "unknown"
        for terms in json.loads(row["document_json"]).get("fill_terms", []):
            if terms.get("trade_id") == trade_id:
                reason = terms.get("reason")
                return "take_profit" if reason == "take_profit" else "stop" if reason in _STOP_REASONS else "unknown"
        return "unknown"

    # --- the daily loss stop (T32) ------------------------------------------------------------------------------

    def _loss_stops(self, portfolios: Sequence[str]) -> int:
        added = 0
        with self.database.immediate() as connection:
            position = self.store.cursor("activity")
            newest = connection.execute("SELECT COALESCE(MAX(rowid), 0) FROM activity_events").fetchone()[0]
            if newest <= position:
                return 0
            rows = connection.execute(
                """SELECT rowid AS seq, event_id, portfolio_id, created_at FROM activity_events WHERE rowid > ?
                   AND rowid <= ? AND kind = 'daily_loss_limit_reached' ORDER BY rowid""",
                (position, newest)).fetchall()
            for row in rows:
                if row["portfolio_id"] in portfolios:
                    _stored, new = self.store.add_trigger(
                        kind="loss_limit_reached", department="trader", portfolio_id=row["portfolio_id"],
                        source_ref=f"activity:{row['event_id']}", occurred_at=parse_utc(row["created_at"]),
                        event_id=row["event_id"])
                    added += int(new)
            self.store.advance("activity", newest)
        return added

    # --- due check-ins (D123) -----------------------------------------------------------------------------------

    def _check_ins(self, portfolios: Sequence[str]) -> int:
        added = 0
        now = self.engine.clock.now()
        for portfolio_id in portfolios:
            for department in sorted(self.engine.departments):
                latest = self.store.latest_check_in(portfolio_id, department)
                if latest is None or latest.basis != "named" or latest.due_at > now:
                    continue
                _stored, new = self.store.add_trigger(
                    kind="check_in", department=department, portfolio_id=portfolio_id,
                    source_ref=f"check-in:{latest.check_in_id}", occurred_at=latest.due_at,
                    check_in_id=latest.check_in_id, note=latest.note)
                added += int(new)
        return added

    # --- department tasks made outside the engine ---------------------------------------------------------------

    def _tasks(self, portfolios: Sequence[str]) -> int:
        """Record a wake for every department root task made outside the engine (Secretary routing, owner requests).

        They are not coalesced; they run as their makers scheduled them. Recording them keeps the wake log complete.
        """
        added = 0
        with self.database.immediate() as connection:
            position = self.store.cursor("tasks")
            rows = connection.execute(
                """SELECT rowid AS seq, task_id, parent_id, portfolio_id, role, objective, created_at, dedup_key
                   FROM tasks WHERE rowid > ? ORDER BY rowid LIMIT ?""", (position, _BATCH)).fetchall()
            for row in rows:
                if (row["parent_id"] is not None or row["portfolio_id"] not in portfolios
                        or row["role"] not in DEPARTMENTS or (row["dedup_key"] or "").startswith("wake:")):
                    continue  # a delegated task runs inside its root's wake; an engine wake is recorded already
                self._task_wake(row)
                added += 1
            if rows:
                self.store.advance("tasks", rows[-1]["seq"])
        return added

    def _task_wake(self, row) -> None:
        from trade_graph.application.events.allowance import read_allowance
        from trade_graph.application.events.engine import INSTRUCTION
        from trade_graph.application.events.store import new_id

        created = parse_utc(row["created_at"])
        day = self.engine.trading_day(created)
        wake_id = new_id()
        stored, _ = self.store.add_trigger(
            kind="task_created", department=row["role"], portfolio_id=row["portfolio_id"],
            source_ref=f"task:{row['task_id']}", occurred_at=created, task_id=row["task_id"],
            objective=str(row["objective"])[:200], status="claimed", wake_id=wake_id)
        context = WakeContext(
            wake_id=wake_id, portfolio_id=row["portfolio_id"], department=row["role"], purpose="task",
            trading_day_start=day.start, trading_day_end=day.end, created_at=created,
            reasons=reasons_of([stored.trigger]), triggers=(stored.trigger,),
            check_in=self.store.latest_check_in(row["portfolio_id"], row["role"]),
            allowance=read_allowance(self.database, self.engine.clock), instruction=INSTRUCTION)
        self.store.insert_wake(context=context, origin="task", task_id=row["task_id"])


__all__ = ["TriggerSources"]
