"""Synthetic CLI outcomes and metadata; no provider, authentication or inference calls."""
import json
from datetime import UTC, datetime
from threading import Event, Thread

import pytest

from trade_graph.adapters.models.subscription import (
    CliOutcome,
    SubscriptionAdapter,
    SubscriptionConfig,
    SubscriptionJournal,
    SubscriptionReadiness,
)
from trade_graph.adapters.models.subscription_process import codex_quota_metadata
from trade_graph.adapters.persistence.db import Database
from trade_graph.contracts.models import ModelRequest, ModelResult
from trade_graph.domain.clock import FrozenClock


@pytest.fixture
def journal(tmp_path):
    database=Database(tmp_path/"state.sqlite")
    yield SubscriptionJournal(database,FrozenClock(datetime(2026,10,8,tzinfo=UTC)))
    database.close()


def request(task="task"):
    return ModelRequest(role="research",task_id=task,root_task_id=task,run_id="run",system_version_id="v",
        provider="openai",model="gpt-6.1-sol",instructions="Summarize public evidence",context={},
        output_schema={"type":"object","properties":{"note":{"type":"string"}},"required":["note"],
                       "additionalProperties":False},schema_name="Research",max_output_tokens=1000,
        max_tool_calls=0,timeout_seconds=600)


def known(clock,remaining=75):
    return SubscriptionReadiness("codex_subscription","0.160.1",True,(),{
        "ordinary_usage_allowed":True,"credits_balance":"0","source":"codex-app-server",
        "observed_at":clock.now().isoformat(),"windows":{
            "primary":{"remaining_percent":remaining,"window_duration_mins":10080},"secondary":None},
        "unavailable_windows":["secondary"]},"chatgpt","linux-bubblewrap")


class Executor:
    def __init__(self,outcomes=None):
        self.calls=[]
        self.outcomes=outcomes or [CliOutcome('\n'.join([
            json.dumps({"type":"item.completed","item":{"type":"agent_message","text":'{"note":"public"}'}}),
            json.dumps({"type":"turn.completed","usage":{"input_tokens":7,"cached_input_tokens":0,
                "output_tokens":9}})]),0)]
    def execute(self,req,**kwargs):
        self.calls.append(req)
        return self.outcomes[min(len(self.calls)-1,len(self.outcomes)-1)]


def model(journal,executor,probe=None):
    config=SubscriptionConfig(model="gpt-6.1-sol",enabled=True,quota_policy_enabled=True,
                              allowed_context_keys={"research":[]})
    return SubscriptionAdapter(config,known(journal.clock),executor,
                               readiness_probe=probe or (lambda:known(journal.clock)))


def test_reserve_refusal_leaves_no_generation_or_attempt_and_can_resume_same_task(journal):
    executor=Executor()
    remaining=[40]
    adapter=model(journal,executor,lambda:known(journal.clock,remaining[0]))
    result=adapter.invoke(request(),invocation_id="later",journal=journal)
    assert result.failure=="quota_reserve" and not executor.calls
    assert journal.row("later") is None
    assert journal.database.execute("SELECT COUNT(*) FROM subscription_attempts").fetchone()[0]==0
    assert journal.database.execute("SELECT COUNT(*) FROM subscription_provider_admissions").fetchone()[0]==0
    remaining[0]=75
    assert adapter.invoke(request(),invocation_id="later",journal=journal).ok
    assert len(executor.calls)==1


def test_every_retry_forces_fresh_metadata_and_retains_single_model(journal):
    failed=CliOutcome('{"type":"turn.failed","error":{"message":"Service unavailable"}}',1)
    executor=Executor([failed,Executor().outcomes[0]])
    observed=[]
    def probe():
        observed.append(len(executor.calls))
        return known(journal.clock)
    adapter=model(journal,executor,probe)
    assert adapter.invoke(request(),invocation_id="retry",journal=journal).ok
    assert observed==[0,0,1] and [req.model for req in executor.calls]==["gpt-6.1-sol","gpt-6.1-sol"]
    assert journal.database.execute("SELECT COUNT(*) FROM subscription_attempts").fetchone()[0]==2


def test_retry_reserve_stop_preserves_actual_failed_attempt_and_usage(journal):
    failed=CliOutcome('\n'.join([json.dumps({"type":"turn.completed","usage":{
        "input_tokens":7,"cached_input_tokens":0,"output_tokens":2}}),
        json.dumps({"type":"turn.failed","error":{"message":"Service unavailable"}})]),1)
    executor=Executor([failed])
    adapter=model(journal,executor,lambda:known(journal.clock,40 if executor.calls else 75))
    result=adapter.invoke(request(),invocation_id="retry-stop",journal=journal)
    assert result.failure=="temporary" and len(executor.calls)==1
    assert result.usage.provider_reported_input_tokens==7
    assert journal.row("retry-stop")["state"]=="FAILED"
    assert journal.database.execute("SELECT COUNT(*) FROM subscription_attempts").fetchone()[0]==1


@pytest.mark.parametrize("case",["stale","exception","missing_probe"])
def test_metadata_failure_refuses_dispatch_without_generation(journal,case):
    executor=Executor()
    def probe():
        if case=="exception":
            raise OSError("synthetic metadata failure")
        status=known(journal.clock)
        status.quota["observed_at"]="2026-10-07T00:00:00+00:00"
        return status
    adapter=model(journal,executor,probe)
    if case=="missing_probe":
        adapter.readiness_probe=None
    assert adapter.invoke(request(),invocation_id=case,journal=journal).failure=="quota_reserve"
    assert not executor.calls and journal.row(case) is None


def test_provider_admission_serializes_independent_controllers(journal):
    second_db=Database(journal.database.path)
    second_journal=SubscriptionJournal(second_db,journal.clock)
    entered,release=Event(),Event()
    class Blocking(Executor):
        def execute(self,req,**kwargs):
            entered.set()
            assert release.wait(3)
            return super().execute(req,**kwargs)
    first_executor,second_executor=Blocking(),Executor()
    first,second=model(journal,first_executor),model(second_journal,second_executor)
    outcomes=[]
    thread=Thread(target=lambda:outcomes.append(first.invoke(request("one"),invocation_id="one",journal=journal)))
    thread.start()
    try:
        assert entered.wait(3)
        result=second.invoke(request("two"),invocation_id="two",journal=second_journal)
        assert result.failure=="quota_reserve" and not second_executor.calls
        assert second_journal.row("two") is None
        # Another writer remains available while the first synthetic CLI is running.
        with second_db.immediate():
            second_db.execute("SELECT COUNT(*) FROM subscription_provider_admissions")
    finally:
        release.set()
        thread.join(3)
        second_db.close()
    assert not thread.is_alive() and outcomes[0].ok


def test_expired_admission_cannot_overlap_an_unresolved_attempt(journal):
    req=request("old")
    journal.begin("old",req,"codex_subscription",{})
    attempt=journal.begin_attempt("old",1,req,"codex_subscription")
    assert journal.acquire_provider_admission("codex_subscription","old",maximum_seconds=600)
    journal.clock.advance(700)
    assert journal.acquire_provider_admission("codex_subscription","new",maximum_seconds=600) is None
    journal.save_attempt(attempt,ModelResult(ok=False,failure="timeout_uncertain"),"UNCERTAIN")
    assert journal.acquire_provider_admission("codex_subscription","new",maximum_seconds=600) is None
    journal.save_attempt(attempt,ModelResult(ok=False,failure="temporary"),"FAILED")
    assert journal.acquire_provider_admission("codex_subscription","new",maximum_seconds=600)


def test_unknown_executor_termination_retains_admission_and_prevents_new_dispatch(journal):
    class Raising(Executor):
        def execute(self,req,**kwargs):
            self.calls.append(req)
            raise OSError("synthetic unexpected executor failure")
    executor=Raising()
    adapter=model(journal,executor)
    assert adapter.invoke(request("one"),invocation_id="one",journal=journal).failure=="timeout_uncertain"
    journal.clock.advance(700)
    assert adapter.invoke(request("two"),invocation_id="two",journal=journal).failure=="quota_reserve"
    assert len(executor.calls)==1 and journal.row("two") is None


def test_known_unavailable_model_pauses_provider_without_switching_model(journal):
    executor=Executor([CliOutcome('{"type":"turn.failed","error":{"code":"model_not_found"}}',1)])
    adapter=model(journal,executor)
    result=adapter.invoke(request(),invocation_id="model-unavailable",journal=journal)
    assert result.failure=="unsupported" and len(executor.calls)==1
    assert journal.provider_status("codex_subscription")["reason"]=="subscription model unavailable"
    assert adapter.public_status(journal=journal)["available_subscription_routes"]==[]


def test_preferred_native_limit_map_retains_every_reported_window_without_bucket_identifiers():
    quota=codex_quota_metadata({"ordinaryUsageAllowed":True,"rateLimits":{
        "credits":{"balance":"0"},"primary":{"usedPercent":1,"windowDurationMins":10080}},
        "rateLimitsByLimitId":{"PRIVATE_BUCKET_A":{"credits":{"balance":"0"},
            "primary":{"usedPercent":20,"windowDurationMins":10080},
            "secondary":{"usedPercent":75,"windowDurationMins":360}},
            "PRIVATE_BUCKET_B":{"primary":{"usedPercent":10,"windowDurationMins":120},"secondary":None}}})
    assert len(quota["windows"])==4 and quota["windows"]["limit_1_secondary"]["remaining_percent"]==25
    assert quota["weekly"]["remaining_percent"]==80 and "PRIVATE" not in json.dumps(quota)
    assert quota["unavailable_windows"]==["limit_2_secondary"]


@pytest.mark.parametrize("duration",[0,-1,None,True])
def test_native_malformed_supported_window_fails_closed(duration):
    quota=codex_quota_metadata({"ordinaryUsageAllowed":True,"rateLimits":{
        "credits":{"balance":"0"},"primary":{"usedPercent":25,"windowDurationMins":10080},
        "secondary":{"usedPercent":25,"windowDurationMins":duration}}})
    assert quota["metadata_error"] is True


def test_unsupported_model_terminal_message_is_recognized_without_model_fallback(journal):
    executor=Executor([CliOutcome(json.dumps({"type":"error", "message":
        "The gpt-6.1-sol model is not supported for this account"}),1)])
    result=model(journal,executor).invoke(request(),invocation_id="unsupported",journal=journal)
    assert result.failure=="unsupported" and len(executor.calls)==1
    assert journal.provider_status("codex_subscription")["ai_paused"]


def test_malformed_preferred_quota_map_cannot_fall_back_to_older_positive_legacy_reading():
    quota=codex_quota_metadata({"ordinaryUsageAllowed":True,"rateLimitsByLimitId":["invalid"],
        "rateLimits":{"credits":{"balance":"0"},"primary":{
            "usedPercent":25,"windowDurationMins":10080}}})
    assert quota=={"metadata_error":True}


def test_expired_replaced_lease_fences_old_metadata_callback_without_dispatch(journal):
    second_db=Database(journal.database.path)
    second_journal=SubscriptionJournal(second_db,journal.clock)
    blocked_probe,release_probe,second_entered,release_second=Event(),Event(),Event(),Event()
    calls=[]
    def first_probe():
        calls.append(True)
        if len(calls)==2:
            blocked_probe.set()
            assert release_probe.wait(3)
        return known(journal.clock)
    class Blocking(Executor):
        def execute(self,req,**kwargs):
            second_entered.set()
            assert release_second.wait(3)
            return super().execute(req,**kwargs)
    first_executor,second_executor=Executor(),Blocking()
    first=model(journal,first_executor,first_probe)
    second=model(second_journal,second_executor)
    first_results,second_results=[],[]
    first_thread=Thread(target=lambda:first_results.append(
        first.invoke(request("old"),invocation_id="old",journal=journal)))
    second_thread=Thread(target=lambda:second_results.append(
        second.invoke(request("new"),invocation_id="new",journal=second_journal)))
    first_thread.start()
    try:
        assert blocked_probe.wait(3)
        journal.clock.advance(700)
        second_thread.start()
        assert second_entered.wait(3)
        release_probe.set()
        first_thread.join(3)
        assert not first_thread.is_alive()
        assert first_results[0].failure=="quota_reserve" and not first_executor.calls
        assert journal.row("old") is None
        assert first.public_status(journal=journal)["quota_policy_routes"][0]["provider_admission"]["occupied"]
    finally:
        release_probe.set()
        release_second.set()
        first_thread.join(3)
        if second_thread.ident is not None:
            second_thread.join(3)
        second_db.close()
    assert second_results[0].ok and len(second_executor.calls)==1


def test_final_authority_callback_cannot_reuse_stale_quota_observation(journal):
    executor=Executor()
    adapter=model(journal,executor)
    authorization_calls=[]
    def authorize():
        authorization_calls.append(True)
        if len(authorization_calls)==2:
            journal.clock.advance(31)
    result=adapter.invoke(request(),invocation_id="stale-final",journal=journal,before_attempt=authorize)
    assert result.failure=="quota_reserve" and not executor.calls and journal.row("stale-final") is None


def test_begin_attempt_fences_current_exact_admission_lease(journal):
    from trade_graph.adapters.models.subscription import ProviderAdmissionLost
    req=request()
    journal.begin("task",req,"codex_subscription",{})
    lease=journal.acquire_provider_admission("codex_subscription","task",maximum_seconds=600)
    with pytest.raises(ProviderAdmissionLost):
        journal.begin_attempt("task",1,req,"codex_subscription",admission_lease_id="wrong")
    assert journal.database.execute("SELECT COUNT(*) FROM subscription_attempts").fetchone()[0]==0
    journal.clock.advance(700)
    with pytest.raises(ProviderAdmissionLost):
        journal.begin_attempt("task",1,req,"codex_subscription",admission_lease_id=lease)


def test_owner_weekly_reserve_requires_a_weekly_reading_even_when_shorter_allowance_is_positive(journal):
    def probe():
        status=known(journal.clock)
        status.quota["windows"]={"primary":{"remaining_percent":99,"window_duration_mins":300}}
        return status
    executor=Executor()
    result=model(journal,executor,probe).invoke(request(),invocation_id="no-weekly",journal=journal)
    assert result.failure=="quota_reserve" and "weekly" in result.message
    assert not executor.calls and journal.row("no-weekly") is None


def test_null_optional_native_bucket_is_disclosed_as_unavailable_instead_of_zero():
    quota=codex_quota_metadata({"ordinaryUsageAllowed":True,"rateLimits":{
        "credits":{"balance":"0"}},"rateLimitsByLimitId":{
        "codex":{"primary":{"usedPercent":1,"windowDurationMins":10080}},"optional":None}})
    assert "metadata_error" not in quota and quota["weekly"]["remaining_percent"]==99
    assert quota["windows"]["limit_2_primary"] is None and "limit_2_primary" in quota["unavailable_windows"]


@pytest.mark.parametrize("change",["readiness","ordinary","credits","provider_pause"])
def test_final_admission_rechecks_full_provider_state_after_authority_callback(journal,change):
    from dataclasses import replace
    executor=Executor()
    adapter=model(journal,executor)
    calls=[]
    def authorize():
        calls.append(True)
        if len(calls)==2:
            if change=="readiness":
                adapter.readiness=replace(adapter.readiness,ready=False)
            elif change=="ordinary":
                adapter.readiness.quota["ordinary_usage_allowed"]=False
            elif change=="credits":
                adapter.readiness.quota["credits_balance"]="1"
            else:
                journal.pause_ai("codex_subscription",adapter.readiness.quota)
    result=adapter.invoke(request(),invocation_id=change,journal=journal,before_attempt=authorize)
    assert result.failure=="quota_reserve" and not executor.calls and journal.row(change) is None


def test_final_authority_callback_cancellation_cannot_dispatch(journal):
    executor=Executor()
    adapter=model(journal,executor)
    cancelled=Event()
    calls=[]
    def authorize():
        calls.append(True)
        if len(calls)==2:
            cancelled.set()
    result=adapter.invoke(request(),invocation_id="cancelled",journal=journal,
        before_attempt=authorize,cancel_event=cancelled)
    assert result.failure=="temporary" and not executor.calls and journal.row("cancelled") is None
    assert not journal.provider_admission_status("codex_subscription")["occupied"]


def test_each_actual_attempt_retains_its_own_sanitized_fresh_quota_snapshot(journal):
    failed=CliOutcome('{"type":"turn.failed","error":{"message":"Service unavailable"}}',1)
    executor=Executor([failed,Executor().outcomes[0]])
    def probe():
        status=known(journal.clock,75 if not executor.calls else 74)
        status.quota["private_account"]="PRIVATE_ACCOUNT_SENTINEL"
        return status
    adapter=model(journal,executor,probe)
    assert adapter.invoke(request(),invocation_id="retained-quota",journal=journal).ok
    rows=journal.database.execute("SELECT quota_json FROM subscription_attempts ORDER BY attempt_index").fetchall()
    quotas=[json.loads(row["quota_json"]) for row in rows]
    assert [quota["windows"]["primary"]["remaining_percent"] for quota in quotas]==[75,74]
    assert "PRIVATE_ACCOUNT_SENTINEL" not in json.dumps(quotas)


def test_durable_provider_pause_reports_its_actual_reason_without_dispatch(journal):
    executor=Executor()
    adapter=model(journal,executor)
    journal.pause_ai("codex_subscription",known(journal.clock).quota,reason="subscription model unavailable")
    result=adapter.invoke(request(),invocation_id="paused-model",journal=journal)
    assert result.failure=="quota_reserve" and result.message=="subscription model unavailable"
    assert not executor.calls and journal.row("paused-model") is None
    policy=adapter.public_status(journal=journal)["quota_policy_routes"][0]
    assert policy["provider_ai_paused"] and policy["provider_pause_reason"]=="subscription model unavailable"
