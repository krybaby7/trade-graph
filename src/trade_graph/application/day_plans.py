"""Install Trader day plans: resting limit entries, each with a protective exit (T34; D115, D117, D119, D138).

The Trader writes a ``DayPlan`` for a focus coin. ``DayPlans.install`` validates it deterministically and installs
it; nothing here approves, scores or second-guesses the Trader's judgement (no confidence threshold, committee or
Leader approval, D121). The checks are the owner's rules and the facts a venue would enforce:

- day-plan orders stay off until the owner has recorded money limits (D138, W3-A's gate);
- the strategy version is registered and has a practice-range confirmation report (D138 item 5: a report must
  exist, with no pass mark; the Trader reads it and decides);
- spot, long only, no leverage (D127); the coin is in the owner's scope; the portfolio is not paused;
- prices on the tick and quantities on the lot, above the venue minimum; every entry rests below the ask, so it is
  a maker bid and not an order that would fill at once; it expires inside the trading day (D138 item 6);
- the whole plan fits the per-coin and total limits, counting every resting entry as if filled (D181), because
  each entry is authorized through ``Execution.authorize(..., day_plan=True)``.

Installation persists the plan, every entry's authorized intent with its reservation and outbox row, and the plan's
alarms in one transaction, before anything reaches the broker (dispatch sends them later). A retried installation
with the same ``request_id`` returns the installed plan instead of resting its entries twice. A new plan for a coin
replaces that coin's latest plan: its resting entries are cancelled and reconciled first, and if any of them is
still in an unknown state the new plan is refused until reconciliation settles it.

``manage`` is the management step: a pause cancels resting entries and keeps protective exits (D117), entries past
their expiry are cancelled, every filled part of an entry is protected (``DayPlanProtection.sweep``) and protective
exits are sent. Fills attach their exit at once (``Execution.record_fill``).

Paper only: a live venue needs native protective orders first (T19).
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from trade_graph.application.alarms.book import AlarmBook
from trade_graph.application.day_plan_protection import OUTSTANDING, TERMINAL
from trade_graph.application.execution import Execution
from trade_graph.application.practice_range.store import PracticeRangeStore, RunRecord
from trade_graph.contracts.alarms import AlarmRequest
from trade_graph.contracts.day_plans import DAY_PLAN_CONTRACT_VERSION, DayPlan, expiry_of
from trade_graph.contracts.models import Decision, InstrumentRules, OwnerPolicy
from trade_graph.domain.clock import parse_utc, utc_iso
from trade_graph.domain.errors import (
    AuthorityDenied,
    DuplicateRecord,
    NotFound,
    StaleState,
    TradeGraphError,
    ValidationFailure,
)
from trade_graph.domain.money import Money, Quantity, canonical_decimal
from trade_graph.domain.precision import floor_to_increment
from trade_graph.kernel.money_limits import TradingDay, trading_day
from trade_graph.kernel.riba import refuse_riba_symbol

_REFERENCE = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")
SYSTEM_VERSION = "day-plans"


@dataclass(frozen=True, slots=True)
class PlanEntryRecord:
    entry_id: str
    position: int
    intent_id: str
    decision_id: str
    quantity: str
    limit_price: str
    stop_price: str
    take_profit_price: str | None
    expires_at: str


@dataclass(frozen=True, slots=True)
class InstalledPlan:
    plan_id: str
    portfolio_id: str
    request_id: str
    symbol: str
    trading_day_start: str
    trading_day_end: str
    strategy_ref: str
    strategy_sha256: str
    practice_reports: tuple[str, ...]
    supersedes_plan_id: str | None
    policy_revision: str
    mandate_revision: str
    task_id: str | None
    installed_at: str
    document_sha256: str
    plan: DayPlan
    entries: tuple[PlanEntryRecord, ...]
    alarm_ids: tuple[str, ...]
    created: bool


@dataclass(frozen=True, slots=True)
class _Checked:
    policy: OwnerPolicy
    mandate_revision: str
    day: TradingDay
    strategy_sha256: str
    reports: tuple[RunRecord, ...]
    expiries: tuple[datetime, ...]
    rules: InstrumentRules | None


class DayPlans:
    """Validate, install and manage Trader day plans on the paper account."""

    def __init__(self, execution: Execution, *, practice_range: PracticeRangeStore | None = None,
                 alarms: AlarmBook | None = None) -> None:
        self.execution = execution
        self.database, self.clock = execution.database, execution.clock
        self.practice_range = practice_range or PracticeRangeStore(self.database, self.clock)
        self.alarms = alarms or AlarmBook(self.database, self.clock)
        self.protection = execution.day_plan_protection

    # --- installing ---------------------------------------------------------------------------------------------

    async def install(self, portfolio_id: str, plan: DayPlan, *, request_id: str, task_id: str | None = None,
                      snapshot_id: str | None = None, system_version_id: str = SYSTEM_VERSION) -> InstalledPlan:
        """Install ``plan`` (or return the plan this ``request_id`` already installed).

        Refusals raise the typed error after recording it as a visible ``day_plan_refused`` activity event; a
        refused plan writes nothing else. Call it under the service's execution lock (the Trader's turn in T35).
        """
        if not isinstance(plan, DayPlan):
            raise ValidationFailure("a DayPlan is required")
        for name, value in (("request_id", request_id), ("task_id", task_id), ("snapshot_id", snapshot_id)):
            if (value is not None or name == "request_id") and (
                    not isinstance(value, str) or _REFERENCE.fullmatch(value) is None):
                raise ValidationFailure(f"{name} is 1 to 128 letters, digits or ._:/- characters")
        existing = self._plan_for_request(portfolio_id, request_id)
        if existing is not None:
            return self._same_plan(existing, plan)
        try:
            self._check(portfolio_id, plan)
            latest = self._latest(portfolio_id, plan.symbol)
            if latest is not None:
                await self._retire(latest, reason="replaced")
            return self._install(portfolio_id, plan, request_id=request_id, task_id=task_id,
                                 snapshot_id=snapshot_id, system_version_id=system_version_id)
        except TradeGraphError as error:
            self._refused(portfolio_id, plan, request_id=request_id, task_id=task_id, error=error)
            raise

    def _check(self, portfolio_id: str, plan: DayPlan) -> _Checked:
        """Every deterministic check that needs no write. ``authorize`` repeats the money rules per entry."""
        execution = self.execution
        if (execution.venue, execution.account_id, execution.mode) != ("paper", "paper", "paper"):
            raise AuthorityDenied("day plans install on the paper account only; a live venue needs native "
                                  "protective orders first (T19)")
        portfolio = self.database.execute("SELECT mode FROM portfolios WHERE portfolio_id = ?",
                                          (portfolio_id,)).fetchone()
        if portfolio is None or portfolio["mode"] != "paper":
            raise AuthorityDenied("day plans need a paper portfolio")
        policy = execution.authority.active_policy()
        limits = execution.money_limits.require_day_plan_orders(policy)
        mandate = execution.authority.active_mandate(portfolio_id)
        profile = execution.profile(portfolio_id)
        if profile != "RUNNING":
            raise AuthorityDenied(f"pause {profile} refuses new day plans (resting entries are cancelled and "
                                  "protective exits kept while paused)")
        refuse_riba_symbol(plan.symbol)
        try:
            strategy = self.practice_range.strategy(plan.strategy_ref)
        except NotFound:
            raise ValidationFailure(f"{plan.strategy_ref} is not a registered practice-range strategy version; "
                                    "register it and replay it first (D138 item 5)") from None
        now = self.clock.now()
        reports = tuple(report for report in self.practice_range.reports_for(plan.strategy_ref, phase="confirm")
                        if parse_utc(report.created_at) <= now)
        if not reports:
            raise ValidationFailure(
                f"{plan.strategy_ref} has no practice-range confirmation report yet: a report must exist before a "
                "strategy version enters a day plan (D138 item 5). It is evidence for the Trader, not a pass mark")
        day = trading_day(now, limits.trading_day_start_utc)
        rules = None
        expiries: list[datetime] = []
        if plan.entries:
            rules = execution.instrument("paper", plan.symbol)
            quote = execution.latest_observation(plan.symbol, utc_iso(now), "paper")
            if quote is None or quote.ask is None:
                raise StaleState("no quote yet to tell a resting entry from one that would fill at once")
            for number, item in enumerate(plan.entries, start=1):
                self._check_entry(number, item.quantity, item.limit_price, item.stop_price, item.take_profit_price,
                                  rules=rules, ask=quote.ask)
                expiry = expiry_of(item, day.end)
                if expiry <= now:
                    raise ValidationFailure(f"entry {number}: its expiry {utc_iso(expiry)} must be in the future")
                if expiry > day.end:
                    raise ValidationFailure(f"entry {number}: it expires after the end of the trading day "
                                            f"({utc_iso(day.end)}); tomorrow's plan rests tomorrow's entries")
                expiries.append(expiry)
        return _Checked(policy=policy, mandate_revision=str(mandate.revision), day=day,
                        strategy_sha256=strategy.content_sha256, reports=reports, expiries=tuple(expiries),
                        rules=rules)

    @staticmethod
    def _check_entry(number: int, quantity: Decimal, limit: Decimal, stop: Decimal, target: Decimal | None, *,
                     rules: InstrumentRules, ask: Decimal) -> None:
        for name, value in (("limit price", limit), ("stop", stop), ("take-profit", target)):
            if value is not None and value != floor_to_increment(value, rules.price_increment):
                raise ValidationFailure(f"entry {number}: the {name} {canonical_decimal(value)} is off the price "
                                        f"increment {canonical_decimal(rules.price_increment)}")
        if quantity != floor_to_increment(quantity, rules.quantity_increment):
            raise ValidationFailure(f"entry {number}: the quantity {canonical_decimal(quantity)} is off the "
                                    f"quantity increment {canonical_decimal(rules.quantity_increment)}")
        if quantity < rules.min_quantity or quantity * limit < rules.min_notional:
            raise ValidationFailure(f"entry {number}: below the venue minimum (quantity "
                                    f"{canonical_decimal(rules.min_quantity)}, value "
                                    f"{canonical_decimal(rules.min_notional)})")
        if limit >= ask:
            raise ValidationFailure(f"entry {number}: a day-plan entry rests below the market; its limit "
                                    f"{canonical_decimal(limit)} is at or above the ask {canonical_decimal(ask)}, so "
                                    "it would fill at once as a taker")

    def _install(self, portfolio_id: str, plan: DayPlan, *, request_id: str, task_id: str | None,
                 snapshot_id: str | None, system_version_id: str) -> InstalledPlan:
        execution = self.execution
        with self.database.immediate() as connection:
            existing = self._plan_for_request(portfolio_id, request_id)
            if existing is not None:
                return self._same_plan(existing, plan)
            checked = self._check(portfolio_id, plan)
            latest = self._latest(portfolio_id, plan.symbol)
            if latest is not None:
                unsettled = [item.intent_id for item in latest.entries
                             if execution.intent_state(item.intent_id) not in TERMINAL]
                if unsettled:
                    raise StaleState(
                        f"plan {latest.plan_id} for {plan.symbol} has an entry whose status is not settled yet "
                        "(unknown or cancel pending); it is reconciled before the plan is replaced")
            plan_id = f"dp-{uuid.uuid4().hex}"
            now = self.clock.now()
            installed_at = utc_iso(now)
            policy = checked.policy
            connection.execute(
                """INSERT INTO day_plans (plan_id, portfolio_id, request_id, symbol, trading_day_start,
                   trading_day_end, strategy_ref, strategy_sha256, practice_reports, supersedes_plan_id,
                   policy_revision, mandate_revision, task_id, contract_version, document_json, document_sha256,
                   installed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (plan_id, portfolio_id, request_id, plan.symbol, utc_iso(checked.day.start), utc_iso(checked.day.end),
                 plan.strategy_ref, checked.strategy_sha256,
                 json.dumps([report.run_id for report in checked.reports]),
                 None if latest is None else latest.plan_id, policy.revision_id, checked.mandate_revision, task_id,
                 DAY_PLAN_CONTRACT_VERSION, json.dumps(plan.canonical_document(), sort_keys=True),
                 plan.content_sha256, installed_at))
            if latest is not None:
                self._event(latest.plan_id, "superseded", document={"by": plan_id})
            base, quote = plan.symbol.split("/", 1)
            intents = []
            for position, (item, expiry) in enumerate(zip(plan.entries, checked.expiries, strict=True)):
                decision_id = f"{plan_id}:d{position}"
                decision = Decision(
                    record_id=decision_id, created_at_utc=now, run_id=task_id or plan_id, task_id=task_id,
                    root_task_id=task_id or plan_id, portfolio_id=portfolio_id, mode="paper",
                    system_version_id=system_version_id,
                    evidence_refs=[f"practice-range:{report.run_id}" for report in checked.reports],
                    trace_id=plan_id, action="enter", symbol=plan.symbol,
                    quantity=Quantity(amount=item.quantity, asset=base),
                    limit_price=Money(amount=item.limit_price, currency=quote),
                    rationale=plan.rationale, invalidation=plan.invalidation,
                    horizon_seconds=max(1, int((expiry - now).total_seconds())),
                    strategy_id=plan.strategy_id, snapshot_id=snapshot_id or f"day-plan:{plan_id}",
                    mandate_revision=checked.mandate_revision, policy_revision=policy.revision_id,
                )
                intent_id = execution.authorize(portfolio_id, decision, day_plan=True)
                intents.append(intent_id)
                connection.execute(
                    """INSERT INTO day_plan_entries (entry_id, plan_id, position, intent_id, decision_id, quantity,
                       limit_price, stop_price, take_profit_price, expires_at, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (f"{plan_id}:e{position}", plan_id, position, intent_id, decision_id,
                     canonical_decimal(item.quantity), canonical_decimal(item.limit_price),
                     canonical_decimal(item.stop_price),
                     None if item.take_profit_price is None else canonical_decimal(item.take_profit_price),
                     utc_iso(expiry), installed_at))
            alarm_ids = []
            for position, alarm in enumerate(plan.alarms):
                record = self.alarms.create(AlarmRequest(
                    department="trader", condition=alarm.condition(), portfolio_id=portfolio_id,
                    day_plan_ref=plan_id, task_id=task_id, request_id=f"{plan_id}:a{position}", note=alarm.note))
                alarm_ids.append(record.alarm_id)
            if latest is not None:
                for alarm_id in latest.alarm_ids:
                    self.alarms.cancel(alarm_id, department="trader", reason="replaced by a new day plan",
                                       task_id=task_id)
            self._event(plan_id, "installed", document={"alarm_ids": alarm_ids, "entry_intents": intents,
                                                        "practice_reports": [r.run_id for r in checked.reports]})
        return self._record(plan_id, created=True)

    async def _retire(self, plan: InstalledPlan, *, reason: str) -> list[str]:
        """Cancel the plan's outstanding entries (reconciling each) and record why."""
        outstanding = [item.intent_id for item in plan.entries
                       if self.execution.intent_state(item.intent_id) in OUTSTANDING]
        if not outstanding:
            return []
        acted = await self.execution.cancel_intents(outstanding, reason=reason)
        self._cancelled_events(plan.entries, acted, reason)
        return acted

    def _cancelled_events(self, entries, acted: list[str], reason: str) -> None:
        ended = {item.intent_id: item for item in entries
                 if item.intent_id in acted and self.execution.intent_state(item.intent_id) in {"CANCELLED",
                                                                                                 "REJECTED"}}
        with self.database.immediate() as connection:
            for intent_id, item in ended.items():
                connection.execute(
                    "INSERT OR IGNORE INTO day_plan_events (plan_id, entry_id, kind, reason, document_json, "
                    "created_at) VALUES (?, ?, 'entry_cancelled', ?, ?, ?)",
                    (item.entry_id.rsplit(":", 1)[0], item.entry_id, reason,
                     json.dumps({"intent_id": intent_id}), utc_iso(self.clock.now())))

    def _same_plan(self, existing: InstalledPlan, plan: DayPlan) -> InstalledPlan:
        if existing.document_sha256 != plan.content_sha256:
            raise DuplicateRecord(f"request {existing.request_id} already installed a different plan "
                                  f"({existing.plan_id}); a revised plan needs a new request")
        return existing

    def _refused(self, portfolio_id: str, plan: DayPlan, *, request_id: str, task_id: str | None,
                 error: TradeGraphError) -> None:
        known = self.database.execute("SELECT 1 FROM portfolios WHERE portfolio_id = ?", (portfolio_id,)).fetchone()
        self.execution.ledger._activity(portfolio_id if known else None, "day_plan_refused", {
            "portfolio_id": portfolio_id, "request_id": request_id, "task_id": task_id, "symbol": plan.symbol,
            "strategy_ref": plan.strategy_ref, "document_sha256": plan.content_sha256, "code": error.code,
            "error": type(error).__name__, "reason": str(error)[:500], "refused_at": utc_iso(self.clock.now()),
        })

    # --- managing -----------------------------------------------------------------------------------------------

    async def manage(self, portfolio_id: str) -> dict[str, Any]:
        """The day-plan management step; run it on every service tick, after the pause and money-limit steps.

        A pause (any profile but RUNNING) cancels resting entries; entries past their expiry are cancelled; every
        filled part of an entry is protected; protective exits are sent. Nothing is sold and no exit is removed.
        """
        execution = self.execution
        profile = execution.profile(portfolio_id)
        now = utc_iso(self.clock.now())
        rows = self.database.execute(
            """SELECT e.*, i.state FROM day_plan_entries e JOIN day_plans p ON p.plan_id = e.plan_id
               JOIN order_intents i ON i.intent_id = e.intent_id WHERE p.portfolio_id = ? ORDER BY e.sequence""",
            (portfolio_id,)).fetchall()
        due: dict[str, list] = {"pause": [], "expired": []}
        for row in rows:
            if row["state"] not in OUTSTANDING:
                continue
            if profile != "RUNNING":
                due["pause"].append(row)
            elif row["expires_at"] <= now:
                due["expired"].append(row)
        cancelled: list[str] = []
        for reason, items in due.items():
            if not items:
                continue
            entries = [self._entry(row) for row in items]
            acted = await execution.cancel_intents([item.intent_id for item in entries], reason=reason)
            self._cancelled_events(entries, acted, reason)
            cancelled.extend(acted)
        protected = self.protection.sweep(portfolio_id)
        dispatched = await execution.dispatch(protection_only=True)
        return {"portfolio_id": portfolio_id, "profile": profile, "cancelled": cancelled, "protected": protected,
                "dispatched": dispatched}

    # --- reading ------------------------------------------------------------------------------------------------

    def get(self, plan_id: str) -> InstalledPlan:
        return self._record(plan_id, created=False)

    def active(self, portfolio_id: str, symbol: str | None = None) -> list[InstalledPlan]:
        """The latest plan of each coin whose trading day has not ended."""
        rows = self.database.execute(
            """SELECT plan_id FROM day_plans p WHERE portfolio_id = ? AND (? IS NULL OR symbol = ?)
               AND trading_day_end > ? AND NOT EXISTS (SELECT 1 FROM day_plans n WHERE n.supersedes_plan_id =
               p.plan_id) ORDER BY sequence""",
            (portfolio_id, symbol, symbol, utc_iso(self.clock.now()))).fetchall()
        return [self.get(row["plan_id"]) for row in rows]

    def status(self, plan_id: str) -> dict[str, Any]:
        """The plan as it stands: each entry's order state and fills, its exits and what is still unprotected."""
        record = self.get(plan_id)
        execution = self.execution
        entries = []
        for item in record.entries:
            row = self.protection.entry_for_intent(item.intent_id)
            filled, covered = self.protection.coverage(row)
            exits = [{
                "exit_id": exit_["exit_id"], "intent_id": exit_["intent_id"],
                "state": execution.intent_state(exit_["intent_id"]), "quantity": exit_["quantity"],
                "sold": canonical_decimal(execution._filled_quantity(exit_["intent_id"])),
                "stop_price": exit_["stop_price"], "take_profit_price": exit_["take_profit_price"],
                "cause": exit_["cause"], "fill_trade_id": exit_["fill_trade_id"], "created_at": exit_["created_at"],
            } for exit_ in self.protection.exits(item.entry_id)]
            entries.append({
                "entry_id": item.entry_id, "intent_id": item.intent_id, "state": execution.intent_state(item.intent_id),
                "quantity": item.quantity, "filled": canonical_decimal(filled), "limit_price": item.limit_price,
                "stop_price": item.stop_price, "take_profit_price": item.take_profit_price,
                "expires_at": item.expires_at, "unprotected": canonical_decimal(max(Decimal("0"), filled - covered)),
                "exits": exits,
            })
        events = [{"kind": row["kind"], "entry_id": row["entry_id"], "reason": row["reason"],
                   "document": json.loads(row["document_json"]), "created_at": row["created_at"]}
                  for row in self.database.execute("SELECT * FROM day_plan_events WHERE plan_id = ? ORDER BY sequence",
                                                   (plan_id,)).fetchall()]
        superseded_by = self.database.execute("SELECT plan_id FROM day_plans WHERE supersedes_plan_id = ?",
                                              (plan_id,)).fetchone()
        return {
            "plan_id": record.plan_id, "portfolio_id": record.portfolio_id, "symbol": record.symbol,
            "strategy_ref": record.strategy_ref, "practice_reports": list(record.practice_reports),
            "trading_day_start": record.trading_day_start, "trading_day_end": record.trading_day_end,
            "installed_at": record.installed_at, "superseded_by": superseded_by["plan_id"] if superseded_by else None,
            "rationale": record.plan.rationale, "invalidation": record.plan.invalidation,
            "alarm_ids": list(record.alarm_ids), "entries": entries, "events": events,
        }

    def _plan_for_request(self, portfolio_id: str, request_id: str) -> InstalledPlan | None:
        row = self.database.execute("SELECT plan_id FROM day_plans WHERE portfolio_id = ? AND request_id = ?",
                                    (portfolio_id, request_id)).fetchone()
        return None if row is None else self._record(row["plan_id"], created=False)

    def _latest(self, portfolio_id: str, symbol: str) -> InstalledPlan | None:
        row = self.database.execute(
            """SELECT plan_id FROM day_plans p WHERE portfolio_id = ? AND symbol = ? AND NOT EXISTS
               (SELECT 1 FROM day_plans n WHERE n.supersedes_plan_id = p.plan_id)""",
            (portfolio_id, symbol)).fetchone()
        return None if row is None else self._record(row["plan_id"], created=False)

    @staticmethod
    def _entry(row) -> PlanEntryRecord:
        return PlanEntryRecord(entry_id=row["entry_id"], position=row["position"], intent_id=row["intent_id"],
                               decision_id=row["decision_id"], quantity=row["quantity"],
                               limit_price=row["limit_price"], stop_price=row["stop_price"],
                               take_profit_price=row["take_profit_price"], expires_at=row["expires_at"])

    def _record(self, plan_id: str, *, created: bool) -> InstalledPlan:
        row = self.database.execute("SELECT * FROM day_plans WHERE plan_id = ?", (plan_id,)).fetchone()
        if row is None:
            raise NotFound(f"no day plan {plan_id}")
        plan = DayPlan.model_validate_json(row["document_json"])
        if plan.content_sha256 != row["document_sha256"]:
            raise ValidationFailure(f"the stored document of day plan {plan_id} does not match its hash")
        entries = tuple(self._entry(item) for item in self.database.execute(
            "SELECT * FROM day_plan_entries WHERE plan_id = ? ORDER BY position", (plan_id,)).fetchall())
        installed = self.database.execute(
            "SELECT document_json FROM day_plan_events WHERE plan_id = ? AND kind = 'installed'",
            (plan_id,)).fetchone()
        alarm_ids = tuple(json.loads(installed["document_json"]).get("alarm_ids", [])) if installed else ()
        return InstalledPlan(
            plan_id=plan_id, portfolio_id=row["portfolio_id"], request_id=row["request_id"], symbol=row["symbol"],
            trading_day_start=row["trading_day_start"], trading_day_end=row["trading_day_end"],
            strategy_ref=row["strategy_ref"], strategy_sha256=row["strategy_sha256"],
            practice_reports=tuple(json.loads(row["practice_reports"])),
            supersedes_plan_id=row["supersedes_plan_id"], policy_revision=row["policy_revision"],
            mandate_revision=row["mandate_revision"], task_id=row["task_id"], installed_at=row["installed_at"],
            document_sha256=row["document_sha256"], plan=plan, entries=entries, alarm_ids=alarm_ids,
            created=created)

    def _event(self, plan_id: str, kind: str, *, entry_id: str | None = None, reason: str | None = None,
               document: dict[str, Any] | None = None) -> None:
        with self.database.immediate() as connection:
            connection.execute(
                "INSERT INTO day_plan_events (plan_id, entry_id, kind, reason, document_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (plan_id, entry_id, kind, reason, json.dumps(document or {}, sort_keys=True),
                 utc_iso(self.clock.now())))


__all__ = ["SYSTEM_VERSION", "DayPlans", "InstalledPlan", "PlanEntryRecord"]
