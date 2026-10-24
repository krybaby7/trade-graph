"""The Leader's news rounds (W6-B, T38) composed with the owner's Perplexity switch (W6-A, T37; D205, D213).

W6-B's handler takes the switch and the spend rule as injected functions; W6-A's ``perplexity_switch`` module is
the one typed read of the owner's protected setting. This is the composition the handoffs name for wiring the
Leader:

    LeaderNewsRounds(..., enabled=lambda: perplexity_enabled_for(database, "leader"),
                     spend_policy=lambda: perplexity_setting(database).spend_policy, ...)

With nothing recorded the Leader stays off Perplexity (fail closed: no call, no reservation); once the owner's switch
is recorded, the next round runs with the owner's recorded spend rule. The installed service does not build this
handler yet (see docs/L3-INSTALL-CHECKLIST.md step 12). Everything is synthetic: the day-plan account, a scripted
Perplexity (documented shapes, made-up key) and a scripted Trader; no network, real key, paid call or real order.
"""

from __future__ import annotations

import json

from tests.leader_news_support import LeaderSetup, full_round
from tests.wake_support import run_one

from trade_graph.application.leader_news import LeaderNewsRounds
from trade_graph.application.perplexity_switch import (
    perplexity_enabled_for,
    perplexity_setting,
    record_perplexity_departments,
)


def _owner_switched(setup: LeaderSetup) -> LeaderNewsRounds:
    """The Leader's handler reading W6-A's protected switch and spend rule, with no spend rule of its own."""
    database = setup.database
    return LeaderNewsRounds(setup.research, enabled=lambda: perplexity_enabled_for(database, "leader"),
                            spend_policy=lambda: perplexity_setting(database).spend_policy, engine=setup.engine)


def _round(setup: LeaderSetup, rounds: LeaderNewsRounds) -> str | None:
    setup.engine.step([setup.portfolio])
    return run_one(setup.scheduler, setup.engine, {"leader": rounds})


def test_with_nothing_recorded_the_leader_stays_off_perplexity_and_nothing_is_reserved_or_sent(tmp_path) -> None:
    setup = LeaderSetup(tmp_path, full_round(), policy=None)
    rounds = _owner_switched(setup)
    assert perplexity_setting(setup.database).revision_id is None  # shipped off
    assert _round(setup, rounds) is not None
    [record] = rounds.store.rounds(setup.portfolio)
    assert record.status == "switched_off" and record.run_id is None and record.receipt_ids == ()
    assert setup.transport.calls == [] and setup.rows("SELECT * FROM budget_reservations") == []
    assert setup.rows("SELECT * FROM web_research_runs") == []


def test_research_switched_on_alone_keeps_the_leader_off(tmp_path) -> None:
    setup = LeaderSetup(tmp_path, full_round(), policy=None)
    record_perplexity_departments(setup.database, setup.clock, research=True, leader=False, monthly_limit_usd=None,
                                  revision_id="synthetic-owner-switch-research-only")
    rounds = _owner_switched(setup)
    _round(setup, rounds)
    [record] = rounds.store.rounds(setup.portfolio)
    assert record.status == "switched_off" and setup.transport.calls == []


def test_once_the_owner_records_the_switch_the_round_runs_with_the_owners_spend_rule(tmp_path) -> None:
    setup = LeaderSetup(tmp_path, full_round(), policy=None)
    rounds = _owner_switched(setup)
    record_perplexity_departments(setup.database, setup.clock, research=True, leader=True, monthly_limit_usd=None,
                                  revision_id="synthetic-owner-switch")
    task_id = _round(setup, rounds)
    [record] = rounds.store.rounds(setup.portfolio)
    assert record.status == "completed" and record.task_id == task_id
    assert len(setup.transport.calls) == 5 and len(record.receipt_ids) == 5
    assert record.billing == "resolved" and record.unresolved_reservations == ()
    [run] = setup.rows("SELECT policy_json FROM web_research_runs WHERE run_id = ?", record.run_id)
    policy = json.loads(run["policy_json"])
    assert (policy["revision_id"], policy["mode"], policy["decision_ref"]) == (
        "synthetic-owner-switch/spend", "unlimited_testing", "D125")
