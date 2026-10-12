"""Test-only strategy IDs leave the default mandate; old databases get a new revision, never rewritten history."""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tests.integration.test_pause_profiles import _fill_entry, _quote, _stack

from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.authority import AuthorityRecord, paper_mandate, paper_owner_policy
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.cli import main
from trade_graph.contracts.models import Decision, Mandate
from trade_graph.domain.clock import FrozenClock, utc_iso
from trade_graph.domain.errors import AuthorityDenied
from trade_graph.paper_runtime import installed_artifacts
from trade_graph.roles.strategies import TEMPLATES

# Exactly what `trade-graph init` wrote before the default was cleaned.
LEGACY = ["slow-trend", "slow-trend-pullback", "s"]


def _no_action(clock, portfolio, *, record_id: str, mandate_revision: str) -> Decision:
    return Decision(
        record_id=record_id,
        created_at_utc=clock.now(),
        run_id="run",
        task_id="task",
        root_task_id="root",
        portfolio_id=portfolio,
        mode="paper",
        system_version_id="v1",
        trace_id="trace",
        action="no_action",
        rationale="no setup on the hourly candles",
        invalidation="a pullback setup appears",
        horizon_seconds=3600,
        strategy_id="slow-trend-pullback",
        snapshot_id="snap-old",
        mandate_revision=mandate_revision,
        policy_revision="initial-paper-policy",
        no_action_reason="no setup",
    )


def _legacy_db(tmp_path, strategy_ids=LEGACY):
    clock = FrozenClock(datetime(2026, 10, 7, 8, 13, tzinfo=UTC))
    database = Database(tmp_path / "paper.sqlite")
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="EUR", mode="paper")
    ledger.deposit(portfolio, "USD", Decimal("10000"), "opening")
    record = AuthorityRecord(database, clock)
    record.install_policy(paper_owner_policy(revision_id="initial-paper-policy"), role="owner")
    record.install_mandate(paper_mandate(portfolio, strategy_ids=list(strategy_ids)), role="owner")
    Execution(database, ledger, clock, PaperBroker(database, clock)).record_non_order(
        portfolio, _no_action(clock, portfolio, record_id="old-no-action", mandate_revision="1"),
    )
    return clock, database, record, portfolio


def _mandate_rows(database, portfolio) -> list[dict]:
    return [dict(row) for row in database.execute(
        "SELECT * FROM mandates WHERE portfolio_id = ? ORDER BY revision", (portfolio,),
    ).fetchall()]


def test_default_paper_mandate_names_only_known_templates() -> None:
    mandate = paper_mandate("p")
    assert "slow-trend" not in mandate.strategy_ids
    assert "s" not in mandate.strategy_ids
    installed = {json.loads(text)["strategy_id"] for name, text in installed_artifacts().items()
                 if name.startswith("strategies/templates/")}
    assert mandate.strategy_ids
    assert set(mandate.strategy_ids) <= set(TEMPLATES) | installed
    assert paper_mandate("p", strategy_ids=["slow-trend"]).strategy_ids == ["slow-trend"]


def test_init_installs_mandate_without_test_leftovers(tmp_path, capsys) -> None:
    path = tmp_path / "p.sqlite"
    assert main(["init", "--database", str(path)]) == 0
    portfolio = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["portfolio_id"]
    database = Database(path)
    try:
        mandate = AuthorityRecord(database, FrozenClock(datetime(2026, 10, 12, tzinfo=UTC))).active_mandate(portfolio)
    finally:
        database.close()
    assert mandate.strategy_ids == ["slow-trend-pullback"]
    assert mandate.revision == 1


def test_legacy_mandate_gets_new_revision_without_rewriting_history(tmp_path) -> None:
    clock, database, record, portfolio = _legacy_db(tmp_path)
    before = _mandate_rows(database, portfolio)[0]
    decisions = [dict(row) for row in database.execute("SELECT * FROM decisions").fetchall()]

    clock.advance(3 * 86400)
    revised = record.drop_legacy_strategy_ids(portfolio, role="owner")

    assert revised is not None
    assert revised.revision == 2
    assert revised.mandate_id == f"paper-{portfolio}-2"
    assert record.active_mandate(portfolio) == revised
    assert revised.strategy_ids == ["slow-trend-pullback"]
    changed = {"mandate_id", "revision", "strategy_ids"}
    assert revised.model_dump(exclude=changed) == Mandate.model_validate_json(
        before["document_json"]).model_dump(exclude=changed)

    rows = _mandate_rows(database, portfolio)
    assert len(rows) == 2
    old, new = rows
    assert {key: value for key, value in old.items() if key != "active"} == {
        key: value for key, value in before.items() if key != "active"}
    assert (before["active"], old["active"]) == (1, 0)
    assert json.loads(old["document_json"])["strategy_ids"] == LEGACY
    assert new["active"] == 1
    assert new["created_at"] == utc_iso(clock.now())

    assert [dict(row) for row in database.execute("SELECT * FROM decisions").fetchall()] == decisions
    assert decisions[0]["mandate_revision"] == "1"
    cited = database.execute(
        "SELECT document_json FROM mandates WHERE portfolio_id = ? AND CAST(revision AS TEXT) = ?",
        (portfolio, decisions[0]["mandate_revision"]),
    ).fetchone()
    assert "slow-trend" in json.loads(cited["document_json"])["strategy_ids"]

    record.require_for_decision(
        _no_action(clock, portfolio, record_id="new", mandate_revision="2"), venue="paper", mode="paper",
    )
    with pytest.raises(AuthorityDenied, match="decision mandate revision is not active"):
        record.require_for_decision(
            _no_action(clock, portfolio, record_id="stale", mandate_revision="1"), venue="paper", mode="paper",
        )
    database.close()


def test_cleanup_is_owner_only_and_idempotent(tmp_path) -> None:
    clock, database, record, portfolio = _legacy_db(tmp_path)
    for role in ("leader", "trader"):
        with pytest.raises(AuthorityDenied):
            record.drop_legacy_strategy_ids(portfolio, role=role)
    assert len(_mandate_rows(database, portfolio)) == 1
    assert record.drop_legacy_strategy_ids(portfolio, role="owner") is not None
    assert record.drop_legacy_strategy_ids(portfolio, role="owner") is None
    assert record.legacy_strategy_revision(portfolio) is None
    assert len(_mandate_rows(database, portfolio)) == 2
    database.close()

    _, clean, clean_record, clean_portfolio = _legacy_db(
        tmp_path / "clean", strategy_ids=["slow-trend-pullback"])
    assert clean_record.legacy_strategy_revision(clean_portfolio) is None
    clean.close()

    _, only_leftover, leftover_record, leftover_portfolio = _legacy_db(tmp_path / "only", strategy_ids=["s"])
    proposal = leftover_record.legacy_strategy_revision(leftover_portfolio)
    # An empty list would switch off the named-strategy check, so the default replaces it.
    assert proposal is not None and proposal.strategy_ids == ["slow-trend-pullback"]
    assert len(_mandate_rows(only_leftover, leftover_portfolio)) == 1
    only_leftover.close()


def test_cleanup_cli_previews_then_writes_only_on_confirm(tmp_path, capsys) -> None:
    _, database, _, portfolio = _legacy_db(tmp_path)
    database.close()
    path = str(tmp_path / "paper.sqlite")

    def active_revisions() -> tuple[int, int]:
        reopened = Database(path)
        try:
            rows = _mandate_rows(reopened, portfolio)
        finally:
            reopened.close()
        return len(rows), next(row["revision"] for row in rows if row["active"])

    assert main(["mandate-cleanup", "--database", path]) == 0
    preview = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert preview["portfolio_id"] == portfolio
    assert preview["applied"] is False
    assert preview["current_revision"] == 1
    assert preview["new_revision"] == 2
    assert preview["removed_strategy_ids"] == ["slow-trend", "s"]
    assert preview["strategy_ids"] == ["slow-trend-pullback"]
    assert preview["paid_calls_enabled"] is False and preview["live_enabled"] is False
    assert "history" in preview
    assert active_revisions() == (1, 1)

    assert main(["mandate-cleanup", "--database", path, "--portfolio-id", portfolio, "--confirm"]) == 0
    applied = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert applied["applied"] is True
    assert applied["current_revision"] == 1 and applied["new_revision"] == 2
    assert applied["strategy_ids"] == ["slow-trend-pullback"]
    assert active_revisions() == (2, 2)

    assert main(["mandate-cleanup", "--database", path, "--confirm"]) == 0
    repeat = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert repeat["applied"] is False
    assert repeat["current_revision"] == 2
    assert repeat["new_revision"] is None
    assert repeat["removed_strategy_ids"] == []
    assert active_revisions() == (2, 2)

    with pytest.raises(SystemExit) as refused:
        main(["mandate-cleanup", "--database", path, "--portfolio-id", "unknown", "--confirm"])
    assert refused.value.code == 2
    assert active_revisions() == (2, 2)


def test_cleanup_cli_preview_names_a_strategy_it_adds(tmp_path, capsys) -> None:
    _, database, _, _ = _legacy_db(tmp_path, strategy_ids=["s"])
    database.close()
    assert main(["mandate-cleanup", "--database", str(tmp_path / "paper.sqlite")]) == 0
    preview = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert preview["removed_strategy_ids"] == ["s"]
    assert preview["added_strategy_ids"] == ["slow-trend-pullback"]


def test_cleanup_cli_refuses_to_write_while_the_paper_service_holds_its_lease(tmp_path, capsys) -> None:
    _, database, _, portfolio = _legacy_db(tmp_path)
    database.execute("INSERT INTO process_leases (lease_name, owner, expires_at) VALUES (?, ?, ?)",
                     ("paper-service", "boot-1", "2999-01-01T00:00:00.000000Z"))
    database.close()
    path = str(tmp_path / "paper.sqlite")

    assert main(["mandate-cleanup", "--database", path]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["new_revision"] == 2
    with pytest.raises(SystemExit) as refused:
        main(["mandate-cleanup", "--database", path, "--confirm"])
    assert refused.value.code == 2
    assert "running" in capsys.readouterr().err

    reopened = Database(path)
    try:
        assert len(_mandate_rows(reopened, portfolio)) == 1
        # An expired lease is a stopped service; the owner can then confirm.
        reopened.execute("UPDATE process_leases SET expires_at = ? WHERE lease_name = 'paper-service'",
                         ("2000-01-01T00:00:00.000000Z",))
    finally:
        reopened.close()
    assert main(["mandate-cleanup", "--database", path, "--confirm"]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["applied"] is True


def test_owner_flatten_exits_under_non_discretionary_mandate(tmp_path) -> None:
    clock, execution, broker, portfolio = _stack(tmp_path)
    _fill_entry(clock, execution, portfolio)
    execution.authority.install_mandate(paper_mandate(
        portfolio, revision=2, mandate_id="closed", discretionary_experiment=False,
        strategy_ids=["slow-trend-pullback"],
    ), role="owner")
    submitted = broker.submit_count
    execution.save_observation(_quote(clock, "100", "100.1", observation_id="flatten-quote"))
    execution.set_pause(portfolio, "FLATTEN", "owner", "close")
    assert asyncio.run(execution.advance_pause(portfolio)) == "flattening"
    assert broker.submit_count == submitted + 1
    assert execution.database.execute(
        "SELECT COUNT(*) FROM activity_events WHERE kind = 'flatten_exit_not_submitted'"
    ).fetchone()[0] == 0
    flatten = [json.loads(row["payload_json"]) for row in execution.database.execute(
        "SELECT payload_json FROM decisions WHERE decision_id LIKE 'flatten-%'"
    ).fetchall()]
    assert flatten and all(item["strategy_id"] != "slow-trend" for item in flatten)
    clock.advance(1)
    execution.on_observation(_quote(clock, "99", "100", observation_id="flatten-fill"))
    assert execution.owned_quantity(portfolio, "BTC") == Decimal("0")
