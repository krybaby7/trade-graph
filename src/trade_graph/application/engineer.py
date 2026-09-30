"""R1 artifact runner. The controller records check results; candidate prose does not."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from trade_graph.adapters.engineering.provenance import checks_module_hash
from trade_graph.adapters.engineering.runner import ALLOWLIST_PREFIXES, EngineerRunner
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import ChangeResult, ChangeTask
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState, ValidationFailure
from trade_graph.kernel.authority import path_is_protected

MAX_FILES = 5
MAX_CHANGED_LINES = 200
ALLOWED_CLASSES = frozenset(
    {"artifact_config", "prompt", "report_template", "context_policy", "schedule"}
)


class ArtifactEngineer:
    def __init__(self, database: Database, clock: Clock, source_root: Path, ledger: Ledger) -> None:
        self.database = database
        self.clock = clock
        self.ledger = ledger
        self.runner = EngineerRunner(source_root)

    def baseline(self, destination: Path) -> str:
        return self.runner.stage(destination)

    def commission(self, portfolio_id: str, task: ChangeTask) -> str:
        unknown = [item for item in task.allowed_classes if item not in ALLOWED_CLASSES]
        if unknown:
            raise AuthorityDenied(f"change class is not allowlisted: {unknown}")
        if task.max_steps < 1 or task.max_spend.amount < 0:
            raise ValidationFailure("task bounds must allow one bounded attempt")
        for raw in task.allowed_paths:
            self._assert_path(raw, task.allowed_paths)
        now = utc_iso(self.clock.now())
        with self.database.immediate() as conn:
            existing = conn.execute(
                "SELECT change_id FROM change_tasks WHERE change_id = ?",
                (task.record_id,),
            ).fetchone()
            if existing is not None:
                raise ValidationFailure("change task already exists")
            conn.execute(
                """INSERT INTO change_tasks
                (change_id, portfolio_id, state, document_json, baseline_hash, created_at)
                VALUES (?, ?, 'AUTHORIZED', ?, ?, ?)""",
                (task.record_id, portfolio_id, task.model_dump_json(), task.baseline_hash, now),
            )
        return task.record_id

    def implement(
        self,
        portfolio_id: str,
        change_id: str,
        files: dict[str, str],
        destination: Path,
    ) -> ChangeResult:
        task = self._task(portfolio_id, change_id)
        attempts = self.database.execute(
            "SELECT COUNT(*) AS n FROM candidates WHERE change_id = ?",
            (change_id,),
        ).fetchone()["n"]
        if attempts >= task.max_steps:
            return self._fail(portfolio_id, task, [], "attempt limit reached")
        if self.clock.now() > task.expires_at_utc:
            return self._fail(portfolio_id, task, list(files), "change task expired")
        try:
            self._bounds(task, files)
        except (PermissionError, ValidationFailure) as exc:
            return self._fail(portfolio_id, task, list(files), str(exc))
        baseline = self.runner.stage(destination)
        if baseline != task.baseline_hash:
            return self._fail(portfolio_id, task, list(files), "baseline moved; revalidate")
        changed = self.runner.apply_files(destination, files)
        report = self.runner.attest(destination)
        attestation_id = self._record_attestation(task, changed, report)
        state = "READY" if report["exit_code"] == 0 else "FAILED"
        return self._store(
            portfolio_id,
            task,
            changed,
            state,
            report["content_hash"],
            attestation_id,
            "independent checks recorded; a runner label is not provenance",
        )

    def _bounds(self, task: ChangeTask, files: dict[str, str]) -> None:
        if not files:
            raise ValidationFailure("advice without a file is not an artifact")
        if len(files) > MAX_FILES:
            raise ValidationFailure("file limit exceeded")
        if _line_count(files) > MAX_CHANGED_LINES:
            raise ValidationFailure("line limit exceeded")
        for relative in files:
            self._assert_path(relative, task.allowed_paths)

    def _assert_path(self, relative: str, allowed_paths: list[str]) -> None:
        normalized = relative.replace("\\", "/").lstrip("./")
        if ".." in Path(normalized).parts or path_is_protected(normalized):
            raise PermissionError(normalized)
        if not normalized.startswith(ALLOWLIST_PREFIXES):
            raise PermissionError(normalized)
        if not any(_path_allowed(normalized, item) for item in allowed_paths):
            raise PermissionError(normalized)

    def _task(self, portfolio_id: str, change_id: str) -> ChangeTask:
        row = self.database.execute(
            "SELECT document_json FROM change_tasks WHERE change_id = ? AND portfolio_id = ?",
            (change_id, portfolio_id),
        ).fetchone()
        if row is None:
            raise ValidationFailure("unknown change task")
        return ChangeTask.model_validate_json(row["document_json"])

    def _record_attestation(self, task: ChangeTask, changed: list[str], report: dict) -> str:
        content_hash = report["content_hash"]
        module_hash = checks_module_hash()
        existing = self.database.execute(
            """SELECT attestation_id, checks_module_hash, exit_code
            FROM controller_attestations WHERE content_hash = ?""",
            (content_hash,),
        ).fetchone()
        if existing is not None:
            if existing["checks_module_hash"] != module_hash or existing["exit_code"] != report["exit_code"]:
                raise StaleState("attestation conflict")
            return str(existing["attestation_id"])
        attestation_id = str(uuid.uuid4())
        manifest = {
            "change_id": task.record_id,
            "changed_files": changed,
            "content_hash": content_hash,
            "baseline_hash": task.baseline_hash,
            "checks_module_hash": module_hash,
            "exit_code": report["exit_code"],
            "bounds": {"max_files": MAX_FILES, "max_lines": MAX_CHANGED_LINES, "max_steps": task.max_steps},
            "secret_free": True,
        }
        now = utc_iso(self.clock.now())
        with self.database.immediate() as conn:
            conn.execute(
                """INSERT INTO controller_attestations
                (attestation_id, content_hash, checks_module_hash, exit_code, command,
                 stdout_sha256, stderr_sha256, manifest_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    attestation_id,
                    content_hash,
                    module_hash,
                    report["exit_code"],
                    report["command"],
                    hashlib.sha256(report["stdout"].encode()).hexdigest(),
                    hashlib.sha256(report["stderr"].encode()).hexdigest(),
                    json.dumps(manifest, sort_keys=True),
                    now,
                ),
            )
        return attestation_id

    def _fail(self, portfolio_id: str, task: ChangeTask, files: list[str], reason: str) -> ChangeResult:
        return self._store(portfolio_id, task, files, "FAILED", None, None, reason)

    def _store(
        self,
        portfolio_id: str,
        task: ChangeTask,
        files: list[str],
        state: str,
        content_hash: str | None,
        attestation_id: str | None,
        limits: str,
    ) -> ChangeResult:
        candidate_id = str(uuid.uuid4())
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
            change_id=task.record_id,
            candidate_id=candidate_id,
            state=state,  # type: ignore[arg-type]
            content_hash=content_hash,
            changed_files=files,
            attestation_id=attestation_id,
            known_limits=limits,
        )
        now = utc_iso(self.clock.now())
        with self.database.immediate() as conn:
            conn.execute(
                """INSERT INTO candidates
                (candidate_id, change_id, state, content_hash, baseline_hash,
                 attestation_json, document_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    candidate_id,
                    task.record_id,
                    state,
                    content_hash,
                    task.baseline_hash,
                    None,
                    result.model_dump_json(),
                    now,
                ),
            )
            conn.execute(
                "UPDATE change_tasks SET state = ? WHERE change_id = ?",
                (state, task.record_id),
            )
        self.ledger._activity(
            portfolio_id,
            "engineer_candidate",
            {"candidate_id": candidate_id, "state": state, "limits": limits, "content_hash": content_hash},
        )
        return result


def _line_count(files: dict[str, str]) -> int:
    total = 0
    for content in files.values():
        if content == "":
            continue
        total += content.count("\n") + (0 if content.endswith("\n") else 1)
    return total


def _path_allowed(path: str, allowed: str) -> bool:
    item = allowed.replace("\\", "/").lstrip("./")
    return path == item or path.startswith(item.rstrip("/") + "/")
