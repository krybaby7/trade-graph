"""HQ's today's-trading block (W8-C): today's loss against the owner's limit, the scorecard and the price collector.

Synthetic paper accounts only (``tests/money_limit_support.py``, ``tests/scorecard_support.py``): made-up limits,
prices, fills and collector records. No network, key, model, provider or exchange call; reading HQ writes nothing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from tests.hq_trading_support import collector_record, fullest_facts, golden_scorecard, runtime_for, table_counts
from tests.integration import test_hq_payload as payload
from tests.integration import test_hq_projection as t
from tests.integration.test_dashboard import _client
from tests.money_limit_support import open_account
from tests.scorecard_support import PORTFOLIO, START, Paper, golden_day

from trade_graph.api import hq, hq_copy, hq_trading, resources
from trade_graph.api.hq_contract import TradingBlock, iso_ms, money_display, validate_snapshot
from trade_graph.application import scorecard as scorecard_module
from trade_graph.application import service_controller
from trade_graph.application.scorecard import GATES_STATEMENT
from trade_graph.domain.clock import utc_iso

D = Decimal


def _text(value) -> str:
    return hq_copy.plain(value)


def _block(runtime) -> dict:
    facts = hq_trading.compute(runtime, runtime.clock.now())
    block = hq_trading.block(facts)
    TradingBlock.model_validate(block)
    return block


def _buy_sol(account, quantity: str = "25") -> str:
    intent = account.authorize("enter", "SOL/USD", quantity)
    account.dispatch()
    account.clock.advance(1)
    account.quote("SOL/USD", "99.9", "100.1")
    assert account.state(intent) == "FILLED"
    return intent


# --- today's loss against the limit ---------------------------------------------------------------------------------


def test_without_recorded_money_limits_the_loss_block_says_so_and_invents_nothing(tmp_path) -> None:
    account = open_account(tmp_path, limits=None)
    loss = _block(runtime_for(account))["loss"]
    assert loss["state"] == "not_recorded"
    assert all(loss[key] is None for key in ("currency", "limit_percent", "day_start", "value", "loss",
                                             "limit_amount", "used_percent"))
    assert "money limits aren't recorded yet" in _text(loss["headline"])
    assert "trade-graph money-limits --confirm" in _text(loss["detail"])


def test_the_loss_is_the_guards_own_reading_in_the_accounts_currency(tmp_path) -> None:
    account = open_account(tmp_path)
    account.enforce()
    _buy_sol(account)
    document = account.execution.money_limits.status(account.portfolio, account.policy).document()
    loss = _block(runtime_for(account))["loss"]
    assert loss["state"] == "within" and loss["currency"] == "USD" and loss["limit_percent"] == "4"
    assert (loss["day_start"], loss["resumes_at"]) == ("2026-10-16T00:00:00.000Z", "2026-10-17T00:00:00.000Z")
    assert loss["day_start_value"]["amount"] == document["day_start_value"] == "10000"
    assert loss["day_start_basis"] == "at_day_start"
    assert loss["value"]["amount"] == document["value"]
    assert loss["loss"]["amount"] == document["loss"] and loss["limit_amount"]["amount"] == "400"
    # 25 SOL bought at 100.1 with the 0.8% taker fee, valued at the 100 mid: a loss of 2.5 + 20.02.
    assert D(loss["loss"]["amount"]) == D("22.52")
    assert loss["used_percent"] == pytest.approx(float(D("22.52") / D("400") * 100))
    words = _text(loss["headline"])
    assert "$22.52" in words and "$400.00" in words and "4%" in words


def test_a_gain_uses_none_of_the_limit(tmp_path) -> None:
    account = open_account(tmp_path)
    account.enforce()
    _buy_sol(account)
    account.clock.advance(60)
    account.quote("SOL/USD", "109.9", "110.1")
    loss = _block(runtime_for(account))["loss"]
    assert loss["state"] == "within" and D(loss["loss"]["amount"]) < 0 and loss["used_percent"] == 0
    assert "up " in _text(loss["headline"]).lower()


def test_a_reached_limit_says_new_buying_waits_until_the_next_day_start(tmp_path) -> None:
    account = open_account(tmp_path)
    account.enforce()
    _buy_sol(account)
    account.clock.advance(60)
    account.quote("SOL/USD", "83.9", "84.1")
    reached = _block(runtime_for(account))["loss"]
    assert reached["state"] == "reached" and reached["used_percent"] == 100
    account.enforce()
    facts = hq_trading.compute(runtime_for(account), account.clock.now())
    stopped = hq_trading.block(facts)["loss"]
    assert stopped["state"] == "stopped"
    words = _text(stopped["headline"]) + " " + _text(stopped["detail"])
    assert "no new buying" in words.lower() and "safety exits" in words.lower()
    assert {"t": "time", "at": "2026-10-17T00:00:00.000Z", "style": "day_clock"} in stopped["headline"]
    (notice,) = hq_trading.notices(facts)
    assert notice["code"] == "loss_limit_reached" and notice["tone"] == "attention"
    assert "nothing was sold because of it" in _text(notice["text"]).lower()


def test_a_loss_that_cannot_be_checked_is_unknown_never_zero(tmp_path) -> None:
    account = open_account(tmp_path)
    account.enforce()
    _buy_sol(account)
    account.clock.advance(20 * 60)  # no price for 20 minutes: SOL's mark is too old
    loss = _block(runtime_for(account))["loss"]
    assert loss["state"] == "unknown" and loss["loss"] is None and loss["value"] is None
    assert loss["day_start_value"]["amount"] == "10000"
    assert "too old" in _text(loss["detail"]) and "$0.00" not in json.dumps(loss)


# --- the scorecard ---------------------------------------------------------------------------------------------------


@pytest.fixture
def paper(tmp_path):
    paper = Paper(tmp_path / "state.sqlite", at=START - timedelta(hours=1))
    yield paper
    paper.close()


def test_the_scorecard_shows_the_scorecards_own_figures(paper) -> None:
    golden_day(paper)  # the hand-checked day; the clock is now an hour after its end
    paper.mark("ETH", "3200")  # fresh prices, unchanged, so nothing is provisional
    paper.mark("BTC", "62000")
    runtime = runtime_for(paper)
    runtime.portfolio_id = PORTFOLIO
    card = _block(runtime)["scorecard"]
    assert card["available"] and card["status"] == "complete" and card["currency"] == "USD"
    assert (card["period_start"], card["period_end"]) == ("2026-11-02T00:00:00.000Z", "2026-11-03T01:00:00.000Z")
    assert card["result"]["amount"] == "210.14" and card["versus_btc"]["amount"] == "40.82783616"
    assert card["beats_cash"] is True and card["beats_btc"] is True
    facts = {item["label"]: _text(item["value"]) for item in card["facts"]}
    assert facts["Trades"] == "2 (1 won, 1 lost)" and facts["Fees paid"] == "$102.06"
    assert facts["Average win"] == "$220.64" and facts["Average loss"] == money_display("-66.6", "USD")
    assert facts["Worst fall"] == "1.6% ($165.26)" and facts["Longest losing run"] == "1 trade"
    lines = [_text(line) for line in card["lines"]]
    assert any("$40.83" in line and "Bitcoin" in line for line in lines)
    assert any("euros" in line and "€193.33" in line for line in lines)
    assert any("Real AI spending" in line and "never pays" in line for line in lines)
    assert any("D138 item 7" in line and "not a pass mark" in line for line in lines)
    assert _text(card["note"]) == GATES_STATEMENT
    assert "up $210.14" in _text(card["headline"]).lower()


def test_an_unscorable_account_says_why_in_plain_words(paper) -> None:
    runtime = runtime_for(paper)
    runtime.portfolio_id = PORTFOLIO
    paper.at(START)
    card = _block(runtime)["scorecard"]
    assert card["available"] is False and card["result"] is None and card["facts"] == []
    assert "no opening deposit yet" in _text(card["unavailable_reason"])


def test_a_provisional_scorecard_lists_why(paper) -> None:
    golden_day(paper)
    paper.at(START + timedelta(hours=25, minutes=20))  # the newest prices are 80 minutes old now
    runtime = runtime_for(paper)
    runtime.portfolio_id = PORTFOLIO
    card = _block(runtime)["scorecard"]
    assert card["status"] == "provisional"
    assert any("minutes old" in _text(item) for item in card["provisional"])


# --- caching like HQ's money facts ---------------------------------------------------------------------------------


def test_the_facts_are_cached_for_a_minute_behind_a_fingerprint(paper, monkeypatch) -> None:
    golden_day(paper)
    runtime = runtime_for(paper)
    runtime.portfolio_id = PORTFOLIO
    calls = []
    original = scorecard_module.compute_scorecard

    def counted(*args, **kwargs):
        calls.append(kwargs["end"])
        return original(*args, **kwargs)

    monkeypatch.setattr(hq_trading, "compute_scorecard", counted)
    timer = [1000.0]
    monkeypatch.setattr(hq_trading, "_monotonic", lambda: timer[0])
    first = hq_trading.facts(runtime, paper.clock.now())
    assert hq_trading.facts(runtime, paper.clock.now()) is first and len(calls) == 1
    timer[0] += 30
    paper.fill("ETH/USD", "sell", "0.5", "3200", liquidity="taker")  # a new fill shows on the next poll
    assert hq_trading.facts(runtime, paper.clock.now()) is not first and len(calls) == 2
    timer[0] += hq_trading.MAX_AGE_SECONDS + 1
    hq_trading.facts(runtime, paper.clock.now())
    assert len(calls) == 3


def test_reading_the_block_writes_nothing(tmp_path) -> None:
    account = open_account(tmp_path)
    account.enforce()
    _buy_sol(account)
    collector_record(account.ledger, account.portfolio, {"job": "scan", "status": "ok"}, at=account.clock.now())
    before = table_counts(account.database)
    changes = account.database.execute("SELECT total_changes()").fetchone()[0]
    for _ in range(2):
        _block(runtime_for(account))
    assert table_counts(account.database) == before
    assert account.database.execute("SELECT total_changes()").fetchone()[0] == changes


# --- the price collector -------------------------------------------------------------------------------------------


def test_the_collector_panel_reads_its_activity_records(tmp_path) -> None:
    account = open_account(tmp_path)
    runtime = runtime_for(account)
    now = account.clock.now()
    assert _block(runtime)["collector"]["state"] == "no_records"
    collector_record(account.ledger, account.portfolio, {"job": "catalog", "status": "ok", "usd_pairs": 512},
                     at=now - timedelta(hours=3))
    collector_record(account.ledger, account.portfolio, {"job": "candles", "status": "ok", "inserted": 40,
                                                         "failed_series": 0, "gaps": 0}, at=now - timedelta(hours=2))
    collector_record(account.ledger, account.portfolio,
                     {"job": "scan", "status": "ok", "ranking_pass": {"attempted": 400, "ok": 398, "runs": 14}},
                     at=now - timedelta(minutes=10))
    collector = _block(runtime)["collector"]
    assert collector["state"] == "ok" and collector["problems_24h"] == 0
    assert collector["last_scan_at"] == iso_ms(now - timedelta(minutes=10))
    assert "working" in _text(collector["headline"])
    account.clock.advance(60)
    collector_record(account.ledger, account.portfolio,
                     {"job": "candles", "status": "partial", "failed_series": 2, "gaps": 1,
                      "stop_reason": "rate_limited"}, at=account.clock.now())
    collector = _block(runtime)["collector"]
    assert collector["state"] == "attention" and collector["problems_24h"] == 1
    words = " ".join(_text(line) for line in collector["lines"])
    assert "rate limit" in words.lower() and "gap" in words.lower()


def test_a_collector_switched_off_in_the_settings_says_so(tmp_path) -> None:
    account = open_account(tmp_path)
    config = SimpleNamespace(price_collector=SimpleNamespace(enabled=False))
    collector = _block(runtime_for(account, config=config))["collector"]
    assert collector["state"] == "off" and "switched off" in _text(collector["headline"])


# --- the fullest block and the live account ------------------------------------------------------------------------


def test_the_fullest_block_stays_small_and_valid() -> None:
    now = datetime(2026, 10, 10, 8, 14, 30, tzinfo=UTC)
    block = hq_trading.block(fullest_facts(now))
    TradingBlock.model_validate(block)
    size = len(json.dumps(block, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    print(f"fullest trading block: {size} B")
    assert size < 9 * 1024
    assert block["scorecard"]["more_provisional"] == 2 and len(block["scorecard"]["provisional"]) == 5
    assert block["loss"]["state"] == "stopped"


def test_a_live_account_shows_the_block_without_paper_words(tmp_path) -> None:
    account = open_account(tmp_path)
    account.database.execute("UPDATE portfolios SET mode='live' WHERE portfolio_id=?", (account.portfolio,))
    block = _block(runtime_for(account))
    assert block["loss"]["state"] == "not_applied" and block["scorecard"]["available"] is False
    assert "practice" not in json.dumps(block).lower()


def test_golden_scorecard_document_feeds_the_block_unchanged() -> None:
    document = golden_scorecard()
    facts = {"as_of": datetime(2026, 11, 3, tzinfo=UTC), "paper": True,
             "loss": {"recorded": False, "limits": None, "document": None, "error": None},
             "scorecard": {"document": document, "unavailable": None},
             "collector": {"enabled": None, "events": [], "truncated": False}}
    block = hq_trading.block(facts)
    TradingBlock.model_validate(block)
    assert block["scorecard"]["result"]["amount"] == document["metrics"]["result"]


# --- in the snapshot ---------------------------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path, monkeypatch):
    """The HQ projection tests' dashboard (``tests/integration/test_hq_projection.py``), two days in."""
    client, database, portfolio, token, csrf, leader = _client(tmp_path)
    runtime = client.app.state.runtime
    runtime.clock.advance(2 * 86400 + 3600)
    monkeypatch.setattr(service_controller, "process_identity", lambda pid: "birth" if pid == t.LIVE_PID else None)
    monkeypatch.setattr(resources, "observation",
                        lambda runtime: {"observed_at": utc_iso(runtime.clock.now()), "warnings": []})
    yield SimpleNamespace(client=client, runtime=runtime, db=database, pid=portfolio, token=token, csrf=csrf,
                          leader=leader)
    database.close()


@pytest.fixture
def busy(env):
    """The busy synthetic week of ``tests/integration/test_hq_payload.py``."""
    env.runtime.clock.advance(8 * 86400 - 3600)
    payload._seed(env)
    return env


def test_the_snapshot_carries_the_block_and_reading_it_starts_nothing(env) -> None:
    t.quiet(env)
    data = t.snap(env)
    trading = data["trading"]
    assert trading["loss"]["state"] == "not_recorded"  # this account has no owner policy with money limits
    assert trading["collector"]["state"] == "no_records"
    # This dashboard's account opened in January 2026, before the paper fee profile's first revision: the
    # scorecard says so in plain words instead of guessing the fees.
    card = trading["scorecard"]
    assert card["available"] is False and "fees to compare with cannot be read" in _text(card["unavailable_reason"])
    assert "loss_limit_reached" not in [notice["code"] for notice in data["notices"]]


@pytest.mark.parametrize(("failure", "words", "consequence"), [
    ("money_limits:StaleState", "couldn't check your money limits", "safety exits and sales keep working"),
    ("day_plans:ValidationFailure", "couldn't look after the day plans", "safety exits already in place stay"),
    ("collector:RuntimeError", "the price collector's last step failed", "come from the separate price feed"),
])
def test_a_failing_step_is_named_in_plain_words(env, failure, words, consequence) -> None:
    t.run(env)
    t.reading(env)
    env.db.execute("UPDATE graph_service_runs SET error_type=? WHERE run_id='r-1'", (failure,))
    data = t.snap(env)
    prefix, code = failure.split(":")
    (problem,) = data["team"]["service"]["problems"]
    assert (problem["prefix"], problem["code"]) == (prefix + ":", code)
    assert data["now"]["code"] == "routine_failing"
    assert words in _text(data["now"]["headline"]) and "a routine step is failing" not in json.dumps(data)
    card = next(item for item in data["attention"] if item["id"].startswith("routine_failing"))
    assert words in _text(card["title"]).lower() and consequence in _text(card["consequence"]).lower()


def test_a_live_snapshot_carries_the_block_without_practice_words(env) -> None:
    t.quiet(env, mode="live")
    data = t.snap(env)
    assert data["trading"]["loss"]["state"] == "not_applied"
    assert "practice" not in json.dumps(data["trading"]).lower()


def test_the_busy_week_with_the_fullest_trading_block_fits_the_budget(busy) -> None:
    data = hq.snapshot(busy.runtime, payload.OWNER, calendar_days=14)
    data["trading"] = hq_trading.block(fullest_facts(busy.runtime.clock.now()))
    validate_snapshot(data)
    size = payload._wire_bytes(data)
    print(f"busy week with the fullest trading block: {size} B")
    assert size <= payload.BUDGET and len(json.dumps(data)) < payload.BUDGET
