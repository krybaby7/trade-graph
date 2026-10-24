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
    probe_isolated_claude,
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


def test_root_owned_cli_under_writable_parent_is_refused(tmp_path):
    # The parent check precedes reading/hashing binary bytes. A controlled stat
    # substitution models a root-owned file dropped in a user-writable directory.
    binary = tmp_path / "cli"
    shutil.copyfile(Path("/usr/bin/python3").resolve(), binary)
    original_stat = Path.stat

    def stat_result(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == binary:
            values = list(result)
            values[4] = 0
            values[0] &= ~0o022
            return os.stat_result(values)
        return result

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(Path, "stat", stat_result)
        with pytest.raises(ValueError, match="parent"):
            NativeCliPin(binary, hashlib.sha256(binary.read_bytes()).hexdigest()).verify()


def test_production_claude_preflight_uses_same_auth_mount_and_no_inference(tmp_path, monkeypatch):
    credentials = tmp_path / ".credentials.json"
    credentials.write_text('{"fixture":"synthetic only"}')
    credentials.chmod(0o600)
    commands = []

    def fake_run(boundary, arguments, payload, **kwargs):
        commands.append(boundary.command(arguments))
        if "--version" in arguments:
            return CliOutcome("2.1.285", 0)
        return CliOutcome('{"loggedIn":true,"authMethod":"claude.ai","apiProvider":"firstParty",'
                          '"subscriptionType":"max"}', 0)

    monkeypatch.setattr(LinuxFilesystemBoundary, "run", fake_run)
    protected_pin = NativeCliPin(Path("/usr/bin/python3").resolve(), system_python().sha256)
    config = SubscriptionConfig(provider="claude_subscription", model="claude-sonnet-5-5", enabled=True)
    status = probe_isolated_claude(config, protected_pin, credentials, extra_usage_disabled=True,
                                  isolation_verified=True)
    assert status["authentication"] == "subscription" and status["ready"]
    assert len(commands) == 2 and all("-p" not in command for command in commands)
    assert all(str(credentials) in command for command in commands)


def test_codex_preflight_and_executor_use_opaque_official_auth_and_isolated_schema(tmp_path, monkeypatch):
    from trade_graph.adapters.models import subscription_process as module
    from trade_graph.contracts.models import ModelRequest
    credentials = tmp_path / "auth.json"
    credentials.write_text('{"fixture":"synthetic only"}')
    credentials.chmod(0o600)
    commands = []
    def fake_run(boundary, arguments, payload, **kwargs):
        commands.append(boundary.command(arguments))
        if "--version" in arguments:
            return CliOutcome("codex-cli 0.160.1",0)
        if "status" in arguments:
            return CliOutcome("Logged in using ChatGPT",0)
        assert b"Summarize" in payload and b'"market"' in payload
        assert json.loads(boundary.schema_file.read_text()) == {"type":"object","properties":{},
            "additionalProperties":False,"required":[]}
        return CliOutcome('{"type":"turn.completed"}',0)
    monkeypatch.setattr(LinuxFilesystemBoundary,"run",fake_run)
    pin=NativeCliPin(Path("/usr/bin/python3").resolve(),system_python().sha256)
    config=SubscriptionConfig(provider="codex_subscription",model="gpt-6.1-sol",enabled=True)
    status=module.probe_isolated_codex(config,pin,credentials,extra_usage_disabled=True,isolation_verified=True,
        quota={"ordinary_usage_allowed":True,"credits_balance":"0","remaining_percent":75})
    assert status["ready"] and status["authentication"] == "chatgpt"
    assert all("/home/runner/.codex/auth.json" in command for command in commands)
    catalog=tmp_path / "catalog.json"
    catalog.write_text('{"models":[]}')
    catalog.chmod(0o600)
    executor=module.LinuxSubscriptionExecutor(pin,credentials,proxy_url="http://172.30.0.2:8080",config=config,
                                             model_catalog_file=catalog)
    req=ModelRequest(role="research",task_id="t",root_task_id="r",run_id="s",system_version_id="v",
        provider="openai",model=config.model,instructions="Summarize",context={"market":{}},
        output_schema={"type":"object"},schema_name="research",max_output_tokens=16384,max_tool_calls=0,timeout_seconds=600)
    executor.execute(req)
    assert "--output-schema" in commands[-1] and "--ignore-user-config" in commands[-1]
    assert str(catalog) in commands[-1] and '/request/model-catalog.json' in commands[-1]
    assert 'model_catalog_json="/request/model-catalog.json"' in commands[-1]
    assert "ANTHROPIC_API_KEY" not in commands[-1] and "OPENAI_API_KEY" not in commands[-1]


def test_process_output_bound_can_be_set_for_normal_operation():
    boundary=LinuxFilesystemBoundary(system_python(),share_network=False)
    outcome=boundary.run(["-I","-c","print('x'*400000)"],b"",maximum_seconds=4,maximum_output_bytes=500000)
    assert outcome.exit_code == 0 and not outcome.stopped


def test_native_codex_auth_cache_can_refresh_only_its_dedicated_private_directory(tmp_path):
    directory=tmp_path / "private-cli-auth"
    directory.mkdir(mode=0o700)
    credentials=directory / "auth.json"
    credentials.write_text('{"fixture":"synthetic only"}')
    credentials.chmod(0o600)
    boundary=LinuxFilesystemBoundary(system_python(),share_network=False,credential_file=credentials,
        provider="codex_subscription",credential_writable=True)
    code=("import pathlib; p=pathlib.Path('/home/runner/.codex/auth.json'); "
          "p.with_suffix('.new').write_text('renewed'); p.with_suffix('.new').replace(p); print(p.read_text())")
    result=boundary.run(["-I","-c",code],b"",maximum_seconds=3)
    assert result.exit_code==0 and result.stdout.strip()=="renewed"
    assert credentials.read_text()=="renewed"


def test_codex_quota_metadata_is_explicitly_sanitized_without_account_identifiers():
    from trade_graph.adapters.models import subscription_process as module
    quota=module.codex_quota_metadata({"ordinaryUsageAllowed":True,"accountId":"PRIVATE",
        "rateLimits":{"primary":{"usedPercent":25,"windowDurationMins":10080,"resetsAt":1791822315},
          "secondary":None,"credits":{"balance":"0","unlimited":False,"hasCredits":False}}})
    assert quota["ordinary_usage_allowed"] and quota["weekly"]["remaining_percent"]==75
    assert quota["credits_balance"]=="0" and "PRIVATE" not in json.dumps(quota)


def test_interactive_codex_quota_probe_awaits_each_response_then_exits(tmp_path):
    from trade_graph.adapters.models.subscription_process import probe_codex_account_quota
    script=tmp_path / "quota.py"
    script.write_text("""import json,sys
for line in sys.stdin:
 d=json.loads(line)
 if d['method']=='initialized': continue
 if d['method']=='initialize': r={}
 elif d['method']=='account/read': r={'account':{'type':'chatgpt','id':'PRIVATE_ACCOUNT'}}
 elif d['method']=='account/rateLimits/read': r={'ordinaryUsageAllowed':True,'accountId':'PRIVATE_ACCOUNT',
  'rateLimits':{'primary':{'usedPercent':25,'windowDurationMins':10080,'resetsAt':1791822315},
  'credits':{'balance':'0'}}}
 else: raise RuntimeError('unexpected model request')
 print(json.dumps({'id':d['id'],'result':r})+'\\n'+json.dumps({'method':'notification'}),flush=True)
""")
    class Boundary:
        def command(self,args):
            assert 'app-server' in args and 'exec' not in args
            return [sys.executable,str(script)]
    quota=probe_codex_account_quota(Boundary(),maximum_seconds=3)
    assert quota['weekly']['remaining_percent']==75 and quota['credits_balance']=='0'
    assert 'PRIVATE' not in json.dumps(quota)


def test_malformed_or_hanging_quota_metadata_is_bounded_and_unknown(tmp_path):
    import time

    from trade_graph.adapters.models.subscription_process import probe_codex_account_quota
    class Boundary:
        def command(self,args):
            return [sys.executable,'-c',"import time;print('invalid json',flush=True);time.sleep(30)"]
    started=time.monotonic()
    assert probe_codex_account_quota(Boundary(),maximum_seconds=0.2)=={}
    assert time.monotonic()-started < 3


def test_native_codex_receives_recursive_strict_wire_schema_without_changing_domain_contract(tmp_path,monkeypatch):
    from copy import deepcopy

    from trade_graph.adapters.models import subscription_process as module
    from trade_graph.application.runtime_departments import ResearchReply
    from trade_graph.contracts.models import ModelRequest
    credentials=tmp_path/"auth.json"
    credentials.write_text('{"fixture":"synthetic only"}')
    credentials.chmod(0o600)
    catalog=tmp_path/"catalog.json"
    catalog.write_text('{"models":[]}')
    catalog.chmod(0o600)
    original=ResearchReply.model_json_schema()
    snapshot=deepcopy(original)
    captured=[]
    def fake_run(boundary,arguments,payload,**kwargs):
        captured.append(json.loads(boundary.schema_file.read_text()))
        return CliOutcome('{"type":"turn.completed"}',0)
    monkeypatch.setattr(LinuxFilesystemBoundary,"run",fake_run)
    pin=NativeCliPin(Path("/usr/bin/python3").resolve(),system_python().sha256)
    config=SubscriptionConfig(provider="codex_subscription",model="gpt-6.1-sol",enabled=True)
    executor=module.LinuxSubscriptionExecutor(pin,credentials,proxy_url="http://172.30.0.2:8080",config=config,
                                             model_catalog_file=catalog)
    req=ModelRequest(role="research",task_id="t",root_task_id="r",run_id="s",system_version_id="v",
        provider="openai",model=config.model,instructions="Summarize",context={"market":{}},
        output_schema=original,schema_name="ResearchReply",max_output_tokens=16384,max_tool_calls=0,timeout_seconds=600)
    executor.execute(req)
    def verify(node):
        if isinstance(node,dict):
            if node.get("type")=="object":
                assert node["required"]==list(node["properties"])
                assert node["additionalProperties"] is False
            assert "default" not in node
            for value in node.values():
                verify(value)
        elif isinstance(node,list):
            for value in node:
                verify(value)
    verify(captured[0])
    assert "findings" in captured[0]["required"] and "findings" not in original["required"]
    assert original==snapshot and req.output_schema==snapshot
