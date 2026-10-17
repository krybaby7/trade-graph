"""The paper scorecard (T42), checked by hand to the last digit on a synthetic paper ledger.

Every expected number is worked out in the comments with plain arithmetic, not by calling the code. The ledger,
prices, fills, fees, rates and AI calls are synthetic (``tests/scorecard_support.py``); no exchange, provider or
network is used. The sums are W2-A's (``kernel/trade_metrics.py``); AI spending follows W2-C's receipts rules.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from tests.scorecard_support import PORTFOLIO, START, Paper, golden_day

from trade_graph.application.scorecard import (
    GATES_STATEMENT,
    ScorecardUnavailable,
    compute_scorecard,
    format_scorecard_text,
)

D = Decimal
END = START + timedelta(hours=24)
GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "scorecard" / "golden_scorecard.json"


@pytest.fixture
def paper(tmp_path):
    paper = Paper(tmp_path / "state.sqlite", at=START - timedelta(hours=1))
    yield paper
    paper.close()


def _card(paper: Paper, **kwargs):
    return compute_scorecard(paper.database, portfolio_id=PORTFOLIO, end=kwargs.pop("end", END), **kwargs)


def test_the_golden_day_is_exact_to_the_last_digit(paper):
    calls = golden_day(paper)
    card = _card(paper)
    metrics = card.metrics
    assert (card.start, card.end, card.start_basis, card.currency) == (START, END, "opening_deposit", "USD")
    assert card.status == "complete" and card.provisional == ()
    # Trade 1, ETH, two partial buys and two partial sells: bought 3600 + 2400 = 6000, sold 4725 + 1570 = 6295,
    # fees 14.4 + 9.6 (maker 0.4%) + 37.8 + 12.56 (taker 0.8%) = 74.36; result 6295 - 6000 - 74.36 = 220.64, a win.
    # Trade 2, BTC: bought 1830 (fee 7.32), sold 1785 (fee 14.28): 1785 - 1830 - 21.6 = -66.6, a loss.
    first, second = metrics.round_trips
    assert (first.symbol, first.bought, first.sold, first.fees, first.result) == (
        "ETH/USD", D("6000"), D("6295"), D("74.36"), D("220.64"))
    assert len(first.fill_refs) == 4 and first.outcome == "win"
    assert (second.symbol, second.bought, second.sold, second.fees, second.result) == (
        "BTC/USD", D("1830"), D("1785"), D("21.6"), D("-66.6"))
    assert metrics.trades == 2 and metrics.wins == 1 and metrics.losses == 1 and metrics.flat_trades == 0
    assert metrics.win_rate == D("0.5") and metrics.average_win == D("220.64") and metrics.average_loss == D("-66.6")
    assert metrics.worst_losing_streak == 1
    # Fees paid on fills: 74.36 + 21.6 + 6.1 (the open ETH buy) = 102.06.
    assert metrics.fees_paid == D("102.06")
    # Cash: 10000 + 220.64 - 66.6 - (1525 + 6.1) = 8622.94. The 0.5 ETH still held is marked at 3200: 1600, so the
    # marked equity is 10222.94; valued as sold at the end with the 0.8% taker fee (12.8) it is 1587.2, and the
    # ending equity is 8622.94 + 1587.2 = 10210.14. Result 210.14 = 220.64 - 66.6 + (1587.2 - 1525 - 6.1).
    assert card.ending_equity_marked == D("10222.94") and card.exit_fees == D("12.8")
    assert metrics.starting_equity == D("10000") and metrics.ending_equity == D("10210.14")
    assert metrics.result == D("210.14") and metrics.result_fraction == D("0.021014")
    (held,) = metrics.open_positions
    assert (held.symbol, held.quantity, held.bought, held.fees) == ("ETH/USD", D("0.5"), D("1525"), D("6.1"))
    # The equity curve: 10000; 9985.6 (1h: 6385.6 cash + 1.2 x 3000); 9976 (3976 + 2 x 3000); 9876 (2 x 2950);
    # 10176; 10213.2 (8663.2 + 0.5 x 3100); 10238.2 (the peak, 0.5 x 3150 at 3h05); 10220.64; 10183.32
    # (8383.32 + 0.03 x 60000); 10183.32; 10153.32 (0.03 x 59000); 10154.04; 10197.94 (8622.94 + 0.5 x 3150);
    # 10072.94 (12h, 0.5 x 2900: the trough); the end valuation 10210.14. Worst drawdown 10238.2 - 10072.94 =
    # 165.26, and 165.26 / 10238.2 = 0.01614150925... = 0.0161415092 at ten places (the 9876 dip was 1.24%).
    drawdown = metrics.worst_drawdown
    assert (drawdown.amount, drawdown.fraction) == (D("165.26"), D("0.0161415092"))
    assert drawdown.peak_at == START + timedelta(hours=3, minutes=5)
    assert drawdown.trough_at == START + timedelta(hours=12)
    assert card.equity_points == 15 and card.unvalued_points == 0
    # Holding cash earns nothing (D127): 210.14 better than cash.
    assert metrics.versus_cash == D("210.14")
    # Holding BTC from the paper ledger's BTC/USD marks: 10000 / (60000 x 1.008) = 0.16534391534..., floored to
    # 0.16534391; principal 9920.6346, fee 79.3650768, cash left 0.0003232. Sold at 62000: 10251.32242, fee
    # 82.01057936; ending 10169.31216384, result 169.31216384. The paper result is 40.82783616 better.
    hold = metrics.benchmarks["hold_btc"]
    assert (hold.quantity, hold.buy_fee, hold.cash_left) == (D("0.16534391"), D("79.3650768"), D("0.0003232"))
    assert (hold.sell_fee, hold.ending_value, hold.result) == (D("82.01057936"), D("10169.31216384"),
                                                               D("169.31216384"))
    assert metrics.versus_benchmarks == {"hold_btc": D("40.82783616")}
    assert card.btc.cost_profile_ref == "kraken-spot-starting-tier@1" and card.btc.lot_size == D("0.00000001")
    # EUR reporting (A43): 10000 x 0.90 = 9000 at the start; 10210.14 x 0.92 = 9393.3288 at the end; result
    # 393.3288 in EUR, of which 10000 x (0.92 - 0.90) = 200 is the dollar's own move (holding USD cash), so the
    # paper result against USD cash is 9393.3288 - 9200 = 193.3288 (= 210.14 x 0.92). BTC's 169.31216384 x 0.92 =
    # 155.7671907328; the difference 40.82783616 x 0.92 = 37.5616092672.
    eur = card.eur
    assert (eur.rate_start, eur.rate_end, eur.stale) == (D("0.90"), D("0.92"), False)
    assert (eur.starting_equity, eur.ending_equity, eur.result) == (D("9000"), D("9393.3288"), D("393.3288"))
    assert (eur.currency_effect, eur.versus_cash) == (D("200"), D("193.3288"))
    assert (eur.hold_btc_result, eur.versus_hold_btc) == (D("155.7671907328"), D("37.5616092672"))
    # AI spending beside it, for the same day, from receipts (the calls before and after the day are left out):
    # research 0.004 + one search 0.005 actual; the leader's call charged at its maximum; the open call held at its
    # maximum (never zero); Jev 0.000042; the test call apart.
    spending = paper.spending
    ai = card.ai
    perplexity = ai.services["perplexity"]
    assert perplexity.actual == D("0.009") and perplexity.calls == 4
    assert perplexity.estimated == spending.held_usd(calls["conservative"])
    assert perplexity.unresolved == spending.held_usd(calls["uncertain"]) > 0
    assert ai.services["jev"].actual == D("0.000042") and ai.services["other"].calls == 0
    assert ai.synthetic_calls == 1
    assert ai.total.total == D("0.009042") + spending.held_usd(calls["conservative"]) \
        + spending.held_usd(calls["uncertain"])
    # D138 item 7: one day and two trades is too short for a keep-going reading, even though both benchmarks
    # are beaten.
    reading = card.owner_test_reading
    assert (reading.days, reading.trades, reading.long_enough) == (D("1"), 2, False)
    assert (reading.beats_cash, reading.beats_btc, reading.keep_going) == (True, True, None)


def test_the_golden_document_matches_its_fixture(paper):
    golden_day(paper)
    document = _card(paper).document()
    assert document == json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert document["metrics"]["result"] == "210.14" and document["metrics"]["fees_paid"] == "102.06"
    assert document["scorecard_sha256"] == _card(paper).document()["scorecard_sha256"]  # reruns reproduce it


def test_the_scorecard_states_the_three_separate_gates_and_that_paper_gains_never_pay_for_ai(paper):
    golden_day(paper)
    card = _card(paper)
    document = card.document()
    assert "Working software, economic evidence and permission to trade real money are three separate gates" \
        in GATES_STATEMENT
    gates = document["gates"]
    assert gates["statement"] == GATES_STATEMENT
    assert gates["working_software"]["judged_here"] is False
    assert gates["economic_evidence"]["judged_here"] is False
    assert gates["real_money"]["authorized"] is False
    assert document["simulated"] is True and document["mode"] == "paper"
    assert document["paper_result_pays_for_ai"] is False
    text = format_scorecard_text(card)
    assert GATES_STATEMENT in text
    assert "never pays for" in text and "simulated" in text
    assert "210.14" in text and "40.82783616" in text


def test_a_position_held_at_the_start_is_carried_in_at_the_start_price(paper):
    golden_day(paper)
    card = _card(paper, start=START + timedelta(hours=2))
    metrics = card.metrics
    # At 02:00 the account holds 2 ETH marked at 3100 and 3976 cash: 10176. The ETH is carried in as bought at
    # 3100 with no fee: 6200. Its trade: 6295 - 6200 - (37.8 + 12.56) = 44.64. Result 10210.14 - 10176 = 34.14.
    assert card.start_basis == "requested" and card.carried_in == ("ETH",)
    assert metrics.starting_equity == D("10176") and metrics.result == D("34.14")
    first = metrics.round_trips[0]
    assert (first.bought, first.fees, first.result) == (D("6200"), D("50.36"), D("44.64"))
    assert first.fill_refs[0] == "carried-in:ETH"
    assert card.document()["metrics"]["round_trips"][0]["fill_refs"][0] == "carried-in:ETH"
    # The BTC price at 02:00 is the 00:00 mark, two hours old: the BTC comparison is labelled provisional.
    assert card.status == "provisional"
    assert any("BTC" in reason and "start" in reason for reason in card.provisional)


def test_old_prices_make_the_scorecard_provisional_never_silently_complete(paper):
    golden_day(paper)
    card = _card(paper, end=START + timedelta(hours=23))
    # At 23:00 the newest ETH mark is from 12:00 (11 hours old) and the newest BTC mark from 07:00.
    assert card.status == "provisional"
    assert any("ETH" in reason and "end" in reason for reason in card.provisional)
    assert any("BTC" in reason and "end" in reason for reason in card.provisional)
    # The figures are still the ledger's: ending equity 8622.94 + 0.5 x 2900 x 0.992 = 10061.34.
    assert card.metrics.ending_equity == D("10061.34")


def test_money_added_or_taken_out_during_the_period_is_refused(paper):
    golden_day(paper)
    paper.at(START + timedelta(hours=26))
    paper.deposit("500", ref="top-up")
    with pytest.raises(ScorecardUnavailable, match="deposit or withdrawal"):
        _card(paper, end=START + timedelta(hours=27))
    # A period that ends before the deposit is fine.
    assert _card(paper).metrics.result == D("210.14")


def test_without_a_btc_price_the_comparison_is_unavailable_never_invented(paper):
    paper.at(START)
    paper.deposit("10000")
    paper.mark("ETH", "3000")
    paper.at(START + timedelta(hours=1))
    paper.fill("ETH/USD", "buy", "1", "3000")  # 3000, fee 12
    paper.at(START + timedelta(hours=2))
    paper.mark("ETH", "3100")
    paper.fill("ETH/USD", "sell", "1", "3100", liquidity="taker")  # 3100, fee 24.8
    card = _card(paper, end=START + timedelta(hours=3))
    # 3100 - 3000 - 36.8 = 63.2.
    assert card.metrics.result == D("63.2") and card.metrics.benchmarks == {}
    assert card.btc.available is False and "BTC" in card.btc.reason
    assert card.owner_test_reading.beats_btc is None and card.owner_test_reading.keep_going is None
    assert card.document()["benchmarks"]["hold_btc"]["available"] is False


def test_a_period_without_trades_has_no_invented_averages(paper):
    paper.at(START)
    paper.deposit("10000")
    paper.mark("BTC", "60000")
    paper.at(START + timedelta(hours=6))
    paper.mark("BTC", "60000")
    card = _card(paper, end=START + timedelta(hours=6))
    metrics = card.metrics
    assert metrics.trades == 0 and metrics.result == 0 and metrics.win_rate is None
    assert metrics.average_win is None and metrics.average_loss is None and metrics.fees_paid == 0
    # Holding BTC at a flat price still pays both fees: it loses to cash, and cash (0) is not beaten either.
    assert card.owner_test_reading.beats_cash is False and card.owner_test_reading.beats_btc is True
    assert card.eur.available is False and "rate" in card.eur.reason  # no reference rate recorded at all


def test_fees_charged_in_the_coin_reconcile_with_the_ledger(paper):
    paper.at(START)
    paper.deposit("1000")
    paper.mark("BTC", "100")
    paper.at(START + timedelta(hours=1))
    # Buy 1 at 100 with a 0.01 BTC fee: 0.99 held, cash 900. Sell 0.98 at 110 with a 0.01 BTC fee: cash 1007.8.
    paper.fill("BTC/USD", "buy", "1", "100", fee="0.01", fee_asset="BTC")
    paper.at(START + timedelta(hours=2))
    paper.mark("BTC", "110")
    paper.fill("BTC/USD", "sell", "0.98", "110", fee="0.01", fee_asset="BTC", liquidity="taker")
    card = _card(paper, end=START + timedelta(hours=3))
    (trip,) = card.metrics.round_trips
    # Bought 99 + fee 1 (0.01 x 100); sold 108.9 less fee 1.1 (0.01 x 110): 7.8, the cash the trade made.
    assert (trip.bought, trip.sold, trip.fees, trip.result) == (D("99"), D("108.9"), D("2.1"), D("7.8"))
    assert card.metrics.result == D("7.8") == paper.ledger.native_equity(PORTFOLIO, "USD").equity - D("1000")


def test_a_reading_of_d138_item_7_needs_four_weeks_and_thirty_trades(paper):
    paper.at(START)
    paper.fx("0.90")
    paper.deposit("10000")
    paper.mark("BTC", "60000")
    paper.mark("ETH", "3000")
    for day in range(30):
        # Each day: buy 1 ETH at 3000 (fee 12), sell at 3040 (fee 24.32): +3.68 a day, 30 small wins.
        paper.at(START + timedelta(days=day, hours=1))
        paper.mark("ETH", "3000")
        paper.fill("ETH/USD", "buy", "1", "3000")
        paper.at(START + timedelta(days=day, hours=2))
        paper.mark("ETH", "3040")
        paper.fill("ETH/USD", "sell", "1", "3040", liquidity="taker")
    end = START + timedelta(days=30)
    paper.at(end)
    paper.mark("BTC", "66000")
    paper.mark("ETH", "3040")
    card = _card(paper, end=end)
    reading = card.owner_test_reading
    assert (reading.days, reading.trades, reading.long_enough) == (D("30"), 30, True)
    assert card.metrics.result == D("110.4")  # 30 x 3.68
    # BTC rose 10%: 10000 / 60480 -> 0.16534391 BTC, sold at 66000 with the fee: well above +110.4.
    assert reading.beats_cash is True and reading.beats_btc is False and reading.keep_going is False


def test_unscorable_requests_say_why(paper):
    paper.at(START)
    with pytest.raises(ScorecardUnavailable, match="no paper portfolio"):
        compute_scorecard(paper.database, portfolio_id="missing", end=END)
    with pytest.raises(ScorecardUnavailable, match="opening deposit"):
        _card(paper)
    paper.deposit("10000")
    paper.mark("BTC", "60000")
    with pytest.raises(ScorecardUnavailable, match="before the opening deposit"):
        _card(paper, start=START - timedelta(minutes=1))
    with pytest.raises(ScorecardUnavailable, match="ends after it starts"):
        _card(paper, start=START + timedelta(hours=2), end=START + timedelta(hours=1))
    paper.at(START + timedelta(hours=1))
    paper.fill("SOL/USD", "buy", "1", "100")  # no SOL price was ever recorded
    with pytest.raises(ScorecardUnavailable, match="no SOL price"):
        _card(paper, start=START + timedelta(hours=2), end=START + timedelta(hours=3))


def test_cash_in_another_currency_is_not_scored_rather_than_guessed(paper):
    paper.at(START)
    paper.deposit("10000")
    paper.deposit("100", asset="EUR", ref="euros")
    with pytest.raises(ScorecardUnavailable, match="EUR"):
        _card(paper, end=START + timedelta(hours=1))


def test_the_scorecard_reads_through_a_read_only_connection_and_writes_nothing(paper):
    golden_day(paper)
    path = paper.database.path
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        card = compute_scorecard(connection, portfolio_id=PORTFOLIO, end=END)
    finally:
        connection.close()
    assert card.metrics.result == D("210.14")
    assert card.document() == _card(paper).document()


def test_ledger_reference_rates_are_read_as_the_books_use_them(paper):
    paper.at(START)
    assert paper.ledger.fx_rate("USD", "EUR", "2026-11-02T00:00:00.000000Z") == (None, True)
    paper.fx("0.91", stale=True)
    assert paper.ledger.fx_rate("USD", "EUR", "2026-11-02T00:00:00.000000Z") == (D("0.91"), True)
    assert paper.ledger.fx_rate("USD", "USD", "2026-11-02T00:00:00.000000Z") == (D("1"), False)
