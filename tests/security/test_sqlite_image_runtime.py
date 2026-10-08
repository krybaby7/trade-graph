"""The image admits exact SQLite source and verifies Python's loaded library."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def builder():
    spec = importlib.util.spec_from_file_location("sqlite_image_builder", ROOT / "scripts/build_protected_image.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture(tmp_path):
    module = builder()
    policy = json.loads((ROOT / "deploy/protected-image-inputs.json").read_bytes())["sqlite"]
    source = (f'#define SQLITE_VERSION "{policy["version"]}"\n'
              f'#define SQLITE_SOURCE_ID "{policy["source_id"]}"\n').encode()
    policy["amalgamation_sha3_256"] = hashlib.sha3_256(source).hexdigest()
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as target:
        for name, value in {"sqlite3.c": source, "sqlite3.h": source, "shell.c": b"shell",
                            "sqlite3ext.h": b"extension"}.items():
            target.writestr("sqlite-amalgamation-3530400/" + name, value)
    policy["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    policy["archive_sha3_256"] = hashlib.sha3_256(archive.read_bytes()).hexdigest()
    context = tmp_path / "context"
    context.mkdir()
    return module, policy, source, archive, context


def test_only_hash_verified_source_is_staged_with_complete_identity(tmp_path):
    module, policy, source, archive, context = fixture(tmp_path)
    evidence = module.stage_sqlite_source(context, {"sqlite": policy}, archive=archive)
    assert set(path.name for path in (context / "sqlite-source").iterdir()) == {"sqlite3.c", "sqlite3.h"}
    assert (context / "sqlite-source/sqlite3.c").read_bytes() == source
    assert evidence["sqlite_source"] == policy
    assert evidence["sqlite_source_sha256"] == module.directory_sha256(context / "sqlite-source")
    assert evidence["sqlite_runtime_probe_sha256"] == module.file_sha256(context / "verify-sqlite-runtime.py")


@pytest.mark.parametrize("attack", ["archive_hash", "c_hash", "version", "source_id", "compiler_tag",
                                   "external_url", "extra_field", "linked_archive", "traversal", "duplicate"])
def test_changed_or_unreviewed_sqlite_inputs_fail_before_compilation(tmp_path, attack):
    module, policy, source, archive, context = fixture(tmp_path)
    if attack == "archive_hash":
        policy["archive_sha256"] = "0" * 64
    elif attack == "c_hash":
        policy["amalgamation_sha3_256"] = "0" * 64
    elif attack == "version":
        policy["version"] = "3.53.3"
    elif attack == "source_id":
        policy["source_id"] = "2026-07-24 19:02:57 " + "0" * 64
    elif attack == "compiler_tag":
        policy["compiler_image"] = "python:latest"
    elif attack == "external_url":
        policy["archive_url"] = "https://unreviewed.invalid/sqlite-amalgamation-3530400.zip"
    elif attack == "extra_field":
        policy["extra"] = True
    elif attack == "linked_archive":
        alias = tmp_path / "linked.zip"
        alias.symlink_to(archive)
        archive = alias
    else:
        with zipfile.ZipFile(archive, "a") as target:
            target.writestr("../sqlite3.c" if attack == "traversal" else
                            "sqlite-amalgamation-3530400/sqlite3.c", source)
        policy["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
        policy["archive_sha3_256"] = hashlib.sha3_256(archive.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        module.stage_sqlite_source(context, {"sqlite": policy}, archive=archive)
    assert not (context / "sqlite-source").exists()


def test_acquisition_is_bounded_and_official_hashes_are_required(tmp_path, monkeypatch):
    module, policy, source, archive, context = fixture(tmp_path)
    calls = []

    class Response(io.BytesIO):
        def geturl(self):
            return policy["archive_url"]

    def acquire(url, **kwargs):
        calls.append((url, kwargs))
        return Response(archive.read_bytes())

    monkeypatch.setattr(module.urllib.request, "urlopen", acquire)
    module.stage_sqlite_source(context, {"sqlite": policy})
    assert calls[0][0] == policy["archive_url"]
    assert calls[0][1]["timeout"] <= 60


def test_runtime_probe_rejects_old_or_differently_mapped_sqlite(tmp_path):
    module = builder()
    document = tmp_path / "expected.json"
    document.write_text(json.dumps({"sqlite_source": {
        "version": "0.0.0", "source_id": "unreviewed-library"}}))
    result = subprocess.run([sys.executable, "-I", "-B", "-c", module.SQLITE_RUNTIME_PROBE, str(document)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert "Python did not load the reviewed SQLite runtime" in result.stderr
    dockerfile = (ROOT / "deploy/Dockerfile.protected").read_text()
    assert "FROM ${SQLITE_COMPILER_IMAGE} AS sqlite_builder" in dockerfile
    assert "COPY --from=sqlite_builder /built-sqlite/libsqlite3.so.0 /usr/local/lib/libsqlite3.so.0" in dockerfile
    assert dockerfile.index("ldconfig") < dockerfile.index("verify-sqlite-runtime.py /build-inputs.json --record")
    assert (dockerfile.index("verify-sqlite-runtime.py /build-inputs.json --record")
            < dockerfile.index("deployment_image seal"))
