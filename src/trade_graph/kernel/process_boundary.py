"""Owner-pinned protected-process scaffold with numeric observation/proposal RPC.

This is deliberately absent from application, Engineer tools and live execution.
The protected parent owns its policy and synthetic/private credential; a fresh
exec child receives only an allowlisted numeric snapshot. No caller/child output
is an attestation or grants a financial action. Production kernel extraction,
host-image pinning and the owner-granted broader class remain separate work.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from trade_graph.adapters.engineering.process import run_bounded
from trade_graph.adapters.engineering.sandbox import INPUT_BYTES, OUTPUT_BYTES, WALL_SECONDS
from trade_graph.domain.money import canonical_decimal, parse_decimal

CHILD = Path(__file__).resolve().parents[1] / "adapters" / "isolation" / "mutable_child.py"
ENGINEERING = CHILD.parent.parent / "engineering"


def protected_fingerprint() -> str:
    """Owner computes/pins this outside mutable candidate or model execution."""
    digest = hashlib.sha256()
    files = [Path(__file__), CHILD, *(ENGINEERING / name for name in ("sandbox.py", "process.py", "provenance.py"))]
    for path in files:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    digest.update(sys.version.encode())
    digest.update(Path(sys.executable).resolve().read_bytes())
    return digest.hexdigest()


@dataclass(frozen=True)
class BoundaryPolicy:
    snapshot_fields: tuple[str, ...] = ("price_eur", "spread_bps", "position_quantity")
    feature_names: tuple[str, ...] = ("signal",)
    maximum_input_magnitude: Decimal = Decimal("1000000000000")
    maximum_feature_magnitude: Decimal = Decimal("1")
    maximum_source_bytes: int = 65536
    wall_seconds: Decimal = Decimal("2")

    def __post_init__(self):
        for names in (self.snapshot_fields, self.feature_names):
            if (not names or len(names) > 32 or len(set(names)) != len(names)
                    or any(type(name) is not str or not name.isidentifier() or len(name) > 64 for name in names)):
                raise ValueError("owner-pinned field allowlists are required")
        protected = {"credential", "key", "secret", "budget", "ledger", "owner_policy", "withdraw", "enable_live"}
        if protected & (set(self.snapshot_fields) | set(self.feature_names)):
            raise ValueError("protected capabilities cannot enter mutable RPC")
        for amount in (self.maximum_input_magnitude, self.maximum_feature_magnitude, self.wall_seconds):
            if (type(amount) is not Decimal or not amount.is_finite() or amount <= 0
                    or not -18 <= amount.as_tuple().exponent <= 36 or len(amount.as_tuple().digits) > 36):
                raise ValueError("finite positive Decimal bounds required")
        if (type(self.maximum_source_bytes) is not int or not 1 <= self.maximum_source_bytes <= 65536
                or self.wall_seconds > WALL_SECONDS):
            raise ValueError("policy cannot expand host resource bounds")

    def fingerprint(self) -> str:
        document = {"snapshot_fields": self.snapshot_fields, "feature_names": self.feature_names,
                    "maximum_input_magnitude": canonical_decimal(self.maximum_input_magnitude),
                    "maximum_feature_magnitude": canonical_decimal(self.maximum_feature_magnitude),
                    "maximum_source_bytes": self.maximum_source_bytes,
                    "wall_seconds": canonical_decimal(self.wall_seconds)}
        return hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()


def _no_duplicate_keys(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate RPC fields")
        document[key] = value
    return document


def _bounded_number(value: str, maximum: Decimal) -> str:
    if type(value) is not str or len(value) > 128:
        raise ValueError("RPC numbers require bounded Decimal strings")
    amount = parse_decimal(value)
    parts = amount.as_tuple()
    # Bound fixed-point precision before formatting. A short scientific string
    # such as 0e-999999999 can otherwise expand into huge protected-parent output.
    if (not -18 <= parts.exponent <= 36 or len(parts.digits) > 36 or abs(amount) > maximum):
        raise ValueError("number exceeds protected fixed-point bounds")
    return canonical_decimal(amount)


class ProtectedBoundaryHarness:
    """A testable protected parent; no budget/order/ledger mutation operation exists.

    Kernel/controller identity and RPC policy are explicit owner pins, not
    candidate-supplied checks. Source execution happens exclusively in fresh
    confined child processes. The credential is never put into their input,
    environment, command line, inherited descriptors or results.
    """

    def __init__(self, *, expected_kernel_sha256: str, expected_policy_sha256: str,
                 policy: BoundaryPolicy, credential: str) -> None:
        self._expected_kernel = expected_kernel_sha256
        self._expected_policy = expected_policy_sha256
        self._policy = policy
        self._credential = credential
        self._assert_pinned()

    def _assert_pinned(self) -> None:
        if (protected_fingerprint() != self._expected_kernel
                or self._policy.fingerprint() != self._expected_policy):
            raise PermissionError("protected kernel/controller/policy pin mismatch")

    def snapshot(self, observations: dict[str, str]) -> dict:
        self._assert_pinned()
        if type(observations) is not dict or set(observations) != set(self._policy.snapshot_fields):
            raise ValueError("snapshot must contain exactly the owner-allowlisted numeric observations")
        normalized = {}
        for name, value in observations.items():
            if type(value) is not str:
                raise ValueError("RPC numbers must use Decimal strings")
            normalized[name] = _bounded_number(value, self._policy.maximum_input_magnitude)
        encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        return {"snapshot_id": hashlib.sha256(encoded.encode()).hexdigest(), "observations": normalized}

    def dispatch(self, raw: str, snapshot: dict) -> dict:
        """Strict, scoped RPC; owner writes and financial execution have no route."""
        self._assert_pinned()
        if len(raw.encode()) > OUTPUT_BYTES:
            raise ValueError("RPC reply exceeds output limit")
        message = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
        if type(message) is not dict:
            raise ValueError("RPC object required")
        operation = message.get("operation")
        if operation == "read_snapshot" and set(message) == {"operation", "snapshot_id"}:
            if message["snapshot_id"] != snapshot["snapshot_id"]:
                raise ValueError("stale snapshot")
            return {"snapshot": snapshot}
        if operation != "propose_features" or set(message) != {"operation", "snapshot_id", "features"}:
            raise PermissionError("mutable RPC operation is not allowlisted")
        if message["snapshot_id"] != snapshot["snapshot_id"]:
            raise ValueError("stale snapshot")
        features = message["features"]
        if type(features) is not dict or set(features) != set(self._policy.feature_names):
            raise ValueError("features must match the owner-pinned allowlist")
        normalized = {}
        for name, value in features.items():
            normalized[name] = _bounded_number(value, self._policy.maximum_feature_magnitude)
        return {"snapshot_id": snapshot["snapshot_id"], "features": normalized}

    def evaluate(self, source: str, observations: dict[str, str], *, expected_source_sha256: str) -> dict:
        self._assert_pinned()
        if type(source) is not str or len(source.encode()) > self._policy.maximum_source_bytes:
            raise ValueError("candidate source exceeds owner-pinned bound")
        if hashlib.sha256(source.encode()).hexdigest() != expected_source_sha256:
            raise ValueError("candidate source content hash mismatch")
        snapshot = self.snapshot(observations)
        payload = json.dumps({"source": source, "snapshot": snapshot}, allow_nan=False).encode()
        if len(payload) > INPUT_BYTES:
            raise ValueError("candidate input exceeds host bound")
        process = run_bounded([sys.executable, "-I", "-S", "-B", str(CHILD)], payload,
                              cwd=str(CHILD.parent), wall_seconds=float(self._policy.wall_seconds))
        result = {
            "status": "rejected", "source_sha256": expected_source_sha256,
            "kernel_sha256": self._expected_kernel, "policy_sha256": self._expected_policy,
            "process": process, "proposal": None,
            "live_authorization": False, "deployed_engineer_authorization": False,
        }
        if process["exit_code"] == 0:
            try:
                result["proposal"] = self.dispatch(process["stdout"], snapshot)
                # A child may only propose numeric features, even though read_snapshot is scoped RPC.
                if "features" not in result["proposal"]:
                    raise ValueError("candidate must return a feature proposal")
                result["status"] = "validated_numeric_proposal"
            except (ValueError, PermissionError, TypeError, ArithmeticError, RecursionError) as exc:
                result["proposal"] = None
                result["process"]["validation_error"] = str(exc)[:300]
        self._assert_pinned()
        return result

    def deterministic_fallback(self, observations: dict[str, str]) -> dict:
        """Protected recovery needs no candidate or model execution; not a trade/hold."""
        snapshot = self.snapshot(observations)
        return {"status": "mutable_unavailable", "snapshot_id": snapshot["snapshot_id"],
                "features": {name: "0" for name in self._policy.feature_names}}
