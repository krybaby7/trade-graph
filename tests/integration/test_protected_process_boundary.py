"""Actual Linux-host attacks against a fresh confined mutable-code process.

Only synthetic credentials/state are used. This verifies the standalone scaffold,
not production kernel extraction, an immutable host image or an owner class grant.
"""

import hashlib
import json
import os
from decimal import Decimal, localcontext

import pytest

from trade_graph.kernel.process_boundary import (
    BoundaryPolicy,
    ProtectedBoundaryHarness,
    protected_fingerprint,
)

OBSERVATIONS = {"price_eur": "100", "spread_bps": "3", "position_quantity": "0"}
SYNTHETIC_CREDENTIAL = "synthetic-parent-only-credential-41"


def harness(*, wall_seconds="2", feature_names=("signal",)):
    policy = BoundaryPolicy(wall_seconds=Decimal(wall_seconds), feature_names=feature_names)
    return ProtectedBoundaryHarness(expected_kernel_sha256=protected_fingerprint(),
                                    expected_policy_sha256=policy.fingerprint(),
                                    policy=policy, credential=SYNTHETIC_CREDENTIAL)


def evaluate(kernel, source):
    return kernel.evaluate(source, OBSERVATIONS, expected_source_sha256=hashlib.sha256(source.encode()).hexdigest())


def test_pure_numeric_proposal_works_only_in_separate_process():
    kernel = harness(feature_names=("signal", "separate_process"))
    source = f"""
import os
def propose(snapshot):
    assert snapshot["observations"]["price_eur"] == "100"
    return {{"signal":"0.25", "separate_process":"1" if os.getpid() != {os.getpid()} else "0"}}
"""
    result = evaluate(kernel, source)
    assert result["status"] == "validated_numeric_proposal", result
    assert result["proposal"]["features"] == {"signal": "0.25", "separate_process": "1"}
    assert result["live_authorization"] is False
    assert result["deployed_engineer_authorization"] is False
    assert SYNTHETIC_CREDENTIAL not in json.dumps(result)


def test_arbitrary_python_cannot_reach_parent_memory_keys_files_network_or_other_processes(tmp_path, monkeypatch):
    monkeypatch.setenv("PRODUCTION_SYNTHETIC_KEY", SYNTHETIC_CREDENTIAL)
    monkeypatch.setenv("LD_PRELOAD", str(tmp_path / "attacker.so"))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    secret = tmp_path / "sibling-production.sqlite"
    secret.write_text("synthetic protected accounting sentinel")
    secret.chmod(0o666)  # Access must be denied by the OS boundary even for readable/writable host state.
    inherited = os.open(secret, os.O_RDWR)
    escape = tmp_path / "mutable-escape"
    source = f"""
import os, sys, socket, ctypes
def propose(snapshot):
    assert "PRODUCTION_SYNTHETIC_KEY" not in os.environ
    assert "LD_PRELOAD" not in os.environ and "PYTHONPATH" not in os.environ
    blocked = []
    def check(action):
        try:
            action()
            blocked.append(False)
        except OSError as error:
            blocked.append(error.errno in (1, 9))
    check(lambda: os.open({str(secret)!r}, os.O_RDONLY))
    check(lambda: os.open({str(secret)!r}, os.O_WRONLY | os.O_TRUNC))
    check(lambda: os.stat({str(secret)!r}))
    check(lambda: os.open({str(escape)!r}, os.O_CREAT | os.O_WRONLY, 0o600))
    check(lambda: os.open('/proc/{os.getpid()}/environ', os.O_RDONLY))
    check(lambda: os.open('/proc/{os.getpid()}/mem', os.O_RDONLY))
    check(lambda: os.open('/proc/{os.getpid()}/fd/{inherited}', os.O_RDWR))
    check(lambda: os.read({inherited}, 1))
    check(lambda: os.write({inherited}, b'overwrite'))
    check(lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM))
    check(lambda: socket.socket(socket.AF_INET6, socket.SOCK_STREAM))
    check(lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
    check(lambda: os.fork())
    check(lambda: os.execv('/bin/true', ['true']))
    check(lambda: os.kill({os.getpid()}, 9))
    check(lambda: os.chmod({str(secret)!r}, 0o777))
    libc = ctypes.CDLL(None, use_errno=True)
    for nr in (101, 165, 157, 160, 250, 272, 302, 308, 310, 311, 317, 321, 425, 434):
        ctypes.set_errno(0)
        blocked.append(libc.syscall(nr, 0, 0, 0, 0, 0, 0) == -1 and ctypes.get_errno() == 1)
    return {{"signal": "1" if all(blocked) and len(blocked) == 30 else "0"}}
"""
    try:
        kernel = harness()
        result = evaluate(kernel, source)
        assert result["status"] == "validated_numeric_proposal", result
        assert result["proposal"]["features"]["signal"] == "1", result["process"]
        assert kernel._credential == SYNTHETIC_CREDENTIAL
        assert secret.read_text() == "synthetic protected accounting sentinel"
        assert not escape.exists()
        assert SYNTHETIC_CREDENTIAL not in json.dumps(result)
    finally:
        os.close(inherited)


@pytest.mark.parametrize("operation", ["get_secret", "set_budget", "rewrite_ledger", "withdraw", "enable_live",
                                       "activate", "replace_kernel", "change_gate"])
def test_child_cannot_route_privileged_rpc_operations(operation):
    source = f"""
import os, json
def propose(snapshot):
    message = {{"operation":{operation!r}, "snapshot_id":snapshot["snapshot_id"], "features":{{"signal":"1"}}}}
    os.write(1, json.dumps(message).encode())
    os._exit(0)
"""
    kernel = harness()
    result = evaluate(kernel, source)
    assert result["status"] == "rejected"
    assert result["proposal"] is None
    assert "not allowlisted" in result["process"]["validation_error"]
    assert kernel._credential == SYNTHETIC_CREDENTIAL


@pytest.mark.parametrize("features", ["{'signal': 0.5}", "{'signal': 'NaN'}", "{'signal': 'Infinity'}",
                                      "{'signal': '2'}", "{'signal': '0', 'set_budget': '100'}",
                                      "{'signal': {'get_secret': True}}", "{'signal':'invalid'}",
                                      "{'signal':'0e-999999999'}", "{'signal':'0e999999999'}"])
def test_parent_revalidates_numeric_output_independently(features):
    result = evaluate(harness(), f"def propose(snapshot):\n    return {features}\n")
    assert result["status"] == "rejected"
    assert result["proposal"] is None


@pytest.mark.parametrize("feature", ["1.00001", "-1.00001"])
def test_actual_child_output_cannot_round_under_protected_magnitude(feature):
    kernel = harness()
    with localcontext() as context:
        context.prec = 3
        result = evaluate(kernel, f"def propose(snapshot):\n    return {{'signal': {feature!r}}}\n")
    assert result["status"] == "rejected"
    assert result["proposal"] is None
    assert "bounds" in result["process"]["validation_error"]


@pytest.mark.parametrize("value", ["1000000000000.01", "-1000000000000.01"])
def test_snapshot_input_bound_is_exact_under_small_decimal_context(value):
    kernel = harness()
    with localcontext() as context:
        context.prec = 3
        with pytest.raises(ValueError, match="bounds"):
            kernel.snapshot({**OBSERVATIONS, "price_eur": value})


def test_duplicate_json_fields_and_stale_snapshot_are_rejected():
    kernel = harness()
    snapshot = kernel.snapshot(OBSERVATIONS)
    with pytest.raises(ValueError, match="duplicate"):
        kernel.dispatch('{"operation":"get_secret","operation":"read_snapshot","snapshot_id":"x"}', snapshot)
    with pytest.raises(ValueError, match="stale"):
        kernel.dispatch(json.dumps({"operation": "propose_features", "snapshot_id": "stale",
                                    "features": {"signal": "0"}}), snapshot)
    assert kernel.dispatch(json.dumps({"operation": "read_snapshot", "snapshot_id": snapshot["snapshot_id"]}),
                           snapshot)["snapshot"] == snapshot


def test_memory_cpu_wall_and_output_bounds_leave_protected_controller_alive():
    memory_source = """
def propose(snapshot):
    try:
        data = bytearray(256 * 1024 * 1024)
    except MemoryError:
        return {"signal":"1"}
    return {"signal":"0"}
"""
    kernel = harness()
    result = evaluate(kernel, memory_source)
    assert result["status"] == "validated_numeric_proposal", result
    assert result["proposal"]["features"]["signal"] == "1"

    cpu = evaluate(harness(wall_seconds="8"), "def propose(snapshot):\n    while True: pass\n")
    assert cpu["status"] == "rejected" and cpu["process"]["exit_code"] < 0
    assert "wall-clock" not in cpu["process"]["stderr"]

    wall = evaluate(harness(wall_seconds="0.2"),
                    "import time\ndef propose(snapshot):\n    time.sleep(30)\n    return {'signal':'0'}\n")
    assert wall["status"] == "rejected" and "wall-clock" in wall["process"]["stderr"]

    output = evaluate(kernel, "import os\ndef propose(snapshot):\n    while True: os.write(1, b'x' * 8192)\n")
    assert output["status"] == "rejected" and "output limit" in output["process"]["stderr"]
    assert len(output["process"]["stdout"].encode()) <= 16384
    assert kernel.deterministic_fallback(OBSERVATIONS)["features"] == {"signal": "0"}
    assert kernel._credential == SYNTHETIC_CREDENTIAL


def test_closed_output_pipes_cannot_hold_the_protected_parent_past_deadline():
    source = """
import os, time
def propose(snapshot):
    os.close(1)
    os.close(2)
    time.sleep(30)
"""
    result = evaluate(harness(wall_seconds="0.2"), source)
    assert result["status"] == "rejected"
    assert "wall-clock" in result["process"]["stderr"]


def test_deep_malformed_rpc_does_not_crash_protected_parent():
    source = """
import os
def propose(snapshot):
    os.write(1, b'[' * 5000 + b'0' + b']' * 5000)
    os._exit(0)
"""
    kernel = harness()
    result = evaluate(kernel, source)
    assert result["status"] == "rejected" and result["proposal"] is None
    assert kernel.deterministic_fallback(OBSERVATIONS)["features"] == {"signal": "0"}


def test_pinned_kernel_policy_source_and_public_snapshot_bounds_fail_closed(monkeypatch):
    policy = BoundaryPolicy()
    with pytest.raises(PermissionError, match="pin mismatch"):
        ProtectedBoundaryHarness(expected_kernel_sha256="0" * 64, expected_policy_sha256=policy.fingerprint(),
                                 policy=policy, credential=SYNTHETIC_CREDENTIAL)
    with pytest.raises(PermissionError, match="pin mismatch"):
        ProtectedBoundaryHarness(expected_kernel_sha256=protected_fingerprint(), expected_policy_sha256="0" * 64,
                                 policy=policy, credential=SYNTHETIC_CREDENTIAL)
    kernel = harness()
    with pytest.raises(ValueError, match="content hash"):
        kernel.evaluate("def propose(snapshot): return {'signal':'0'}", OBSERVATIONS, expected_source_sha256="0" * 64)
    with pytest.raises(ValueError, match="exactly"):
        kernel.snapshot({**OBSERVATIONS, "credential": SYNTHETIC_CREDENTIAL})
    with pytest.raises(ValueError, match="Decimal strings"):
        kernel.snapshot({**OBSERVATIONS, "price_eur": 100.0})
    with pytest.raises(ValueError, match="bounds"):
        kernel.snapshot({**OBSERVATIONS, "price_eur": "1000000000001"})
    with pytest.raises(ValueError, match="protected"):
        BoundaryPolicy(feature_names=("set_budget", "budget"))
    monkeypatch.setattr("trade_graph.kernel.process_boundary.protected_fingerprint", lambda: "0" * 64)
    with pytest.raises(PermissionError, match="pin mismatch"):
        kernel.deterministic_fallback(OBSERVATIONS)


def test_candidate_imports_and_filesystem_shadow_cannot_replace_the_pinned_worker(tmp_path, monkeypatch):
    (tmp_path / "json.py").write_text("raise RuntimeError('untrusted json shadow')")
    (tmp_path / "sandbox.py").write_text("def confine(): return {}")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    source = """
import os
def propose(snapshot):
    try:
        os.open('/etc/passwd', os.O_RDONLY)
        return {'signal':'0'}
    except PermissionError:
        return {'signal':'1'}
"""
    result = evaluate(harness(), source)
    assert result["status"] == "validated_numeric_proposal", result
    assert result["proposal"]["features"]["signal"] == "1"
    assert "untrusted json shadow" not in json.dumps(result)
