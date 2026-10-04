"""Lossless private runtime capture storage with explicit expansion bounds."""

from __future__ import annotations

import zlib
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal

from pydantic import Field, field_validator

from trade_graph.evaluation_contracts import EvaluationContract, Fingerprint, Reference


class RuntimeHistoryPolicy(EvaluationContract):
    """Collector-pinned deployment policy, declared before the first binding."""

    schema_version: Literal[1] = 1
    deployment_id: Reference
    database_identity: tuple[int, int]
    declared_at: datetime
    storage: Literal["inline", "lossless_zlib"]
    maximum_records: int = Field(strict=True, gt=0)
    maximum_snapshots: int = Field(strict=True, gt=0)
    maximum_stored_bytes: int = Field(strict=True, gt=0)
    maximum_expanded_bytes: int = Field(strict=True, gt=0)
    maximum_record_bytes: int = Field(strict=True, gt=0)
    maximum_snapshot_record_bytes: int = Field(strict=True, gt=0)

    @field_validator("declared_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("history policy time requires a timezone")
        return value.astimezone(UTC)


class ArchivedRuntimeCapture(EvaluationContract):
    """Content-addressed storage manifest; never substitutes for a capture."""

    schema_version: Literal[1] = 1
    storage: Literal["lossless_zlib"] = "lossless_zlib"
    capture_sha256: Fingerprint
    expanded_sha256: Fingerprint
    compressed_sha256: Fingerprint
    expanded_bytes: int = Field(strict=True, gt=0)
    compressed_bytes: int = Field(strict=True, gt=0)


def check_archived_history(connection, policy: RuntimeHistoryPolicy, *, extra_stored_bytes: int = 0,
                          extra_expanded_bytes: int = 0, extra_records: int = 0, extra_snapshots: int = 0):
    """Preflight all retained capture, report and blob storage without payload reads."""
    for kinds, maximum, extra in (
        (("runtime_capture", "runtime_binding"), policy.maximum_records, extra_records),
        (("snapshot",), policy.maximum_snapshots, extra_snapshots),
    ):
        placeholders = ",".join("?" for _ in kinds)
        count = connection.execute(
            f"SELECT COUNT(*) FROM (SELECT 1 FROM evaluation_records WHERE kind IN ({placeholders}) LIMIT ?)",
            (*kinds, maximum + 1),
        ).fetchone()[0]
        if count + extra > maximum:
            raise ValueError("runtime archived capture/report history exceeds verification bounds")
    blob_count = connection.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM runtime_capture_blobs LIMIT ?)", (policy.maximum_records + 1,),
    ).fetchone()[0]
    if blob_count > policy.maximum_records:
        raise ValueError("runtime archived capture object history exceeds verification bounds")
    # Validate expansion metadata before SUM so SQLite integer arithmetic cannot
    # be overflowed by false manifests. A stream is checked again while decoding.
    if connection.execute(
        "SELECT 1 FROM evaluation_records WHERE "
        "(kind='runtime_capture' AND (length(CAST(document_json AS BLOB)) > 4096 OR "
        "json_extract(document_json,'$.storage') IS NOT 'lossless_zlib' OR "
        "json_type(document_json,'$.expanded_bytes') IS NOT 'integer' OR "
        "json_extract(document_json,'$.expanded_bytes') <= 0 OR "
        "json_extract(document_json,'$.expanded_bytes') > ?)) OR "
        "(kind='snapshot' AND length(CAST(document_json AS BLOB)) > ?) OR "
        "(kind='runtime_binding' AND length(CAST(document_json AS BLOB)) > ?) LIMIT 1",
        (policy.maximum_record_bytes, policy.maximum_snapshot_record_bytes, policy.maximum_record_bytes),
    ).fetchone():
        raise ValueError("runtime archived history metadata exceeds verification byte bounds")
    documents = connection.execute(
        "SELECT COALESCE(SUM(length(CAST(document_json AS BLOB))),0),"
        "COALESCE(SUM(CASE WHEN kind='runtime_capture' THEN json_extract(document_json,'$.expanded_bytes') "
        "ELSE length(CAST(document_json AS BLOB)) END),0) FROM evaluation_records "
        "WHERE kind IN ('runtime_capture','runtime_binding','runtime_history_policy','snapshot')",
    ).fetchone()
    blobs = connection.execute(
        "SELECT COALESCE(SUM(length(compressed_data)),0),COALESCE(MAX(length(compressed_data)),0) "
        "FROM runtime_capture_blobs",
    ).fetchone()
    if (documents[0] + blobs[0] + extra_stored_bytes > policy.maximum_stored_bytes
            or documents[1] + extra_expanded_bytes > policy.maximum_expanded_bytes
            or blobs[1] > policy.maximum_record_bytes):
        raise ValueError("runtime archived capture/report history exceeds verification byte bounds")


def archive_capture(raw: str, capture_sha256: str, *, maximum_bytes: int):
    encoded = raw.encode("utf-8")
    if len(encoded) > maximum_bytes:
        raise ValueError("runtime retained capture exceeds verification byte bounds")
    compressed = zlib.compress(encoded)
    return ArchivedRuntimeCapture(
        capture_sha256=capture_sha256,
        expanded_sha256=sha256(encoded).hexdigest(),
        compressed_sha256=sha256(compressed).hexdigest(),
        expanded_bytes=len(encoded), compressed_bytes=len(compressed),
    ), compressed


def expand_capture(manifest: ArchivedRuntimeCapture, compressed: bytes, *, maximum_bytes: int) -> str:
    """Bound output during decompression, including dishonest stored metadata."""
    if (manifest.expanded_bytes > maximum_bytes or len(compressed) != manifest.compressed_bytes
            or sha256(compressed).hexdigest() != manifest.compressed_sha256):
        raise ValueError("runtime archived capture storage digest or byte bounds changed")
    decoder = zlib.decompressobj()
    try:
        expanded = decoder.decompress(compressed, manifest.expanded_bytes + 1)
    except zlib.error as error:
        raise ValueError("runtime archived capture compression is invalid") from error
    if (len(expanded) != manifest.expanded_bytes or not decoder.eof
            or decoder.unconsumed_tail or decoder.unused_data
            or sha256(expanded).hexdigest() != manifest.expanded_sha256):
        raise ValueError("runtime archived capture expansion digest or byte bounds changed")
    try:
        return expanded.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("runtime archived capture contains invalid document bytes") from error
