"""Independent review: recovered unwitnessed terminal history is a forward commitment."""
import pytest
from tests.integration.test_financial_recovery import operator, proposal, recovery_stack
from tests.unit.test_subscription_adapter import request as model_request

from trade_graph.adapters.models.subscription import SubscriptionJournal
from trade_graph.contracts.models import ModelResult
from trade_graph.domain.errors import StaleState


@pytest.mark.parametrize("mutation", ["delete", "identity", "terminal_usage", "terminal_outcome"])
def test_recovered_terminal_history_is_protected_before_first_normal_checkpoint(tmp_path, mutation):
    original, clock, pid, old, recovered, identity, witness = recovery_stack(tmp_path)
    # Match the incident: the witness predates all 13 invocations / 15 attempts.
    journal = SubscriptionJournal(recovered.database, clock)
    base = model_request().model_copy(update={"provider": "codex_subscription", "model": "gpt-6.1-sol"})
    for index in range(13):
        invocation = f"review-invocation-{index}"
        model = base.model_copy(update={"task_id": f"review-task-{index}"})
        journal.begin(invocation, model, "codex_subscription", {})
        for attempt_index in range(2 if index < 2 else 1):
            attempt = journal.begin_attempt(invocation, attempt_index + 1, model, "codex_subscription", quota={})
            result = ModelResult(ok=False, failure="timeout_uncertain", message="synthetic unknown usage")
            journal.save_attempt(attempt, result, "UNCERTAIN")
        journal.save(invocation, result, "UNCERTAIN")
    operator(recovered).apply(proposal(recovered, identity))
    assert recovered.database.execute("SELECT count(*) FROM subscription_attempts").fetchone()[0] == 15
    assert recovered.database.execute("SELECT count(*) FROM subscription_invocations").fetchone()[0] == 13
    if mutation == "delete":
        recovered.database.execute("DELETE FROM subscription_attempts WHERE rowid=1")
    elif mutation == "identity":
        recovered.database.execute("UPDATE subscription_attempts SET request_hash='altered' WHERE rowid=1")
    elif mutation == "terminal_usage":
        recovered.database.execute("UPDATE subscription_attempts SET usage_json='{}' WHERE rowid=1")
    else:
        recovered.database.execute("UPDATE subscription_attempts SET state='COMPLETED', result_json='{}' WHERE rowid=1")
    with pytest.raises(StaleState):
        recovered.retain_budget_history(pid)


def financial_input_stack(tmp_path):
    original, clock, pid, old, recovered, identity, witness = recovery_stack(tmp_path)
    from trade_graph.domain.clock import utc_iso
    at = utc_iso(clock.now())
    recovered.database.execute("INSERT INTO valuation_marks VALUES (?,?,?,?,?,?,?,?,?)",
        ("z-recovered-mark", pid, "BTC", "USD", "99.5", "mid", at, 0, "synthetic-review"))
    recovered.database.execute("INSERT INTO fx_rates VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("z-recovered-fx", "USD", "EUR", "0.9", "synthetic-review", at, at, at, "reference", 0))
    operator(recovered).apply(proposal(recovered, identity))
    return recovered, pid

@pytest.mark.parametrize("table,identity,column", [
    ("valuation_marks", "mark_id", "mark"), ("fx_rates", "rate_id", "rate")])
@pytest.mark.parametrize("mutation", ["delete", "update"])
def test_recovered_financial_inputs_are_retained_before_first_normal_checkpoint(
        tmp_path, table, identity, column, mutation):
    recovered, pid = financial_input_stack(tmp_path)
    key = "z-recovered-mark" if table == "valuation_marks" else "z-recovered-fx"
    if mutation == "delete":
        recovered.database.execute(f"DELETE FROM {table} WHERE {identity}=?", (key,))
    else:
        recovered.database.execute(f"UPDATE {table} SET {column}='1.1' WHERE {identity}=?", (key,))
    with pytest.raises(StaleState):
        recovered.retain_budget_history(pid)


def test_recovery_retained_marks_and_fx_accept_new_lower_sorting_ids(tmp_path):
    recovered, pid = financial_input_stack(tmp_path)
    recovered.database.execute("""INSERT INTO valuation_marks SELECT 'a-appended-mark',portfolio_id,asset,
        quote_currency,mark,convention,observed_at,stale,source FROM valuation_marks
        WHERE mark_id='z-recovered-mark'""")
    recovered.database.execute("""INSERT INTO fx_rates SELECT 'a-appended-fx',base,quote,rate,source,
        observed_at,valid_as_of,retrieved_at,kind,stale FROM fx_rates WHERE rate_id='z-recovered-fx'""")
    recovered.retain_budget_history(pid)
