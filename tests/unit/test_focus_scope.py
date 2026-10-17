"""Which focus coins the owner's coin list covers (T37; D127, D138 item 3): a listed mandate names its coins, the
owner's coin rule covers spot pairs in its quote currency, and riba products never count. Synthetic records only."""

from __future__ import annotations

from tests.money_limit_support import open_account, synthetic_policy

from trade_graph.application.focus_scope import focus_scope_refusals
from trade_graph.contracts.money_limits import CoinScope


def test_a_listed_mandate_covers_only_its_coins(tmp_path) -> None:
    account = open_account(tmp_path)  # listed: SOL/USD and BTC/USD
    refusals = focus_scope_refusals(account.execution.authority, account.portfolio,
                                    ("SOL/USD", "BTC/USD", "ADA/USD", "BTCPERP/USD"))
    assert set(refusals) == {"ADA/USD", "BTCPERP/USD"}
    assert "outside the owner's coin list" in refusals["ADA/USD"]
    assert "riba" in refusals["BTCPERP/USD"]  # a perpetual never counts, listed or not (D127)


def test_the_coin_rule_covers_spot_pairs_in_its_quote_currency(tmp_path) -> None:
    account = open_account(tmp_path, scope=CoinScope(), mandate_scope="owner_rule")
    refusals = focus_scope_refusals(account.execution.authority, account.portfolio,
                                    ("SOL/USD", "ADA/USD", "ADA/EUR", "SOLPERP/USD"))
    assert set(refusals) == {"ADA/EUR", "SOLPERP/USD"}


def test_without_an_owner_policy_or_mandate_nothing_is_covered(tmp_path) -> None:
    account = open_account(tmp_path)
    assert set(focus_scope_refusals(account.execution.authority, "no-such-portfolio", ("SOL/USD",))) == {"SOL/USD"}
    account.execution.authority.install_policy(synthetic_policy("synthetic-2"), role="owner")
    assert focus_scope_refusals(account.execution.authority, account.portfolio, ()) == {}
