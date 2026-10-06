import json
from datetime import UTC, datetime
from threading import Event

import pytest

from trade_graph.adapters.models.subscription import (
    CliOutcome,
    SubscriptionAdapter,
    SubscriptionConfig,
    SubscriptionJournal,
    SubscriptionReadiness,
    assess_subscription,
    claude_command,
    claude_environment,
    parse_claude_outcome,
    sanitize_quota,
)
from trade_graph.adapters.persistence.db import Database
from trade_graph.contracts.models import ModelRequest
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import StaleState


def request(**kwargs):
    return ModelRequest(role="research", task_id="task", root_task_id="root", run_id="run",
                        system_version_id="version", provider="anthropic", model="claude-sonnet-5-5",
                        instructions="Summarize public data", context={"market": {"source": "public"}},
                        output_schema={"type": "object", "properties": {"note": {"type": "string"}},
                                       "required": ["note"], "additionalProperties": False},
                        schema_name="research", max_output_tokens=1000, max_tool_calls=0,
                        timeout_seconds=20, **kwargs)


@pytest.fixture
def journal(tmp_path):
    database = Database(tmp_path / "state.sqlite")
    yield SubscriptionJournal(database, FrozenClock(datetime(2026, 10, 6, tzinfo=UTC)))
    database.close()


def ready():
    return SubscriptionReadiness(provider="claude_subscription", cli_version="2.1.285", ready=True,
                                 blockers=(), quota={"remaining_percent": None},
                                 authentication="subscription", isolation="linux-bubblewrap")


class Executor:
    def __init__(self, outcome=None):
        self.calls = 0
        self.outcome = outcome or CliOutcome(stdout='{"type":"result","is_error":false,'
            '"structured_output":{"note":"public observation"},'
            '"modelUsage":{"claude-sonnet-5-5":{"inputTokens":7,"outputTokens":9}}}', exit_code=0)

    def execute(self, request, *, cancel_event=None):
        self.calls += 1
        return self.outcome


def adapter(executor, readiness=None):
    return SubscriptionAdapter(SubscriptionConfig(provider="claude_subscription", model="claude-sonnet-5-5",
                               enabled=True, allowed_context_keys={"research": ["market"]}),
                               readiness or ready(), executor)


def test_codex_builtin_retry_blocker_cannot_be_overridden():
    status = assess_subscription(SubscriptionConfig(provider="codex_subscription", model="gpt-6.1-sol", enabled=True),
        cli_version="0.160.1", authentication="chatgpt", quota={"remaining_percent": 80},
        extra_usage_disabled=True, isolation_ready=True, native_linux=True)
    assert not status.ready
    assert any("built-in" in blocker and "retry" in blocker for blocker in status.blockers)


def test_claude_admission_requires_native_login_no_extra_usage_and_recent_cli():
    config = SubscriptionConfig(provider="claude_subscription", model="claude-sonnet-5-5", enabled=True)
    status = assess_subscription(config, cli_version="2.1.280", authentication="none", quota={},
                                extra_usage_disabled=False, isolation_ready=False, native_linux=False)
    assert not status.ready
    assert len(status.blockers) >= 5


def test_success_persisted_with_actual_model_and_unknown_subscription_cost(journal):
    executor = Executor()
    model = adapter(executor)
    result = model.invoke(request(), invocation_id="one", journal=journal)
    assert result.ok and result.provider_model == "claude-sonnet-5-5"
    assert result.usage.uncached_input_tokens == 7
    assert result.usage.billed_output_tokens == 9
    assert model.invoke(request(), invocation_id="one", journal=journal) == result
    assert executor.calls == 1
    row = journal.row("one")
    assert row["state"] == "COMPLETED"
    assert row["cost_status"] == "unknown" and row["actual_cost_native"] is None
    assert row["synthetic"] == 0


def test_reused_identity_changed_request_never_dispatches(journal):
    executor = Executor()
    model = adapter(executor)
    model.invoke(request(), invocation_id="one", journal=journal)
    changed = request().model_copy(update={"instructions": "Changed"})
    with pytest.raises(StaleState):
        model.invoke(changed, invocation_id="one", journal=journal)
    assert executor.calls == 1


@pytest.mark.parametrize("output", [
    '{"type":"result","is_error":false,"structured_output":{"note":1}}',
    '{"type":"result","is_error":false,"structured_output":{"note":"x","extra":1}}',
    '{"type":"result","is_error":false,"structured_output":{"note":"a","note":"b"}}',
    '{"type":"result","is_error":false,"structured_output":{"note":"x"},"modelUsage":'
    '{"another-model":{"inputTokens":1,"outputTokens":1}}}',
])
def test_invalid_or_fallback_output_fails_without_repairs(journal, output):
    executor = Executor(CliOutcome(stdout=output, exit_code=0))
    result = adapter(executor).invoke(request(), invocation_id="one", journal=journal)
    assert not result.ok and result.failure == "validation"
    assert executor.calls == 1


def test_quota_failure_persists_stop_new_ai_admission(journal):
    executor = Executor(CliOutcome(stdout='{"type":"result","is_error":true,"error":"rate_limit"}', exit_code=1))
    model = adapter(executor)
    first = model.invoke(request(), invocation_id="one", journal=journal)
    second = model.invoke(request().model_copy(update={"task_id": "next"}), invocation_id="two", journal=journal)
    assert first.failure == second.failure == "rate_limit"
    assert executor.calls == 1
    assert journal.provider_status("claude_subscription")["ai_paused"]


@pytest.mark.parametrize("reason", ["timeout", "cancelled", "output_limit"])
def test_uncertain_completion_retains_unknown_usage_and_never_replays(journal, reason):
    executor = Executor(CliOutcome(stdout="", exit_code=-9, stopped=reason))
    model = adapter(executor)
    assert model.invoke(request(), invocation_id="one", journal=journal).failure == "timeout_uncertain"
    assert model.invoke(request(), invocation_id="one", journal=journal).failure == "timeout_uncertain"
    assert journal.row("one")["state"] == "UNCERTAIN" and executor.calls == 1


def test_crashed_dispatch_recovery_never_calls_model(journal):
    executor = Executor()
    model = adapter(executor)
    journal.begin("one", request(), "claude_subscription", ready().quota)
    result = model.invoke(request(), invocation_id="one", journal=journal)
    assert result.failure == "timeout_uncertain" and executor.calls == 0


def test_blocked_route_and_non_allowlisted_inputs_do_not_dispatch(journal):
    executor = Executor()
    blocked = SubscriptionReadiness(provider="claude_subscription", cli_version="2.1.280", ready=False,
        blockers=("not logged in",), quota={}, authentication="none", isolation="unavailable")
    blocked_result = adapter(executor, blocked).invoke(request(), invocation_id="blocked", journal=journal)
    assert blocked_result.failure == "credentials"
    unsafe = request().model_copy(update={"context": {"database": "/private/state.sqlite"}})
    assert adapter(executor).invoke(unsafe, invocation_id="unsafe", journal=journal).failure == "validation"
    assert executor.calls == 0


def test_pre_cancel_does_not_dispatch(journal):
    executor = Executor()
    event = Event()
    event.set()
    result = adapter(executor).invoke(request(), invocation_id="cancelled", journal=journal, cancel_event=event)
    assert not result.ok and executor.calls == 0
    assert journal.row("cancelled")["cost_status"] == "not_incurred"


def test_claude_supported_controls_disable_all_retry_repair_tools_and_fallback():
    command = claude_command("/cli/claude", request())
    assert command[command.index("--max-turns") + 1] == "1"
    assert command[command.index("--tools") + 1] == ""
    assert "--fallback-model" not in command
    settings = json.loads(command[command.index("--settings") + 1])
    assert settings["availableModels"] == [request().model]
    assert settings["fallbackModel"] == []
    environment = claude_environment(1000)
    assert environment["CLAUDE_CODE_MAX_RETRIES"] == "0"
    assert environment["MAX_STRUCTURED_OUTPUT_RETRIES"] == "1"
    assert environment["CLAUDE_CODE_NONSTREAMING_TIMEOUT_RETRIES"] == "0"
    assert environment["CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK"] == "1"
    assert not any(key in environment for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "PATH"))


def test_schema_failure_retains_known_actual_model_usage(journal):
    outcome = CliOutcome(stdout='{"type":"result","is_error":false,"structured_output":{"note":1},'
        '"modelUsage":{"claude-sonnet-5-5":{"inputTokens":7,"outputTokens":9}}}', exit_code=0)
    result = adapter(Executor(outcome)).invoke(request(), invocation_id="one", journal=journal)
    assert not result.ok and result.provider_model == "claude-sonnet-5-5"
    assert result.usage.uncached_input_tokens == 7 and result.usage.billed_output_tokens == 9
    assert journal.row("one")["usage_json"] is not None


def test_failed_cli_retains_valid_usage_and_quota_phrase_pauses(journal):
    outcome = CliOutcome(stdout='{"type":"result","is_error":true,"subtype":"error_during_execution",'
        '"result":"You have hit your limit",'
        '"modelUsage":{"claude-sonnet-5-5":{"inputTokens":7,"outputTokens":9}}}', exit_code=1)
    result = adapter(Executor(outcome)).invoke(request(), invocation_id="one", journal=journal)
    assert result.failure == "rate_limit" and result.provider_model == "claude-sonnet-5-5"
    assert result.usage.billed_output_tokens == 9
    assert journal.provider_status("claude_subscription")["ai_paused"]


def test_used_percent_exhaustion_fails_admission_and_quota_sanitization_is_bounded():
    config = SubscriptionConfig(provider="claude_subscription", model="claude-sonnet-5-5", enabled=True)
    status = assess_subscription(config, cli_version="2.1.285", authentication="subscription",
        quota={"weekly": {"used_percent": 100}}, extra_usage_disabled=True, isolation_ready=True, native_linux=True)
    assert not status.ready and any("quota exhausted" in item for item in status.blockers)
    clean = sanitize_quota({"source": "private" * 200, "observed_at": {"source": "nested"},
                           "used_percent": -1, "remaining_percent": float("inf"), "credits_balance": "x",
                           "weekly": {"weekly": {"remaining_percent": 1}}, "private_token": "hidden"})
    assert clean == {}


@pytest.mark.parametrize("counters", ['{"inputTokens":1.5,"outputTokens":2}',
                                    '{"inputTokens":"7","outputTokens":2}',
                                    '{"inputTokens":true,"outputTokens":2}'])
def test_malformed_usage_is_unknown_not_coerced(counters):
    outcome = CliOutcome(stdout='{"type":"result","is_error":false,"structured_output":{"note":"x"},'
        '"modelUsage":{"claude-sonnet-5-5":' + counters + '}}', exit_code=0)
    result = parse_claude_outcome(outcome, request())
    assert result.usage is None and not result.ok


@pytest.mark.parametrize("path", ["/private/state.sqlite", "file:///home/adami/private", "C:\\Users\\private",
                                  "runtime/state.sqlite", "../secrets", "Read /home/adami/.env"])
def test_context_private_paths_are_refused_before_dispatch(journal, path):
    executor = Executor()
    supplied = request().model_copy(update={"context": {"market": {"source": path}}})
    result = adapter(executor).invoke(supplied, invocation_id="one", journal=journal)
    assert result.failure == "validation" and executor.calls == 0


def test_quota_wrong_types_are_dropped_without_raising():
    assert sanitize_quota({"source": {"token": "hidden"}, "used_percent": True}) == {}


def test_error_array_quota_message_stops_new_ai_and_extra_turns_are_rejected(journal):
    errored = CliOutcome(stdout='{"type":"result","is_error":true,"subtype":"error_during_execution",'
        '"errors":["You have hit your limit"]}', exit_code=1)
    assert adapter(Executor(errored)).invoke(request(), invocation_id="one", journal=journal).failure == "rate_limit"
    extra_turns = CliOutcome(stdout='{"type":"result","is_error":false,"num_turns":2,'
        '"structured_output":{"note":"x"},"modelUsage":{"claude-sonnet-5-5":{"inputTokens":7,"outputTokens":9}}}',
        exit_code=0)
    result = parse_claude_outcome(extra_turns, request())
    assert result.failure == "validation" and result.usage.billed_output_tokens == 9


def test_unreviewed_model_alias_is_rejected_before_launch(journal):
    executor = Executor()
    config = SubscriptionConfig(provider="claude_subscription", model="sonnet", enabled=True,
                                allowed_context_keys={"research": ["market"]})
    model = SubscriptionAdapter(config, ready(), executor)
    result = model.invoke(request().model_copy(update={"model": "sonnet"}), invocation_id="alias", journal=journal)
    assert result.failure == "validation" and executor.calls == 0


def test_known_exhausted_preflight_persists_quota_pause_before_readiness_rejection(journal):
    config = SubscriptionConfig(provider="claude_subscription", model="claude-sonnet-5-5", enabled=True,
                                allowed_context_keys={"research": ["market"]})
    status = assess_subscription(config, cli_version="2.1.285", authentication="subscription",
        quota={"weekly": {"used_percent": 100}}, extra_usage_disabled=True, isolation_ready=True, native_linux=True)
    executor = Executor()
    result = SubscriptionAdapter(config, status, executor).invoke(request(), invocation_id="exhausted", journal=journal)
    assert result.failure == "rate_limit" and executor.calls == 0
    assert journal.provider_status("claude_subscription")["ai_paused"]
