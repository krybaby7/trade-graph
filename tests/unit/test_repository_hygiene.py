"""Publication checks inspect staged content without printing credentials."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_repository_hygiene.py"
_SPEC = importlib.util.spec_from_file_location("repository_hygiene", _SCRIPT)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.mark.parametrize("path", [".env", "runtime/journal.json", ".private/owner.json", "id_ed25519", "data.sqlite"])
def test_publication_rejects_private_files_even_when_force_staged(tmp_path, path):
    _git(tmp_path, "init")
    file = tmp_path / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("synthetic fixture")
    _git(tmp_path, "add", "--", path)
    assert _MODULE.scan(tmp_path, staged=True) == [(path, 0, "private runtime or credential file")]


def test_staged_scanner_rejects_index_secret_after_working_copy_was_scrubbed(tmp_path):
    _git(tmp_path, "init")
    fixture = "sk-" + "A1b2C3d4" * 8
    file = tmp_path / "config.txt"
    file.write_text("label\n" + fixture)
    _git(tmp_path, "add", "config.txt")
    file.write_text("scrubbed")
    assert _MODULE.scan(tmp_path) == []
    result = _MODULE.scan(tmp_path, staged=True)
    assert result == [("config.txt", 2, "provider credential")]
    assert fixture not in repr(result)


def test_public_example_and_synthetic_placeholder_are_allowed(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / ".env.example").write_text("OPENAI_API_KEY=\n")
    (tmp_path / "fixture.txt").write_text("sk-test-fixture-only")
    _git(tmp_path, "add", ".env.example", "fixture.txt")
    assert _MODULE.scan(tmp_path, staged=True) == []


def test_artifact_scan_rejects_credential_bytes_without_printing_matches(tmp_path):
    fixture = "sk-" + "Synthet1c" * 8
    result = tmp_path / "results.xml"
    result.write_text("<testsuite>\n" + fixture + "</testsuite>")
    violations = _MODULE.scan_artifacts([result])
    assert violations == [("results.xml", 2, "provider credential")]
    assert fixture not in repr(violations)


@pytest.mark.parametrize("name", ["owner-session.json", "journal.sqlite", "masked-report.json"])
def test_artifact_scan_rejects_private_runtime_files_even_with_changed_extensions(tmp_path, name):
    result = tmp_path / name
    result.write_bytes(b"SQLite format 3\x00" + b"synthetic runtime")
    assert _MODULE.scan_artifacts([result])


def test_artifact_scan_requires_exact_existing_file_and_rejects_symlink(tmp_path):
    missing = tmp_path / "absent.xml"
    assert _MODULE.scan_artifacts([missing]) == [("absent.xml", 0, "unreadable artifact")]
    result = tmp_path / "evidence.json"
    result.write_text('{"scope": "synthetic", "passed": true}')
    link = tmp_path / "linked.json"
    link.symlink_to(result)
    assert _MODULE.scan_artifacts([link]) == [("linked.json", 0, "private runtime or credential artifact")]
    assert _MODULE.scan_artifacts([result]) == []
