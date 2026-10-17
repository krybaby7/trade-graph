"""The Trader's exit tools on day-plan positions (W8-B, C4; D115-D117, D127, D185-D194, D245-D249).

In a wake reply the Trader may close its day-plan position in a coin now, or move that position's safety exit to a
new stop (and optional take-profit). Software carries it out as one recorded sequence (``day_plan_action_steps``):

1. deterministic checks, before anything is touched: the paper account, the portfolio not paused, spot (D127), the
   coin has an open day-plan position, a quote, and for a move a stop below the bid and a take-profit above it on
   the price steps; for a close the mandate allows the sell and it is above the venue minimum. A refusal here
   touches nothing. There is no confidence threshold, committee or approval (D116, D121);
2. the intents about to be cancelled are recorded, then cancelled and reconciled (a close cancels the coin's resting
   day-plan buys first, then its exits; a move only the exits);
3. a close sells what those exits protected with one market sell (each entry's part recorded, so the protection
   arithmetic counts it); a move attaches new exits at the new terms for exactly what is filled and uncovered;
4. the sell or the new exits are sent at once, and the action ends ``done``.

An open position is never left without a safety exit, except during the sell itself: a cancel that gets no answer
stops the sequence (the unanswered exit still covers its coins, unknown is not cancelled) and every exit that was
cancelled is put back with its own terms; a sell or new exit that software or the broker refuses puts the old exits
back the same way. A crash leaves the action running: the management step's sweep protects the position meanwhile
(at the terms in force), and applying the same request again finishes it from the records, never selling twice.
A newer action for the coin, or an action the event engine finds obsolete, ends an unfinished one as failed.

Paper only, like day plans: a live venue needs native protective orders first (T19). This module is protected
execution control (``kernel/authority.py``): agents cannot edit it.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from trade_graph.application.day_plan_protection import OUTSTANDING, TERMINAL
from trade_graph.application.execution import Execution
from trade_graph.contracts.day_plans import (
    DayPlanPosition,
    PositionAction,
    PositionActionSummary,
    PositionExit,
)
from trade_graph.contracts.models import Decision, InstrumentRules, Mandate, OwnerPolicy, Quantity
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import (
    AuthorityDenied,
    DuplicateRecord,
    NotFound,
    StaleState,
    TradeGraphError,
    ValidationFailure,
)
from trade_graph.domain.money import canonical_decimal
from trade_graph.domain.precision import floor_to_increment
from trade_graph.kernel.authority import pause_allows_new_decisions, pause_allows_protection
from trade_graph.kernel.riba import refuse_riba_symbol

_REFERENCE = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")
SYSTEM_VERSION = "day-plan-actions"
END_STEPS = frozenset({"done", "refused", "failed"})
MAX_POSITIONS = 16
MAX_TERMS_SHOWN = 8
_ENTRIES = """SELECT e.*, p.portfolio_id, p.symbol, p.strategy_ref FROM day_plan_entries e
              JOIN day_plans p ON p.plan_id = e.plan_id"""
_OUTSTANDING_SQL = "(" + ", ".join(f"'{state}'" for state in sorted(OUTSTANDING)) + ")"


@dataclass(frozen=True, slots=True)
class ActionRecord:
    """One of the Trader's position actions as recorded, with every step and how it ended (``running`` if not)."""

    action_id: str
    portfolio_id: str
    request_id: str
    symbol: str
    action: PositionAction
    document_sha256: str
    task_id: str | None
    requested_at: str
    status: str
    reason: str | None
    error_code: str | None
    steps: tuple[dict[str, Any], ...]
    created: bool


class DayPlanActions:
    """Close a day-plan position or move its safety exit for the Trader, on the paper account."""

    def __init__(self, execution: Execution) -> None:
        self.execution = execution
        self.database, self.clock = execution.database, execution.clock
        self.protection = execution.day_plan_protection

    def available(self) -> bool:
        """The day-plan records (0034) and the action records (0044) exist."""
        return self.protection.available() and self.protection.actions_available()

    # --- applying an action -------------------------------------------------------------------------------------

    async def apply(self, portfolio_id: str, action: PositionAction, *, request_id: str,
                    task_id: str | None = None) -> ActionRecord:
        """Carry out ``action`` (or finish or return the action this ``request_id`` already started).

        Returns the record, whose ``status`` says how it ended (``done``, ``refused``, ``failed``); a refusal or failure
        of the action itself is recorded, not raised. Call it under the service's execution lock (the event engine
        does, after the Trader's answer was saved).
        """
        if not isinstance(action, PositionAction):
            raise ValidationFailure("a PositionAction is required")
        for name, value in (("request_id", request_id), ("task_id", task_id)):
            if (value is not None or name == "request_id") and (
                    not isinstance(value, str) or _REFERENCE.fullmatch(value) is None):
                raise ValidationFailure(f"{name} is 1 to 128 letters, digits or ._:/- characters")
        if not self.available():
            raise StaleState("this database has no records for the Trader's position actions yet (state migration "
                             "0044); the exits stay as they are")
        existing = self.for_request(portfolio_id, request_id)
        if existing is not None:
            if existing.document_sha256 != action.content_sha256:
                raise DuplicateRecord(f"request {request_id} already asked for a different action "
                                      f"({existing.action_id}); a new action needs a new request")
            if existing.status == "running":
                await self._run(existing.action_id)
            return self._record(existing.action_id, created=False)
        if self.database.execute("SELECT 1 FROM portfolios WHERE portfolio_id = ?",
                                 (portfolio_id,)).fetchone() is None:
            raise NotFound(f"no portfolio {portfolio_id}")
        for earlier in self._running(portfolio_id, action.symbol):
            await self._finish(earlier, "failed", restore=True,
                               reason=f"superseded by a newer action for {action.symbol} (request {request_id})")
        action_id = self._insert(portfolio_id, action, request_id=request_id, task_id=task_id)
        await self._run(action_id)
        return self._record(action_id, created=True)

    async def abandon(self, portfolio_id: str, request_id: str, *, reason: str) -> ActionRecord | None:
        """End an unfinished action as failed and put its old exits back (an obsolete wake, W8-B); idempotent."""
        if not self.available():
            return None
        record = self.for_request(portfolio_id, request_id)
        if record is None:
            return None
        if record.status == "running":
            await self._finish(record.action_id, "failed", reason=reason, restore=True)
        return self._record(record.action_id, created=False)

    async def _run(self, action_id: str) -> None:
        record = self._record(action_id, created=False)
        kinds = {step["kind"] for step in record.steps}
        if "sell_placed" in kinds:  # a restart after the sell was persisted: send it, never place a second one
            await self._send_sell(record)
            return
        if "exits_attached" in kinds:
            await self._send_exits(record)
            return
        try:
            self._check(record)
        except TradeGraphError as error:
            await self._finish(action_id, "refused", reason=str(error), code=error.code, restore=True)
            return
        if record.action.action == "close":
            await self._close(record)
        else:
            await self._move(record)

    def _check(self, record: ActionRecord) -> None:
        """Every deterministic check, before anything is cancelled. Raises the typed refusal."""
        execution, action = self.execution, record.action
        if (execution.venue, execution.account_id, execution.mode) != ("paper", "paper", "paper"):
            raise AuthorityDenied("the Trader's position actions work on the paper account only; a live venue needs "
                                  "native protective orders first (T19)")
        portfolio = self.database.execute("SELECT mode FROM portfolios WHERE portfolio_id = ?",
                                          (record.portfolio_id,)).fetchone()
        if portfolio is None or portfolio["mode"] != "paper":
            raise AuthorityDenied("the Trader's position actions need a paper portfolio")
        profile = execution.profile(record.portfolio_id)
        if not pause_allows_new_decisions(profile):  # type: ignore[arg-type]
            raise AuthorityDenied(f"pause {profile} refuses the Trader's position actions; the protective exits stay "
                                  "as they are and the pause manages the position")
        refuse_riba_symbol(action.symbol)
        rules = execution.instrument("paper", action.symbol)
        position = self._coin_position(record.portfolio_id, action.symbol, last_action=False)
        if position.protected + position.unprotected <= 0:
            verb = "close" if action.action == "close" else "move the safety exit of"
            raise ValidationFailure(f"there is no open day-plan position in {action.symbol} to {verb}")
        quote = execution.latest_observation(action.symbol, utc_iso(self.clock.now()), "paper")
        if quote is None or quote.bid is None:
            raise StaleState(f"no quote for {action.symbol} yet to place a stop or a sell against")
        bid = quote.bid
        if action.action == "move_exit":
            self._check_terms(action, rules, bid)
            return
        estimate = floor_to_increment(min(position.held, position.protected + position.unprotected),
                                      rules.quantity_increment)
        policy = execution.authority.active_policy()
        mandate = execution.authority.active_mandate(record.portfolio_id)
        entry = self._entries(record.portfolio_id, action.symbol)[0]
        execution.authority.require_for_decision(
            self._sell_decision(record, estimate if estimate > 0 else rules.min_quantity, rules, policy, mandate,
                                entry), venue="paper", mode="paper")
        if estimate < rules.min_quantity or estimate * bid < rules.min_notional:
            raise ValidationFailure(f"selling {canonical_decimal(estimate)} {rules.base_asset} is below the venue "
                                    f"minimum (quantity {canonical_decimal(rules.min_quantity)}, value "
                                    f"{canonical_decimal(rules.min_notional)}); the exit stays")

    @staticmethod
    def _check_terms(action: PositionAction, rules: InstrumentRules, bid: Decimal) -> None:
        stop, target = action.stop_price, action.take_profit_price
        for name, value in (("stop", stop), ("take-profit", target)):
            if value is not None and value != floor_to_increment(value, rules.price_increment):
                raise ValidationFailure(f"the {name} {canonical_decimal(value)} is off the price increment "
                                        f"{canonical_decimal(rules.price_increment)}")
        if stop is None or stop >= bid:
            raise ValidationFailure(f"the new stop {canonical_decimal(stop)} is not below the bid "
                                    f"{canonical_decimal(bid)}: a stop rests below the market; to sell now, close "
                                    "the position")
        if target is not None and target <= bid:
            raise ValidationFailure(f"the take-profit {canonical_decimal(target)} is not above the bid "
                                    f"{canonical_decimal(bid)}: it would sell at once; to sell now, close the position")

    # --- the sequence -------------------------------------------------------------------------------------------

    async def _close(self, record: ActionRecord) -> None:
        portfolio_id, symbol = record.portfolio_id, record.symbol
        resting = [row["intent_id"] for row in self._entries(portfolio_id, symbol)
                   if self.execution.intent_state(row["intent_id"]) in OUTSTANDING]
        if resting and not await self._cancel(record, entries=resting, exits=[], reason="trader_close"):
            return
        exits = self._outstanding_exits(portfolio_id, symbol)  # read after the buys: a late fill has its exit too
        if exits and not await self._cancel(record, entries=[], exits=exits, reason="trader_close"):
            return
        await self._sell(record)

    async def _move(self, record: ActionRecord) -> None:
        execution, action = self.execution, record.action
        portfolio_id, symbol = record.portfolio_id, record.symbol
        exits = self._outstanding_exits(portfolio_id, symbol)
        if exits and not await self._cancel(record, entries=[], exits=exits, reason="trader_move_exit"):
            return
        profile = execution.profile(portfolio_id)
        if not pause_allows_protection(profile):  # type: ignore[arg-type]
            await self._finish(record.action_id, "refused", restore=True,
                               reason=f"pause {profile} began during the move; it keeps no protective exits")
            return
        placed: list[dict[str, str]] = []
        try:
            with self.database.immediate() as connection:
                for entry in self._entries(portfolio_id, symbol):
                    result = self.protection.place(entry, cause="sweep", stop_price=action.stop_price,
                                                   take_profit_price=action.take_profit_price)
                    if result is None:
                        continue
                    intent_id, quantity = result
                    self._link(connection, record.action_id, entry["entry_id"], intent_id, "moved_exit", quantity)
                    placed.append({"entry_id": entry["entry_id"], "intent_id": intent_id,
                                   "quantity": canonical_decimal(quantity)})
                if placed:
                    self._step(record.action_id, "exits_attached", {
                        "exits": placed, "stop_price": canonical_decimal(action.stop_price),
                        "take_profit_price": None if action.take_profit_price is None
                        else canonical_decimal(action.take_profit_price)})
        except TradeGraphError as error:
            await self._finish(record.action_id, "refused", reason=f"the new exit could not be placed: {error}",
                               code=error.code, restore=True)
            return
        if not placed:
            await self._finish(record.action_id, "done",
                               document={"note": "nothing was left to protect: the old exits had sold it"})
            return
        await self._send_exits(self._record(record.action_id, created=False))

    async def _cancel(self, record: ActionRecord, *, entries: list[str], exits: list[str], reason: str) -> bool:
        """Record what is about to be cancelled, cancel and reconcile it; False (and the action ended) when any
        answer is still unknown."""
        execution = self.execution
        self._step(record.action_id, "cancelling", {"entries": entries, "exits": exits})
        await execution.cancel_intents([*entries, *exits], reason=reason)
        states = {intent_id: execution.intent_state(intent_id) for intent_id in [*entries, *exits]}
        self._step(record.action_id, "cancelled", {"states": states})
        unsettled = [intent_id for intent_id, state in states.items() if state not in TERMINAL]
        if not unsettled:
            return True
        self._step(record.action_id, "unsettled", {"intents": unsettled},
                   reason="a cancel got no answer yet; unknown is not cancelled, so those orders stay in place")
        await self._finish(record.action_id, "failed", restore=True,
                           reason="a cancel got no answer; the old exits stay in place and nothing was sold or "
                                  "moved (reconciliation keeps asking)")
        return False

    async def _sell(self, record: ActionRecord) -> None:
        execution = self.execution
        rules = execution.instrument("paper", record.symbol)
        parts = self._parts(record.portfolio_id, record.symbol, rules)
        if not parts:
            await self._finish(record.action_id, "done",
                               document={"sold": "0", "note": "nothing was left to sell: the old exits had sold it"})
            return
        total = sum((quantity for _entry, quantity in parts), Decimal("0"))
        try:
            policy = execution.authority.active_policy()
            mandate = execution.authority.active_mandate(record.portfolio_id)
            with self.database.immediate() as connection:
                sell_id = execution.authorize(record.portfolio_id, self._sell_decision(
                    record, total, rules, policy, mandate, parts[0][0]))
                for entry, quantity in parts:
                    self._link(connection, record.action_id, entry["entry_id"], sell_id, "sell", quantity)
                self._step(record.action_id, "sell_placed", {
                    "intent_id": sell_id, "quantity": canonical_decimal(total),
                    "parts": [{"entry_id": entry["entry_id"], "quantity": canonical_decimal(quantity)}
                              for entry, quantity in parts]})
        except TradeGraphError as error:
            await self._finish(record.action_id, "refused", reason=f"the sell was refused: {error}", code=error.code,
                               restore=True)
            return
        await self._send_sell(self._record(record.action_id, created=False))

    async def _send_sell(self, record: ActionRecord) -> None:
        execution = self.execution
        sell_id = next(step["document"]["intent_id"] for step in record.steps if step["kind"] == "sell_placed")
        await execution.dispatch(intent_ids=[sell_id])
        state = execution.intent_state(sell_id)
        self._step(record.action_id, "sell_sent", {"intent_id": sell_id, "state": state})
        if state == "REJECTED":
            await self._finish(record.action_id, "refused", code="broker_rejected", restore=True,
                               reason=f"the broker refused the sell: {self._refusal(sell_id)}")
            return
        await self._finish(record.action_id, "done", document={"intent_id": sell_id, "state": state})

    async def _send_exits(self, record: ActionRecord) -> None:
        execution = self.execution
        placed = [item["intent_id"] for step in record.steps if step["kind"] == "exits_attached"
                  for item in step["document"]["exits"]]
        await execution.dispatch(protection_only=True)
        states = {intent_id: execution.intent_state(intent_id) for intent_id in placed}
        rejected = [intent_id for intent_id, state in states.items() if state == "REJECTED"]
        if rejected:
            await self._finish(record.action_id, "refused", code="broker_rejected", restore=True,
                               reason=f"the broker refused the new exit: {self._refusal(rejected[0])}")
            return
        await self._finish(record.action_id, "done", document={"exits": states})

    async def _finish(self, action_id: str, kind: str, *, reason: str | None = None, code: str | None = None,
                      document: dict[str, Any] | None = None, restore: bool = False) -> None:
        if restore and kind != "done":
            await self._restore(action_id)
        detail = dict(document or {})
        if code is not None:
            detail["code"] = code
        self._step(action_id, kind, detail, reason=None if reason is None else reason[:500])

    async def _restore(self, action_id: str) -> None:
        """Put back every exit this action cancelled, with its own terms, for what it had not sold; then sweep the
        coin, so nothing filled is left without an exit. Recorded as ``exits_restored`` when anything was done."""
        record = self._record(action_id, created=False)
        cancelled = self._cancelled_exits(record)
        if not any(step["kind"] == "cancelling" for step in record.steps):
            return  # nothing was cancelled: the old exits are untouched
        execution = self.execution
        profile = execution.profile(record.portfolio_id)
        if not pause_allows_protection(profile):  # type: ignore[arg-type]
            self._step(action_id, "exits_restored", {"skipped": f"pause {profile} keeps no protective exits"})
            return
        restored: list[dict[str, str]] = []
        deferred: list[dict[str, str]] = []
        for intent_id in cancelled:
            if execution.intent_state(intent_id) not in {"CANCELLED", "REJECTED"}:
                continue  # still outstanding (an unanswered cancel covers its coins) or it sold everything
            old = self.database.execute("SELECT * FROM day_plan_exits WHERE intent_id = ?", (intent_id,)).fetchone()
            entry = self._entry(old["entry_id"])
            unsold = Decimal(old["quantity"]) - execution._filled_quantity(intent_id)
            if unsold <= 0:
                continue
            target = old["take_profit_price"]
            try:
                with self.database.immediate() as connection:
                    placed = self.protection.place(entry, cause="sweep", stop_price=Decimal(old["stop_price"]),
                                                   take_profit_price=None if target is None else Decimal(target),
                                                   limit=unsold)
                    if placed is not None:
                        self._link(connection, action_id, entry["entry_id"], placed[0], "restored_exit", placed[1])
            except Exception as error:  # noqa: BLE001 - recorded; the management step's sweep retries it
                self.protection._deferred(entry, error, exposed=unsold)
                deferred.append({"replaces": intent_id, "reason": f"{type(error).__name__}: {error}"[:300]})
                continue
            if placed is not None:
                restored.append({"replaces": intent_id, "intent_id": placed[0],
                                 "quantity": canonical_decimal(placed[1])})
        swept = [intent_id for entry in self._entries(record.portfolio_id, record.symbol)
                 if (intent_id := self.protection.protect(entry, cause="sweep")) is not None]
        if not (restored or swept or deferred):
            return
        self._step(action_id, "exits_restored", {"restored": restored, "swept": swept, "deferred": deferred})
        await execution.dispatch(protection_only=True)

    # --- helpers ------------------------------------------------------------------------------------------------

    def _parts(self, portfolio_id: str, symbol: str, rules: InstrumentRules) -> list[tuple[Any, Decimal]]:
        """What the close sells, entry by entry: each filled, uncovered part, never more than is held and
        unreserved (spot, D127), on the lot."""
        execution = self.execution
        wanted = []
        for entry in self._entries(portfolio_id, symbol):
            filled, covered = self.protection.coverage(entry)
            if filled - covered > 0:
                wanted.append((entry, filled - covered))
        available = (execution.owned_quantity(portfolio_id, rules.base_asset)
                     - execution._reserved(portfolio_id, rules.base_asset))
        left = floor_to_increment(min(sum((item for _e, item in wanted), Decimal("0")), max(available, Decimal(0))),
                                  rules.quantity_increment)
        parts = []
        for entry, exposed in wanted:
            take = min(exposed, left)
            if take > 0:
                parts.append((entry, take))
                left -= take
        return parts

    def _sell_decision(self, record: ActionRecord, quantity: Decimal, rules: InstrumentRules, policy: OwnerPolicy,
                       mandate: Mandate, entry: Any) -> Decision:
        task_id = record.task_id
        return Decision(
            record_id=f"{record.action_id}:sell", created_at_utc=self.clock.now(), run_id=task_id or record.action_id,
            task_id=task_id, root_task_id=task_id or record.action_id, portfolio_id=record.portfolio_id,
            mode="paper", system_version_id=SYSTEM_VERSION, evidence_refs=[f"day-plan-action:{record.action_id}"],
            trace_id=record.action_id, action="exit", symbol=record.symbol,
            quantity=Quantity(amount=quantity, asset=rules.base_asset), rationale=record.action.reason,
            invalidation="The Trader closed its day-plan position.", horizon_seconds=60,
            strategy_id=entry["strategy_ref"].split("@", 1)[0], snapshot_id=f"day-plan-action:{record.action_id}",
            mandate_revision=str(mandate.revision), policy_revision=policy.revision_id)

    def _refusal(self, intent_id: str) -> str:
        payload = self.execution._payload(intent_id)
        return str(payload.get("message") or payload.get("error") or "rejected")[:300]

    def _entries(self, portfolio_id: str, symbol: str) -> list[Any]:
        return self.database.execute(f"{_ENTRIES} WHERE p.portfolio_id = ? AND p.symbol = ? ORDER BY e.sequence",
                                     (portfolio_id, symbol)).fetchall()

    def _entry(self, entry_id: str) -> Any:
        return self.database.execute(f"{_ENTRIES} WHERE e.entry_id = ?", (entry_id,)).fetchone()

    def _outstanding_exits(self, portfolio_id: str, symbol: str) -> list[str]:
        rows = self.database.execute(
            f"""SELECT x.intent_id FROM day_plan_exits x JOIN day_plan_entries e ON e.entry_id = x.entry_id
                JOIN day_plans p ON p.plan_id = e.plan_id JOIN order_intents i ON i.intent_id = x.intent_id
                WHERE p.portfolio_id = ? AND p.symbol = ? AND i.state IN {_OUTSTANDING_SQL} ORDER BY x.sequence""",
            (portfolio_id, symbol)).fetchall()
        return [row["intent_id"] for row in rows]

    @staticmethod
    def _cancelled_exits(record: ActionRecord) -> list[str]:
        exits: list[str] = []
        for step in record.steps:
            if step["kind"] == "cancelling":
                exits.extend(item for item in step["document"].get("exits", []) if item not in exits)
        return exits

    def _link(self, connection, action_id: str, entry_id: str, intent_id: str, role: str, quantity: Decimal) -> None:
        connection.execute(
            "INSERT INTO day_plan_action_orders (action_id, entry_id, intent_id, role, quantity, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (action_id, entry_id, intent_id, role, canonical_decimal(quantity), utc_iso(self.clock.now())))

    def _step(self, action_id: str, kind: str, document: dict[str, Any] | None = None, *,
              reason: str | None = None) -> None:
        with self.database.immediate() as connection:
            connection.execute(
                "INSERT INTO day_plan_action_steps (action_id, kind, reason, document_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (action_id, kind, reason, json.dumps(document or {}, sort_keys=True), utc_iso(self.clock.now())))

    def _insert(self, portfolio_id: str, action: PositionAction, *, request_id: str, task_id: str | None) -> str:
        authority = self.execution.authority
        try:
            policy_revision: str | None = authority.active_policy().revision_id
            mandate_revision: str | None = str(authority.active_mandate(portfolio_id).revision)
        except TradeGraphError:
            policy_revision = mandate_revision = None
        action_id = f"da-{uuid.uuid4().hex}"
        with self.database.immediate() as connection:
            connection.execute(
                """INSERT INTO day_plan_actions (action_id, portfolio_id, request_id, symbol, action, stop_price,
                   take_profit_price, task_id, policy_revision, mandate_revision, document_json, document_sha256,
                   requested_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (action_id, portfolio_id, request_id, action.symbol, action.action,
                 None if action.stop_price is None else canonical_decimal(action.stop_price),
                 None if action.take_profit_price is None else canonical_decimal(action.take_profit_price),
                 task_id, policy_revision, mandate_revision, json.dumps(action.canonical_document(), sort_keys=True),
                 action.content_sha256, utc_iso(self.clock.now())))
        return action_id

    # --- reading ------------------------------------------------------------------------------------------------

    def get(self, action_id: str) -> ActionRecord:
        return self._record(action_id, created=False)

    def for_request(self, portfolio_id: str, request_id: str) -> ActionRecord | None:
        if not self.available():
            return None
        row = self.database.execute("SELECT action_id FROM day_plan_actions WHERE portfolio_id = ? AND request_id = ?",
                                    (portfolio_id, request_id)).fetchone()
        return None if row is None else self._record(row["action_id"], created=False)

    def _running(self, portfolio_id: str, symbol: str) -> list[str]:
        rows = self.database.execute(
            f"""SELECT action_id FROM day_plan_actions a WHERE portfolio_id = ? AND symbol = ? AND NOT EXISTS (
                SELECT 1 FROM day_plan_action_steps s WHERE s.action_id = a.action_id
                AND s.kind IN {tuple(sorted(END_STEPS))!r}) ORDER BY sequence""", (portfolio_id, symbol)).fetchall()
        return [row["action_id"] for row in rows]

    def positions(self, portfolio_id: str) -> list[DayPlanPosition]:
        """Each coin's day-plan position with its exits and newest action, for the Trader's wake (data only)."""
        if not self.protection.available():
            return []
        symbols = [row["symbol"] for row in self.database.execute(
            "SELECT symbol FROM day_plans WHERE portfolio_id = ? GROUP BY symbol ORDER BY MIN(sequence)",
            (portfolio_id,)).fetchall()]
        shown = []
        for symbol in symbols:
            position = self._coin_position(portfolio_id, symbol, last_action=True)
            if position.protected > 0 or position.unprotected > 0:
                shown.append(position)
        return shown[:MAX_POSITIONS]

    def _coin_position(self, portfolio_id: str, symbol: str, *, last_action: bool) -> DayPlanPosition:
        execution = self.execution
        held = execution.owned_quantity(portfolio_id, symbol.split("/", 1)[0])
        terms: dict[tuple[str, str | None], Decimal] = {}
        protected = exposure = Decimal("0")
        resting = 0
        for entry in self._entries(portfolio_id, symbol):
            if execution.intent_state(entry["intent_id"]) in OUTSTANDING:
                resting += 1
            filled, covered = self.protection.coverage(entry)
            exposure += max(Decimal("0"), filled - covered)
            for item in self.protection.exits(entry["entry_id"]):
                if execution.intent_state(item["intent_id"]) not in OUTSTANDING:
                    continue
                remaining = Decimal(item["quantity"]) - execution._filled_quantity(item["intent_id"])
                if remaining > 0:
                    key = (item["stop_price"], item["take_profit_price"])
                    terms[key] = terms.get(key, Decimal("0")) + remaining
                    protected += remaining
        exits = tuple(PositionExit(stop_price=stop, take_profit_price=target, quantity=quantity)
                      for (stop, target), quantity in list(terms.items())[:MAX_TERMS_SHOWN])
        return DayPlanPosition(
            symbol=symbol, held=held, protected=protected,
            unprotected=min(exposure, max(Decimal("0"), held - protected)), exits=exits, resting_entries=resting,
            last_action=self._last_action(portfolio_id, symbol) if last_action else None)

    def _last_action(self, portfolio_id: str, symbol: str) -> PositionActionSummary | None:
        if not self.protection.actions_available():
            return None
        row = self.database.execute("SELECT action_id FROM day_plan_actions WHERE portfolio_id = ? AND symbol = ? "
                                    "ORDER BY sequence DESC LIMIT 1", (portfolio_id, symbol)).fetchone()
        if row is None:
            return None
        record = self._record(row["action_id"], created=False)
        return PositionActionSummary(action_id=record.action_id, action=record.action.action,
                                     status=record.status, reason=record.reason,
                                     requested_at=record.requested_at)

    def _record(self, action_id: str, *, created: bool) -> ActionRecord:
        row = self.database.execute("SELECT * FROM day_plan_actions WHERE action_id = ?", (action_id,)).fetchone()
        if row is None:
            raise NotFound(f"no day-plan action {action_id}")
        action = PositionAction.model_validate_json(row["document_json"])
        if action.content_sha256 != row["document_sha256"]:
            raise ValidationFailure(f"the stored document of day-plan action {action_id} does not match its hash")
        steps = tuple({"kind": item["kind"], "reason": item["reason"], "document": json.loads(item["document_json"]),
                       "created_at": item["created_at"]} for item in self.database.execute(
            "SELECT * FROM day_plan_action_steps WHERE action_id = ? ORDER BY sequence", (action_id,)).fetchall())
        end = next((step for step in steps if step["kind"] in END_STEPS), None)
        return ActionRecord(
            action_id=action_id, portfolio_id=row["portfolio_id"], request_id=row["request_id"], symbol=row["symbol"],
            action=action, document_sha256=row["document_sha256"], task_id=row["task_id"],
            requested_at=row["requested_at"], status=end["kind"] if end else "running",
            reason=end["reason"] if end else None, error_code=end["document"].get("code") if end else None,
            steps=steps, created=created)


__all__ = ["SYSTEM_VERSION", "ActionRecord", "DayPlanActions"]
