"""Bounded readers for Trade Graph HQ (spec §B.4). Plain dicts in, plain dicts out; no copy text.

Every query is bounded by ``LIMIT``, a single row, an index, or "rowid-desc
iteration": ``ORDER BY rowid DESC`` without ``LIMIT``, stopping after ``cap`` rows
or once a row is older than ``since - 10 min``. The first row is always kept, so
the newest record is visible even when it is older than the window.

A capped reader that hits its cap inside the story window reports a floor: the
oldest moment it read completely. The story then covers only the time from the
highest floor on, and the next page starts there (``story_floor``), so nothing
is silently dropped.

Nothing here reads the dashboard's cached readiness, probes a process other than
the recorded service PID, calls a provider, or takes a database lock (no
ownership probe).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from trade_graph.adapters.models.subscription import sanitize_quota
from trade_graph.api import evidence, resources
from trade_graph.application.service_controller import ACTIVE_TASK, observed_run
from trade_graph.domain.clock import utc_iso

SCAN_SLACK = timedelta(minutes=10)
TASK_WINDOW = timedelta(days=8)
TASK_CAP = 400
DECISION_CAP = 120
EVENT_CAP = 3000
DEFERRAL_CAP = 60000
DEFERRAL_STREAK_GAP = timedelta(seconds=90)
# Allowance deferrals are read separately (deferral_streaks): one every 30 s per waiting turn would crowd
# every other event out of the capped scan.
STORY_EVENT_KINDS = ("subscription_ai_paused", "service_degraded", "service_recovered", "public_feed_health",
                     "public_history_refresh", "role_failed", "portfolio_opened", "engineer_candidate")
UNCLEAR_ORDER_STATES = frozenset({"UNKNOWN", "SUBMITTING", "CANCEL_PENDING"})
OPEN_ORDER_STATES = frozenset({"SUBMISSION_PENDING", "SUBMITTING", "OPEN", "PARTIALLY_FILLED", "UNKNOWN",
                               "CANCEL_PENDING"})
PROBLEM_REPORT_KINDS = frozenset({"incident", "failed", "dead_letter", "blocked_budget", "waiting_external",
                                  "overdue"})
WORKING_ATTEMPT_TASKS = 6


def parse(value) -> datetime | None:
    """A timezone-aware instant from a persisted timestamp, or None when absent or malformed."""
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return moment.astimezone(UTC) if moment.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None  # e.g. 9999-12-31T23:59:59-01:00 cannot be expressed in UTC


def seconds_between(later: datetime | None, earlier: datetime | None) -> int | None:
    if later is None or earlier is None:
        return None
    return int((later - earlier).total_seconds())


def loads(value) -> dict:
    return evidence._json(value)


def _scan(rows: Iterable, since: datetime, cap: int, column: str = "created_at") -> list[dict]:
    result: list[dict] = []
    cutoff = since - SCAN_SLACK
    for row in rows:
        if len(result) >= cap:
            break
        item = dict(row)
        result.append(item)
        moment = parse(item.get(column))
        if moment is not None and moment < cutoff:
            break
    return result


def _floor(rows: list[dict], cap: int, story_start: datetime) -> datetime | None:
    """The oldest moment a capped reader read completely, when its cap cut into the story window."""
    if len(rows) < cap:
        return None
    oldest = min((moment for row in rows if (moment := parse(row.get("created_at"))) is not None), default=None)
    return oldest if oldest is not None and oldest >= story_start else None


def story_floor(story_start: datetime, *readers: tuple[list[dict], int]) -> datetime | None:
    """The latest floor of the capped story readers, or None when none was cut inside the window."""
    floors = [floor for rows, cap in readers if (floor := _floor(rows, cap, story_start)) is not None]
    return max(floors, default=None)


# --- R1-R29 -------------------------------------------------------------------------------------


def portfolio(database, pid: str) -> dict | None:  # R1
    row = database.execute("SELECT mode, created_at, reporting_currency FROM portfolios WHERE portfolio_id=?",
                           (pid,)).fetchone()
    return dict(row) if row else None


def current_run(database, now: datetime) -> dict:  # R2
    """The newest recorded run, checked against its recorded PID only.

    HQ never probes database ownership (D110): a probe takes the database lock for a
    moment and could collide with the owner's Start. HQ therefore never reports
    ``ATTACHED_EXISTING``; Mission Control still does.
    """
    row = database.execute("SELECT * FROM graph_service_runs ORDER BY rowid DESC LIMIT 1").fetchone()
    return observed_run(row, now) or {"run_id": None, "status": "IDLE", "heartbeat_at": None,
                                      "requested_at": None, "finished_at": None, "error_type": None}


def recent_runs(database) -> list[dict]:  # R3
    return [dict(row) for row in database.execute(
        """SELECT run_id, status, requested_at, heartbeat_at, finished_at, error_type
        FROM graph_service_runs ORDER BY rowid DESC LIMIT 20""").fetchall()]


def pause(database, pid: str) -> dict | None:  # R4
    row = database.execute("SELECT * FROM pause_states WHERE portfolio_id=?", (pid,)).fetchone()
    return dict(row) if row else None


def provider_states(database) -> list[dict]:  # R5
    return [dict(row) for row in database.execute(
        "SELECT provider, ai_paused, reason, quota_json, updated_at FROM subscription_provider_state "
        "ORDER BY provider").fetchall()]


def latest_quota(database, providers: list[dict], now: datetime) -> dict | None:  # R6
    """The newest persisted allowance reading, chosen as the activity view chooses it."""
    candidates = []
    for row in database.execute(
            """SELECT a.provider, a.quota_json, a.created_at FROM subscription_attempts a
            JOIN subscription_invocations i USING(invocation_id)
            WHERE i.synthetic=0 AND a.quota_json <> '{}' ORDER BY a.rowid DESC LIMIT 20""").fetchall():
        quota = sanitize_quota(loads(row["quota_json"]))
        if quota:
            candidates.append({**quota, "provider": row["provider"]})
    for row in providers:
        quota = sanitize_quota(loads(row["quota_json"]))
        if quota:
            candidates.append({**quota, "provider": row["provider"]})
    observed = [item for item in candidates
                if (moment := parse(item.get("observed_at"))) is not None and moment <= now]
    return max(observed, key=lambda item: parse(item["observed_at"]), default=None)


def schedules(database, pid: str) -> list[dict]:  # R7
    return [dict(row) for row in database.execute(
        "SELECT * FROM schedules WHERE portfolio_id=? ORDER BY name, schedule_id", (pid,)).fetchall()]


def active_tasks(database, pid: str) -> list[dict]:  # R8
    marks = ",".join("?" for _ in ACTIVE_TASK)
    return [dict(row) for row in database.execute(
        f"""SELECT * FROM tasks WHERE status IN ({marks}) AND (portfolio_id=? OR portfolio_id IS NULL)
        ORDER BY created_at, task_id""", (*sorted(ACTIVE_TASK), pid)).fetchall()]


def recent_tasks(database, pid: str, now: datetime) -> list[dict]:  # R9
    cursor = database.execute(
        """SELECT t.*, r.status AS result_status, r.document_json AS result_json,
        r.created_at AS result_created_at FROM tasks t LEFT JOIN role_results r USING(task_id)
        WHERE (t.portfolio_id=? OR t.portfolio_id IS NULL) ORDER BY t.rowid DESC""", (pid,))
    return _scan(cursor, now - TASK_WINDOW, TASK_CAP)


def attempt_starts(database, task_ids: list[str]) -> dict[str, str]:  # R10
    starts = {}
    for task_id in task_ids[:WORKING_ATTEMPT_TASKS]:
        row = database.execute(
            """SELECT MIN(a.created_at) AS started FROM subscription_attempts a
            JOIN subscription_invocations i USING(invocation_id) WHERE i.task_id=?""", (task_id,)).fetchone()
        if row and row["started"]:
            starts[task_id] = row["started"]
    return starts


def decisions(database, pid: str, since: datetime) -> list[dict]:  # R11
    cursor = database.execute(
        """SELECT decision_id, action, payload_json, created_at, task_id FROM decisions
        WHERE portfolio_id=? ORDER BY rowid DESC""", (pid,))
    return _scan(cursor, since, DECISION_CAP)


def leader_decisions(database, pid: str) -> list[dict]:  # R12
    return [dict(row) for row in database.execute(
        """SELECT decision_id, state, created_at, task_id FROM leader_decisions WHERE portfolio_id=?
        ORDER BY rowid DESC LIMIT 20""", (pid,)).fetchall()]


def findings(database, pid: str, now_iso: str) -> list[dict]:  # R13
    rows = database.execute(
        """SELECT * FROM findings WHERE (portfolio_id=? OR portfolio_id IS NULL)
        ORDER BY rowid DESC LIMIT 40""", (pid,)).fetchall()
    return [evidence._finding(row, now_iso) for row in rows]


def deferral_streaks(database, pid: str, *, since: datetime, before: datetime | None = None,
                     cap: int = DEFERRAL_CAP) -> list[dict]:  # R14b
    """Allowance-wait streaks, newest first, counted in full back to ``since``.

    Reads only the time, role and task of each ``subscription_quota_deferred`` row, newest first, until
    a row is older than ``since`` (rows up to 90 s older are peeked at to see whether a streak goes on)
    or ``cap`` rows were read. A department's streak breaks after a 90 s gap. A streak is
    ``complete: False`` when it may continue before the oldest row read. Malformed payloads are skipped.
    """
    sql = """SELECT created_at,
        CASE WHEN json_valid(payload_json) THEN json_extract(payload_json, '$.role') END AS role,
        CASE WHEN json_valid(payload_json) THEN json_extract(payload_json, '$.task_id') END AS task_id
        FROM activity_events WHERE (portfolio_id=? OR portfolio_id IS NULL) AND kind='subscription_quota_deferred'"""
    params: list = [pid]
    if before is not None:
        sql += " AND created_at < ?"
        params.append(utc_iso(before))
    open_by_role: dict[str, dict] = {}
    streaks: list[dict] = []
    read, oldest_read = 0, None
    for row in database.execute(sql + " ORDER BY rowid DESC", tuple(params)):
        moment, role = parse(row["created_at"]), row["role"]
        if moment is not None and moment < since:
            # Peek: an older row of the same department within the gap means that streak goes on.
            if moment < since - DEFERRAL_STREAK_GAP:
                break
            streak = open_by_role.get(role) if isinstance(role, str) else None
            if streak is not None and streak["first_at"] - moment <= DEFERRAL_STREAK_GAP:
                streak["complete"] = False
            continue
        if read >= cap:
            for streak in open_by_role.values():  # the cap stopped the scan: streaks near its end may go on
                if oldest_read is not None and streak["first_at"] - oldest_read <= DEFERRAL_STREAK_GAP:
                    streak["complete"] = False
            break
        read += 1
        if moment is None or not isinstance(role, str) or not role:
            continue
        oldest_read = moment if oldest_read is None else min(oldest_read, moment)
        streak = open_by_role.get(role)
        if streak is None or streak["first_at"] - moment > DEFERRAL_STREAK_GAP:
            streak = {"role": role, "first_at": moment, "last_at": moment, "count": 0,
                      "task_id": row["task_id"] if isinstance(row["task_id"], str) else None, "complete": True,
                      "moments": []}
            open_by_role[role] = streak
            streaks.append(streak)
        streak["first_at"] = min(streak["first_at"], moment)
        streak["last_at"] = max(streak["last_at"], moment)
        streak["count"] += 1
        streak["moments"].append(moment)  # lets the story split a streak where a page is cut
    return streaks


def activity_events(database, pid: str, since: datetime) -> list[dict]:  # R14
    marks = ",".join("?" for _ in STORY_EVENT_KINDS)
    cursor = database.execute(
        f"""SELECT event_id, portfolio_id, kind, payload_json, created_at FROM activity_events
        WHERE (portfolio_id=? OR portfolio_id IS NULL) AND kind IN ({marks}) ORDER BY rowid DESC""",
        (pid, *STORY_EVENT_KINDS))
    events = _scan(cursor, since, EVENT_CAP)
    for event in events:
        event["payload"] = loads(event.pop("payload_json"))
    return events


def latest_marks(database, pid: str) -> dict[str, dict]:  # R15
    marks: dict[str, dict] = {}
    for row in database.execute(
            """SELECT asset, mark, quote_currency, observed_at, stale FROM valuation_marks WHERE portfolio_id=?
            ORDER BY rowid DESC LIMIT 20""", (pid,)).fetchall():
        marks.setdefault(row["asset"], dict(row))
    return marks


def latest_fx(database) -> dict | None:  # R16
    row = database.execute(
        "SELECT valid_as_of, retrieved_at, stale FROM fx_rates ORDER BY rowid DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def orders(database, pid: str) -> dict:  # R17
    counts = {row["state"]: row["n"] for row in database.execute(
        "SELECT state, COUNT(*) AS n FROM order_intents WHERE portfolio_id=? GROUP BY state", (pid,)).fetchall()}
    return {"by_state": counts,
            "open": sum(count for state, count in counts.items() if state in OPEN_ORDER_STATES),
            "unclear": sum(count for state, count in counts.items() if state in UNCLEAR_ORDER_STATES)}


def fills(database, pid: str, since: datetime) -> list[dict]:  # R18
    rows = _scan(database.execute(
        """SELECT fill_id, intent_id, document_json, created_at FROM fills WHERE portfolio_id=?
        ORDER BY rowid DESC""", (pid,)), since, DECISION_CAP)
    for row in rows:
        row["document"] = loads(row.pop("document_json"))
    return rows


def secretary(database, pid: str) -> list[dict]:  # R19
    return [dict(row) for row in database.execute(
        """SELECT sequence, role, kind, created_at FROM secretary_reports WHERE portfolio_id=?
        ORDER BY sequence DESC LIMIT 200""", (pid,)).fetchall()]


def rollout(database, pid: str) -> dict | None:  # R20
    row = database.execute(
        """SELECT rollout_id, state, generation, policy_json, activated_at FROM version_rollouts
        WHERE portfolio_id=? ORDER BY generation DESC LIMIT 1""", (pid,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["policy"] = loads(result.pop("policy_json"))
    return result


def improvements(database, pid: str) -> dict:  # R21
    proposed = []
    for row in database.execute(
            """SELECT change_id, state, document_json, created_at FROM change_tasks
            WHERE portfolio_id=? AND state='PROPOSED' LIMIT 20""", (pid,)).fetchall():
        item = dict(row)
        item["document"] = loads(item.pop("document_json"))
        proposed.append(item)
    candidates = [dict(row) for row in database.execute(
        """SELECT c.candidate_id, c.change_id, c.state, c.created_at FROM candidates c
        JOIN change_tasks t USING(change_id) WHERE t.portfolio_id=? AND c.state IN ('READY','FAILED')
        LIMIT 20""", (pid,)).fetchall()]
    events = [dict(row) for row in database.execute(
        """SELECT event_id, kind, created_at FROM version_events WHERE portfolio_id=?
        ORDER BY rowid DESC LIMIT 20""", (pid,)).fetchall()]
    return {"proposed": proposed, "candidates": candidates, "events": events}


def owner_commands(runtime) -> list[dict]:  # R22 (owner only)
    from trade_graph.api.controls import _scope
    from trade_graph.application.owner_commands import command_history

    return command_history(runtime, _scope(runtime, "owner"), limit=40)


def service_requests(database, pid: str) -> list[dict]:  # R23
    result = []
    for row in database.execute(
            """SELECT request_id, action, result_json, created_at FROM service_control_requests
            WHERE portfolio_id=? ORDER BY rowid DESC LIMIT 40""", (pid,)).fetchall():
        item = dict(row)
        item["result"] = loads(item.pop("result_json"))
        result.append(item)
    return result


def bills(database, deployment: str) -> list[dict]:  # R24
    result = []
    for row in database.execute(
            """SELECT sequence, record_type, expense_kind, document_json, created_at FROM owner_expense_evidence
            WHERE deployment_id=? ORDER BY sequence DESC LIMIT 20""", (deployment,)).fetchall():
        item = dict(row)
        item["document"] = loads(item.pop("document_json"))
        result.append(item)
    return result


def owner_revision(runtime) -> int:  # R25 (owner only)
    from trade_graph.api.controls import _revision, _scope

    return _revision(runtime, _scope(runtime, "owner"))


def optimisation_cycle(database, pid: str) -> dict | None:  # R26
    from trade_graph.application.service_controller import cycle_state

    root = database.execute(
        """SELECT * FROM tasks WHERE portfolio_id=? AND objective='owner-optimisation-cycle'
        ORDER BY rowid DESC LIMIT 1""", (pid,)).fetchone()
    if root is None:
        return None
    rows = database.execute("SELECT status, role FROM tasks WHERE root_task_id=?", (root["task_id"],)).fetchall()
    return {"task_id": root["task_id"], "created_at": root["created_at"], "deadline_at": root["deadline_at"],
            "state": cycle_state(rows) if rows else cycle_state([root])}


def resource_observation(runtime) -> dict:  # R27 (30 s cache)
    return resources.observation(runtime)


def checkins(runtime, start: str, end: str, *, owner: bool) -> dict:  # R28 (owner only, P2)
    """Owner check-ins through the WP3 module, imported lazily; absent store means unavailable."""
    empty = {"available": False, "items": [], "last_done_at": None}
    if not owner:
        return empty
    try:
        from trade_graph.api import owner_checkins
    except ImportError:
        return empty
    try:
        if not owner_checkins.available(runtime):
            return empty
        return {"available": True, "items": owner_checkins.window(runtime, start, end),
                "last_done_at": owner_checkins.last_done_at(runtime)}
    except (ValueError, TypeError, LookupError, sqlite3.DatabaseError):
        return empty  # HQ keeps working without check-ins rather than failing the page


def config(runtime) -> dict:  # R29
    settings = getattr(runtime, "config", None)
    return {
        "automatic_schedule": getattr(settings, "automatic_schedule", True),
        "optimisation_manual_only": getattr(settings, "optimisation_manual_only", False),
        "schedule_intervals": getattr(settings, "schedule_intervals", None),
        "optimisation_deadline_seconds": getattr(settings, "optimisation_deadline_seconds", None),
        "public_poll_interval_seconds": getattr(settings, "public_poll_interval_seconds", 10),
        "settings": settings,
    }




def period_orders(database, pid: str, since: datetime) -> list[dict]:  # R17b: orders placed since a moment
    cursor = database.execute(
        "SELECT intent_id, state, created_at FROM order_intents WHERE portfolio_id=? ORDER BY rowid DESC", (pid,))
    return _scan(cursor, since, DECISION_CAP)


def collect(runtime, now: datetime, *, story_start: datetime, prompt_from: datetime, owner: bool) -> dict:
    """Run R1-R27 and R29 once for one snapshot (R28 is read by the caller first)."""
    database, pid = runtime.database, runtime.portfolio_id
    now_iso = utc_iso(now)
    deployment = getattr(runtime, "deployment_id", "deployment")
    providers = provider_states(database)
    tasks = active_tasks(database, pid)
    working = [task["task_id"] for task in tasks if task["status"] in {"LEASED", "RUNNING"}]
    recent = recent_tasks(database, pid, now)
    decision_rows = decisions(database, pid, min(story_start, prompt_from))
    events = activity_events(database, pid, story_start)
    fill_rows = fills(database, pid, story_start)
    floor = story_floor(story_start, (recent, TASK_CAP), (decision_rows, DECISION_CAP), (events, EVENT_CAP),
                        (fill_rows, DECISION_CAP))
    return {
        "now": now, "now_iso": now_iso,
        "portfolio": portfolio(database, pid),
        "run": current_run(database, now),
        "runs": recent_runs(database),
        "pause": pause(database, pid),
        "providers": providers,
        "quota": latest_quota(database, providers, now),
        "schedules": schedules(database, pid),
        "active_tasks": tasks,
        "recent_tasks": recent,
        "attempt_starts": attempt_starts(database, working),
        "decisions": decision_rows,
        "leader_decisions": leader_decisions(database, pid),
        "findings": findings(database, pid, now_iso),
        "events": events,
        # Allowance waits are counted from where the page starts, so an ongoing wait stays on the first page.
        "deferrals": deferral_streaks(database, pid, since=max(story_start, floor or story_start)),
        "story_floor": floor,
        "marks": latest_marks(database, pid),
        "fx": latest_fx(database),
        "orders": orders(database, pid),
        "recent_orders": period_orders(database, pid, prompt_from),
        "fills": fill_rows,
        "secretary": secretary(database, pid),
        "rollout": rollout(database, pid),
        "improvements": improvements(database, pid),
        "commands": owner_commands(runtime) if owner else [],
        "service_requests": service_requests(database, pid),
        "bills": bills(database, deployment),
        "owner_revision": owner_revision(runtime) if owner else None,
        "cycle": optimisation_cycle(database, pid),
        "resources": resource_observation(runtime),
        "config": config(runtime),
    }


def story_before(runtime, *, start: datetime, before: datetime) -> dict:
    """Story inputs for an earlier page: the same readers, restricted to rows before ``before``."""
    database, pid = runtime.database, runtime.portfolio_id
    cutoff = utc_iso(before)
    tasks = _scan(database.execute(
        """SELECT t.*, r.status AS result_status, r.document_json AS result_json,
        r.created_at AS result_created_at FROM tasks t LEFT JOIN role_results r USING(task_id)
        WHERE (t.portfolio_id=? OR t.portfolio_id IS NULL) AND t.created_at < ? ORDER BY t.rowid DESC""",
        (pid, cutoff)), start, TASK_CAP)
    rows = _scan(database.execute(
        """SELECT decision_id, action, payload_json, created_at, task_id FROM decisions
        WHERE portfolio_id=? AND created_at < ? ORDER BY rowid DESC""", (pid, cutoff)), start, DECISION_CAP)
    marks = ",".join("?" for _ in STORY_EVENT_KINDS)
    events = _scan(database.execute(
        f"""SELECT event_id, portfolio_id, kind, payload_json, created_at FROM activity_events
        WHERE (portfolio_id=? OR portfolio_id IS NULL) AND kind IN ({marks}) AND created_at < ?
        ORDER BY rowid DESC""", (pid, *STORY_EVENT_KINDS, cutoff)), start, EVENT_CAP)
    for event in events:
        event["payload"] = loads(event.pop("payload_json"))
    fill_rows = []
    for row in _scan(database.execute(
            """SELECT fill_id, intent_id, document_json, created_at FROM fills
            WHERE portfolio_id=? AND created_at < ? ORDER BY rowid DESC""", (pid, cutoff)), start, DECISION_CAP):
        row["document"] = loads(row.pop("document_json"))
        fill_rows.append(row)
    runs = [row for row in recent_runs(database)
            if (moment := parse(row.get("requested_at"))) is not None and moment < before]
    floor = story_floor(start, (tasks, TASK_CAP), (rows, DECISION_CAP), (events, EVENT_CAP),
                        (fill_rows, DECISION_CAP))
    return {"recent_tasks": tasks, "decisions": rows, "events": events, "fills": fill_rows, "runs": runs,
            "deferrals": deferral_streaks(database, pid, since=max(start, floor or start), before=before),
            "story_floor": floor}
