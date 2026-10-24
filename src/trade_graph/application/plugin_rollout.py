"""Independent offline feature rollout; no trading-runtime admission or routing.

Durable version state belongs to the protected SQLite parent. Every transition
and functional sample is authenticated and tied to exact sealed executable bytes.
Rollback switches code only, preserving all finance and paid-work evidence.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta
from decimal import Context, Decimal, localcontext
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trade_graph.adapters.engineering.artifact_files import content_hash
from trade_graph.adapters.engineering.plugin_artifacts import SHA256_PATTERN, canonical_bytes, sha256
from trade_graph.adapters.engineering.plugin_runtime import authenticate, authenticated_report, strict_document
from trade_graph.adapters.engineering.plugin_shadow import _canonical_number, load_corpus
from trade_graph.application import plugin_engineering
from trade_graph.application.change_authority import authorized_change
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState, TradeGraphError

KIND = "offline_pure_feature_plugin"
EXPERIMENT = "offline-plugin-rollout"


def rollout_controller_sha256() -> str:
    return sha256(Path(__file__).read_bytes() + plugin_engineering.plugin_controller_sha256().encode())


class PluginRolloutPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal[1] = 1
    execution_scope: Literal["offline_preparation"] = "offline_preparation"
    fingerprint_kind: Literal["offline_pure_feature_plugin"] = KIND
    experiment_id: Literal["offline-plugin-rollout"] = EXPERIMENT
    portfolio_id: str = Field(min_length=1, max_length=128)
    commission_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    health_corpus_sha256: str = Field(pattern=SHA256_PATTERN)
    minimum_samples: int = Field(default=1, ge=1, le=16)
    maximum_health_seconds: int = Field(default=30, ge=1, le=60)
    observation_deadline_seconds: int = Field(default=300, ge=1, le=3600)
    maximum_commission_history: int = Field(default=32, ge=1, le=64)
    maximum_absolute_error: str = "0.5"

    @field_validator("maximum_absolute_error")
    @classmethod
    def bounded_error(cls, value):
        value = _canonical_number(value)
        if not Decimal("0") <= Decimal(value) <= Decimal("1000000"):
            raise ValueError("bounded nonnegative functional error required")
        return value

    @property
    def sha256(self) -> str:
        return sha256(canonical_bytes(self.model_dump()))


class OfflinePluginRolloutController:
    def __init__(self, engineer, *, policy: PluginRolloutPolicy, expected_policy_sha256: str,
                 expected_controller_sha256: str, receipt_key: bytes) -> None:
        if type(receipt_key) is not bytes or len(receipt_key) < 32:
            raise ValueError("independent private rollout receipt key required")
        self.engineer, self.database, self.clock, self.store = (
            engineer, engineer.database, engineer.clock, engineer.store,
        )
        self._policy_bytes = canonical_bytes(policy.model_dump())
        self.policy_pin, self.controller_pin = expected_policy_sha256, expected_controller_sha256
        self._key = receipt_key
        self._assert_pinned()

    @property
    def policy(self) -> PluginRolloutPolicy:
        return PluginRolloutPolicy.model_validate(strict_document(self._policy_bytes, maximum_bytes=8192))

    def _assert_pinned(self) -> None:
        if (sha256(self._policy_bytes) != self.policy_pin or rollout_controller_sha256() != self.controller_pin
                or self.policy.commission_policy_sha256 != self.engineer.policy_pin):
            raise AuthorityDenied("independent offline rollout policy/controller mismatch")
        self.engineer._assert_pinned()
        corpus = load_corpus(self.store, self.policy.health_corpus_sha256)
        commission = self.engineer.policy
        if (corpus.baseline_release_id != commission.baseline_release_id
                or corpus.baseline_runtime_build_sha256 != commission.baseline_runtime_build_sha256
                or corpus.baseline_source_sha256 != commission.baseline_source_sha256
                or corpus.protected_manifest_sha256 != commission.protected_manifest_sha256
                or len(corpus.cases) < self.policy.minimum_samples
                or len(corpus.cases) * self.engineer.builder.replay_validator.policy.boundary.wall_seconds
                > self.policy.maximum_health_seconds):
            raise AuthorityDenied("independent functional health corpus/envelope mismatch")

    def _offline(self) -> None:
        row = self.database.execute("SELECT * FROM portfolios WHERE portfolio_id = ?",
                                    (self.policy.portfolio_id,)).fetchone()
        if row is None or row["mode"] != "paper" or row["experiment_id"] != EXPERIMENT:
            raise AuthorityDenied("explicit isolated offline-plugin-rollout paper experiment required")
        if self.database.execute("SELECT 1 FROM model_invocations WHERE portfolio_id = ? AND "
                                 "json_extract(request_json, '$.provider') IS NOT 'scripted' LIMIT 1",
                                 (self.policy.portfolio_id,)).fetchone():
            raise AuthorityDenied("offline rollout cannot use real provider evidence")
        if self.database.execute("SELECT 1 FROM usage_receipts r WHERE r.synthetic != 1 AND ("
                                 "EXISTS (SELECT 1 FROM model_invocations i WHERE "
                                 "i.reservation_id = r.reservation_id AND i.portfolio_id = ?) OR "
                                 "EXISTS (SELECT 1 FROM budget_reservations b JOIN tasks t ON t.task_id = b.task_id "
                                 "WHERE b.reservation_id = r.reservation_id AND t.portfolio_id = ?) OR "
                                 "EXISTS (SELECT 1 FROM cost_allocations c WHERE c.receipt_id = r.receipt_id "
                                 "AND c.portfolio_id = ?)) LIMIT 1",
                                 (self.policy.portfolio_id,) * 3).fetchone():
            raise AuthorityDenied("offline rollout cannot use real usage receipts")
        if self.database.execute("SELECT 1 FROM budget_reservations b JOIN tasks t ON t.task_id = b.task_id "
                                 "WHERE t.portfolio_id = ? AND b.synthetic != 1 LIMIT 1",
                                 (self.policy.portfolio_id,)).fetchone():
            raise AuthorityDenied("offline rollout cannot use real provider reservations")
        if self.database.execute("SELECT 1 FROM order_intents WHERE portfolio_id = ? LIMIT 1",
                                 (self.policy.portfolio_id,)).fetchone():
            raise AuthorityDenied("offline feature experiment cannot route order effects")
        if self.database.execute("SELECT 1 FROM fills WHERE portfolio_id = ? AND "
                                 "(venue != 'paper' OR json_extract(document_json, '$.synthetic') IS NOT 1) LIMIT 1",
                                 (self.policy.portfolio_id,)).fetchone():
            raise AuthorityDenied("offline feature experiment cannot use real venue effects")

    def _quiescent(self) -> None:
        if self.database.execute("SELECT 1 FROM tasks WHERE portfolio_id = ? AND status IN ('LEASED', 'RUNNING') "
                                 "LIMIT 1", (self.policy.portfolio_id,)).fetchone():
            raise StaleState("offline plugin decision boundary is not quiescent")

    def _receipt(self, report: dict) -> str:
        return self.store.retain_receipt(authenticate(report, self._key))

    def _release(self, digest: str, *, verify_runtime: bool = True) -> dict:
        report = authenticated_report(self.store, digest, self._key)
        expected = {"kind": "offline_plugin_release", "portfolio_id": self.policy.portfolio_id,
                    "controller_sha256": self.controller_pin, "rollout_policy_sha256": self.policy_pin,
                    "commission_policy_sha256": self.engineer.policy_pin,
                    "production_authorization": False, "live_authorization": False,
                    "economic_evidence": "not_evaluated", "state_schema": "stateless/v1"}
        if any(report.get(name) != value for name, value in expected.items()):
            raise AuthorityDenied("offline plugin release receipt/policy substitution")
        if not verify_runtime:
            return report
        manifest, source = self.engineer.builder.load(report["runtime_build_sha256"])
        if (manifest.source_sha256 != report["source_sha256"]
                or content_hash({plugin_engineering.SOURCE_PATH: source}) != report["artifact_hash"]):
            raise AuthorityDenied("offline plugin release exact source/artifact mismatch")
        if report["candidate_id"] is None:
            commission = self.engineer.policy
            if (report["version_id"] != commission.baseline_release_id
                    or report["runtime_build_sha256"] != commission.baseline_runtime_build_sha256
                    or report["source_sha256"] != commission.baseline_source_sha256):
                raise AuthorityDenied("offline plugin baseline is not independently pinned")
        else:
            candidate = self.engineer.verify_candidate(report["candidate_id"])
            if (candidate["status"] != "READY" or candidate["commission_receipt_sha256"]
                    != report["commission_receipt_sha256"] or candidate["evidence"]["runtime_build_sha256"]
                    != report["runtime_build_sha256"] or report["version_id"] != report["candidate_id"]):
                raise AuthorityDenied("offline release lacks exact independently commissioned candidate")
        return report

    def _fingerprint(self, release: dict, receipt: str) -> dict:
        return {"kind": KIND, "artifact": release["artifact_hash"],
                "runtime_build_sha256": release["runtime_build_sha256"], "source_sha256": release["source_sha256"],
                "controller_sha256": self.controller_pin, "rollout_policy_sha256": self.policy_pin,
                "release_receipt_sha256": receipt}

    def _active(self, *, verify_runtime: bool = True) -> tuple[dict, dict]:
        self._assert_pinned()
        self._offline()
        row = self.database.execute("SELECT * FROM active_versions WHERE portfolio_id = ?",
                                    (self.policy.portfolio_id,)).fetchone()
        if row is None:
            raise AuthorityDenied("offline plugin baseline is not initialized")
        active = dict(row)
        fingerprint = strict_document(active["fingerprint_json"].encode(), maximum_bytes=8192)
        if fingerprint.get("kind") != KIND:
            raise AuthorityDenied("R1/trading active version cannot become an offline plugin")
        release = self._release(fingerprint["release_receipt_sha256"], verify_runtime=verify_runtime)
        if (fingerprint != self._fingerprint(release, fingerprint["release_receipt_sha256"])
                or active["artifact_hash"] != release["artifact_hash"]
                or active["version_id"] != release["version_id"]):
            raise AuthorityDenied("offline active pointer/release identity mismatch")
        event = self.database.execute("SELECT details_json FROM version_events WHERE portfolio_id = ? "
                                      "ORDER BY rowid DESC LIMIT 1", (self.policy.portfolio_id,)).fetchone()
        if event is None:
            raise AuthorityDenied("offline active pointer has no independent durable transition")
        transition = authenticated_report(self.store, json.loads(event[0])["receipt_sha256"], self._key)
        if (transition["kind"] != "offline_plugin_transition" or transition["generation"] != active["generation"]
                or transition["new_fingerprint"] != fingerprint or transition["new_version_id"] != active["version_id"]
                or transition["portfolio_id"] != self.policy.portfolio_id
                or transition["controller_sha256"] != self.controller_pin
                or transition["rollout_policy_sha256"] != self.policy_pin):
            raise AuthorityDenied("offline active pointer durable generation/transition mismatch")
        return active, release

    def _transition(self, old: dict | None, fingerprint: dict, version_id: str, generation: int, action: str) -> None:
        report = {"kind": "offline_plugin_transition", "portfolio_id": self.policy.portfolio_id,
                  "controller_sha256": self.controller_pin, "rollout_policy_sha256": self.policy_pin,
                  "old_generation": old["generation"] if old else None, "generation": generation,
                  "old_fingerprint": json.loads(old["fingerprint_json"]) if old else None,
                  "new_fingerprint": fingerprint, "new_version_id": version_id, "action": action,
                  "production_authorization": False, "live_authorization": False, "economic_evidence": "not_evaluated"}
        receipt = self._receipt(report)
        self.database.execute("INSERT INTO version_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                              (uuid.uuid4().hex, self.policy.portfolio_id, "offline_plugin_" + action,
                               old["artifact_hash"] if old else fingerprint["artifact"], fingerprint["artifact"],
                               utc_iso(self.clock.now()), json.dumps({"receipt_sha256": receipt})))

    def initialize_baseline(self) -> None:
        """Fresh explicitly offline portfolio only; never convert an ordinary pointer."""
        self._assert_pinned()
        self._offline()
        with self.database.immediate():
            self._quiescent()
            if self.database.execute("SELECT 1 FROM active_versions WHERE portfolio_id = ?",
                                     (self.policy.portfolio_id,)).fetchone():
                self._active()
                return
            policy = self.engineer.policy
            report = {"kind": "offline_plugin_release", "portfolio_id": self.policy.portfolio_id,
                      "controller_sha256": self.controller_pin, "rollout_policy_sha256": self.policy_pin,
                      "commission_policy_sha256": self.engineer.policy_pin, "version_id": policy.baseline_release_id,
                      "artifact_hash": policy.baseline_artifact_sha256,
                      "runtime_build_sha256": policy.baseline_runtime_build_sha256,
                      "source_sha256": policy.baseline_source_sha256, "candidate_id": None,
                      "commission_receipt_sha256": None, "state_schema": "stateless/v1",
                      "production_authorization": False, "live_authorization": False,
                      "economic_evidence": "not_evaluated"}
            receipt = self._receipt(report)
            self._release(receipt)
            fingerprint = self._fingerprint(report, receipt)
            self.database.execute("INSERT INTO active_versions (portfolio_id, version_id, artifact_hash, "
                                  "fingerprint_json, activated_at, generation) VALUES (?, ?, ?, ?, ?, 0)",
                                  (self.policy.portfolio_id, report["version_id"], report["artifact_hash"],
                                   json.dumps(fingerprint, sort_keys=True), utc_iso(self.clock.now())))
            self.database.execute("INSERT INTO version_history VALUES (?, ?, ?)",
                                  (self.policy.portfolio_id, report["version_id"], report["artifact_hash"]))
            self._transition(None, fingerprint, report["version_id"], 0, "initialize")

    def activate(self, candidate_id: str, *, expected_generation: int) -> dict:
        with self.database.immediate():
            active, _ = self._active()
            self._quiescent()
            if active["generation"] != expected_generation:
                raise StaleState("offline activation generation changed")
            candidate = self.engineer.verify_candidate(candidate_id)
            task, change, _ = authorized_change(self.database, self.clock, self.policy.portfolio_id,
                                                candidate["change_id"])
            row = self.database.execute("SELECT state FROM candidates WHERE candidate_id = ?",
                                        (candidate_id,)).fetchone()
            if (candidate["status"] != "READY" or row[0] != "READY" or change["state"] != "READY"
                    or task.baseline_hash != active["artifact_hash"]
                    or active["version_id"] != self.engineer.policy.baseline_release_id):
                raise AuthorityDenied("current exact commissioned offline baseline/READY candidate required")
            manifest, source = self.engineer.builder.load(candidate["evidence"]["runtime_build_sha256"])
            report = {"kind": "offline_plugin_release", "portfolio_id": self.policy.portfolio_id,
                      "controller_sha256": self.controller_pin, "rollout_policy_sha256": self.policy_pin,
                      "commission_policy_sha256": self.engineer.policy_pin, "version_id": candidate_id,
                      "artifact_hash": content_hash({plugin_engineering.SOURCE_PATH: source}),
                      "runtime_build_sha256": manifest.runtime_build_sha256, "source_sha256": manifest.source_sha256,
                      "candidate_id": candidate_id, "commission_receipt_sha256": candidate["commission_receipt_sha256"],
                      "state_schema": "stateless/v1", "production_authorization": False,
                      "live_authorization": False, "economic_evidence": "not_evaluated"}
            receipt = self._receipt(report)
            self._release(receipt)
            fingerprint, generation = self._fingerprint(report, receipt), active["generation"] + 1
            changed = self.database.execute("UPDATE active_versions SET version_id = ?, artifact_hash = ?, "
                                            "fingerprint_json = ?, generation = ?, activated_at = ? WHERE "
                                            "portfolio_id = ? AND generation = ? AND artifact_hash = ?",
                                            (candidate_id, report["artifact_hash"],
                                             json.dumps(fingerprint, sort_keys=True),
                                             generation, utc_iso(self.clock.now()), self.policy.portfolio_id,
                                             expected_generation, active["artifact_hash"]))
            if changed.rowcount != 1:
                raise StaleState("offline activation CAS failed")
            self.database.execute("INSERT INTO version_history VALUES (?, ?, ?)",
                                  (self.policy.portfolio_id, candidate_id, report["artifact_hash"]))
            pinned = {"policy": self.policy.model_dump(), "policy_sha256": self.policy_pin,
                      "controller_sha256": self.controller_pin,
                      "previous_fingerprint": json.loads(active["fingerprint_json"])}
            self.database.execute("INSERT INTO version_rollouts VALUES (?, ?, ?, ?, ?, ?, ?, 'OBSERVING', ?, ?)",
                                  (uuid.uuid4().hex, self.policy.portfolio_id, candidate_id, generation,
                                   report["artifact_hash"], active["artifact_hash"], active["version_id"],
                                   json.dumps(pinned, sort_keys=True), utc_iso(self.clock.now())))
            self.database.execute("UPDATE candidates SET state = 'OBSERVING' WHERE candidate_id = ?", (candidate_id,))
            self.database.execute("UPDATE change_tasks SET state = 'OBSERVING' WHERE change_id = ?",
                                  (candidate["change_id"],))
            self._transition(active, fingerprint, candidate_id, generation, "activate")
            return {"generation": generation, "artifact_hash": report["artifact_hash"], "state": "OBSERVING"}

    def _rollout(self, generation: int):
        return self.database.execute("SELECT * FROM version_rollouts WHERE portfolio_id = ? AND generation = ?",
                                     (self.policy.portfolio_id, generation)).fetchone()

    def _samples(self, rollout, release: dict) -> list[dict]:
        """Validate retained controller evidence before counting any health result."""
        corpus = load_corpus(self.store, self.policy.health_corpus_sha256)
        cases = {case.case_id: case for case in corpus.cases}
        if self.database.execute("SELECT 1 FROM version_observations WHERE rollout_id = ? AND "
                                 "length(CAST(document_json AS BLOB)) > 256 LIMIT 1",
                                 (rollout["rollout_id"],)).fetchone():
            raise AuthorityDenied("offline health sample pointer byte bound exceeded")
        rows = self.database.execute("SELECT observation_id, document_json FROM version_observations "
                                     "WHERE rollout_id = ? LIMIT ?", (rollout["rollout_id"], len(cases) + 1)).fetchall()
        if len(rows) > len(cases):
            raise AuthorityDenied("offline health sample count exceeds independent corpus")
        reports = []
        for row in rows:
            report = authenticated_report(self.store, json.loads(row["document_json"])["receipt_sha256"], self._key)
            case = cases.get(row["observation_id"])
            expected = {"kind": "offline_plugin_health", "portfolio_id": self.policy.portfolio_id,
                        "generation": rollout["generation"], "runtime_build_sha256": release["runtime_build_sha256"],
                        "health_corpus_sha256": self.policy.health_corpus_sha256,
                        "controller_sha256": self.controller_pin, "rollout_policy_sha256": self.policy_pin,
                        "case_id": row["observation_id"], "production_authorization": False,
                        "live_authorization": False, "economic_evidence": "not_evaluated"}
            if case is None or any(report.get(name) != value for name, value in expected.items()):
                raise AuthorityDenied("offline health sample generation/corpus/source substitution")
            inputs = {name: point.value for name, point in case.evidence.items()}
            if report["snapshot_sha256"] != sha256(canonical_bytes(inputs)):
                raise AuthorityDenied("offline health sample exact input changed")
            if report["phase"] not in {"STARTED", "COMPLETED"}:
                raise AuthorityDenied("invalid offline health sample phase")
            if report["phase"] == "STARTED" and (report["outcome"] is not None or report["healthy"] is not None):
                raise AuthorityDenied("interrupted offline health cannot claim an outcome")
            if report["phase"] == "COMPLETED":
                started = authenticated_report(self.store, report["started_receipt_sha256"], self._key)
                if {**started, "phase": "COMPLETED", "outcome": report["outcome"],
                    "healthy": report["healthy"], "started_receipt_sha256": report["started_receipt_sha256"]} != report:
                    raise AuthorityDenied("offline health result lacks exact durable pre-execution intent")
                outcome = report["outcome"]
                healthy = self._healthy(outcome, case)
                if report["healthy"] is not healthy:
                    raise AuthorityDenied("offline health sample metrics disagree with actual output")
            reports.append(report)
        return reports

    def _healthy(self, outcome: dict, case) -> bool:
        if not self._numeric_outcome(outcome):
            return False
        with localcontext(Context(prec=100)):
            return (set(outcome["features"]) == set(case.expected_features)
                    and all(abs(Decimal(value) - Decimal(case.expected_features[name]))
                            <= Decimal(self.policy.maximum_absolute_error)
                            for name, value in outcome["features"].items()))

    def _numeric_outcome(self, outcome: dict) -> bool:
        boundary = self.engineer.builder.replay_validator.policy.boundary
        if (outcome.get("status") != "validated_numeric_proposal" or type(outcome.get("features")) is not dict
                or set(outcome["features"]) != set(boundary.feature_names)):
            return False
        try:
            return all(_canonical_number(value) == value
                       and Decimal(value).copy_abs() <= boundary.maximum_feature_magnitude
                       for value in outcome["features"].values())
        except (ValueError, TypeError):
            return False

    def evaluate(self, observations: dict[str, str]) -> dict:
        """Return actual feature output; never decision/order/financial instructions."""
        self.maintain()
        active, release = self._active()
        try:
            outcome = self.engineer.builder.evaluate(release["runtime_build_sha256"], observations)
        except (ValueError, OSError) as exc:
            outcome = {"status": "controller_rejected", "features": None, "diagnostic": type(exc).__name__}
        with self.database.immediate():
            current, _ = self._active()
            if current["generation"] != active["generation"]:
                raise StaleState("obsolete offline feature result cannot publish")
            if not self._numeric_outcome(outcome):
                if outcome["status"] == "validated_numeric_proposal":
                    outcome = {"status": "controller_rejected", "features": None,
                               "diagnostic": "feature_contract_mismatch"}
                rollout = self._rollout(active["generation"])
                if rollout:
                    self.database.execute("UPDATE version_rollouts SET state = 'ROLLBACK_PENDING' WHERE rollout_id = ?",
                                          (rollout["rollout_id"],))
        if outcome["status"] != "validated_numeric_proposal":
            self.maintain()
        return {**outcome, "generation": active["generation"], "runtime_build_sha256": release["runtime_build_sha256"],
                "production_authorization": False, "live_authorization": False}

    def advance(self) -> dict:
        """Run the pinned offline lifecycle automatically after commissioned work."""
        state = self.maintain()
        active, release = self._active()
        if release["candidate_id"] is None:
            history = self.database.execute(
                "SELECT c.candidate_id, c.state, c.baseline_hash FROM candidates c JOIN change_tasks t "
                "ON t.change_id = c.change_id WHERE t.portfolio_id = ? ORDER BY c.rowid LIMIT ?",
                (self.policy.portfolio_id, self.policy.maximum_commission_history + 1),
            ).fetchall()
            if len(history) > self.policy.maximum_commission_history:
                raise AuthorityDenied("offline commissioned candidate history quota exceeded")
            candidate = next((row for row in history if row["state"] == "READY"
                              and row["baseline_hash"] == active["artifact_hash"]), None)
            if candidate is not None:
                self.activate(candidate[0], expected_generation=active["generation"])
                state = "OBSERVING"
        if state == "OBSERVING":
            state = self.health()
        return {"state": state, "generation": self._active()[0]["generation"],
                "production_authorization": False, "live_authorization": False, "economic_evidence": "not_evaluated"}

    def health(self) -> str:
        """Trusted functional sampling from a retained independent corpus, no model."""
        self.maintain()
        active, release = self._active()
        rollout = self._rollout(active["generation"])
        if rollout is None or rollout["state"] in {"ACTIVE", "RESTORED"}:
            return "BASELINE" if rollout is None else rollout["state"]
        if rollout["state"] != "OBSERVING":
            raise AuthorityDenied("offline rollout cannot accept health work")
        corpus = load_corpus(self.store, self.policy.health_corpus_sha256)
        completed = {report["case_id"] for report in self._samples(rollout, release)
                     if report["phase"] == "COMPLETED"}
        started_ns = time.monotonic_ns()
        for case in corpus.cases:
            if case.case_id in completed:
                continue
            inputs = {name: point.value for name, point in case.evidence.items()}
            report = {"kind": "offline_plugin_health", "portfolio_id": self.policy.portfolio_id,
                      "generation": active["generation"], "runtime_build_sha256": release["runtime_build_sha256"],
                      "health_corpus_sha256": self.policy.health_corpus_sha256, "case_id": case.case_id,
                      "controller_sha256": self.controller_pin, "rollout_policy_sha256": self.policy_pin,
                      "snapshot_sha256": sha256(canonical_bytes(inputs)), "attempt_id": uuid.uuid4().hex,
                      "phase": "STARTED", "outcome": None, "healthy": None,
                      "production_authorization": False, "live_authorization": False,
                      "economic_evidence": "not_evaluated"}
            receipt = self._receipt(report)
            with self.database.immediate():
                current, _ = self._active(verify_runtime=False)
                if current["generation"] != active["generation"]:
                    raise StaleState("obsolete offline health cannot change a newer generation")
                self.database.execute("INSERT INTO version_observations VALUES (?, ?, ?, ?)",
                                      (rollout["rollout_id"], case.case_id,
                                       json.dumps({"receipt_sha256": receipt}), utc_iso(self.clock.now())))
            remaining = self.policy.maximum_health_seconds * 1_000_000_000 - (time.monotonic_ns() - started_ns)
            boundary = self.engineer.builder.replay_validator.policy.boundary
            if remaining < int(boundary.wall_seconds * 1_000_000_000):
                outcome = {"status": "not_run_parent_deadline", "features": None}
            else:
                try:
                    outcome = self.engineer.builder.evaluate(release["runtime_build_sha256"], inputs)
                except (ValueError, OSError) as exc:
                    outcome = {"status": "controller_rejected", "features": None, "diagnostic": type(exc).__name__}
            healthy = self._healthy(outcome, case)
            finished = self._receipt({**report, "phase": "COMPLETED", "outcome": outcome, "healthy": healthy,
                                      "started_receipt_sha256": receipt})
            with self.database.immediate():
                current, _ = self._active(verify_runtime=False)
                if current["generation"] != active["generation"]:
                    raise StaleState("obsolete offline health cannot publish")
                changed = self.database.execute("UPDATE version_observations SET document_json = ? WHERE "
                                                "rollout_id = ? AND observation_id = ? AND document_json = ?",
                                                (json.dumps({"receipt_sha256": finished}), rollout["rollout_id"],
                                                 case.case_id, json.dumps({"receipt_sha256": receipt})))
                if changed.rowcount != 1:
                    raise StaleState("offline health attempt was replaced")
                if not healthy:
                    self.database.execute("UPDATE version_rollouts SET state = 'ROLLBACK_PENDING' WHERE rollout_id = ?",
                                          (rollout["rollout_id"],))
                    break
        with self.database.immediate():
            current, _ = self._active(verify_runtime=False)
            if current["generation"] != active["generation"]:
                raise StaleState("obsolete offline health cannot publish")
            healthy_samples = sum(report["healthy"] is True for report in self._samples(rollout, release))
            latest = self._rollout(active["generation"])
            age = self.clock.now() - datetime.fromisoformat(rollout["activated_at"].replace("Z", "+00:00"))
            if age >= timedelta(seconds=self.policy.observation_deadline_seconds):
                self.database.execute("UPDATE version_rollouts SET state = 'ROLLBACK_PENDING' WHERE rollout_id = ?",
                                      (rollout["rollout_id"],))
                latest = self._rollout(active["generation"])
            if latest["state"] == "OBSERVING" and healthy_samples >= self.policy.minimum_samples:
                self.database.execute("UPDATE version_rollouts SET state = 'ACTIVE' WHERE rollout_id = ?",
                                      (rollout["rollout_id"],))
                self.database.execute("UPDATE candidates SET state = 'ACTIVE' WHERE candidate_id = ?",
                                      (release["candidate_id"],))
                self.database.execute("UPDATE change_tasks SET state = 'ACTIVE' WHERE change_id = "
                                      "(SELECT change_id FROM candidates WHERE candidate_id = ?)",
                                      (release["candidate_id"],))
        return self.maintain()

    def maintain(self) -> str:
        """Restart-safe deterministic recovery, independent of candidate/model health."""
        with self.database.immediate():
            active, release = self._active(verify_runtime=False)
            rollout = self._rollout(active["generation"])
            if rollout is None:
                return "BASELINE"
            pinned = strict_document(rollout["policy_json"].encode(), maximum_bytes=16384)
            if (pinned["policy"] != self.policy.model_dump() or pinned["policy_sha256"] != self.policy_pin
                    or pinned["controller_sha256"] != self.controller_pin):
                raise AuthorityDenied("offline rollout persisted health policy changed")
            if rollout["state"] not in {"OBSERVING", "ACTIVE", "ROLLBACK_PENDING", "BLOCKED"}:
                raise AuthorityDenied("offline rollout persisted lifecycle changed")
            samples = self._samples(rollout, release)
            if (any(report["phase"] == "STARTED" or report["healthy"] is False for report in samples)
                    or (rollout["state"] == "ACTIVE"
                        and sum(report["healthy"] is True for report in samples) < self.policy.minimum_samples)):
                self.database.execute("UPDATE version_rollouts SET state = 'ROLLBACK_PENDING' WHERE rollout_id = ?",
                                      (rollout["rollout_id"],))
                rollout = self._rollout(active["generation"])
            try:
                self._release(json.loads(active["fingerprint_json"])["release_receipt_sha256"])
            except (TradeGraphError, ValueError, OSError):
                self.database.execute("UPDATE version_rollouts SET state = 'ROLLBACK_PENDING' WHERE rollout_id = ?",
                                      (rollout["rollout_id"],))
                rollout = self._rollout(active["generation"])
            age = self.clock.now() - datetime.fromisoformat(rollout["activated_at"].replace("Z", "+00:00"))
            if rollout["state"] == "OBSERVING" and age >= timedelta(seconds=self.policy.observation_deadline_seconds):
                self.database.execute("UPDATE version_rollouts SET state = 'ROLLBACK_PENDING' WHERE rollout_id = ?",
                                      (rollout["rollout_id"],))
                rollout = self._rollout(active["generation"])
            if rollout["state"] not in {"ROLLBACK_PENDING", "BLOCKED"}:
                return rollout["state"]
            self._quiescent()
            previous_fingerprint = pinned["previous_fingerprint"]
            transition_row = self.database.execute(
                "SELECT details_json FROM version_events WHERE portfolio_id = ? ORDER BY rowid DESC LIMIT 1",
                (self.policy.portfolio_id,),
            ).fetchone()
            transition = authenticated_report(self.store, json.loads(transition_row[0])["receipt_sha256"], self._key)
            if transition["old_fingerprint"] != previous_fingerprint:
                raise AuthorityDenied("offline rollback predecessor differs from authenticated activation")
            previous = self._release(previous_fingerprint["release_receipt_sha256"])
            if (previous_fingerprint != self._fingerprint(previous, previous_fingerprint["release_receipt_sha256"])
                    or previous["artifact_hash"] != rollout["previous_hash"]
                    or previous["version_id"] != rollout["previous_version_id"]):
                raise AuthorityDenied("offline rollback predecessor identity changed")
            known = self.database.execute("SELECT 1 FROM version_history WHERE portfolio_id = ? AND version_id = ? "
                                          "AND artifact_hash = ?", (self.policy.portfolio_id, previous["version_id"],
                                                                    previous["artifact_hash"])).fetchone()
            if known is None:
                raise AuthorityDenied("offline rollback predecessor lacks durable history")
            changed = self.database.execute("UPDATE active_versions SET version_id = ?, artifact_hash = ?, "
                                            "fingerprint_json = ?, generation = generation + 1, activated_at = ? "
                                            "WHERE portfolio_id = ? AND generation = ? AND artifact_hash = ?",
                                            (previous["version_id"], previous["artifact_hash"],
                                             json.dumps(previous_fingerprint, sort_keys=True),
                                             utc_iso(self.clock.now()),
                                             self.policy.portfolio_id, active["generation"], active["artifact_hash"]))
            if changed.rowcount != 1:
                raise StaleState("offline rollback CAS failed")
            self.database.execute("UPDATE version_rollouts SET state = 'ROLLED_BACK' WHERE rollout_id = ?",
                                  (rollout["rollout_id"],))
            self.database.execute("UPDATE candidates SET state = 'ROLLED_BACK' WHERE candidate_id = ?",
                                  (active["version_id"],))
            self.database.execute("UPDATE change_tasks SET state = 'ROLLED_BACK' WHERE change_id = "
                                  "(SELECT change_id FROM candidates WHERE candidate_id = ?)", (active["version_id"],))
            self._transition(active, previous_fingerprint, previous["version_id"], active["generation"] + 1, "rollback")
            return "ROLLED_BACK"
