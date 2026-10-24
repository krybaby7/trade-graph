"""Recover interrupted operator receipts without repeating their effects.

The operating service calls recovery under its exclusive startup lease, before
accepting HTTP writes or starting workers. An elapsed timestamp alone never
authorizes recovery of a command that another process may still be executing.
"""

from __future__ import annotations

import json

from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import StaleState


def command_scopes(runtime) -> tuple[str, str]:
    deployment = getattr(runtime, "deployment_id", "deployment")
    return f"owner:{deployment}", f"leader:{runtime.portfolio_id}"


def _evidence_document(raw: str | None) -> dict:
    if raw is None:
        return {}
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            return value
    except (ValueError, TypeError, RecursionError):
        pass
    # Preserve unreadable private evidence while reporting only uncertainty.
    return {"invalid_evidence": True, "prior_evidence_json": raw}


def local_command_effects(runtime) -> dict:
    """Capture committed management state, excluding private owner prose."""
    execution = getattr(runtime, "execution", None)
    pause = execution.pause(runtime.portfolio_id) if execution else None
    return {
        "pause": None if pause is None else {
            name: pause[name] for name in ("profile", "originator", "achieved", "requested_at")
        }
    }


def record_command_evidence(runtime, command_id: str, action: str, revision: int, *,
                            result: dict | None = None, error: int | None = None) -> None:
    """Join the caller's transaction; never independently commit an owner write."""
    if not runtime.database.connection.in_transaction:
        raise StaleState("command evidence requires the caller's effect transaction")
    row = runtime.database.execute(
        "SELECT effect_json FROM dashboard_command_evidence WHERE command_id = ?", (command_id,)
    ).fetchone()
    effect = _evidence_document(row["effect_json"]) if row else {}
    snapshot = local_command_effects(runtime)
    effect.setdefault("initial_local_effects", effect.get("local_effects", snapshot))
    effect["local_effects"] = snapshot
    if result is not None:
        effect.update(response=result, error=error)
    runtime.database.execute(
        """INSERT INTO dashboard_command_evidence
        (command_id, action, portfolio_id, deployment_id, revision, phase, effect_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(command_id) DO UPDATE SET phase=excluded.phase,
          effect_json=excluded.effect_json, updated_at=excluded.updated_at""",
        (command_id, action, runtime.portfolio_id, getattr(runtime, "deployment_id", "deployment"), revision,
         "LOCAL_COMMITTED" if result is None else "EFFECT_COMMITTED", json.dumps(effect), utc_iso(runtime.clock.now())),
    )


def command_history(runtime, scope: str, *, limit: int = 100, offset: int = 0) -> list[dict]:
    """Return safe, scoped receipt metadata; request bodies and hashes stay private."""
    rows = runtime.database.execute(
        """SELECT c.command_id, c.status, c.created_at, e.action, e.revision, e.phase,
                  e.effect_json, e.updated_at
        FROM dashboard_commands c LEFT JOIN dashboard_command_evidence e USING(command_id)
        WHERE c.scope = ? ORDER BY c.created_at DESC, c.rowid DESC LIMIT ? OFFSET ?""",
        (scope, limit, offset),
    ).fetchall()
    result = []
    for row in rows:
        item = {name: row[name] for name in ("command_id", "status", "created_at", "action", "revision", "phase")}
        if row["phase"] == "RECOVERED":
            effect = _evidence_document(row["effect_json"])
            item.update(recovery=effect["recovery"], recovered_at=row["updated_at"])
        result.append(item)
    return result


def recover_owner_commands(runtime) -> list[dict]:
    """Finish abandoned receipts during fenced startup, without external calls.

    A durable effect receipt restores its exact result. Otherwise the original
    request terminates as FAILED/needs-review with its committed local evidence;
    the controller reconciles execution separately. No budget, policy, task,
    pause, resume, cancellation or flatten request is replayed here.
    """
    database = runtime.database
    owner_scope, leader_scope = command_scopes(runtime)
    recovered = []
    with database.immediate():
        rows = database.execute(
            """SELECT c.*, e.action, e.portfolio_id, e.deployment_id, e.revision,
                      e.phase, e.effect_json
            FROM dashboard_commands c LEFT JOIN dashboard_command_evidence e USING(command_id)
            WHERE c.scope IN (?, ?) AND c.status = 'PROCESSING'
            ORDER BY c.created_at, c.rowid""",
            (owner_scope, leader_scope),
        ).fetchall()
        for row in rows:
            state = database.execute(
                "SELECT revision FROM dashboard_control_state WHERE scope = ?", (row["scope"],)
            ).fetchone()
            revision = row["revision"] if row["revision"] is not None else 0
            evidence = _evidence_document(row["effect_json"])
            response = evidence.get("response")
            error = evidence.get("error")
            binding_valid = (
                row["deployment_id"] == getattr(runtime, "deployment_id", "deployment")
                and (row["scope"] == owner_scope or row["portfolio_id"] == runtime.portfolio_id)
                and revision >= 1 and state is not None and state["revision"] >= revision
            )
            committed = (
                binding_valid and row["phase"] == "EFFECT_COMMITTED" and isinstance(response, dict)
                and response.get("revision") == revision
                and (error is None or (type(error) is int and 400 <= error <= 599))
            )
            recovery = {
                "effect_outcome": "COMMITTED" if committed and error is None else "UNKNOWN",
                "receipt_outcome": "COMMITTED" if committed else "UNKNOWN",
                "needs_review": not committed or error is not None,
                "local_effects": evidence.get("local_effects", {}),
                "initial_local_effects": evidence.get("initial_local_effects", {}),
                "replayed": False,
            }
            if committed:
                status = "COMPLETE" if error is None else f"FAILED:{error}"
            else:
                status = "FAILED:409"
                response = {
                    "revision": revision,
                    "detail": {
                        "reason": "interrupted command requires review; external outcome is not established",
                        "command_id": row["command_id"], "command_state": "FAILED", **recovery,
                    },
                }
            database.execute(
                "UPDATE dashboard_commands SET status = ?, response_json = ? WHERE command_id = ?",
                (status, json.dumps(response), row["command_id"]),
            )
            # Legacy commands have no binding beyond their protected scope. Keep
            # that uncertainty explicit; never infer a previous effect from the
            # portfolio's present state or invent missing command revisions.
            evidence.update(recovery=recovery)
            if row["phase"] is None:
                database.execute(
                    """INSERT INTO dashboard_command_evidence
                    (command_id, action, portfolio_id, deployment_id, revision, phase, effect_json, updated_at)
                    VALUES (?, 'legacy-unknown', ?, ?, ?, 'RECOVERED', ?, ?)""",
                    (row["command_id"], runtime.portfolio_id, getattr(runtime, "deployment_id", "deployment"),
                     revision, json.dumps(evidence), utc_iso(runtime.clock.now())),
                )
            else:
                database.execute(
                    "UPDATE dashboard_command_evidence SET phase = 'RECOVERED', effect_json = ?, updated_at = ? "
                    "WHERE command_id = ?",
                    (json.dumps(evidence), utc_iso(runtime.clock.now()), row["command_id"]),
                )
            recovered.append({"command_id": row["command_id"], "status": status, **recovery})
    return recovered
