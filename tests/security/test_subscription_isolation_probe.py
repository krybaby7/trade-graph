"""Scripted probe decisions and loopback CONNECT checks need no credentials."""

import importlib.util
import json
import socketserver
import threading
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/probe_subscription_isolation.py"


def probe():
    assert SCRIPT.exists(), "credential-free actual-image isolation probe is missing"
    spec = importlib.util.spec_from_file_location("subscription_isolation_probe", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_partial_or_false_observations_never_emit_admission_proof():
    module = probe()
    checks = {name: True for name in module.CHECKS}
    proof = module.admission_proof("a" * 64, "b" * 64, checks)
    assert proof["kind"] == "actual-linux-boundary"
    assert set(proof) == {"schema_version", "kind", "native_cli_sha256", "protected_package_sha256", "checks"}
    assert set(proof["checks"]) == module.CHECKS
    for name in module.CHECKS:
        altered = {**checks, name: False}
        assert module.admission_proof("a" * 64, "b" * 64, altered) is None
        altered.pop(name)
        assert module.admission_proof("a" * 64, "b" * 64, altered) is None
    assert module.admission_proof("a" * 64, "b" * 64, {**checks, "unreviewed": True}) is None
    assert module.admission_proof("a" * 64, "b" * 64, {**checks, "tools_disabled": 1}) is None


@pytest.mark.parametrize("ip,provider,market", [
    ("127.0.0.1", 8080, 8081), ("169.254.169.254", 8080, 8081),
    ("8.8.8.8", 8080, 8081), ("host.example", 8080, 8081),
    ("172.30.0.2", 8080, 8080), ("172.30.0.2", 443, 8081),
])
def test_probe_requires_exact_private_proxy_and_distinct_unprivileged_ports(ip, provider, market):
    with pytest.raises(ValueError):
        probe().validate_network(ip, provider, market)


def test_native_tool_capability_requires_every_exact_help_flag():
    module = probe()
    help_text = "\n".join(flag + " <argument>" for flag in module.REQUIRED_FLAGS)
    assert module.supports_required_flags(help_text)
    for flag in module.REQUIRED_FLAGS:
        assert not module.supports_required_flags(help_text.replace(flag + " ", flag + "-unrelated "))


def test_connect_probe_closes_after_headers_without_provider_tls_or_request_payload():
    module = probe()
    observed = []

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(1)
            header = b""
            while not header.endswith(b"\r\n\r\n"):
                header += self.request.recv(1)
            self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            observed.append((header, self.request.recv(1)))

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        assert module.connect_status("127.0.0.1", server.server_address[1], "api.anthropic.com") == 200
        server.shutdown()
        assert observed == [(b"CONNECT api.anthropic.com:443 HTTP/1.1\r\nHost: api.anthropic.com:443\r\n\r\n", b"")]
    finally:
        server.server_close()
        worker.join()


@pytest.mark.parametrize("document", [
    {"private_files_denied": True},
    {"private_files_denied": True, "windows_mounts_denied": True, "host_proc_denied": False,
     "api_environment_denied": True},
    {"private_files_denied": True, "windows_mounts_denied": True, "host_proc_denied": True,
     "api_environment_denied": 1},
])
def test_malformed_scripted_filesystem_results_are_refused(document):
    module = probe()
    assert module.filesystem_observations(json.dumps(document)) is None


def test_filesystem_observations_require_only_the_four_reviewed_true_fields():
    module = probe()
    document = {name: True for name in module.FILESYSTEM_CHECKS}
    assert module.filesystem_observations(json.dumps(document)) == document
    assert module.filesystem_observations("not-json") is None


def test_hidden_max_turns_is_verified_by_offline_parser_controls_instead_of_help_advertising():
    module = probe()
    assert "--max-turns" not in module.REQUIRED_FLAGS
    accepted = (1, "Error: Input must be provided either through stdin or as a prompt argument when using --print\n")
    rejected = (1, "error: unknown option '--trade-graph-invalid-probe-flag'\n")
    assert module.parser_controls_verified(accepted, rejected)
    assert not module.parser_controls_verified((0, "help output"), (0, "help output"))
    assert not module.parser_controls_verified(rejected, rejected)
    assert not module.parser_controls_verified(accepted, accepted)
    assert not module.parser_controls_verified((1, "credentials missing"), rejected)


def test_parser_diagnostic_has_empty_stdin_and_captures_only_bounded_error_output():
    import sys
    from types import SimpleNamespace

    module = probe()
    code = ("import sys; assert sys.stdin.read() == ''; "
            "sys.stderr.write('synthetic parser refusal'); sys.exit(1)")
    boundary = SimpleNamespace(command=lambda args: [sys.executable, "-I", "-c", code])
    assert module.bounded_parser_diagnostic(boundary, []) == (1, "synthetic parser refusal")


def test_parser_diagnostic_refuses_timeout_and_output_flood():
    import sys
    from types import SimpleNamespace

    module = probe()
    for code in ["import time; time.sleep(2)", "print('x'*9000)"]:
        boundary = SimpleNamespace(command=lambda args, code=code: [sys.executable, "-I", "-c", code])
        assert module.bounded_parser_diagnostic(boundary, [], maximum_seconds=0.1, maximum_bytes=64) == (-1, "")


def test_codex_help_controls_are_exec_specific_and_complete():
    module = probe()
    help_text = "\n".join(flag + " <argument>" for flag in module.CODEX_REQUIRED_FLAGS)
    assert module.supports_required_flags(help_text, "codex_subscription")
    for flag in module.CODEX_REQUIRED_FLAGS:
        assert not module.supports_required_flags(help_text.replace(flag + " ", flag + "-other "),
                                                 "codex_subscription")


def test_codex_empty_input_parser_refuses_before_authentication_or_inference():
    module = probe()
    accepted = (1, "No prompt provided via stdin.\n")
    rejected = (2, "error: unexpected argument '--trade-graph-invalid-probe-flag' found\n\n"
        "  tip: to pass '--trade-graph-invalid-probe-flag' as a value, use "
        "'-- --trade-graph-invalid-probe-flag'\n\n"
        "Usage: codex exec [OPTIONS] [PROMPT]\n"
        "       codex exec [OPTIONS] <COMMAND> [ARGS]\n\n"
        "For more information, try '--help'.\n")
    assert module.parser_controls_verified(accepted, rejected, "codex_subscription")
    assert not module.parser_controls_verified((0, "help output"), rejected, "codex_subscription")
    assert not module.parser_controls_verified((1, "credentials missing"), rejected, "codex_subscription")


def catalog():
    return {"models": [{"slug": "gpt-6.1-sol", "shell_type": "disabled", "apply_patch_tool_type": None,
        "experimental_supported_tools": [], "tool_mode": "direct", "node_repl_disabled": True,
        "supports_search_tool": False, "multi_agent_version": None, "multi_agent_reasoning_effort": None,
        "include_apps_usage_instructions": True, "include_plugin_usage_instructions": True,
        "include_skills_usage_instructions": True}]}


@pytest.mark.parametrize("field,value", [
    ("slug", "different-model"), ("shell_type", "shell_command"), ("apply_patch_tool_type", "freeform"),
    ("experimental_supported_tools", ["filesystem"]), ("tool_mode", "code_mode"),
    ("node_repl_disabled", False), ("supports_search_tool", True),
    ("multi_agent_version", "v2"), ("multi_agent_reasoning_effort", "high"),
])
def test_codex_catalog_cannot_retain_filesystem_patch_or_plugin_capabilities(field, value):
    module = probe()
    document = catalog()
    assert module.catalog_document_verified(document, "gpt-6.1-sol")
    document["models"][0][field] = value
    assert not module.catalog_document_verified(document, "gpt-6.1-sol")


@pytest.mark.parametrize("provider", ["codex_subscription", "claude_subscription"])
def test_probe_verifies_configured_normal_limits_instead_of_one_turn_zero_retries(monkeypatch, tmp_path, provider):
    from trade_graph.adapters.models.subscription import SubscriptionConfig
    module = probe()
    observed = []
    class Boundary:
        def __init__(self, native, **kwargs):
            observed.append(kwargs)
        def command(self, arguments):
            return ["/cli/runner", *arguments]
    monkeypatch.setattr(module, "LinuxFilesystemBoundary", Boundary)
    accepted = (1, "No prompt provided via stdin.\n") if provider == "codex_subscription" else (
        1, "Error: Input must be provided either through stdin or as a prompt argument when using --print\n")
    monkeypatch.setattr(module, "bounded_parser_diagnostic", lambda _boundary, args:
        (2, "synthetic negative") if "--trade-graph-invalid-probe-flag" in args else accepted)
    monkeypatch.setattr(module, "parser_controls_verified", lambda actual, rejected, *_a:
        actual == accepted and rejected == (2, "synthetic negative"))
    config = SubscriptionConfig(provider=provider, model="gpt-6.1-sol" if provider == "codex_subscription"
        else "claude-sonnet-5-5", maximum_turns=24, cli_transport_retries=4, cli_structured_output_attempts=5,
        department_tools={"research": ["web_search"]} if provider == "codex_subscription" else {})
    public_catalog = tmp_path / "models.json"
    public_catalog.write_text(json.dumps(catalog()))
    required = module.CODEX_REQUIRED_FLAGS if provider == "codex_subscription" else module.REQUIRED_FLAGS
    assert module.tool_configuration_verified(None, "\n".join(required), config=config, catalog=public_catalog)
    assert observed[0]["share_network"] is False and observed[0].get("credential_file") is None
    if provider == "claude_subscription":
        assert observed[0]["environment"]["CLAUDE_CODE_MAX_TURNS"] == "24"
        assert observed[0]["environment"]["CLAUDE_CODE_MAX_RETRIES"] == "4"
        assert observed[0]["environment"]["MAX_STRUCTURED_OUTPUT_RETRIES"] == "5"


@pytest.mark.parametrize("field", ["apply_patch_tool_type", "multi_agent_version", "multi_agent_reasoning_effort"])
def test_codex_catalog_requires_explicit_null_tool_control_fields(field):
    module = probe()
    document = catalog()
    document["models"][0].pop(field)
    assert not module.catalog_document_verified(document, "gpt-6.1-sol")


def test_codex_probe_refuses_missing_image_generation_disable(monkeypatch, tmp_path):
    from trade_graph.adapters.models.subscription import SubscriptionConfig
    module = probe()
    original = module.codex_command
    def command(*args, **kwargs):
        arguments = original(*args, **kwargs)
        index = next(i for i, value in enumerate(arguments) if value.startswith("features.image_generation="))
        return arguments[:index - 1] + arguments[index + 1:]
    class Boundary:
        def __init__(self, *args, **kwargs):
            pass
        def command(self, arguments):
            return ["/cli/runner", *arguments]
    monkeypatch.setattr(module, "codex_command", command)
    monkeypatch.setattr(module, "LinuxFilesystemBoundary", Boundary)
    monkeypatch.setattr(module, "bounded_parser_diagnostic", lambda *_a: pytest.fail("unsafe parser command"))
    public_catalog = tmp_path / "models.json"
    public_catalog.write_text(json.dumps(catalog()))
    assert not module.tool_configuration_verified(None, "\n".join(module.CODEX_REQUIRED_FLAGS),
        config=SubscriptionConfig(provider="codex_subscription", model="gpt-6.1-sol"), catalog=public_catalog)
