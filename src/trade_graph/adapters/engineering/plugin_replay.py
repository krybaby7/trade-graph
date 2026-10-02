"""Independent, owner-pinned finite replay evidence for sealed pure plugins.

Fresh OS-confined child execution checks conformance and repeatability over a
fixed corpus. A signed receipt is limited evidence, never economics, an owner
class grant, staging rollout or production activation permission.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from trade_graph.adapters.engineering import artifact_files, plugin_artifacts
from trade_graph.adapters.engineering.plugin_artifacts import (
    PluginStageStore,
    canonical_bytes,
    require_digest,
    sha256,
)
from trade_graph.domain import money
from trade_graph.kernel.process_boundary import (
    BoundaryPolicy,
    ProtectedBoundaryHarness,
    protected_fingerprint,
)


class ReplayCase(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    case_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.:-]+$")
    observations: dict[str, str]
    expected_features: dict[str, str]


@dataclass(frozen=True)
class ReplayPolicy:
    boundary: BoundaryPolicy = BoundaryPolicy()
    repetitions: int = 2
    maximum_cases: int = 8
    maximum_validation_seconds: Decimal = Decimal("60")

    def __post_init__(self):
        if (type(self.boundary) is not BoundaryPolicy or type(self.repetitions) is not int
                or not 2 <= self.repetitions <= 5 or type(self.maximum_cases) is not int
                or not 1 <= self.maximum_cases <= 16):
            raise ValueError("bounded repeatability policy required")
        if (type(self.maximum_validation_seconds) is not Decimal or not self.maximum_validation_seconds.is_finite()
                or not 1 <= self.maximum_validation_seconds <= 60):
            raise ValueError("bounded parent validation deadline required")

    def document(self) -> dict:
        return {"boundary_policy_sha256": self.boundary.fingerprint(), "repetitions": self.repetitions,
                "maximum_cases": self.maximum_cases,
                "maximum_validation_seconds": str(self.maximum_validation_seconds)}


def validator_fingerprint() -> str:
    """Pin from an owner-controlled immutable release, never candidate checkout."""
    digest = hashlib.sha256()
    for path in (Path(__file__), Path(plugin_artifacts.__file__), Path(artifact_files.__file__), Path(money.__file__)):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    digest.update(protected_fingerprint().encode())
    digest.update(sys.version.encode())
    return digest.hexdigest()


def protected_manifest_sha256(policy: ReplayPolicy, *, kernel_sha256: str, validator_sha256: str) -> str:
    require_digest(kernel_sha256)
    require_digest(validator_sha256)
    return sha256(canonical_bytes({"schema_version": 1, "class_name": "pure_feature_plugin",
                                   "contract": "numeric_features/v1", "kernel_sha256": kernel_sha256,
                                   "validator_sha256": validator_sha256, "replay_policy": policy.document()}))


def _corpus_bytes(cases: tuple[ReplayCase, ...]) -> bytes:
    if type(cases) is not tuple or not 1 <= len(cases) <= 16 or any(type(case) is not ReplayCase for case in cases):
        raise ValueError("owner-fixed typed replay cases required")
    data = canonical_bytes({"schema_version": 1, "cases": [case.model_dump() for case in cases]})
    if len(data) > 131072:
        raise ValueError("owner-fixed corpus byte bound exceeded")
    return data


def corpus_sha256(cases: tuple[ReplayCase, ...]) -> str:
    return sha256(_corpus_bytes(cases))


class PluginReplayValidator:
    """Trusted parent copies/pins a corpus; candidate receives no tests or key.

    The receipt key is an owner-controlled private value shared only with the
    future independent verifier. Possession of a candidate manifest, report,
    test-name string or caller boolean does not produce a valid receipt.
    """

    def __init__(self, store: PluginStageStore, *, policy: ReplayPolicy, cases: tuple[ReplayCase, ...],
                 expected_kernel_sha256: str, expected_validator_sha256: str,
                 expected_protected_manifest_sha256: str, expected_corpus_sha256: str,
                 receipt_key: bytes) -> None:
        if type(receipt_key) is not bytes or len(receipt_key) < 32:
            raise ValueError("owner-private receipt authentication key of at least 32 bytes required")
        for digest in (expected_kernel_sha256, expected_validator_sha256, expected_protected_manifest_sha256,
                       expected_corpus_sha256):
            require_digest(digest)
        self.store, self.policy = store, policy
        self._kernel_sha256 = expected_kernel_sha256
        self._validator_sha256 = expected_validator_sha256
        self._protected_manifest_sha256 = expected_protected_manifest_sha256
        self._corpus_sha256 = expected_corpus_sha256
        self._receipt_key = receipt_key
        self._assert_pinned()
        corpus_data = _corpus_bytes(cases)
        copied_cases = json.loads(corpus_data)["cases"]
        if (not 1 <= len(cases) <= policy.maximum_cases
                or len({case["case_id"] for case in copied_cases}) != len(copied_cases)
                or sha256(corpus_data) != self._corpus_sha256
                or len(cases) * policy.repetitions * policy.boundary.wall_seconds > policy.maximum_validation_seconds):
            raise ValueError("corpus identity, uniqueness or resource envelope mismatch")
        # Deep-copy via canonical bytes: caller mutation cannot change held-out
        # expectations after validation or leak them into child input.
        self._corpus_bytes = corpus_data
        self._harness = ProtectedBoundaryHarness(expected_kernel_sha256=expected_kernel_sha256,
                                                expected_policy_sha256=policy.boundary.fingerprint(),
                                                policy=policy.boundary, credential="")
        for case in copied_cases:
            snapshot = self._harness.snapshot(case["observations"])
            message = {"operation": "propose_features", "snapshot_id": snapshot["snapshot_id"],
                       "features": case["expected_features"]}
            normalized = self._harness.dispatch(canonical_bytes(message).decode(), snapshot)
            if normalized["features"] != case["expected_features"]:
                # Fixed corpus identity uses canonical Decimal strings; refusing
                # noncanonical expectations avoids changing its pinned bytes.
                raise ValueError("held-out expected features require canonical Decimal strings")

    def _assert_pinned(self) -> None:
        if (protected_fingerprint() != self._kernel_sha256
                or validator_fingerprint() != self._validator_sha256
                or protected_manifest_sha256(self.policy, kernel_sha256=self._kernel_sha256,
                                             validator_sha256=self._validator_sha256)
                != self._protected_manifest_sha256):
            raise PermissionError("owner-pinned plugin validator/kernel/policy mismatch")
        if hasattr(self, "_corpus_bytes") and sha256(self._corpus_bytes) != self._corpus_sha256:
            raise PermissionError("owner-pinned held-out corpus mismatch")

    def _bound_stage(self, build_digest: str):
        stage = self.store.load(build_digest)
        manifest = stage.manifest
        if (manifest.protected_manifest_sha256 != self._protected_manifest_sha256
                or manifest.validation_corpus_sha256 != self._corpus_sha256):
            raise PermissionError("plugin stage is bound to another validator or held-out corpus")
        return stage

    def validate(self, build_digest: str) -> str:
        """Retain every actual attempt; no child-supplied status is authority."""
        self._assert_pinned()
        stage = self._bound_stage(build_digest)
        attempts, failures, outputs = [], set(), {}
        fixed_cases = json.loads(self._corpus_bytes)["cases"]
        started_at = datetime.now(UTC).isoformat()
        started_ns = time.monotonic_ns()
        deadline_ns = started_ns + int(self.policy.maximum_validation_seconds * 1_000_000_000)
        # Alternating corpus order and fresh exec per case avoid carrying module
        # globals between invocations. This remains finite replay evidence.
        for repetition in range(self.policy.repetitions):
            cases = fixed_cases if repetition % 2 == 0 else fixed_cases[::-1]
            for case in cases:
                self._assert_pinned()
                # Do not start a bounded child whose full wall-time envelope no
                # longer fits the independent parent's remaining work quota.
                if deadline_ns - time.monotonic_ns() < int(self.policy.boundary.wall_seconds * 1_000_000_000):
                    failures.add("parent_validation_deadline")
                    attempts.append({"case_id": case["case_id"], "repetition": repetition,
                                     "status": "not_run_parent_deadline", "exit_code": None,
                                     "features": None, "diagnostic": "remaining parent quota insufficient"})
                    continue
                attempt_ns = time.monotonic_ns()
                result = self._harness.evaluate(stage.source_text, case["observations"],
                                                expected_source_sha256=stage.manifest.source_sha256)
                features = result["proposal"]["features"] if result["proposal"] else None
                attempts.append({"case_id": case["case_id"], "repetition": repetition,
                                 "status": result["status"], "exit_code": result["process"]["exit_code"],
                                 "features": features,
                                 "elapsed_seconds": str(Decimal(time.monotonic_ns() - attempt_ns) / 1_000_000_000),
                                 "diagnostic": result["process"].get("validation_error",
                                                                         result["process"]["stderr"])[:300]})
                if result["status"] != "validated_numeric_proposal":
                    failures.add("capability_or_resource_or_contract_failure")
                elif features != case["expected_features"]:
                    failures.add("held_out_expectation_mismatch")
                if case["case_id"] in outputs and outputs[case["case_id"]] != features:
                    failures.add("nondeterministic_replay")
                outputs.setdefault(case["case_id"], features)
        self._assert_pinned()
        # Detect changed on-disk bytes after the exact tested copy ran. A file
        # replacement can never transfer its predecessor's receipt to new code.
        try:
            if self._bound_stage(build_digest) != stage:
                failures.add("stage_changed_during_validation")
        except (ValueError, OSError):
            failures.add("stage_changed_during_validation")
        report = {"schema_version": 1, "validation_id": "plugin-validation-" + uuid.uuid4().hex,
                  "class_name": "pure_feature_plugin", "contract": "numeric_features/v1",
                  "build_digest": build_digest, "source_sha256": stage.manifest.source_sha256,
                  "manifest_sha256": stage.manifest_sha256,
                  "baseline_release_id": stage.manifest.baseline_release_id,
                  "baseline_source_sha256": stage.manifest.baseline_source_sha256,
                  "protected_manifest_sha256": self._protected_manifest_sha256,
                  "validation_corpus_sha256": self._corpus_sha256,
                  "started_at_utc": started_at, "completed_at_utc": datetime.now(UTC).isoformat(),
                  "elapsed_seconds": str(Decimal(time.monotonic_ns() - started_ns) / 1_000_000_000),
                  "resource_policy": self.policy.document(),
                  "status": "finite_replay_passed" if not failures else "rejected",
                  "failures": sorted(failures), "attempts": attempts,
                  "production_authorization": False, "live_authorization": False,
                  "economic_evidence": "not_evaluated"}
        signature = hmac.new(self._receipt_key, canonical_bytes(report), hashlib.sha256).hexdigest()
        return self.store.retain_receipt({"report": report, "authentication_sha256": signature})

    def verified_receipt(self, digest: str, *, build_digest: str) -> dict:
        """Verify persisted evidence on restart; it never returns deployment authority."""
        self._assert_pinned()
        envelope = self.store.receipt(digest)
        if type(envelope) is not dict or set(envelope) != {"report", "authentication_sha256"}:
            raise PermissionError("invalid parent-authenticated plugin receipt")
        report, signature = envelope["report"], envelope["authentication_sha256"]
        if type(report) is not dict or type(signature) is not str:
            raise PermissionError("invalid parent-authenticated plugin receipt")
        try:
            require_digest(signature)
        except ValueError as exc:
            raise PermissionError("invalid parent-authenticated plugin receipt") from exc
        expected = hmac.new(self._receipt_key, canonical_bytes(report), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise PermissionError("plugin receipt authentication failed")
        stage = self._bound_stage(build_digest)
        required = {"schema_version": 1, "class_name": "pure_feature_plugin", "contract": "numeric_features/v1",
                    "build_digest": build_digest, "source_sha256": stage.manifest.source_sha256,
                    "manifest_sha256": stage.manifest_sha256,
                    "baseline_release_id": stage.manifest.baseline_release_id,
                    "baseline_source_sha256": stage.manifest.baseline_source_sha256,
                    "protected_manifest_sha256": self._protected_manifest_sha256,
                    "validation_corpus_sha256": self._corpus_sha256,
                    "production_authorization": False, "live_authorization": False,
                    "economic_evidence": "not_evaluated"}
        if any(report.get(name) != value for name, value in required.items()):
            raise PermissionError("plugin receipt build/baseline/protected/corpus identity mismatch")
        return {**report, "validation_receipt_sha256": digest}
