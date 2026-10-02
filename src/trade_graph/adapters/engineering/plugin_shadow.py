"""Independent shadow comparisons against actual sealed baseline executions.

Only retained numeric cases enter children. Outcome expectations, comparison
rules, baseline identity, corpus bytes and receipt keys stay owner-pinned outside
candidate input. This functional shadow evidence is not T18 economics or rollout.
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal, localcontext
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trade_graph.adapters.engineering.plugin_artifacts import (
    SHA256_PATTERN,
    PluginStageStore,
    canonical_bytes,
    require_digest,
    sha256,
)
from trade_graph.adapters.engineering.plugin_runtime import (
    PluginRuntimeBuilder,
    authenticate,
    authenticated_report,
    runtime_builder_sha256,
    strict_document,
)
from trade_graph.domain.money import canonical_decimal


def _canonical_number(value: str) -> str:
    if type(value) is not str or not 1 <= len(value) <= 128:
        raise ValueError("bounded canonical numeric evidence required")
    try:
        amount = Decimal(value)
    except (ArithmeticError, ValueError) as exc:
        raise ValueError("bounded canonical numeric evidence required") from exc
    if (not amount.is_finite() or len(amount.as_tuple().digits) > 36
            or not -18 <= amount.as_tuple().exponent <= 36 or amount.copy_abs() > Decimal("1e36")
            or canonical_decimal(amount) != value):
        raise ValueError("bounded canonical numeric evidence required")
    return value


class NumericEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    value: str = Field(min_length=1, max_length=128)
    event_at_utc: datetime
    available_at_utc: datetime
    source_ref: str = Field(min_length=1, max_length=128)
    source_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("value")
    @classmethod
    def _finite_numeric(cls, value):
        return _canonical_number(value)

    @field_validator("event_at_utc", "available_at_utc")
    @classmethod
    def _utc(cls, value):
        if value.utcoffset() != timedelta(0):
            raise ValueError("explicit UTC evidence clocks required")
        return value

    @model_validator(mode="after")
    def _ordered(self):
        if self.event_at_utc > self.available_at_utc:
            raise ValueError("event time cannot exceed source availability")
        return self


class ShadowCase(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    case_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.:-]+$")
    decision_at_utc: datetime
    evidence: dict[str, NumericEvidence]
    expected_features: dict[str, str]

    @field_validator("expected_features")
    @classmethod
    def _bounded_expected(cls, value):
        if not 1 <= len(value) <= 32:
            raise ValueError("bounded expected feature mapping required")
        for name, number in value.items():
            if not name.isidentifier() or len(name) > 64:
                raise ValueError("bounded expected feature name required")
            _canonical_number(number)
        return value

    @field_validator("decision_at_utc")
    @classmethod
    def _utc(cls, value):
        if value.utcoffset() != timedelta(0):
            raise ValueError("explicit UTC decision clock required")
        return value

    @model_validator(mode="after")
    def _point_in_time(self):
        if not 1 <= len(self.evidence) <= 32 or len(self.expected_features) > 32:
            raise ValueError("bounded evidence/features required")
        if any(item.available_at_utc > self.decision_at_utc for item in self.evidence.values()):
            raise ValueError("future source availability at decision time")
        return self


class ShadowCorpus(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: int = Field(default=1, ge=1, le=1)
    corpus_id: str = Field(min_length=1, max_length=128)
    baseline_release_id: str = Field(min_length=1, max_length=128)
    baseline_runtime_build_sha256: str = Field(pattern=SHA256_PATTERN)
    baseline_source_sha256: str = Field(pattern=SHA256_PATTERN)
    protected_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    cases: tuple[ShadowCase, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def _chronological(self):
        if (len({case.case_id for case in self.cases}) != len(self.cases)
                or any(left.decision_at_utc >= right.decision_at_utc
                       for left, right in zip(self.cases, self.cases[1:]))):
            raise ValueError("unique strictly chronological shadow cases required")
        return self


def retain_corpus(store: PluginStageStore, corpus: ShadowCorpus) -> str:
    # Revalidation catches nested mappings mutated after frozen model creation.
    data = canonical_bytes(corpus.model_dump(mode="json"))
    document = strict_document(data)
    ShadowCorpus.model_validate_json(canonical_bytes(document))
    digest = sha256(data)
    store._publish("corpora", digest, {"corpus.json": data})
    return digest


def load_corpus(store: PluginStageStore, digest: str) -> ShadowCorpus:
    require_digest(digest)
    fd = store._object("corpora", digest)
    try:
        store._private_directory(fd, sealed=True)
        if set(os.listdir(fd)) != {"corpus.json"}:
            raise PermissionError("shadow corpus has unexpected files")
        data = store._read_file(fd, "corpus.json", 131072)
        document = strict_document(data)
        corpus = ShadowCorpus.model_validate_json(canonical_bytes(document))
        if sha256(data) != digest or canonical_bytes(corpus.model_dump(mode="json")) != data:
            raise PermissionError("shadow corpus exact retained byte identity mismatch")
        return corpus
    finally:
        os.close(fd)


class ShadowPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: int = Field(default=1, ge=1, le=1)
    corpus_sha256: str = Field(pattern=SHA256_PATTERN)
    baseline_release_id: str = Field(min_length=1, max_length=128)
    baseline_runtime_build_sha256: str = Field(pattern=SHA256_PATTERN)
    baseline_source_sha256: str = Field(pattern=SHA256_PATTERN)
    protected_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    state_schema: str = Field(default="stateless/v1", min_length=1, max_length=128)
    repetitions: int = Field(default=2, ge=2, le=3)
    maximum_cases: int = Field(default=4, ge=1, le=8)
    maximum_seconds: int = Field(default=60, ge=1, le=60)
    maximum_mean_absolute_error: str = "0"
    maximum_absolute_error: str = "0"
    allowed_mean_error_regression: str = "0"

    @field_validator("maximum_mean_absolute_error", "maximum_absolute_error", "allowed_mean_error_regression")
    @classmethod
    def _bounded_nonnegative(cls, value):
        if len(value) > 128:
            raise ValueError("bounded canonical error tolerance required")
        try:
            amount = Decimal(value)
        except (ArithmeticError, ValueError) as exc:
            raise ValueError("bounded canonical error tolerance required") from exc
        if (not amount.is_finite() or amount < 0 or len(amount.as_tuple().digits) > 36
                or not -18 <= amount.as_tuple().exponent <= 18 or canonical_decimal(amount) != value):
            raise ValueError("bounded canonical error tolerance required")
        return value

    @property
    def sha256(self) -> str:
        return sha256(canonical_bytes(self.model_dump()))


def shadow_runner_sha256() -> str:
    digest = hashlib.sha256()
    digest.update(Path(__file__).read_bytes())
    digest.update(runtime_builder_sha256().encode())
    return digest.hexdigest()


class PluginShadowRunner:
    def __init__(self, builder: PluginRuntimeBuilder, *, policy: ShadowPolicy, expected_policy_sha256: str,
                 expected_runner_sha256: str, receipt_key: bytes) -> None:
        require_digest(expected_policy_sha256)
        require_digest(expected_runner_sha256)
        if type(receipt_key) is not bytes or len(receipt_key) < 32:
            raise ValueError("owner-private shadow receipt key required")
        self.builder, self.store = builder, builder.store
        self._policy_bytes = canonical_bytes(policy.model_dump())
        self.policy_pin, self.runner_pin = expected_policy_sha256, expected_runner_sha256
        self._key = receipt_key
        self._assert_pinned()

    def _assert_pinned(self) -> None:
        if sha256(self._policy_bytes) != self.policy_pin or shadow_runner_sha256() != self.runner_pin:
            raise PermissionError("owner-pinned shadow policy/runner mismatch")
        self.builder._assert_pinned()

    def _inputs(self, candidate_digest: str):
        self._assert_pinned()
        policy = ShadowPolicy.model_validate(strict_document(self._policy_bytes, maximum_bytes=8192))
        corpus = load_corpus(self.store, policy.corpus_sha256)
        baseline, _ = self.builder.load(policy.baseline_runtime_build_sha256)
        candidate, _ = self.builder.load(candidate_digest)
        failures = []
        for field in ("baseline_release_id", "baseline_runtime_build_sha256", "baseline_source_sha256",
                      "protected_manifest_sha256"):
            if getattr(corpus, field) != getattr(policy, field):
                failures.append("corpus_baseline_or_protected_mismatch")
                break
        if (baseline.source_sha256 != policy.baseline_source_sha256
                or candidate.baseline_release_id != policy.baseline_release_id
                or candidate.baseline_source_sha256 != baseline.source_sha256):
            failures.append("actual_baseline_or_candidate_version_mismatch")
        if (candidate.contract != baseline.contract or candidate.state_schema != baseline.state_schema
                or candidate.state_schema != policy.state_schema
                or candidate.protected_manifest_sha256 != policy.protected_manifest_sha256
                or baseline.protected_manifest_sha256 != policy.protected_manifest_sha256):
            failures.append("runtime_contract_or_state_compatibility_mismatch")
        boundary = self.builder.replay_validator.policy.boundary
        if (len(corpus.cases) > policy.maximum_cases
                or len(corpus.cases) * policy.repetitions * 2 * boundary.wall_seconds > policy.maximum_seconds):
            failures.append("shadow_resource_envelope_exceeded")
        harness = self.builder.replay_validator._harness
        for case in corpus.cases:
            snapshot = harness.snapshot({name: item.value for name, item in case.evidence.items()})
            expected = harness.dispatch(canonical_bytes({"operation": "propose_features",
                "snapshot_id": snapshot["snapshot_id"], "features": case.expected_features}).decode(), snapshot)
            if expected["features"] != case.expected_features:
                raise ValueError("shadow expected features require canonical Decimal strings")
        return policy, corpus, baseline, candidate, failures

    def compare(self, candidate_digest: str) -> str:
        policy, corpus, baseline, candidate, failures = self._inputs(candidate_digest)
        failures = set(failures)
        started_ns = time.monotonic_ns()
        deadline_ns = started_ns + policy.maximum_seconds * 1_000_000_000
        attempts, earlier, errors = [], {}, {"baseline": [], "candidate": []}
        if not failures:
            for repetition in range(policy.repetitions):
                arms = ("baseline", "candidate") if repetition % 2 == 0 else ("candidate", "baseline")
                for case in corpus.cases:
                    observations = {name: item.value for name, item in case.evidence.items()}
                    for arm in arms:
                        runtime = baseline if arm == "baseline" else candidate
                        self._assert_pinned()
                        available = deadline_ns - time.monotonic_ns()
                        if available < int(self.builder.replay_validator.policy.boundary.wall_seconds * 1_000_000_000):
                            outcome = {"status": "not_run_parent_deadline", "features": None, "exit_code": None}
                            failures.add("shadow_parent_deadline")
                        else:
                            try:
                                outcome = self.builder.evaluate(runtime.runtime_build_sha256, observations)
                            except (ValueError, OSError) as exc:
                                outcome = {"status": "controller_rejected", "features": None, "exit_code": None,
                                           "diagnostic": type(exc).__name__}
                        features = outcome["features"]
                        attempts.append({"case_id": case.case_id, "arm": arm, "repetition": repetition,
                                         "runtime_build_sha256": runtime.runtime_build_sha256,
                                         "source_sha256": runtime.source_sha256,
                                         "decision_at_utc": case.decision_at_utc.isoformat(),
                                         "status": outcome["status"], "exit_code": outcome["exit_code"],
                                         "features": features, "fallback": outcome.get("fallback"),
                                         "diagnostic": outcome.get("diagnostic")})
                        if outcome["status"] != "validated_numeric_proposal":
                            failures.add(f"{arm}_capability_resource_or_controller_failure")
                        else:
                            with localcontext() as context:
                                context.prec = 100
                                errors[arm].extend(abs(Decimal(value) - Decimal(case.expected_features[name]))
                                                   for name, value in features.items())
                        key = (arm, case.case_id)
                        if key in earlier and earlier[key] != features:
                            failures.add(f"{arm}_nondeterministic_shadow")
                        earlier[key] = features
        metrics = {}
        with localcontext() as context:
            context.prec = 100
            for arm, values in errors.items():
                metrics[arm] = None if not values else {
                    "mean_absolute_error_upper": canonical_decimal((sum(values) / len(values)).quantize(
                        Decimal("1e-18"), rounding=ROUND_CEILING)),
                    "maximum_absolute_error": canonical_decimal(max(values)),
                    "total_absolute_error": canonical_decimal(sum(values)), "samples": len(values)}
            if metrics["baseline"] is not None and metrics["candidate"] is not None:
                candidate_total = sum(errors["candidate"])
                baseline_total = sum(errors["baseline"])
                candidate_count, baseline_count = len(errors["candidate"]), len(errors["baseline"])
                # Compare exact sums/counts without allowing a rounded baseline
                # display mean to loosen the owner's regression tolerance.
                if (candidate_total > Decimal(policy.maximum_mean_absolute_error) * candidate_count
                        or Decimal(metrics["candidate"]["maximum_absolute_error"])
                        > Decimal(policy.maximum_absolute_error)
                        or candidate_total * baseline_count > baseline_total * candidate_count
                        + Decimal(policy.allowed_mean_error_regression) * candidate_count * baseline_count):
                    failures.add("predeclared_functional_quality_or_regression_limit")
        self._assert_pinned()
        # Current pins and both exact builds/corpus must still verify. Retain a
        # negative receipt if a controller detects changed bytes after execution.
        try:
            _, _, _, _, final_failures = self._inputs(candidate_digest)
            failures.update(final_failures)
        except (ValueError, OSError):
            failures.add("retained_artifact_changed_during_shadow")
        report = {"schema_version": 1, "kind": "plugin_shadow", "shadow_attempt_id": uuid.uuid4().hex,
                  "status": "functional_shadow_passed" if not failures else "rejected", "failures": sorted(failures),
                  "shadow_runner_sha256": self.runner_pin, "shadow_policy_sha256": self.policy_pin,
                  "corpus_sha256": policy.corpus_sha256, "protected_manifest_sha256": policy.protected_manifest_sha256,
                  "baseline_release_id": policy.baseline_release_id,
                  "baseline_runtime_build_sha256": baseline.runtime_build_sha256,
                  "baseline_source_sha256": baseline.source_sha256,
                  "candidate_runtime_build_sha256": candidate.runtime_build_sha256,
                  "candidate_source_sha256": candidate.source_sha256,
                  "candidate_stage_build_digest": candidate.stage_build_digest,
                  "candidate_stage_manifest_sha256": candidate.stage_manifest_sha256,
                  "candidate_replay_receipt_sha256": candidate.replay_receipt_sha256,
                  "state_schema": policy.state_schema, "attempts": attempts, "metrics": metrics,
                  "elapsed_seconds": str(Decimal(time.monotonic_ns() - started_ns) / 1_000_000_000),
                  "created_at_utc": datetime.now(UTC).isoformat(), "production_authorization": False,
                  "live_authorization": False, "economic_evidence": "not_evaluated",
                  "source_verification": "retained_declared_point_in_time"}
        return self.store.retain_receipt(authenticate(report, self._key))

    def verify(self, receipt_digest: str, *, candidate_digest: str) -> dict:
        self._assert_pinned()
        report = authenticated_report(self.store, receipt_digest, self._key)
        policy, _, baseline, candidate, _ = self._inputs(candidate_digest)
        required = {"kind": "plugin_shadow", "shadow_runner_sha256": self.runner_pin,
                    "shadow_policy_sha256": self.policy_pin, "corpus_sha256": policy.corpus_sha256,
                    "protected_manifest_sha256": policy.protected_manifest_sha256,
                    "baseline_release_id": policy.baseline_release_id,
                    "baseline_runtime_build_sha256": baseline.runtime_build_sha256,
                    "baseline_source_sha256": baseline.source_sha256,
                    "candidate_runtime_build_sha256": candidate.runtime_build_sha256,
                    "candidate_source_sha256": candidate.source_sha256,
                    "candidate_stage_build_digest": candidate.stage_build_digest,
                    "candidate_stage_manifest_sha256": candidate.stage_manifest_sha256,
                    "candidate_replay_receipt_sha256": candidate.replay_receipt_sha256,
                    "state_schema": policy.state_schema, "production_authorization": False,
                    "live_authorization": False, "economic_evidence": "not_evaluated",
                    "source_verification": "retained_declared_point_in_time"}
        if any(report.get(key) != value for key, value in required.items()):
            raise PermissionError("shadow receipt independent baseline/build/corpus/policy mismatch")
        return {**report, "shadow_receipt_sha256": receipt_digest}
