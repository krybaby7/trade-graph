import json
from datetime import UTC, datetime

import pytest

from trade_graph.adapters.models.subscription import (
    CliOutcome,
    SubscriptionAdapter,
    SubscriptionConfig,
    SubscriptionJournal,
    SubscriptionReadiness,
    parse_codex_outcome,
    subscription_failure_diagnostic,
)
from trade_graph.adapters.persistence.db import Database
from trade_graph.contracts.models import ModelRequest
from trade_graph.domain.clock import FrozenClock

SCHEMA = {"type": "object", "properties": {"actions": {"type": "array", "items": {
    "type": "object", "properties": {"amount": {"type": "string"}},
    "required": ["amount"], "additionalProperties": False}}},
    "required": ["actions"], "additionalProperties": False}


def request():
    return ModelRequest(role="leader", task_id="task", root_task_id="root", run_id="run",
        system_version_id="version", provider="openai", model="gpt-6.1-sol",
        instructions="Use synthetic public data", context={"market": {}}, output_schema=SCHEMA,
        schema_name="LeaderReply", max_output_tokens=16384, max_tool_calls=0, timeout_seconds=600)


def error(code="invalid_json_schema", context="('properties', 'actions', 'items', 'properties', 'amount')"):
    return {"error": {"code": code, "type": "invalid_request_error", "param": "text.format.schema",
        "message": "Invalid schema for response_format 'PRIVATE_ACCOUNT': In context=" + context +
            ", pattern is unsupported. PRIVATE_TOKEN private@example.invalid https://private.invalid"}}


def test_native_failure_diagnostic_retains_only_known_code_and_resolved_schema_path():
    raw = "unexpected status 400 Bad Request: " + json.dumps(error())
    result = subscription_failure_diagnostic("", raw, schema=SCHEMA)
    assert result == ("invalid_schema", "invalid_json_schema", "#/properties/actions/items/properties/amount")
    assert "PRIVATE" not in repr(result) and "private" not in repr(result)


def test_embedded_native_error_in_turn_failed_is_sanitized_without_raw_message():
    raw = json.dumps({"type": "turn.failed", "error": {"message": "unexpected status 400: " + json.dumps(error())}})
    result = parse_codex_outcome(CliOutcome(raw, 1), request())
    assert result.failure == "validation" and result.usage is None
    assert "invalid_json_schema" in result.message and "#/properties/actions/items/properties/amount" in result.message
    assert "PRIVATE" not in result.model_dump_json() and "private" not in result.model_dump_json()
    assert result.raw_redacted == "" and result.payload is None


def test_error_with_null_code_uses_explicit_schema_category_without_inventing_code():
    result = subscription_failure_diagnostic("", json.dumps(error(None)), schema=SCHEMA)
    assert result == ("invalid_schema", "", "#/properties/actions/items/properties/amount")


@pytest.mark.parametrize("context", [
    "('PRIVATE_TOKEN',)", "('properties', 'PRIVATE_TOKEN')", "__import__('os').environ",
])
def test_unresolvable_or_executable_schema_context_is_never_retained(context):
    assert subscription_failure_diagnostic("", json.dumps(error(context=context)), schema=SCHEMA) == (
        "invalid_schema", "invalid_json_schema", "")


def test_unknown_codes_and_successful_agent_text_are_not_failure_evidence():
    raw = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(error())}})
    assert subscription_failure_diagnostic(raw, "", schema=SCHEMA) == ("", "", "")
    assert subscription_failure_diagnostic("", json.dumps({"error": {"code": "PRIVATE_TOKEN", "message": "PRIVATE"}}),
        schema=SCHEMA) == ("", "", "")


def test_native_diagnostic_fields_are_sanitized_again_before_durable_receipt():
    result = parse_codex_outcome(CliOutcome("", 1, error_category="invalid_schema", error_code="PRIVATE_TOKEN",
        schema_path="#/PRIVATE_TOKEN"), request())
    assert result.failure == "validation"
    assert "PRIVATE" not in result.model_dump_json()


def test_proven_invalid_schema_is_one_actual_attempt_not_transient_retry(tmp_path):
    database = Database(tmp_path / "state.sqlite")
    journal = SubscriptionJournal(database, FrozenClock(datetime(2026, 10, 8, tzinfo=UTC)))
    class Executor:
        calls = 0
        def execute(self, request, *, cancel_event=None):
            self.calls += 1
            return CliOutcome(json.dumps({"type": "turn.failed", "error": error()["error"]}), 1,
                process_terminated=True)
    executor = Executor()
    config = SubscriptionConfig(provider="codex_subscription", model="gpt-6.1-sol", enabled=True,
        application_max_attempts=3, allowed_context_keys={"leader": ["market"]})
    readiness = SubscriptionReadiness("codex_subscription", "0.160.1", True, (), {}, "chatgpt", "linux-bubblewrap")
    adapter = SubscriptionAdapter(config, readiness, executor)
    result = adapter.invoke(request(), invocation_id="leader-failed-schema", journal=journal)
    assert result.failure == "validation" and executor.calls == 1
    rows = database.execute("SELECT state,result_json,usage_json FROM subscription_attempts").fetchall()
    assert len(rows) == 1 and rows[0]["state"] == "FAILED" and rows[0]["usage_json"] is None
    assert "invalid_json_schema" in rows[0]["result_json"] and "PRIVATE" not in rows[0]["result_json"]
    assert not journal.provider_status("codex_subscription")["ai_paused"]
    database.close()


def test_native_top_level_error_event_exposes_only_sanitized_diagnostic():
    raw = json.dumps({**error()["error"], "type": "error"})
    result = parse_codex_outcome(CliOutcome(raw, 1), request())
    assert result.failure == "validation" and "invalid_json_schema" in result.message
    assert "PRIVATE" not in result.model_dump_json()


def test_actual_subprocess_stderr_diagnostic_reaches_receipt_without_raw_text(tmp_path, monkeypatch):
    import sys
    from pathlib import Path

    from trade_graph.adapters.models.subscription_process import LinuxFilesystemBoundary, NativeCliPin
    schema_file = tmp_path / "schema.json"
    schema_file.write_text(json.dumps(SCHEMA))
    boundary = LinuxFilesystemBoundary(NativeCliPin(Path(sys.executable), "0" * 64, require_root_owner=False),
        share_network=False, provider="codex_subscription", schema_file=schema_file)
    code = "import sys;sys.stderr.write(" + repr(json.dumps(error())) + ");sys.exit(1)"
    monkeypatch.setattr(boundary, "command", lambda _: [sys.executable, "-I", "-c", code])
    outcome = boundary.run([], b"", maximum_seconds=3)
    assert outcome.stdout == "" and outcome.process_terminated
    assert outcome.error_code == "invalid_json_schema" and outcome.error_category == "invalid_schema"
    assert "PRIVATE" not in repr(outcome)
    result = parse_codex_outcome(outcome, request())
    assert result.failure == "validation" and "invalid_json_schema" in result.message
    assert "PRIVATE" not in result.model_dump_json()


def test_sanitized_stderr_schema_diagnostic_survives_malformed_stdout():
    result = parse_codex_outcome(CliOutcome("not-json", 1, error_category="invalid_schema",
        error_code="invalid_json_schema", schema_path="#/properties/actions"), request())
    assert result.failure == "validation" and "invalid_json_schema" in result.message
    assert "#/properties/actions" in result.message and "not-json" not in result.model_dump_json()


@pytest.mark.parametrize("code,failure,reason", [
    ("model_not_found", "unsupported", "subscription model unavailable"),
    ("rate_limit_exceeded", "rate_limit", "subscription quota exhausted"),
    ("quota_exceeded", "rate_limit", "subscription quota exhausted"),
    ("usage_limit_reached", "rate_limit", "subscription quota exhausted"),
])
@pytest.mark.parametrize("source", ["native_jsonl", "native_stderr_fields"])
def test_native_availability_failure_keeps_canonical_protected_service_contract(
    tmp_path, code, failure, reason, source,
):
    from types import SimpleNamespace

    from trade_graph.application.paper_service import PaperService
    database = Database(tmp_path / "availability.sqlite")
    journal = SubscriptionJournal(database, FrozenClock(datetime(2026, 10, 8, tzinfo=UTC)))
    class Executor:
        calls = 0
        def execute(self, request, *, cancel_event=None):
            self.calls += 1
            if source == "native_jsonl":
                return CliOutcome(json.dumps({"type": "turn.failed", "error": {"code": code}}), 1,
                    process_terminated=True)
            return CliOutcome("", 1, error_category="provider_error", error_code=code, process_terminated=True)
    executor = Executor()
    config = SubscriptionConfig(provider="codex_subscription", model="gpt-6.1-sol", enabled=True,
        application_max_attempts=3, allowed_context_keys={"leader": ["market"]})
    readiness = SubscriptionReadiness("codex_subscription", "0.160.1", True, (), {}, "chatgpt", "linux-bubblewrap")
    adapter = SubscriptionAdapter(config, readiness, executor)
    result = adapter.invoke(request(), invocation_id="availability-failure", journal=journal)
    assert result.failure == failure and result.message == reason and executor.calls == 1
    assert journal.provider_status("codex_subscription")["reason"] == reason
    assert adapter.public_status(journal=journal)["available_subscription_routes"] == []
    service = SimpleNamespace(database=database, subscription_provider="codex_subscription")
    assert PaperService._subscription_availability_failure(service, "task")
    database.execute("UPDATE subscription_provider_state SET reason=? WHERE provider=?",
        (reason + " (untrusted suffix)", "codex_subscription"))
    assert not PaperService._subscription_availability_failure(service, "task")
    assert database.execute("SELECT COUNT(*) FROM subscription_attempts").fetchone()[0] == 1
    assert database.execute("SELECT COUNT(*) FROM model_invocations").fetchone()[0] == 0
    database.close()


@pytest.mark.parametrize("code,failure,reason", [
    ("model_not_found", "unsupported", "subscription model unavailable"),
    ("rate_limit_exceeded", "rate_limit", "subscription quota exhausted"),
    ("quota_exceeded", "rate_limit", "subscription quota exhausted"),
])
def test_trusted_native_availability_diagnostic_survives_malformed_stdout(code, failure, reason):
    result = parse_codex_outcome(CliOutcome("not-json", 1, error_category="provider_error", error_code=code), request())
    assert result.failure == failure and result.message == reason and result.usage is None
    assert "not-json" not in result.model_dump_json()


def test_stderr_nonerror_event_cannot_promote_nested_agent_content_to_diagnostic():
    raw = json.dumps({"type": "item.completed", "item": {"type": "agent_message", **error()}})
    assert subscription_failure_diagnostic("", raw, schema=SCHEMA) == ("", "", "")
    typed = json.dumps({"type": "item.completed", **error()})
    assert subscription_failure_diagnostic("", typed, schema=SCHEMA) == ("", "", "")


@pytest.mark.parametrize("exit_code", [1, 2, -9])
def test_native_terminal_failure_retains_bounded_exit_and_sanitized_cause_in_receipt(tmp_path, exit_code):
    database = Database(tmp_path / "native-cause.sqlite")
    journal = SubscriptionJournal(database, FrozenClock(datetime(2026, 10, 8, tzinfo=UTC)))
    class Executor:
        def execute(self, request, *, cancel_event=None):
            return CliOutcome(json.dumps({"type": "turn.failed", "error": error()["error"]}), exit_code,
                              process_terminated=True)
    config = SubscriptionConfig(provider="codex_subscription", model="gpt-6.1-sol", enabled=True,
        application_max_attempts=1, allowed_context_keys={"leader": ["market"]})
    readiness = SubscriptionReadiness("codex_subscription", "0.160.1", True, (), {}, "chatgpt", "linux-bubblewrap")
    result = SubscriptionAdapter(config, readiness, Executor()).invoke(
        request(), invocation_id="safe-native-cause", journal=journal)
    assert result.diagnostic.model_dump() == {
        "native_exit_code": exit_code, "category": "invalid_schema", "error_code": "invalid_json_schema",
        "schema_path": "#/properties/actions/items/properties/amount"}
    retained = json.loads(database.execute("SELECT result_json FROM subscription_attempts").fetchone()[0])
    assert retained["diagnostic"] == result.diagnostic.model_dump()
    assert "PRIVATE" not in json.dumps(retained) and "private" not in json.dumps(retained)
    database.close()


def test_native_generic_failure_retains_exit_without_guessing_original_cause():
    result = parse_codex_outcome(CliOutcome('{"type":"turn.failed","error":{"message":"PRIVATE"}}', 2), request())
    assert result.failure == "temporary" and result.message == "subscription CLI failed"
    assert result.diagnostic.model_dump() == {
        "native_exit_code": 2, "category": "native_failure", "error_code": "", "schema_path": ""}
    assert "PRIVATE" not in result.model_dump_json()


def test_native_availability_diagnostic_keeps_exact_control_message_and_exit():
    result = parse_codex_outcome(
        CliOutcome("", 1, error_category="provider_error", error_code="model_not_found"), request())
    assert result.message == "subscription model unavailable"
    assert result.diagnostic.native_exit_code == 1
    assert result.diagnostic.error_code == "model_not_found"


def test_native_untrusted_diagnostic_fields_and_unbounded_exit_are_not_retained():
    result = parse_codex_outcome(CliOutcome("not-json", 10**20, error_category="PRIVATE", error_code="PRIVATE",
                                           schema_path="#/PRIVATE"), request())
    assert result.diagnostic.native_exit_code is None
    assert result.diagnostic.category == "invalid_output"
    assert result.diagnostic.error_code == "" and result.diagnostic.schema_path == ""
    assert "PRIVATE" not in result.model_dump_json() and "not-json" not in result.model_dump_json()


def test_absent_native_diagnostic_does_not_change_legacy_result_serialization():
    from trade_graph.contracts.models import ModelResult
    result = ModelResult(ok=True, payload={"actions": []})
    assert "diagnostic" not in result.model_dump() and "diagnostic" not in result.model_dump_json()
    assert ModelResult.model_validate_json(result.model_dump_json()).diagnostic is None
