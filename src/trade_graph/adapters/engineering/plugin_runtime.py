"""Sealed executable application bundles tied to independent replay evidence.

The bundle copies its actual trusted worker/sandbox and source. Python binary,
stdlib and native-extension bytes are pinned on the current host, not copied into
an OS/container image. This offline preparation grants no deployed authority.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import stat
import sys
import sysconfig
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from trade_graph.adapters.engineering.plugin_artifacts import (
    MAX_SOURCE_BYTES,
    SHA256_PATTERN,
    PluginStageStore,
    bounded_document,
    canonical_bytes,
    require_digest,
    sha256,
)
from trade_graph.adapters.engineering.plugin_replay import PluginReplayValidator, validator_fingerprint
from trade_graph.adapters.engineering.process import run_bounded
from trade_graph.adapters.engineering.sandbox import INPUT_BYTES
from trade_graph.kernel.process_boundary import ProtectedBoundaryHarness

WORKER = Path(__file__).with_name("plugin_worker.py")
SANDBOX = Path(__file__).with_name("sandbox.py")
FILES = {"source.py", "worker.py", "sandbox.py", "manifest.json"}


def strict_document(data: bytes, *, maximum_bytes: int = 131072) -> dict:
    return bounded_document(data, maximum_bytes=maximum_bytes)


def runtime_environment_sha256() -> str:
    """Exact current Python/stdlib inventory; OS linker dependencies remain separate.

    Runtime invocation redirects bytecode-cache lookup to an absent path inside
    the sealed bundle and disables writes. Host .pyc caches are not consumed.
    """
    digest = hashlib.sha256()
    digest.update(sys.version.encode())
    digest.update(sys.platform.encode())
    executable = Path(sys.executable).resolve()
    digest.update(str(executable).encode())
    digest.update(_bounded_runtime_bytes(executable))
    root = Path(sysconfig.get_path("stdlib")).resolve()
    files = []
    for base, directories, names in os.walk(root, followlinks=False):
        if any((Path(base) / name).is_symlink() for name in directories):
            raise PermissionError("runtime inventory cannot omit linked dependency directories")
        directories[:] = sorted(name for name in directories if name not in {"site-packages", "dist-packages",
                                                                            "__pycache__"})
        for name in sorted(names):
            path = Path(base) / name
            if path.suffix in {".py", ".so", ".pyd"}:
                files.append(path)
    if not 1 <= len(files) <= 4096:
        raise ValueError("Python runtime file inventory exceeds bound")
    total = 0
    for path in sorted(files):
        data = _bounded_runtime_bytes(path, maximum=16777216)
        total += len(data)
        if len(data) > 16777216 or total > 134217728:
            raise ValueError("Python runtime bytes exceed bound")
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(data)
    library = Path(sysconfig.get_config_var("LIBDIR")) / sysconfig.get_config_var("LDLIBRARY")
    if library.is_file():
        digest.update(str(library.resolve()).encode())
        digest.update(_bounded_runtime_bytes(library.resolve()))
    standard_zip = root.parent / f"python{sys.version_info.major}{sys.version_info.minor}.zip"
    digest.update(str(standard_zip).encode())
    digest.update(_bounded_runtime_bytes(standard_zip) if standard_zip.exists() else b"absent")
    return digest.hexdigest()


def _bounded_runtime_bytes(path: Path, maximum: int = 33554432) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise ValueError("runtime dependency must be a bounded regular file")
        data = bytearray()
        while len(data) <= maximum:
            chunk = os.read(fd, min(65536, maximum + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(fd)
        if len(data) > maximum or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise PermissionError("runtime dependency changed or exceeded bound")
        return bytes(data)
    finally:
        os.close(fd)


def runtime_builder_sha256() -> str:
    digest = hashlib.sha256()
    for path in (Path(__file__), WORKER, SANDBOX):
        digest.update(path.name.encode() + b"\0")
        digest.update(path.read_bytes())
    digest.update(validator_fingerprint().encode())
    return digest.hexdigest()


class PluginRuntimeManifest(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: int = Field(default=1, ge=1, le=1)
    class_name: Literal["pure_feature_plugin"] = "pure_feature_plugin"
    contract: Literal["numeric_features/v1"] = "numeric_features/v1"
    state_schema: Literal["stateless/v1"] = "stateless/v1"
    runtime_build_sha256: str = Field(pattern=SHA256_PATTERN)
    stage_build_digest: str = Field(pattern=SHA256_PATTERN)
    source_sha256: str = Field(pattern=SHA256_PATTERN)
    source_bytes: int = Field(ge=1, le=MAX_SOURCE_BYTES)
    stage_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    replay_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    protected_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    validation_corpus_sha256: str = Field(pattern=SHA256_PATTERN)
    baseline_release_id: str = Field(min_length=1, max_length=128)
    baseline_source_sha256: str = Field(pattern=SHA256_PATTERN)
    runtime_builder_sha256: str = Field(pattern=SHA256_PATTERN)
    runtime_environment_sha256: str = Field(pattern=SHA256_PATTERN)
    worker_sha256: str = Field(pattern=SHA256_PATTERN)
    sandbox_sha256: str = Field(pattern=SHA256_PATTERN)

    def identity(self) -> dict:
        return self.model_dump(exclude={"runtime_build_sha256"})


def authenticate(report: dict, key: bytes) -> dict:
    signature = hmac.new(key, canonical_bytes(report), hashlib.sha256).hexdigest()
    return {"report": report, "authentication_sha256": signature}


def authenticated_report(store: PluginStageStore, digest: str, key: bytes) -> dict:
    envelope = store.receipt(digest)
    # Reparse exact canonical bytes with structural bounds before trusting nested fields.
    strict_document(canonical_bytes(envelope), maximum_bytes=524288)
    if type(envelope) is not dict or set(envelope) != {"report", "authentication_sha256"}:
        raise PermissionError("invalid parent-authenticated bundle/shadow receipt")
    report, signature = envelope["report"], envelope["authentication_sha256"]
    try:
        require_digest(signature)
    except ValueError as exc:
        raise PermissionError("invalid receipt authentication") from exc
    expected = hmac.new(key, canonical_bytes(report), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected) or type(report) is not dict:
        raise PermissionError("bundle/shadow receipt authentication failed")
    return report


class PluginRuntimeBuilder:
    def __init__(self, store: PluginStageStore, *, replay_validator: PluginReplayValidator,
                 expected_builder_sha256: str, expected_environment_sha256: str,
                 expected_protected_manifest_sha256: str, receipt_key: bytes) -> None:
        for digest in (expected_builder_sha256, expected_environment_sha256, expected_protected_manifest_sha256):
            require_digest(digest)
        if type(receipt_key) is not bytes or len(receipt_key) < 32:
            raise ValueError("owner-private bundle receipt key required")
        self.store, self.replay_validator = store, replay_validator
        self.builder_pin, self.environment_pin = expected_builder_sha256, expected_environment_sha256
        self.protected_pin, self._key = expected_protected_manifest_sha256, receipt_key
        self._assert_pinned()

    def _assert_pinned(self) -> None:
        if runtime_builder_sha256() != self.builder_pin or runtime_environment_sha256() != self.environment_pin:
            raise PermissionError("owner-pinned runtime builder/environment mismatch")

    def build(self, stage_build_digest: str, *, replay_receipt_sha256: str) -> dict:
        self._assert_pinned()
        stage = self.store.load(stage_build_digest)
        replay = self.replay_validator.verified_receipt(replay_receipt_sha256, build_digest=stage_build_digest)
        failures = []
        if stage.manifest.protected_manifest_sha256 != self.protected_pin:
            failures.append("protected_manifest_mismatch")
        if replay["status"] != "finite_replay_passed":
            failures.append("independent_replay_rejected")
        built = None
        if not failures:
            worker, sandbox = WORKER.read_bytes(), SANDBOX.read_bytes()
            identity = {"schema_version": 1, "class_name": "pure_feature_plugin", "contract": "numeric_features/v1",
                        "state_schema": "stateless/v1", "stage_build_digest": stage_build_digest,
                        "source_sha256": stage.manifest.source_sha256, "source_bytes": stage.manifest.source_bytes,
                        "stage_manifest_sha256": stage.manifest_sha256, "replay_receipt_sha256": replay_receipt_sha256,
                        "protected_manifest_sha256": self.protected_pin,
                        "validation_corpus_sha256": stage.manifest.validation_corpus_sha256,
                        "baseline_release_id": stage.manifest.baseline_release_id,
                        "baseline_source_sha256": stage.manifest.baseline_source_sha256,
                        "runtime_builder_sha256": self.builder_pin, "runtime_environment_sha256": self.environment_pin,
                        "worker_sha256": sha256(worker), "sandbox_sha256": sha256(sandbox)}
            built = sha256(canonical_bytes(identity))
            manifest = PluginRuntimeManifest(**identity, runtime_build_sha256=built)
            self.store._publish("runtimes", built, {"source.py": stage.source_text.encode(), "worker.py": worker,
                                                    "sandbox.py": sandbox,
                                                    "manifest.json": canonical_bytes(manifest.model_dump())})
            self.load(built)
        self._assert_pinned()
        report = {"schema_version": 1, "kind": "plugin_runtime_build", "build_attempt_id": uuid.uuid4().hex,
                  "status": "built" if built else "rejected", "failures": failures,
                  "runtime_build_sha256": built, "stage_build_digest": stage_build_digest,
                  "replay_receipt_sha256": replay_receipt_sha256, "source_sha256": stage.manifest.source_sha256,
                  "stage_manifest_sha256": stage.manifest_sha256, "runtime_builder_sha256": self.builder_pin,
                  "runtime_environment_sha256": self.environment_pin, "protected_manifest_sha256": self.protected_pin,
                  "production_authorization": False, "live_authorization": False}
        receipt = self.store.retain_receipt(authenticate(report, self._key))
        return {**report, "build_receipt_sha256": receipt}

    def load(self, digest: str) -> tuple[PluginRuntimeManifest, str]:
        self._assert_pinned()
        require_digest(digest)
        fd = self.store._object("runtimes", digest)
        try:
            self.store._private_directory(fd, sealed=True)
            if set(os.listdir(fd)) != FILES:
                raise PermissionError("runtime bundle has unexpected files")
            data = self.store._read_file(fd, "manifest.json", 8192)
            document = strict_document(data, maximum_bytes=8192)
            manifest = PluginRuntimeManifest.model_validate(document)
            if (data != canonical_bytes(manifest.model_dump()) or manifest.runtime_build_sha256 != digest
                    or sha256(canonical_bytes(manifest.identity())) != digest
                    or manifest.runtime_builder_sha256 != self.builder_pin
                    or manifest.runtime_environment_sha256 != self.environment_pin
                    or manifest.protected_manifest_sha256 != self.protected_pin):
                raise PermissionError("runtime bundle manifest/pin identity mismatch")
            source = self.store._read_file(fd, "source.py", MAX_SOURCE_BYTES)
            worker = self.store._read_file(fd, "worker.py", 65536)
            sandbox = self.store._read_file(fd, "sandbox.py", 65536)
            if (len(source) != manifest.source_bytes or sha256(source) != manifest.source_sha256
                    or sha256(worker) != manifest.worker_sha256 or sha256(sandbox) != manifest.sandbox_sha256
                    or worker != WORKER.read_bytes() or sandbox != SANDBOX.read_bytes()):
                raise PermissionError("runtime bundle source/worker/sandbox content mismatch")
            stage = self.store.load(manifest.stage_build_digest)
            replay = self.replay_validator.verified_receipt(manifest.replay_receipt_sha256,
                                                           build_digest=manifest.stage_build_digest)
            if (stage.source_text.encode() != source or stage.manifest_sha256 != manifest.stage_manifest_sha256
                    or replay["status"] != "finite_replay_passed"
                    or manifest.baseline_release_id != stage.manifest.baseline_release_id
                    or manifest.baseline_source_sha256 != stage.manifest.baseline_source_sha256
                    or manifest.validation_corpus_sha256 != stage.manifest.validation_corpus_sha256):
                raise PermissionError("runtime bundle lacks exact independently passed stage/replay")
            return manifest, source.decode()
        finally:
            os.close(fd)

    def evaluate(self, digest: str, observations: dict[str, str]) -> dict:
        manifest, source = self.load(digest)
        policy = self.replay_validator.policy.boundary
        harness = ProtectedBoundaryHarness(expected_kernel_sha256=self.replay_validator._kernel_sha256,
                                          expected_policy_sha256=policy.fingerprint(), policy=policy, credential="")
        snapshot = harness.snapshot(observations)
        payload = canonical_bytes({"source": source, "snapshot": snapshot})
        if len(payload) > INPUT_BYTES:
            raise ValueError("runtime payload byte bound exceeded")
        directory = self.store.root / "runtimes" / digest
        # -B alone still reads host caches. An absent cache-prefix inside the
        # verified sealed object forces source imports from the pinned stdlib.
        command = [str(Path(sys.executable).resolve()), "-I", "-S", "-B", "-X",
                   f"pycache_prefix={directory / 'absent-cache'}",
                   str(directory / "worker.py")]
        process = run_bounded(command, payload, cwd=str(directory), wall_seconds=float(policy.wall_seconds))
        proposal, status = None, "rejected"
        if process["exit_code"] == 0:
            try:
                proposal = harness.dispatch(process["stdout"], snapshot)
                if "features" not in proposal:
                    raise ValueError("feature proposal required")
                status = "validated_numeric_proposal"
            except (ValueError, TypeError, PermissionError, ArithmeticError, RecursionError):
                proposal = None
        self.load(digest)
        return {"status": status, "features": proposal["features"] if proposal else None,
                "exit_code": process["exit_code"], "runtime_build_sha256": digest,
                "source_sha256": manifest.source_sha256,
                "fallback": harness.deterministic_fallback(observations) if proposal is None else None,
                "production_authorization": False, "live_authorization": False}

    def verify_build_receipt(self, digest: str, *, stage_build_digest: str) -> dict:
        self._assert_pinned()
        require_digest(stage_build_digest)
        report = authenticated_report(self.store, digest, self._key)
        if (report.get("kind") != "plugin_runtime_build" or report.get("runtime_builder_sha256") != self.builder_pin
                or report.get("runtime_environment_sha256") != self.environment_pin
                or report.get("protected_manifest_sha256") != self.protected_pin
                or report.get("stage_build_digest") != stage_build_digest
                or report.get("production_authorization") is not False
                or report.get("live_authorization") is not False):
            raise PermissionError("runtime build receipt scope/pin mismatch")
        stage = self.store.load(report["stage_build_digest"])
        if (stage.manifest.source_sha256 != report["source_sha256"]
                or stage.manifest_sha256 != report["stage_manifest_sha256"]):
            raise PermissionError("runtime build receipt stage mismatch")
        self.replay_validator.verified_receipt(report["replay_receipt_sha256"],
                                              build_digest=report["stage_build_digest"])
        if report["status"] == "built":
            manifest, _ = self.load(report["runtime_build_sha256"])
            if manifest.replay_receipt_sha256 != report["replay_receipt_sha256"]:
                raise PermissionError("runtime build receipt replay mismatch")
        return {**report, "build_receipt_sha256": digest}
