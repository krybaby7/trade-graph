"""Kernel and path probes run as child processes; no production secret is used."""

import hashlib
import json
import os
import subprocess
import sys

import pytest
from tests.integration.test_engineer import POLICY

from trade_graph.adapters.engineering.artifact_files import ensure_directory, read_tree, write_file
from trade_graph.adapters.engineering.artifact_policy import artifact_class, validate
from trade_graph.adapters.engineering.process import run_bounded
from trade_graph.adapters.engineering.provenance import CHECKS_FILE, checks_module_hash, scrubbed_env
from trade_graph.adapters.engineering.runner import EngineerRunner
from trade_graph.adapters.engineering.sandbox import MEMORY_BYTES, OUTPUT_BYTES


def probe(tmp_path, body, *, wall=10):
    script = tmp_path / "trusted-probe.py"
    script.write_text(
        "import runpy,os,sys,json,socket,ctypes,resource,time\n"
        f"sandbox = runpy.run_path({str(CHECKS_FILE.parent / 'sandbox.py')!r})\n" + body
    )
    return run_bounded([sys.executable, "-I", "-S", "-B", str(script)], b"", cwd=str(tmp_path), wall_seconds=wall)


def test_kernel_blocks_host_files_network_processes_and_inherited_descriptors(tmp_path):
    secret = tmp_path / "production.sqlite"
    secret.write_text("world-readable synthetic production sentinel")
    secret.chmod(0o644)
    result = probe(
        tmp_path,
        f"""
fd = os.open({str(secret)!r}, os.O_RDONLY)
libc = ctypes.CDLL(None, use_errno=True)
evidence = sandbox["confine"]()
failures = {{}}
def blocked(name, action):
    try:
        action()
        failures[name] = "unexpectedly allowed"
    except OSError as exc:
        failures[name] = exc.errno
blocked("read_secret", lambda: os.open({str(secret)!r}, os.O_RDONLY))
blocked("stat_secret", lambda: os.stat({str(secret)!r}))
blocked("create_file", lambda: os.open({str(tmp_path / "escape")!r}, os.O_CREAT | os.O_WRONLY, 0o600))
blocked("socket", lambda: socket.socket())
blocked("fork", lambda: os.fork())
blocked("exec", lambda: os.execv("/bin/true", ["true"]))
blocked("inherited_fd", lambda: os.read(fd, 1))
blocked("chmod", lambda: os.chmod({str(secret)!r}, 0o777))
for name,nr in [("ptrace",101),("mount",165),("io_uring",425),
                ("process_vm_readv",310),("setrlimit",160),("prlimit64",302)]:
    ctypes.set_errno(0)
    assert libc.syscall(nr, 0, 0, 0, 0, 0, 0) == -1
    failures[name] = ctypes.get_errno()
try:
    large = bytearray(256 * 1024 * 1024)
    failures["memory"] = "unexpectedly allowed"
except MemoryError:
    failures["memory"] = "bounded"
print(json.dumps({{"isolation": evidence, "probes": failures}}))
""",
    )
    assert result["exit_code"] == 0, result
    output = json.loads(result["stdout"])
    assert output["isolation"]["uid"] != 0
    assert output["isolation"]["no_new_privs"] and output["isolation"]["memory_bytes"] == MEMORY_BYTES
    assert output["probes"].pop("memory") == "bounded"
    assert set(output["probes"].values()).issubset({1, 9})  # EPERM or closed descriptor.
    assert secret.read_text() == "world-readable synthetic production sentinel"
    assert not (tmp_path / "escape").exists()


def test_checker_cpu_limit_is_kernel_enforced(tmp_path):
    result = probe(tmp_path, 'sandbox["confine"]()\nwhile True: pass\n', wall=8)
    assert result["exit_code"] < 0
    assert "wall-clock" not in result["stderr"]


@pytest.mark.parametrize(
    "body, expected",
    [
        ('sandbox["confine"]()\ntime.sleep(30)\n', "wall-clock"),
        ('sandbox["confine"]()\nwhile True: os.write(1,b"x" * 8192)\n', "output limit"),
    ],
)
def test_checker_wall_and_output_limits_are_enforced(tmp_path, body, expected):
    result = probe(tmp_path, body, wall=0.3)
    assert result["exit_code"] != 0 and expected in result["stderr"]
    assert len(result["stdout"].encode()) <= OUTPUT_BYTES


def test_unsupported_platform_fails_closed_instead_of_scrub_only(tmp_path):
    result = probe(tmp_path, 'sys.platform="unsupported"\nsandbox["confine"]()\nprint("CHECKED")\n')
    assert result["exit_code"] != 0 and "CHECKED" not in result["stdout"]
    assert "requires Linux x86_64 seccomp-bpf" in result["stderr"]


@pytest.mark.parametrize(
    "path",
    [
        "../escape",
        "/tmp/context_policy.json",
        "artifacts/../context_policy.json",
        "artifacts\\context_policy.json",
        "artifacts/.env",
        "artifacts/executable.py",
        "artifacts//context_policy.json",
        "artifacts/context_policy.json/..",
        "trade_graph/adapters/engineering/checks.py",
        "tests/test_checks.py",
        "artifacts/unregistered.json",
    ],
)
def test_path_and_executable_class_allowlist_is_explicit(path):
    with pytest.raises(ValueError):
        artifact_class(path)


@pytest.mark.parametrize("kind", ["symlink_file", "symlink_parent", "hardlink", "fifo"])
def test_proxy_never_follows_artifact_files_into_production_state(tmp_path, kind):
    source = tmp_path / "source"
    (source / "artifacts").mkdir(parents=True)
    secret = tmp_path / "production.sqlite"
    secret.write_text("synthetic secret")
    path = source / "artifacts" / "context_policy.json"
    if kind == "symlink_file":
        path.symlink_to(secret)
    elif kind == "symlink_parent":
        (source / "artifacts").rmdir()
        (source / "artifacts").symlink_to(tmp_path, target_is_directory=True)
    elif kind == "hardlink":
        os.link(secret, path)
    else:
        os.mkfifo(path)
    with pytest.raises((OSError, ValueError)):
        read_tree(source, source=True)
    with pytest.raises((OSError, ValueError)):
        write_file(source, "artifacts/context_policy.json", "replacement")
    assert secret.read_text() == "synthetic secret"


def test_parent_symlink_is_denied_even_before_stage_creation(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "alias").symlink_to(real, target_is_directory=True)
    with pytest.raises(OSError):
        ensure_directory(tmp_path / "alias" / "stage")
    assert not (real / "stage").exists()


def test_independent_checker_cannot_be_shadowed_and_checks_bound_artifact_bytes(tmp_path, monkeypatch):
    source = tmp_path / "source"
    ensure_directory(source)
    write_file(source, "artifacts/context_policy.json", json.dumps(POLICY))
    runner = EngineerRunner(source)
    runner.stage(tmp_path / "stage")
    # A candidate cannot modify checker imports, cwd, environment or command.
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "stage"))
    monkeypatch.setenv("LD_PRELOAD", str(tmp_path / "malicious.so"))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")
    report = runner.attest(tmp_path / "stage")
    assert report["exit_code"] == 0 and report["isolation"]["verified"], report
    assert report["checks_module_hash"] == checks_module_hash()
    files = report["artifact_files"]
    assert (
        report["manifest"]["files"][0]["sha256"]
        == hashlib.sha256(files["artifacts/context_policy.json"].encode()).hexdigest()
    )
    shadow = tmp_path / "stage" / "json.py"
    shadow.write_text("raise Exception('shadow executed')")
    result = runner.attest(tmp_path / "stage")
    assert result["exit_code"] != 0
    assert "shadow executed" not in result["stderr"]
    assert "PYTHONPATH" not in scrubbed_env() and "LD_PRELOAD" not in scrubbed_env()


@pytest.mark.parametrize(
    "bad",
    [
        '{"schema_version":1,"schema_version":1,"max_general_lessons":5,"always_include":[]}',
        json.dumps({**POLICY, "schema_version": True}),
        json.dumps({**POLICY, "max_general_lessons": True}),
        json.dumps({**POLICY, "shell_command": "echo approvals"}),
    ],
)
def test_trusted_data_grammar_cannot_be_expanded_by_generated_fields(bad):
    assert validate({"artifacts/context_policy.json": bad})


def test_resource_bounds_reject_large_file_and_protected_workspace_content(tmp_path):
    source = tmp_path / "source"
    ensure_directory(source / "artifacts")
    path = source / "artifacts" / "context_policy.json"
    path.write_bytes(b"x" * 65537)
    with pytest.raises(ValueError, match="bytes"):
        read_tree(source, source=True)
    path.write_text(json.dumps(POLICY))
    (source / "artifacts" / "production.sqlite").write_text("not an artifact")
    with pytest.raises(ValueError, match="unregistered"):
        read_tree(source, source=True)


def test_checker_does_not_accept_oversized_input(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(CHECKS_FILE)],
        input=b" " * 524289,
        capture_output=True,
        env=scrubbed_env(),
        timeout=12,
    )
    assert proc.returncode != 0
    assert len(proc.stdout) + len(proc.stderr) <= OUTPUT_BYTES
