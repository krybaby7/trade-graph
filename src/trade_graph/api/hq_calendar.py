"""HQ calendar (spec §B.5.10): department turns, owner check-ins and dated deadlines.

Only the next turn of a schedule is exact. Later turns are estimates
(``base + k * interval``), because each turn books the next one when it actually
runs; when the team cannot run them they are also marked conditional.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from trade_graph.api import hq_copy as c
from trade_graph.api.hq_contract import (
    AI_DEPARTMENTS,
    CALENDAR_MAX_ITEMS,
    SERIES_MAX_PROJECTIONS,
    UP_NEXT_MAX,
    iso_ms,
)
from trade_graph.api.hq_state import SCHEDULED_ROLES, STUCK, Projection, _dt

PAST_WINDOW = timedelta(hours=48)
MANDATE_HORIZON = timedelta(days=30)
RESEARCH_EXPIRY_MAX = 10
HISTORY_INTERVAL_SECONDS = 3600


def calendar(p: Projection, days: int, checkins: list[dict]) -> dict:
    start, end = p.now - PAST_WINDOW, p.now + timedelta(days=days)
    items: list[dict] = []
    notes: list[list] = []

    def add(item: dict) -> None:
        moment = _dt(item["at"])
        if moment is not None and start <= moment <= end:
            items.append(item)

    for dept in SCHEDULED_ROLES:
        for item in _future_turns(p, p.roles[dept], end):
            add(item)
    for item in _past_turns(p):
        add(item)
    for item in checkins:
        add(_checkin_item(p, item))
    for item in _deadlines(p):
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
            "recurring": _recurring(p), "notes": notes}


def _item(item_id: str, kind: str, dept, at: datetime | str, title: list, status: str, p: Projection, *,
          exact: bool = True, conditional: bool = False, detail=None, warning=None, waiting=None, link=None,
          checkin_id=None, series=None) -> dict:
    moment = _dt(at)
    return {"id": item_id, "kind": kind, "dept": dept, "at": iso_ms(moment), "title": title, "status": status,
            "exact": exact, "conditional": conditional, "past": moment < p.now, "detail": detail,
            "warning": warning, "waiting_because": waiting, "link": link, "checkin_id": checkin_id,
            "series": series}


def _turn_title(dept: str) -> list:
    return c.says(c.CALENDAR["turn"].format(dept=c.dept_name(dept)))


def _future_turns(p: Projection, role, end: datetime) -> list:
    if role.schedule is None or role.next_at is None:
        return []
    schedule_id = role.schedule["schedule_id"]
    series = {"schedule_id": schedule_id, "every_seconds": role.every} if role.every else None
    gate = role.blocking_gate
    first_detail = None
    if not role.overdue:
        if gate == "G1":
            first_detail = c.says(c.CALENDAR["only_if_running"])
        elif gate == "G5":
            first_detail = c.says(c.CALENDAR["only_if_resume"])
    items = [_item(f"turn:{schedule_id}:{iso_ms(role.next_at)}", "dept_turn", role.dept, role.next_at,
                   _turn_title(role.dept), "overdue" if role.overdue else "planned", p, detail=first_detail,
                   waiting=role.gate_text if role.overdue else None, series=series)]
    if not role.every:
        return items
    conditional = gate in {"G1", "G2", "G3", "G4", "G5", "G6"}
    base = max(role.next_at, p.now) if conditional else role.next_at
    detail = c.says(c.CONDITIONAL) if conditional else None
    for step in range(1, SERIES_MAX_PROJECTIONS + 1):
        at = base + timedelta(seconds=role.every * step)
        if at > end:
            break
        items.append(_item(f"turn:{schedule_id}:{iso_ms(at)}", "dept_turn", role.dept, at, _turn_title(role.dept),
                           "estimated", p, exact=False, conditional=conditional, detail=detail, series=series))
    return items


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
        reason = c.reason_plain(p.raw_reason(task)) or c.REASON["unknown"]
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
    for finding in p.notes_in_use[:RESEARCH_EXPIRY_MAX]:
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


def _recurring(p: Projection) -> list:
    if not p.active or p.history_event is None:
        return []
    payload = p.history_event["payload"]
    next_at = _dt(payload.get("next_refresh_at_utc"))
    return [{"id": "history", "title": c.says(c.CALENDAR["history"]), "every_seconds": HISTORY_INTERVAL_SECONDS,
             "next_at": iso_ms(next_at) if next_at else None, "machine": True}]

