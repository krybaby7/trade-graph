#!/usr/bin/env python3
"""Run inside the actual credential-free subscription image, never an owner runtime.

Use the production UID, read-only root, cap-drop ALL, no-new-privileges, pinned
custom seccomp and dedicated internal network. Independently inspect those Docker
settings on the host. Mount only a synthetic canary read-only at
/mnt/c/trade-graph-probe-canary; provide this script over Docker stdin.

The adversarial ELF interpreter receives a test-only read-only stdlib bind in
addition to the production bwrap flags. The real native CLI is separately checked
under the unchanged production boundary. tools_disabled verifies native flag
support and the production host-tool boundary, without model inference.
Server-side web search may remain configured for a department; no filesystem,
shell, patch, credential or external MCP capability is granted to model output.
CONNECT probes close before TLS, so no provider/exchange API requests are sent.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import ipaddress
import json
import os
import re
import selectors
import signal
import socket
import subprocess
import sys
import sysconfig
import tempfile
import time
from pathlib import Path

from trade_graph.adapters.models.subscription import (
    SubscriptionConfig,
    claude_command,
    claude_environment,
    codex_command,
)
from trade_graph.adapters.models.subscription_process import LinuxFilesystemBoundary, NativeCliPin
from trade_graph.contracts.models import ModelRequest
from trade_graph.kernel.runtime_manifest import protected_package_sha256
from trade_graph.kernel.subscription_proxy import PROVIDER_HOSTS

FILESYSTEM_CHECKS = frozenset({"private_files_denied", "windows_mounts_denied",
                              "host_proc_denied", "api_environment_denied"})
CHECKS = FILESYSTEM_CHECKS | {"descendants_killed", "tools_disabled", "direct_egress_denied",
                             "provider_route_restricted", "market_route_restricted"}
REQUIRED_FLAGS = frozenset({"--restricted", "--safe-mode", "--tools", "--disallowedTools",
    "--strict-mcp-config", "--mcp-config", "--setting-sources", "--settings",
    "--no-session-persistence", "--json-schema"})
CODEX_REQUIRED_FLAGS = frozenset({"--json", "--ephemeral", "--ignore-user-config", "--ignore-rules",
    "--skip-git-repo-check", "--sandbox", "--output-schema", "--model", "--config"})
CANARY = b"SYNTHETIC_ONLY_SUBSCRIPTION_BOUNDARY_CANARY\n"
WINDOWS_CANARY = Path("/mnt/c/trade-graph-probe-canary")
ENV_NAMES = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "KRAKEN_API_KEY", "KRAKEN_API_SECRET",
             "WSL_INTEROP", "TRADE_GRAPH_PROBE_PARENT_ONLY")

FILESYSTEM_CODE = """
import json,os,pathlib
data=json.loads(input())
def denied(path):
 try:
  pathlib.Path(path).read_bytes()
  return False
 except OSError:
  return True
checks={
 'private_files_denied':denied(data['private']),
 'windows_mounts_denied':denied('/mnt/c/trade-graph-probe-canary') and not pathlib.Path('/mnt/c').exists(),
 'host_proc_denied':os.readlink('/proc/self/ns/pid') != data['parent_pid_namespace']
      and denied('/proc/'+str(data['parent_pid'])+'/root'+data['private']),
 'api_environment_denied':all(name not in os.environ for name in data['environment_names'])
      and os.environ.get('PATH') == '/nonexistent'
}
print(json.dumps(checks))
"""
DESCENDANT_CODE = """
import json,os,time
print(json.dumps({'pid_namespace':os.readlink('/proc/self/ns/pid')}),flush=True)
if os.fork() == 0:
 os.setsid()
time.sleep(30)
"""


def admission_proof(native_sha256: str, package_sha256: str, checks: dict) -> dict | None:
    if (set(checks) != CHECKS or any(value is not True for value in checks.values())
            or not re.fullmatch(r"[0-9a-f]{64}", native_sha256)
            or not re.fullmatch(r"[0-9a-f]{64}", package_sha256)):
        return None
    return {"schema_version": 1, "kind": "actual-linux-boundary", "native_cli_sha256": native_sha256,
            "protected_package_sha256": package_sha256, "checks": sorted(CHECKS)}


def validate_network(address: str, provider_port: int, market_port: int) -> None:
    value = ipaddress.IPv4Address(address)
    if (str(value) != address or not value.is_private or value.is_loopback or value.is_link_local
            or value.is_multicast or value.is_unspecified or provider_port == market_port
            or any(type(port) is not int or not 1024 <= port <= 65535 for port in (provider_port, market_port))):
        raise ValueError("exact reviewed private proxy and separate ports required")


def supports_required_flags(help_text: str, provider: str = "claude_subscription") -> bool:
    required = CODEX_REQUIRED_FLAGS if provider == "codex_subscription" else REQUIRED_FLAGS
    return all(re.search(r"(?:^|\s)" + re.escape(flag) + r"(?=\s|,|=|$)", help_text) for flag in required)



def parser_controls_verified(accepted: tuple[int, str], rejected: tuple[int, str],
                             provider: str = "claude_subscription") -> bool:
    if provider == "codex_subscription":
        return (accepted == (1, "No prompt provided via stdin.\n")
            and rejected == (2, "error: unexpected argument '--trade-graph-invalid-probe-flag' found\n\n"
                "  tip: to pass '--trade-graph-invalid-probe-flag' as a value, use "
                "'-- --trade-graph-invalid-probe-flag'\n\n"
                "Usage: codex exec [OPTIONS] [PROMPT]\n"
                "       codex exec [OPTIONS] <COMMAND> [ARGS]\n\n"
                "For more information, try '--help'.\n"))
    # --help short-circuits unknown-option checking in pinned Claude 2.1.292.
    # Empty input reaches an offline fixed guard only after valid option parsing.
    return (accepted == (1, "Error: Input must be provided either through stdin or as a prompt argument "
                         "when using --print\n")
            and rejected == (1, "error: unknown option '--trade-graph-invalid-probe-flag'\n"))


def bounded_parser_diagnostic(boundary, arguments: list[str], *, maximum_seconds=5, maximum_bytes=8192):
    """Fresh offline namespace, EOF stdin, merged bounded output; never a prompt."""
    command = boundary.command(arguments)
    payload = bytearray()
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          cwd="/", env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, close_fds=True,
                          start_new_session=True) as process:
        deadline = time.monotonic() + maximum_seconds
        try:
            os.set_blocking(process.stdout.fileno(), False)
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return -1, ""
                    for key, _ in selector.select(remaining):
                        data = os.read(key.fd, 8192)
                        if not data:
                            selector.unregister(key.fileobj)
                        else:
                            payload.extend(data)
                            if len(payload) > maximum_bytes:
                                return -1, ""
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return -1, ""
            return process.wait(timeout=remaining), payload.decode("utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired):
            return -1, ""
        finally:
            if process.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def filesystem_observations(payload: str) -> dict | None:
    try:
        value = json.loads(payload)
        if type(value) is dict and set(value) == FILESYSTEM_CHECKS and all(v is True for v in value.values()):
            return value
    except (ValueError, TypeError, RecursionError):
        pass
    return None


def connect_status(address: str, port: int, host: str) -> int:
    """A successful CONNECT closes before ClientHello: no upstream effect."""
    request = f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode("ascii")
    with socket.create_connection((address, port), timeout=2) as stream:
        stream.settimeout(2)
        stream.sendall(request)
        header = bytearray()
        deadline = time.monotonic() + 3
        while not header.endswith(b"\r\n\r\n"):
            if len(header) >= 8192 or time.monotonic() >= deadline:
                raise ValueError("bounded CONNECT response required")
            chunk = stream.recv(1)
            if not chunk:
                raise ValueError("complete CONNECT response required")
            header.extend(chunk)
        first_line = bytes(header).split(b"\r\n", 1)[0]
        match = re.fullmatch(rb"HTTP/1\.1 ([0-9]{3}) [A-Za-z ]+", first_line)
        if not match:
            raise ValueError("fixed CONNECT status required")
        return int(match[1])


class ProbeInterpreterBoundary(LinuxFilesystemBoundary):
    """Test-only stronger attack surface; the production CLI gets no stdlib bind."""

    def command(self, arguments: list[str]) -> list[str]:
        command = super().command(arguments)
        index = len(command) - len(arguments) - 1
        if command[index] != "/cli/runner":
            raise ValueError("fixed executable boundary changed")
        stdlib = Path(sysconfig.get_path("stdlib")).resolve()
        if not stdlib.is_absolute() or not str(stdlib).startswith(("/usr/lib/", "/usr/local/lib/")):
            raise ValueError("root image interpreter stdlib required")
        extra = ["--ro-bind", str(stdlib), str(stdlib)]
        if sysconfig.get_config_var("Py_ENABLE_SHARED"):
            library_dir = Path(sysconfig.get_config_var("LIBDIR")).resolve()
            library = library_dir / sysconfig.get_config_var("INSTSONAME")
            if (not str(library_dir).startswith(("/usr/lib", "/usr/local/lib"))
                    or not library.is_file()):
                raise ValueError("root image interpreter shared library required")
            # /cli/runner changes $ORIGIN, so the original ELF relative RUNPATH
            # cannot locate libpython. This explicit test-only path fixes that.
            extra += ["--ro-bind", str(library), str(library),
                      "--setenv", "LD_LIBRARY_PATH", str(library_dir)]
        return command[:index] + extra + command[index:]


def namespace_exists(namespace: str) -> bool:
    for path in Path("/proc").iterdir():
        if path.name.isdigit():
            with contextlib.suppress(OSError):
                if os.readlink(path / "ns/pid") == namespace:
                    return True
    return False


def catalog_document_verified(document: dict, model: str) -> bool:
    models = document.get("models") if type(document) is dict else None
    return bool(type(models) is list and len(models) == 1 and type(models[0]) is dict
        and models[0].get("slug") == model and models[0].get("shell_type") == "disabled"
        and "apply_patch_tool_type" in models[0] and models[0]["apply_patch_tool_type"] is None
        and models[0].get("experimental_supported_tools") == []
        and models[0].get("tool_mode") == "direct" and models[0].get("node_repl_disabled") is True
        and models[0].get("supports_search_tool") is False
        and "multi_agent_version" in models[0] and models[0]["multi_agent_version"] is None
        and "multi_agent_reasoning_effort" in models[0] and models[0]["multi_agent_reasoning_effort"] is None)


def verify_catalog(path: Path, sha256: str, model: str) -> None:
    if (path.is_symlink() or not path.is_file() or not path.is_absolute()
            or not re.fullmatch(r"[0-9a-f]{64}", sha256)
            or path.stat().st_uid != 0 or path.stat().st_mode & 0o022 or path.stat().st_size > 262144):
        raise ValueError("pinned root-controlled public model catalog required")
    for parent in path.parents:
        if parent.stat().st_uid != 0 or parent.stat().st_mode & 0o022:
            raise ValueError("model catalog ancestors must be root-controlled")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha256 or not catalog_document_verified(json.loads(raw), model):
        raise ValueError("model catalog identity or host-tool controls differ from owner pin")


def tool_configuration_verified(native: NativeCliPin, help_text: str, *, config: SubscriptionConfig | None = None,
                                catalog: Path | None = None) -> bool:
    configured = config or SubscriptionConfig(provider="claude_subscription", model="claude-sonnet-5-5")
    provider = configured.provider
    if not supports_required_flags(help_text, provider):
        return False
    tools = configured.department_tools.get("research", [])
    request = ModelRequest(role="research", task_id="synthetic-probe", root_task_id="synthetic-probe",
        run_id="synthetic-probe", system_version_id="synthetic-probe",
        provider="openai" if provider == "codex_subscription" else "anthropic", model=configured.model,
        instructions="Command inspection only; do not dispatch.", context={}, output_schema={"type": "object"},
        schema_name="SyntheticProbe", max_output_tokens=configured.maximum_output_tokens,
        max_tool_calls=configured.maximum_tool_calls if tools else 0, timeout_seconds=configured.maximum_seconds)
    if provider == "codex_subscription":
        if catalog is None or not catalog_document_verified(json.loads(catalog.read_bytes()), request.model):
            return False
        with tempfile.TemporaryDirectory(prefix="tg-probe-schema-") as directory:
            schema = Path(directory) / "schema.json"
            schema.write_text(json.dumps(request.output_schema), encoding="utf-8")
            schema.chmod(0o600)
            arguments = codex_command("/cli/runner", request)[1:]
            boundary = LinuxFilesystemBoundary(native, share_network=False, provider=provider,
                schema_file=schema, model_catalog_file=catalog)
            command = boundary.command(arguments)
            settings = {}
            for index, value in enumerate(command[:-1]):
                if value == "-c":
                    name, encoded = command[index + 1].split("=", 1)
                    settings[name] = json.loads(encoded)
            expected = {"forced_login_method": "chatgpt", "model_provider": "openai",
                "web_search": "live" if request.max_tool_calls else "disabled",
                "model_catalog_json": "/request/model-catalog.json", "agents.enabled": False,
                "history.persistence": "none", "tools.update_plan.enabled": False,
                "tools.experimental_request_user_input.enabled": False}
            for name in ("shell_tool", "unified_exec", "apps", "view_image", "image_generation", "code_mode",
                         "code_mode_only", "code_mode_host", "multi_agent_v2", "plugins", "shell_snapshot"):
                expected["features." + name] = False
            if (any(settings.get(name) != value for name, value in expected.items())
                    or command[command.index("--sandbox") + 1] != "read-only"
                    or command[command.index("--output-schema") + 1] != "/request/schema.json"):
                return False
            accepted = bounded_parser_diagnostic(boundary, arguments)
            rejected = bounded_parser_diagnostic(boundary, [*arguments, "--trade-graph-invalid-probe-flag"])
        return parser_controls_verified(accepted, rejected, provider)
    arguments = claude_command("/cli/runner", request, configured)[1:]
    environment = claude_environment(request.max_output_tokens, configured)
    boundary = LinuxFilesystemBoundary(native, share_network=False, environment=environment, provider=provider)
    command = boundary.command(arguments)
    for name, expected in {"--tools": "", "--disallowedTools": "mcp__*", "--mcp-config": '{"mcpServers":{}}',
                           "--setting-sources": "", "--max-turns": str(configured.maximum_turns)}.items():
        if command[command.index(name) + 1] != expected:
            return False
    if (environment["CLAUDE_CODE_MAX_RETRIES"] != str(configured.cli_transport_retries)
            or environment["MAX_STRUCTURED_OUTPUT_RETRIES"] != str(configured.cli_structured_output_attempts)
            or environment["CLAUDE_CODE_NONSTREAMING_TIMEOUT_RETRIES"] != str(configured.cli_transport_retries)):
        return False
    accepted = bounded_parser_diagnostic(boundary, arguments)
    rejected = bounded_parser_diagnostic(boundary, [*arguments, "--trade-graph-invalid-probe-flag"])
    if not parser_controls_verified(accepted, rejected):
        return False
    settings = json.loads(command[command.index("--settings") + 1])
    return (settings == {"switchModelsOnFlag": False, "availableModels": [request.model], "fallbackModel": []}
            and "--strict-mcp-config" in command and "--no-session-persistence" in command
            and "--restricted" in command and "--safe-mode" in command)


def run_probe(native: NativeCliPin, expected_version: str, proxy_ip: str, provider_port: int, market_port: int, *,
              config: SubscriptionConfig | None = None, catalog: Path | None = None, catalog_sha256: str = "") -> dict:
    """Only fixed synthetic paths are read. No login or runtime database is opened."""
    validate_network(proxy_ip, provider_port, market_port)
    configured = config or SubscriptionConfig(provider="claude_subscription", model="claude-sonnet-5-5")
    provider = configured.provider
    if provider == "codex_subscription":
        if catalog is None:
            raise ValueError("Codex public model catalog required")
        verify_catalog(catalog, catalog_sha256, configured.model)
    checks = {name: False for name in CHECKS}
    blockers = []
    if sys.platform != "linux" or os.getuid() != 10001 or os.getgid() != 10001:
        raise ValueError("actual production UID/GID 10001:10001 required")
    if any(name in os.environ for name in ENV_NAMES[:-1]):
        raise ValueError("credential/interop environment names refused in credential-free probe")
    native.verify()
    if WINDOWS_CANARY.is_symlink() or WINDOWS_CANARY.read_bytes() != CANARY:
        raise ValueError("exact synthetic Windows mount canary required")
    environment = (claude_environment(configured.maximum_output_tokens, configured)
                   if provider == "claude_subscription" else {"CODEX_DISABLE_UPDATE_CHECK": "1"})
    native_boundary = LinuxFilesystemBoundary(native, share_network=False, environment=environment, provider=provider)
    version = native_boundary.run(["--version"], b"", maximum_seconds=10)
    help_result = native_boundary.run(["exec", "--help"] if provider == "codex_subscription" else ["--help"],
                                      b"", maximum_seconds=10)
    version_ok = version.exit_code == 0 and not version.stopped and re.search(
        r"(?<![0-9.])" + re.escape(expected_version) + r"(?![0-9.])", version.stdout)
    checks["tools_disabled"] = bool(version_ok and help_result.exit_code == 0 and not help_result.stopped
        and tool_configuration_verified(native, help_result.stdout, config=configured, catalog=catalog))
    if not checks["tools_disabled"]:
        blockers.append("native offline version/help or exact tool configuration refused")

    interpreter_path = Path(sys.executable).resolve()
    interpreter = NativeCliPin(interpreter_path, hashlib.sha256(interpreter_path.read_bytes()).hexdigest())
    boundary = ProbeInterpreterBoundary(interpreter, share_network=False)
    with tempfile.TemporaryDirectory(prefix="tg-subscription-probe-", dir="/tmp") as temporary:
        canary = Path(temporary) / "synthetic-private-canary"
        canary.write_bytes(CANARY)
        payload = {"private": str(canary), "parent_pid": os.getpid(),
                   "parent_pid_namespace": os.readlink("/proc/self/ns/pid"), "environment_names": ENV_NAMES}
        os.environ["TRADE_GRAPH_PROBE_PARENT_ONLY"] = "synthetic-parent-only"
        try:
            observed = boundary.run(["-I", "-B", "-c", FILESYSTEM_CODE],
                json.dumps(payload).encode() + b"\n", maximum_seconds=10)
        finally:
            os.environ.pop("TRADE_GRAPH_PROBE_PARENT_ONLY", None)
        parsed = filesystem_observations(observed.stdout) if observed.exit_code == 0 and not observed.stopped else None
        if parsed:
            checks.update(parsed)
        else:
            blockers.append("nested namespace/filesystem/environment probe refused; "
                            "check custom seccomp and userns policy")

    descendants = boundary.run(["-I", "-B", "-c", DESCENDANT_CODE], b"", maximum_seconds=1)
    try:
        namespace = json.loads(descendants.stdout)["pid_namespace"]
        valid_namespace = type(namespace) is str and re.fullmatch(r"pid:\[[0-9]+\]", namespace)
        deadline = time.monotonic() + 2
        while valid_namespace and namespace_exists(namespace) and time.monotonic() < deadline:
            time.sleep(0.02)
        checks["descendants_killed"] = bool(valid_namespace and descendants.stopped == "timeout"
            and descendants.exit_code != 0 and not namespace_exists(namespace))
    except (ValueError, TypeError, KeyError, RecursionError):
        pass
    if not checks["descendants_killed"]:
        blockers.append("detached descendant namespace survived or nested process probe refused")

    shared_network = ProbeInterpreterBoundary(interpreter, share_network=True).run(
        ["-I", "-B", "-c", "import os; print(os.readlink('/proc/self/ns/net'))"], b"", maximum_seconds=5)
    network_matches = (shared_network.exit_code == 0 and not shared_network.stopped
                       and shared_network.stdout.strip() == os.readlink("/proc/self/ns/net"))
    if not network_matches:
        blockers.append("production shared-network namespace probe refused")

    direct_denied = []
    for address in ("1.1.1.1", "8.8.8.8"):
        try:
            with socket.create_connection((address, 443), timeout=2):
                direct_denied.append(False)
        except OSError:
            direct_denied.append(True)
    checks["direct_egress_denied"] = network_matches and all(direct_denied)
    if not checks["direct_egress_denied"]:
        blockers.append("direct public TCP egress was unexpectedly reachable")

    for name, port, admitted, denied in (
        ("provider_route_restricted", provider_port,
         tuple(sorted(PROVIDER_HOSTS)),
         ("api.kraken.com", "api.frankfurter.dev", "api.openai.com", "example.com", "169.254.169.254")),
        ("market_route_restricted", market_port,
         ("api.kraken.com", "api.frankfurter.dev"),
         (*sorted(PROVIDER_HOSTS), "example.com", "127.0.0.1")),
    ):
        try:
            # Close every admitted CONNECT before any TLS bytes/provider request.
            permitted = all(connect_status(proxy_ip, port, host) == 200 for host in admitted)
            forbidden = all(connect_status(proxy_ip, port, host) == 403 for host in denied)
            checks[name] = permitted and forbidden
        except (OSError, ValueError):
            checks[name] = False
        if not checks[name]:
            blockers.append(name + " admission/rejection probe failed")
    package_sha = protected_package_sha256()
    proof = admission_proof(native.sha256, package_sha, checks)
    return {"schema_version": 1, "kind": "credential-free-actual-image-observations", "checks": checks,
            "blockers": blockers, "admission_proof": proof, "inference_attempts": 0,
            "provider_api_requests": 0, "exchange_api_requests": 0, "live_authorization": False,
            "paid_authorization": False, "provider": provider,
            "configured_limits": configured.model_dump(exclude={"allowed_context_keys"}),
            "provider_proxy_hosts": sorted(PROVIDER_HOSTS),
            "model_catalog_sha256": catalog_sha256 or None,
            "tools_evidence": "native-help-production-host-tool-command-and-offline-parser-controls",
            "test_only_stdlib_bind": True, "probe_only_synthetic_windows_mount": True,
            "production_docker_spec_verification": "separate-required-without-probe-fixture-mounts",
            "native_version_exit_code": version.exit_code, "native_help_exit_code": help_result.exit_code,
            "filesystem_probe_exit_code": observed.exit_code,
            "descendant_probe_exit_code": descendants.exit_code,
            "shared_network_probe_exit_code": shared_network.exit_code}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["claude_subscription", "codex_subscription"],
                        default="claude_subscription")
    parser.add_argument("--model")
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--catalog-sha256", default="")
    parser.add_argument("--maximum-seconds", type=int, default=600)
    parser.add_argument("--maximum-output-tokens", type=int, default=16384)
    parser.add_argument("--maximum-turns", type=int, default=8)
    parser.add_argument("--maximum-output-bytes", type=int, default=2097152)
    parser.add_argument("--cli-transport-retries", type=int, default=2)
    parser.add_argument("--cli-structured-output-attempts", type=int, default=3)
    parser.add_argument("--web-search", action="store_true")
    parser.add_argument("--native-binary", type=Path)
    parser.add_argument("--native-sha256", required=True)
    parser.add_argument("--expected-version")
    parser.add_argument("--proxy-ip", required=True)
    parser.add_argument("--provider-port", type=int, default=8080)
    parser.add_argument("--market-port", type=int, default=8081)
    args = parser.parse_args(argv)
    try:
        is_codex = args.provider == "codex_subscription"
        configured = SubscriptionConfig(provider=args.provider, model=args.model or (
            "gpt-6.1-sol" if is_codex else "claude-sonnet-5-5"), maximum_seconds=args.maximum_seconds,
            maximum_output_tokens=args.maximum_output_tokens, maximum_turns=args.maximum_turns,
            maximum_output_bytes=args.maximum_output_bytes, cli_transport_retries=args.cli_transport_retries,
            cli_structured_output_attempts=args.cli_structured_output_attempts,
            department_tools={"research": ["web_search"]} if args.web_search else {})
        binary = args.native_binary or Path("/opt/trade-graph/codex" if is_codex else "/opt/trade-graph/claude")
        result = run_probe(NativeCliPin(binary, args.native_sha256),
                           args.expected_version or ("0.160.1" if is_codex else "2.1.292"),
                           args.proxy_ip, args.provider_port, args.market_port, config=configured,
                           catalog=args.catalog, catalog_sha256=args.catalog_sha256)
    except (OSError, ValueError, TypeError):
        print(json.dumps({"kind": "credential-free-actual-image-observations", "admission_proof": None,
                          "blockers": ["actual image/probe prerequisites refused"], "inference_attempts": 0}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result["admission_proof"] is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
