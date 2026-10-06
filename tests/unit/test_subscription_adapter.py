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


def test_codex_supported_subscription_retry_route_is_admitted():
    status = assess_subscription(SubscriptionConfig(provider="codex_subscription", model="gpt-6.1-sol", enabled=True),
        cli_version="0.160.1", authentication="chatgpt",
        quota={"remaining_percent":80,"ordinary_usage_allowed":True,"credits_balance":"0"},
        extra_usage_disabled=True, isolation_ready=True, native_linux=True)
    assert status.ready
    assert not status.blockers


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
    unsafe = request().model_copy(update={"task_id": "unsafe-task",
                                          "context": {"database": "/private/state.sqlite"}})
    assert adapter(executor).invoke(unsafe, invocation_id="unsafe", journal=journal).failure == "validation"
    assert executor.calls == 0


def test_pre_cancel_does_not_dispatch(journal):
    executor = Executor()
    event = Event()
    event.set()
    result = adapter(executor).invoke(request(), invocation_id="cancelled", journal=journal, cancel_event=event)
    assert not result.ok and executor.calls == 0
    assert journal.row("cancelled")["cost_status"] == "not_incurred"


def test_claude_normal_controls_use_configured_turns_and_retries():
    command = claude_command("/cli/claude", request())
    assert command[command.index("--max-turns") + 1] == "8"
    assert command[command.index("--tools") + 1] == ""
    assert "--fallback-model" not in command
    settings = json.loads(command[command.index("--settings") + 1])
    assert settings["availableModels"] == [request().model]
    assert settings["fallbackModel"] == []
    environment = claude_environment(1000)
    assert environment["CLAUDE_CODE_MAX_RETRIES"] == "2"
    assert environment["MAX_STRUCTURED_OUTPUT_RETRIES"] == "3"
    assert environment["CLAUDE_CODE_NONSTREAMING_TIMEOUT_RETRIES"] == "2"
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


def test_error_array_quota_message_stops_new_ai_and_normal_multiple_turns_are_valid(journal):
    errored = CliOutcome(stdout='{"type":"result","is_error":true,"subtype":"error_during_execution",'
        '"errors":["You have hit your limit"]}', exit_code=1)
    assert adapter(Executor(errored)).invoke(request(), invocation_id="one", journal=journal).failure == "rate_limit"
    extra_turns = CliOutcome(stdout='{"type":"result","is_error":false,"num_turns":2,'
        '"structured_output":{"note":"x"},"modelUsage":{"claude-sonnet-5-5":{"inputTokens":7,"outputTokens":9}}}',
        exit_code=0)
    result = parse_claude_outcome(extra_turns, request())
    assert result.ok and result.usage.billed_output_tokens == 9


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


def test_another_invocation_identity_cannot_replay_the_same_subscription_task(journal):
    executor = Executor()
    model = adapter(executor)
    model.invoke(request(), invocation_id="first", journal=journal)
    with pytest.raises(StaleState, match="task already"):
        model.invoke(request(), invocation_id="second", journal=journal)
    assert executor.calls == 1


def test_normal_subscription_limits_are_configurable():
    config = SubscriptionConfig(model="gpt-6.1-sol", maximum_seconds=1200, maximum_output_tokens=32768,
        maximum_turns=16, maximum_output_bytes=4194304, application_max_attempts=4, cli_transport_retries=3)
    assert config.maximum_seconds == 1200 and config.maximum_turns == 16


def test_codex_structured_jsonl_validates_payload_and_retains_real_usage():
    req = request().model_copy(update={"provider":"openai", "model":"gpt-6.1-sol"})
    outcome = CliOutcome(stdout='\n'.join([
        json.dumps({"type":"thread.started","thread_id":"private-thread"}),
        json.dumps({"type":"item.completed","item":{"type":"agent_message","text":'{"note":"public"}'}}),
        json.dumps({"type":"turn.completed","usage":{"input_tokens":13,"cached_input_tokens":3,"output_tokens":9}}),
    ]), exit_code=0)
    from trade_graph.adapters.models import subscription
    result = subscription.parse_codex_outcome(outcome, req)
    assert result.ok and result.payload == {"note":"public"}
    assert result.usage.uncached_input_tokens == 10 and result.usage.cache_read_tokens == 3
    assert result.provider_model is None  # CLI JSONL does not attest an actual model.
    command = subscription.codex_command("/cli/runner", req)
    assert "--json" in command and "--output-schema" in command and "--ignore-user-config" in command
    assert "forced_login_method=\"chatgpt\"" in command


def test_codex_uncertain_completion_and_tool_execution_are_not_success():
    req = request().model_copy(update={"provider":"openai", "model":"gpt-6.1-sol"})
    partial = CliOutcome('{"type":"turn.started"}', 0)
    from trade_graph.adapters.models import subscription
    assert subscription.parse_codex_outcome(partial, req).failure == "timeout_uncertain"
    tool = CliOutcome('\n'.join([json.dumps({"type":"item.completed","item":{
        "type":"command_execution", "command":"cat private"}}), json.dumps({"type":"turn.completed"})]),0)
    assert subscription.parse_codex_outcome(tool, req).failure == "validation"


def test_known_terminal_transient_failure_retries_with_separate_receipts(journal):
    class Transient:
        calls=0
        def execute(self, req, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return CliOutcome('{"type":"result","is_error":true,"result":"Service unavailable",'
                    '"modelUsage":{"claude-sonnet-5-5":{"inputTokens":3,"outputTokens":1}}}',1)
            return Executor().outcome
    executor=Transient()
    result=adapter(executor).invoke(request(),invocation_id="retry",journal=journal)
    assert result.ok and executor.calls == 2
    rows=journal.database.execute("SELECT * FROM subscription_attempts ORDER BY attempt_index").fetchall()
    assert [row["state"] for row in rows] == ["FAILED","COMPLETED"]
    assert journal.row("retry")["state"] == "COMPLETED"
    assert json.loads(journal.row("retry")["usage_json"])["uncached_input_tokens"] == 10
    assert adapter(executor).invoke(request(),invocation_id="retry",journal=journal).ok
    assert executor.calls == 2


def test_verified_subscription_fallback_records_both_models_and_all_known_usage(journal):
    primary=adapter(Executor(CliOutcome('{"type":"result","is_error":true,"result":"service unavailable",'
        '"modelUsage":{"claude-sonnet-5-5":{"inputTokens":3,"outputTokens":1}}}',1)))
    class Codex:
        calls=0
        def execute(self, req, **kwargs):
            self.calls += 1
            assert req.model == "gpt-6.1-sol" and req.provider == "openai"
            return CliOutcome('\n'.join([json.dumps({"type":"item.completed","item":{
                "type":"agent_message","text":'{"note":"fallback public"}'}}),json.dumps({
                "type":"turn.completed","model":"gpt-6.1-sol","usage":{"input_tokens":7,"output_tokens":9}})]),0)
    status=SubscriptionReadiness("codex_subscription","0.160.1",True,(),{},"chatgpt","linux-bubblewrap")
    config=SubscriptionConfig(provider="codex_subscription",model="gpt-6.1-sol",enabled=True,
        allowed_context_keys={"research":["market"]})
    fallback=SubscriptionAdapter(config,status,Codex())
    model=SubscriptionAdapter(primary.config,primary.readiness,primary.executor,fallbacks=(fallback,))
    result=model.invoke(request(),invocation_id="fallback",journal=journal)
    assert result.ok and result.provider_model == "gpt-6.1-sol"
    assert result.usage.uncached_input_tokens == 10
    rows=journal.database.execute(
        "SELECT provider,requested_model FROM subscription_attempts ORDER BY attempt_index").fetchall()
    assert [(row[0],row[1]) for row in rows] == [
        ("claude_subscription","claude-sonnet-5-5"),("codex_subscription","gpt-6.1-sol")]


def test_each_retry_reauthorizes_and_missing_usage_stays_unknown(journal):
    calls=[]
    def authorize():
        calls.append(True)
        if len(calls)>1:
            raise ValueError("owner pause")
    executor=Executor(CliOutcome('{"type":"result","is_error":true,"result":"service unavailable"}',1))
    result=adapter(executor).invoke(request(),invocation_id="guard",journal=journal,before_attempt=authorize)
    assert result.failure == "validation" and executor.calls == 1 and len(calls)==2
    assert journal.row("guard")["usage_json"] is None
    assert journal.database.execute("SELECT COUNT(*) FROM subscription_attempts").fetchone()[0] == 1


def test_codex_department_web_search_uses_supported_tool_with_bounded_observed_usage(journal):
    from trade_graph.adapters.models import subscription
    config=SubscriptionConfig(provider="codex_subscription",model="gpt-6.1-sol",enabled=True,
        allowed_context_keys={"research":["market"]},department_tools={"research":["web_search"]},
        maximum_tool_calls=16)
    req=request().model_copy(update={"provider":"openai","model":"gpt-6.1-sol","max_tool_calls":16})
    outcome=CliOutcome('\n'.join([json.dumps({"type":"item.completed","item":{
        "id":"search1","type":"web_search","query":"public crypto"}}),json.dumps({
        "type":"item.completed","item":{"type":"agent_message","text":'{"note":"public"}'}}),
        json.dumps({"type":"turn.completed","usage":{"input_tokens":7,"output_tokens":9}})]),0)
    status=SubscriptionReadiness("codex_subscription","0.160.1",True,(),{},"chatgpt","linux-bubblewrap")
    result=SubscriptionAdapter(config,status,Executor(outcome)).invoke(req,invocation_id="search",journal=journal)
    assert result.ok and result.usage.tool_units==1
    command=subscription.codex_command("/cli/runner",req)
    assert 'web_search="live"' in command
    assert 'features.shell_tool=false' in command and 'features.view_image=false' in command
    assert 'features.image_generation=false' in command


def test_engineer_distinct_commissioned_generation_ids_can_dispatch(journal):
    config=SubscriptionConfig(provider="claude_subscription",model="claude-sonnet-5-5",enabled=True,
        allowed_context_keys={"engineer":["market"]})
    model=SubscriptionAdapter(config,ready(),Executor())
    req=request().model_copy(update={"role":"engineer"})
    assert model.invoke(req,invocation_id="generation1",journal=journal).ok
    assert model.invoke(req,invocation_id="generation2",journal=journal).ok


def test_fresh_verified_positive_subscription_quota_recovers_quota_pause(journal):
    journal.pause_ai("claude_subscription",{"remaining_percent":0})
    known=SubscriptionReadiness("claude_subscription","2.1.292",True,(),{
        "ordinary_usage_allowed":True,"remaining_percent":75,"observed_at":"2026-10-06T00:01:00+00:00"},
        "subscription","linux-bubblewrap")
    executor=Executor()
    result=adapter(executor,known).invoke(request(),invocation_id="resumed",journal=journal)
    assert result.ok and executor.calls==1
    assert not journal.provider_status("claude_subscription")["ai_paused"]


def test_codex_ordinary_subscription_with_no_spendable_credits_needs_no_claude_checkbox():
    config=SubscriptionConfig(provider="codex_subscription",model="gpt-6.1-sol",enabled=True)
    known=assess_subscription(config,cli_version="0.160.1",authentication="chatgpt",quota={
        "ordinary_usage_allowed":True,"credits_balance":"0","remaining_percent":75},
        extra_usage_disabled=False,isolation_ready=True,native_linux=True)
    assert known.ready


def test_ready_fallback_can_run_when_primary_provider_is_durably_quota_paused(journal):
    journal.pause_ai("claude_subscription",{"remaining_percent":0})
    executor=Executor()
    fallback=adapter(executor)
    fallback.config=fallback.config.model_copy(update={"provider":"codex_subscription","model":"gpt-6.1-sol"})
    fallback.readiness=SubscriptionReadiness("codex_subscription","0.160.1",True,(),{},"chatgpt","linux-bubblewrap")
    fallback.executor=Executor(CliOutcome('\n'.join([json.dumps({"type":"item.completed","item":{
        "type":"agent_message","text":'{"note":"fallback"}'}}),json.dumps({
        "type":"turn.completed","usage":{"input_tokens":7,"output_tokens":9}})]),0))
    primary=adapter(Executor())
    primary=SubscriptionAdapter(primary.config,primary.readiness,primary.executor,fallbacks=(fallback,))
    result=primary.invoke(request(),invocation_id="paused-fallback",journal=journal)
    assert result.ok and primary.executor.calls==0 and fallback.executor.calls==1


def test_codex_partial_multi_turn_usage_is_unknown_instead_of_known_subset():
    from trade_graph.adapters.models.subscription import parse_codex_outcome
    req=request().model_copy(update={"provider":"openai","model":"gpt-6.1-sol"})
    outcome=CliOutcome('\n'.join([json.dumps({"type":"turn.completed","usage":{"input_tokens":7,"output_tokens":9}}),
        json.dumps({"type":"item.completed","item":{"type":"agent_message","text":'{"note":"public"}'}}),
        json.dumps({"type":"turn.completed"})]),0)
    result=parse_codex_outcome(outcome,req)
    assert result.ok and result.usage is None


def test_readiness_metadata_refresh_is_cached_and_never_invokes_inference(journal):
    statuses=[]
    def probe():
        statuses.append(True)
        return ready()
    executor=Executor()
    model=SubscriptionAdapter(adapter(executor).config,ready(),executor,readiness_probe=probe)
    assert model.public_status(journal=journal)["ready"]
    assert model.public_status(journal=journal)["ready"]
    assert len(statuses)==1 and executor.calls==0
    model.refresh_readiness(force=True)
    assert len(statuses)==2 and executor.calls==0


def test_durable_terminal_attempt_recovers_parent_result_without_any_model_replay(journal):
    req=request()
    journal.begin("recovered",req,"claude_subscription",{})
    first=journal.begin_attempt("recovered",1,req,"claude_subscription")
    from trade_graph.contracts.models import ModelResult, ModelUsage
    journal.save_attempt(first,ModelResult(ok=False,failure="temporary",
        usage=ModelUsage(uncached_input_tokens=3,billed_output_tokens=1)),"FAILED")
    final=journal.begin_attempt("recovered",2,req,"claude_subscription")
    journal.save_attempt(final,parse_claude_outcome(Executor().outcome,req),"COMPLETED")
    executor=Executor()
    result=adapter(executor).invoke(req,invocation_id="recovered",journal=journal)
    assert result.ok and result.usage.uncached_input_tokens==10 and executor.calls==0
    assert journal.row("recovered")["state"]=="COMPLETED"


def test_dispatched_attempt_without_terminal_result_recovers_as_uncertain(journal):
    req=request()
    journal.begin("ambiguous",req,"claude_subscription",{})
    attempt=journal.begin_attempt("ambiguous",1,req,"claude_subscription")
    result=journal.recover_result("ambiguous")
    assert result.failure=="timeout_uncertain" and result.usage is None
    row=journal.database.execute("SELECT * FROM subscription_attempts WHERE attempt_id=?",(attempt,)).fetchone()
    assert row["state"]=="UNCERTAIN" and row["cost_status"]=="unknown"


@pytest.mark.parametrize("quota",[{}, {"remaining_percent":80},
    {"ordinary_usage_allowed":True,"credits_balance":"1","remaining_percent":80},
    {"ordinary_usage_allowed":False,"credits_balance":"0","remaining_percent":80}])
def test_codex_cannot_use_claude_extra_usage_boolean_to_bypass_actual_subscription_allowance(quota):
    config=SubscriptionConfig(provider="codex_subscription",model="gpt-6.1-sol",enabled=True)
    status=assess_subscription(config,cli_version="0.160.1",authentication="chatgpt",quota=quota,
        extra_usage_disabled=True,isolation_ready=True,native_linux=True)
    assert not status.ready
    assert "subscription-only allowance is not verified; paid extras remain unauthorized" in status.blockers
