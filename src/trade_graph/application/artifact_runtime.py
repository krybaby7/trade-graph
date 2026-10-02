"""Verified data-artifact consumers. Reloads never restore financial state."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal

from trade_graph.application.authority import AuthorityRecord
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState, ValidationFailure
from trade_graph.roles.judgement import select_context
from trade_graph.roles.strategies import TEMPLATES


class ArtifactRuntime:
    def __init__(self, versions, scheduler) -> None:
        self.versions, self.scheduler = versions, scheduler
        self.database = scheduler.database
        self.loaded: dict[str, dict] = {}

    @staticmethod
    def identity(bundle: dict) -> dict:
        return {"version_id": bundle["version_id"], "artifact_hash": bundle["artifact_hash"],
                "generation": bundle["generation"], "manifest_sha256": bundle["manifest"]["sha256"]}

    def maintain(self, *, reconcile=None, consumer_id: str = "controller") -> dict:
        states = {}
        for row in self.database.execute("SELECT portfolio_id FROM active_versions").fetchall():
            pid = row["portfolio_id"]
            state = self.versions.maintain(pid)
            if state in {"ROLLED_BACK", "RESTORE_PENDING"} and reconcile is not None:
                bundle = self.versions.begin(pid, consumer_id, reconcile)
                self.policy(bundle)
                self.templates(bundle)
                self.schedule_settings(bundle)
                self.apply_schedules(pid, bundle)
                self.loaded[pid] = deepcopy(bundle)
                state = "RESTORED"
            elif state == "BLOCKED" and reconcile is not None:
                # Graph recovery never suspends deterministic position protection.
                reconcile()
            states[pid] = state
        return states

    def prepare(self, task: dict, *, consumer_id: str, reconcile) -> dict:
        pid = task["portfolio_id"]
        bundle = self.loaded.get(pid)
        if bundle is not None:
            try:
                self.versions.assert_current(pid, bundle)
            except (StaleState, ValidationFailure):
                bundle = None
        if bundle is None:
            bundle = self.versions.begin(pid, consumer_id, reconcile, lease=task.get("_lease"))
            # Decode before publishing the process-local loaded generation. Bad data
            # can never silently retain the prior policy under a new fingerprint.
            self.policy(bundle)
            self.templates(bundle)
            self.schedule_settings(bundle)
            self.loaded[pid] = deepcopy(bundle)
            self.apply_schedules(pid, bundle)
        self.versions.assert_current(pid, bundle)
        return deepcopy(bundle)

    def bundle_for(self, task: dict) -> dict:
        artifact = task.get("snapshot", {}).get("artifact") or task.get("artifact")
        if artifact is None:
            raise ValidationFailure("persisted loaded artifact identity required")
        bundle = task.get("_artifact_bundle")
        if bundle is None:
            bundle = self.versions.load_active(task["portfolio_id"], expected_hash=artifact["artifact_hash"])
        if self.identity(bundle) != artifact:
            raise StaleState("snapshot artifact generation or manifest changed")
        self.versions.assert_current(task["portfolio_id"], bundle)
        return bundle

    def assert_task(self, task: dict) -> None:
        self.bundle_for(task)

    @staticmethod
    def policy(bundle: dict) -> dict:
        try:
            return json.loads(bundle["files"]["artifacts/context_policy.json"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationFailure("verified context policy is unavailable") from exc

    def selected_context(self, bundle: dict, guard: dict, lessons: list[dict]) -> dict:
        selected = select_context(self.policy(bundle), lessons)
        # Artifact text chooses a bounded general-lesson limit. Protected software
        # supplies the actual mandate and active safety obligations every time.
        return {**selected, "mandate_obligations": guard["mandate"],
                "active_safety": {"policy": guard["policy"], "pause": guard["pause"]}}

    @staticmethod
    def prompt(bundle: dict, role: str) -> str:
        name = "researcher" if role == "research" else role
        return bundle["files"].get(f"prompts/{name}.md", "")

    @staticmethod
    def templates(bundle: dict) -> dict:
        templates = {key: asdict(value) for key, value in TEMPLATES.items()}
        for name, text in bundle["files"].items():
            if name.startswith("strategies/templates/"):
                document = json.loads(text)
                templates[document["strategy_id"]] = document
        return templates

    @staticmethod
    def schedule_settings(bundle: dict) -> dict[str, int]:
        text = bundle["files"].get("artifacts/schedules.json")
        return json.loads(text)["interval_seconds"] if text else {}

    @staticmethod
    def model_routes(bundle: dict) -> dict[str, str]:
        """Tested data selects card IDs; protected runtime configuration grants approval."""
        text = bundle["files"].get("artifacts/model_routing.json")
        return json.loads(text)["routes"] if text else {}

    def apply_schedules(self, portfolio_id: str, bundle: dict) -> None:
        """Only this explicit namespace is artifact-managed; protection has no schedule here."""
        settings = self.schedule_settings(bundle)
        with self.database.immediate():
            self.versions.assert_current(portfolio_id, bundle)
            rows = self.database.execute(
                "SELECT * FROM schedules WHERE portfolio_id = ? AND name LIKE 'artifact-%-review'",
                (portfolio_id,),
            ).fetchall()
            for row in rows:
                role = row["name"][len("artifact-"):-len("-review")]
                if role not in settings:
                    self.database.execute("DELETE FROM schedules WHERE schedule_id = ?", (row["schedule_id"],))
            for role, interval in settings.items():
                name = f"artifact-{role}-review"
                row = self.database.execute(
                    "SELECT * FROM schedules WHERE portfolio_id = ? AND name = ?", (portfolio_id, name),
                ).fetchone()
                if row is None:
                    self.scheduler.ensure_schedule(portfolio_id, name, interval, "coalesce")
                elif row["interval_seconds"] != interval:
                    self.database.execute(
                        "UPDATE schedules SET interval_seconds = ?, next_due_at = ? WHERE schedule_id = ?",
                        (interval, utc_iso(self.scheduler.clock.now() + timedelta(seconds=interval)),
                         row["schedule_id"]),
                    )

    def coalesce_due(self, portfolio_id: str, role: str, *, allocated_spend: Decimal | None = None,
                     max_attempts: int = 1) -> str | None:
        bundle = self.versions.load_active(portfolio_id)
        self.versions.assert_current(portfolio_id, bundle)
        self.apply_schedules(portfolio_id, bundle)
        if role not in self.schedule_settings(bundle):
            return None
        with self.database.immediate():
            self.versions.assert_current(portfolio_id, bundle)
            self.scheduler._positive(max_attempts, "max_attempts")
            policy = AuthorityRecord(self.database, self.scheduler.clock).active_policy()
            if max_attempts > policy.ordinary_max_paid_attempts:
                raise AuthorityDenied("schedule attempts exceed owner bounds")
            if allocated_spend is not None and (
                not isinstance(allocated_spend, Decimal) or not allocated_spend.is_finite()
                or allocated_spend < 0 or allocated_spend > policy.root_paid_limit.amount
            ):
                raise AuthorityDenied("schedule allocation exceeds owner bounds")
            task_id = self.scheduler.coalesce_due(portfolio_id, f"artifact-{role}-review", role)
            if task_id:
                self.database.execute("UPDATE tasks SET expected_version = ?, max_attempts = ? WHERE task_id = ?",
                                      (bundle["artifact_hash"], max_attempts, task_id))
                if allocated_spend is not None:
                    self.scheduler.allocate(task_id, allocated_spend)
            return task_id

    def render_report(self, portfolio_id: str, sections: dict) -> dict:
        bundle = self.versions.load_active(portfolio_id)
        self.versions.assert_current(portfolio_id, bundle)
        text = bundle["files"].get("artifacts/report_layout.json")
        ordered = json.loads(text)["sections"] if text else ["summary", "financial", "costs", "engineering", "risks"]
        return {name: deepcopy(sections[name]) for name in ordered if name in sections}

    def observe(self, task: dict, output: dict) -> None:
        if task.get("role") != "trader":
            return
        snapshot = task.get("snapshot")
        decision_id = output.get("decision_id")
        if snapshot is None and decision_id:
            row = self.database.execute(
                "SELECT snapshot_id FROM decisions WHERE decision_id = ?", (decision_id,),
            ).fetchone()
            snap = self.database.execute(
                "SELECT payload_json FROM snapshots WHERE snapshot_id = ?", (row[0],),
            ).fetchone()
            snapshot = json.loads(snap[0]) if snap else None
        if snapshot is None:
            # Terminal handler results recover before RoleWorker makes another
            # snapshot. Recover the original scoped request/snapshot for a failed
            # inference too, so a crash cannot drop the health fault permanently.
            snap = self.database.execute(
                """SELECT s.payload_json FROM snapshots s JOIN model_invocations i ON i.run_id = s.snapshot_id
                WHERE i.task_id = ? AND i.portfolio_id = ? AND s.portfolio_id = ?
                ORDER BY i.created_at DESC, s.rowid DESC LIMIT 1""",
                (task["task_id"], task["portfolio_id"], task["portfolio_id"]),
            ).fetchone()
            if snap is None:
                snap = self.database.execute(
                    """SELECT payload_json FROM snapshots WHERE portfolio_id = ?
                    AND json_extract(payload_json, '$.task_id') = ? ORDER BY rowid DESC LIMIT 1""",
                    (task["portfolio_id"], task["task_id"]),
                ).fetchone()
            snapshot = json.loads(snap[0]) if snap else None
        if not snapshot or not snapshot.get("artifact"):
            return
        try:
            bundle = self.versions.load_active(
                task["portfolio_id"], expected_hash=snapshot["artifact"]["artifact_hash"],
            )
            if self.identity(bundle) != snapshot["artifact"]:
                return  # Old completion cannot grade a later activation of identical bytes.
        except (StaleState, ValidationFailure):
            return
        status = output.get("_status", "SUCCEEDED")
        self.versions.observe(
            task["portfolio_id"], bundle, decision_id or task["task_id"],
            ok=status == "SUCCEEDED" and bool(decision_id),
            context_bytes=output.get("context_bytes", 0), input_tokens=output.get("input_tokens", 0),
            output_tokens=output.get("output_tokens", 0), required_retained=bool(
                snapshot.get("selected_context", {}).get("mandate_obligations")
                and snapshot.get("selected_context", {}).get("active_safety")),
            reason=output.get("reason", ""),
        )
