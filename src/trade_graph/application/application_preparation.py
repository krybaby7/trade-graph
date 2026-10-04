"""One-step trusted scheduling and durable functional health for synthetic projection.

This controller is explicitly assembled by trusted preparation code. It never
modifies ordinary R1 versions or grants paid/live/production/class authority.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.adapters.engineering.plugin_artifacts import SHA256_PATTERN, canonical_bytes, sha256
from trade_graph.adapters.engineering.plugin_runtime import authenticate, authenticated_report, strict_document
from trade_graph.application import application_engineering
from trade_graph.application.application_engineering import CommissionedApplicationHandler, OfflineApplicationEngineer
from trade_graph.application.application_projection import FLAGS
from trade_graph.application.change_authority import authorized_change
from trade_graph.application.execution import Execution
from trade_graph.application.worker import RoleWorker
from trade_graph.domain.errors import AuthorityDenied, StaleState, TradeGraphError

EXPERIMENT = "offline-application-preparation"


def application_preparation_controller_sha256() -> str:
    return sha256(Path(__file__).read_bytes()
                  + bytes.fromhex(application_engineering.application_engineering_controller_sha256()))


class ApplicationPreparationPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal[1] = 1
    execution_scope: Literal["offline_preparation"] = "offline_preparation"
    portfolio_id: str = Field(min_length=1, max_length=128)
    worker_task_id: str = Field(min_length=1, max_length=128)
    commission_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    health_corpus_sha256: str = Field(pattern=SHA256_PATTERN)
    health_case_sha256s: list[str] = Field(min_length=1, max_length=8)
    health_deadline_seconds: int = Field(default=120, ge=1, le=120)

    @field_validator("health_case_sha256s")
    @classmethod
    def exact_cases(cls, value):
        if len(set(value)) != len(value) or any(len(item) != 64 or any(c not in "0123456789abcdef" for c in item)
                                              for item in value):
            raise ValueError("independent unique health case digests required")
        return value

    @property
    def sha256(self) -> str:
        return sha256(canonical_bytes(self.model_dump()))


class ApplicationPreparationLoop:
    """At most one Engineer lease and one confined health case per advance()."""

    def __init__(self, engineer: OfflineApplicationEngineer, handler: CommissionedApplicationHandler, *,
                 policy: ApplicationPreparationPolicy, expected_policy_sha256: str,
                 expected_controller_sha256: str, receipt_key: bytes, owner: str, execution: Execution) -> None:
        if (type(engineer) is not OfflineApplicationEngineer or type(handler) is not CommissionedApplicationHandler
                or handler.engineer is not engineer or handler.scheduler.database is not engineer.database):
            raise AuthorityDenied("concrete commissioned application route required")
        if (type(execution) is not Execution or execution.database is not engineer.database
                or type(execution.broker) is not PaperBroker or execution.mode != "paper"
                or execution.venue != "paper" or execution.account_id != "paper"):
            raise AuthorityDenied("concrete protected paper reconciliation required")
        self.execution = execution
        if type(receipt_key) is not bytes or len(receipt_key) < 32 or not owner or len(owner) > 128:
            raise ValueError("private workflow receipt key and fresh bounded process owner required")
        self.engineer, self.handler, self.projection = engineer, handler, engineer.projection
        self.database, self.scheduler, self.store = engineer.database, handler.scheduler, engineer.store
        self.owner, self._key = owner, receipt_key
        self._policy_bytes = canonical_bytes(policy.model_dump())
        self.policy_pin, self.controller_pin = expected_policy_sha256, expected_controller_sha256
        self._assert_pinned()
        self._offline()
        self.path = self.store.root / "projection-workflow.sqlite"
        created = not self.path.exists()
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        fd = os.open(self.path, flags | (os.O_CREAT | os.O_EXCL if created else 0), 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 8 * 1024 * 1024):
                raise AuthorityDenied("private single-link application workflow journal required")
            self.db = sqlite3.connect(self.path, isolation_level=None, timeout=5)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA max_page_count=2048")
            if (self.path.stat().st_dev, self.path.stat().st_ino) != (info.st_dev, info.st_ino):
                raise AuthorityDenied("application workflow journal changed during open")
            if created:
                self.db.execute("CREATE TABLE workflow_state(id INTEGER PRIMARY KEY CHECK(id=1), "
                                "payload_json TEXT NOT NULL,receipt_sha256 TEXT NOT NULL)")
                self.db.execute("CREATE TABLE workflow_samples(case_sha256 TEXT PRIMARY KEY, "
                                "payload_json TEXT NOT NULL,receipt_sha256 TEXT NOT NULL)")
                self._save_state({"status": "ENGINEERING", "candidate_id": None, "build_sha256": None,
                                  "generation": None, "started_at_ns": None, "expansion_receipt_sha256": None})
            if {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")} != {
                "workflow_state", "workflow_samples"
            }:
                raise AuthorityDenied("fixed isolated application workflow schema required")
            self.state()
            self._samples()
        except BaseException:
            if hasattr(self, "db"):
                self.db.close()
            raise
        finally:
            os.close(fd)

    @property
    def policy(self) -> ApplicationPreparationPolicy:
        return ApplicationPreparationPolicy.model_validate(strict_document(self._policy_bytes, maximum_bytes=8192))

    def _assert_pinned(self) -> None:
        if (sha256(self._policy_bytes) != self.policy_pin
                or application_preparation_controller_sha256() != self.controller_pin
                or self.policy.commission_policy_sha256 != self.engineer.policy_pin):
            raise AuthorityDenied("independent application workflow pins changed")
        self.engineer._assert_pinned()
        cases = {sha256(canonical_bytes(case["snapshot"])): case for case in self._health_corpus()["cases"]}
        if not set(self.policy.health_case_sha256s).issubset(cases):
            raise AuthorityDenied("independently selected health case absent from pinned corpus")
        if (self.handler.provider != "scripted" or self.handler.gateway.paid_calls_enabled
                or self.handler.gateway.api_keys):
            raise AuthorityDenied("application preparation cannot use funded credentials")

    def _health_corpus(self) -> dict:
        data = self.projection._load_object("corpora", self.policy.health_corpus_sha256, "corpus.json", 131072)
        document = strict_document(data, maximum_bytes=131072)
        if (set(document) != {"schema_version", "class_name", "cases"} or document["schema_version"] != 1
                or document["class_name"] != "secretary_digest_projection" or type(document["cases"]) is not list
                or not 1 <= len(document["cases"]) <= 8):
            raise AuthorityDenied("independent bounded application health corpus required")
        permitted = {sha256(canonical_bytes(case["snapshot"])): case for case in self.projection._corpus()["cases"]}
        seen = set()
        for case in document["cases"]:
            if type(case) is not dict or set(case) != {"snapshot", "expected_v1", "expected_v2"}:
                raise AuthorityDenied("fixed parent-selected application health case required")
            self.projection._snapshot(case["snapshot"])
            digest = sha256(canonical_bytes(case["snapshot"]))
            if digest in seen or digest not in permitted or case["expected_v1"] != permitted[digest]["expected_v1"]:
                raise AuthorityDenied("health snapshot/baseline differs from permitted synthetic inputs")
            seen.add(digest)
            self.projection._projection(case["expected_v2"], case["snapshot"], 2)
        return document

    def _offline(self) -> None:
        pid = self.policy.portfolio_id
        portfolio = self.database.execute("SELECT * FROM portfolios WHERE portfolio_id=?", (pid,)).fetchone()
        task = self.database.execute("SELECT * FROM tasks WHERE task_id=?", (self.policy.worker_task_id,)).fetchone()
        if (portfolio is None or portfolio["mode"] != "paper" or portfolio["experiment_id"] != EXPERIMENT
                or task is None or task["portfolio_id"] != pid or task["role"] != "engineer"):
            raise AuthorityDenied("explicit exact synthetic application experiment and Engineer task required")
        if self.database.execute("SELECT 1 FROM model_invocations WHERE portfolio_id=? AND "
                                 "json_extract(request_json,'$.provider') IS NOT 'scripted' LIMIT 1",
                                 (pid,)).fetchone():
            raise AuthorityDenied("application preparation cannot use real provider evidence")
        if self.database.execute("SELECT 1 FROM usage_receipts WHERE synthetic!=1 LIMIT 1").fetchone():
            raise AuthorityDenied("application preparation requires a synthetic cost database")
        if self.database.execute("SELECT 1 FROM budget_reservations b JOIN tasks t USING(task_id) "
                                 "WHERE t.portfolio_id=? AND b.synthetic!=1 LIMIT 1", (pid,)).fetchone():
            raise AuthorityDenied("application preparation cannot use funded reservations")
        # FillRecord has no synthetic flag. Require the actual paper venue and
        # account plus an owned paper intent; do not invent a synthetic field.
        if self.database.execute("SELECT 1 FROM fills f LEFT JOIN order_intents i USING(intent_id) "
                                 "WHERE f.portfolio_id=? AND (f.venue!='paper' OR f.account_id!='paper' "
                                 "OR i.portfolio_id IS NOT f.portfolio_id "
                                 "OR json_extract(i.payload_json,'$.mode') IS NOT 'paper' "
                                 "OR json_extract(i.payload_json,'$.venue') IS NOT 'paper' "
                                 "OR json_extract(i.payload_json,'$.account_id') IS NOT 'paper') LIMIT 1",
                                 (pid,)).fetchone():
            raise AuthorityDenied("application preparation cannot use real venue effects")

    def _receipt(self, kind: str, details: dict) -> str:
        report = {"schema_version": 1, "kind": kind, "policy_sha256": self.policy_pin,
                  "controller_sha256": self.controller_pin, "commission_policy_sha256": self.engineer.policy_pin,
                  "portfolio_id": self.policy.portfolio_id, "worker_task_id": self.policy.worker_task_id,
                  **FLAGS, **details}
        return self.store.retain_receipt(authenticate(report, self._key))

    def _verify(self, row, kind: str) -> dict:
        report = authenticated_report(self.store, row["receipt_sha256"], self._key)
        expected = {"schema_version": 1, "kind": kind, "policy_sha256": self.policy_pin,
                    "controller_sha256": self.controller_pin, "commission_policy_sha256": self.engineer.policy_pin,
                    "portfolio_id": self.policy.portfolio_id, "worker_task_id": self.policy.worker_task_id,
                    **FLAGS}
        if any(report.get(key) != value for key, value in expected.items()):
            raise AuthorityDenied("application workflow scope/key/receipt substitution")
        payload = strict_document(row["payload_json"].encode(), maximum_bytes=32768)
        if report.get("payload") != payload or row["payload_json"] != canonical_bytes(payload).decode():
            raise AuthorityDenied("application workflow payload changed")
        return payload

    def state(self) -> dict:
        row = self.db.execute("SELECT * FROM workflow_state WHERE id=1").fetchone()
        if row is None:
            raise AuthorityDenied("missing authenticated workflow state")
        return self._verify(row, "application_workflow_state")

    def _save_state(self, state: dict) -> None:
        receipt = self._receipt("application_workflow_state", {"payload": state})
        self.db.execute("INSERT INTO workflow_state VALUES(1,?,?) ON CONFLICT(id) DO UPDATE "
                        "SET payload_json=excluded.payload_json,receipt_sha256=excluded.receipt_sha256",
                        (canonical_bytes(state).decode(), receipt))

    def _samples(self) -> list[dict]:
        rows = self.db.execute("SELECT * FROM workflow_samples ORDER BY case_sha256").fetchall()
        if len(rows) > len(self.policy.health_case_sha256s):
            raise AuthorityDenied("application health journal exceeds finite case envelope")
        samples = []
        for row in rows:
            sample = self._verify(row, "application_workflow_health")
            if (row["case_sha256"] != sample.get("case_sha256")
                    or row["case_sha256"] not in self.policy.health_case_sha256s):
                raise AuthorityDenied("application health case identity changed")
            samples.append(sample)
        return samples

    def _save_sample(self, sample: dict) -> None:
        receipt = self._receipt("application_workflow_health", {"payload": sample})
        self.db.execute("INSERT INTO workflow_samples VALUES(?,?,?) ON CONFLICT(case_sha256) DO UPDATE "
                        "SET payload_json=excluded.payload_json,receipt_sha256=excluded.receipt_sha256",
                        (sample["case_sha256"], canonical_bytes(sample).decode(), receipt))

    def _candidate(self, candidate_id: str, *, current_authority: bool) -> dict:
        report = self.engineer.verify_candidate(candidate_id)
        if report["portfolio_id"] != self.policy.portfolio_id or report["status"] != "READY":
            raise AuthorityDenied("exact successful application commission required")
        task = self.database.execute("SELECT document_json FROM change_tasks WHERE change_id=?",
                                     (report["change_id"],)).fetchone()
        if json.loads(task[0])["task_id"] != self.policy.worker_task_id:
            raise AuthorityDenied("application candidate belongs to another Engineer job")
        if current_authority:
            authorized_change(self.database, self.engineer.clock, self.policy.portfolio_id, report["change_id"])
        return report

    def _rollback(self, state: dict, reason: str) -> dict:
        recovery = self.projection.rollback(expected_build=state["build_sha256"],
                                            expected_generation=state["generation"])
        current = self.projection.status()
        if (recovery == "NEWER_RELEASE_PRESERVED"
                and current["active_build"] == self.engineer.policy.baseline_build_sha256
                and current["generation"] == state["generation"] + 1 and current["status"] == "RUNNING"):
            recovery = "ROLLED_BACK_ALREADY"
        status = {"ROLLED_BACK": "ROLLED_BACK", "ROLLED_BACK_ALREADY": "ROLLED_BACK",
                  "NEWER_RELEASE_PRESERVED": "SUPERSEDED"}.get(recovery, "MANAGE_ONLY")
        state = {**state, "status": status, "failure": reason, "recovery": recovery}
        self._save_state(state)
        return state

    def _run_engineer(self) -> bool:
        policy = self.policy
        # Atomic selection makes the generic role/portfolio claim an exact route.
        # It cannot consume another commissioned Engineer task in this scope.
        with self.database.immediate():
            if self.database.execute("SELECT 1 FROM tasks WHERE portfolio_id=? AND role='engineer' "
                                     "AND task_id!=? AND status IN('QUEUED','LEASED','RUNNING','WAITING_EXTERNAL') "
                                     "LIMIT 1",
                                     (policy.portfolio_id, policy.worker_task_id)).fetchone():
                raise AuthorityDenied("separate application scope contains competing Engineer work")
            if not self.scheduler.acquire_process_lease("application-preparation:" + policy.worker_task_id,
                                                        self.owner, ttl_seconds=120):
                return False
            lease = self.scheduler.claim(self.owner, ttl_seconds=120, roles={"engineer"},
                                         portfolio_id=policy.portfolio_id)
            if lease is None:
                return False
            if lease.task_id != policy.worker_task_id:
                raise AuthorityDenied("application task route changed during claim")
        worker = RoleWorker(self.scheduler, owner=self.owner,
                            system_version_id=self.engineer.policy.baseline_artifact_sha256,
                            reconcile=lambda: None)
        worker._run_lease(lease, {"engineer": self.handler})
        return True

    async def advance(self) -> dict:
        """Bounded trusted step; no model-generated scheduling/activation commands."""
        self._offline()
        # Financial reconciliation remains the existing protected service. It
        # runs before application pin/health admission, including after failure.
        await self.execution.reconcile()
        self._assert_pinned()
        if not self.scheduler.acquire_process_lease("application-preparation:" + self.policy.worker_task_id,
                                                    self.owner, ttl_seconds=120):
            return {"status": "LEASED_ELSEWHERE", **FLAGS}
        state = self.state()
        if state["status"] in {"ROLLED_BACK", "SUPERSEDED", "MANAGE_ONLY", "FAILED", "WAITING_EXTERNAL"}:
            return state
        if state["status"] == "ENGINEERING":
            self._run_engineer()
            task = self.database.execute("SELECT * FROM tasks WHERE task_id=?",
                                         (self.policy.worker_task_id,)).fetchone()
            if task["status"] in {"WAITING_EXTERNAL", "FAILED", "BLOCKED_BUDGET", "CANCELLED", "DEAD_LETTER"}:
                state = {**state, "status": "WAITING_EXTERNAL" if task["status"] == "WAITING_EXTERNAL" else "FAILED"}
                self._save_state(state)
                return state
            if task["status"] != "SUCCEEDED":
                return state
            output = json.loads(task["output_json"])
            report = self._candidate(output["candidate_id"], current_authority=True)
            before = self.projection.status()
            if (before["active_build"] != self.engineer.policy.baseline_build_sha256
                    or before["generation"] != 1 or before["status"] != "RUNNING"
                    or not self.projection._has_effect(before["active_build"])):
                raise AuthorityDenied("exact effect-backed projection baseline required")
            state = {**state, "status": "ACTIVATING", "candidate_id": output["candidate_id"],
                     "build_sha256": report["evidence"]["build_sha256"], "generation": 2,
                     "started_at_ns": time.time_ns(),
                     "commission_receipt_sha256": report["commission_receipt_sha256"]}
            self._save_state(state)
        if state["status"] == "ACTIVATING":
            try:
                report = self._candidate(state["candidate_id"], current_authority=True)
                if report["commission_receipt_sha256"] != state["commission_receipt_sha256"]:
                    raise AuthorityDenied("activation commission receipt changed")
            except (TradeGraphError, ValueError, OSError, KeyError, TypeError) as exc:
                current = self.projection.status()
                if (current["active_build"], current["generation"]) == (state["build_sha256"], state["generation"]):
                    return self._rollback(state, "protected_activation_" + type(exc).__name__)
                state = {**state, "status": "FAILED", "failure": "protected_activation_" + type(exc).__name__}
                self._save_state(state)
                return state
            before = self.projection.status()
            baseline = self.engineer.policy.baseline_build_sha256
            if before["active_build"] == baseline and before["generation"] == 1:
                if before["phase"] == "LEGACY":
                    receipt = self.projection.expand()
                    state = {**state, "expansion_receipt_sha256": receipt}
                    self._save_state(state)
                if self.projection.status()["phase"] != "EXPANDED":
                    raise AuthorityDenied("compatible fixed expansion required")
                self.projection.activate(report["evidence"]["validation_receipt_sha256"],
                                         expected_build=baseline, expected_generation=1)
            current = self.projection.status()
            if (current["active_build"], current["generation"], current["status"]) != (
                    state["build_sha256"], state["generation"], "RUNNING"):
                raise StaleState("application activation changed during workflow recovery")
            state = {**state, "status": "HEALTH"}
            self._save_state(state)
        if state["status"] in {"HEALTH", "ACTIVE"}:
            try:
                report = self._candidate(state["candidate_id"], current_authority=state["status"] == "HEALTH")
                if report["commission_receipt_sha256"] != state["commission_receipt_sha256"]:
                    raise AuthorityDenied("health commission receipt changed")
                current = self.projection.status()
                if (current["active_build"], current["generation"], current["status"]) != (
                        state["build_sha256"], state["generation"], "RUNNING"):
                    raise AuthorityDenied("active application generation differs from workflow")
                samples = self._samples()
                for sample in samples:
                    if (sample["build_sha256"], sample["generation"]) != (state["build_sha256"], state["generation"]):
                        raise AuthorityDenied("application health sample generation changed")
                    if sample["status"] != "COMPLETED" or sample.get("matched") is not True:
                        return self._rollback(state, "interrupted_or_rejected_health")
                    effect = self.projection.verify_render(sample["render_receipt_sha256"],
                                                           record_id=sample["record_id"])
                    case = next(case for case in self._health_corpus()["cases"]
                                if sha256(canonical_bytes(case["snapshot"])) == sample["case_sha256"])
                    if (effect["record"]["snapshot_sha256"] != sample["case_sha256"]
                            or effect["record"]["build_sha256"] != state["build_sha256"]
                            or effect["projection"] != case["expected_v2"]
                            or sample["commission_receipt_sha256"] != state["commission_receipt_sha256"]):
                        raise AuthorityDenied("application health effect case/cost changed")
                if state["status"] == "ACTIVE":
                    if len(samples) != len(self.policy.health_case_sha256s):
                        raise AuthorityDenied("active projection lacks complete finite health effects")
                    self.projection.read(version=2)
                    return state
                now = time.time_ns()
                if not state["started_at_ns"] <= now <= (
                        state["started_at_ns"] + self.policy.health_deadline_seconds * 1_000_000_000):
                    return self._rollback(state, "finite_health_deadline_exhausted")
                completed = {sample["case_sha256"] for sample in samples}
                pending = next((digest for digest in self.policy.health_case_sha256s if digest not in completed), None)
                if pending is not None:
                    case = next(case for case in self._health_corpus()["cases"]
                                if sha256(canonical_bytes(case["snapshot"])) == pending)
                    sample = {"status": "STARTED", "case_sha256": pending,
                              "build_sha256": state["build_sha256"], "generation": state["generation"],
                              "commission_receipt_sha256": state["commission_receipt_sha256"]}
                    # Commit the authenticated intent before any candidate effect.
                    self._save_sample(sample)
                    rendered = self.projection.render(case["snapshot"])
                    matched = rendered["status"] == "RENDERED" and rendered["projection"] == case["expected_v2"]
                    if not matched:
                        self._save_sample({**sample, "status": "COMPLETED", "matched": False})
                        return self._rollback(state, "independent_functional_health_rejected")
                    self._save_sample({**sample, "status": "COMPLETED", "matched": True,
                                       "record_id": rendered["record_id"],
                                       "render_receipt_sha256": rendered["receipt_sha256"]})
                    if time.time_ns() > state["started_at_ns"] + self.policy.health_deadline_seconds * 1_000_000_000:
                        return self._rollback(state, "finite_health_deadline_exhausted")
                    if len(completed) + 1 == len(self.policy.health_case_sha256s):
                        state = {**state, "status": "ACTIVE"}
                        self._save_state(state)
                return state
            except (TradeGraphError, ValueError, OSError, KeyError, TypeError) as exc:
                return self._rollback(state, "protected_health_" + type(exc).__name__)
        return state

    def close(self) -> None:
        self.db.close()
