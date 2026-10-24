"""An explicit development switch lets the checker run unconfined where seccomp does not exist.

It never weakens a platform that has seccomp, it is off unless asked for, and every record it
produces says "unconfined" so nobody can mistake a development run for the confined one.
"""

import json
import sys

import pytest
from tests.integration.test_engineer import POLICY
from tests.integration.test_engineer_isolation import probe

from trade_graph.adapters.engineering.artifact_files import ensure_directory, write_file
from trade_graph.adapters.engineering.process import run_bounded
from trade_graph.adapters.engineering.provenance import CHECKS_FILE
from trade_graph.adapters.engineering.runner import EngineerRunner
from trade_graph.adapters.engineering.sandbox import (
    CHILD_ENV,
    CONFINED_MECHANISM,
    MEMORY_BYTES,
    UNCONFINED_ENV,
    UNCONFINED_MECHANISM,
    UNCONFINED_VALUE,
    development_unconfined_requested,
    evidence_acceptable,
    seccomp_available,
)
from trade_graph.application.activation import attested_confinement_ok


def development_probe(tmp_path, body):
    script = tmp_path / "trusted-probe.py"
    script.write_text(
        "import runpy,os,sys,json\n"
        f"sandbox = runpy.run_path({str(CHECKS_FILE.parent / 'sandbox.py')!r})\n" + body
    )
    return run_bounded([sys.executable, "-I", "-S", "-B", str(script)], b"", cwd=str(tmp_path),
                       extra_env={UNCONFINED_ENV: UNCONFINED_VALUE})


def test_the_switch_is_off_unless_the_exact_value_is_present():
    assert not development_unconfined_requested({})
    assert not development_unconfined_requested({UNCONFINED_ENV: "1"})
    assert not development_unconfined_requested({UNCONFINED_ENV: "yes"})
    assert development_unconfined_requested({UNCONFINED_ENV: UNCONFINED_VALUE})


def test_without_the_switch_an_unsupported_platform_still_fails_closed(tmp_path):
    result = probe(tmp_path, 'sys.platform="unsupported"\nsandbox["confine"]()\nprint("CHECKED")\n')
    assert result["exit_code"] != 0 and "CHECKED" not in result["stdout"]
    assert "requires Linux x86_64 seccomp-bpf" in result["stderr"]


def test_with_the_switch_a_data_only_child_on_an_unsupported_platform_reports_unconfined_evidence(tmp_path):
    result = development_probe(
        tmp_path, 'sys.platform="unsupported"\nprint(json.dumps(sandbox["confine"](allow_development=True)))\n')
    assert result["exit_code"] == 0, result
    evidence = json.loads(result["stdout"])
    assert evidence["mechanism"] == UNCONFINED_MECHANISM
    assert evidence["filesystem"] == "unrestricted" and evidence["network"] == "unrestricted"
    assert evidence["process_creation"] == "unrestricted" and evidence["no_new_privs"] is False
    assert evidence["uid"] != 0 and evidence["memory_bytes"] == MEMORY_BYTES


def test_a_child_that_runs_candidate_code_never_unconfines(tmp_path):
    """Only the trusted data-only children, the artifact checker and the ledger replay probe, may opt in.
    A child that executes candidate code keeps failing closed without seccomp whatever the environment says:
    the installed-image attack probe proves confinement by trying to kill its parent."""
    result = development_probe(tmp_path, 'sys.platform="unsupported"\nsandbox["confine"]()\nprint("UNCONFINED")\n')
    assert result["exit_code"] != 0 and "UNCONFINED" not in result["stdout"]
    assert "requires Linux x86_64 seccomp-bpf" in result["stderr"]


@pytest.mark.skipif(not seccomp_available(), reason="needs a platform with seccomp to prove it is kept")
def test_the_switch_never_weakens_a_platform_that_has_seccomp(tmp_path):
    result = development_probe(tmp_path, 'print(json.dumps(sandbox["confine"](allow_development=True)))\n')
    assert result["exit_code"] == 0, result
    assert json.loads(result["stdout"])["mechanism"] == CONFINED_MECHANISM


def staged(tmp_path):
    source = tmp_path / "source"
    ensure_directory(source)
    write_file(source, "artifacts/context_policy.json", json.dumps(POLICY))
    return source


def test_a_development_runner_attests_with_the_mechanism_it_really_had(tmp_path):
    source = staged(tmp_path)
    runner = EngineerRunner(source, development_unconfined=True)
    runner.stage(tmp_path / "stage")
    report = runner.attest(tmp_path / "stage")
    assert report["exit_code"] == 0, report
    assert report["isolation"]["verified"] is True
    expected = CONFINED_MECHANISM if seccomp_available() else UNCONFINED_MECHANISM
    assert report["isolation"]["mechanism"] == expected
    assert report["isolation"]["confined"] is seccomp_available()


@pytest.mark.skipif(seccomp_available(), reason="the strict runner is confined here; nothing to refuse")
def test_a_strict_runner_refuses_where_seccomp_is_missing(tmp_path):
    source = staged(tmp_path)
    runner = EngineerRunner(source, development_unconfined=False)
    runner.stage(tmp_path / "stage")
    report = runner.attest(tmp_path / "stage")
    assert report["exit_code"] != 0
    assert report["isolation"] == {"verified": False}


def test_the_runner_reads_the_switch_from_the_environment_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv(UNCONFINED_ENV, raising=False)
    assert EngineerRunner(tmp_path).development_unconfined is False
    monkeypatch.setenv(UNCONFINED_ENV, UNCONFINED_VALUE)
    assert EngineerRunner(tmp_path).development_unconfined is True


CONFINED = {"mechanism": CONFINED_MECHANISM, "uid": 1000, "no_new_privs": True, "filesystem": "denied",
            "network": "denied", "process_creation": "denied"}
UNCONFINED = {"mechanism": UNCONFINED_MECHANISM, "uid": 1000, "no_new_privs": False, "filesystem": "unrestricted",
              "network": "unrestricted", "process_creation": "unrestricted"}


def test_confined_evidence_is_acceptable_with_or_without_the_switch():
    assert evidence_acceptable(CONFINED, allow_development=False)
    assert evidence_acceptable(CONFINED, allow_development=True)


def test_unconfined_evidence_is_acceptable_only_with_the_switch():
    assert not evidence_acceptable(UNCONFINED, allow_development=False)
    assert evidence_acceptable(UNCONFINED, allow_development=True)


@pytest.mark.parametrize("broken", [
    {**CONFINED, "uid": 0},
    {**CONFINED, "no_new_privs": False},
    {**CONFINED, "filesystem": "unrestricted"},
    {**UNCONFINED, "uid": 0},
    {**UNCONFINED, "filesystem": "denied"},
    {**CONFINED, "mechanism": "something-else"},
])
def test_evidence_that_claims_a_mix_of_profiles_is_never_acceptable(broken):
    assert not evidence_acceptable(broken, allow_development=True)


def test_activation_accepts_an_unconfined_attestation_only_in_development():
    from trade_graph.adapters.engineering.sandbox import CPU_SECONDS, INPUT_BYTES, OUTPUT_BYTES, WALL_SECONDS
    bounds = {"memory_bytes": MEMORY_BYTES, "cpu_seconds": CPU_SECONDS, "wall_seconds": WALL_SECONDS,
              "input_bytes": INPUT_BYTES, "output_bytes": OUTPUT_BYTES}
    checked = {**UNCONFINED, **bounds}
    attested = {**checked, "verified": True, "confined": False}
    assert attested_confinement_ok(attested, checked, allow_development=True)
    assert not attested_confinement_ok(attested, checked, allow_development=False)
    strict = {**CONFINED, **bounds}
    assert attested_confinement_ok({**strict, "verified": True, "confined": True}, strict, allow_development=False)
    assert not attested_confinement_ok({**strict, "verified": False, "confined": True}, strict, allow_development=False)


def test_every_bounded_child_carries_the_child_marker(tmp_path):
    result = probe(tmp_path, 'print(json.dumps(os.environ.get("TRADE_GRAPH_CONFINED_CHILD")))\n')
    assert result["exit_code"] == 0, result
    assert json.loads(result["stdout"]) == "1"
    assert CHILD_ENV == "TRADE_GRAPH_CONFINED_CHILD"


def test_the_switch_alone_never_unconfines_a_process_that_is_not_a_bounded_child(tmp_path):
    """A developer's shell may export the switch; a test or tool calling confine() in its own process
    must still fail closed, or it would close its own descriptors and inherit a 3-second CPU limit."""
    import subprocess

    script = tmp_path / "in-process.py"
    script.write_text(
        "import runpy,sys\n"
        f"sandbox = runpy.run_path({str(CHECKS_FILE.parent / 'sandbox.py')!r})\n"
        'sys.platform = "unsupported"\n'
        'sandbox["confine"]()\n'
        'print("UNCONFINED IN PROCESS")\n'
    )
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", UNCONFINED_ENV: UNCONFINED_VALUE}
    outcome = subprocess.run([sys.executable, "-I", "-S", "-B", str(script)], env=env, capture_output=True,
                             text=True, timeout=30)
    assert outcome.returncode != 0 and "UNCONFINED IN PROCESS" not in outcome.stdout
    assert "requires Linux x86_64 seccomp-bpf" in outcome.stderr
