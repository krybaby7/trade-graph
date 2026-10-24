"""Resolve a commission from protected storage, never from caller/model approval flags."""

from __future__ import annotations

import hashlib
import json

from trade_graph.application.authority import AuthorityRecord
from trade_graph.contracts.models import ChangeTask
from trade_graph.domain.errors import AuthorityDenied, StaleState, ValidationFailure


def task_hash(task: ChangeTask) -> str:
    return hashlib.sha256(task.model_dump_json().encode()).hexdigest()


def authorized_change(database, clock, portfolio_id: str, change_id: str):
    row = database.execute(
        "SELECT * FROM change_tasks WHERE change_id = ? AND portfolio_id = ?", (change_id, portfolio_id)
    ).fetchone()
    commission = database.execute(
        "SELECT * FROM engineering_commissions WHERE change_id = ? AND portfolio_id = ?", (change_id, portfolio_id)
    ).fetchone()
    if row is None or commission is None or commission["state"] != "AUTHORIZED":
        raise AuthorityDenied("persisted Leader commission required")
    task = ChangeTask.model_validate_json(row["document_json"])
    decision = database.execute(
        "SELECT * FROM leader_decisions WHERE decision_id = ? AND portfolio_id = ? AND state = 'APPLIED'",
        (commission["decision_id"], portfolio_id),
    ).fetchone()
    if decision is None or not any(
        a.get("kind") == "commission" and a.get("change_id") == change_id
        for a in json.loads(decision["document_json"]).get("actions", [])
    ):
        raise AuthorityDenied("commission is not bound to an applied Leader decision")
    if (
        task.portfolio_id != portfolio_id
        or task.record_id != change_id
        or task.task_id != commission["worker_task_id"]
        or task.mode != "paper"
        or task_hash(task) != commission["task_hash"]
        or row["baseline_hash"] != task.baseline_hash
        or commission["baseline_hash"] != task.baseline_hash
    ):
        raise AuthorityDenied("commission/task identity mismatch")
    if task.expires_at_utc <= clock.now():
        raise AuthorityDenied("change task expired")
    authority = AuthorityRecord(database, clock)
    policy = authority.active_policy()
    mandate = authority.active_mandate(portfolio_id)
    if policy.revision_id != commission["policy_revision"] or mandate.revision != commission["mandate_revision"]:
        raise StaleState("commission authority changed; reauthorize")
    if mandate.expires_at_utc <= clock.now():
        raise AuthorityDenied("mandate expired")
    if not set(task.allowed_classes).issubset(policy.allowed_change_classes):
        raise AuthorityDenied("change class outside owner policy")
    pause = database.execute("SELECT * FROM pause_states WHERE portfolio_id = ?", (portfolio_id,)).fetchone()
    if pause and pause["profile"] != "RUNNING" and pause["originator"] != "leader":
        raise AuthorityDenied("owner/system halt blocks engineering and activation")
    active = database.execute("SELECT * FROM active_versions WHERE portfolio_id = ?", (portfolio_id,)).fetchone()
    if active is None or active["artifact_hash"] != task.baseline_hash:
        raise StaleState("baseline moved; revalidate")
    worker = database.execute("SELECT * FROM tasks WHERE task_id = ?", (task.task_id,)).fetchone()
    if (
        worker is None
        or worker["portfolio_id"] != portfolio_id
        or worker["role"] != "engineer"
        or worker["root_task_id"] != task.root_task_id
        or worker["status"] in {"CANCELLED", "DEAD_LETTER", "BLOCKED_BUDGET"}
    ):
        raise ValidationFailure("commission worker is no longer eligible")
    return task, row, commission
