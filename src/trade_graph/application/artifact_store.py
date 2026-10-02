"""Immutable, bounded data bundles; consumption never rereads an Engineer worktree."""

from __future__ import annotations

import json

from trade_graph.adapters.engineering.artifact_files import (
    MAX_FILE_BYTES,
    MAX_TREE_BYTES,
    MAX_TREE_FILES,
    content_hash,
    manifest,
)
from trade_graph.adapters.engineering.artifact_policy import artifact_class, validate
from trade_graph.adapters.persistence.db import Database, atomic
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import ValidationFailure


class ArtifactStore:
    def __init__(self, database: Database, clock: Clock) -> None:
        self.database, self.clock = database, clock

    @staticmethod
    def check(files: dict[str, str], *, require_valid: bool = True) -> None:
        if (not isinstance(files, dict) or len(files) > MAX_TREE_FILES
                or any(type(k) is not str or type(v) is not str for k, v in files.items())
                or any(len(v.encode()) > MAX_FILE_BYTES for v in files.values())
                or sum(len(v.encode()) for v in files.values()) > MAX_TREE_BYTES):
            raise ValidationFailure("invalid bounded artifact bundle")
        try:
            for path in files:
                artifact_class(path)
        except ValueError as exc:
            raise ValidationFailure("unregistered artifact in bundle") from exc
        if require_valid and (failures := validate(files)):
            raise ValidationFailure("artifact grammar: " + "; ".join(failures))

    @atomic
    def put(self, files: dict[str, str], *, require_valid: bool = True) -> str:
        self.check(files, require_valid=require_valid)
        artifact_hash, proof = content_hash(files), manifest(files)
        prior = self.database.execute(
            "SELECT * FROM artifact_bundles WHERE artifact_hash = ?", (artifact_hash,),
        ).fetchone()
        if prior:
            existing = self.get(artifact_hash, require_valid=False)
            if existing["files"] != files or existing["manifest"] != proof:
                raise ValidationFailure("artifact hash collision with a different manifest")
        else:
            self.database.execute(
                "INSERT INTO artifact_bundles VALUES (?, ?, ?, ?)",
                (artifact_hash, json.dumps(files, sort_keys=True), json.dumps(proof, sort_keys=True),
                 utc_iso(self.clock.now())),
            )
        return artifact_hash

    def get(self, artifact_hash: str, *, require_valid: bool = True) -> dict:
        row = self.database.execute(
            "SELECT * FROM artifact_bundles WHERE artifact_hash = ?", (artifact_hash,),
        ).fetchone()
        if row is None:
            raise ValidationFailure("missing registered artifact bytes; bootstrap the real baseline")
        try:
            files, proof = json.loads(row["files_json"]), json.loads(row["manifest_json"])
            self.check(files, require_valid=require_valid)
            if content_hash(files) != artifact_hash or manifest(files) != proof:
                raise ValidationFailure("artifact bytes/manifest identity mismatch")
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            raise ValidationFailure("corrupt artifact bundle") from exc
        return {"artifact_hash": artifact_hash, "files": files, "manifest": proof}
