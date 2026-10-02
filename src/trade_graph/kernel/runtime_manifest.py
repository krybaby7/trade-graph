"""Owner-pinned identities for the protected runtime, never supplied by a child."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def document_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def protected_package_sha256() -> str:
    """Pin all installed application source, not just the child or its validator.

    This is a source/interpreter pin, not a claim of an immutable dependency/OS
    image. Owners must provide the pin from their approved protected release.
    """
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    files = sorted(root.rglob("*.py")) + [root / "runtime_artifacts.json"]
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(path.read_bytes())
    digest.update(sys.version.encode())
    digest.update(Path(sys.executable).resolve().read_bytes())
    return digest.hexdigest()


@dataclass(frozen=True)
class ProtectedRuntimeManifest:
    schema_version: int
    protected_package_sha256: str
    deployment_id: str
    approved_source_sha256: tuple[str, ...]
    capability_ttl_seconds: int = 15
    maximum_source_bytes: int = 65536
    maximum_output_bytes: int = 16384
    wall_seconds: int = 2
    operations: tuple[str, ...] = ("submit_decision",)

    def __post_init__(self) -> None:
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.protected_package_sha256) is not str or len(self.protected_package_sha256) != 64
                or any(c not in "0123456789abcdef" for c in self.protected_package_sha256)
                or type(self.deployment_id) is not str or not self.deployment_id or len(self.deployment_id) > 128
                or type(self.approved_source_sha256) is not tuple
                or not self.approved_source_sha256 or len(self.approved_source_sha256) > 64
                or any(type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
                       for value in self.approved_source_sha256)
                or len(set(self.approved_source_sha256)) != len(self.approved_source_sha256)
                or type(self.capability_ttl_seconds) is not int or not 1 <= self.capability_ttl_seconds <= 30
                or type(self.maximum_source_bytes) is not int or not 1 <= self.maximum_source_bytes <= 65536
                or type(self.maximum_output_bytes) is not int or not 1 <= self.maximum_output_bytes <= 16384
                or type(self.wall_seconds) is not int or not 1 <= self.wall_seconds <= 10
                or type(self.operations) is not tuple or self.operations != ("submit_decision",)):
            raise ValueError("invalid protected owner manifest")

    @property
    def sha256(self) -> str:
        return document_sha256(asdict(self))

    def assert_current(self) -> None:
        if protected_package_sha256() != self.protected_package_sha256:
            raise PermissionError("protected installed package/interpreter pin mismatch")
