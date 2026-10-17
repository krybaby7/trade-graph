"""T45: the owner's switch between shadow and live news wake-ups, recorded as an owner-policy revision.

``trade-graph news-wakes`` previews by default and writes only with ``--confirm``; it is refused while the paper
service runs, revision-checked, owner-only, and never rewrites an earlier revision. Going live is decided from shadow
evidence (T44's verdicts, the shadow routes and HQ's wake counts), not from fixtures: with only synthetic records it
is refused. Synthetic databases only; no network, model, key or paid call. The "real" evidence in two tests is a
poll whose invented headlines and scripted Jev answers are labelled as real, standing in for a recorded L3 poll.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from tests.news_wake_support import NewsDesk, hold_sol, news_triggers, routes
from tests.wake_support import engine_account

from trade_graph.application.authority import AuthorityRecord
from trade_graph.application.news_wake_switch import (
    COMMAND,
    propose_news_wakes,
    record_news_wakes,
    shadow_evidence,
)
from trade_graph.application.news_wakes import news_wake_setting
from trade_graph.cli import main
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState


def _engine(tmp_path):
    return engine_account(tmp_path, daily_cycle=False)


def _record(account) -> AuthorityRecord:
    return AuthorityRecord(account.database, account.clock)


def _shadow_evidence(account, engine, *, real: bool) -> None:
    desk = NewsDesk(account, labelled_real=real)
    desk.delisting()
    desk.poll()
    engine.step([account.portfolio])


def _audit(account) -> list[dict]:
    return [dict(row) for row in account.database.execute("SELECT * FROM owner_policy_audit ORDER BY sequence")]


def _run(capsys, *arguments) -> dict:
    assert main([COMMAND, *arguments]) == 0
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _path(account) -> str:
    return str(account.database.path)


def test_the_preview_shows_the_shadow_evidence_and_writes_nothing(tmp_path) -> None:
    account, _, engine = _engine(tmp_path)
    hold_sol(account)
    _shadow_evidence(account, engine, real=False)
    before = account.database.execute("SELECT COUNT(*) FROM owner_policy_revisions").fetchone()[0]
    proposal = propose_news_wakes(_record(account), mode="live", revision_id="owner-news-1")
    assert (proposal.mode_from, proposal.mode_to) == ("shadow", "live")
    evidence = proposal.evidence.document()
    assert evidence["headlines"] == 1 and evidence["checked"] == 1 and evidence["would_wake"] == 1
    assert evidence["routes"] == {"shadow": {"leader": 1, "trader": 1}, "live": {"leader": 0, "trader": 0}}
    assert evidence["real"] == {"headlines": 0, "would_wake": 0, "shadow_routes": 0}
    assert evidence["news_wakes"] == {"leader": 0, "trader": 0}
    assert "synthetic" in proposal.refusal
    assert account.database.execute("SELECT COUNT(*) FROM owner_policy_revisions").fetchone()[0] == before


def test_going_live_on_fixtures_alone_is_refused_and_nothing_is_written(tmp_path) -> None:
    account, _, engine = _engine(tmp_path)
    _shadow_evidence(account, engine, real=False)
    with pytest.raises(AuthorityDenied, match="shadow evidence"):
        record_news_wakes(_record(account), mode="live", revision_id="owner-news-1")
    assert news_wake_setting(account.database).mode == "shadow" and _audit(account) == []


def test_going_live_with_real_shadow_evidence_records_an_owner_policy_revision_and_its_audit(tmp_path) -> None:
    account, _, engine = _engine(tmp_path)
    _shadow_evidence(account, engine, real=True)
    current = account.execution.authority.active_policy().revision_id
    proposal = record_news_wakes(_record(account), mode="live", revision_id="owner-news-1", expect_revision=current)
    policy = account.execution.authority.active_policy()
    assert policy.revision_id == "owner-news-1" and policy.news_wakes == "live"
    assert proposal.current.model_dump(exclude={"revision_id", "news_wakes"}) == policy.model_dump(
        exclude={"revision_id", "news_wakes"})  # nothing else in the owner's policy changes
    (audit,) = _audit(account)
    assert (audit["revision_id"], audit["previous_revision_id"], audit["command"], audit["set_by"]) == (
        "owner-news-1", current, COMMAND, "owner")
    changes = json.loads(audit["changes_json"])
    assert changes["changes"] == {"news_wakes": {"from": None, "to": "live"}}
    assert changes["evidence"]["real"]["shadow_routes"] == 1
    # From now on new alarms wake people; the one already routed in shadow stays a shadow record.
    account.clock.advance(60)
    desk = NewsDesk(account, labelled_real=True)
    desk.delisting("BTC")
    desk.poll()
    engine.step([account.portfolio])
    assert [route["mode"] for route in routes(account)] == ["shadow", "live"]
    assert len(news_triggers(account)) == 1


def test_back_to_shadow_needs_no_evidence_and_a_repeat_changes_nothing(tmp_path) -> None:
    account, _, engine = _engine(tmp_path)
    _shadow_evidence(account, engine, real=True)
    record_news_wakes(_record(account), mode="live", revision_id="owner-news-1")
    account.clock.advance(60)
    again = record_news_wakes(_record(account), mode="live", revision_id="owner-news-2")
    assert again.policy is None and len(_audit(account)) == 1
    record_news_wakes(_record(account), mode="shadow", revision_id="owner-news-3")
    assert news_wake_setting(account.database).mode == "shadow"
    assert [json.loads(row["changes_json"])["changes"] for row in _audit(account)] == [
        {"news_wakes": {"from": None, "to": "live"}}, {"news_wakes": {"from": "live", "to": "shadow"}}]


def test_it_is_refused_while_the_service_runs_after_a_changed_preview_and_for_anyone_but_the_owner(tmp_path) -> None:
    account, _, engine = _engine(tmp_path)
    _shadow_evidence(account, engine, real=True)
    with pytest.raises(AuthorityDenied, match="owner"):
        record_news_wakes(_record(account), mode="live", revision_id="x-1", role="leader")
    with pytest.raises(StaleState, match="changed since the preview"):
        record_news_wakes(_record(account), mode="live", revision_id="x-2", expect_revision="an-older-revision")
    account.database.execute(
        "INSERT INTO process_leases (lease_name, owner, expires_at) VALUES ('paper-service', 'synthetic', ?)",
        (utc_iso(account.clock.now() + timedelta(minutes=5)),))
    with pytest.raises(StaleState, match="running"):
        record_news_wakes(_record(account), mode="live", revision_id="x-3")
    assert news_wake_setting(account.database).mode == "shadow" and _audit(account) == []


def test_the_evidence_window_counts_only_what_happened_inside_it_and_the_gate_reads_all_of_it(tmp_path) -> None:
    account, _, engine = _engine(tmp_path)
    _shadow_evidence(account, engine, real=True)
    later = account.clock.now() + timedelta(hours=1)
    assert shadow_evidence(account.database, account.portfolio, since=later).document()["would_wake"] == 0
    assert shadow_evidence(account.database, account.portfolio).document()["would_wake"] == 1
    proposal = record_news_wakes(_record(account), mode="live", revision_id="owner-news-1", since=later)
    assert proposal.evidence.real["shadow_routes"] == 0 and proposal.policy.news_wakes == "live"


def test_the_command_previews_by_default_and_confirms_only_with_real_evidence(tmp_path, capsys) -> None:
    account, _, engine = _engine(tmp_path)
    path = _path(account)
    shown = _run(capsys, "--database", path)
    assert shown["applied"] is False and shown["mode"] == {"from": "shadow", "to": "shadow"}
    preview = _run(capsys, "--database", path, "--live", "on")
    assert preview["applied"] is False and preview["mode"] == {"from": "shadow", "to": "live"}
    assert preview["refused"] and preview["paid_calls_enabled"] is False and preview["live_enabled"] is False
    with pytest.raises(SystemExit):
        main([COMMAND, "--database", path, "--live", "on", "--confirm"])
    assert "shadow evidence" in capsys.readouterr().err
    _shadow_evidence(account, engine, real=True)
    preview = _run(capsys, "--database", path, "--live", "on")
    assert preview["refused"] is None and preview["evidence"]["real"]["shadow_routes"] == 1
    done = _run(capsys, "--database", path, "--live", "on", "--confirm", "--expect-revision",
                preview["current_revision"])
    assert done["applied"] is True and done["new_revision"]
    assert news_wake_setting(account.database).mode == "live"
    with pytest.raises(SystemExit):
        main([COMMAND, "--database", path, "--confirm"])  # confirming needs a choice
    capsys.readouterr()
