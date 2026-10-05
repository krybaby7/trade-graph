"""Read-only project and runtime progress, without promoting fixtures to acceptance."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from trade_graph.api import evidence
from trade_graph.domain.clock import utc_iso

_SOURCE_ROOT = Path(__file__).resolve().parents[3]
_PACKAGED_CATALOG = Path(__file__).resolve().parents[1] / "progress_catalog.json"
_MAX_BYTES = 1024 * 1024
_STATUSES = {"done", "in_progress", "blocked", "pending", "not_started"}
_DESCRIPTIONS = {
    "T00": "Set up the Python project and repeatable development tools.",
    "T01": "Define the information agents exchange and who may control money.",
    "T02": "Keep durable records that survive restarts and support recovery.",
    "T03": "Track balances, trading fees, expenses and portfolio results in EUR.",
    "T04": "Collect timestamped prices and replay observations without future information.",
    "T05": "Simulate orders with explicit fill and cost assumptions for software testing.",
    "T06": "Connect the same agent contracts to OpenAI and Anthropic.",
    "T07": "Limit real API expenses and record actual or uncertain usage.",
    "T08": "Handle orders, cancellations, uncertainty and pauses without duplicate trades.",
    "T09": "Schedule agent work and recover unfinished tasks safely.",
    "T10": "Let Research gather evidence and Trader make decisions within its mandate.",
    "T11": "Review decisions and test improvements against evidence and costs.",
    "T12": "Let Leader coordinate work while software Secretary schedules and records it.",
    "T13": "Let Engineer propose bounded artifacts that independent checks must verify.",
    "T14": "Activate tested artifacts and roll back without deleting financial history.",
    "T15": "Provide private dashboards and owner controls for progress and operations.",
    "T16": "Exercise the complete loop and failure recovery without credentials.",
    "T17": "Connect approved public data and a funded AI provider for a real paper-service run.",
    "T18": "Collect forward results and compare all-in economics against simple baselines.",
    "T19": "Verify the chosen exchange through authenticated, separately authorized checks.",
    "T20": "Permit a small real-money trial only after evidence and explicit owner allocation.",
    "T21": "Verify protected processes and host isolation before broader code authority.",
    "T22": "Extend Engineer beyond current artifacts only after the additional gates are satisfied.",
}
_EVIDENCE = {
    "T17": "Software preparation exists; genuine provider, public-data and intended-host evidence is pending.",
    "T18": "Evaluation tools exist; genuine untouched forward economic evidence is pending.",
    "T19": "Adapter preparation exists; authenticated Kraken conformance is pending.",
    "T20": "Live pilot remains blocked; test results do not authorize real orders.",
    "T21": "Local isolation evidence exists; intended-host verification and broader grants remain pending.",
    "T22": "Broader Engineer scope remains blocked; current artifact scope is retained.",
}
_DEPARTMENTS = (
    (
        "leader",
        "Leader",
        "Sets priorities, commissions work and reviews outcomes.",
        "AI model gateway",
        "/organization",
    ),
    (
        "research",
        "Research",
        "Gathers sourced market evidence for trading decisions.",
        "Model gateway + market data",
        "/organization",
    ),
    (
        "trader",
        "Trader",
        "Chooses trades or waits within the approved mandate.",
        "Model gateway; execution service",
        "/trading",
    ),
    (
        "learning",
        "Learning",
        "Reviews results and maintains evidence-backed lessons.",
        "AI model gateway",
        "/organization",
    ),
    ("optimisation", "Optimisation", "Measures quality, cost and experiment outcomes.", "AI model gateway", "/costs"),
    (
        "engineer",
        "Improvement Engineer",
        "Builds bounded improvements for independent checking.",
        "Model gateway; protected checks",
        "/changes",
    ),
    (
        "secretary",
        "Secretary",
        "Schedules tasks and keeps the organization coordinated.",
        "Local scheduler and records",
        "/organization",
    ),
    (
        "execution",
        "Execution & Accounting",
        "Handles orders, balances, reconciliation and EUR reports.",
        "Local paper broker; Kraken private API conditional; ECB FX",
        "/trading",
    ),
    (
        "market",
        "Market Data",
        "Collects price observations for Research and Trader.",
        "Kraken public REST / WebSocket adapter",
        "/organization",
    ),
)
_CONNECTIONS = (
    ("leader", "secretary", "priorities"),
    ("secretary", "research", "assignments"),
    ("secretary", "trader", "schedule"),
    ("secretary", "learning", "review tasks"),
    ("secretary", "optimisation", "experiments"),
    ("leader", "engineer", "commissions"),
    ("market", "research", "observations"),
    ("market", "trader", "prices"),
    ("research", "trader", "findings"),
    ("trader", "execution", "order decisions"),
    ("execution", "learning", "results"),
    ("execution", "optimisation", "costs & outcomes"),
    ("learning", "leader", "lessons"),
    ("optimisation", "leader", "measurements"),
    ("engineer", "leader", "checked candidates"),
)
_MILESTONES = (
    (
        "foundation",
        "Software foundation",
        "Scoped implementation and offline checks; external operation is separate.",
        tuple(f"T{i:02}" for i in range(17)),
    ),
    (
        "connected-paper",
        "Connected paper run",
        "Real public data and approved AI calls, with virtual trading money.",
        ("T17",),
    ),
    (
        "economics",
        "After-cost evidence",
        "Forward comparison with holding and simple strategies, including all costs.",
        ("T18",),
    ),
    (
        "kraken-checks",
        "Kraken account checks",
        "Actual authenticated exchange evidence after account readiness and authorization.",
        ("T19",),
    ),
    (
        "live-pilot",
        "Bounded live trial",
        "A separate real-money decision with explicit limits and owner activation.",
        ("T20",),
    ),
)


def _bounded_json(path: Path) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise ValueError("progress document exceeds size limit")
    value = json.loads(raw)
    if (
        not isinstance(value, dict)
        or isinstance(value.get("schema_version"), bool)
        or value.get("schema_version") != 1
    ):
        raise ValueError("invalid progress document")
    return value


def _text(value, limit: int = 300) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("invalid progress text")
    return value


def _normalize_tasks(tasks: list, states: dict | None = None) -> list[dict]:
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 100:
        raise ValueError("invalid project tasks")
    result, seen = [], set()
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("invalid project task")
        task_id = _text(task.get("id"), 8)
        if not re.fullmatch(r"T\d{2}", task_id) or task_id in seen:
            raise ValueError("invalid task identifier")
        seen.add(task_id)
        state = states.get(task_id) if states is not None else task
        if not isinstance(state, dict) or state.get("status") not in _STATUSES:
            raise ValueError("missing task status")
        dependencies = task.get("deps" if states is not None else "dependencies")
        if not isinstance(dependencies, list) or len(dependencies) > 100:
            raise ValueError("invalid task dependencies")
        if any(not isinstance(dep, str) for dep in dependencies):
            raise ValueError("invalid task dependency")
        status = state["status"]
        result.append(
            {
                "id": task_id,
                "title": _text(task.get("title")),
                "status": status,
                "description": _DESCRIPTIONS.get(task_id, _text(task.get("title"))),
                "phase": _text(task.get("phase"), 20),
                "dependencies": list(dependencies),
                "evidence_summary": _EVIDENCE.get(
                    task_id,
                    (
                        "Scoped implementation recorded complete; this is not a live or profitability certification."
                        if status == "done"
                        else "Task acceptance remains pending."
                    ),
                ),
            }
        )
    if any(dep not in seen or dep == task["id"] for task in result for dep in task["dependencies"]):
        raise ValueError("unknown task dependency")
    return result


def _project() -> dict:
    """Refresh checkout planning files; wheels expose a clearly dated curated snapshot."""
    tasks_path, states_path = _SOURCE_ROOT / "planning/tasks.json", _SOURCE_ROOT / "planning/progress.json"
    checkout = tasks_path.exists() or states_path.exists() or (_SOURCE_ROOT / ".git").exists()
    try:
        if checkout:
            catalog, states = _bounded_json(tasks_path), _bounded_json(states_path)
            if not isinstance(states.get("tasks"), dict):
                raise ValueError("invalid task states")
            tasks = _normalize_tasks(catalog.get("tasks"), states["tasks"])
            updated_at, source = _text(states.get("updated_at"), 40), "repository"
        else:
            catalog = _bounded_json(_PACKAGED_CATALOG)
            tasks = _normalize_tasks(catalog.get("tasks"))
            updated_at, source = _text(catalog.get("updated_at"), 40), "packaged_snapshot"
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", updated_at):
            raise ValueError("invalid progress date")
        datetime.strptime(updated_at, "%Y-%m-%d")
    except (OSError, ValueError, TypeError, RecursionError):
        return {
            "completed": None,
            "total": None,
            "in_progress": None,
            "blocked": None,
            "percent": None,
            "source": "unavailable",
            "updated_at": None,
            "tasks": [],
        }
    completed = sum(task["status"] == "done" for task in tasks)
    return {
        "completed": completed,
        "total": len(tasks),
        "in_progress": sum(task["status"] == "in_progress" for task in tasks),
        "blocked": sum(task["status"] == "blocked" for task in tasks),
        "percent": round(completed * 100 / len(tasks)),
        "source": source,
        "updated_at": updated_at,
        "tasks": tasks,
    }


def _time(value: str | None) -> datetime | None:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else None
        return result.astimezone(UTC) if result and result.tzinfo else None
    except (TypeError, ValueError):
        return None


def _service(runtime, now: str) -> dict:
    lease = runtime.database.execute(
        "SELECT expires_at FROM process_leases WHERE lease_name = 'paper-service'",
    ).fetchone()
    expires = _time(lease["expires_at"]) if lease else None
    active = bool(expires and expires > _time(now))
    return {
        "status": "running" if active else "idle",
        "label": "Paper service lease current" if active else "No current paper service lease",
        "last_seen": None,
        "lease_expires_at": lease["expires_at"] if lease else None,
    }


def _departments(runtime, now: str) -> list[dict]:
    rows = runtime.database.execute(
        """SELECT role, COUNT(*) AS task_count, SUM(status = 'SUCCEEDED') AS completed_count,
        SUM(status IN ('LEASED','RUNNING') AND julianday(lease_expires_at) > julianday(?)) AS running,
        SUM(status = 'QUEUED') AS queued,
        SUM(status IN ('BLOCKED_BUDGET','WAITING_EXTERNAL') OR
            (status IN ('LEASED','RUNNING') AND
            (lease_expires_at IS NULL OR julianday(lease_expires_at) IS NULL OR
             julianday(lease_expires_at) <= julianday(?)))) AS waiting,
        MAX(created_at) AS last_activity
        FROM tasks WHERE (portfolio_id = ? OR portfolio_id IS NULL)
        AND julianday(created_at) <= julianday(?) GROUP BY role""",
        (now, now, runtime.portfolio_id, now),
    ).fetchall()
    by_role = {row["role"]: row for row in rows}
    departments = []
    for role, name, purpose, api, href in _DEPARTMENTS:
        row = by_role.get(role)
        status = "idle"
        if row:
            status = (
                "running" if row["running"] else "queued" if row["queued"] else "waiting" if row["waiting"] else "idle"
            )
        departments.append(
            {
                "id": role,
                "name": name,
                "purpose": purpose,
                "status": status,
                "task_count": row["task_count"] if row else 0,
                "completed_count": row["completed_count"] if row else 0,
                "last_activity": row["last_activity"] if row else None,
                "api": api,
                "href": href,
            }
        )
    return departments


def _milestones(project: dict) -> list[dict]:
    states = {task["id"]: task["status"] for task in project["tasks"]}
    result = []
    for milestone_id, title, description, task_ids in _MILESTONES:
        relevant = [states.get(task_id) for task_id in task_ids]
        status = "pending"
        if all(state == "done" for state in relevant):
            status = "completed"
        elif "blocked" in relevant:
            status = "blocked"
        elif "in_progress" in relevant:
            status = "in_progress"
        result.append(
            {
                "id": milestone_id,
                "title": title,
                "description": description,
                "status": status,
                "task_ids": list(task_ids),
            }
        )
    return result


def _activity(runtime, now: str) -> list[dict]:
    activity = []
    # The journal projection supplies the existing portfolio scope and redaction.
    # Its payload remains behind the evidence page rather than being copied here.
    for event in evidence.events(runtime, limit=20)["events"]:
        timestamp = _time(event.get("created_at"))
        if timestamp is None or timestamp > _time(now):
            continue
        kind = str(event.get("kind", "event"))[:80]
        activity.append(
            {
                "id": "event:" + str(event["event_id"]),
                "kind": "event",
                "title": kind.replace("_", " ").replace(".", " ").capitalize(),
                "detail": "Recorded runtime event. Open the journal to inspect its evidence.",
                "at": event["created_at"],
                "source": event["source"],
                "href": "/organization",
            }
        )
    rows = runtime.database.execute(
        """SELECT task_id, role, status, created_at FROM tasks
        WHERE (portfolio_id = ? OR portfolio_id IS NULL) AND julianday(created_at) <= julianday(?)
        ORDER BY created_at DESC, task_id DESC LIMIT 20""",
        (runtime.portfolio_id, now),
    ).fetchall()
    names = {role: name for role, name, *_ in _DEPARTMENTS}
    for row in rows:
        activity.append(
            {
                "id": "task:" + row["task_id"],
                "kind": "task",
                "title": names.get(row["role"], "Scheduled") + " task",
                "detail": "Current recorded state: " + row["status"].lower().replace("_", " ") + ".",
                "at": row["created_at"],
                "source": "task journal",
                "href": "/organization",
            }
        )
    return sorted(activity, key=lambda item: (_time(item["at"]), item["id"]), reverse=True)[:20]


def progress(runtime, test_runs: list[dict] | None = None) -> dict:
    """Project implementation acceptance separately from operational and exchange tests."""
    project = _project()
    with runtime.database.snapshot():
        now = utc_iso(runtime.clock.now())
        return {
            "as_of": now,
            "project": project,
            "departments": _departments(runtime, now),
            "connections": [{"from": source, "to": target, "label": label} for source, target, label in _CONNECTIONS],
            "milestones": _milestones(project),
            "service": _service(runtime, now),
            "activity": _activity(runtime, now),
            "next_step": {
                "title": "Prepare Kraken account checks",
                "description": "After verification, configure read-only access and collect real exchange evidence. "
                "Local software checks can run now; they do not verify Kraken or authorize live orders.",
            },
            "test_runs": list(test_runs or []),
        }
