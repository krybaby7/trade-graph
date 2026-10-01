"""Acceptance evidence must come from executed outcomes, including adverse cases."""

import json
import subprocess
import sys

import pytest
from scripts import verify_offline_acceptance as harness


def catalogue(*cases):
    return {"cases": list(cases)}


def case(case_id="A01", coverage="full", *nodes):
    return {
        "id": case_id,
        "coverage": coverage,
        "test_node_ids": list(nodes),
        "covered_subrequirements": ["synthetic assertion"] if nodes else [],
        "uncovered_subrequirements": [] if coverage == "full" else ["uncovered requirement"],
    }


def record(node="tests/example.py::test_example", outcome="passed"):
    return {"node_id": node, "outcome": outcome}


def test_real_junit_preserves_parameters_failure_skip_xfail_xpass_and_teardown_errors(tmp_path):
    # Execute an adverse synthetic mini-suite, not the catalogue's project tests.
    runtime = tmp_path / "artifacts"
    runtime.mkdir()
    (runtime / "config").mkdir()
    (runtime / "tmp").mkdir()
    source = tmp_path / "test_junit_fixture.py"
    source.write_text("""import pytest
@pytest.mark.parametrize("value", [1, 2], ids=["one.dot", "two"])
def test_parameter(value):
    assert value > 0
def test_failure():
    assert False, "deliberate fixture failure"
def test_skip():
    pytest.skip("deliberate fixture skip")
@pytest.mark.xfail(reason="expected fixture failure")
def test_xfail():
    assert False
@pytest.mark.xfail(strict=True, reason="unexpected fixture success")
def test_xpass():
    pass
@pytest.fixture
def broken_teardown():
    yield
    raise RuntimeError("deliberate teardown failure")
def test_call_and_teardown(broken_teardown):
    assert False, "call failure as well"
""")
    junit = runtime / "junit.xml"
    collection = runtime / "collection.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(source),
            "-p",
            "scripts.verify_offline_acceptance",
            "-p",
            "no:cacheprovider",
            "-o",
            "junit_family=xunit1",
            "--junitxml",
            str(junit),
            "--offline-collection-out",
            str(collection),
            "--basetemp",
            str(runtime / "pytest"),
        ],
        cwd=harness.ROOT,
        env=harness.offline_env(runtime),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 1, completed.stdout + completed.stderr
    records, issues, declared = harness.parse_junit(junit)
    assert issues == []
    assert declared[0]["failures"] == "3"
    assert harness.counts(records) == {"total": 8, "passed": 2, "failed": 3, "error": 1, "skipped": 2}
    collected = json.loads(collection.read_text())
    assert len(collected) == 7
    assert any(node.endswith("test_parameter[one.dot]") for node in collected)
    assert {r["node_id"] for r in records} == set(collected)
    teardown = [r for r in records if r["node_id"].endswith("test_call_and_teardown")]
    assert {r["outcome"] for r in teardown} == {"failed", "error"}
    assert any("deliberate teardown failure" in d["text"] for r in teardown for d in r["details"])


def test_partial_and_pending_criteria_cannot_become_accepted_from_passing_tests():
    node = "tests/example.py::test_example"
    data = catalogue(case("A01", "full", node), case("A03", "partial", node), case("A14", "pending"))
    result = harness.build_results(data, [node], [record(node)], 0, [])
    assert result["suite_status"] == "passed"
    assert result["runner_exit_code"] == 1
    assert result["tests_passed"] is True
    assert result["gate_complete"] is False
    assert result["acceptance_complete"] is False
    assert result["counts"]["total"] == 1  # Shared evidence executes/counts once globally.
    assert [c["acceptance_status"] for c in result["cases"]] == ["passed", "partial", "pending"]
    assert [c["test_outcome"] for c in result["cases"]] == ["passed", "passed", "not_run"]


@pytest.mark.parametrize(
    "outcome,exit_code,status",
    [
        ("failed", 1, "failed"),
        ("error", 1, "failed"),
        ("skipped", 0, "incomplete"),
        ("passed", 2, "error"),
        ("passed", 3, "error"),
        ("passed", 4, "error"),
        ("passed", 5, "error"),
        ("passed", -9, "error"),
        ("passed", None, "error"),
    ],
)
def test_exit_codes_and_adverse_outcomes_never_certify_acceptance(outcome, exit_code, status):
    node = "tests/example.py::test_example"
    result = harness.build_results(catalogue(case("A01", "full", node)), [node], [record(node, outcome)], exit_code, [])
    assert result["pytest_exit_code"] == exit_code
    assert result["suite_status"] == status
    assert result["tests_passed"] is False
    assert result["gate_complete"] is False
    assert result["runner_exit_code"] != 0
    assert result["cases"][0]["acceptance_status"] == "unverified"


def test_parameter_variants_are_exact_and_all_must_have_outcomes():
    selector = "tests/example.py::test_example"
    collected = [selector + "[a]", selector + "[b]"]
    result = harness.build_results(catalogue(case("A01", "full", selector)), collected, [record(collected[0])], 0, [])
    assert result["suite_status"] == "error"
    assert result["cases"][0]["test_outcome"] == "incomplete"
    assert result["missing_outcome_node_ids"] == [collected[1]]
    assert harness.matches(selector, selector + "_other") is False
    assert harness.matches(selector + "[a]", selector + "[b]") is False


def test_successful_test_does_not_mask_an_uncollected_selector():
    node = "tests/example.py::test_example"
    missing = "tests/example.py::test_missing"
    result = harness.build_results(catalogue(case("A01", "full", node, missing)), [node], [record(node)], 0, [])
    assert result["cases"][0]["test_outcome"] == "incomplete"
    assert result["cases"][0]["missing_selectors"] == [missing]
    assert result["acceptance_complete"] is False


def test_unexpected_evidence_and_no_collection_cannot_pass():
    node = "tests/example.py::test_example"
    result = harness.build_results(catalogue(case("A01", "full", node)), [], [record(node)], 0, [])
    assert result["suite_status"] == "error"
    assert result["unexpected_outcome_node_ids"] == [node]
    assert result["cases"][0]["evidence"] == []


@pytest.mark.parametrize(
    "content",
    [
        None,
        "<broken",
        "<testsuites/>",
        "<unrecognized/>",
        '<testsuites><testsuite><testcase name="test_no_provenance"/></testsuite></testsuites>',
    ],
)
def test_missing_empty_malformed_and_unattributable_junit_are_errors(tmp_path, content):
    path = tmp_path / "junit.xml"
    if content is not None:
        path.write_text(content)
    records, issues, _ = harness.parse_junit(path)
    assert issues
    if content and "test_no_provenance" in content:
        assert records[0]["node_id"] is None


def test_runtime_environment_excludes_credentials_config_proxies_and_pytest_overrides(tmp_path, monkeypatch):
    for name in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "KRAKEN_API_SECRET",
        "PYTEST_ADDOPTS",
        "PYTHONPATH",
        "HTTPS_PROXY",
        "LANGSMITH_API_KEY",
    ):
        monkeypatch.setenv(name, "synthetic-sensitive-value")
    environment = harness.offline_env(tmp_path)
    assert "synthetic-sensitive-value" not in environment.values()
    assert "HOME" not in environment
    assert environment["XDG_CONFIG_HOME"] == str(tmp_path / "config")
    assert environment["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"


def test_artifact_destination_rejects_checkout_and_symlink_into_it(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    link = tmp_path / "outside"
    link.symlink_to(root, target_is_directory=True)
    for destination in (root, root / "report.json", link / "report.json"):
        with pytest.raises(ValueError, match="outside"):
            harness.require_external(destination, root)
    assert harness.require_external(tmp_path / "report.json", root) == tmp_path / "report.json"


def test_catalogue_is_complete_and_future_ids_are_not_implicitly_runnable():
    data = harness.load_catalogue(harness.CATALOGUE)
    assert [c["id"] for c in data["cases"]] == harness.CASE_IDS
    assert {b["id"] for b in data["pending_boundaries"]} == {"paid_provider_verification", "T17", "T18", "live", "T21"}
    selected = harness.selected_nodes(data)
    for entry in data["coordination"]:
        assert not set(entry["future_test_node_ids"]) & set(selected)


def test_all_applicable_cases_must_be_full_even_when_pytest_exits_zero():
    node = "tests/example.py::test_example"
    for incomplete in (case("A14", "pending"), case("A03", "partial", node)):
        result = harness.build_results(catalogue(case("A01", "full", node), incomplete), [node], [record(node)], 0, [])
        assert result["tests_passed"] is True
        assert result["gate_complete"] is False
        assert result["gate_status"] == "incomplete"
        assert result["runner_exit_code"] == 1


def test_completed_failing_session_preserves_independent_case_outcomes():
    passing = "tests/example.py::test_pass"
    failing = "tests/example.py::test_fail"
    data = catalogue(case("A01", "full", passing), case("A02", "full", failing))
    result = harness.build_results(data, [passing, failing], [record(passing), record(failing, "failed")], 1, [])
    assert [c["test_outcome"] for c in result["cases"]] == ["passed", "failed"]
    assert [c["acceptance_status"] for c in result["cases"]] == ["passed", "unverified"]
    assert result["tests_passed"] is result["gate_complete"] is False


def test_complete_local_coverage_passes_without_certifying_pending_boundaries():
    node = "tests/example.py::test_example"
    data = catalogue(case("A01", "full", node))
    data["pending_boundaries"] = [{"id": "live", "status": "pending"}]
    result = harness.build_results(data, [node], [record(node)], 0, [])
    assert result["tests_passed"] is True
    assert result["gate_complete"] is True
    assert result["runner_exit_code"] == 0
    assert data["pending_boundaries"][0]["status"] == "pending"


@pytest.mark.parametrize(
    "body,expected_exit,expected_status",
    [
        ("assert True", 0, "passed"),
        ("assert False, 'synthetic failure'", 1, "failed"),
        ("pytest.skip('synthetic skip')", 1, "incomplete"),
    ],
)
def test_runnable_harness_emits_bound_report_from_an_actual_pytest_process(
    tmp_path,
    monkeypatch,
    capsys,
    body,
    expected_exit,
    expected_status,
):
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "scripts/verify_offline_acceptance.py").write_bytes(
        (harness.ROOT / "scripts/verify_offline_acceptance.py").read_bytes()
    )
    (root / "uv.lock").write_text("synthetic lock")
    (root / "tests/test_fixture.py").write_text(f"import pytest\ndef test_example():\n    {body}\n")
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    data = catalogue(case("A01", "full", "tests/test_fixture.py::test_example"))
    data["pending_boundaries"] = [{"id": "live", "status": "pending"}]
    source = root / "catalogue.json"
    source.write_text(json.dumps(data))
    # Loader completeness is tested separately; this mini-run isolates command,
    # plugin, real JUnit parsing, report persistence and process return semantics.
    monkeypatch.setattr(harness, "ROOT", root)
    monkeypatch.setattr(harness, "CATALOGUE", source)
    monkeypatch.setattr(harness, "load_catalogue", lambda path: data)
    monkeypatch.setattr(harness, "identity", lambda root: {"commit": "fixture", "uv_lock_sha256": "fixture"})
    destination = tmp_path / "report.json"
    assert harness.main(["--report", str(destination)]) == expected_exit
    report = json.loads(destination.read_text())
    metadata = json.loads(capsys.readouterr().out)
    assert metadata["report"] == str(destination)
    assert metadata["suite_status"] == report["suite_status"]
    assert metadata["gate_complete"] == report["gate_complete"]
    assert metadata["counts"] == report["counts"]
    assert "cases" not in metadata and "evidence" not in metadata
    assert report["suite_status"] == expected_status
    assert report["pytest_exit_code"] == (1 if expected_status == "failed" else 0)
    assert report["counts"]["total"] == 1
    assert report["cases"][0]["actual_test_node_ids"] == ["tests/test_fixture.py::test_example"]
    assert report["pending_boundaries"] == data["pending_boundaries"]
    assert report["artifacts"]["junit_sha256"]


def test_catalogue_rejects_invented_full_coverage_and_unsafe_selectors(tmp_path):
    data = json.loads(harness.CATALOGUE.read_text())
    path = tmp_path / "catalogue.json"
    data["cases"][0]["uncovered_subrequirements"] = ["uncovered"]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="contradicts"):
        harness.load_catalogue(path)
    data["cases"][0]["uncovered_subrequirements"] = []
    data["cases"][0]["test_node_ids"] = ["tests/../src/code.py::test_fake"]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="pytest node ID"):
        harness.load_catalogue(path)


def test_commit_tree_lock_and_working_bytes_are_bound_to_evidence(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init")
    git("config", "user.name", "Synthetic fixture")
    git("config", "user.email", "fixture@example.invalid")
    lock = tmp_path / "uv.lock"
    lock.write_text("synthetic locked bytes")
    git("add", "uv.lock")
    git("commit", "-m", "synthetic fixture")
    before = harness.identity(tmp_path)
    assert len(before["commit"]) == len(before["tree"]) == 40
    assert before["working_tree_status"] == ""
    lock.write_text("changed locked bytes")
    after = harness.identity(tmp_path)
    assert before["commit"] == after["commit"] and before["tree"] == after["tree"]
    assert before["uv_lock_sha256"] != after["uv_lock_sha256"]
    assert before["working_files_sha256"] != after["working_files_sha256"]
    assert after["working_tree_status"]
