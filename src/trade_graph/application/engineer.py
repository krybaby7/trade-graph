"""Bounded artifact implementation under persisted, fenced Leader commissions."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from trade_graph.adapters.engineering.artifact_policy import artifact_class
from trade_graph.adapters.engineering.provenance import checks_module_hash
from trade_graph.adapters.engineering.runner import ALLOWLIST_PREFIXES, EngineerRunner
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.change_authority import authorized_change
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import ChangeResult, ChangeTask
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState, ValidationFailure
from trade_graph.kernel.authority import path_is_protected

MAX_FILES = 5
MAX_CHANGED_LINES = 200
ALLOWED_CLASSES = frozenset({"artifact_config", "prompt", "report_template", "context_policy", "schedule"})


class ArtifactEngineer:
    def __init__(self, database: Database, clock: Clock, source_root: Path, ledger: Ledger) -> None:
        self.database, self.clock, self.ledger = database, clock, ledger
        self.runner = EngineerRunner(source_root)

    def baseline(self, destination: Path) -> str:
        return self.runner.stage(destination)

    def commission(self, portfolio_id: str, task: ChangeTask) -> str:
        raise AuthorityDenied("use propose, then the persisted Leader handler to commission work")

    def propose(self, portfolio_id: str, task: ChangeTask) -> str:
        if task.portfolio_id != portfolio_id or task.mode != "paper":
            raise AuthorityDenied("proposal scope mismatch")
        if not task.allowed_classes or not set(task.allowed_classes).issubset(ALLOWED_CLASSES):
            raise AuthorityDenied("change class is not allowlisted")
        if task.max_steps < 1 or task.max_spend.amount < 0 or task.max_spend.currency != "EUR":
            raise ValidationFailure("task requires bounded steps and nonnegative EUR spend")
        for raw in task.allowed_paths:
            self._assert_path(raw, task.allowed_paths)
        with self.database.immediate() as conn:
            if conn.execute("SELECT 1 FROM change_tasks WHERE change_id = ?", (task.record_id,)).fetchone():
                raise ValidationFailure("change task already exists")
            conn.execute(
                """INSERT INTO change_tasks
                (change_id, portfolio_id, state, document_json, baseline_hash, created_at)
                VALUES (?, ?, 'PROPOSED', ?, ?, ?)""",
                (task.record_id, portfolio_id, task.model_dump_json(), task.baseline_hash, utc_iso(self.clock.now())),
            )
        return task.record_id

    def implement(
        self, portfolio_id: str, change_id: str, files: dict[str, str], destination: Path,
        *, authorize: Callable[[], None] | None = None, operation_id: str | None = None,
        fence: Callable[[], None] | None = None,
    ) -> ChangeResult:
        # No worktree or files touched until lifecycle, commission, scope and baseline pass.
        token = uuid.uuid4().hex
        patch_hash = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
        with self.database.immediate() as conn:
            if fence:
                fence()
            if authorize:
                authorize()
            task, row, commission = authorized_change(self.database, self.clock, portfolio_id, change_id)
            previous = conn.execute(
                """SELECT * FROM engineering_attempts WHERE change_id = ?
                AND json_extract(details_json, '$.operation_id') = ? ORDER BY number DESC LIMIT 1""",
                (change_id, operation_id),
            ).fetchone() if operation_id else None
            if previous and json.loads(previous["details_json"])["patch_hash"] != patch_hash:
                raise StaleState("artifact operation reused for a different patch")
            if previous and previous["state"] in {"READY", "FAILED"}:
                candidate_id = json.loads(previous["details_json"])["candidate_id"]
                cached = conn.execute("SELECT document_json FROM candidates WHERE candidate_id = ?",
                                      (candidate_id,)).fetchone()
                return ChangeResult.model_validate_json(cached["document_json"])
            if previous and len(json.loads(previous["details_json"]).get("recoveries", [])) >= 3:
                raise ValidationFailure("local validation recovery limit reached")
            recovering = (
                row["state"] == "DEVELOPING"
                and ((previous is not None and authorize is not None)
                     or (row["lease_expires_at"] is not None
                         and row["lease_expires_at"] <= utc_iso(self.clock.now())))
            )
            if row["state"] not in {"AUTHORIZED", "FAILED"} and not recovering:
                raise AuthorityDenied("change lifecycle is not runnable")
            if not previous and row["attempts_used"] >= task.max_steps:
                raise ValidationFailure("attempt limit reached")
            if recovering:
                conn.execute(
                    "UPDATE engineering_attempts SET state = 'ABANDONED' WHERE attempt_id = ?", (row["lease_token"],)
                )
            expiry = utc_iso(self.clock.now() + timedelta(seconds=90))
            conn.execute(
                """UPDATE change_tasks SET state = 'DEVELOPING', attempts_used = attempts_used + ?,
                lease_token = ?, lease_expires_at = ? WHERE change_id = ?""",
                (0 if previous else 1, token, expiry, change_id),
            )
            details = {"operation_id": operation_id, "destination": str(destination), "patch_hash": patch_hash}
            if previous:
                old = json.loads(previous["details_json"])
                details["recoveries"] = old.get("recoveries", []) + [
                    {"attempt_id": previous["attempt_id"], "destination": old["destination"]},
                ]
                conn.execute(
                    "UPDATE engineering_attempts SET attempt_id = ?, state = 'RUNNING', details_json = ? "
                    "WHERE attempt_id = ?", (token, json.dumps(details), previous["attempt_id"]),
                )
            else:
                conn.execute(
                    """INSERT INTO engineering_attempts VALUES (?, ?, ?, 'RUNNING', ?, ?)""",
                    (token, change_id, row["attempts_used"] + 1, json.dumps(details), utc_iso(self.clock.now())),
                )
        report = None
        changed = list(files)
        reason = "independent checks recorded with a confined, data-only checker; no executable-code authority"
        try:
            self._bounds(task, files)
            baseline = self.runner.stage(destination)
            if baseline != task.baseline_hash:
                raise StaleState("source baseline moved; revalidate")
            changed = self.runner.apply_files(destination, files)
            report = self.runner.attest(destination)
            if report["exit_code"] != 0:
                findings = report.get("failures") or [report.get("stderr") or "checker failed"]
                reason = "independent checks failed: " + "; ".join(str(f)[:200] for f in findings[:5])
            if report["content_hash"] != self.runner.hash_tree(destination):
                report["exit_code"] = 1
                reason = "artifact changed after testing"
        except Exception as exc:
            reason = str(exc)
        candidate_id, attestation_id = str(uuid.uuid4()), str(uuid.uuid4()) if report else None
        state = "READY" if report and report["exit_code"] == 0 else "FAILED"
        with self.database.immediate() as conn:
            if fence:
                fence()
            # Recheck immediately before publication: a lease or an earlier approval is not current authority.
            current = conn.execute("SELECT * FROM change_tasks WHERE change_id = ?", (change_id,)).fetchone()
            if current["lease_token"] != token or current["lease_expires_at"] <= utc_iso(self.clock.now()):
                raise StaleState("engineering lease expired or replaced")
            try:
                if authorize:
                    authorize()
                authorized_change(self.database, self.clock, portfolio_id, change_id)
                if current["state"] != "DEVELOPING":
                    raise AuthorityDenied("change lifecycle moved during implementation")
            except (AuthorityDenied, StaleState, ValidationFailure) as exc:
                state, reason = "FAILED", str(exc)
            result = ChangeResult(
                record_id=candidate_id,
                created_at_utc=self.clock.now(),
                run_id=task.run_id,
                task_id=task.task_id,
                root_task_id=task.root_task_id,
                portfolio_id=portfolio_id,
                mode="paper",
                system_version_id=task.system_version_id,
                trace_id=task.trace_id,
                change_id=change_id,
                candidate_id=candidate_id,
                state=state,
                content_hash=report["content_hash"] if report else None,
                changed_files=changed,
                attestation_id=attestation_id,
                known_limits=reason,
            )
            if report:
                conn.execute(
                    """INSERT INTO candidate_attestations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        attestation_id,
                        candidate_id,
                        portfolio_id,
                        change_id,
                        commission["decision_id"],
                        commission["task_hash"],
                        report["content_hash"],
                        task.baseline_hash,
                        checks_module_hash(),
                        report["exit_code"],
                        json.dumps(report, sort_keys=True),
                        utc_iso(self.clock.now()),
                    ),
                )
            conn.execute(
                """INSERT INTO candidates
                (candidate_id, change_id, state, content_hash, baseline_hash,
                 attestation_json, document_json, created_at)
                VALUES (?, ?, ?, ?, ?, NULL, ?, ?)""",
                (
                    candidate_id,
                    change_id,
                    state,
                    result.content_hash,
                    task.baseline_hash,
                    result.model_dump_json(),
                    utc_iso(self.clock.now()),
                ),
            )
            # Preserve an external cancellation/rejection instead of resurrecting it as FAILED.
            if current["state"] == "DEVELOPING":
                conn.execute("UPDATE change_tasks SET state = ? WHERE change_id = ?", (state, change_id))
            conn.execute(
                "UPDATE change_tasks SET lease_token = NULL, lease_expires_at = NULL WHERE change_id = ?", (change_id,)
            )
            conn.execute(
                "UPDATE engineering_attempts SET state = ?, details_json = ? WHERE attempt_id = ?",
                (state, json.dumps({**details, "candidate_id": candidate_id, "reason": reason}), token),
            )
            self.ledger._activity(
                portfolio_id, "engineer_candidate", {"candidate_id": candidate_id, "state": state, "limits": reason}
            )
        return result

    def _bounds(self, task: ChangeTask, files: dict[str, str]) -> None:
        if not files:
            raise ValidationFailure("advice without a file is not an artifact")
        if sum(len(content.encode()) for content in files.values()) > 65536:
            raise ValidationFailure("artifact byte limit exceeded")
        if len(files) > MAX_FILES:
            raise ValidationFailure("file limit exceeded")
        if sum(len(content.splitlines()) for content in files.values()) > MAX_CHANGED_LINES:
            raise ValidationFailure("line limit exceeded")
        for relative in files:
            self._assert_path(relative, task.allowed_paths)
            kind = artifact_class(relative)
            # Historical commissions use artifact_config for validated JSON data.
            # Prompt authority is separate and never implied by that umbrella.
            if (kind not in task.allowed_classes
                    and not (kind != "prompt" and "artifact_config" in task.allowed_classes)):
                raise AuthorityDenied("artifact class outside commissioned scope")

    def _assert_path(self, relative: str, allowed_paths: list[str]) -> None:
        normalized = relative.replace("\\", "/")
        if Path(normalized).is_absolute() or ".." in Path(normalized).parts or path_is_protected(normalized):
            raise PermissionError(normalized)
        if not normalized.startswith(ALLOWLIST_PREFIXES):
            raise PermissionError(normalized)
        if not any(normalized == item or normalized.startswith(item.rstrip("/") + "/") for item in allowed_paths):
            raise PermissionError(normalized)
