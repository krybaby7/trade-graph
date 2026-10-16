"""The owner's money limits as policy fields and pure rules (T32; D138 items 1, 2 and 6; D127).

Synthetic values only: the owner's D138 settings reach an installation through the owner command at step L3.
No database, clock or network here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from trade_graph.contracts.models import Mandate, OwnerPolicy
from trade_graph.contracts.money_limits import DEFAULT_TRADING_DAY_START, CoinScope, OwnerMoneyLimits
from trade_graph.domain.errors import AuthorityDenied
from trade_graph.domain.money import Money
from trade_graph.kernel import money_limits as rules
from trade_graph.kernel.authority import assert_action, mandate_within_owner, path_is_protected
from trade_graph.kernel.riba import interest_posting_reasons, refuse_riba_symbol

D = Decimal


def _policy(**updates) -> OwnerPolicy:
    return OwnerPolicy(**{
        "revision_id": "p1",
        "virtual_capital": Money(amount="10000", currency="USD"),
        "monthly_operating": Money(amount="5", currency="EUR"),
        "priority_reserve": Money(amount="1", currency="EUR"),
        "daily_paid_limit": Money(amount="1.50", currency="EUR"),
        "root_paid_limit": Money(amount="1.50", currency="EUR"),
        "maximum_gross_exposure_fraction": "0.75",
        "maximum_single_asset_exposure_fraction": "0.25",
        "allowed_change_classes": ["artifact_config"],
        "allowed_venues": ["paper"],
        "allowed_symbols": ["BTC/USD", "ETH/USD"],
        **updates,
    })


def _mandate(**updates) -> Mandate:
    return Mandate(**{
        "mandate_id": "m1", "portfolio_id": "p", "revision": 1, "strategy_ids": ["slow-trend-pullback"],
        "symbols": ["BTC/USD"], "allowed_order_types": ["market", "limit"], "max_gross_exposure_fraction": "0.75",
        "max_single_asset_exposure_fraction": "0.25", "decision_horizon_seconds": 3600, "max_quote_age_seconds": 30,
        "expires_at_utc": datetime(2030, 1, 1, tzinfo=UTC), "resource_note": "synthetic", **updates,
    })


# --- the owner-policy fields --------------------------------------------------------------------------------------

def test_money_limits_are_decimal_fields_with_the_day_start_defaulting_to_midnight_utc() -> None:
    limits = OwnerMoneyLimits(daily_loss_limit_fraction="0.045")
    assert limits.daily_loss_limit_fraction == D("0.045")
    assert limits.trading_day_start_utc == DEFAULT_TRADING_DAY_START == "00:00"
    assert limits.account_currency == "USD"
    assert limits.model_dump(mode="json")["daily_loss_limit_fraction"] == "0.045"


@pytest.mark.parametrize("fraction", ["0", "-0.01", "1", "1.5", "NaN", "Infinity", 0.03])
def test_a_daily_loss_limit_outside_zero_to_one_or_a_binary_float_is_refused(fraction) -> None:
    with pytest.raises(ValidationError):
        OwnerMoneyLimits(daily_loss_limit_fraction=fraction)


@pytest.mark.parametrize("start", ["24:00", "7:00", "07:60", "07", "07:00:00", "", "noon", "07:00Z"])
def test_the_trading_day_start_is_a_plain_utc_hour_and_minute(start) -> None:
    with pytest.raises(ValidationError):
        OwnerMoneyLimits(daily_loss_limit_fraction="0.04", trading_day_start_utc=start)
    assert OwnerMoneyLimits(daily_loss_limit_fraction="0.04", trading_day_start_utc="23:30").trading_day_start_utc


def test_unknown_money_limit_fields_are_refused() -> None:
    with pytest.raises(ValidationError):
        OwnerMoneyLimits(daily_loss_limit_fraction="0.04", force_sell="yes")
    with pytest.raises(ValidationError):
        CoinScope(rule="any_coin")
    with pytest.raises(ValidationError):
        CoinScope(max_scan_age_seconds=0)


def test_an_older_policy_document_reads_and_serialises_byte_for_byte_as_before() -> None:
    policy = _policy()
    assert policy.money_limits is None and policy.coin_scope is None
    document = policy.model_dump_json()
    assert "money_limits" not in document and "coin_scope" not in document
    assert OwnerPolicy.model_validate_json(document).model_dump_json() == document
    mandate = _mandate()
    assert mandate.coin_scope == "listed"
    assert "coin_scope" not in mandate.model_dump_json()


def test_a_policy_with_money_limits_and_the_coin_rule_round_trips() -> None:
    policy = _policy(money_limits=OwnerMoneyLimits(daily_loss_limit_fraction="0.04", trading_day_start_utc="06:00"),
                     coin_scope=CoinScope())
    again = OwnerPolicy.model_validate_json(policy.model_dump_json())
    assert again == policy
    assert again.money_limits.trading_day_start_utc == "06:00"
    assert again.coin_scope.rule == "scanner_checks" and again.coin_scope.quote_asset == "USD"
    assert again.coin_scope.max_scan_age_seconds == 7200
    assert again.coin_scope.when_unknown == "refuse_new_entries"


def test_a_mandate_cannot_carry_money_limits_or_a_day_start() -> None:
    for field in ("money_limits", "daily_loss_limit_fraction", "trading_day_start_utc", "coin_rule"):
        with pytest.raises(ValidationError):
            _mandate(**{field: "0.5"})


# --- the trading day (D138 item 6) ---------------------------------------------------------------------------------

@pytest.mark.parametrize(("at", "start", "day_start", "next_start"), [
    (datetime(2026, 10, 16, 9, 30, tzinfo=UTC), "00:00",
     datetime(2026, 10, 16, tzinfo=UTC), datetime(2026, 10, 17, tzinfo=UTC)),
    (datetime(2026, 10, 16, 0, 0, tzinfo=UTC), "00:00",
     datetime(2026, 10, 16, tzinfo=UTC), datetime(2026, 10, 17, tzinfo=UTC)),
    (datetime(2026, 10, 16, 23, 59, 59, 999999, tzinfo=UTC), "00:00",
     datetime(2026, 10, 16, tzinfo=UTC), datetime(2026, 10, 17, tzinfo=UTC)),
    (datetime(2026, 10, 16, 5, 0, tzinfo=UTC), "06:30",
     datetime(2026, 10, 15, 6, 30, tzinfo=UTC), datetime(2026, 10, 16, 6, 30, tzinfo=UTC)),
    (datetime(2026, 12, 31, 23, 0, tzinfo=UTC), "00:00",
     datetime(2026, 12, 31, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)),
])
def test_the_trading_day_holds_its_start_and_ends_at_the_next_start(at, start, day_start, next_start) -> None:
    day = rules.trading_day(at, start)
    assert (day.start, day.end) == (day_start, next_start)
    assert day.start <= at < day.end


def test_the_trading_day_needs_an_aware_time() -> None:
    with pytest.raises(ValueError):
        rules.trading_day(datetime(2026, 10, 16, 9), "00:00")


# --- the daily loss limit (D138 item 1) ----------------------------------------------------------------------------

@pytest.mark.parametrize(("start", "current", "reached"), [
    ("10000", "9600.01", False),
    ("10000", "9600", True),        # at the limit is the limit
    ("10000", "9599.99", True),
    ("10000", "12000", False),
    ("2500.50", "2400.48", True),   # 4% of 2500.50 is 100.02
    ("2500.50", "2400.49", False),
])
def test_the_loss_limit_is_reached_at_the_fraction_of_the_day_start_value(start, current, reached) -> None:
    assert rules.loss_limit_reached(day_start_value=D(start), current_value=D(current),
                                    fraction=D("0.04")) is reached


def test_the_loss_limit_amount_is_exact() -> None:
    assert rules.loss_limit_amount(D("10000"), D("0.03")) == D("300.00")
    assert rules.loss_limit_amount(D("9876.54321"), D("0.035")) == D("345.67901235")
    with pytest.raises(ValueError):
        rules.loss_limit_amount(D("0"), D("0.03"))


# --- entries counted as if filled (D138 item 2) --------------------------------------------------------------------

def test_an_entry_is_valued_at_the_highest_of_its_price_the_quote_and_the_mark() -> None:
    below = rules.entry_value("SOL", D("10"), order_price=D("150"), reference=D("201"), mark=D("200"))
    assert below.value == D("2010") and below.shortfall == D("10")
    above = rules.entry_value("SOL", D("10"), order_price=D("210"), reference=D("201"), mark=D("200"))
    assert above.value == D("2100") and above.shortfall == D("100")
    unmarked = rules.entry_value("SOL", D("2"), order_price=D("150"), reference=None, mark=None)
    assert unmarked.value == D("300") and unmarked.shortfall == D("0")
    with pytest.raises(ValueError):
        rules.entry_value("SOL", D("1"), order_price=None, reference=None, mark=None)


def test_entries_count_against_an_equity_that_has_already_paid_their_fees() -> None:
    proposed = rules.entry_value("SOL", D("20"), order_price=D("100"), reference=D("100"), mark=D("100"))
    common = {"inventory": D("0"), "held": {}, "entries": (), "coin_cap": D("0.2"), "gross_cap": D("0.8")}
    # 2,000 of 10,000 is exactly 20%, but a fill pays up to 16 in fees first.
    with pytest.raises(AuthorityDenied, match="single asset exposure"):
        rules.check_entry(equity=D("10000"), proposed=proposed, fee_rate=D("0.008"), **common)
    check = rules.check_entry(equity=D("10000"), proposed=proposed, fee_rate=D("0"), **common)
    assert check.coin_value == D("2000") and check.equity_floor == D("10000")


def test_resting_entries_on_the_same_coin_and_other_coins_add_up() -> None:
    pending = (rules.entry_value("SOL", D("5"), order_price=D("100"), reference=D("100"), mark=D("100")),
               rules.entry_value("BTC", D("0.1"), order_price=D("50000"), reference=D("50000"), mark=D("50000")))
    proposed = rules.entry_value("SOL", D("10"), order_price=D("100"), reference=D("100"), mark=D("100"))
    check = rules.check_entry(equity=D("10000"), inventory=D("1000"), held={"SOL": D("1000")}, entries=pending,
                              proposed=proposed, fee_rate=D("0"), coin_cap=D("0.25"), gross_cap=D("0.8"))
    assert check.coin_value == D("2500")              # 1,000 held + 500 resting + 1,000 new
    assert check.gross_value == D("7500")              # 1,000 held + 500 + 5,000 + 1,000
    with pytest.raises(AuthorityDenied, match="gross exposure"):
        rules.check_entry(equity=D("10000"), inventory=D("1000"), held={"SOL": D("1000")}, entries=pending,
                          proposed=proposed, fee_rate=D("0"), coin_cap=D("0.25"), gross_cap=D("0.7"))


def test_a_new_entry_may_not_push_another_coin_over_its_share_through_its_fees() -> None:
    # 2,000 of SOL is exactly 20% of 10,000; a BTC entry whose fees lower the floor would put SOL above it.
    btc = rules.entry_value("BTC", D("0.01"), order_price=D("50000"), reference=D("50000"), mark=D("50000"))
    common = {"equity": D("10000"), "inventory": D("2000"), "held": {"SOL": D("2000")}, "entries": (),
              "proposed": btc, "coin_cap": D("0.2"), "gross_cap": D("0.8")}
    with pytest.raises(AuthorityDenied, match="single asset exposure"):
        rules.check_entry(fee_rate=D("0.004"), **common)
    rules.check_entry(fee_rate=D("0"), **common)
    # A coin that is already above its share because its price rose does not block other coins (no sell-off).
    rules.check_entry(fee_rate=D("0.004"), **{**common, "held": {"SOL": D("2500")}, "inventory": D("2500")})


def test_no_equity_refuses_every_entry() -> None:
    proposed = rules.entry_value("SOL", D("1"), order_price=D("1"), reference=None, mark=None)
    with pytest.raises(AuthorityDenied, match="no equity"):
        rules.check_entry(equity=D("0"), inventory=D("0"), held={}, entries=(), proposed=proposed,
                          fee_rate=D("0"), coin_cap=D("1"), gross_cap=D("1"))


# --- D127: no riba products -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("symbol", [
    "PF_XBTUSD", "PI_ETHUSD", "FI_XBTUSD_261225", "BTC-PERP", "BTCPERP/USD", "BTC/USD:USD", "BTC-USD-SWAP",
    "BTC-USD-261225", "ETH.S/USD", "DOT.F/USD", "USD.M/EUR", "ETH2.S/ETH", "BTC/USD.HOLD", "btc/usd", "BTC/", "/USD",
    "BTCUSD", "",
])
def test_futures_perpetuals_and_staking_or_earn_variants_are_refused(symbol) -> None:
    with pytest.raises(AuthorityDenied, match="D127"):
        refuse_riba_symbol(symbol)


@pytest.mark.parametrize("symbol", ["BTC/USD", "ETH/USD", "SOL/USD", "XBT/USD", "1INCH/USD", "PAXG/USD"])
def test_spot_pairs_pass_the_product_check(symbol) -> None:
    refuse_riba_symbol(symbol)


@pytest.mark.parametrize(("kind", "ref", "payload", "flagged"), [
    ("deposit", "opening", {"asset": "USD", "amount": "10000"}, ()),
    ("deposit", "synthetic-capital", {"asset": "USD", "amount": "10000"}, ()),
    ("deposit", "kraken:staking-reward:L1", {"asset": "ETH", "amount": "0.01"}, ("reward", "staking")),
    ("deposit", "venue-interest-2026-10", {"asset": "USD", "amount": "1.20"}, ("interest",)),
    ("internal", "earn allocation", {"asset": "USD", "amount": "5"}, ("earn",)),
    ("deposit", "margin rollover", {"asset": "USD", "amount": "1"}, ("margin", "rollover")),
    ("deposit", "lending payout", {"asset": "USD", "amount": "1"}, ("lending",)),
    ("deposit", "ref", {"asset": "ETH.S", "amount": "1"}, ("staked_asset",)),
    ("withdraw", "funding fee", {"asset": "USD", "amount": "1"}, ("funding",)),
])
def test_interest_like_postings_are_flagged_with_their_reasons(kind, ref, payload, flagged) -> None:
    assert interest_posting_reasons(kind, ref, payload) == flagged


# --- agents cannot edit or raise -----------------------------------------------------------------------------------

def test_the_leader_may_narrow_but_never_raise_or_edit_owner_money_limits() -> None:
    for action in ("raise_money_limits", "edit_owner_policy"):
        with pytest.raises(AuthorityDenied):
            assert_action("leader", action, owner_halt=False)
    for role in ("trader", "engineer"):
        with pytest.raises(AuthorityDenied):
            assert_action(role, "edit_owner_policy", owner_halt=False)
    policy = _policy()
    mandate_within_owner(_mandate(max_single_asset_exposure_fraction="0.10"), policy)
    with pytest.raises(AuthorityDenied, match="single-asset"):
        mandate_within_owner(_mandate(max_single_asset_exposure_fraction="0.30"), policy)
    with pytest.raises(AuthorityDenied, match="gross"):
        mandate_within_owner(_mandate(max_gross_exposure_fraction="0.90"), policy)


def test_a_mandate_may_follow_the_coin_rule_only_when_the_owner_policy_has_one() -> None:
    with pytest.raises(AuthorityDenied, match="coin rule"):
        mandate_within_owner(_mandate(coin_scope="owner_rule"), _policy())
    mandate_within_owner(_mandate(coin_scope="owner_rule"), _policy(coin_scope=CoinScope()))


@pytest.mark.parametrize("path", [
    "src/trade_graph/kernel/money_limits.py", "src/trade_graph/kernel/riba.py",
    "src/trade_graph/contracts/money_limits.py", "src/trade_graph/application/money_limits.py",
    "src/trade_graph/adapters/persistence/money_limit_schema.py", "migrations/versions/0033_money_limits.py",
])
def test_the_money_limit_code_is_a_protected_path(path) -> None:
    assert path_is_protected(path)
