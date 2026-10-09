"""Read-only department, usage and heartbeat views from retained business journals.

No readiness, process, credential or quota probe runs while rendering this view.
Native generation completion and validated task completion are separate outcomes.
"""

from __future__ import annotations

from datetime import datetime

from trade_graph.adapters.models.subscription import sanitize_quota
from trade_graph.api import evidence
from trade_graph.api.security import redact
from trade_graph.domain.clock import utc_iso

_ROLES = {
    "leader": "Leader", "research": "Researcher", "trader": "Trader", "learning": "Learning Analyst",
    "optimisation": "Optimisation Analyst", "engineer": "Improvement Engineer",
}
_FIELDS = ("uncached_input_tokens", "cache_read_tokens", "cache_write_tokens", "billed_output_tokens",
           "reasoning_tokens", "tool_units")
_MEASURES = (*_FIELDS, "input_tokens", "output_tokens", "total_tokens")


def _number(value) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _usage(document: dict) -> dict[str, int | None]:
    missing = document.get("unreported_fields", [])
    if not isinstance(missing, list):
        missing = []
    values = {field: None if field in missing else _number(document.get(field)) for field in _FIELDS}
    reported = _number(document.get("provider_reported_input_tokens"))
    input_parts = [values[field] for field in _FIELDS[:3]]
    values["input_tokens"] = (reported if reported is not None else
                              sum(input_parts) if all(value is not None for value in input_parts) else None)
    values["output_tokens"] = values["billed_output_tokens"]
    values["total_tokens"] = (values["input_tokens"] + values["output_tokens"]
                              if values["input_tokens"] is not None and values["output_tokens"] is not None else None)
    return values


def _native(row, *, source: str, parent: dict | None = None) -> dict:
    parent = parent or row
    result = evidence._json(row.get("result_json"))
    usage = evidence._json(row.get("usage_json")) or result.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    status = "FAILED" if row["state"] == "COMPLETED" and result.get("ok") is False else row["state"]
    return {
        "invocation_id": parent["invocation_id"], "attempt_id": row.get("attempt_id", parent["invocation_id"]),
        "attempt_index": row.get("attempt_index", 1), "attempt_kind": row.get("attempt_kind", "primary"),
        "task_id": parent["task_id"],
        "root_task_id": parent["root_task_id"], "run_id": parent["run_id"], "role": parent["role"],
        "system_version_id": parent["system_version_id"], "provider": row["provider"],
        "requested_model": row["requested_model"], "model": row.get("actual_model") or row["requested_model"],
        "status": status, "persisted_state": row["state"], "created_at": row["created_at"],
        "updated_at": row["updated_at"], "completed_at": row["updated_at"] if status == "COMPLETED" else None,
        "synthetic": bool(parent.get("synthetic", False)), "source": source,
        "failure": result.get("failure"), "usage": _usage(usage),
        "usage_reported": bool(usage), "quota": sanitize_quota(evidence._json(row.get("quota_json"))),
        "outcome_basis": "native generation; task validation and application are reported separately",
    }


def _attempts(runtime) -> list[dict]:
    records = []
    invocations = runtime.database.execute(
        """SELECT i.* FROM subscription_invocations i JOIN tasks t USING(task_id)
        WHERE t.portfolio_id = ? OR t.portfolio_id IS NULL""", (runtime.portfolio_id,),
    ).fetchall()
    for invocation in invocations:
        parent = dict(invocation)
        children = runtime.database.execute(
            "SELECT * FROM subscription_attempts WHERE invocation_id=? ORDER BY attempt_index",
            (parent["invocation_id"],),
        ).fetchall()
        if children:
            records.extend(_native(dict(child), source="subscription_attempts", parent=parent) for child in children)
        elif parent["state"] != "BLOCKED" and parent["cost_status"] != "not_incurred":
            records.append(_native(parent, source="legacy subscription invocation"))
    # Each API invocation is one gateway-owned attempt. Receipt aggregates are never added again.
    api_rows = runtime.database.execute(
        """SELECT i.*, r.synthetic AS accounted_synthetic, r.role AS accounted_role, r.attempt_kind
        FROM model_invocations i JOIN budget_reservations r USING(reservation_id)
        WHERE i.portfolio_id=?""", (runtime.portfolio_id,),
    ).fetchall()
    for row in api_rows:
        document = dict(row)
        request = evidence._json(row["request_json"])
        document.update(role=row["accounted_role"], provider=request.get("provider", "unknown"),
                        requested_model=request.get("model", "unknown"), synthetic=row["accounted_synthetic"])
        result = evidence._json(row["result_json"])
        document["actual_model"] = result.get("provider_model")
        records.append(_native(document, source="model_invocations"))
    return sorted(records, key=lambda item: (item["updated_at"], item["attempt_id"]), reverse=True)


def _totals(records: list[dict]) -> dict:
    actual = [record for record in records if not record["synthetic"]]
    totals = {
        "attempts": len(records), "actual_attempts": len(actual), "synthetic_attempts": len(records) - len(actual),
        "completed": sum(record["status"] == "COMPLETED" for record in actual),
        "failed": sum(record["status"] == "FAILED" for record in actual),
        "uncertain": sum(record["status"] in {"UNCERTAIN", "DISPATCHED"} for record in actual),
        "retries": sum(record["attempt_index"] > 1 or record["attempt_kind"] != "primary" for record in actual),
        "missing_usage_attempts": sum(not record["usage_reported"] for record in actual),
        "coverage_label": "Known subtotal; missing usage remains unknown",
        "known_subtotal": True, "basis": "actual native attempts; synthetic usage excluded",
    }
    coverage = {}
    for field in _MEASURES:
        reported = [record["usage"][field] for record in actual if record["usage"][field] is not None]
        totals[field] = sum(reported) if reported or not actual else None
        coverage[field] = {"reported": len(reported), "unreported": len(actual) - len(reported),
                           "attempts": len(actual)}
    totals["coverage"] = coverage
    totals["unreported_fields"] = {field: coverage[field]["unreported"] for field in _FIELDS}
    return totals


def _shared_quota(runtime, records: list[dict], now: str) -> dict:
    candidates = []
    for record in records:
        if not record["synthetic"] and record["source"] == "subscription_attempts" and record["quota"]:
            candidates.append({**record["quota"], "provider": record["provider"],
                               "record_source": "subscription_attempts", "attempt_id": record["attempt_id"]})
    for row in runtime.database.execute("SELECT * FROM subscription_provider_state").fetchall():
        quota = sanitize_quota(evidence._json(row["quota_json"]))
        if quota:
            candidates.append({**quota, "provider": row["provider"],
                               "record_source": "subscription_provider_state"})
    observed = [item for item in candidates if evidence._before(item.get("observed_at"), now)]
    latest = max(observed, key=lambda item: datetime.fromisoformat(item["observed_at"].replace("Z", "+00:00")),
                 default={})
    windows = latest.get("windows", {})
    if not windows:
        windows = {key: latest[key] for key in ("five_hour", "weekly") if key in latest}
    return {"scope": "shared account allowance", "observed_at": latest.get("observed_at"),
            "provider": latest.get("provider"), "windows": windows, "source": latest.get("source"),
            "record_source": latest.get("record_source"), "available": bool(windows),
            "metadata_error": latest.get("metadata_error", False),
            "unavailable_windows": latest.get("unavailable_windows", []),
            "ordinary_usage_allowed": latest.get("ordinary_usage_allowed"),
            "basis": "persisted allowance observation; shared with other account activity", "probe_performed": False}


def _schedule_role(name: str) -> str:
    if name.startswith("artifact-"):
        name = name[len("artifact-"):]
    return name.removesuffix("-review")


def _service(runtime, now: str) -> dict:
    row = runtime.database.execute(
        "SELECT * FROM graph_service_runs WHERE portfolio_id=? ORDER BY requested_at DESC,run_id DESC LIMIT 1",
        (runtime.portfolio_id,),
    ).fetchone()
    result = {"status": "UNOBSERVED", "heartbeat_at": None, "stale": None,
              "source": "persisted service journal", "heartbeat_stale_after_seconds": 90,
              "basis": "retained service status; process liveness is not probed"}
    if row:
        result.update(evidence._pick(dict(row), ("run_id", "mode", "status", "requested_at", "heartbeat_at",
                                                "finished_at", "error_type", "stop_requested")))
        heartbeat = row["heartbeat_at"]
        if row["status"] in {"RUNNING", "STARTING", "MANAGEMENT_ONLY", "STOPPING"}:
            try:
                age = (datetime.fromisoformat(now.replace("Z", "+00:00")) -
                       datetime.fromisoformat((heartbeat or "").replace("Z", "+00:00"))).total_seconds()
                result.update(heartbeat_age_seconds=age, stale=age < 0 or age > 90)
            except (ValueError, TypeError):
                result["stale"] = True
        else:
            result["stale"] = False
    return result


def overview(runtime) -> dict:
    """Return a coherent retained-record snapshot without changing the running system."""
    with runtime.database.snapshot():
        now = utc_iso(runtime.clock.now())
        records = _attempts(runtime)
        rows = runtime.database.execute(
            """SELECT t.*, r.created_at AS result_created_at FROM tasks t LEFT JOIN role_results r USING(task_id)
            WHERE t.portfolio_id=? OR t.portfolio_id IS NULL ORDER BY t.created_at DESC,t.task_id DESC""",
            (runtime.portfolio_id,),
        ).fetchall()
        tasks = [{**evidence._task(row, now), "result_created_at": row["result_created_at"]} for row in rows]
        schedules = runtime.database.execute("SELECT * FROM schedules WHERE portfolio_id=? ORDER BY next_due_at",
                                             (runtime.portfolio_id,)).fetchall()
        departments, usage_departments = [], []
        for role, name in _ROLES.items():
            native = [record for record in records if record["role"] == role]
            role_tasks = [task for task in tasks if task["role"] == role]
            applied = [task for task in role_tasks if task["status"] in evidence._TERMINAL or task["result_created_at"]]
            applied.sort(key=lambda task: (task["result_created_at"] or task["created_at"], task["task_id"]),
                         reverse=True)
            role_schedules = [dict(row) for row in schedules if _schedule_role(row["name"]) == role]
            next_due = (evidence._pick(role_schedules[0], ("schedule_id", "name", "last_due_at", "next_due_at",
                                                         "interval_seconds", "missed_run_policy"))
                        if role_schedules else None)
            if next_due:
                next_due.update(persisted=True, overdue=evidence._before(next_due["next_due_at"], now),
                                basis="persisted schedule; worker activation is not probed")
            usage = {"role": role, "name": name, **_totals(native)}
            usage_departments.append(usage)
            latest = next((record for record in native if not record["synthetic"]), native[0] if native else None)
            departments.append({
                "role": role, "name": name, "last_native":
                    evidence._pick(latest, ("invocation_id", "attempt_id", "task_id", "provider", "model", "status",
                                           "created_at", "updated_at", "completed_at", "synthetic", "failure",
                                           "persisted_state",
                                           "outcome_basis")) if latest else None,
                "last_applied": applied[0] if applied else None, "next_due": next_due,
                "active_tasks": sum(task["status"] not in evidence._TERMINAL for task in role_tasks),
                "failed_tasks": sum(task["status"] in {"FAILED", "DEAD_LETTER"} for task in role_tasks),
                "retries": [task for task in role_tasks if task["status"] not in evidence._TERMINAL
                            and task["attempts_used"] > 0][:5], "usage": usage,
            })
        recent_decisions = evidence.decisions(runtime, 5)
        recent_findings = evidence.research(runtime, 5)
        counts = {"tasks": len(tasks), "active_tasks": sum(task["status"] not in evidence._TERMINAL for task in tasks),
                  "decisions": recent_decisions["total"], "findings": recent_findings["total"]}
        for table, key in (("fills", "fills"), ("order_intents", "orders")):
            counts[key] = runtime.database.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE portfolio_id=?",
                                                  (runtime.portfolio_id,)).fetchone()["n"]
        counts["trades"] = counts["fills"]
        return redact({"portfolio_id": runtime.portfolio_id, "as_of": now, "departments": departments,
                       "usage": {"period": "all retained lifetime records", "departments": usage_departments,
                                 "totals": _totals(records), "shared_quota": _shared_quota(runtime, records, now)},
                       "counts": counts, "count_definitions": {"trades": "persisted fills, including partial fills",
                                                                  "orders": "persisted order intents"},
                       "service": _service(runtime, now), "decisions": recent_decisions["decisions"],
                       "findings": recent_findings["findings"]})
