import hashlib
import json
import os
import shutil
import sys
import threading
from pathlib import Path

import pytest

from trade_graph.adapters.models.subscription import CliOutcome, SubscriptionConfig
from trade_graph.adapters.models.subscription_process import (
    LinuxFilesystemBoundary,
    NativeCliPin,
    probe_subscription,
)

pytestmark = pytest.mark.skipif(sys.platform != "linux" or not shutil.which("bwrap"),
                                reason="native Linux bubblewrap boundary required")


def system_python():
    binary = Path("/usr/bin/python3").resolve()
    return NativeCliPin(binary, hashlib.sha256(binary.read_bytes()).hexdigest(), require_root_owner=False)


def test_actual_namespace_denies_private_files_env_proc_and_wsl_mounts(tmp_path, monkeypatch):
    secret = tmp_path / "private.sqlite"
    secret.write_text("SYNTHETIC_PRIVATE_SENTINEL")
    monkeypatch.setenv("KRAKEN_API_SECRET", "SYNTHETIC_SECRET_NOT_FOR_CHILD")
    boundary = LinuxFilesystemBoundary(system_python(), share_network=False)
    code = """import json,os,pathlib
paths=json.loads(input())
denied=[]
for p in paths:
 try:
  pathlib.Path(p).read_bytes()
  denied.append(False)
 except OSError: denied.append(True)
print(json.dumps({'denied':denied,'env_denied':not os.environ.get('KRAKEN_API_SECRET'),
 'interop_denied':not os.environ.get('WSL_INTEROP'),'mnt_missing':not pathlib.Path('/mnt/c').exists()}))
"""
    paths = [str(secret), str(Path.home() / ".codex" / "auth.json"),
             "/mnt/c/Users/adami/.codex/auth.json", f"/proc/{os.getpid()}/environ",
             str(Path(__file__).resolve().parents[2] / "src/trade_graph/application/execution.py")]
    outcome = boundary.run(["-I", "-c", code], json.dumps(paths).encode() + b"\n", maximum_seconds=5)
    assert outcome.exit_code == 0, outcome
    document = json.loads(outcome.stdout)
    assert all(document["denied"])
    assert document["env_denied"] and document["interop_denied"] and document["mnt_missing"]
    assert "SYNTHETIC" not in outcome.stdout


def test_isolated_process_deadline_kills_descendants(tmp_path):
    boundary = LinuxFilesystemBoundary(system_python(), share_network=False)
    code = "import os,time; os.fork(); time.sleep(30)"
    outcome = boundary.run(["-I", "-c", code], b"", maximum_seconds=0.2)
    assert outcome.stopped == "timeout" and outcome.exit_code != 0


def test_isolated_process_cancellation_and_output_limit():
    boundary = LinuxFilesystemBoundary(system_python(), share_network=False)
    event = threading.Event()
    timer = threading.Timer(0.2, event.set)
    timer.start()
    try:
        outcome = boundary.run(["-I", "-c", "import time;time.sleep(30)"], b"", maximum_seconds=4,
                               cancel_event=event)
    finally:
        timer.cancel()
    assert outcome.stopped == "cancelled"
    flooded = boundary.run(["-I", "-c", "print('x'*400000)"], b"", maximum_seconds=4)
    assert flooded.stopped == "output_limit"


def test_cli_pin_rejects_windows_interop_scripts_and_changed_binary(tmp_path):
    with pytest.raises(ValueError, match="native"):
        NativeCliPin(Path("/mnt/c/Windows/cmd.exe"), "0" * 64).verify()
    script = tmp_path / "claude"
    script.write_text("#!/bin/sh\nprintf no\n")
    with pytest.raises(ValueError, match="ELF"):
        NativeCliPin(script, hashlib.sha256(script.read_bytes()).hexdigest(), require_root_owner=False).verify()
    binary = Path("/usr/bin/python3").resolve()
    with pytest.raises(ValueError, match="hash"):
        NativeCliPin(binary, "0" * 64, require_root_owner=False).verify()


def test_native_cli_probe_never_invokes_model_and_excludes_identifiers(monkeypatch):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        if "--version" in command:
            return CliOutcome(stdout="2.1.285 (Claude Code)", exit_code=0)
        return CliOutcome(stdout='{"loggedIn":true,"authMethod":"claude.ai","apiProvider":"firstParty",'
                                '"subscriptionType":"max","email":"private@example.invalid","token":"private"}',
                          exit_code=0)

    status = probe_subscription(SubscriptionConfig(provider="claude_subscription", model="claude-sonnet-5-5"),
        executable=Path("/usr/bin/python3").resolve(), run_metadata=runner)
    assert all("-p" not in call and "exec" not in call for call in calls)
    assert status["authentication"] == "subscription"
    assert "private" not in json.dumps(status)
    assert not status["ready"]
