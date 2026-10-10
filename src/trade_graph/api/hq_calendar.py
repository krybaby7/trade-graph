"""HQ calendar (spec §B.5.10): department turns, owner check-ins and dated deadlines.

Only the next turn of a schedule is exact. Later turns are estimates
(``base + k * interval``), because each turn books the next one when it actually
runs; when the team cannot run them they are also marked conditional. Estimates
more than 48 hours ahead are not listed one by one: each schedule's later
projections are summarised in one ``calendar.estimates`` series (payload budget, D110).
Close deadlines of one kind (research notes written together expire together) are one
item with a ``count`` and an ``until`` (D111).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from trade_graph.api import hq_copy as c
from trade_graph.api.hq_contract import (
    AI_DEPARTMENTS,
    CALENDAR_GROUP_WINDOW_SECONDS,
    CALENDAR_GROUPED_KINDS,
    CALENDAR_MAX_ITEMS,
    ESTIMATE_ITEMS_HORIZON_HOURS,
    SERIES_MAX_PROJECTIONS,
    UP_NEXT_MAX,
    iso_ms,
)
from trade_graph.api.hq_state import SCHEDULED_ROLES, STUCK, Projection, _dt

PAST_WINDOW = timedelta(hours=48)
ESTIMATE_ITEMS_HORIZON = timedelta(hours=ESTIMATE_ITEMS_HORIZON_HOURS)
MANDATE_HORIZON = timedelta(days=30)
GROUP_WINDOW = timedelta(seconds=CALENDAR_GROUP_WINDOW_SECONDS)
RESEARCH_EXPIRY_MAX = 10  # research-note expiry items (single or grouped), soonest first
HISTORY_INTERVAL_SECONDS = 3600
# Gates under which a department's turns may not happen at all, and what its next turn depends on (U1).
CONDITIONAL_GATES = frozenset({"G1", "G2", "G3", "G4", "G5", "G6", "GM"})
FIRST_TURN_CONDITION = {
    "G1": c.CALENDAR["only_if_running"],
    "G2": c.CALENDAR["only_if_ai_on"],
    "G3": c.CALENDAR["only_if_allowance"],
    "G4": c.GATE["G4"],
    "G5": c.CALENDAR["only_if_resume"],
    "G6": c.CALENDAR["only_if_timetable"],
    "GM": c.CALENDAR["only_if_ai_available"],
}


def calendar(p: Projection, days: int, checkins: list[dict]) -> dict:
    start, end = p.now - PAST_WINDOW, p.now + timedelta(days=days)
    items: list[dict] = []
    notes: list[list] = []

    def add(item: dict | None) -> None:
        moment = _dt(item["at"]) if item else None
        if moment is not None and start <= moment <= end:
            items.append(item)

    estimates: list[dict] = []
    for dept in SCHEDULED_ROLES:
        turns, estimate = _future_turns(p, p.roles[dept], end)
        for item in turns:
            add(item)
        if estimate is not None:
            estimates.append(estimate)
    for item in _past_turns(p):
        add(item)
    for item in checkins:
        add(_checkin_item(p, item))
    for item in _grouped([item for item in _deadlines(p) if item and start <= _dt(item["at"]) <= end]):
        add(item)
    refills = [_refill(p, window) for window in p.windows if window["resets_at"]]
    for item in refills:
        add(item)
    if p.quota and not refills:
        notes.append(c.says(c.CALENDAR["refill_unknown"]))
    if not p.facts["schedules"]:
        notes.insert(0, c.says(c.CALENDAR["no_turns"]))
    unique: dict[str, dict] = {}
    for item in items:
        unique.setdefault(item["id"], item)
    ordered = sorted(unique.values(), key=lambda item: (item["at"], item["kind"] != "owner_checkin", item["id"]))
    truncated = len(ordered) > CALENDAR_MAX_ITEMS
    ordered = ordered[:CALENDAR_MAX_ITEMS]
    upcoming = [item for item in ordered if not item["past"] and item["status"] != "estimated"]
    return {"from": iso_ms(start), "to": iso_ms(end), "truncated": truncated,
            "up_next": [item["id"] for item in upcoming[:UP_NEXT_MAX]], "items": ordered,
            "recurring": _recurring(p), "notes": notes, "estimates": estimates}


def _item(item_id: str, kind: str, dept, at: datetime | str, title: list, status: str, p: Projection, *,
          exact: bool = True, conditional: bool = False, detail=None, warning=None, waiting=None, link=None,
          checkin_id=None, series=None) -> dict | None:
    moment = _dt(at)
    if moment is None:
        return None  # an unreadable timestamp is left out, never guessed
    return {"id": item_id, "kind": kind, "dept": dept, "at": iso_ms(moment), "title": title, "status": status,
            "exact": exact, "conditional": conditional, "past": moment < p.now, "detail": detail,
            "warning": warning, "waiting_because": waiting, "link": link, "checkin_id": checkin_id,
            "series": series}


def _turn_title(dept: str) -> list:
    return c.says(c.CALENDAR["turn"].format(dept=c.dept_name(dept)))


def _future_turns(p: Projection, role, end: datetime) -> tuple[list, dict | None]:
    """A schedule's next turn and its estimated successors up to ``end``.

    Estimates up to 48 hours ahead are listed as items; later ones (within the same
    ``SERIES_MAX_PROJECTIONS`` cap) come back as one summarised series, or None.
    """
    if role.schedule is None or role.next_at is None:
        return [], None
    schedule_id = role.schedule["schedule_id"]
    series = {"schedule_id": schedule_id, "every_seconds": role.every} if role.every else None
    gate = role.blocking_gate
    conditional = gate in CONDITIONAL_GATES
    upcoming = not role.overdue
    first_detail = c.says(FIRST_TURN_CONDITION[gate]) if upcoming and gate in FIRST_TURN_CONDITION else None
    items: list = [_item(f"turn:{schedule_id}:{iso_ms(role.next_at)}", "dept_turn", role.dept, role.next_at,
                   _turn_title(role.dept), "planned" if upcoming else "overdue", p, detail=first_detail,
                   conditional=conditional and upcoming, waiting=None if upcoming else role.gate_text,
                   series=series)]
    if not role.every:
        return items, None
    # The scheduler books the next turn at the moment a turn fires, plus the interval
    # (scheduler.py), so an overdue turn's successors count from now, never from the missed time (C3).
    base = max(role.next_at, p.now)
    detail = c.says(c.CONDITIONAL) if conditional else None
    horizon = p.now + ESTIMATE_ITEMS_HORIZON
    later: list[datetime] = []
    for step in range(1, SERIES_MAX_PROJECTIONS + 1):
        at = base + timedelta(seconds=role.every * step)
        if at <= p.now:
            continue
        if at > end:
            break
        if at > horizon:
            later.append(at)
            continue
        items.append(_item(f"turn:{schedule_id}:{iso_ms(at)}", "dept_turn", role.dept, at, _turn_title(role.dept),
                           "estimated", p, exact=False, conditional=conditional, detail=detail, series=series))
    if not later:
        return items, None
    return items, {"schedule_id": schedule_id, "dept": role.dept, "title": _turn_title(role.dept),
                   "first_at": iso_ms(later[0]), "every_seconds": role.every, "count": len(later),
                   "conditional": conditional, "detail": detail}


def _task_status(p: Projection, task: dict) -> tuple[str, list | None]:
    status = task["status"]
    if status == "SUCCEEDED":
        return "done", None
    if p.is_skip(task):
        reason = c.SKIP_PLAIN[p.skip_key(task)]
        return "skipped", c.says(c.CALENDAR["skipped"].format(reason=reason))
    if status == "CANCELLED":
        return "cancelled", None
    if status in {"FAILED", "DEAD_LETTER"}:
        reason = c.reason_plain(p.raw_reason(task), live=not p.paper) or c.REASON["unknown"]
        return "problem", c.says(c.CALENDAR["done_problem"].format(reason=reason))
    if status in STUCK:
        return "stuck", c.says(c.CALENDAR["stuck"])
    return "waiting", c.says(c.CALENDAR["waiting"])


def _past_turns(p: Projection) -> list:
    items = []
    for task in p.facts["recent_tasks"]:
        key = task.get("dedup_key") or ""
        if task["role"] not in AI_DEPARTMENTS:
            continue
        status, detail = _task_status(p, task)
        if key.startswith("schedule:"):
            parts = key.split(":", 2)
            if len(parts) != 3 or _dt(parts[2]) is None:
                continue
            schedule = p.schedule_by_id.get(parts[1])
            every = schedule["interval_seconds"] if schedule and schedule["interval_seconds"] > 0 else None
            series = {"schedule_id": parts[1], "every_seconds": every} if every else None
            items.append(_item(f"turn:{parts[1]}:{iso_ms(_dt(parts[2]))}", "dept_turn", task["role"],
                               task["created_at"], _turn_title(task["role"]), status, p, detail=detail,
                               series=series))
        elif key.startswith("secretary:"):
            detail = c.join(c.says(c.CALENDAR["extra_review_detail"]), detail)
            items.append(_item(f"review:{task['task_id']}", "extra_review", task["role"], task["created_at"],
                               c.says(c.CALENDAR["extra_review"].format(dept=c.dept_name(task["role"]))),
                               status, p, detail=detail, link="/organization"))
    return items


def _checkin_item(p: Projection, item: dict) -> dict:
    return _item(f"checkin:{item['checkin_id']}", "owner_checkin", "you", item["planned_at"],
                 c.says(c.CALENDAR["checkin"].format(title=item["title"])), item["state"], p,
                 checkin_id=item["checkin_id"])


def _refill(p: Projection, window: dict) -> dict:
    label = c.CALENDAR["allowance_refill_labels"][window["label"]]
    return _item(f"refill:{window['name']}", "allowance_refill", None, window["resets_at"],
                 c.says(c.CALENDAR["allowance_refill"].format(label=label)), "planned", p)


def _deadlines(p: Projection) -> list:
    items = []
    trader_next = p.roles["trader"].next_at
    for finding in p.notes_in_use:  # at most 40 (reader R13); grouped and capped in _grouped
        expires = _dt(finding.get("expires_at"))
        if expires is None:
            continue
        warning = None
        if trader_next is not None and expires < trader_next:
            warning = c.says(c.CALENDAR["research_expiry_warning"], c.time(trader_next, "clock"), ".")
        items.append(_item(f"research-expiry:{finding['finding_id']}", "research_expiry", "research", expires,
                           c.says(c.CALENDAR["research_expiry"]), "planned", p, warning=warning))
    cycle = p.facts["cycle"]
    if cycle and _dt(cycle.get("deadline_at")):
        items.append(_item(f"optimisation-deadline:{cycle['task_id']}", "optimisation_deadline", "optimisation",
                           cycle["deadline_at"], c.says(c.CALENDAR["optimisation_deadline"]), "planned", p))
    rollout = p.facts["rollout"]
    if rollout and rollout["state"] in ("OBSERVING", "RESTART_PENDING") and _dt(rollout.get("activated_at")):
        policy = rollout.get("policy") or {}
        if rollout["state"] == "OBSERVING":
            seconds = policy.get("horizon_seconds") if type(policy.get("horizon_seconds")) is int else 3600
            title = c.CALENDAR["trial_end"]
        else:
            seconds = (policy.get("reload_timeout_seconds") if type(policy.get("reload_timeout_seconds")) is int
                       else 60)
            title = c.CALENDAR["reload_end"]
        at = _dt(rollout["activated_at"]) + timedelta(seconds=seconds)
        items.append(_item(f"trial:{rollout['rollout_id']}", "trial_end", "leader", at, c.says(title), "estimated",
                           p, exact=False, link="/changes"))
    for change in p.facts["improvements"]["proposed"]:
        expires = _dt(change["document"].get("expires_at_utc"))
        if expires is not None:
            items.append(_item(f"proposal-expiry:{change['change_id']}", "proposal_expiry", "leader", expires,
                               c.says(c.CALENDAR["proposal_expiry"]), "planned", p, link="/changes"))
    for task in p.roles["engineer"].active:
        deadline = _dt(task.get("deadline_at"))
        if deadline is not None:
            items.append(_item(f"engineer-deadline:{task['task_id']}", "engineer_deadline", "engineer", deadline,
                               c.says(c.CALENDAR["engineer_deadline"]), "planned", p, link="/changes"))
    mandate = p.cache.get("mandate") or {}
    expires = _dt(mandate.get("expires_at"))
    if expires is not None and p.now <= expires <= p.now + MANDATE_HORIZON:
        items.append(_item("mandate-expiry", "mandate_expiry", "trader", expires,
                           c.says(c.CALENDAR["mandate_expiry"]), "planned", p, link="/trading"))
    return items


def _grouped(items: list[dict]) -> list[dict]:
    """Close deadlines of one kind become one item with ``count`` and ``until`` (contract ``CalendarItem``).

    An item joins the open group with the same kind, department, status, link and texts while it is
    within ``GROUP_WINDOW`` of that group's earliest member; otherwise it starts a new group. Groups keep
    their earliest member's id. Research-note expiries are then capped at ``RESEARCH_EXPIRY_MAX`` items.
    """
    open_groups: dict[tuple, list[dict]] = {}
    groups: list[list[dict]] = []
    for item in sorted(items, key=lambda entry: (entry["at"], entry["id"])):
        key = None
        if item["kind"] in CALENDAR_GROUPED_KINDS:
            key = (item["kind"], item["dept"], item["status"], item["past"], item["link"],
                   json.dumps([item["title"], item["detail"], item["warning"]], sort_keys=True))
            members = open_groups.get(key)
            if members and _dt(item["at"]) - _dt(members[0]["at"]) <= GROUP_WINDOW:
                members.append(item)
                continue
        groups.append([item])
        if key is not None:
            open_groups[key] = groups[-1]
    result: list[dict] = []
    research = 0
    for members in groups:
        first = members[0]
        if first["kind"] == "research_expiry":
            research += 1
            if research > RESEARCH_EXPIRY_MAX:
                continue
        if len(members) > 1:
            title = c.says(c.CALENDAR[f"{first['kind']}_many"].format(n=len(members)))
            first = {**first, "title": title, "count": len(members), "until": members[-1]["at"]}
        result.append(first)
    return result


def _recurring(p: Projection) -> list:
    if not p.active or p.history_event is None:
        return []
    payload = p.history_event["payload"]
    next_at = _dt(payload.get("next_refresh_at_utc"))
    return [{"id": "history", "title": c.says(c.CALENDAR["history"]), "every_seconds": HISTORY_INTERVAL_SECONDS,
             "next_at": iso_ms(next_at) if next_at else None, "machine": True}]

