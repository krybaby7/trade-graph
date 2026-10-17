"""The owner command ``trade-graph money-limits`` (T32): preview by default, record only with ``--confirm``.

It records one owner-policy revision with the loss limit, the day start, the amount per coin, the total and the
coin list, a matching mandate revision and an audit row; it is revision-checked and refused while the paper
service runs. The tests record synthetic values (5%, 25%, 75%, 06:00); the owner's D138 settings are only the
command's defaults, shown in a preview that writes nothing. No network, model, key or order.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.authority import AuthorityRecord, paper_mandate, paper_owner_policy
from trade_graph.application.ledger import Ledger
from trade_graph.application.money_limits import D138_VALUES, MoneyLimitValues, record_money_limits
from trade_graph.cli import main
from trade_graph.contracts.models import Decision
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import AuthorityDenied, StaleState

D = Decimal
SYNTHETIC = ["--daily-loss-percent", "5", "--per-coin-percent", "25", "--total-percent", "75",
             "--day-start", "06:00"]


def _database(tmp_path):
    folder = tmp_path / "runtime"
    folder.mkdir(mode=0o700)
    path = folder / "trade_graph.sqlite"
    clock = FrozenClock(datetime(2026, 10, 15, 9, 0, tzinfo=UTC))
    database = Database(path)
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="EUR")
    ledger.deposit(portfolio, "USD", D("10000"), "opening")
    record = AuthorityRecord(database, clock)
    record.install_policy(paper_owner_policy(revision_id="initial-paper-policy"), role="owner")
    record.install_mandate(paper_mandate(portfolio), role="owner")
    database.close()
    return str(path), portfolio


def _run(capsys, *arguments) -> dict:
    assert main(["money-limits", *arguments]) == 0
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _state(path: str, portfolio: str):
    database = Database(path)
    try:
        record = AuthorityRecord(database, FrozenClock(datetime(2026, 10, 16, tzinfo=UTC)))
        audit = [dict(row) for row in database.execute("SELECT * FROM owner_policy_audit ORDER BY sequence")]
        policies = database.execute("SELECT COUNT(*) FROM owner_policy_revisions").fetchone()[0]
        return record.active_policy(), record.active_mandate(portfolio), audit, policies
    finally:
        database.close()


def test_the_preview_shows_the_owner_settings_and_writes_nothing(tmp_path, capsys) -> None:
    path, portfolio = _database(tmp_path)
    before = _state(path, portfolio)
    preview = _run(capsys, "--database", path)
    assert preview["applied"] is False and preview["new_revision"] is None
    assert preview["portfolio_id"] == portfolio and preview["current_revision"] == "initial-paper-policy"
    # The defaults are the owner's D138 decisions; they are recorded only by the owner's --confirm.
    assert preview["values"] == {"daily_loss_limit_percent": "3", "per_coin_percent": "20", "total_percent": "80",
                                 "trading_day_start_utc": "00:00", "coin_list": "rule"}
    assert set(preview["changes"]) == {"maximum_single_asset_exposure_fraction", "money_limits", "coin_scope"}
    assert preview["changes"]["maximum_single_asset_exposure_fraction"] == {"from": "0.5", "to": "0.2"}
    assert preview["mandate"] == {"current_revision": 1, "new_revision": 2}
    assert preview["paid_calls_enabled"] is False and preview["live_enabled"] is False
    assert "stay off" in preview["day_plan_orders"]
    assert _state(path, portfolio) == before


def test_confirm_records_one_audited_revision_and_a_matching_mandate(tmp_path, capsys) -> None:
    path, portfolio = _database(tmp_path)
    applied = _run(capsys, "--database", path, *SYNTHETIC, "--coins", "rule", "--confirm")
    assert applied["applied"] is True and applied["new_revision"].startswith("owner-")
    policy, mandate, audit, policies = _state(path, portfolio)
    assert policy.revision_id == applied["new_revision"] and policies == 2
    assert policy.money_limits.daily_loss_limit_fraction == D("0.05")
    assert policy.money_limits.trading_day_start_utc == "06:00"
    assert policy.money_limits.account_currency == "USD"
    assert (policy.maximum_single_asset_exposure_fraction, policy.maximum_gross_exposure_fraction) == (D("0.25"),
                                                                                                       D("0.75"))
    assert policy.coin_scope is not None and policy.coin_scope.max_scan_age_seconds == 7200
    assert (policy.live_enabled, policy.paid_calls_enabled, policy.withdrawals_allowed, policy.leverage_allowed) == (
        False, False, False, False)
    assert (mandate.revision, mandate.coin_scope) == (2, "owner_rule")
    assert (mandate.max_single_asset_exposure_fraction, mandate.max_gross_exposure_fraction) == (D("0.25"),
                                                                                                 D("0.75"))
    (row,) = audit
    assert (row["revision_id"], row["previous_revision_id"], row["set_by"], row["command"]) == (
        policy.revision_id, "initial-paper-policy", "owner", "money-limits")
    changes = json.loads(row["changes_json"])
    assert changes["portfolio_id"] == portfolio and changes["mandate_revision"] == 2
    assert changes["changes"]["money_limits"]["to"]["daily_loss_limit_fraction"] == "0.05"
    assert "day-plan orders may be installed" in applied["day_plan_orders"]


def test_recording_the_same_values_again_changes_nothing(tmp_path, capsys) -> None:
    path, portfolio = _database(tmp_path)
    _run(capsys, "--database", path, *SYNTHETIC, "--confirm")
    again = _run(capsys, "--database", path, *SYNTHETIC, "--confirm")
    assert again["applied"] is False and again["changes"] == {} and again["mandate"]["new_revision"] is None
    _policy, _mandate, audit, policies = _state(path, portfolio)
    assert len(audit) == 1 and policies == 2


def test_the_coin_list_can_stay_a_list(tmp_path, capsys) -> None:
    path, portfolio = _database(tmp_path)
    _run(capsys, "--database", path, *SYNTHETIC, "--coins", "list", "--confirm")
    policy, mandate, _audit, _policies = _state(path, portfolio)
    assert policy.coin_scope is None and mandate.coin_scope == "listed"
    assert policy.allowed_symbols == ["BTC/USD", "ETH/USD"]


def test_a_decision_made_under_the_old_revision_is_refused_afterwards(tmp_path, capsys) -> None:
    path, portfolio = _database(tmp_path)
    _run(capsys, "--database", path, *SYNTHETIC, "--confirm")
    database = Database(path)
    try:
        record = AuthorityRecord(database, FrozenClock(datetime(2026, 10, 16, tzinfo=UTC)))
        stale = Decision(record_id="d", created_at_utc=datetime(2026, 10, 16, tzinfo=UTC), run_id="r",
                         portfolio_id=portfolio, mode="paper", system_version_id="v", trace_id="t", action="hold",
                         rationale="synthetic", invalidation="synthetic", horizon_seconds=60, strategy_id="s",
                         snapshot_id="snap", mandate_revision="1", policy_revision="initial-paper-policy")
        with pytest.raises(AuthorityDenied, match="policy revision is not active"):
            record.require_for_decision(stale, venue="paper", mode="paper")
    finally:
        database.close()


def test_confirm_is_refused_when_the_previewed_revision_is_no_longer_active(tmp_path, capsys) -> None:
    path, portfolio = _database(tmp_path)
    with pytest.raises(SystemExit) as refused:
        main(["money-limits", "--database", path, *SYNTHETIC, "--expect-revision", "something-else", "--confirm"])
    assert refused.value.code == 2 and "changed since the preview" in capsys.readouterr().err
    assert _state(path, portfolio)[3] == 1
    applied = _run(capsys, "--database", path, *SYNTHETIC, "--expect-revision", "initial-paper-policy", "--confirm")
    assert applied["applied"] is True


def test_confirm_is_refused_while_the_paper_service_holds_its_lease(tmp_path, capsys) -> None:
    path, portfolio = _database(tmp_path)
    database = Database(path)
    database.execute("INSERT INTO process_leases (lease_name, owner, expires_at) VALUES (?, ?, ?)",
                     ("paper-service", "boot-1", "2999-01-01T00:00:00.000000Z"))
    database.close()
    assert _run(capsys, "--database", path, *SYNTHETIC)["applied"] is False  # a preview is still fine
    with pytest.raises(SystemExit) as refused:
        main(["money-limits", "--database", path, *SYNTHETIC, "--confirm"])
    assert refused.value.code == 2 and "running" in capsys.readouterr().err
    assert _state(path, portfolio)[3] == 1


@pytest.mark.parametrize("arguments", [
    ["--daily-loss-percent", "0"], ["--daily-loss-percent", "100"], ["--daily-loss-percent", "-1"],
    ["--daily-loss-percent", "three"], ["--daily-loss-percent", "NaN"],
    ["--per-coin-percent", "90", "--total-percent", "80"], ["--total-percent", "120"], ["--per-coin-percent", "0"],
    ["--day-start", "24:00"], ["--day-start", "7am"],
])
def test_impossible_values_are_refused_before_anything_is_written(tmp_path, capsys, arguments) -> None:
    path, portfolio = _database(tmp_path)
    with pytest.raises(SystemExit) as refused:
        main(["money-limits", "--database", path, *arguments, "--confirm"])
    assert refused.value.code == 2
    assert _state(path, portfolio)[3] == 1


def test_only_the_owner_records_money_limits(tmp_path) -> None:
    path, portfolio = _database(tmp_path)
    database = Database(path)
    try:
        record = AuthorityRecord(database, FrozenClock(datetime(2026, 10, 16, tzinfo=UTC)))
        values = MoneyLimitValues(D("0.05"), D("0.25"), D("0.75"), "06:00", "rule")
        for role in ("leader", "trader", "engineer"):
            with pytest.raises(AuthorityDenied):
                record_money_limits(record, portfolio, values, revision_id=f"by-{role}", role=role)
        assert database.execute("SELECT COUNT(*) FROM owner_policy_revisions").fetchone()[0] == 1
        with pytest.raises(StaleState, match="changed since the preview"):
            record_money_limits(record, portfolio, values, revision_id="owner-x", expect_revision="other")
    finally:
        database.close()


def test_the_d138_defaults_are_the_owner_decisions() -> None:
    assert D138_VALUES == MoneyLimitValues(D("0.03"), D("0.20"), D("0.80"), "00:00", "rule")


def test_a_missing_or_open_database_is_refused(tmp_path, capsys) -> None:
    with pytest.raises(SystemExit):
        main(["money-limits", "--database", str(tmp_path / "absent.sqlite")])
    path, _portfolio = _database(tmp_path)
    (tmp_path / "runtime").chmod(0o755)
    with pytest.raises(SystemExit):
        main(["money-limits", "--database", path, "--confirm"])
    assert "0700" in capsys.readouterr().err


def test_under_the_recorded_coin_rule_the_paper_feed_may_watch_other_usd_pairs(tmp_path, capsys, monkeypatch) -> None:
    from trade_graph.application.authority import owner_scope_covers
    from trade_graph.paper_runtime import PaperRuntimeConfig, assemble_paper_runtime

    folder = tmp_path / "runtime"
    folder.mkdir(mode=0o700)
    path = folder / "trade_graph.sqlite"
    assert main(["init", "--database", str(path)]) == 0
    capsys.readouterr()
    monkeypatch.chdir(tmp_path)
    wider = PaperRuntimeConfig(paper_symbols=["BTC/USD", "SOL/USD"])
    with pytest.raises(ValueError, match="exceed the persisted owner policy"):
        assemble_paper_runtime(path, config=wider)  # the list allows BTC and ETH only
    _run(capsys, "--database", str(path), *SYNTHETIC, "--confirm")
    runtime = assemble_paper_runtime(path, config=wider)
    try:
        policy = runtime.execution.authority.active_policy()
        assert owner_scope_covers(policy, "SOL/USD") and owner_scope_covers(policy, "BTC/USD")
        assert not owner_scope_covers(policy, "SOL/EUR") and not owner_scope_covers(policy, "BTCPERP/USD")
    finally:
        runtime.database.close()
