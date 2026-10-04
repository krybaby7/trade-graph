"""Offline confined application projections and synthetic compatible migration evidence.

This class has no production admission route. Source receives only sanitized
Secretary reports. Fixed trusted SQL owns a separate synthetic sidecar; code
rollback switches a pointer and never restores financial or application data.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import sqlite3
import stat
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from trade_graph.adapters.engineering import (
    artifact_files,
    plugin_artifacts,
    plugin_runtime,
    process,
    provenance,
    sandbox,
)
from trade_graph.adapters.engineering.plugin_artifacts import (
    SHA256_PATTERN,
    PluginStageStore,
    bounded_document,
    canonical_bytes,
    require_digest,
    sha256,
)
from trade_graph.adapters.engineering.plugin_runtime import (
    authenticate,
    authenticated_report,
    runtime_environment_sha256,
)
from trade_graph.application.secretary import Secretary
from trade_graph.domain.errors import AuthorityDenied, StaleState, ValidationFailure

CHILD = Path(__file__).resolve().parents[1] / "adapters" / "isolation" / "department_child.py"
CLASS_NAME = "secretary_digest_projection"
SOURCE_PATH = "applications/secretary_projection.py"
ROLES = {"research", "learning", "optimisation", "trader", "engineer", "system"}
LABELS = ("Reports", "Research", "Learning", "Optimisation", "Trader", "Engineering", "System", "Incidents")
FLAGS = {
    "execution_scope": "offline_preparation",
    "production_authorization": False,
    "live_authorization": False,
    "paid_authorization": False,
    "economic_evidence": "not_evaluated",
}


def projection_controller_sha256() -> str:
    """Independent controller/boundary pin; this calculation is not an owner grant."""
    paths = (
        Path(__file__),
        CHILD,
        Path(sys.modules[Secretary.__module__].__file__),
        *(
            Path(module.__file__)
            for module in (artifact_files, plugin_artifacts, plugin_runtime, process, provenance, sandbox)
        ),
    )
    return sha256(b"".join(path.name.encode() + b"\0" + path.read_bytes() for path in paths))


def _text(value, maximum: int) -> str:
    if (
        type(value) is not str
        or not value
        or len(value.encode()) > maximum
        or any(ord(char) < 32 or char in "<>" for char in value)
    ):
        raise ValidationFailure("bounded plain projection text required")
    return value


class ProjectionPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal[1] = 1
    execution_scope: Literal["offline_preparation"] = "offline_preparation"
    class_name: Literal["secretary_digest_projection"] = CLASS_NAME
    source_path: Literal["applications/secretary_projection.py"] = SOURCE_PATH
    baseline_source_sha256: str = Field(pattern=SHA256_PATTERN)
    corpus_sha256: str = Field(pattern=SHA256_PATTERN)
    maximum_reports: int = Field(default=20, ge=1, le=20)
    maximum_records: int = Field(default=128, ge=1, le=128)
    maximum_cases: int = Field(default=8, ge=1, le=8)
    repetitions: int = Field(default=2, ge=2, le=3)
    wall_seconds: int = Field(default=2, ge=1, le=3)

    @property
    def sha256(self) -> str:
        return sha256(canonical_bytes(self.model_dump()))


class ProjectionManifest(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal[1] = 1
    class_name: Literal["secretary_digest_projection"] = CLASS_NAME
    contract: Literal["secretary_projection/v1", "secretary_projection/v2"]
    source_path: Literal["applications/secretary_projection.py"] = SOURCE_PATH
    source_sha256: str = Field(pattern=SHA256_PATTERN)
    source_bytes: int = Field(ge=1, le=65536)
    baseline_source_sha256: str = Field(pattern=SHA256_PATTERN)
    controller_sha256: str = Field(pattern=SHA256_PATTERN)
    environment_sha256: str = Field(pattern=SHA256_PATTERN)
    policy_sha256: str = Field(pattern=SHA256_PATTERN)
    corpus_sha256: str = Field(pattern=SHA256_PATTERN)
    build_sha256: str = Field(pattern=SHA256_PATTERN)

    def identity(self) -> dict:
        return self.model_dump(exclude={"build_sha256"})


def secretary_snapshot(secretary: Secretary, portfolio_id: str) -> dict:
    """Read actual trusted Secretary effects; exclude finances, costs and owner state."""
    if not isinstance(secretary, Secretary):
        raise AuthorityDenied("actual trusted Secretary required")
    with secretary.database.snapshot():
        portfolio = secretary.database.execute(
            "SELECT mode FROM portfolios WHERE portfolio_id=?", (portfolio_id,)
        ).fetchone()
        if (
            portfolio is None
            or portfolio["mode"] != "paper"
            or secretary.database.execute("SELECT 1 FROM usage_receipts WHERE synthetic=0 LIMIT 1").fetchone()
            is not None
        ):
            raise AuthorityDenied("offline projection requires paper fixtures without actual paid receipts")
        digest = secretary.digest(portfolio_id)
        reports = []
        if not digest.get("digest_id") or not 1 <= len(digest["reports"]) <= 20:
            raise ValidationFailure("bounded persisted Secretary digest required")
        for supplied in digest["reports"]:
            row = secretary.database.execute(
                "SELECT document_json,material FROM secretary_reports WHERE report_id=? AND portfolio_id=?",
                (supplied["report_id"], portfolio_id),
            ).fetchone()
            if row is None or json.loads(row["document_json"]) != supplied:
                raise AuthorityDenied("Secretary report is outside the actual persisted digest")
            role = supplied["role"]
            if role not in ROLES:
                raise ValidationFailure("unregistered report department")
            refs = supplied["evidence_refs"]
            if not 1 <= len(refs) <= 20 or any(type(ref) is not str or not 1 <= len(ref) <= 128 for ref in refs):
                raise ValidationFailure("bounded report evidence references required")
            # Real role reports contain a newline before Outcome. Preserve the
            # original immutable DB comparison above, then normalize whitespace.
            summary = supplied["summary"]
            if type(summary) is not str:
                raise ValidationFailure("plain report summary required")
            reports.append(
                {
                    "report_id": _text(supplied["report_id"], 128),
                    "role": role,
                    "kind": _text(supplied["kind"], 80),
                    "summary": _text(" ".join(summary.split()), 3000),
                    "evidence_refs": refs,
                    "material": bool(row["material"]),
                    "incident": supplied["kind"] == "incident",
                }
            )
    return {"schema_version": 1, "digest_id": _text(digest["digest_id"], 128), "reports": reports}


def retain_projection_corpus(store: PluginStageStore, cases: list[dict]) -> str:
    """Parent-selected expectations never enter the source process."""
    document = {"schema_version": 1, "class_name": CLASS_NAME, "cases": cases}
    data = canonical_bytes(document)
    bounded_document(data, maximum_bytes=131072)
    if not 1 <= len(cases) <= 8:
        raise ValidationFailure("bounded finite projection corpus required")
    digest = sha256(data)
    store._publish("corpora", digest, {"corpus.json": data})
    return digest


class OfflineApplicationProjection:
    """Pinned class-specific controller over synthetic app state, absent from runtime."""

    def __init__(
        self,
        store: PluginStageStore,
        *,
        policy: ProjectionPolicy,
        expected_policy_sha256: str,
        expected_controller_sha256: str,
        expected_environment_sha256: str,
        receipt_key: bytes,
    ) -> None:
        if type(receipt_key) is not bytes or len(receipt_key) < 32:
            raise ValueError("private independent projection receipt key required")
        self.store, self._key = store, receipt_key
        self._policy_bytes = canonical_bytes(policy.model_dump())
        self.policy_pin, self.controller_pin = expected_policy_sha256, expected_controller_sha256
        self.environment_pin = expected_environment_sha256
        self._assert_pinned()
        if policy.maximum_cases * policy.repetitions * policy.wall_seconds > 60:
            raise ValidationFailure("finite projection process work exceeds sixty-second envelope")
        self._corpus()
        self.path = store.root / "projection.sqlite"
        created = not self.path.exists()
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        fd = os.open(self.path, flags | (os.O_CREAT | os.O_EXCL if created else 0), 0o600)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise AuthorityDenied("private single-link synthetic projection sidecar required")
            self.db = sqlite3.connect(self.path, isolation_level=None, timeout=5)
            self.db.row_factory = sqlite3.Row
            if (self.path.stat().st_dev, self.path.stat().st_ino) != (info.st_dev, info.st_ino):
                self.db.close()
                raise AuthorityDenied("projection sidecar changed during open")
            if created:
                self._create()
            self._assert_sidecar()
        except BaseException:
            if hasattr(self, "db"):
                self.db.close()
            raise
        finally:
            os.close(fd)

    @property
    def policy(self) -> ProjectionPolicy:
        return ProjectionPolicy.model_validate(bounded_document(self._policy_bytes, maximum_bytes=8192))

    def _assert_pinned(self) -> None:
        if (
            sha256(self._policy_bytes) != self.policy_pin
            or projection_controller_sha256() != self.controller_pin
            or runtime_environment_sha256() != self.environment_pin
        ):
            raise AuthorityDenied("projection policy/controller/environment pin mismatch")

    def _load_object(self, collection: str, digest: str, filename: str, maximum: int) -> bytes:
        require_digest(digest)
        fd = self.store._object(collection, digest)
        try:
            self.store._private_directory(fd, sealed=True)
            if set(os.listdir(fd)) != {filename}:
                raise AuthorityDenied("projection object contains unexpected files")
            data = self.store._read_file(fd, filename, maximum)
            if sha256(data) != digest:
                raise AuthorityDenied("projection content-addressed object changed")
            return data
        finally:
            os.close(fd)

    def _corpus(self) -> dict:
        data = self._load_object("corpora", self.policy.corpus_sha256, "corpus.json", 131072)
        document = bounded_document(data, maximum_bytes=131072)
        if (
            set(document) != {"schema_version", "class_name", "cases"}
            or document["schema_version"] != 1
            or document["class_name"] != CLASS_NAME
            or not 1 <= len(document["cases"]) <= self.policy.maximum_cases
        ):
            raise AuthorityDenied("class-specific bounded projection corpus required")
        seen = set()
        for case in document["cases"]:
            if type(case) is not dict or set(case) != {"snapshot", "expected_v1", "expected_v2"}:
                raise ValidationFailure("fixed independently selected projection case required")
            snapshot = case["snapshot"]
            self._snapshot(snapshot)
            digest = sha256(canonical_bytes(snapshot))
            if digest in seen:
                raise ValidationFailure("duplicate projection corpus snapshot")
            seen.add(digest)
            self._projection(case["expected_v1"], snapshot, 1)
            self._projection(case["expected_v2"], snapshot, 2)
        return document

    def _snapshot(self, snapshot: dict) -> None:
        if (
            type(snapshot) is not dict
            or set(snapshot) != {"schema_version", "digest_id", "reports"}
            or type(snapshot["schema_version"]) is not int
            or snapshot["schema_version"] != 1
            or type(snapshot["reports"]) is not list
            or not 1 <= len(snapshot["reports"]) <= self.policy.maximum_reports
        ):
            raise ValidationFailure("bounded projection-only snapshot required")
        _text(snapshot["digest_id"], 128)
        seen = set()
        for report in snapshot["reports"]:
            if (
                type(report) is not dict
                or set(report) != {"report_id", "role", "kind", "summary", "evidence_refs", "material", "incident"}
                or report["role"] not in ROLES
                or type(report["material"]) is not bool
                or type(report["incident"]) is not bool
            ):
                raise ValidationFailure("strict public report snapshot required")
            for field, maximum in (("report_id", 128), ("kind", 80), ("summary", 3000)):
                _text(report[field], maximum)
            refs = report["evidence_refs"]
            if type(refs) is not list or not 1 <= len(refs) <= 20:
                raise ValidationFailure("bounded report references required")
            for ref in refs:
                _text(ref, 128)
            if report["report_id"] in seen:
                raise ValidationFailure("duplicate report identity")
            seen.add(report["report_id"])

    def _projection(self, projection: dict, snapshot: dict, version: int) -> list[str]:
        fields = {"title", "report_ids" if version == 1 else "groups"}
        if type(projection) is not dict or set(projection) != fields:
            raise ValidationFailure("exact application projection contract required")
        _text(projection["title"], 200)
        if version == 1:
            ids = projection["report_ids"]
        else:
            groups = projection["groups"]
            if type(groups) is not list or not 1 <= len(groups) <= len(LABELS):
                raise ValidationFailure("bounded registered projection groups required")
            labels, ids = set(), []
            for group in groups:
                if (
                    type(group) is not dict
                    or set(group) != {"label", "report_ids"}
                    or group["label"] not in LABELS
                    or group["label"] in labels
                    or type(group["report_ids"]) is not list
                    or not group["report_ids"]
                ):
                    raise ValidationFailure("exact registered unique projection group required")
                labels.add(group["label"])
                ids.extend(group["report_ids"])
        expected = [report["report_id"] for report in snapshot["reports"]]
        if (
            type(ids) is not list
            or any(type(value) is not str for value in ids)
            or len(ids) != len(expected)
            or len(set(ids)) != len(ids)
            or set(ids) != set(expected)
        ):
            raise ValidationFailure(
                "every supplied report, including incidents/material reports, required exactly once"
            )
        return ids

    def _receipt(self, kind: str, details: dict) -> str:
        report = {
            "schema_version": 1,
            "class_name": CLASS_NAME,
            "kind": kind,
            "attempt_id": uuid.uuid4().hex,
            "policy_sha256": self.policy_pin,
            "controller_sha256": self.controller_pin,
            "environment_sha256": self.environment_pin,
            "corpus_sha256": self.policy.corpus_sha256,
            **FLAGS,
            **details,
        }
        return self.store.retain_receipt(authenticate(report, self._key))

    def _verify(self, digest: str, kind: str) -> dict:
        self._assert_pinned()
        report = authenticated_report(self.store, digest, self._key)
        required = {
            "schema_version": 1,
            "class_name": CLASS_NAME,
            "kind": kind,
            "policy_sha256": self.policy_pin,
            "controller_sha256": self.controller_pin,
            "environment_sha256": self.environment_pin,
            "corpus_sha256": self.policy.corpus_sha256,
            **FLAGS,
        }
        if any(report.get(key) != value for key, value in required.items()):
            raise AuthorityDenied("projection receipt class/pins/scope substitution refused")
        return report

    def stage(self, source: str, *, version: Literal[1, 2]) -> str:
        self._assert_pinned()
        if type(source) is not str or not 1 <= len(source.encode()) <= 65536 or len(source.splitlines()) > 200:
            raise ValidationFailure("one bounded selected application source required")
        if type(version) is not int or version not in {1, 2}:
            raise ValidationFailure("fixed projection contract version required")
        identity = {
            "schema_version": 1,
            "class_name": CLASS_NAME,
            "source_path": SOURCE_PATH,
            "contract": f"secretary_projection/v{version}",
            "source_sha256": sha256(source.encode()),
            "source_bytes": len(source.encode()),
            "baseline_source_sha256": self.policy.baseline_source_sha256,
            "policy_sha256": self.policy_pin,
            "controller_sha256": self.controller_pin,
            "environment_sha256": self.environment_pin,
            "corpus_sha256": self.policy.corpus_sha256,
        }
        manifest = ProjectionManifest(**identity, build_sha256=sha256(canonical_bytes(identity)))
        self.store._publish(
            "stages",
            manifest.build_sha256,
            {"source.py": source.encode(), "manifest.json": canonical_bytes(manifest.model_dump())},
        )
        return manifest.build_sha256

    def load(self, build: str) -> tuple[ProjectionManifest, str]:
        self._assert_pinned()
        require_digest(build)
        fd = self.store._object("stages", build)
        try:
            self.store._private_directory(fd, sealed=True)
            if set(os.listdir(fd)) != {"source.py", "manifest.json"}:
                raise AuthorityDenied("projection stage contains unexpected files")
            data = self.store._read_file(fd, "manifest.json", 8192)
            manifest = ProjectionManifest.model_validate(bounded_document(data, maximum_bytes=8192))
            if (
                data != canonical_bytes(manifest.model_dump())
                or manifest.build_sha256 != build
                or sha256(canonical_bytes(manifest.identity())) != build
                or manifest.policy_sha256 != self.policy_pin
                or manifest.controller_sha256 != self.controller_pin
                or manifest.environment_sha256 != self.environment_pin
                or manifest.baseline_source_sha256 != self.policy.baseline_source_sha256
                or manifest.corpus_sha256 != self.policy.corpus_sha256
            ):
                raise AuthorityDenied("projection manifest/source/baseline/pins mismatch")
            source = self.store._read_file(fd, "source.py", 65536)
            if len(source) != manifest.source_bytes or sha256(source) != manifest.source_sha256:
                raise AuthorityDenied("sealed application source changed")
            return manifest, source.decode()
        finally:
            os.close(fd)

    def _evaluate(self, build: str, snapshot: dict) -> tuple[dict, dict]:
        manifest, source = self.load(build)
        self._snapshot(snapshot)
        nonce, capability = secrets.token_hex(24), secrets.token_hex(32)
        context = {
            "request_id": nonce,
            "capability": capability,
            "operation": "render_projection",
            "snapshot": snapshot,
        }
        payload = canonical_bytes({"source": source, "context": context})
        if len(payload) > 131072:
            raise ValidationFailure("bounded projection input exceeded")
        # Installed trusted worker, exact pinned boundary, fresh scrubbed process.
        # Avoid importing mutable host bytecode caches; source imports are pinned.
        command = [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-X",
            f"pycache_prefix={self.store.root / 'stages' / build / 'absent-cache'}",
            str(CHILD),
        ]
        outcome = process.run_bounded(command, payload, cwd=str(CHILD.parent), wall_seconds=self.policy.wall_seconds)
        self._assert_pinned()
        diagnostic = {"exit_code": outcome["exit_code"], "input_sha256": sha256(canonical_bytes(snapshot))}
        try:
            if outcome["exit_code"] != 0:
                raise ValidationFailure("confined projection process failed")
            envelope = bounded_document(outcome["stdout"].encode(), maximum_bytes=16384)
            if (
                set(envelope) != {"request_id", "capability", "operation", "proposal"}
                or envelope["request_id"] != nonce
                or envelope["operation"] != "render_projection"
                or type(envelope["capability"]) is not str
                or not hmac.compare_digest(envelope["capability"], capability)
            ):
                raise ValidationFailure("projection child envelope/snapshot binding changed")
            proposal = envelope["proposal"]
            if (
                type(proposal) is not dict
                or set(proposal) != {"node", "payload"}
                or proposal["node"] != "render_projection"
            ):
                raise ValidationFailure("projection cannot propose a protected operation")
            self._projection(proposal["payload"], snapshot, int(manifest.contract[-1]))
        except (ValidationFailure, ValueError, TypeError, RecursionError) as exc:
            exc.process_diagnostic = diagnostic
            raise
        return proposal["payload"], diagnostic

    def validate(self, build: str) -> str:
        manifest, _ = self.load(build)
        attempts, failures = [], []
        version = int(manifest.contract[-1])
        for case in self._corpus()["cases"]:
            for repetition in range(self.policy.repetitions):
                try:
                    output, diagnostic = self._evaluate(build, case["snapshot"])
                    matched = output == case[f"expected_v{version}"]
                    attempts.append({**diagnostic, "repetition": repetition, "output": output, "matched": matched})
                    if not matched:
                        failures.append("independent_projection_expectation_mismatch")
                except (ValidationFailure, ValueError, TypeError, RecursionError) as exc:
                    attempts.append(
                        {
                            "input_sha256": sha256(canonical_bytes(case["snapshot"])),
                            "repetition": repetition,
                            "failure": type(exc).__name__,
                            **getattr(exc, "process_diagnostic", {}),
                        }
                    )
                    failures.append("confined_projection_rejected")
        return self._receipt(
            "projection_validation",
            {
                "build_sha256": build,
                "source_sha256": manifest.source_sha256,
                "contract": manifest.contract,
                "status": "finite_projection_passed" if not failures else "rejected",
                "attempts": attempts,
                "failures": failures,
            },
        )

    def verify_validation(self, receipt: str, *, build: str) -> dict:
        report = self._verify(receipt, "projection_validation")
        manifest, _ = self.load(build)
        if (
            report.get("build_sha256") != build
            or report.get("source_sha256") != manifest.source_sha256
            or report.get("contract") != manifest.contract
        ):
            raise AuthorityDenied("projection validation source/contract receipt substitution")
        return report

    @contextmanager
    def _transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def _create(self) -> None:
        with self._transaction():
            self.db.execute(
                "CREATE TABLE projection_meta (id INTEGER PRIMARY KEY CHECK(id=1), scope TEXT NOT NULL, "
                "policy_sha256 TEXT NOT NULL, controller_sha256 TEXT NOT NULL, key_sha256 TEXT NOT NULL)"
            )
            self.db.execute(
                "INSERT INTO projection_meta VALUES (1,?,?,?,?)",
                ("synthetic_secretary_projection", self.policy_pin, self.controller_pin, sha256(self._key)),
            )
            self.db.execute(
                "CREATE TABLE projection_state (id INTEGER PRIMARY KEY CHECK(id=1), active_build TEXT, "
                "previous_build TEXT, generation INTEGER NOT NULL, phase TEXT NOT NULL, status TEXT NOT NULL)"
            )
            self.db.execute("INSERT INTO projection_state VALUES (1,NULL,NULL,0,'LEGACY','MANAGE_ONLY')")
            self.db.execute(
                "CREATE TABLE projection_releases (build_sha256 TEXT PRIMARY KEY, validation_receipt TEXT NOT NULL, "
                "source_sha256 TEXT NOT NULL, contract TEXT NOT NULL, status TEXT NOT NULL)"
            )
            self.db.execute(
                "CREATE TABLE projection_records (record_id TEXT PRIMARY KEY, snapshot_sha256 TEXT NOT NULL, "
                "build_sha256 TEXT NOT NULL, title TEXT NOT NULL, report_ids_json TEXT NOT NULL, "
                "receipt_sha256 TEXT NOT NULL)"
            )

    def _assert_sidecar(self) -> None:
        tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables != {"projection_meta", "projection_state", "projection_releases", "projection_records"}:
            raise AuthorityDenied("existing file is not an isolated synthetic projection sidecar")
        row = self.db.execute("SELECT * FROM projection_meta WHERE id=1").fetchone()
        if row is None or (row["scope"], row["policy_sha256"], row["controller_sha256"], row["key_sha256"]) != (
            "synthetic_secretary_projection",
            self.policy_pin,
            self.controller_pin,
            sha256(self._key),
        ):
            raise AuthorityDenied("sidecar scope/pins/authentication key changed")
        state = self.status()
        expected = {"record_id", "snapshot_sha256", "build_sha256", "title", "receipt_sha256"}
        if state["phase"] != "CONTRACTED":
            expected.add("report_ids_json")
        if state["phase"] != "LEGACY":
            expected.add("groups_json")
        if (
            state["phase"] not in {"LEGACY", "EXPANDED", "CONTRACTED"}
            or {row[1] for row in self.db.execute("PRAGMA table_info(projection_records)")} != expected
        ):
            raise AuthorityDenied("synthetic application schema/phase mismatch")

    def status(self) -> dict:
        return dict(self.db.execute("SELECT * FROM projection_state WHERE id=1").fetchone())

    def _admit(self, receipt: str) -> str:
        report = self._verify(receipt, "projection_validation")
        build = report["build_sha256"]
        self.verify_validation(receipt, build=build)
        if report["status"] != "finite_projection_passed":
            raise AuthorityDenied("independent passing projection evidence required")
        manifest, _ = self.load(build)
        prior = self.db.execute("SELECT * FROM projection_releases WHERE build_sha256=?", (build,)).fetchone()
        if prior is None:
            self.db.execute(
                "INSERT INTO projection_releases VALUES (?,?,?,?,'VALIDATED')",
                (build, receipt, manifest.source_sha256, manifest.contract),
            )
        elif prior["status"] != "VALIDATED":
            raise AuthorityDenied("retired incompatible projection release refused")
        return build

    def install_baseline(self, receipt: str) -> None:
        with self._transaction():
            build = self._admit(receipt)
            manifest, _ = self.load(build)
            if (
                manifest.source_sha256 != self.policy.baseline_source_sha256
                or manifest.contract != "secretary_projection/v1"
            ):
                raise AuthorityDenied("exact selected application baseline required")
            if self.status()["active_build"] is not None or self.status()["phase"] != "LEGACY":
                raise StaleState("projection baseline already established")
            self.db.execute(
                "UPDATE projection_state SET active_build=?,generation=1,status='RUNNING' WHERE id=1", (build,)
            )

    def expand(self) -> str:
        self._assert_pinned()
        self._assert_sidecar()
        with self._transaction():
            state = self.status()
            if state["phase"] != "LEGACY":
                raise StaleState("fixed expansion requires the legacy synthetic schema")
            rows = self._rows()
            for row in rows:
                self._verify_row(row)
            self.db.execute("ALTER TABLE projection_records ADD COLUMN groups_json TEXT")
            for row in rows:
                groups = [{"label": "Reports", "report_ids": json.loads(row["report_ids_json"])}]
                self.db.execute(
                    "UPDATE projection_records SET groups_json=? WHERE record_id=?",
                    (canonical_bytes(groups).decode(), row["record_id"]),
                )
            self.db.execute("UPDATE projection_state SET phase='EXPANDED' WHERE id=1")
        return self._receipt("projection_expand", {"rows_backfilled": len(rows), "phase": "EXPANDED"})

    def activate(self, receipt: str, *, expected_build: str, expected_generation: int) -> None:
        self._assert_sidecar()
        with self._transaction():
            state = self.status()
            if (
                state["active_build"] != expected_build
                or state["generation"] != expected_generation
                or state["phase"] not in {"EXPANDED", "CONTRACTED"}
            ):
                raise StaleState("projection activation baseline/generation/schema changed")
            build = self._admit(receipt)
            if self.load(build)[0].contract != "secretary_projection/v2":
                raise AuthorityDenied("new projection requires compatible v2 contract")
            previous = state["active_build"] if self._has_effect(state["active_build"]) else state["previous_build"]
            self.db.execute(
                "UPDATE projection_state SET active_build=?,previous_build=?,generation=generation+1,"
                "status='RUNNING' WHERE id=1",
                (build, previous),
            )

    def _rows(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM projection_records ORDER BY rowid LIMIT ?", (self.policy.maximum_records + 1,)
        ).fetchall()
        if len(rows) > self.policy.maximum_records:
            raise ValidationFailure("synthetic projection history bound exceeded")
        return [dict(row) for row in rows]

    def read(self, *, version: Literal[1, 2]) -> list[dict]:
        self._assert_sidecar()
        if (
            type(version) is not int
            or version not in {1, 2}
            or (version == 1 and self.status()["phase"] == "CONTRACTED")
        ):
            raise AuthorityDenied("retired legacy projection reader is incompatible")
        output = []
        for row in self._rows():
            self._verify_row(row)
            if version == 1:
                projection = {"title": row["title"], "report_ids": json.loads(row["report_ids_json"])}
            else:
                groups = row.get("groups_json")
                projection = {
                    "title": row["title"],
                    "groups": json.loads(groups)
                    if groups
                    else [{"label": "Reports", "report_ids": json.loads(row["report_ids_json"])}],
                }
            output.append({"record_id": row["record_id"], "projection": projection})
        return output

    def _verify_row(self, row: dict) -> dict:
        report = self._verify(row["receipt_sha256"], "projection_render")
        snapshot = next(
            (
                case["snapshot"]
                for case in self._corpus()["cases"]
                if sha256(canonical_bytes(case["snapshot"])) == row["snapshot_sha256"]
            ),
            None,
        )
        if snapshot is None or report.get("contract") not in {"secretary_projection/v1", "secretary_projection/v2"}:
            raise AuthorityDenied("projection effect snapshot/contract substitution")
        projection = report["projection"]
        ids = self._projection(projection, snapshot, int(report["contract"][-1]))
        expected = {
            "record_id": row["record_id"],
            "snapshot_sha256": row["snapshot_sha256"],
            "build_sha256": row["build_sha256"],
            "title": row["title"],
            "report_ids_json": canonical_bytes(ids).decode(),
        }
        if report.get("record") != expected or projection["title"] != row["title"]:
            raise AuthorityDenied("projection effect receipt differs from durable application record")
        release = self.db.execute(
            "SELECT * FROM projection_releases WHERE build_sha256=?", (row["build_sha256"],)
        ).fetchone()
        if (
            release is None
            or report.get("source_sha256") != release["source_sha256"]
            or report["contract"] != release["contract"]
        ):
            raise AuthorityDenied("projection effect source/contract provenance changed")
        if "report_ids_json" in row and row["report_ids_json"] != expected["report_ids_json"]:
            raise AuthorityDenied("projection legacy IDs differ from authenticated effect")
        if "groups_json" in row:
            groups = projection.get("groups")
            expected_groups = canonical_bytes(groups or [{"label": "Reports", "report_ids": ids}]).decode()
            if row["groups_json"] != expected_groups and not (groups is None and row["groups_json"] is None):
                raise AuthorityDenied("projection groups differ from authenticated effect/backfill")
            if self.status()["phase"] == "CONTRACTED" and row["groups_json"] is None:
                raise AuthorityDenied("contracted projection requires backfilled groups")
        return report

    def verify_render(self, receipt: str, *, record_id: str) -> dict:
        self._assert_sidecar()
        row = self.db.execute("SELECT * FROM projection_records WHERE record_id=?", (record_id,)).fetchone()
        if row is None or row["receipt_sha256"] != receipt:
            raise AuthorityDenied("projection receipt does not identify an actual durable effect")
        return self._verify_row(dict(row))

    def _has_effect(self, build: str | None) -> bool:
        found = False
        for row in self._rows():
            self._verify_row(row)
            found |= row["build_sha256"] == build
        return found

    def rollback(self, *, expected_build: str, expected_generation: int) -> str:
        self._assert_pinned()
        with self._transaction():
            state = self.status()
            if (state["active_build"], state["generation"]) != (expected_build, expected_generation):
                return "NEWER_RELEASE_PRESERVED"
            previous = state["previous_build"]
            release = self.db.execute("SELECT * FROM projection_releases WHERE build_sha256=?", (previous,)).fetchone()
            verified = False
            if previous and release and release["status"] == "VALIDATED":
                try:
                    verified = self._has_effect(previous)
                    self.verify_validation(release["validation_receipt"], build=previous)
                except (AuthorityDenied, ValidationFailure, ValueError, OSError):
                    verified = False
            if verified:
                if release["contract"] == "secretary_projection/v1" and state["phase"] == "CONTRACTED":
                    raise AuthorityDenied("legacy rollback refused after contract retirement")
                self.db.execute(
                    "UPDATE projection_state SET active_build=?,previous_build=NULL,generation=generation+1,"
                    "status='RUNNING' WHERE id=1",
                    (previous,),
                )
                return "ROLLED_BACK"
            self.db.execute("UPDATE projection_state SET generation=generation+1,status='MANAGE_ONLY' WHERE id=1")
            return "MANAGE_ONLY"

    def render(self, snapshot: dict) -> dict:
        self._assert_sidecar()
        self._snapshot(snapshot)
        digest = sha256(canonical_bytes(snapshot))
        if digest not in {sha256(canonical_bytes(case["snapshot"])) for case in self._corpus()["cases"]}:
            raise AuthorityDenied("offline rendering requires an exact independently pinned synthetic corpus input")
        state = self.status()
        if state["status"] != "RUNNING":
            raise AuthorityDenied("synthetic projection is management-only")
        release = self.db.execute(
            "SELECT * FROM projection_releases WHERE build_sha256=?", (state["active_build"],)
        ).fetchone()
        try:
            if release is None or release["status"] != "VALIDATED":
                raise AuthorityDenied("active application lacks retained independent evidence")
            self.verify_validation(release["validation_receipt"], build=state["active_build"])
            projection, diagnostic = self._evaluate(state["active_build"], snapshot)
        except (AuthorityDenied, ValidationFailure, ValueError, OSError, TypeError, RecursionError) as exc:
            receipt = self._receipt(
                "projection_render_failure",
                {
                    "build_sha256": state["active_build"],
                    "generation": state["generation"],
                    "input_sha256": digest,
                    "failure": type(exc).__name__,
                    "process": getattr(exc, "process_diagnostic", {}),
                },
            )
            recovery = self.rollback(expected_build=state["active_build"], expected_generation=state["generation"])
            return {"status": "REJECTED", "recovery": recovery, "receipt_sha256": receipt, **FLAGS}
        manifest, _ = self.load(state["active_build"])
        ids = self._projection(projection, snapshot, int(manifest.contract[-1]))
        record = {
            "record_id": secrets.token_hex(24),
            "snapshot_sha256": digest,
            "build_sha256": state["active_build"],
            "title": projection["title"],
            "report_ids_json": canonical_bytes(ids).decode(),
        }
        groups = projection.get("groups")
        receipt = self._receipt(
            "projection_render",
            {
                "record": record,
                "projection": projection,
                "generation": state["generation"],
                "process": diagnostic,
                "contract": manifest.contract,
                "source_sha256": manifest.source_sha256,
            },
        )
        with self._transaction():
            if self.status() != state:
                raise StaleState("application generation/schema changed during confined render")
            if len(self._rows()) >= self.policy.maximum_records:
                raise ValidationFailure("synthetic projection record quota exhausted")
            if state["phase"] == "CONTRACTED":
                self.db.execute(
                    "INSERT INTO projection_records VALUES (?,?,?,?,?,?)",
                    (
                        record["record_id"],
                        digest,
                        state["active_build"],
                        record["title"],
                        receipt,
                        canonical_bytes(groups).decode(),
                    ),
                )
            else:
                columns = "record_id,snapshot_sha256,build_sha256,title,report_ids_json,receipt_sha256"
                values = (*record.values(), receipt)
                if state["phase"] == "EXPANDED":
                    columns += ",groups_json"
                    values += (canonical_bytes(groups).decode() if groups else None,)
                self.db.execute(
                    f"INSERT INTO projection_records ({columns}) VALUES ({','.join('?' for _ in values)})", values
                )
        return {
            "status": "RENDERED",
            "record_id": record["record_id"],
            "projection": projection,
            "receipt_sha256": receipt,
            **FLAGS,
        }

    def render_secretary(self, secretary: Secretary, portfolio_id: str) -> dict:
        return self.render(secretary_snapshot(secretary, portfolio_id))

    def contract_clone(self) -> str:
        """Fixed destructive rehearsal only on a new disposable clone, never self.db."""
        self._assert_pinned()
        self._assert_sidecar()
        state = self.status()
        if state["phase"] != "EXPANDED" or self.load(state["active_build"])[0].contract != "secretary_projection/v2":
            raise AuthorityDenied("contract rehearsal requires expanded v2 active state")
        self.read(version=2)
        clone_path = self.store.root / ("contract-rehearsal-" + secrets.token_hex(16) + ".sqlite")
        fd = os.open(clone_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        clone = sqlite3.connect(clone_path, isolation_level=None)
        try:
            self.db.backup(clone)
            clone.execute("BEGIN IMMEDIATE")
            rows = clone.execute("SELECT record_id,report_ids_json,groups_json FROM projection_records").fetchall()
            for record_id, legacy, groups in rows:
                if groups is None:
                    clone.execute(
                        "UPDATE projection_records SET groups_json=? WHERE record_id=?",
                        (canonical_bytes([{"label": "Reports", "report_ids": json.loads(legacy)}]).decode(), record_id),
                    )
            clone.execute(
                "CREATE TABLE contracted_records (record_id TEXT PRIMARY KEY, snapshot_sha256 TEXT NOT NULL, "
                "build_sha256 TEXT NOT NULL, title TEXT NOT NULL, receipt_sha256 TEXT NOT NULL, "
                "groups_json TEXT NOT NULL)"
            )
            clone.execute(
                "INSERT INTO contracted_records SELECT record_id,snapshot_sha256,build_sha256,title,"
                "receipt_sha256,groups_json FROM projection_records"
            )
            clone.execute("DROP TABLE projection_records")
            clone.execute("ALTER TABLE contracted_records RENAME TO projection_records")
            clone.execute("UPDATE projection_releases SET status='RETIRED' WHERE contract='secretary_projection/v1'")
            clone.execute("UPDATE projection_state SET phase='CONTRACTED',previous_build=NULL WHERE id=1")
            clone.execute("COMMIT")
            if clone.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValidationFailure("contract clone integrity failed")
            retained = clone.execute("SELECT COUNT(*) FROM projection_records").fetchone()[0]
        finally:
            clone.close()
        # No restore or rename over authoritative state occurs. Reopen the clone
        # under the same trusted controller to prove reader/release compatibility.
        rehearsal = object.__new__(type(self))
        rehearsal.__dict__ = self.__dict__.copy()
        rehearsal.path = clone_path
        rehearsal.db = sqlite3.connect(clone_path, isolation_level=None)
        rehearsal.db.row_factory = sqlite3.Row
        try:
            rehearsal._assert_sidecar()
            projections = rehearsal.read(version=2)
            legacy_refused = False
            try:
                rehearsal.read(version=1)
            except AuthorityDenied:
                legacy_refused = True
            retired = [
                dict(row) for row in rehearsal.db.execute("SELECT * FROM projection_releases WHERE status='RETIRED'")
            ]
            for release in retired:
                try:
                    rehearsal._admit(release["validation_receipt"])
                except AuthorityDenied:
                    continue
                raise AuthorityDenied("contract clone admitted a retired rollback baseline")
            rendered = rehearsal.render(rehearsal._corpus()["cases"][0]["snapshot"])
            if rendered["status"] != "RENDERED":
                raise ValidationFailure("contract clone failed actual v2 application render/write")
            rehearsal.verify_render(rendered["receipt_sha256"], record_id=rendered["record_id"])
        finally:
            rehearsal.close()
        if self.status() != state:
            raise StaleState("original application pointer changed during clone rehearsal")
        return self._receipt(
            "projection_contract_clone",
            {
                "status": "rehearsed",
                "clone_sha256": sha256(clone_path.read_bytes()),
                "rows_retained": retained,
                "v2_projection_sha256": sha256(canonical_bytes(projections)),
                "v2_render_receipt_sha256": rendered["receipt_sha256"],
                "legacy_reader_refused": legacy_refused,
                "retired_releases_refused": len(retired),
                "original_phase": state["phase"],
                "original_generation": state["generation"],
            },
        )

    def close(self) -> None:
        self.db.close()
