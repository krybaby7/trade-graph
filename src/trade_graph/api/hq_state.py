"""HQ projection rules (spec §B.5): right now, team, rooms, quests, attention, decisions, money.

Everything shown is derived from persisted records gathered by ``hq_facts`` and the
``hq_money`` cache. Quiet means idle: nothing is "working" unless a task row says so.
The calendar and story modules read the derived facts from :class:`Projection`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trade_graph.api import hq_copy as c
from trade_graph.api.activity import _schedule_role
from trade_graph.api.hq_contract import (
    ACTIVE_SERVICE_STATUSES,
    AI_DEPARTMENTS,
    ATTENTION_SEVERITY,
    DEPARTMENT_ORDER,
    NOW_PRESENTATION,
    OFFLINE_NOW_CODES,
    PAUSED_WORK_PROFILES,
    QUEST_MAX_ITEMS,
    SEVERITY_ORDER,
    TURN_STUCK_MAX_CARDS,
    iso_ms,
)
from trade_graph.api.hq_facts import loads, parse, seconds_between
from trade_graph.application.paper_service import DEFAULT_SCHEDULES
from trade_graph.application.service_controller import automatic_optimisation_enabled

SCHEDULED_ROLES = ("trader", "research", "learning", "leader", "optimisation")
PROMPT_TURN_ROLES = ("trader", "research", "learning", "leader")
TERMINAL = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "DEAD_LETTER"})
STUCK = frozenset({"WAITING_EXTERNAL", "BLOCKED_BUDGET"})
FINISHED_QUESTS_PER_DEPT = 5  # finished jobs listed per department (payload budget, B18)
STALE_AFTER = timedelta(seconds=90)
OVERDUE_GRACE = 120
QUOTA_DEFERRAL_RECENT = 120
PROBLEM_PREFIXES = ("feed:", "reconciliation:", "pause:", "dispatch:", "scheduler:", "money_limits:", "day_plans:",
                    "collector:")
ACCOUNT_PROBLEM_PREFIXES = ("reconciliation:", "dispatch:", "pause:", "money_limits:", "day_plans:")
OWNER_PAUSE_RECOMMENDERS = ("repeat_problems", "prices_old", "orders_unclear")
# Leader pauses that protect more than the owner's "Pause AI" (protect-only); HQ never offers to weaken them.
STRONGER_PAUSES = ("CANCEL_ALL", "FLATTEN", "STOPPED")
CHECKIN_SUGGESTION_DELAY = timedelta(minutes=20)
ASSET_CODE = re.compile(r"^[A-Z0-9]{2,16}$")


def _dt(value) -> datetime | None:
    return value if isinstance(value, datetime) else parse(value)


def base_asset(symbol) -> str | None:
    """The coin of a pair such as ``ETH/USD`` ("ETH"), when it reads as an asset code."""
    if not isinstance(symbol, str):
        return None
    base = symbol.split("/", 1)[0]
    return base if ASSET_CODE.fullmatch(base) else None


def quote_asset(symbol) -> str | None:
    """The pricing currency of a pair such as ``ETH/USD`` ("USD")."""
    if not isinstance(symbol, str) or "/" not in symbol:
        return None
    quote = symbol.split("/", 1)[1]
    return quote if ASSET_CODE.fullmatch(quote) else None


def amount_and_unit(value, unit_key: str) -> tuple[Decimal | None, str | None]:
    """A persisted ``{"amount", <unit_key>}`` object (Quantity or Money) or a plain decimal string (C12)."""
    if isinstance(value, dict):
        unit = value.get(unit_key)
        unit = unit if isinstance(unit, str) and ASSET_CODE.fullmatch(unit) else None
        return c.decimal_or_none(value.get("amount")), unit
    return c.decimal_or_none(value), None


def _iso(value) -> str | None:
    moment = _dt(value)
    return None if moment is None else iso_ms(moment)


@dataclass
class Role:
    dept: str
    schedule: dict | None = None
    extra_rows: int = 0
    every: int | None = None
    next_at: datetime | None = None
    overdue: bool = False
    gate: str | None = None
    gate_text: list | None = None
    blocking_gate: str | None = None
    active: list = field(default_factory=list)
    recent: list = field(default_factory=list)
    working: dict | None = None
    stuck: list = field(default_factory=list)
    due: dict | None = None
    deferred: dict | None = None
    light: str = "resting"
    light_key: str = "resting"
    had_problem: bool = False
    check_in: dict | None = None  # with the event engine on: the check-in that sets ``next_at`` (D123)


class Projection:
    """Derived HQ facts for one snapshot."""

    def __init__(self, runtime, facts: dict, cache: dict, viewer, checkins: dict) -> None:
        self.runtime, self.facts, self.cache, self.viewer = runtime, facts, cache, viewer
        self.now: datetime = facts["now"]
        self.as_of = iso_ms(self.now)
        portfolio = facts["portfolio"] or {}
        self.mode = "live" if portfolio.get("mode") == "live" else "paper"
        self.paper = self.mode == "paper"
        self.started_at = _dt(portfolio.get("created_at")) or self.now
        self.reporting = portfolio.get("reporting_currency") or "EUR"
        self.owner = viewer.role == "owner"
        self.can_act = (viewer.role == "owner" and viewer.auth == "cookie" and viewer.csrf_present
                        and self.mode == "paper")
        self.checkin_facts = checkins
        self.config = facts["config"]
        self.auto_optimisation = bool(automatic_optimisation_enabled(self.config["settings"]))
        self.unclear_orders = facts["orders"]["unclear"]
        self.open_orders = facts["orders"]["open"]
        # Fresh on every poll: open orders (R17, the execution's outstanding states) plus cached holdings.
        nonflat = cache.get("nonflat_positions")
        self.flat = None if nonflat is None else (not nonflat and self.open_orders == 0)
        self.decision_by_task = {row["task_id"]: row for row in facts["decisions"] if row.get("task_id")}
        self.decision_by_id = {row["decision_id"]: row for row in facts["decisions"]}
        self.findings_by_id = {row["finding_id"]: row for row in facts["findings"]}
        self.schedule_by_id = {row["schedule_id"]: row for row in facts["schedules"]}
        self._service()
        self._pause()
        self._ai()
        self._allowance()
        self._roles()
        self._now_code()
        self._lights()
        self._notes()
        self._market_state()

    def use_story_inputs(self, overrides: dict) -> None:
        """Swap in the story readers for an earlier page, keeping the current state."""
        self.facts = {**self.facts, **overrides}
        self.decision_by_task = {row["task_id"]: row for row in self.facts["decisions"] if row.get("task_id")}
        self.decision_by_id = {row["decision_id"]: row for row in self.facts["decisions"]}

    # --- Team facts (§B.5.1) ---------------------------------------------------------------

    def _service(self) -> None:
        run = self.facts["run"]
        status = run["status"]
        self.status = status
        self.active = status in ACTIVE_SERVICE_STATUSES
        self.heartbeat = _dt(run.get("heartbeat_at"))
        self.requested = _dt(run.get("requested_at"))
        self.finished = _dt(run.get("finished_at"))
        self.run_id = run.get("run_id")
        stale = None
        if self.run_id is not None:
            stale = False
        if self.active:
            if self.heartbeat is None:
                stale = (status != "STARTING" or self.requested is None
                         or self.now - self.requested > STALE_AFTER)
            else:
                age = (self.now - self.heartbeat).total_seconds()
                stale = age > STALE_AFTER.total_seconds() or age < 0
        self.stale = stale
        self.problems = []
        if self.active and run.get("error_type"):
            for item in str(run["error_type"]).split(";"):
                item = item.strip()
                if not item:
                    continue
                prefix = next((known for known in PROBLEM_PREFIXES if item.startswith(known)), "other")
                code = item[len(prefix):] if prefix != "other" else item
                self.problems.append((prefix, c.technical(code) or "unknown"))
        self.scheduler_failing = any(prefix == "scheduler:" for prefix, _ in self.problems)
        self.running_fresh = self.active and self.stale is False

    def _pause(self) -> None:
        row = self.facts["pause"]
        profile = row["profile"] if row and row["profile"] in c.PAUSE_PROFILE else "RUNNING"
        self.profile = profile
        paused = profile != "RUNNING"
        originator = row["originator"] if row and paused else None
        self.originator = originator if originator in ("owner", "system", "leader") else None
        self.pause_since = _dt(row["requested_at"]) if row and paused else None
        self.achieved = row["achieved"] if row and paused else None
        self.pause_reason = row["reason"] if row and paused else None
        self.departments_paused = profile in PAUSED_WORK_PROFILES
        self.system_pause = paused and self.originator == "system"
        self.owner_pause = paused and self.originator == "owner"
        self.leader_pause = paused and self.originator == "leader"

    def _ai(self) -> None:
        providers = self.facts["providers"]
        paused = [row for row in providers if row["ai_paused"]]
        self.provider_paused = bool(paused)
        self.provider_reason = paused[0]["reason"] if paused else None
        model = [row for row in paused if row["reason"] == "subscription model unavailable"]
        quota = [row for row in paused if row["reason"] == "subscription quota exhausted"]
        self.deferred_tasks = [task for task in self.facts["active_tasks"]
                               if task["status"] == "QUEUED" and loads(task.get("output_json")).get(
                                   "_subscription_quota_deferred")]
        streaks = self.facts.get("deferrals") or []  # newest first
        self.newest_deferral = streaks[0] if streaks else None
        recent_deferral = any(0 <= (self.now - streak["last_at"]).total_seconds() <= QUOTA_DEFERRAL_RECENT
                              for streak in streaks)
        self.quota = self.facts["quota"]
        self.threshold = self.cache.get("threshold")
        self.windows, self.unreported = self._windows()
        remaining = [window["remaining_percent"] for window in self.windows
                     if window["remaining_percent"] is not None]
        self.min_remaining = min(remaining) if remaining else None
        self.reading_at = _dt(self.quota.get("observed_at")) if self.quota else None
        below = (self.threshold is not None and self.min_remaining is not None
                 and self.min_remaining <= self.threshold)
        self.ai_since = None
        if model:
            state, self.ai_since = "paused_model", _dt(model[0]["updated_at"])
        elif quota:
            state, self.ai_since = "paused_quota", _dt(quota[0]["updated_at"])
        elif self.deferred_tasks or recent_deferral:
            state = "waiting_for_allowance"
            self.ai_since = self.newest_deferral["first_at"] if self.newest_deferral else None
        elif below:
            state = "probably_low"
        elif self.quota:
            state = "ok_last_reading"
        else:
            state = "unknown"
        self.ai_state = state

    def _windows(self) -> tuple[list, list]:
        quota = self.quota or {}
        raw = quota.get("windows")
        if not isinstance(raw, dict):
            raw = {name: quota[name] for name in ("five_hour", "weekly") if name in quota}
        windows, unreported = [], set(quota.get("unavailable_windows") or [])
        for name, window in sorted(raw.items()):
            if not isinstance(window, dict):
                unreported.add(name)
                continue
            remaining = window.get("remaining_percent")
            used = window.get("used_percent")
            if remaining is None and used is not None:
                remaining = 100 - used
            duration = window.get("window_duration_mins")
            label = "weekly" if duration == 10080 else "5-hour" if duration == 300 else "other"
            resets = window.get("resets_at")
            windows.append({
                "name": name, "label": label, "duration_minutes": duration if type(duration) is int else None,
                "remaining_percent": remaining,
                "resets_at": iso_ms(datetime.fromtimestamp(resets, UTC)) if type(resets) is int else None,
            })
        return windows, sorted(unreported - {window["name"] for window in windows})

    def _allowance(self) -> None:
        if self.quota is None or self.threshold is None or self.min_remaining is None:
            status = "unknown"
        elif self.min_remaining <= self.threshold:
            status = "below_line"
        elif self.min_remaining <= self.threshold + 10:
            status = "near_line"
        else:
            status = "ok"
        self.allowance_status = status
        self.lowest_window = min((window for window in self.windows if window["remaining_percent"] is not None),
                                 key=lambda window: window["remaining_percent"], default=None)

    # --- Departments' next turns and gates (§B.5.3, §B.5.4) -----------------------------------

    def _roles(self) -> None:
        self.roles: dict[str, Role] = {}
        recent_by_role: dict[str, list] = {}
        for task in self.facts["recent_tasks"]:
            recent_by_role.setdefault(task["role"], []).append(task)
        for dept in AI_DEPARTMENTS:
            role = Role(dept)
            role.active = [task for task in self.facts["active_tasks"] if task["role"] == dept]
            role.recent = sorted(recent_by_role.get(dept, []), key=self.task_moment, reverse=True)
            role.working = next((task for task in role.active if task["status"] in {"LEASED", "RUNNING"}
                                 and self._lease_live(task)), None)
            role.stuck = [task for task in role.active if task["status"] in STUCK]
            role.deferred = next((task for task in self.deferred_tasks if task["role"] == dept), None)
            role.due = next((task for task in role.active if task["status"] == "QUEUED"
                             and task is not role.deferred and self._due(task)), None)
            if dept in SCHEDULED_ROLES:
                if self.facts.get("check_ins") is not None:
                    self._check_in_turn(role)
                else:
                    self._schedule(role)
            elif dept == "engineer":
                deadlines = [moment for task in role.active if (moment := _dt(task.get("deadline_at")))]
                role.next_at = min(deadlines, default=None)
                role.overdue = role.next_at is not None and role.next_at <= self.now
            role.blocking_gate = self._blocking_gate(dept)
            if role.overdue and dept != "engineer":
                role.gate, role.gate_text = self._gate(role)
            self.roles[dept] = role

    def _schedule(self, role: Role) -> None:
        """The schedule row that books this department's turns (spec §B.5.3, C6).

        Rows left over for a department the installation no longer schedules are ignored; an
        ``artifact-`` row, when there is one, always wins over the plain row it replaced.
        """
        rows = [row for row in self.facts["schedules"] if _schedule_role(row["name"]) == role.dept]
        intervals = self.config["schedule_intervals"]
        configured = set(DEFAULT_SCHEDULES if intervals is None else intervals)
        candidates = [row for row in rows if row["name"].startswith("artifact-") or role.dept in configured]
        if role.dept == "optimisation" and not self.auto_optimisation:
            candidates = []
        artifacts = [row for row in candidates if row["name"].startswith("artifact-")]
        candidates = artifacts or candidates
        if not candidates:
            return
        chosen = max(candidates, key=lambda row: (row["last_due_at"] or "", row["name"]))
        role.schedule, role.extra_rows = chosen, len(rows) - 1
        role.every = chosen["interval_seconds"] if chosen["interval_seconds"] and chosen["interval_seconds"] > 0 \
            else None
        role.next_at = _dt(chosen["next_due_at"])
        role.overdue = role.next_at is not None and role.next_at <= self.now

    def _check_in_turn(self, role: Role) -> None:
        """With the event engine on, a department's next turn is its own check-in (D123): the time it named, else the
        next day start, as ``EventEngine.next_check_ins`` reads it. The fixed schedule rows stop moving then, so they
        are not read (W6-C's follow-up, built in T45). Optimisation is woken only when it is not owner-requested-only.
        """
        if role.dept == "optimisation" and not self.auto_optimisation:
            return
        item = (self.facts.get("check_ins") or {}).get(role.dept)
        moment = _dt(item["at"]) if item else None
        if moment is None:
            return
        role.check_in = item
        role.next_at = moment
        role.overdue = item["basis"] == "named" and moment <= self.now

    def _lease_live(self, task: dict) -> bool:
        expiry = _dt(task.get("lease_expires_at"))
        return expiry is not None and expiry > self.now

    def _due(self, task: dict) -> bool:
        due = _dt(task.get("due_at"))
        return due is None or due <= self.now

    def _department_paused(self, dept: str) -> bool:
        return self.departments_paused and not (dept == "leader" and self.leader_pause)

    def _blocking_gate(self, dept: str) -> str | None:
        if not self.running_fresh:
            return "G1"
        if self.status == "MANAGEMENT_ONLY":
            return "GM"  # started while AI wasn't ready: turns run only if it becomes available (C13)
        if self.provider_paused:
            return "G2"
        if self.ai_state == "waiting_for_allowance":
            return "G3"
        if self.config["automatic_schedule"] is False:
            return "G4"
        if self._department_paused(dept):
            return "G5"
        if self.scheduler_failing:
            return "G6"
        return None

    def _gate(self, role: Role) -> tuple[str, list]:
        gate = role.blocking_gate
        if gate == "G1":
            return gate, c.says(c.GATE["G1"])
        if gate == "GM":
            return gate, c.says(c.GATE["GM"])
        if gate == "G2":
            return gate, c.says(c.GATE["G2"].format(reason=self.ai_pause_reason()))
        if gate == "G3":
            return gate, c.says(c.GATE["G3"])
        if gate == "G4":
            return gate, c.says(c.GATE["G4"])
        if gate == "G5":
            return gate, c.says(c.GATE["G5"].format(by=c.ORIGINATOR_SHORT.get(self.originator, "by you")))
        if gate == "G6":
            return gate, c.says(c.GATE["G6"])
        name = role.schedule["name"] if role.schedule else None
        if name and any(task["objective"] == name and task["status"] in {"QUEUED", "LEASED", "RUNNING"}
                        for task in role.active):
            return "G7", c.says(c.GATE["G7"])
        if self.ai_state == "probably_low":
            return "G8", c.says(c.GATE["G8"].format(p=c.pct(self.min_remaining), line=c.pct(self.threshold)),
                                self.reading_ago() + ".")
        overdue = (self.now - role.next_at).total_seconds()
        if overdue <= OVERDUE_GRACE:
            return "G9", c.says(c.GATE["G9"])
        return "G10", c.says(c.GATE["G10_before"], c.duration(overdue), c.GATE["G10_after"])

    def ai_pause_reason(self) -> str:
        return c.AI_PAUSE_REASON.get(self.provider_reason or "", c.AI_PAUSE_REASON_OTHER)

    def reading_ago(self) -> str:
        return c.ago((self.now - self.reading_at).total_seconds()) if self.reading_at else c.MISC["time_not_recorded"]

    # --- Right now (§B.5.2) -------------------------------------------------------------------

    def _now_code(self) -> None:
        status = self.status
        working = [role.working for role in self.roles.values() if role.working]
        due = [task for task in self.facts["active_tasks"] if task["status"] == "QUEUED"
               and self._due(task) and task not in self.deferred_tasks]
        overdue = any(role.overdue and role.next_at is not None
                      and (self.now - role.next_at).total_seconds() > OVERDUE_GRACE
                      for role in self.roles.values() if role.dept in SCHEDULED_ROLES)
        other_problem = any(prefix != "scheduler:" for prefix, _ in self.problems)
        if status in ("IDLE", "UNOBSERVED"):
            code = "no_run"
        elif status in ("FAILED", "INTERRUPTED"):
            code = "crashed"
        elif status == "STOPPED":
            code = "stopped"
        elif status == "ATTACHED_EXISTING":
            code = "attached_unknown"
        elif self.active and self.stale:
            code = "silent"
        elif status == "STOPPING":
            code = "stopping"
        elif self.system_pause:
            code = "safety_stop"
        elif self.owner_pause and self.profile == "STOPPED":
            code = "owner_stop_setting"
        elif self.owner_pause:
            code = "owner_pause"
        elif self.leader_pause:
            code = "leader_pause"
        elif self.ai_state == "paused_model":
            code = "model_unavailable"
        elif self.ai_state == "paused_quota":
            code = "quota_paused"
        elif self.scheduler_failing:
            code = "timetable_failing"
        elif other_problem:
            code = "routine_failing"
        elif self.unclear_orders > 0:
            code = "orders_unclear"
        elif self.ai_state == "waiting_for_allowance":
            code = "waiting_allowance"
        elif working:
            code = "working"
        elif due:
            code = "starting_turn"
        elif self.ai_state == "probably_low" and overdue:
            code = "probably_low_allowance"
        else:
            code = "calm"
        self.code = code
        self.offline = code in OFFLINE_NOW_CODES
        self.working_task = working[0] if working else None
        self.due_task = due[0] if due else None

    def _lights(self) -> None:
        for role in self.roles.values():
            role.had_problem = self._had_problem(role)
            if self.offline:
                role.light, role.light_key = "off", "off"
            elif role.stuck:
                role.light, role.light_key = "stuck", "stuck"
            elif role.working:
                role.light, role.light_key = "working", "working"
            elif role.due or role.deferred:
                role.light = "waiting"
                role.light_key = "waiting_allowance" if role.deferred else "waiting"
            elif self.provider_paused:
                role.light = "paused"
                role.light_key = {"paused_model": "paused_model", "paused_quota": "paused_quota"}.get(
                    self.ai_state, "paused_provider")
            elif self._department_paused(role.dept):
                role.light, role.light_key = "paused", "paused_" + (self.originator or "owner")
            elif not role.active and (role.dept == "engineer"
                                      or (role.dept == "optimisation" and not self.auto_optimisation)):
                role.light, role.light_key = "on_call", "on_call"
            else:
                role.light, role.light_key = "resting", "resting"

    def _had_problem(self, role: Role) -> bool:
        for task in role.recent:
            if task["status"] not in TERMINAL or self.is_skip(task):
                continue
            return task["status"] in {"FAILED", "DEAD_LETTER"}
        return False

    # --- Research notes (attention, heads-up) --------------------------------------------------

    def _notes(self) -> None:
        findings = self.facts["findings"]
        self.notes_in_use = [row for row in findings if row.get("fresh")]
        trader = self.roles["trader"]
        trader_next = trader.next_at
        expiries = [moment for row in findings if (moment := _dt(row.get("expires_at")))]
        self.latest_note_expiry = max(expiries, default=None)
        in_use_expiries = [moment for row in self.notes_in_use if (moment := _dt(row.get("expires_at")))]
        condition_a = bool(self.notes_in_use) and trader_next is not None and all(
            moment < trader_next for moment in in_use_expiries)
        last_turn = self.trader_last_turn_at()
        condition_b = False
        if last_turn is not None:
            window = [row for row in findings
                      if (created := _dt(row.get("created_at"))) and last_turn - timedelta(hours=24) <= created
                      <= last_turn]
            used = [row for row in window if (available := _dt(row.get("available_at"))) and available <= last_turn
                    and (expires := _dt(row.get("expires_at"))) and expires > last_turn
                    and row.get("relevance") != "untrusted-page"]
            condition_b = bool(window) and not used
        self.notes_expire_early = condition_a or condition_b
        self.notes_missing_next = (trader_next is not None and self.latest_note_expiry is not None
                                   and not any(moment > trader_next for moment in in_use_expiries))
        candidates = in_use_expiries if condition_a else expiries
        self.notes_expiry = max(candidates, default=None)

    def trader_last_turn_at(self) -> datetime | None:
        moments = [moment for row in self.facts["decisions"] if (moment := _dt(row["created_at"]))]
        tasks = [self.task_moment(task) for task in self.roles["trader"].recent if task["status"] in TERMINAL]
        return max([*moments, *[moment for moment in tasks if moment]], default=None)

    # --- Market light -------------------------------------------------------------------------

    def _market_state(self) -> None:
        marks = self.facts["marks"]
        observed = [moment for row in marks.values() if (moment := _dt(row.get("observed_at")))]
        self.last_price_at = max(observed, default=None)
        poll = self.config.get("public_poll_interval_seconds") or 10
        limit = max(60, 3 * int(poll))
        self.price_age = seconds_between(self.now, self.last_price_at)
        self.prices_old = self.price_age is not None and self.price_age > limit
        # A previous run's feed problem is not this run's: the worker resets its counters on restart and
        # never writes "recovered" for the old run (C2).
        newest = next((event for event in self.facts["events"] if event["kind"] == "public_feed_health"), None)
        degraded = (newest is not None and self.active and self.run_id is not None
                    and newest["payload"].get("status") == "degraded" and self.event_run(newest) == self.run_id)
        self.feed_degraded = degraded
        if self.offline:
            self.market_light = "off"
        elif self.prices_old or degraded:
            self.market_light = "attention"
        else:
            self.market_light = "ok"
        self.history_event = next((event for event in self.facts["events"]
                                   if event["kind"] == "public_history_refresh"), None)

    # --- Shared helpers ------------------------------------------------------------------------

    def event_run(self, event: dict) -> str | None:
        """The team run an activity event belongs to: its ``run_id``, else the newest run requested before it."""
        run_id = event["payload"].get("run_id")
        if isinstance(run_id, str) and run_id:
            return run_id
        moment = _dt(event.get("created_at"))
        if moment is None:
            return None
        if self.run_id is not None and self.requested is not None and moment >= self.requested:
            return self.run_id
        for run in self.facts["runs"]:  # newest first
            requested = _dt(run.get("requested_at"))
            if requested is not None and requested <= moment:
                return run["run_id"]
        return None

    def run_end(self, run_id: str | None) -> datetime | None:
        """When a recorded run ended: its finish time, otherwise its last sign of life."""
        if run_id is None:
            return None
        if run_id == self.run_id:
            return self.finished or self.heartbeat
        run = next((row for row in self.facts["runs"] if row["run_id"] == run_id), None)
        return (_dt(run.get("finished_at")) or _dt(run.get("heartbeat_at"))) if run else None

    def task_moment(self, task: dict) -> datetime:
        return _dt(task.get("result_created_at")) or _dt(task.get("created_at")) or self.started_at

    def is_skip(self, task: dict) -> bool:
        return task["status"] == "DEAD_LETTER" and self.skip_key(task) is not None

    def skip_key(self, task: dict) -> str | None:
        return c.SKIP_REASONS.get(loads(task.get("output_json")).get("reason"))

    def raw_reason(self, task: dict):
        output = loads(task.get("output_json"))
        result = loads(task.get("result_json"))
        return output.get("reason") or result.get("reason") or result.get("error")

    def task_cadence(self, task: dict) -> str | None:
        key = task.get("dedup_key") or ""
        if key.startswith("schedule:"):
            parts = key.split(":", 2)
            schedule = self.schedule_by_id.get(parts[1]) if len(parts) == 3 else None
            if schedule:
                return c.cadence(schedule["interval_seconds"])
        role = self.roles.get(task["role"])
        if role and role.every and (key.startswith("schedule:") or str(task.get("objective", "")).endswith(
                "-review")):
            return c.cadence(role.every)
        return None

    def origin(self, task: dict) -> tuple[str, list]:
        key = task.get("dedup_key") or ""
        objective = str(task.get("objective") or "")
        if key.startswith("schedule:") or objective.endswith("-review"):
            cadence = self.task_cadence(task)
            words = c.ORIGIN["schedule"].format(cadence=cadence) if cadence else c.ORIGIN["schedule_plain"]
            return "schedule", c.says(words)
        if key.startswith("secretary:"):
            return "secretary", c.says(c.ORIGIN["secretary"])
        if objective == c.FOLLOWUP_OBJECTIVE:
            return "followup", c.says(c.ORIGIN["followup"])
        if objective == c.OPTIMISATION_OBJECTIVE:
            return "owner", c.says(c.ORIGIN["owner"])
        if key.startswith("commission:"):
            return "commission", c.says(c.ORIGIN["commission"])
        return "leader", c.says(c.ORIGIN["leader"].format(objective=c.cut(c.technical(objective, 400) or "", 80)))

    def state_text(self, task: dict) -> list:
        status = task["status"]
        output = loads(task.get("output_json"))
        deadline = _dt(task.get("deadline_at"))
        if status not in TERMINAL and status not in STUCK and deadline is not None and deadline <= self.now:
            return c.says(c.TASK_STATE["past_deadline"])
        if status == "QUEUED":
            due = _dt(task.get("due_at"))
            if output.get("_subscription_quota_deferred"):
                return c.says(c.TASK_STATE["queued_allowance"])
            if due is None or due <= self.now:
                return c.says(c.TASK_STATE["queued_due"])
            if output.get("reason") == "discretionary work paused":
                return c.says(c.TASK_STATE["queued_paused"])
            return c.says(c.TASK_STATE["queued_later"], c.time(due, "relative_clock"))
        if status in {"LEASED", "RUNNING"}:
            if not self._lease_live(task):
                return c.says(c.TASK_STATE["lease_lost"])
            if status == "LEASED":
                return c.says(c.TASK_STATE["leased"])
            return c.says(c.TASK_STATE["running"].format(used=task["attempts_used"], max=task["max_attempts"]))
        if status in STUCK:
            return c.says(c.TASK_STATE[status])
        if status == "SUCCEEDED":
            decision = self.task_decision(task)
            if task["role"] == "trader" and decision is not None:
                words = c.DECISION_ACTION.get(decision["action"], decision["action"].replace("_", " "))
                capital = words[:1].upper() + words[1:]
                if decision["action"] == "no_action":
                    return [c.term("wait", capital, code=decision["action"])]
                return [c.text(capital, code=decision["action"])]
            return c.says(c.TASK_STATE["SUCCEEDED"])
        if status == "DEAD_LETTER":
            skip = self.skip_key(task)
            if skip:
                return c.says(c.TASK_STATE[skip])
            return c.says(c.TASK_STATE["DEAD_LETTER"].format(max=task["max_attempts"]))
        return c.says(c.TASK_STATE.get(status, status.replace("_", " ").capitalize()))

    def task_decision(self, task: dict) -> dict | None:
        decision = self.decision_by_task.get(task["task_id"])
        if decision is None:
            decision_id = loads(task.get("output_json")).get("decision_id")
            decision = self.decision_by_id.get(decision_id) if decision_id else None
        return decision

    def decision_symbols(self, payload: dict) -> list[str]:
        symbols = []
        if isinstance(payload.get("symbol"), str):
            symbols.append(payload["symbol"])
        for action in payload.get("actions") or []:
            if isinstance(action, dict) and isinstance(action.get("symbol"), str):
                symbols.append(action["symbol"])
        if not symbols:
            mandate = self.cache.get("mandate") or {}
            symbols = list(mandate.get("symbols") or [])
        return list(dict.fromkeys(symbols))

    def symbols_text(self, symbols: list[str]) -> str:
        names = list(dict.fromkeys(symbol.split("/")[0] for symbol in symbols if symbol))
        if not names:
            return c.MISC["the_market"]
        return names[0] if len(names) == 1 else c.MISC["list_separator"].join(names[:-1]) + c.MISC["and"] + names[-1]

    def decision_quote(self, payload: dict) -> dict | None:
        return c.quote(payload.get("rationale") or payload.get("no_action_reason"))

    def link_for(self, task: dict) -> str:
        decision = self.task_decision(task) if task["role"] == "trader" else None
        if decision is not None:
            return f"/decisions/{decision['decision_id']}"
        return "/changes" if task["role"] == "engineer" else "/organization"


# --- Snapshot sections ---------------------------------------------------------------------------


def account(p: Projection) -> dict:
    days = int((p.now - p.started_at).total_seconds() // 86400)
    return {"started_at": iso_ms(p.started_at), "day_number": max(1, days + 1), "reporting_currency": p.reporting}


def viewer(p: Projection) -> dict:
    reason = None
    if not p.can_act:
        reason = c.says(c.LIVE_MODE if p.mode == "live" else c.READ_ONLY)
    return {"role": p.viewer.role, "auth": p.viewer.auth, "can_act": p.can_act, "act_block_reason": reason,
            "owner_revision": p.facts["owner_revision"] if p.owner else None}


def team(p: Projection) -> dict:
    return {"service": _service_block(p), "pause": _pause_block(p), "ai": _ai_block(p),
            "allowance": _allowance_block(p), "automatic_turns": bool(p.config["automatic_schedule"]),
            "automatic_optimisation": p.auto_optimisation}


def _service_block(p: Projection) -> dict:
    problems = []
    for prefix, code in p.problems:
        words = c.problem_prefix(prefix, live=not p.paper)[0]
        problems.append({"prefix": prefix, "code": code,
                         "text": [c.text(words[:1].upper() + words[1:], code=f"{prefix}{code}"
                                         if prefix != "other" else code)]})
    return {"status": p.status, "active": p.active, "run_id": p.run_id,
            "started_at": _iso(p.requested) if p.run_id else None,
            "last_sign_of_life_at": _iso(p.heartbeat),
            "silent_seconds": seconds_between(p.now, p.heartbeat), "stale": p.stale, "problems": problems,
            "plain": [c.text(c.SERVICE_STATUS[p.status], code=p.status)]}


def _pause_block(p: Projection) -> dict:
    reason = None
    if p.system_pause:
        reason = c.says(c.SAFETY["reason"].format(trigger=safety_trigger(p)))
    elif p.owner_pause:
        reason = c.says(c.PAUSED_BY["owner"])
    elif p.leader_pause:
        quoted = c.quote(p.pause_reason, 160)
        reason = c.says(c.PAUSED_BY["leader_quote"].format(quote=quoted["text"]) if quoted
                        else c.PAUSED_BY["leader"])
    return {"profile": p.profile, "originator": p.originator, "since": _iso(p.pause_since),
            "achieved": p.achieved, "departments_paused": p.departments_paused,
            "plain": [c.text(c.PAUSE_PROFILE[p.profile], code=p.profile)], "reason": reason}


ROLE_UNAVAILABLE_REASON = "role unavailable; manage positions"  # paper_service.py, after a failed turn
MANAGEMENT_SERVICE_REASON = "Subscription management service"  # subscription_management.py (prefix)
SAFETY_EVIDENCE_BEFORE = timedelta(minutes=10)
SAFETY_EVIDENCE_AFTER = timedelta(seconds=5)


def safety_trigger(p: Projection) -> str:
    """What triggered the automatic stop, in plain words, from evidence close to it in time (C8).

    Only the worker's "role unavailable" stop is explained by a task problem, and only by one
    recorded from 10 minutes before the stop to 5 seconds after it. Anything else is unknown.
    """
    reason = p.pause_reason or ""
    if reason.startswith(MANAGEMENT_SERVICE_REASON):
        return c.SAFETY["management_service"]
    if reason != ROLE_UNAVAILABLE_REASON or p.pause_since is None:
        return c.SAFETY["unknown"]
    since = p.pause_since
    for task in p.facts["recent_tasks"]:
        moment = p.task_moment(task)
        if not since - SAFETY_EVIDENCE_BEFORE <= moment <= since + SAFETY_EVIDENCE_AFTER:
            continue
        if task["status"] == "BLOCKED_BUDGET":
            return c.SAFETY["out_of_allowance"].format(dept=c.dept_name(task["role"])) if task["role"] in c.ROOM \
                else c.SAFETY["out_of_allowance_plain"]
        if task["role"] == "trader" and task["status"] in {"FAILED", "WAITING_EXTERNAL", "DEAD_LETTER"} \
                and not p.is_skip(task):
            return c.SAFETY["trader_failed"]
    return c.SAFETY["unknown"]


def _ai_block(p: Projection) -> dict:
    clears = {"paused_model": False, "paused_quota": True}.get(p.ai_state)
    return {"state": p.ai_state, "plain": c.says(c.AI_STATE[p.ai_state]), "clears_by_itself": clears,
            "since": _iso(p.ai_since)}


def _allowance_block(p: Projection) -> dict:
    return {"status": p.allowance_status, "observed_at": _iso(p.reading_at),
            "age_seconds": seconds_between(p.now, p.reading_at), "stop_line_percent": p.threshold,
            "windows": p.windows, "unreported_windows": p.unreported, "plain": allowance_plain(p),
            "stop_line_state": stop_line_state(p)}


def stop_line_state(p: Projection) -> str:
    """The reserve rule: read and on, read and off, or unreadable, which is never "none" (contract 1.1)."""
    return {True: "on", False: "off"}.get(p.cache.get("threshold_enabled"), "unknown")


def allowance_plain(p: Projection) -> list:
    line = c.ALLOWANCE_LINE.format(line=c.pct(p.threshold)) if p.threshold is not None else None
    if not p.windows or p.min_remaining is None:
        # A reading whose only windows are unreported is not "no reading" (spec §E.2).
        first = c.ALLOWANCE_NONE_REPORTED if p.unreported else c.ALLOWANCE_NO_READING
        return c.join(c.says(first), c.says(line) if line else None)
    parts = []
    for window in p.windows:
        if window["remaining_percent"] is None:
            continue
        duration = c.span((window["duration_minutes"] or 0) * 60)
        left = c.ALLOWANCE_LEFT[window["label"]].format(p=c.pct(window["remaining_percent"]), duration=duration)
        if window["resets_at"]:
            parts.append(c.says(left + ". " + c.ALLOWANCE_REFILLS, c.time(window["resets_at"], "day_clock"), "."))
        else:
            parts.append(c.says(left + "."))
    if p.unreported:
        parts.append(c.says(c.ALLOWANCE_UNREPORTED))
    if p.cache.get("threshold_enabled") is False:
        parts.append(c.says(c.ALLOWANCE_NO_RULE))
    return c.join(*parts)


# --- Right now -------------------------------------------------------------------------------------


def now_block(p: Projection, attention_items: list) -> dict:
    tone, light, primary = NOW_PRESENTATION[p.code]
    headline, additions, if_nothing, since = _now_copy(p)
    count = sum(item["sev"] != "info" for item in attention_items)
    detail = c.join(additions, _now_facts(p, count))
    if primary == "start_trading" and not p.can_act:
        primary = None
    if primary == "resume" and not p.can_act:
        primary = None
    return {"code": p.code, "tone": tone, "light": light, "headline": headline, "detail": detail,
            "if_nothing": if_nothing or None, "since": _iso(since), "primary_decision": primary,
            "attention_count": count}


def _now_copy(p: Projection) -> tuple[list, list, list, datetime | None]:
    code = p.code
    copy = c.NOW
    if code == "no_run":
        head, detail, nothing = copy["no_run"]
        return c.says(head), c.says(detail), c.says(nothing), None
    if code == "crashed":
        moment = p.finished or p.heartbeat
        last = p.heartbeat or p.finished
        head = c.says(copy["crashed"][0], c.time(moment, "relative"), ".") if moment else \
            c.says(copy["crashed"][0].rstrip() + ".")
        detail = (c.says(copy["crashed"][1], c.time(last, "relative"), copy["crashed_detail_after"]) if last
                  else c.says(copy["crashed_unknown"]))
        return head, detail, c.says(copy["crashed"][2]), last
    if code == "stopped":
        head = (c.says(copy["stopped"][0], c.time(p.finished, "day_clock"), ").") if p.finished
                else c.says(c.MISC["switched_off"]))
        return head, c.says(copy["stopped"][1]), c.says(copy["stopped"][2]), p.finished
    if code == "attached_unknown":
        head, detail, nothing = copy["attached_unknown"]
        return c.says(head), c.says(detail), c.says(nothing), None
    if code == "silent":
        head = (c.says(copy["silent"][0], c.duration((p.now - p.heartbeat).total_seconds()), ".")
                if p.heartbeat else c.says(copy["silent_never"]))
        return head, c.says(copy["silent"][1]), c.says(copy["silent"][2]), p.heartbeat or p.requested
    if code == "stopping":
        return c.says(copy["stopping"][0]), [], c.says(copy["stopping"][2]), None
    if code == "safety_stop":
        head = c.says(copy["safety_stop"][0].format(profile=c.PAUSE_SHORT[p.profile]),
                      c.time(p.pause_since, "relative") if p.pause_since else None, ".")
        detail = c.says(copy["safety_stop"][1] + " " + c.PAUSE_CONSEQUENCE[p.profile])
        return head, detail, c.says(copy["safety_stop"][2]), p.pause_since
    if code == "owner_stop_setting":
        head, detail, nothing = copy["owner_stop_setting"]
        return c.says(head), c.says(detail), c.says(nothing), p.pause_since
    if code == "owner_pause":
        head = c.says(copy["owner_pause"][0], c.time(p.pause_since, "relative") if p.pause_since else None,
                      f" ({c.PAUSE_SHORT[p.profile]}).")
        return head, c.says(copy["owner_pause"][1]), c.says(copy["owner_pause"][2]), p.pause_since
    if code == "leader_pause":
        head = c.says(copy["leader_pause"][0], c.time(p.pause_since, "relative") if p.pause_since else None, ".")
        quoted = c.quote(p.pause_reason, 160)
        detail = c.says(copy["leader_pause"][1] + "“" + quoted["text"] + "”") if quoted else []
        return head, detail, c.says(copy["leader_pause"][2]), p.pause_since
    if code in ("model_unavailable", "quota_paused", "timetable_failing", "orders_unclear"):
        head, detail, nothing = copy[code]
        if code == "orders_unclear" and not p.paper:
            head = c.LIVE_NOW["orders_unclear"]
        since = p.ai_since if code in ("model_unavailable", "quota_paused") else None
        return c.says(head), c.says(detail), c.says(nothing), since
    if code == "routine_failing":
        prefix = next(prefix for prefix, _ in p.problems if prefix != "scheduler:")
        words, consequence = c.problem_prefix(prefix, live=not p.paper)
        return (c.says(copy["routine_failing"][0].format(problem=words)), c.says(consequence),
                c.says(consequence), None)
    if code == "waiting_allowance":
        dept = _waiting_dept(p)
        head, detail, nothing = copy["waiting_allowance"]
        return c.says(head.format(dept=dept)), c.says(detail), c.says(nothing), p.ai_since
    if code == "working":
        task = p.working_task
        dept = c.dept_name(task["role"]) if task["role"] in c.ROOM else c.MISC["a_department"]
        started = p.facts["attempt_starts"].get(task["task_id"])
        tries = copy["working_try"].format(used=task["attempts_used"], max=task["max_attempts"])
        detail = (c.says(copy["working"][1], c.time(started, "relative"), tries + ".") if started
                  else c.says(copy["working_unknown"] + tries + "."))
        return (c.says(copy["working"][0].format(dept=dept)), detail, c.says(copy["working"][2]),
                _dt(started) or _dt(task["created_at"]))
    if code == "starting_turn":
        task = p.due_task
        dept = c.dept_name(task["role"]) if task["role"] in c.ROOM else c.MISC["a_department"]
        return (c.says(copy["starting_turn"][0].format(dept=dept)), [], [],
                _dt(task.get("due_at")) or _dt(task["created_at"]))
    if code == "probably_low_allowance":
        head, detail, nothing = copy["probably_low_allowance"]
        return (c.says(head), c.says(detail.format(p=c.pct(p.min_remaining), line=c.pct(p.threshold)),
                                     p.reading_ago() + copy["probably_low_after"]), c.says(nothing), p.reading_at)
    return (*_calm_copy(p), None)


def _waiting_dept(p: Projection) -> str:
    task = p.deferred_tasks[0] if p.deferred_tasks else None
    role = task["role"] if task else (p.newest_deferral or {}).get("role")
    return c.dept_name(role) if role in c.ROOM else c.MISC["a_department"]


def next_turn(p: Projection) -> tuple[str, datetime] | None:
    upcoming = [(role.next_at, role.dept) for role in p.roles.values()
                if role.dept in SCHEDULED_ROLES and role.next_at is not None and role.next_at > p.now]
    if not upcoming:
        return None
    at, dept = min(upcoming)
    return dept, at


def _calm_copy(p: Projection) -> tuple[list, list, list]:
    head, next_label, nothing = c.NOW["calm"]
    upcoming = next_turn(p)
    if upcoming is None:
        none = c.says(c.NOW["calm_none"])
        if p.status == "MANAGEMENT_ONLY":
            none = c.join(c.says(c.NOW["management_only"]), none)
        return c.says(head), none, []
    dept, at = upcoming
    detail = c.says(next_label + c.TURN_PHRASE[dept] + " ", c.time(at, "relative_clock"), ".")
    if p.status == "MANAGEMENT_ONLY":
        detail = c.join(c.says(c.NOW["management_only"]), detail)
    if dept == "trader":
        if_nothing = c.says(nothing + "at ", c.time(at, "clock"), c.NOW["calm_trader"])
    else:
        cadence = c.cadence(p.roles[dept].every)
        other = (c.NOW["calm_other"].format(dept=c.dept_name(dept), cadence=cadence) if cadence
                 else c.NOW["calm_other_plain"].format(dept=c.dept_name(dept)))
        if_nothing = c.says(nothing + other, c.time(at, "clock"), ".")
    return c.says(head), detail, if_nothing


def _now_facts(p: Projection, count: int) -> list:
    cache = p.cache
    label = c.FACTS["practice_money"] if p.paper else c.FACTS["account_value"]
    value = c.money(cache.get("equity"), p.reporting) if cache.get("available") else None
    money_part = c.says(label, c.money_segment(value["amount"], p.reporting)) if value else \
        c.says(label + c.FACTS["money_unknown"])
    coins = c.decimal_or_none(cache.get("inventory")) if cache.get("available") else None
    if coins is None:
        holdings = c.says(c.FACTS["holdings_unknown"])
    elif coins == 0 and p.flat is not False:
        holdings = c.says(c.FACTS["nothing_held"])
    else:
        holdings = c.says(c.money_segment(coins, p.reporting), c.FACTS["in_coins"])
    if p.min_remaining is None:
        allowance = c.says(c.term("allowance", c.FACTS["allowance"]), c.FACTS["allowance_unknown"])
    elif p.threshold is not None:
        allowance = c.says(c.term("allowance", c.FACTS["allowance"]), c.FACTS["allowance_reading"].format(
            p=c.pct(p.min_remaining), line=c.pct(p.threshold), ago=p.reading_ago()))
    else:
        allowance = c.says(c.term("allowance", c.FACTS["allowance"]), c.FACTS["allowance_no_line"].format(
            p=c.pct(p.min_remaining), ago=p.reading_ago()))
    parts = [money_part, holdings, allowance]
    if count:
        parts.append(c.says(c.plural(c.FACTS["needs_you"], count)))
    return c.says(*c.join(*parts, sep=" · "), ".")


# --- Attention (§B.5.7) --------------------------------------------------------------------------


def _card(code: str, title: list, *, why=None, consequence=None, how=None, decision=None, checkin_id=None,
          link=None, since=None, can_fix_here=False, suffix: str | None = None) -> dict:
    sev = ATTENTION_SEVERITY[code]
    return {"id": code if suffix is None else f"{code}:{suffix}", "sev": sev, "title": title,
            "why": why or [], "consequence": consequence or [], "can_fix_here": can_fix_here, "how": how,
            "decision": decision, "checkin_id": checkin_id,
            "link": {"href": link, "label": c.LINK_LABEL[link]} if link else None,
            "in_next_prompt": sev != "info" and code != "checkin_due", "since": _iso(since)}


def attention(p: Projection, checkins: list[dict], extra: list[dict] | tuple = ()) -> list:
    """Needs you. ``extra`` adds cards projected elsewhere (the AI spending alerts, ``hq_spending``)."""
    items = []
    act = p.can_act
    owner_link = "/owner" if p.owner else None
    if p.code in ("no_run", "crashed", "stopped"):
        title, why, consequence = c.ATTENTION["team_off"]
        items.append(_card("team_off", c.says(title), why=c.says(why), consequence=c.says(consequence),
                           decision="start_trading" if act else None, can_fix_here=act,
                           since=p.finished or p.heartbeat))
    if p.code in ("silent", "attached_unknown"):
        title, why, consequence = c.ATTENTION["team_silent"]
        if p.code == "silent" and p.heartbeat:
            heading = c.says(title, c.duration((p.now - p.heartbeat).total_seconds()))
            how = c.says(c.ATTENTION["team_silent_how"])
        else:
            heading = c.says(c.NOW["attached_unknown"][0] if p.code == "attached_unknown" else c.NOW["silent_never"])
            how = c.says(c.ATTENTION["team_attached_how"] if p.code == "attached_unknown"
                         else c.ATTENTION["team_silent_how"])
        items.append(_card("team_silent", heading, why=c.says(why), consequence=c.says(consequence), how=how,
                           link="/progress", since=p.heartbeat))
    if p.code == "safety_stop":
        title = c.says(c.ATTENTION["safety_stop"], c.time(p.pause_since, "clock") if p.pause_since else None)
        why = c.says(c.SAFETY["why"].format(trigger=safety_trigger(p), profile=c.PAUSE_SHORT[p.profile]))
        items.append(_card("safety_stop", title, why=why, consequence=c.says(c.PAUSE_CONSEQUENCE[p.profile]),
                           how=c.says(c.ATTENTION["safety_stop_how"]), link=owner_link, since=p.pause_since))
    if p.ai_state == "paused_model":
        title, why, consequence = c.ATTENTION["model_unavailable"]
        items.append(_card("model_unavailable", c.says(title), why=c.says(why), consequence=c.says(consequence),
                           how=c.says(c.ATTENTION["model_unavailable_how"]), since=p.ai_since))
    if p.scheduler_failing:
        title, why = c.ATTENTION["timetable_failing"]
        items.append(_card("timetable_failing", c.says(title), why=c.says(why),
                           how=c.says(c.ATTENTION["in_prompt"])))
    stuck = sorted((task for task in p.facts["active_tasks"] if task["status"] in STUCK),
                   key=lambda task: task["created_at"], reverse=True)
    for task in stuck[:TURN_STUCK_MAX_CARDS]:
        dept = c.dept_name(task["role"]) if task["role"] in c.ROOM else c.MISC["a_department"]
        # The state text already says it won't retry by itself; no second sentence repeating it (U7).
        items.append(_card("turn_stuck", c.says(c.ATTENTION["turn_stuck"].format(dept=dept)),
                           why=p.state_text(task), consequence=[],
                           how=c.says(c.ATTENTION["turn_stuck_how"]), link="/organization",
                           since=task["created_at"], suffix=c.technical(task["task_id"], 100).replace(" ", "_")))
    due = [item for item in checkins if item["state"] in ("due", "overdue")]
    if due:
        first = min(due, key=lambda item: item["planned_at"])
        items.append(_card("checkin_due", c.says(c.ATTENTION["checkin_due"].format(
            title=first["title"], state=first["state"])), why=c.says(c.ATTENTION["checkin_due_why"]),
            checkin_id=first["checkin_id"], can_fix_here=True, since=first["planned_at"]))
    if p.unclear_orders:
        forms = c.ATTENTION["orders_unclear"] if p.paper else c.LIVE_ATTENTION["orders_unclear"]
        items.append(_card("orders_unclear", c.says(c.plural(forms, p.unclear_orders)),
                           why=c.says(c.ATTENTION["orders_unclear_why"]),
                           consequence=c.says(c.ATTENTION["orders_unclear_consequence"]), link="/trading"))
    others = [(prefix, code) for prefix, code in p.problems if prefix != "scheduler:"]
    for index, (prefix, code) in enumerate(dict.fromkeys(others)):
        words, consequence = c.problem_prefix(prefix, live=not p.paper)
        suffix = None if index == 0 else (prefix.rstrip(":") if prefix != "other" else f"other_{index}")
        items.append(_card("routine_failing", [c.text(words[:1].upper() + words[1:], code=code)],
                           consequence=c.says(consequence), suffix=suffix))
    items.extend(_repeat_problems(p))
    if p.notes_expire_early:
        trader_next = p.roles["trader"].next_at
        consequence = (c.says(c.ATTENTION["notes_expire_early_at"], c.time(trader_next, "clock"),
                              c.ATTENTION["notes_expire_early_after"]) if trader_next else [])
        items.append(_card("notes_expire_early", c.says(c.ATTENTION["notes_expire_early"]),
                           why=c.says(c.ATTENTION["notes_expire_early_why"]), consequence=consequence,
                           how=c.says(c.ATTENTION["nothing_now"]), since=p.notes_expiry))
    if p.market_light == "attention" and p.prices_old and p.price_age is not None:
        before, after = c.MISC["prices_old"]
        items.append(_card("prices_old", c.says(before, c.duration(p.price_age), after),
                           why=c.says(c.ATTENTION["prices_old_why"]),
                           consequence=c.says(c.ATTENTION["prices_old_consequence"]), since=p.last_price_at))
    if p.allowance_status in ("near_line", "below_line"):
        items.append(_allowance_card(p))
    if p.owner:
        for row in p.facts["commands"]:
            recovery = row.get("recovery") if isinstance(row.get("recovery"), dict) else {}
            if row.get("phase") == "RECOVERED" and recovery.get("needs_review"):
                title, why, consequence = c.ATTENTION["command_interrupted"]
                items.append(_card("command_interrupted", c.says(title), why=c.says(why),
                                   consequence=c.says(consequence), link="/owner",
                                   since=row.get("recovered_at") or row.get("created_at")))
                break
    for index, warning in enumerate(p.facts["resources"].get("warnings") or []):
        items.append(_card("resources", c.says(c.RESOURCE_WARNING.get(warning, c.RESOURCE_OTHER)),
                           suffix=None if index == 0 else str(index + 1),
                           since=p.facts["resources"].get("observed_at")))
    if p.owner_pause:
        items.append(_card("owner_paused", c.says(c.ATTENTION["owner_paused"]),
                           consequence=c.says(c.ATTENTION["owner_paused_consequence"]),
                           decision="resume" if act and p.originator != "system" else None, can_fix_here=act,
                           since=p.pause_since))
    codes = p.cache.get("codes") or []
    if "subscription_bills_unconfirmed" in codes or "other_costs_unconfirmed" in codes:
        key = "costs_unconfirmed" if "subscription_bills_unconfirmed" in codes else "costs_unconfirmed_other"
        title, why = c.ATTENTION[key]
        consequence = (c.says(c.ATTENTION["costs_gap"].format(day=c.day_text(p.started_at)))
                       if "history_gap" in codes else [])
        how, link = None, None
        if not act:
            if p.mode == "live":
                how, link = c.says(c.ATTENTION["costs_live_how"]), owner_link
            else:
                how = c.says(c.ATTENTION["costs_reader_how"])
        items.append(_card("costs_unconfirmed", c.says(title), why=c.says(why), consequence=consequence,
                           how=how, link=link, decision="record_bill" if act else None, can_fix_here=act,
                           since=p.started_at))
    trial = _trial_card(p)
    if trial:
        items.append(trial)
    items.extend(extra)
    for item in items:
        # "since" is when the situation began; a future moment (a note's expiry, a check-in up to 15 min ahead)
        # is not one (U3).
        if item["since"] is not None and item["since"] > p.as_of:
            item["since"] = None
    ranks = {sev: index for index, sev in enumerate(SEVERITY_ORDER)}
    items.sort(key=lambda item: item["since"] or "", reverse=True)
    items.sort(key=lambda item: ranks[item["sev"]])
    return items


def _allowance_card(p: Projection) -> dict:
    below = p.allowance_status == "below_line"
    remaining, line = c.pct(p.min_remaining), c.pct(p.threshold)
    title = c.ATTENTION["allowance_below" if below else "allowance_near"].format(p=remaining, line=line)
    if below:
        refill = (p.lowest_window or {}).get("resets_at")
        consequence = (c.says(c.ATTENTION["allowance_below_refill"], c.time(refill, "day_clock"), ").")
                       if refill else c.says(c.ATTENTION["allowance_below_unknown"]))
    else:
        consequence = c.says(c.ATTENTION["allowance_near_consequence"])
    pause_possible = p.can_act and not p.system_pause and (
        p.profile == "RUNNING" or (p.leader_pause and p.profile not in STRONGER_PAUSES))
    return _card("allowance_near", c.says(title), why=c.says(c.ATTENTION["allowance_why"]),
                 consequence=consequence, decision="pause_ai" if pause_possible else None,
                 can_fix_here=pause_possible, since=p.reading_at)


def _repeat_problems(p: Projection) -> list:
    cards = []
    week = p.now - timedelta(days=7)
    for role in p.roles.values():
        streaks: dict[str, list] = {}
        closed: set[str] = set()
        for task in role.recent:  # newest first
            objective = task["objective"]
            if objective in closed or task["status"] not in TERMINAL or p.is_skip(task):
                continue
            if p.task_moment(task) < week:
                closed.add(objective)
                continue
            if task["status"] in {"FAILED", "DEAD_LETTER"}:
                streaks.setdefault(objective, []).append(task)
            else:
                closed.add(objective)
        for objective, problems in streaks.items():
            if len(problems) < 2:
                continue
            latest = problems[0]
            dept = c.dept_name(role.dept)
            cadence = p.task_cadence(latest)
            reason = c.reason_segment(p.raw_reason(latest), live=not p.paper) or c.text(c.REASON["unknown"])
            consequence = c.says(c.ATTENTION["repeat_problems_next"], c.time(role.next_at, "day_clock"), ".") \
                if role.next_at else []
            cards.append(_card(
                "repeat_problems",
                c.says(c.ATTENTION["repeat_problems"].format(dept=dept, cadence=cadence, n=len(problems)) if cadence
                       else c.ATTENTION["repeat_problems_plain"].format(dept=dept, n=len(problems))),
                why=c.says(c.MISC["last_reason"][0], reason, c.MISC["last_reason"][1]), consequence=consequence,
                how=c.says(c.ATTENTION["in_prompt"]), since=p.task_moment(latest),
                suffix=None if not cards else f"{role.dept}_{len(cards)}"))
    return cards


def _trial_card(p: Projection) -> dict | None:
    rollout = p.facts["rollout"]
    trader = p.roles["trader"]
    if not rollout or rollout["state"] != "OBSERVING" or not trader.every:
        return None
    policy = rollout.get("policy") or {}
    horizon = policy.get("horizon_seconds") if type(policy.get("horizon_seconds")) is int else 3600
    needed = policy.get("min_decisions") if type(policy.get("min_decisions")) is int else 3
    if trader.every <= horizon:
        return None
    return _card("trial_rollback_likely", c.says(c.ATTENTION["trial_rollback_likely"]),
                 why=c.says(c.ATTENTION["trial_rollback_why"].format(
                     n=needed, horizon=c.span(horizon),
                     cadence=c.cadence(trader.every))),
                 since=rollout.get("activated_at"))


# --- Departments (§B.5.5) --------------------------------------------------------------------------


def departments(p: Projection) -> list:
    rooms = []
    for dept, kind in DEPARTMENT_ORDER:
        rooms.append(_ai_room(p, p.roles[dept]) if kind == "ai" else _software_room(p, dept))
    return rooms


def _room_base(dept: str, kind: str, job: str) -> dict:
    name, room, _ = c.ROOM[dept]
    return {"id": dept, "kind": kind, "name": name, "room": room, "job": c.says(job),
            "details_href": c.ROOM_DETAILS[dept]}


def _ai_room(p: Projection, role: Role) -> dict:
    dept = role.dept
    job = c.room_job(dept, live=not p.paper)
    if dept == "optimisation" and not p.auto_optimisation:
        job = c.OPTIMISATION_MANUAL_JOB
    room = _room_base(dept, "ai", job)
    week = p.now - timedelta(days=7)
    in_week = [task for task in role.recent if p.task_moment(task) >= week]
    record = {"done": sum(task["status"] == "SUCCEEDED" for task in in_week),
              "problems": sum((task["status"] in {"FAILED", "DEAD_LETTER"} and not p.is_skip(task))
                              or task["status"] in STUCK for task in in_week),
              "skipped": sum(p.is_skip(task) for task in in_week)}
    open_quests = len(role.active)
    if dept == "leader":
        open_quests += len(p.facts["improvements"]["proposed"]) + len(p.facts["improvements"]["candidates"])
    facts = []
    if dept == "optimisation":
        facts.append({"label": c.FACT_LABEL["automatic"],
                      "value": c.says(c.FACT_TEXT["automatic_on" if p.auto_optimisation else "automatic_off"])})
    if dept == "engineer" and not role.active:
        facts.append({"label": c.FACT_LABEL["next"], "value": c.says(c.FACT_TEXT["engineer_next"])})
    heads_up, if_nothing = _room_heads_up(p, role)
    room.update({
        "light": role.light, "light_text": c.says(c.LIGHT[role.light_key]), "had_problem": role.had_problem,
        "now": _room_now(p, role), "last": _room_last(p, role), "next": _room_next(p, role),
        "heads_up": heads_up, "if_nothing": if_nothing, "rules": _rules(p) if dept == "trader" else None,
        "record_7d": record, "open_quests": open_quests, "facts": facts,
    })
    return room


def _room_now(p: Projection, role: Role) -> list:
    key = role.light_key
    if key == "off":
        return c.says(c.ROOM_NOW["off"])
    if key == "stuck":
        return p.state_text(role.stuck[0])
    if key == "working":
        task = role.working
        started = p.facts["attempt_starts"].get(task["task_id"])
        state = p.state_text(task)
        if started:
            return c.says(*state, c.MISC["turn_began"], c.time(started, "relative"), ".")
        return c.says(*state, ".")
    if key in ("waiting", "waiting_allowance", "paused_quota", "paused_model", "paused_provider"):
        return c.says(c.ROOM_NOW[key])
    if key.startswith("paused_"):
        return c.says(c.ROOM_NOW["paused_by"].format(by=c.ORIGINATOR_SHORT.get(p.originator, "by you")))
    if key == "on_call":
        return c.says(c.ROOM_NOW["on_call_optimisation" if role.dept == "optimisation" else "on_call_engineer"])
    resting = c.ROOM_NOW["resting"] if role.next_at is not None else c.ROOM_NOW["no_turn"]
    return c.says(resting + (c.ROOM_NOW["had_problem"] if role.had_problem else ""))


def _room_next(p: Projection, role: Role) -> dict | None:
    if role.next_at is None:
        return None
    note = None
    if role.overdue and role.dept != "engineer":
        note = c.says(c.CATCH_UP)
    elif role.dept == "engineer":
        note = c.says(c.CALENDAR["engineer_deadline"])
    return {"at": iso_ms(role.next_at), "exact": True, "every_seconds": role.every, "overdue": role.overdue,
            "waiting_because": role.gate_text if role.overdue else None, "note": note,
            "extra_rows": role.extra_rows, "status": next_status(role)}


def next_status(role: Role) -> str:
    """planned, due_now (G9), waiting (a known gate holds) or overdue (a reason HQ can't see; contract 1.2)."""
    if not role.overdue:
        return "planned"
    if role.dept == "engineer" or role.gate == "G10":
        return "overdue"
    return "due_now" if role.gate == "G9" else "waiting"


def _room_last(p: Projection, role: Role) -> dict | None:
    tasks = [task for task in role.recent if task["status"] in TERMINAL or task["status"] in STUCK]
    task = tasks[0] if tasks else None
    if role.dept == "trader":
        decision = None
        if task is not None and task["status"] == "SUCCEEDED":
            decision = p.task_decision(task)
        newest = p.facts["decisions"][0] if p.facts["decisions"] else None
        if newest is not None and (task is None or (_dt(newest["created_at"]) or p.started_at) >= p.task_moment(task)):
            decision = newest
        if decision is not None:
            return _decision_last(p, decision)
    if task is None:
        return None
    at = iso_ms(p.task_moment(task))
    cadence = p.task_cadence(task)
    result = loads(task.get("result_json"))
    link = p.link_for(task)
    status = task["status"]
    if status == "SUCCEEDED":
        return {"at": at, "outcome": "done", "text": c.says(_done_words(p, task, cadence)),
                "quote": c.quote(result.get("summary")), "link": link}
    if status in STUCK:
        return {"at": at, "outcome": "stuck", "text": c.says(c.LAST["stuck_plain"]), "quote": None, "link": link}
    if p.is_skip(task) or status == "CANCELLED":
        reason = c.SKIP_PLAIN.get(p.skip_key(task) or "", c.MISC["cancelled"])
        return {"at": at, "outcome": "skipped", "text": c.says(c.LAST["skipped"].format(reason=reason)),
                "quote": None, "link": link}
    reason = c.reason_segment(p.raw_reason(task), live=not p.paper) or c.text(c.REASON["unknown"])
    lead = c.LAST["problem"].format(cadence=cadence) if cadence else c.LAST["problem_plain"]
    return {"at": at, "outcome": "problem", "text": [c.text(lead), reason, c.text(".")], "quote": None,
            "link": link}


def _done_words(p: Projection, task: dict, cadence: str | None) -> str:
    result = loads(task.get("result_json"))
    if task["role"] == "research":
        count = len(result.get("finding_ids") or [])
        return c.plural(c.LAST["research"], count)
    if task["role"] == "learning":
        count = len(result.get("lesson_revision_ids") or [])
        return c.plural(c.LAST["learning"], count)
    if task["role"] == "optimisation":
        return c.LAST["optimisation"]
    return c.LAST["done"].format(cadence=cadence) if cadence else c.LAST["done_plain"]


def _decision_last(p: Projection, decision: dict) -> dict:
    payload = loads(decision.get("payload_json"))
    action = decision["action"]
    words = c.DECISION_ACTION.get(action, action.replace("_", " "))
    symbols = p.decision_symbols(payload)
    if action in ("no_action", "hold"):
        before, after = c.LAST["decided_wait"]
        segment = c.term("wait", words, code=action) if action == "no_action" else c.text(words, code=action)
        text = [c.text(before.format(symbols=p.symbols_text(symbols))), segment, c.text(after)]
    elif action in ("enter", "exit"):
        asset = (base_asset(symbols[0]) if symbols else None) or c.MISC["a_coin"]
        task = p.runtime.database.execute("SELECT output_json FROM tasks WHERE task_id=?",
                                          (decision.get("task_id"),)).fetchone()
        # The software refused this order before anything was sent or held (trader.py).
        key = "decided_trade_refused" if task and isinstance(loads(task[0]).get("refusal"), dict) else "decided_trade"
        text = [c.text(c.LAST[key].format(verb="buy" if action == "enter" else "sell", symbol=asset), code=action)]
    else:
        text = [c.text(c.LAST["decided_other"].format(action=words[:1].upper() + words[1:]), code=action)]
    return {"at": iso_ms(_dt(decision["created_at"]) or p.now), "outcome": "decided", "text": text,
            "quote": p.decision_quote(payload), "link": f"/decisions/{decision['decision_id']}"}


def _room_heads_up(p: Projection, role: Role) -> tuple[list | None, list | None]:
    trader_next = p.roles["trader"].next_at
    if role.dept == "trader":
        if p.offline or trader_next is None:
            return None, None
        if role.light == "working":
            return None, c.says(c.NOW["working"][2])
        if p.notes_missing_next and p.latest_note_expiry is not None:
            before, after = c.HEADS_UP["trader_expires" if p.latest_note_expiry > p.now else "trader_expired"]
            heads = c.says(before, c.time(p.latest_note_expiry, "clock"), after)
            nothing = c.says(c.HEADS_UP["trader_if_nothing"][0], c.time(trader_next, "clock"),
                             c.HEADS_UP["trader_if_nothing"][1])
            return heads, nothing
        return None, c.says(c.HEADS_UP["trader_default"], c.time(trader_next, "clock"), c.NOW["calm_trader"])
    if role.light == "working":
        return None, c.says(c.NOW["working"][2])
    if role.dept == "research" and not p.offline and trader_next is not None and p.notes_expiry is not None \
            and p.latest_note_expiry is not None and p.latest_note_expiry < trader_next:
        before, after = c.HEADS_UP["research_expire"]
        return c.says(before, c.time(p.latest_note_expiry, "clock"), after), None
    return None, None


def _rules(p: Projection) -> list | None:
    mandate = p.cache.get("mandate")
    if not mandate or not mandate.get("symbols"):
        return None
    symbols = mandate["symbols"]
    listed = (symbols[0] if len(symbols) == 1
              else c.MISC["list_separator"].join(symbols[:-1]) + c.MISC["and"] + symbols[-1])
    fraction = c.decimal_or_none(mandate.get("max_gross_exposure_fraction"))
    if fraction is None:
        return c.says(c.RULES["symbols"].format(symbols=listed))
    return c.says(c.RULES["limit"].format(symbols=listed, pct=c.pct(fraction * 100)))


def _software_room(p: Projection, dept: str) -> dict:
    room = _room_base(dept, "software", c.room_job(dept, live=not p.paper))
    facts: list = []
    if dept == "secretary":
        light = "off" if p.offline else "ok"
        reports = p.facts["secretary"]
        if reports:
            facts.append({"label": c.FACT_LABEL["last_report"],
                          "value": [c.time(reports[0]["created_at"], "relative")]})
        day = p.now - timedelta(hours=24)
        flagged = sum(1 for row in reports if row["kind"] in {"incident", "failed", "dead_letter", "blocked_budget",
                                                              "waiting_external", "overdue"}
                      and (_dt(row["created_at"]) or p.started_at) >= day)
        facts.append({"label": c.FACT_LABEL["flagged"], "value": c.says(str(flagged))})
        now_text = c.says(c.ROOM_NOW["off"] if light == "off" else c.ROOM_NOW["secretary"])
    elif dept == "market":
        light = p.market_light
        if p.last_price_at:
            facts.append({"label": c.FACT_LABEL["last_price"], "value": [c.time(p.last_price_at, "relative")]})
        history = p.history_event["payload"] if p.history_event else {}
        next_refresh = _dt(history.get("next_refresh_at_utc"))
        if next_refresh and p.active:
            facts.append({"label": c.FACT_LABEL["history"],
                          "value": c.says(c.FACT_TEXT["history_next"], c.time(next_refresh, "clock"))})
        fx = p.facts["fx"]
        if fx and _dt(fx.get("valid_as_of")):
            facts.append({"label": c.FACT_LABEL["euro_rate"],
                          "value": c.says(c.FACT_TEXT["euro_dated"], c.time(fx["valid_as_of"], "date"))})
        if light == "off":
            now_text = c.says(c.ROOM_NOW["off"])
        elif p.feed_degraded:
            now_text = c.says(c.ROOM_NOW["market_degraded"])
        elif p.last_price_at is None:
            now_text = c.says(c.ROOM_NOW["market_none"])
        elif p.prices_old:
            now_text = c.says(c.ROOM_NOW["market_old"])
        else:
            now_text = c.says(c.ROOM_NOW["market_ok"])
    else:
        account_problem = any(prefix in ACCOUNT_PROBLEM_PREFIXES for prefix, _ in p.problems)
        light = "off" if p.offline else "attention" if p.unclear_orders or account_problem else "ok"
        facts.append({"label": c.FACT_LABEL["open_orders"], "value": c.says(str(p.open_orders))})
        facts.append({"label": c.FACT_LABEL["unclear"], "value": c.says(str(p.unclear_orders))})
        coins = c.decimal_or_none(p.cache.get("inventory")) if p.cache.get("available") else None
        if coins is None:
            holdings = c.says(c.ROOM_NOW["accounts_unknown"].rstrip("."))
        elif coins == 0:
            holdings = c.says(c.FACT_TEXT["nothing_held"])
        else:
            holdings = c.says(c.money_segment(coins, p.reporting), c.MISC["in_coins"])
        facts.append({"label": c.FACT_LABEL["holdings"], "value": holdings})
        facts.append({"label": c.FACT_LABEL["double_check"], "value": c.says(c.FACT_TEXT["not_recorded"])})
        if light == "off":
            now_text = c.says(c.ROOM_NOW["off"])
        elif p.unclear_orders:
            now_text = c.says(c.ROOM_NOW["accounts_unclear"] if p.paper else c.LIVE_ROOM_NOW["accounts_unclear"])
        elif coins is None:
            now_text = c.says(c.ROOM_NOW["accounts_unknown"])
        else:
            orders = (c.plural(c.ROOM_NOW["accounts_orders"], p.open_orders) if p.open_orders
                      else c.ROOM_NOW["accounts_no_orders"])
            if coins == 0:
                now_text = (c.says(c.MISC["nothing_held_orders"] + orders) if p.open_orders
                            else c.says(c.ROOM_NOW["accounts_flat"]))
            else:
                before, after = c.MISC["holding"]
                now_text = c.says(before, c.money_segment(coins, p.reporting), after + orders)
    light_key = "off" if light == "off" else light
    room.update({"light": light, "light_text": c.says(c.LIGHT[light_key]), "had_problem": False, "now": now_text,
                 "last": None, "next": None, "heads_up": None, "if_nothing": None, "rules": None,
                 "record_7d": {"done": 0, "problems": 0, "skipped": 0}, "open_quests": 0, "facts": facts})
    return room


# --- Quests (§B.5.6) ----------------------------------------------------------------------------------


def quests(p: Projection) -> list:
    items = []
    active = sorted((task for task in p.facts["active_tasks"] if task["role"] in c.ROOM),
                    key=lambda task: task["created_at"], reverse=True)
    for task in active:
        items.append(_quest(p, task))
    for dept in c.ROOM:
        role = p.roles.get(dept)
        tasks = [task for task in (role.recent if role else [])
                 if task["status"] in TERMINAL][:FINISHED_QUESTS_PER_DEPT]
        items.extend(_quest(p, task) for task in tasks)
    for change in p.facts["improvements"]["proposed"]:
        expires = _dt(change["document"].get("expires_at_utc"))
        state = c.says(c.TASK_STATE["change_proposed"], c.time(expires, "day_clock"), ")") if expires else \
            c.says(c.TASK_STATE["change_proposed_plain"])
        items.append({"id": f"change:{change['change_id']}", "dept": "leader", "origin": "improvement",
                      "origin_text": c.says(c.ORIGIN["improvement"]), "state": change["state"], "state_text": state,
                      "needs_you": False, "terminal": False, "created_at": iso_ms(_dt(change["created_at"]) or p.now),
                      "due_at": None, "deadline_at": _iso(expires), "finished_at": None, "attempts": None,
                      "reason": None, "link": "/changes"})
    for candidate in p.facts["improvements"]["candidates"]:
        items.append({"id": f"candidate:{candidate['candidate_id']}", "dept": "leader", "origin": "improvement",
                      "origin_text": c.says(c.ORIGIN["improvement"]), "state": candidate["state"],
                      "state_text": c.says(c.TASK_STATE["candidate_" + candidate["state"]]), "needs_you": False,
                      "terminal": False, "created_at": iso_ms(_dt(candidate["created_at"]) or p.now), "due_at": None,
                      "deadline_at": None, "finished_at": None, "attempts": None, "reason": None,
                      "link": f"/changes/{candidate['candidate_id']}"})
    unique, seen = [], set()
    for item in items:
        if item["id"] not in seen:
            seen.add(item["id"])
            unique.append(item)
    return unique[:QUEST_MAX_ITEMS]


def _quest(p: Projection, task: dict) -> dict:
    origin, origin_text = p.origin(task)
    status = task["status"]
    terminal = status in TERMINAL
    reason = None
    if (status in {"FAILED", "DEAD_LETTER"} and not p.is_skip(task)) or status in STUCK:
        segment = c.reason_segment(p.raw_reason(task), live=not p.paper)
        reason = [segment] if segment else None
    finished = _dt(task.get("result_created_at")) if terminal else None
    return {"id": f"task:{task['task_id']}", "dept": task["role"], "origin": origin, "origin_text": origin_text,
            "state": status, "state_text": p.state_text(task), "needs_you": status in STUCK, "terminal": terminal,
            "created_at": iso_ms(_dt(task["created_at"]) or p.now), "due_at": _iso(task.get("due_at")),
            "deadline_at": _iso(task.get("deadline_at")), "finished_at": _iso(finished),
            "attempts": {"used": max(0, int(task["attempts_used"])), "max": max(0, int(task["max_attempts"]))},
            "reason": reason, "link": p.link_for(task)}


# --- Decisions (§B.5.8, §D.8) ---------------------------------------------------------------------


def decisions(p: Projection, attention_items: list, checkins_block: dict | None, barriers: list[str]) -> list:
    if not p.can_act:
        return []
    codes = {item["id"].split(":", 1)[0] for item in attention_items}
    cards = [_start_card(p), _pause_card(p, codes), _resume_card(p, barriers), _optimisation_card(p),
             _stop_card(p), _bill_card(p), _complete_card(p), _plan_card(p, checkins_block)]
    return cards


def _decision(key: str, *, shown: bool, available: bool = True, unavailable: str | None = None,
              recommended: str | None = None, what: list | None = None, cost: list | None = None,
              when: list | None = None, right_now: list | None = None, facts: dict | None = None,
              undo: list | None = None, reversible: str | None = None) -> dict:
    copy = c.DECISION[key]
    return {"id": key, "shown": shown, "available": available,
            "unavailable_because": None if available else c.says(unavailable),
            "recommended": recommended is not None and available,
            "recommended_because": c.says(recommended) if recommended is not None and available else None,
            "title": copy["title"], "button_label": copy["button"], "confirm_label": copy["confirm"],
            "weight": copy["weight"],
            "preview": {"what_happens": what or [c.says(copy["what"])], "cost": cost or c.says(copy["cost"]),
                        "undo": undo or c.says(copy["undo"]), "reversible": reversible or copy["reversible"],
                        "when": when or c.says(copy.get("when") or copy.get("not_needed")),
                        "right_now": right_now or []},
            "facts": facts or {}}


def _start_card(p: Projection) -> dict:
    copy = c.DECISION["start_trading"]
    shown = p.code in ("no_run", "crashed", "stopped", "silent", "attached_unknown")
    blocked = p.code in ("silent", "attached_unknown")
    recommended = copy["recommended"] if p.code in ("no_run", "crashed", "stopped") else None
    when = c.says(recommended or (copy["not_needed_silent"] if blocked else copy["not_needed"]))
    return _decision("start_trading", shown=shown, available=not blocked,
                     unavailable=copy["silent"] if blocked else None, recommended=recommended, when=when)


def _waiting_turns(p: Projection) -> list:
    turns = sorted((role.next_at, role.dept) for role in p.roles.values()
                   if role.dept in SCHEDULED_ROLES and role.next_at is not None)
    if not turns:
        return []
    copy = c.DECISION["pause_ai"]
    parts: list = [copy["wait_before"]]
    for index, (at, dept) in enumerate(turns[:4]):
        if index:
            parts.append(", ")
        if at <= p.now:
            parts.append(c.dept_name(dept) + c.MISC["overdue"])
        else:
            same_day = at.date() == p.now.date()
            parts.extend([c.dept_name(dept) + " ", c.time(at, "clock" if same_day else "day_clock")])
    parts.append(copy["wait_after"])
    return [c.says(*parts)]


def _pause_card(p: Projection, codes: set[str]) -> dict:
    copy = c.DECISION["pause_ai"]
    shown = p.profile == "RUNNING" or p.leader_pause
    recommended, when_text = None, None
    if p.code != "calm" and not p.system_pause:
        if "repeat_problems" in codes:
            dept = _repeat_dept(p)
            recommended = copy["repeat_problems"].format(dept=dept)
            when_text = copy["repeat_problems_when"].format(dept=dept)
        elif "prices_old" in codes:
            recommended, when_text = copy["prices_old"], copy["prices_old_when"]
        elif "orders_unclear" in codes:
            recommended, when_text = copy["orders_unclear"], copy["orders_unclear_when"]
        elif p.allowance_status == "below_line":
            recommended, when_text = copy["allowance_below"], copy["allowance_below_when"]
    if when_text is None:
        when_text = (copy["not_needed_off"] if p.offline and p.code != "silent"
                     else copy["not_needed_silent"] if p.code == "silent" else copy["not_needed"])
    stronger = p.leader_pause and p.profile in STRONGER_PAUSES
    available = not p.system_pause and not stronger
    unavailable = (copy["system"] if p.system_pause
                   else copy["leader_stronger"].format(profile=c.PAUSE_SHORT[p.profile]) if stronger else None)
    what = [c.says(copy["what"])]
    if p.leader_pause and available:
        what.insert(0, c.says(copy["replaces_leader"].format(profile=c.PAUSE_SHORT[p.profile])))
    working = [role for role in p.roles.values() if role.working]
    if working:
        what.append(c.says(copy["working"].format(dept=c.dept_name(working[0].dept))))
    what.append(c.says(copy["continues"]))
    right_now = _waiting_turns(p) if p.running_fresh and p.profile == "RUNNING" else []
    return _decision("pause_ai", shown=shown, available=available, unavailable=unavailable,
                     recommended=recommended, what=what, when=c.says(when_text), right_now=right_now)


def _repeat_dept(p: Projection) -> str:
    for role in p.roles.values():
        problems = 0
        for task in role.recent:
            if task["status"] not in TERMINAL or p.is_skip(task):
                continue
            if task["status"] in {"FAILED", "DEAD_LETTER"}:
                problems += 1
                if problems >= 2:
                    return c.dept_name(role.dept)
            else:
                break
    return c.MISC["a_department"]


def _resume_card(p: Projection, barriers: list[str]) -> dict:
    copy = c.DECISION["resume"]
    shown = p.profile != "RUNNING"
    blockers = [{"code": raw, "text": c.says(c.BARRIER.get(raw, c.REASON["unknown"])),
                 "definite": raw == "system pause requires controller recovery"} for raw in barriers]
    what = [c.says(copy["what"])]
    if p.allowance_status == "below_line":
        what.append(c.says(copy["below_line"]))
    if blockers:
        listed = "; ".join(c.plain(blocker["text"]) for blocker in blockers)
        definite = all(blocker["definite"] for blocker in blockers)
        when = c.says((copy["blocked_definite"] if definite else copy["blocked"]).format(list=listed))
    else:
        when = c.says(copy["when"] if shown else copy["only_after"])
    recommended = copy["recommended"] if p.owner_pause and not blockers else None
    return _decision("resume", shown=shown, available=not p.system_pause,
                     unavailable=copy["system"] if p.system_pause else None, recommended=recommended, what=what,
                     when=when, facts={"predicted_blockers": blockers} if shown else {})


def _optimisation_card(p: Projection) -> dict:
    copy = c.DECISION["start_optimisation"]
    cycle = p.facts["cycle"]
    eligible = (p.status == "RUNNING" and p.stale is False and p.profile == "RUNNING" and not p.provider_paused
                and not (cycle and cycle["active"]))
    tries = p.cache.get("max_attempts")
    deadline = p.config.get("optimisation_deadline_seconds")
    deadline = deadline if type(deadline) is int and deadline > 0 else None
    limit = copy["deadline"].format(duration=c.span(deadline)) if deadline else copy["no_deadline"]
    cost = c.says(copy["cost"].format(tries=tries if tries is not None else copy["few"], deadline=limit))
    return _decision("start_optimisation", shown=True, available=eligible,
                     unavailable=copy["ineligible"] if not eligible else None, cost=cost,
                     facts={"max_attempts": tries, "deadline_seconds": deadline})


def _stop_card(p: Projection) -> dict:
    copy = c.DECISION["stop_safely"]
    flat = p.flat
    if flat is True:
        what = [c.says(copy["what_flat"])]
        if p.system_pause:
            what.append(c.says(copy["replaces_safety_stop"]))
    elif flat is False:
        what = [c.says(copy["what_held"])]
    else:
        what = [c.says(copy["what_unknown"])]
    if p.system_pause:
        # Never recommended during an automatic safety stop, and only partly undoable (spec §B.5.8).
        return _decision("stop_safely", shown=p.active, what=what, facts={"flat": flat},
                         undo=c.says(copy["undo_safety"]), reversible="partly",
                         when=c.says(copy["not_recommended_safety"]))
    return _decision("stop_safely", shown=p.active, what=what, facts={"flat": flat})


def _bill_card(p: Projection) -> dict:
    copy = c.DECISION["record_bill"]
    codes = p.cache.get("codes") or []
    recommended = (copy["recommended"] if "subscription_bills_unconfirmed" in codes
                   else copy["recommended_other"] if "other_costs_unconfirmed" in codes else None)
    return _decision("record_bill", shown=True, recommended=recommended,
                     facts={"graph_share_presets": list(c.GRAPH_SHARE_PRESETS),
                            "scheduled_departments": list(c.SCHEDULED_DEPARTMENTS)})


def previous_month(now: datetime) -> tuple[datetime, datetime]:
    end = now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start = (end - timedelta(days=1)).replace(day=1)
    return start, end


def _complete_card(p: Projection) -> dict:
    copy = c.DECISION["declare_bills_complete"]
    start, end = previous_month(p.now)
    recommended = None
    bills = p.facts["bills"]
    for row in bills:  # newest first
        if row["record_type"] == "completeness":
            break
        period_start = _dt(row["document"].get("period_start"))
        period_end = _dt(row["document"].get("period_end"))
        if period_start and period_end and period_start < end and period_end > start:
            recommended = copy["recommended"]
            break
    what = [c.says(copy["what"])]
    if "history_gap" in (p.cache.get("codes") or []):
        what.append(c.says(copy["gap"]))
    return _decision("declare_bills_complete", shown=True, recommended=recommended, what=what,
                     facts={"suggested_period": {"start": iso_ms(start), "end": iso_ms(end)}})


def _plan_card(p: Projection, block: dict | None) -> dict:
    copy = c.DECISION["plan_checkin"]
    available = bool(block and block["available"])
    future = [item for item in (block or {}).get("items", []) if item["state"] in ("planned", "due", "overdue")]
    return _decision("plan_checkin", shown=available, available=available,
                     unavailable=copy["unavailable"] if not available else None,
                     recommended=copy["recommended"] if available and not future else None)


# --- Money and market (§B.3, §D.11) ----------------------------------------------------------------


def _zero_counts() -> dict:
    return dict.fromkeys(("decisions", "waits", "buys", "sells", "other_decisions", "orders", "completed_trades",
                          "research_notes", "notes_in_use", "lessons"), 0)


def money_block(p: Projection) -> dict:
    cache = p.cache
    at_risk = c.says(c.AT_RISK_PAPER if p.paper else c.AT_RISK_LIVE)
    holdings = {"coins": None, "cash": None, "flat": p.flat, "open_orders": p.open_orders,
                "unclear_orders": p.unclear_orders}
    counts = cache.get("counts") or _zero_counts()
    as_of = _iso(cache.get("as_of"))
    if not cache.get("available"):
        unavailable = c.says(c.MONEY["unavailable"] if p.paper else c.LIVE_MONEY["unavailable"])
        return {"available": False, "unavailable_reason": unavailable, "as_of": as_of,
                "practice_value": None, "value_final": False, "estimate_notes": [],
                "opening": {"native": [], "reporting": None, "at": None},
                "change": {"total": None, "currency_effect": None, "team_effect": None,
                           "unknown_reason": unavailable},
                "holdings": holdings, "counts": counts,
                "pays_for_itself": {"status": "unknown", "net": None, "missing": [], "honest_note": None},
                "at_risk": at_risk}
    reporting = cache["reporting_currency"]
    holdings.update(coins=c.money(cache.get("inventory"), reporting), cash=c.money(cache.get("cash"), reporting))
    total = c.money(cache.get("trading_pnl"), reporting)
    change = {"total": total, "currency_effect": c.money(cache.get("fx_pnl"), reporting),
              "team_effect": c.money(cache.get("strategy_alpha"), reporting),
              "unknown_reason": None if total else c.says(c.MONEY["change_unknown"]
                                                          + c.MONEY["change_unknown_reason"] + ".")}
    opening = {"native": [money for item in cache.get("opening_native") or []
                          if (money := c.money(item.get("amount"), item.get("asset")))],
               "reporting": c.money(cache.get("opening_reporting"), reporting), "at": _iso(cache.get("opening_at"))}
    value = c.money(cache.get("equity"), reporting)
    return {"available": True, "unavailable_reason": None, "as_of": as_of, "practice_value": value,
            "value_final": value is not None and not cache.get("valuation_provisional"),
            "estimate_notes": _estimate_notes(cache, p.flat), "opening": opening, "change": change,
            "holdings": holdings, "counts": counts, "pays_for_itself": _pays(p, cache), "at_risk": at_risk}


def _estimate_notes(cache: dict, flat: bool | None) -> list:
    notes: dict[str, dict] = {}
    for fx in cache.get("fx") or []:
        if fx["expired"] and "euro_rate_too_old" not in notes:
            notes["euro_rate_too_old"] = {"code": "euro_rate_too_old", "tone": "attention",
                                          "text": c.says(c.MONEY["euro_rate_too_old"])}
        elif fx["recorded_stale"] and not fx["expired"] and _dt(fx.get("valid_as_of")) \
                and "euro_rate_from_earlier_day" not in notes:
            before, after = c.MONEY["euro_rate_from_earlier_day"]
            notes["euro_rate_from_earlier_day"] = {"code": "euro_rate_from_earlier_day", "tone": "calm",
                                                   "text": c.says(before, c.time(fx["valid_as_of"], "date"), after)}
    # Old or missing prices only make the total an estimate when something is held.
    if cache.get("valuation_provisional") and flat is not True:
        marks = cache.get("marks") or []
        stale = [mark["age_seconds"] for mark in marks if mark["stale"] and mark.get("age_seconds") is not None]
        if stale:
            before, after = c.MONEY["prices_old"]
            notes["prices_old"] = {"code": "prices_old", "tone": "attention",
                                   "text": c.says(before, c.duration(max(stale)), after)}
        elif not marks:
            notes["no_prices"] = {"code": "no_prices", "tone": "attention", "text": c.says(c.MONEY["no_prices"])}
    return list(notes.values())


def _pays(p: Projection, cache: dict) -> dict:
    status = cache.get("economic_status") or "unknown"
    net = c.money(cache.get("net"), cache["reporting_currency"]) if status != "unknown" else None
    if net is None:
        status = "unknown"
    codes = list(dict.fromkeys(cache.get("codes") or []))
    unverified = cache.get("recovery_status") == "UNAVAILABLE"
    missing = []
    for code in codes:
        words, fixable = c.econ(code, live=not p.paper)
        if code == "history_gap" and unverified:
            words = c.NOTICE["history_unverified"][1]
        missing.append({"code": code if code in c.ECON else "other", "text": c.says(words),
                        "fixable_here": fixable and p.can_act})
    honest = None
    if "history_gap" in codes:
        template = c.MONEY["honest_unverified" if unverified else "honest_gap"]
        honest = c.says(template.format(day=c.day_text(p.started_at)))
    return {"status": status, "net": net, "missing": missing, "honest_note": honest}


def pays_plain(block: dict, *, live: bool = False) -> str:
    pays = block["pays_for_itself"]
    suffix = "_live" if live else ""
    if pays["status"] == "covered":
        return c.PROMPT["pays_covered" + suffix].format(net=pays["net"]["display"])
    if pays["status"] == "not_covered":
        net = pays["net"]["display"] if live else pays["net"]["display"].lstrip("\u2212-")
        return c.PROMPT["pays_not_covered" + suffix].format(net=net)
    return c.NOT_KNOWN_YET


def market(p: Projection) -> list:
    items = []
    averages = p.cache.get("market") or {}
    for symbol in ("BTC/USD", "ETH/USD"):
        base, quote = symbol.split("/")
        mark = p.facts["marks"].get(base)
        price = c.money(mark["mark"], quote) if mark and mark["quote_currency"] == quote else None
        price_at = _dt(mark["observed_at"]) if price else None
        values = averages.get(symbol) or {}
        avg20, avg50 = c.money(values.get("sma_20"), quote), c.money(values.get("sma_50"), quote)
        if avg20 and avg50 and Decimal(avg50["amount"]) != 0:
            short, long = Decimal(avg20["amount"]), Decimal(avg50["amount"])
            change = (short - long) / long
            trend = "level" if abs(change) < Decimal("0.001") else "below" if short < long else "above"
        else:
            trend = "not_enough_history"
        items.append({"symbol": symbol, "price": price, "price_at": _iso(price_at),
                      "price_age_seconds": seconds_between(p.now, price_at), "avg_20h": avg20, "avg_50h": avg50,
                      "trend": trend, "text": c.says(c.TREND[trend] + c.TREND_TAIL)})
    return items


# --- Check-ins, prompt facts, notices, links --------------------------------------------------------


def checkins_block(p: Projection) -> dict | None:
    if not p.owner:
        return None
    facts = p.checkin_facts
    available = bool(facts["available"])
    items = list(facts["items"]) if available else []
    open_items = [item for item in items if item["state"] in ("planned", "due", "overdue")]
    trader = p.roles["trader"].next_at
    suggested = iso_ms(trader + CHECKIN_SUGGESTION_DELAY) if trader is not None and trader > p.now else None
    return {"available": available, "items": items,
            "next_id": min(open_items, key=lambda item: item["planned_at"])["checkin_id"] if open_items else None,
            "last_done_at": _iso(facts["last_done_at"]) if available else None, "suggested_at": suggested}


def prompt_since(p: Projection) -> tuple[datetime, datetime | None]:
    """(the review prompt's "since" moment, the last done check-in or None): that check-in, else 24 h ago."""
    last_done = _dt(p.checkin_facts.get("last_done_at")) if p.owner and p.checkin_facts["available"] else None
    return last_done or p.now - timedelta(hours=24), last_done


def prompt_facts(p: Projection, attention_items: list, story_items: list, money: dict, counts: dict) -> dict:
    """The review prompt's facts; ``counts`` are exact (``hq_facts.period_counts`` from ``prompt_since``)."""
    since, last_done = prompt_since(p)

    def within(value) -> bool:
        moment = _dt(value)
        return moment is not None and since <= moment <= p.now

    flagged = [c.cut(c.plain(item["title"]), 200) for item in attention_items if item["sev"] in ("act", "watch")]
    for item in story_items:
        if item["problem"] and within(item["at"]):
            flagged.append(c.cut(c.plain(item["headline"]), 200))
    flagged = list(dict.fromkeys(flagged))[:10]
    turns = [{"dept": dept, "name": c.dept_name(dept), "at": _iso(p.roles[dept].next_at)}
             for dept in PROMPT_TURN_ROLES]
    if p.auto_optimisation:
        turns.append({"dept": "optimisation", "name": c.dept_name("optimisation"),
                      "at": _iso(p.roles["optimisation"].next_at)})
    value = money["practice_value"]
    return {
        "as_of": p.as_of, "team": _team_plain(p), "setting": _setting_plain(p), "allowance": _allowance_line(p),
        "next_turns": turns,
        "since": {"from": _iso(last_done), "label": c.PROMPT["since_checkin" if last_done else "last_24h"],
                  "decisions": counts["decisions"], "waits": counts["waits"], "orders": counts["orders"],
                  "completed_trades": counts["completed_trades"], "problems": counts["problems"],
                  "research_notes": counts["research_notes"]},
        "flagged": flagged, "practice_value": value["display"] if value else None,
        "value_final": money["value_final"], "pays_for_itself": pays_plain(money, live=not p.paper),
    }


def _team_plain(p: Projection) -> str:
    status = c.SERVICE_STATUS[p.status]
    if p.code == "silent" and p.heartbeat:
        return c.PROMPT["team_silent"].format(span=c.span((p.now - p.heartbeat).total_seconds()),
                                             clock=c.clock_utc(p.heartbeat))
    if p.active and p.heartbeat:
        return c.PROMPT["team_active"].format(status=status, ago=c.ago((p.now - p.heartbeat).total_seconds()))
    if p.heartbeat and not p.active:
        return c.PROMPT["team_off"].format(status=status, clock=c.clock_utc(p.heartbeat))
    return status


def _setting_plain(p: Projection) -> str:
    if p.profile == "RUNNING":
        return c.PAUSE_SETTING["RUNNING"]
    if p.system_pause:
        return c.PROMPT["safety_setting"].format(profile=c.PAUSE_SHORT[p.profile])
    by = c.ORIGINATOR_SHORT.get(p.originator or "", "")
    return f"{c.PAUSE_SETTING[p.profile]} ({by})" if by else c.PAUSE_SETTING[p.profile]


def _allowance_line(p: Projection) -> str:
    window = p.lowest_window
    unknown = stop_line_state(p) == "unknown"
    if window is None:
        if p.threshold is not None:
            return c.PROMPT["allowance_none"].format(line=c.pct(p.threshold))
        return c.PROMPT["allowance_none_rule_unknown" if unknown else "allowance_none_no_line"]
    phrase = c.WINDOW_PHRASE[window["label"]]
    reading = {"p": c.pct(window["remaining_percent"]), "window": phrase, "ago": p.reading_ago()}
    if p.threshold is not None:
        line = c.PROMPT["allowance"].format(**reading, line=c.pct(p.threshold))
    else:
        line = c.PROMPT["allowance_rule_unknown" if unknown else "allowance_no_line"].format(**reading)
    return line + (c.PROMPT["allowance_unreported"] if p.unreported else "")


def notices(p: Projection) -> list:
    items = []
    if p.mode == "live":
        title, text = c.NOTICE["live_mode"]
        items.append({"code": "live_mode", "tone": "attention", "title": c.says(title), "text": c.says(text),
                      "link": "/owner" if p.owner else None})
    elif not p.can_act:
        title, text = c.NOTICE["read_only"]
        items.append({"code": "read_only", "tone": "info", "title": c.says(title), "text": c.says(text),
                      "link": None})
    recovery = p.cache.get("recovery_status")
    if recovery in ("PARTIAL_HISTORY", "UNAVAILABLE"):
        code = "history_gap" if recovery == "PARTIAL_HISTORY" else "history_unverified"
        title, text = c.NOTICE[code]
        items.append({"code": code, "tone": "info", "title": c.says(title), "text": c.says(text),
                      "link": "/journal"})
    return items


def links(p: Projection) -> dict:
    return {"journal": "/journal", "mission_control": "/progress", "trading": "/trading",
            "organisation": "/organization", "costs": "/costs", "improvements": "/changes",
            "owner": "/owner" if p.owner else None, "review_export": "/api/v1/review-export"}
