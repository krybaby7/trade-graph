"""The owner's money limits enforced by software (T32): the day-plan gate, the daily loss limit, the coin rule,
D127 and who may change what.

Synthetic values only (4% a day, 30% per coin, 90% in total; ``tests/money_limit_support.py``), never the owner's
D138 settings, which reach an installation through ``trade-graph money-limits --confirm`` at step L3. Everything
runs on the paper broker with scripted quotes: no network, model, key or real order.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError
from tests.money_limit_support import MORNING, SYMBOLS, Account, open_account, synthetic_policy

from trade_graph.api import hq_copy
from trade_graph.application.authority import paper_mandate
from trade_graph.application.coin_scanner.store import CoinRuleAnswer
from trade_graph.application.money_limits import DAY_PLAN_ORDERS_OFF
from trade_graph.contracts.models import Decision
from trade_graph.contracts.money_limits import CoinScope
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState

D = Decimal


def _rows(account: Account, table: str) -> list[dict]:
    return [dict(row) for row in account.database.execute(f"SELECT * FROM {table} ORDER BY sequence")]


def _events(account: Account, kind: str) -> list[dict]:
    return [json.loads(row["payload_json"]) for row in account.database.execute(
        "SELECT payload_json FROM activity_events WHERE kind = ? ORDER BY rowid", (kind,))]


def _buy_sol(account: Account, quantity: str = "25") -> str:
    intent = account.authorize("enter", "SOL/USD", quantity)
    account.dispatch()
    account.clock.advance(1)
    account.quote("SOL/USD", "99.9", "100.1")
    assert account.state(intent) == "FILLED"
    return intent


def _protect(account: Account, quantity: str, stop: str) -> str:
    intent = account.execution.place_protection(account.portfolio, "SOL/USD", D(quantity), D(stop), "snap-stop")
    account.dispatch()
    assert account.state(intent) == "OPEN"
    return intent


# --- the day-plan gate ---------------------------------------------------------------------------------------------

def test_day_plan_orders_stay_off_until_the_owner_records_money_limits(tmp_path) -> None:
    account = open_account(tmp_path, limits=None)
    with pytest.raises(AuthorityDenied, match="day-plan orders stay off") as refused:
        account.authorize("enter", "SOL/USD", "1", limit="95", day_plan=True)
    assert str(refused.value) == DAY_PLAN_ORDERS_OFF
    assert account.database.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0
    # The existing Trader path is unchanged while no limits are recorded; a day plan's protective exits are not
    # entries and never wait for the gate.
    assert account.authorize("enter", "SOL/USD", "1", limit="95")
    account.install(synthetic_policy("synthetic-2"))
    assert account.authorize("enter", "SOL/USD", "1", limit="94", day_plan=True)


# --- the day-start value -------------------------------------------------------------------------------------------

def test_the_day_start_value_is_read_at_the_day_start_and_recorded_once(tmp_path) -> None:
    account = open_account(tmp_path)
    summary = account.enforce()
    assert summary["money_limits"]["day_start"] == "2026-10-16T00:00:00.000000Z"
    assert summary["money_limits"]["resumes_at"] == "2026-10-17T00:00:00.000000Z"
    assert summary["money_limits"]["day_start_value"] == "10000"
    assert summary["money_limits"]["day_start_basis"] == "at_day_start"
    account.enforce()
    rows = _rows(account, "trading_day_values")
    assert len(rows) == 1
    assert (rows[0]["value"], rows[0]["valued_at"], rows[0]["basis"], rows[0]["policy_revision"]) == (
        "10000", "2026-10-16T00:00:00.000000Z", "at_day_start", "synthetic-1")


def test_a_portfolio_opened_during_the_day_takes_its_first_valuation(tmp_path) -> None:
    account = open_account(tmp_path, opened=datetime(2026, 10, 16, 7, 0, tzinfo=UTC))
    status = account.execution.money_limits.status(account.portfolio, account.policy)
    assert status.day_start is None and status.unknown == "the day-start value is not known yet"
    with pytest.raises(StaleState, match="daily loss limit can't be checked"):
        account.authorize("enter", "SOL/USD", "1")
    account.enforce()
    (row,) = _rows(account, "trading_day_values")
    assert (row["basis"], row["valued_at"], row["value"]) == ("first_valuation_after_start", utc_iso(MORNING),
                                                               "10000")
    assert account.authorize("enter", "SOL/USD", "1")


def test_a_held_coin_without_a_recent_mark_at_the_day_start_waits_for_a_fresh_valuation(tmp_path) -> None:
    account = open_account(tmp_path)
    account.enforce()
    _buy_sol(account, "10")
    # The service stops overnight; it is back at 06:00 the next day with fresh prices.
    account.clock.advance((datetime(2026, 10, 17, 6, 0, tzinfo=UTC) - account.clock.now()).total_seconds())
    status = account.execution.money_limits.status(account.portfolio, account.policy)
    assert status.day_start is None and status.unknown is not None
    account.quote("SOL/USD", "109.9", "110.1")
    account.quote("BTC/USD", "49990", "50010")
    summary = account.enforce()["money_limits"]
    assert summary["day_start_basis"] == "first_valuation_after_start"
    assert summary["day_start_valued_at"] == "2026-10-17T06:00:00.000000Z"
    assert D(summary["day_start_value"]) == account.value()


def test_the_loss_counts_fees_and_open_positions_at_their_marks(tmp_path) -> None:
    account = open_account(tmp_path)
    account.enforce()
    _buy_sol(account)
    status = account.execution.money_limits.status(account.portfolio, account.policy)
    fee = D("25") * D("100.1") * D("0.008")
    assert status.loss == D("25") * (D("100.1") - D("100")) + fee  # half the spread and the taker fee
    assert status.limit_amount == D("400") and not status.reached


# --- the synthetic day across the loss limit -----------------------------------------------------------------------

def test_a_day_across_the_loss_limit_cancels_resting_entries_refuses_new_ones_and_keeps_protective_exits(
        tmp_path) -> None:
    account = open_account(tmp_path)
    account.enforce()
    _buy_sol(account)
    stop = _protect(account, "20", "80")
    resting = [account.authorize("enter", "SOL/USD", "2", limit="70"),
               account.authorize("enter", "BTC/USD", "0.01", limit="45000")]
    account.dispatch()
    assert [account.state(intent) for intent in resting] == ["OPEN", "OPEN"]  # resting at the venue
    unsent = account.authorize("enter", "BTC/USD", "0.01", limit="44000")    # and one not sent yet

    # SOL falls to 84: 25 × 16 plus the fees and spread is more than 4% of 10,000.
    account.clock.advance(60)
    account.quote("SOL/USD", "83.9", "84.1")
    assert account.value() < D("9600")
    summary = account.enforce()
    assert summary["money_limits"]["stopped"] is True and summary["money_limits"]["reached"] is True
    assert set(summary["cancelled"]) == {*resting, unsent}
    assert [account.state(intent) for intent in resting] == ["CANCELLED", "CANCELLED"]
    assert account.state(unsent) == "REJECTED"
    assert account.state(stop) == "OPEN"  # the protective exit stays
    (row,) = _rows(account, "daily_loss_stops")
    assert row["resumes_at"] == "2026-10-17T00:00:00.000000Z" and D(row["loss"]) >= D(row["limit_amount"]) == 400
    assert len(_events(account, "daily_loss_limit_reached")) == 1

    # New entries are refused for the rest of the day, with plain words in HQ.
    with pytest.raises(AuthorityDenied, match="daily loss limit reached") as refused:
        account.authorize("enter", "BTC/USD", "0.001")
    refusal = account.execution.record_refusal(account.portfolio, account.decision("enter", "BTC/USD", "0.001"),
                                               refused.value)
    assert "loss limit" in hq_copy.reason_plain(refusal["reason"])
    # Exits go on: the Trader may sell what the protective exit does not hold.
    sell = account.authorize("exit", "SOL/USD", "5")
    account.dispatch()
    account.clock.advance(1)
    account.quote("SOL/USD", "83.9", "84.1")
    assert account.state(sell) == "FILLED" and account.held("SOL") == D("20")

    # The protective exit still fires when the price reaches it.
    account.clock.advance(60)
    account.quote("SOL/USD", "78.9", "79.1")
    assert account.state(stop) == "FILLED" and account.held("SOL") == 0

    # A recovery the same day does not lift the stop.
    account.clock.advance(3600)
    account.quote("SOL/USD", "119.9", "120.1")
    account.enforce()
    with pytest.raises(AuthorityDenied, match="daily loss limit reached"):
        account.authorize("enter", "SOL/USD", "1")
    assert len(_rows(account, "daily_loss_stops")) == 1

    # Buying resumes at the next day start, measured from a new day-start value.
    account.clock.advance((datetime(2026, 10, 16, 23, 55, tzinfo=UTC) - account.clock.now()).total_seconds())
    account.quote("SOL/USD", "99.9", "100.1")
    account.quote("BTC/USD", "49990", "50010")
    account.clock.advance(330)
    account.quote("SOL/USD", "99.9", "100.1")
    summary = account.enforce()["money_limits"]
    assert summary["day_start"] == "2026-10-17T00:00:00.000000Z" and summary["stopped"] is False
    assert summary["day_start_basis"] == "at_day_start"
    assert account.authorize("enter", "SOL/USD", "1")


def test_the_limit_refuses_entries_as_soon_as_it_is_reached_even_before_the_stop_is_recorded(tmp_path) -> None:
    account = open_account(tmp_path)
    account.enforce()
    _buy_sol(account)
    pending = account.authorize("enter", "BTC/USD", "0.01", limit="45000")
    account.clock.advance(60)
    account.quote("SOL/USD", "83.9", "84.1")
    with pytest.raises(AuthorityDenied, match="daily loss limit reached"):
        account.authorize("enter", "BTC/USD", "0.001")
    assert _rows(account, "daily_loss_stops") == []  # authorize only reads; the management step records
    submitted = account.broker.submit_count
    account.dispatch()  # an unsent entry is not sent once the limit is reached
    assert account.state(pending) == "REJECTED" and account.broker.submit_count == submitted
    account.enforce()
    assert len(_rows(account, "daily_loss_stops")) == 1


def test_a_day_start_at_another_hour_moves_the_reset(tmp_path) -> None:
    from trade_graph.contracts.money_limits import OwnerMoneyLimits

    account = open_account(tmp_path, limits=OwnerMoneyLimits(daily_loss_limit_fraction="0.04",
                                                             trading_day_start_utc="06:00"))
    summary = account.enforce()["money_limits"]
    assert (summary["day_start"], summary["resumes_at"]) == ("2026-10-16T06:00:00.000000Z",
                                                             "2026-10-17T06:00:00.000000Z")


def test_the_paper_service_management_step_runs_the_money_limits(tmp_path) -> None:
    from trade_graph.application.paper_service import PaperService

    account = open_account(tmp_path)
    service = PaperService(account.database, account.execution, clock=account.clock,
                           portfolio_ids=[account.portfolio], automatic_schedule=False)
    service.management_portfolio_ids = (account.portfolio,)
    _management, failures = service._management()
    assert failures == ()
    assert len(_rows(account, "trading_day_values")) == 1


# --- the coin rule (D138 item 3) -----------------------------------------------------------------------------------

class ScriptedRule:
    """Answers like ``CoinScanStore.coin_rule``; ``answers`` maps a symbol to passed, excluded or unknown."""

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, datetime, timedelta]] = []

    def __call__(self, symbol: str, *, as_of: datetime, max_age: timedelta) -> CoinRuleAnswer:
        self.calls.append((symbol, as_of, max_age))
        answer = self.answers.get(symbol, "not_in_catalog")
        if answer == "passed":
            return CoinRuleAnswer(symbol, "passed_checks", True, scan_id="scan", position=3)
        if answer in ("no_recent_scan", "scan_failed"):
            return CoinRuleAnswer(symbol, answer, None)
        if answer == "not_in_catalog":
            return CoinRuleAnswer(symbol, "not_in_catalog", False, scan_id="scan")
        return CoinRuleAnswer(symbol, "excluded", False, scan_id="scan", reasons=(answer,))


def _rule_account(tmp_path, answers: dict[str, str], *, mandate_scope: str = "owner_rule",
                  symbols: list[str] | None = None) -> tuple[Account, ScriptedRule]:
    rule = ScriptedRule(answers)
    account = open_account(tmp_path, scope=CoinScope(), mandate_scope=mandate_scope, coin_rule=rule,
                           symbols=symbols or ["BTC/USD"])
    account.enforce()
    return account, rule


def test_under_the_coin_rule_any_pair_that_passed_the_scanner_checks_may_be_bought(tmp_path) -> None:
    account, rule = _rule_account(tmp_path, {"SOL/USD": "passed", "ADA/USD": "thin_volume"})
    assert account.authorize("enter", "SOL/USD", "1")  # not on the old BTC-only list
    assert rule.calls[-1][0] == "SOL/USD" and rule.calls[-1][2] == timedelta(hours=2)
    assert rule.calls[-1][1] == account.clock.now()
    with pytest.raises(AuthorityDenied, match="outside the owner's coin rule: thin_volume"):
        account.authorize("enter", "ADA/USD", "100")
    with pytest.raises(AuthorityDenied, match="outside the owner's coin rule: not_in_catalog"):
        account.authorize("enter", "BTC/USD", "0.001")


@pytest.mark.parametrize("unknown", ["no_recent_scan", "scan_failed"])
def test_an_unknown_coin_rule_refuses_new_entries_as_stale(tmp_path, unknown) -> None:
    account, _rule = _rule_account(tmp_path, {"SOL/USD": unknown})
    with pytest.raises(StaleState, match=f"coin rule can't be checked: {unknown}"):
        account.authorize("enter", "SOL/USD", "1")
    assert account.database.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0


def test_the_coin_rule_never_blocks_an_exit_and_rechecks_an_unsent_entry(tmp_path) -> None:
    account, rule = _rule_account(tmp_path, {"SOL/USD": "passed", "ADA/USD": "passed"})
    _buy_sol(account, "5")
    rule.answers["SOL/USD"] = "wide_spread"
    sell = account.authorize("exit", "SOL/USD", "5")  # a coin that later fails the scan can still be sold
    waiting = account.authorize("enter", "ADA/USD", "100")
    rule.answers["ADA/USD"] = "no_recent_scan"
    submitted = account.broker.submit_count
    account.dispatch()
    assert account.state(sell) == "OPEN"
    assert account.state(waiting) == "SUBMISSION_PENDING"  # unknown holds an unsent entry, it is not rejected
    rule.answers["ADA/USD"] = "thin_volume"
    account.dispatch()
    assert account.state(waiting) == "REJECTED"            # excluded rejects it before it is sent
    assert account.broker.submit_count == submitted + 1    # only the SOL exit was sent


def test_the_coin_rule_covers_only_its_quote_currency_and_a_listed_mandate_still_narrows_it(tmp_path) -> None:
    account, _rule = _rule_account(tmp_path, {"SOL/USD": "passed", "ADA/USD": "passed"}, mandate_scope="listed",
                                   symbols=["BTC/USD", "SOL/USD"])
    assert account.authorize("enter", "SOL/USD", "1")
    with pytest.raises(AuthorityDenied, match="outside the active mandate"):
        account.authorize("enter", "ADA/USD", "100")  # passed the scan, but the mandate lists other coins
    with pytest.raises(AuthorityDenied, match="only USD pairs"):
        account.execution.authority.require_coin_rule(account.policy, "SOL/EUR")


def test_the_coin_rule_reads_the_scanner_store_by_default(tmp_path) -> None:
    from tests.coin_scanner_support import (
        AS_OF,
        Path_,
        hourly,
        new_store,
        record,
        save_candles,
        save_catalog,
        ticker,
        tight_quote,
    )
    from tests.integration.test_coin_scanner import RULES

    from trade_graph.application.coin_scanner.scanner import CoinScanner
    from trade_graph.application.coin_scanner.store import CoinScanStore
    from trade_graph.domain.clock import FrozenClock

    candles = new_store(tmp_path / "candles" / "private")
    save_catalog(candles, [record(base) for base in ("SOL", "LTC", "USDT")], observed_at=AS_OF - timedelta(hours=5))
    save_candles(candles, hourly("SOL/USD", Path_("200", "0.03", "0.0005", "5000")))
    save_candles(candles, hourly("LTC/USD", Path_("50", "0.02", "0", "100")))
    scan = CoinScanner(candles, clock=FrozenClock(AS_OF + timedelta(seconds=30)), rules=RULES).scan(
        as_of=AS_OF, ticker=ticker({"SOL/USD": tight_quote("200", "0.0002"), "LTC/USD": tight_quote("50")}))
    account = open_account(tmp_path, scope=CoinScope(), mandate_scope="owner_rule", symbols=["BTC/USD"],
                           now=AS_OF + timedelta(minutes=10))
    CoinScanStore(account.database, account.clock).record(scan)
    account.enforce()
    assert account.authorize("enter", "SOL/USD", "1")
    with pytest.raises(AuthorityDenied, match="thin_volume"):
        account.authorize("enter", "LTC/USD", "1")
    with pytest.raises(AuthorityDenied, match="stablecoin"):
        account.authorize("enter", "USDT/USD", "1")
    account.clock.advance(3 * 3600)
    account.quote("SOL/USD", "99.9", "100.1")
    with pytest.raises(StaleState, match="no_recent_scan"):
        account.authorize("enter", "SOL/USD", "1")


# --- D127: no riba products ----------------------------------------------------------------------------------------

def test_derivatives_and_staking_variants_are_refused_even_with_known_rules(tmp_path) -> None:
    account = open_account(tmp_path, symbols=["SOL/USD", "BTC/USD", "BTCPERP/USD"])
    account.quote("BTCPERP/USD", "49990", "50010")
    with pytest.raises(AuthorityDenied, match="D127"):
        account.authorize("enter", "BTCPERP/USD", "0.001")
    with pytest.raises(AuthorityDenied, match="D127"):
        account.execution.authorize(account.portfolio, account.decision("enter", "SOL/USD", "1").model_copy(
            update={"symbol": "SOL/USD:USD"}))
    assert account.database.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0


def test_margin_short_sales_and_lending_have_no_way_in(tmp_path) -> None:
    account = open_account(tmp_path)
    with pytest.raises(AuthorityDenied, match="margin and leverage are refused"):
        account.execution.authority.install_policy(
            synthetic_policy("leveraged").model_copy(update={"leverage_allowed": True}), role="owner")
    with pytest.raises(AuthorityDenied, match="short exposure"):
        account.execution.authority.install_mandate(
            paper_mandate(account.portfolio, revision=9, gross="0.9", asset="0.3", symbols=["SOL/USD"]).model_copy(
                update={"order_types_long_only": False}), role="owner")
    # A short sale is an exit of more than is held: refused, nothing is borrowed, no intent is written.
    with pytest.raises(Exception, match="insufficient available balance"):
        account.authorize("exit", "SOL/USD", "1")
    for action in ("short", "lend", "stake", "earn", "borrow"):
        with pytest.raises(ValidationError):
            Decision.model_validate({**account.decision("enter", "SOL/USD", "1").model_dump(), "action": action})
    assert account.database.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0


def test_interest_like_postings_are_flagged_once_for_the_owner(tmp_path) -> None:
    account = open_account(tmp_path, limits=None)
    account.ledger.deposit(account.portfolio, "USD", D("1.25"), "venue staking reward 2026-10")
    first = account.enforce()["flagged"]
    assert [item["reasons"] for item in first] == [["reward", "staking"]]
    assert account.enforce()["flagged"] == []
    (flag,) = _rows(account, "riba_posting_flags")
    assert flag["kind"] == "deposit" and json.loads(flag["reasons_json"]) == ["reward", "staking"]
    (event,) = _events(account, "riba_interest_posting")
    assert event["amount"] == "1.25" and "D127" in event["note"]


# --- who may change the limits ------------------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["leader", "trader", "engineer", "research", "optimisation"])
def test_no_agent_can_record_or_raise_the_owner_money_limits(tmp_path, role) -> None:
    account = open_account(tmp_path)
    before = account.database.execute("SELECT COUNT(*) FROM owner_policy_revisions").fetchone()[0]
    raised = synthetic_policy(f"raised-by-{role}", gross="1", asset="1").model_copy(update={"money_limits": None})
    with pytest.raises(AuthorityDenied):
        account.execution.authority.install_policy(raised, role=role)
    assert account.database.execute("SELECT COUNT(*) FROM owner_policy_revisions").fetchone()[0] == before
    assert account.policy.money_limits.daily_loss_limit_fraction == D("0.04")


def test_the_leader_may_narrow_the_mandate_but_never_raise_it_or_follow_a_rule_the_owner_lacks(tmp_path) -> None:
    account = open_account(tmp_path)
    authority = account.execution.authority
    def mandate(asset: str, **updates):
        return paper_mandate(account.portfolio, revision=2, gross="0.9", asset=asset, symbols=SYMBOLS).model_copy(
            update=updates)

    with pytest.raises(AuthorityDenied, match="single-asset"):
        authority.install_mandate(mandate("0.5"), role="leader")
    with pytest.raises(AuthorityDenied, match="coin rule"):
        authority.install_mandate(mandate("0.3", coin_scope="owner_rule"), role="leader")
    authority.install_mandate(mandate("0.1"), role="leader")
    assert authority.limits(account.policy, authority.active_mandate(account.portfolio))[1] == D("0.1")
    with pytest.raises(AuthorityDenied, match="single asset exposure"):
        account.authorize("enter", "SOL/USD", "11")  # 1,100 of 10,000 is above the Leader's 10%


# --- entries counted as if filled, through Execution ---------------------------------------------------------------

def test_a_resting_entry_below_the_market_counts_at_its_value_once_filled(tmp_path) -> None:
    account = open_account(tmp_path, limits=None)
    # 40 SOL at a limit of 60 is 2,400 (24%); once it fills at the market's 100 it is worth 4,000 (40%).
    with pytest.raises(AuthorityDenied, match="single asset exposure"):
        account.authorize("enter", "SOL/USD", "40", limit="60")
    assert account.authorize("enter", "SOL/USD", "29", limit="60")
    with pytest.raises(AuthorityDenied, match="single asset exposure"):
        account.authorize("enter", "SOL/USD", "1", limit="60")  # the resting 29 count at 100.1 each


def test_a_held_coin_counts_at_its_mark_when_a_cheaper_entry_is_added(tmp_path) -> None:
    account = open_account(tmp_path, limits=None)
    _buy_sol(account, "29")
    with pytest.raises(AuthorityDenied, match="single asset exposure"):
        account.authorize("enter", "SOL/USD", "1", limit="50")
