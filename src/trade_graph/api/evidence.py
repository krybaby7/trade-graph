"""Scoped, read-only journal projections for organisation and evidence navigation."""

from __future__ import annotations

import difflib
import json
from datetime import datetime

from trade_graph.api.security import redact
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import NotFound, ValidationFailure

_ENVELOPE = (
    "schema_version",
    "record_id",
    "created_at_utc",
    "run_id",
    "task_id",
    "root_task_id",
    "portfolio_id",
    "mode",
    "system_version_id",
    "evidence_refs",
    "trace_id",
)
_FINDING = (
    *_ENVELOPE,
    "question",
    "claim",
    "source_url",
    "publisher",
    "published_at_utc",
    "event_at_utc",
    "retrieved_at_utc",
    "available_at_utc",
    "source_hash",
    "instruments",
    "relevance",
    "counterevidence",
    "expires_at_utc",
    "invalidation",
)
_LESSON = (
    *_ENVELOPE,
    "lesson_id",
    "revision",
    "observation",
    "supporting_cases",
    "counterexamples",
    "explanation",
    "proposed_improvement",
    "validation_method",
    "subsequent_result",
    "scope",
    "sample_note",
    "confidence_category",
    "linked_decisions",
    "status",
    "supersedes",
    "process_assessment",
    "outcome_sign",
)
_DECISION = (
    *_ENVELOPE,
    "action",
    "symbol",
    "position_ref",
    "order_ref",
    "quantity",
    "limit_price",
    "stop_price",
    "time_in_force",
    "rationale",
    "invalidation",
    "horizon_seconds",
    "strategy_id",
    "experiment_id",
    "snapshot_id",
    "mandate_revision",
    "policy_revision",
    "no_action_reason",
    "uncertainty_note",
    "actions",
    "intended_outcome",
    "review_criteria",
    "pause_profile",
    "activate_candidate_id",
    "reject_candidate_id",
    "task_objectives",
    "mandate_id",
)
_TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "DEAD_LETTER"}


def _json(value: str | None) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _pick(value: dict, keys: tuple[str, ...]) -> dict:
    return {key: value[key] for key in keys if key in value}


def _before(value: str | None, now: str) -> bool:
    if not value:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) <= datetime.fromisoformat(
            now.replace("Z", "+00:00")
        )
    except (ValueError, TypeError):
        return False


def _pagination(total: int, limit: int, offset: int) -> dict:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ValidationFailure("limit must be an integer from 1 to 200")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValidationFailure("offset must be a nonnegative integer")
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_offset": offset + limit if offset + limit < total else None,
    }


def _task(row, now: str) -> dict:
    task = _pick(
        dict(row),
        (
            "task_id",
            "root_task_id",
            "parent_id",
            "portfolio_id",
            "role",
            "status",
            "objective",
            "priority",
            "due_at",
            "deadline_at",
            "created_at",
            "expected_version",
            "allocated_spend",
            "max_steps",
            "max_attempts",
            "attempts_used",
            "lease_owner",
            "lease_expires_at",
        ),
    )
    task["lease_expired"] = row["status"] in {"LEASED", "RUNNING"} and (
        row["lease_expires_at"] is None or _before(row["lease_expires_at"], now)
    )
    task["blocked"] = row["status"] in {"BLOCKED_BUDGET", "WAITING_EXTERNAL"}
    task["overdue"] = row["status"] not in _TERMINAL and _before(row["deadline_at"], now)
    task["trigger"] = _pick(
        _json(row["input_json"]),
        (
            "event_id",
            "event_ids",
            "digest_id",
            "schedule_id",
            "observation_id",
            "source",
            "evidence_refs",
        ),
    )
    # A schedule occurrence is real persisted provenance, even when input_json is empty.
    if row["dedup_key"] and row["dedup_key"].startswith("schedule:"):
        parts = row["dedup_key"].split(":", 2)
        if len(parts) == 3:
            task["trigger"].update(schedule_id=parts[1], due_at=parts[2])
    output = _json(row["output_json"])
    task["completion"] = _pick(output, ("decision_id", "intent_id", "report_id", "candidate_id", "change_id", "reason"))
    return task


def _finding(row, now: str) -> dict:
    result = _pick(_json(row["document_json"]), _FINDING)
    available = _before(row["available_at"], now)
    stale = _before(row["expires_at"], now)
    result.update(
        finding_id=row["finding_id"],
        portfolio_id=row["portfolio_id"],
        available_at=row["available_at"],
        expires_at=row["expires_at"],
        created_at=row["created_at"],
        source_hash=row["source_hash"],
        available=available,
        stale=stale,
        fresh=available and not stale and result.get("relevance") != "untrusted-page",
    )
    return result


def _lesson(runtime, row) -> dict:
    latest = runtime.database.execute(
        "SELECT MAX(revision) AS revision FROM lessons WHERE portfolio_id = ? AND lesson_id = ?",
        (runtime.portfolio_id, row["lesson_id"]),
    ).fetchone()["revision"]
    result = _pick(_json(row["document_json"]), _LESSON)
    result.update(
        revision_id=row["revision_id"],
        lesson_id=row["lesson_id"],
        portfolio_id=row["portfolio_id"],
        revision=row["revision"],
        status=row["status"],
        created_at=row["created_at"],
        latest=row["revision"] == latest,
    )
    return result


def _decision_card(row) -> dict:
    doc = _pick(_json(row["payload_json"]), _DECISION)
    doc.update(
        decision_id=row["decision_id"],
        record_id=row["decision_id"],
        kind=row["kind"],
        portfolio_id=row["portfolio_id"],
        created_at=row["created_at"],
        snapshot_id=row["snapshot_id"],
        task_id=row["task_id"],
    )
    if row["kind"] == "leader":
        doc["state"] = row["state"]
    else:
        doc.update(
            action=row["action"],
            system_version_id=row["system_version_id"],
            mandate_revision=row["mandate_revision"],
            policy_revision=row["policy_revision"],
        )
    return doc


_DECISIONS_SQL = """SELECT decision_id, portfolio_id, action, payload_json, mandate_revision,
    policy_revision, snapshot_id, system_version_id, created_at, task_id, 'trader' AS kind, NULL AS state
    FROM decisions WHERE portfolio_id = ?
    UNION ALL SELECT decision_id, portfolio_id, NULL, document_json, NULL, NULL, snapshot_id,
    NULL, created_at, task_id, 'leader', state FROM leader_decisions WHERE portfolio_id = ?"""


def decisions(runtime, limit: int = 50, offset: int = 0) -> dict:
    """Discover persisted Trader and Leader decisions, including no-action decisions."""
    with runtime.database.snapshot():
        now = utc_iso(runtime.clock.now())
        total = runtime.database.execute(
            f"SELECT COUNT(*) AS n FROM ({_DECISIONS_SQL})",
            (runtime.portfolio_id, runtime.portfolio_id),
        ).fetchone()["n"]
        page = _pagination(total, limit, offset)
        rows = runtime.database.execute(
            f"SELECT * FROM ({_DECISIONS_SQL}) ORDER BY created_at DESC, decision_id DESC, kind LIMIT ? OFFSET ?",
            (runtime.portfolio_id, runtime.portfolio_id, limit, offset),
        ).fetchall()
        return redact(
            {
                "portfolio_id": runtime.portfolio_id,
                "as_of": now,
                "decisions": [_decision_card(row) for row in rows],
                **page,
            }
        )


def organization(runtime, limit: int = 50, offset: int = 0) -> dict:
    with runtime.database.snapshot():
        now = utc_iso(runtime.clock.now())
        scope = "portfolio_id = ? OR portfolio_id IS NULL"
        total = runtime.database.execute(
            f"SELECT COUNT(*) AS n FROM tasks WHERE {scope}", (runtime.portfolio_id,)
        ).fetchone()["n"]
        page = _pagination(total, limit, offset)
        rows = runtime.database.execute(
            f"SELECT * FROM tasks WHERE {scope} ORDER BY created_at DESC, task_id DESC LIMIT ? OFFSET ?",
            (runtime.portfolio_id, limit, offset),
        ).fetchall()
        tasks = [_task(row, now) for row in rows]
        counts = runtime.database.execute(
            f"""SELECT SUM(status NOT IN ('SUCCEEDED','FAILED','CANCELLED','DEAD_LETTER')) AS active,
            SUM(status IN ('BLOCKED_BUDGET','WAITING_EXTERNAL')) AS blocked,
            SUM(status IN ('LEASED','RUNNING')) AS leased,
            SUM(julianday(deadline_at) <= julianday(?)
                AND status NOT IN ('SUCCEEDED','FAILED','CANCELLED','DEAD_LETTER')) AS overdue
            FROM tasks WHERE {scope}""",
            (now, runtime.portfolio_id),
        ).fetchone()
        active = runtime.database.execute(
            "SELECT * FROM active_versions WHERE portfolio_id = ?", (runtime.portfolio_id,)
        ).fetchone()
        version = None
        if active:
            version = _pick(dict(active), ("version_id", "artifact_hash", "generation", "activated_at"))
            version["fingerprint"] = _json(active["fingerprint_json"])
            rollout = runtime.database.execute(
                "SELECT state FROM version_rollouts WHERE portfolio_id = ? AND generation = ?",
                (runtime.portfolio_id, active["generation"]),
            ).fetchone()
            version["rollout_state"] = rollout["state"] if rollout else "BASELINE"
        mandate = runtime.database.execute(
            "SELECT * FROM mandates WHERE portfolio_id = ? AND active = 1 ORDER BY revision DESC LIMIT 1",
            (runtime.portfolio_id,),
        ).fetchone()
        mandate_doc = None
        if mandate:
            mandate_doc = _json(mandate["document_json"])
            mandate_doc.update(
                mandate_id=mandate["mandate_id"],
                revision=mandate["revision"],
                expires_at=mandate["expires_at"],
                expired=_before(mandate["expires_at"], now),
            )
        pause = runtime.database.execute(
            "SELECT * FROM pause_states WHERE portfolio_id = ?", (runtime.portfolio_id,)
        ).fetchone()
        pause_doc = None
        if pause:
            pause_doc = _pick(dict(pause), ("profile", "originator", "reason", "scope", "requested_at", "achieved"))
            pause_doc["details"] = _json(pause["details_json"])
        leader_state = runtime.database.execute(
            "SELECT revision FROM dashboard_control_state WHERE scope = ?",
            (f"leader:{runtime.portfolio_id}",),
        ).fetchone()
        decision_page = decisions(runtime, limit, offset)
        return redact(
            {
                "portfolio_id": runtime.portfolio_id,
                "leader_revision": leader_state["revision"] if leader_state else 0,
                "as_of": now,
                "artifact_hash": version["artifact_hash"] if version else None,
                "version": version,
                "mandate": mandate_doc,
                "pause": pause_doc,
                "tasks": tasks,
                "leases": [t for t in tasks if t["status"] in {"LEASED", "RUNNING"}],
                "blocked": [t for t in tasks if t["blocked"]],
                "overdue": [t for t in tasks if t["overdue"]],
                "counts": {key: counts[key] or 0 for key in counts.keys()},
                "decisions": decision_page["decisions"],
                "decisions_pagination": _pick(decision_page, ("total", "limit", "offset", "next_offset")),
                "trigger_events": events(runtime, limit)["events"],
                "source": "task journal",
                **page,
            }
        )


def research(runtime, limit: int = 50, offset: int = 0) -> dict:
    with runtime.database.snapshot():
        now = utc_iso(runtime.clock.now())
        total = runtime.database.execute(
            "SELECT COUNT(*) AS n FROM findings WHERE portfolio_id = ? OR portfolio_id IS NULL",
            (runtime.portfolio_id,),
        ).fetchone()["n"]
        page = _pagination(total, limit, offset)
        rows = runtime.database.execute(
            """SELECT * FROM findings WHERE portfolio_id = ? OR portfolio_id IS NULL
            ORDER BY created_at DESC, finding_id DESC LIMIT ? OFFSET ?""",
            (runtime.portfolio_id, limit, offset),
        ).fetchall()
        return redact(
            {
                "portfolio_id": runtime.portfolio_id,
                "as_of": now,
                "findings": [_finding(row, now) for row in rows],
                **page,
            }
        )


def lessons(runtime, limit: int = 50, offset: int = 0) -> dict:
    with runtime.database.snapshot():
        total = runtime.database.execute(
            "SELECT COUNT(*) AS n FROM lessons WHERE portfolio_id = ?", (runtime.portfolio_id,)
        ).fetchone()["n"]
        page = _pagination(total, limit, offset)
        rows = runtime.database.execute(
            "SELECT * FROM lessons WHERE portfolio_id = ? ORDER BY created_at DESC, revision_id DESC LIMIT ? OFFSET ?",
            (runtime.portfolio_id, limit, offset),
        ).fetchall()
        return redact(
            {
                "portfolio_id": runtime.portfolio_id,
                "as_of": utc_iso(runtime.clock.now()),
                "lessons": [_lesson(runtime, row) for row in rows],
                **page,
            }
        )


def decision(runtime, decision_id: str) -> dict:
    """Join evidence by persisted identity and account scope; omit provider conversations."""
    with runtime.database.snapshot():
        now = utc_iso(runtime.clock.now())
        row = runtime.database.execute(
            f"SELECT * FROM ({_DECISIONS_SQL}) WHERE decision_id = ? ORDER BY kind LIMIT 1",
            (runtime.portfolio_id, runtime.portfolio_id, decision_id),
        ).fetchone()
        if row is None:
            raise NotFound("decision not found")
        doc = _decision_card(row)
        snapshot = runtime.database.execute(
            "SELECT * FROM snapshots WHERE snapshot_id = ? AND portfolio_id = ?",
            (row["snapshot_id"], runtime.portfolio_id),
        ).fetchone()
        snapshot_doc, snapshot_payload, missing = None, {}, []
        if snapshot:
            snapshot_payload = _json(snapshot["payload_json"])
            snapshot_doc = _pick(dict(snapshot), ("snapshot_id", "as_of", "created_at"))
            snapshot_doc["artifact"] = _pick(
                snapshot_payload.get("artifact", {}), ("version_id", "artifact_hash", "generation", "manifest_sha256")
            )
        elif row["snapshot_id"]:
            missing.append(row["snapshot_id"])
        selected = snapshot_payload.get("selected_context", {})
        refs = set(ref for ref in doc.get("evidence_refs", []) if isinstance(ref, str))
        refs.update(ref for ref in snapshot_payload.get("evidence_refs", []) if isinstance(ref, str))
        lesson_refs = {
            item["record_id"]
            for item in selected.get("lessons", [])
            if isinstance(item, dict) and isinstance(item.get("record_id"), str)
        }
        findings = []
        for ref in sorted(refs):
            finding = runtime.database.execute(
                "SELECT * FROM findings WHERE finding_id = ? AND (portfolio_id = ? OR portfolio_id IS NULL)",
                (ref, runtime.portfolio_id),
            ).fetchone()
            if finding:
                findings.append(_finding(finding, now))
        lesson_rows = runtime.database.execute(
            """SELECT * FROM lessons WHERE portfolio_id = ? AND
            (revision_id IN (SELECT value FROM json_each(?)) OR
             EXISTS (SELECT 1 FROM json_each(document_json, '$.linked_decisions') WHERE value = ?))
            ORDER BY lesson_id, revision""",
            (runtime.portfolio_id, json.dumps(sorted(lesson_refs | refs)), decision_id),
        ).fetchall()
        lesson_docs = [_lesson(runtime, item) for item in lesson_rows]
        if snapshot_doc is not None:
            snapshot_doc["selected_context"] = {
                "always_include": selected.get("always_include", []),
                "lesson_revision_ids": [
                    item["revision_id"] for item in lesson_docs if item["revision_id"] in lesson_refs
                ],
            }
        orders = []
        order_rows = runtime.database.execute(
            """SELECT * FROM order_intents WHERE portfolio_id = ? AND json_extract(payload_json, '$.decision_id') = ?
            ORDER BY created_at, intent_id""",
            (runtime.portfolio_id, decision_id),
        ).fetchall()
        for order in order_rows:
            card = _pick(
                _json(order["payload_json"]),
                (
                    "decision_id",
                    "snapshot_id",
                    "side",
                    "order_type",
                    "quantity",
                    "limit_price",
                    "stop_price",
                    "submitted_quantity",
                    "submitted_price",
                    "requested_quantity",
                    "rounding",
                    "reserve_asset",
                    "reserve_amount",
                    "time_in_force",
                    "reduce_only",
                    "execution_deviation",
                    "venue",
                    "mode",
                ),
            )
            card.update(
                _pick(dict(order), ("intent_id", "client_order_id", "state", "symbol", "created_at", "updated_at"))
            )
            orders.append(card)
        fill_rows = runtime.database.execute(
            """SELECT f.* FROM fills f JOIN order_intents o ON o.intent_id = f.intent_id
            WHERE f.portfolio_id = ? AND o.portfolio_id = ? AND json_extract(o.payload_json, '$.decision_id') = ?
            ORDER BY f.created_at, f.fill_id""",
            (runtime.portfolio_id, runtime.portfolio_id, decision_id),
        ).fetchall()
        fills = []
        for fill in fill_rows:
            card = _pick(
                _json(fill["document_json"]),
                (
                    "venue",
                    "symbol",
                    "side",
                    "quantity",
                    "price",
                    "fee_amount",
                    "fee_asset",
                    "liquidity",
                    "filled_at_utc",
                    "heuristic",
                    "reference_mid",
                    "fee_identified_rate",
                    "quote_cost",
                    "fee_components",
                ),
            )
            card.update(_pick(dict(fill), ("fill_id", "intent_id", "created_at")))
            fills.append(card)
        task = runtime.database.execute(
            "SELECT * FROM tasks WHERE task_id = ? AND portfolio_id = ?", (row["task_id"], runtime.portfolio_id)
        ).fetchone()
        mandate = runtime.database.execute(
            "SELECT document_json FROM mandates WHERE portfolio_id = ? AND CAST(revision AS TEXT) = ?",
            (runtime.portfolio_id, doc.get("mandate_revision")),
        ).fetchone()
        version_refs = {doc.get("system_version_id")}
        if snapshot_doc and snapshot_doc["artifact"]:
            version_refs.update(
                (snapshot_doc["artifact"].get("version_id"), snapshot_doc["artifact"].get("artifact_hash"))
            )
        version_rows = runtime.database.execute(
            """SELECT version_id, artifact_hash FROM version_history WHERE portfolio_id = ? AND
            (version_id IN (SELECT value FROM json_each(?)) OR artifact_hash IN (SELECT value FROM json_each(?)))
            ORDER BY version_id""",
            (runtime.portfolio_id, json.dumps(list(version_refs)), json.dumps(list(version_refs))),
        ).fetchall()
        return redact(
            {
                "portfolio_id": runtime.portfolio_id,
                "decision": doc,
                "snapshot": snapshot_doc,
                "task": _task(task, now) if task else None,
                "orders": orders,
                "fills": fills,
                "research": findings,
                "lessons": lesson_docs,
                "versions": [dict(v) for v in version_rows],
                "mandate": _json(mandate["document_json"]) if mandate else None,
                "evidence_missing": missing,
            }
        )


def _candidate(row) -> dict:
    task, result = _json(row["task_json"]), _json(row["document_json"])
    card = _pick(dict(row), ("candidate_id", "change_id", "state", "content_hash", "baseline_hash", "created_at"))
    card.update(_pick(task, ("objective", "success_criteria", "rollback_criteria", "allowed_classes")))
    card.update(_pick(result, ("known_limits", "changed_files", "attestation_id")))
    return card


def _version_event(row) -> dict:
    card = _pick(dict(row), ("event_id", "kind", "from_hash", "to_hash", "created_at"))
    card["details"] = _json(row["details_json"])
    return card


def changes(runtime, limit: int = 50, offset: int = 0) -> dict:
    with runtime.database.snapshot():
        total = runtime.database.execute(
            "SELECT COUNT(*) AS n FROM candidates c JOIN change_tasks t USING(change_id) WHERE t.portfolio_id = ?",
            (runtime.portfolio_id,),
        ).fetchone()["n"]
        page = _pagination(total, limit, offset)
        rows = runtime.database.execute(
            """SELECT c.*, t.document_json AS task_json FROM candidates c JOIN change_tasks t USING(change_id)
            WHERE t.portfolio_id = ? ORDER BY c.created_at DESC, c.candidate_id DESC LIMIT ? OFFSET ?""",
            (runtime.portfolio_id, limit, offset),
        ).fetchall()
        event_rows = runtime.database.execute(
            """SELECT * FROM version_events WHERE portfolio_id = ?
            ORDER BY created_at DESC, event_id DESC LIMIT ? OFFSET ?""",
            (runtime.portfolio_id, limit, offset),
        ).fetchall()
        event_total = runtime.database.execute(
            "SELECT COUNT(*) AS n FROM version_events WHERE portfolio_id = ?", (runtime.portfolio_id,)
        ).fetchone()["n"]
        return redact(
            {
                "portfolio_id": runtime.portfolio_id,
                "as_of": utc_iso(runtime.clock.now()),
                "candidates": [_candidate(row) for row in rows],
                "events": [_version_event(row) for row in event_rows],
                "events_pagination": _pagination(event_total, limit, offset),
                **page,
            }
        )


def change(runtime, candidate_id: str) -> dict:
    with runtime.database.snapshot():
        row = runtime.database.execute(
            """SELECT c.*, t.document_json AS task_json FROM candidates c JOIN change_tasks t USING(change_id)
            WHERE c.candidate_id = ? AND t.portfolio_id = ?""",
            (candidate_id, runtime.portfolio_id),
        ).fetchone()
        if row is None:
            raise NotFound("change candidate not found")
        candidate = _candidate(row)
        task = runtime.database.execute(
            "SELECT * FROM change_tasks WHERE change_id = ? AND portfolio_id = ?",
            (row["change_id"], runtime.portfolio_id),
        ).fetchone()
        task_doc = _pick(
            _json(task["document_json"]),
            (
                *_ENVELOPE,
                "objective",
                "baseline_version",
                "baseline_hash",
                "allowed_classes",
                "allowed_paths",
                "invariants",
                "max_spend",
                "max_steps",
                "test_plan",
                "success_criteria",
                "rollback_criteria",
                "expires_at_utc",
            ),
        )
        task_doc.update(change_id=task["change_id"], state=task["state"], attempts_used=task["attempts_used"])
        proof = runtime.database.execute(
            """SELECT * FROM candidate_attestations WHERE attestation_id = ? AND candidate_id = ?
            AND portfolio_id = ? AND change_id = ?""",
            (candidate.get("attestation_id"), candidate_id, runtime.portfolio_id, row["change_id"]),
        ).fetchone()
        attestation, artifacts = None, []
        if proof:
            report = _json(proof["report_json"])
            attestation = _pick(
                dict(proof),
                (
                    "attestation_id",
                    "candidate_id",
                    "change_id",
                    "decision_id",
                    "task_hash",
                    "content_hash",
                    "baseline_hash",
                    "checks_module_hash",
                    "exit_code",
                    "created_at",
                ),
            )
            attestation["report"] = _pick(
                report,
                (
                    "command",
                    "exit_code",
                    "stdout",
                    "stderr",
                    "manifest",
                    "isolation",
                    "failures",
                ),
            )
            # Read only persisted attested bytes. No stage path or filesystem reads occur here.
            baseline = runtime.database.execute(
                "SELECT files_json FROM artifact_bundles WHERE artifact_hash = ?", (row["baseline_hash"],)
            ).fetchone()
            old_files = _json(baseline["files_json"]) if baseline else {}
            files = report.get("artifact_files", {})
            if isinstance(files, dict):
                for path in candidate.get("changed_files", []):
                    after, before = files.get(path), old_files.get(path)
                    if isinstance(after, str):
                        diff = "".join(
                            difflib.unified_diff(
                                (before or "").splitlines(keepends=True),
                                after.splitlines(keepends=True),
                                fromfile=f"before/{path}",
                                tofile=f"after/{path}",
                            )
                        )
                        artifacts.append({"path": path, "before": before, "after": after, "diff": diff})
        attempts = runtime.database.execute(
            "SELECT * FROM engineering_attempts WHERE change_id = ? ORDER BY number, attempt_id",
            (row["change_id"],),
        ).fetchall()
        rollouts = runtime.database.execute(
            """SELECT * FROM version_rollouts WHERE portfolio_id = ? AND candidate_id = ?
            ORDER BY generation, rollout_id""",
            (runtime.portfolio_id, candidate_id),
        ).fetchall()
        rollout_docs = []
        for rollout in rollouts:
            doc = _pick(
                dict(rollout),
                (
                    "rollout_id",
                    "state",
                    "generation",
                    "target_hash",
                    "previous_hash",
                    "previous_version_id",
                    "activated_at",
                ),
            )
            doc["policy"] = _json(rollout["policy_json"])
            rollout_docs.append(doc)
        observations = runtime.database.execute(
            """SELECT o.* FROM version_observations o JOIN version_rollouts r USING(rollout_id)
            WHERE r.portfolio_id = ? AND r.candidate_id = ? ORDER BY o.created_at, o.observation_id""",
            (runtime.portfolio_id, candidate_id),
        ).fetchall()
        event_rows = runtime.database.execute(
            """SELECT * FROM version_events WHERE portfolio_id = ? AND
            (json_extract(details_json, '$.candidate_id') = ? OR json_extract(details_json, '$.version_id') = ?
             OR from_hash = ? OR to_hash = ?) ORDER BY created_at, event_id""",
            (runtime.portfolio_id, candidate_id, candidate_id, row["content_hash"], row["content_hash"]),
        ).fetchall()
        return redact(
            {
                "portfolio_id": runtime.portfolio_id,
                "candidate": candidate,
                "change_task": task_doc,
                "attestation": attestation,
                "artifacts": artifacts,
                "attempts": [
                    {
                        **_pick(dict(a), ("attempt_id", "number", "state", "created_at")),
                        "details": _json(a["details_json"]),
                    }
                    for a in attempts
                ],
                "rollouts": rollout_docs,
                "observations": [
                    {**_json(o["document_json"]), **_pick(dict(o), ("rollout_id", "observation_id", "created_at"))}
                    for o in observations
                ],
                "events": [_version_event(event) for event in event_rows],
            }
        )


_EVENTS_SQL = """SELECT event_id, kind, payload_json, created_at, 'activity' AS source, NULL AS from_hash,
    NULL AS to_hash FROM activity_events WHERE portfolio_id = ? OR portfolio_id IS NULL
    UNION ALL SELECT event_id, kind, details_json, created_at, 'version', from_hash, to_hash
    FROM version_events WHERE portfolio_id = ?"""


def events(runtime, limit: int = 50, offset: int = 0) -> dict:
    with runtime.database.snapshot():
        total = runtime.database.execute(
            f"SELECT COUNT(*) AS n FROM ({_EVENTS_SQL})", (runtime.portfolio_id, runtime.portfolio_id)
        ).fetchone()["n"]
        page = _pagination(total, limit, offset)
        rows = runtime.database.execute(
            f"SELECT * FROM ({_EVENTS_SQL}) ORDER BY created_at DESC, event_id DESC, source LIMIT ? OFFSET ?",
            (runtime.portfolio_id, runtime.portfolio_id, limit, offset),
        ).fetchall()
        return redact(
            {
                "portfolio_id": runtime.portfolio_id,
                "as_of": utc_iso(runtime.clock.now()),
                "events": [
                    {
                        **_pick(dict(row), ("event_id", "kind", "created_at", "source", "from_hash", "to_hash")),
                        "details": _json(row["payload_json"]),
                    }
                    for row in rows
                ],
                **page,
            }
        )
