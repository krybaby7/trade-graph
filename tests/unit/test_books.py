from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trade_graph.contracts.models import FillRecord
from trade_graph.domain.money import Money
from trade_graph.kernel.books import (
    Books,
    Expense,
    FxRate,
    Mark,
    add_expense,
    apply_fill,
    deposit,
    internal_transfer,
    mark_equity,
    performance,
    withdraw,
)


def _ts(minute: int) -> str:
    value = datetime(2026, 1, 1, 0, minute, tzinfo=UTC)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _fill(**kwargs) -> FillRecord:
    base = dict(
        venue="sim",
        account_id="paper",
        trade_id="t",
        intent_id=None,
        symbol="TEST/EUR",
        side="buy",
        quantity="1",
        price="40",
        fee_amount="0.40",
        fee_asset="EUR",
        liquidity="taker",
        filled_at_utc=datetime(2026, 1, 1, tzinfo=UTC),
        heuristic=False,
        reference_mid="42",
    )
    base.update(kwargs)
    return FillRecord.model_validate(base)


def test_eur100_fixture_and_external_expense() -> None:
    books = Books()
    deposit(books, "EUR", Decimal("100"), _ts(0), "open")
    apply_fill(
        books,
        _fill(),
        base_asset="TEST",
        quote_asset="EUR",
        lot_id="lot1",
        at=_ts(1),
    )
    assert books.cash_amount("EUR") == Decimal("59.60")
    marks = [Mark("TEST", Decimal("44"), "EUR", _ts(2), False, "fixture")]
    view = mark_equity(books, reporting="EUR", marks=marks, rates=[], at=_ts(2))
    assert view.provisional is False
    assert view.equity == Decimal("103.60")
    assert view.unrealized == Decimal("3.60")
    apply_fill(
        books,
        _fill(trade_id="sell", side="sell", price="44", fee_amount="0.44", reference_mid="44"),
        base_asset="TEST",
        quote_asset="EUR",
        lot_id="unused",
        at=_ts(3),
    )
    assert books.cash_amount("EUR") == Decimal("103.16")
    end = mark_equity(books, reporting="EUR", marks=marks, rates=[], at=_ts(3))
    start = mark_equity(books, reporting="EUR", marks=[], rates=[], at=_ts(0))
    # Rebuild start from a copy that only has the deposit.
    start_books = Books()
    deposit(start_books, "EUR", Decimal("100"), _ts(0), "open")
    start = mark_equity(start_books, reporting="EUR", marks=[], rates=[], at=_ts(0))
    result = performance(
        books,
        reporting="EUR",
        marks=marks,
        rates=[],
        start=_ts(0),
        end=_ts(3),
        start_view=start,
        end_view=end,
    )
    assert result.trading_pnl == Decimal("3.16")
    add_expense(
        books,
        Expense("ai", Decimal("1"), "USD", Decimal("0.90"), "EUR", False, "real", "accrued", _ts(4)),
    )
    end2 = mark_equity(books, reporting="EUR", marks=marks, rates=[], at=_ts(4))
    after = performance(
        books,
        reporting="EUR",
        marks=marks,
        rates=[],
        start=_ts(0),
        end=_ts(4),
        start_view=start,
        end_view=end2,
    )
    assert after.trading_pnl == Decimal("3.16")
    assert after.economic_pnl == Decimal("2.26")
    assert end2.equity == Decimal("103.16")


def test_embedded_expense_matches_external_economic_result() -> None:
    def _run(embedded: bool) -> Decimal:
        books = Books()
        deposit(books, "EUR", Decimal("100"), _ts(0), "open")
        apply_fill(books, _fill(trade_id="b"), base_asset="TEST", quote_asset="EUR", lot_id="l", at=_ts(1))
        apply_fill(
            books,
            _fill(trade_id="s", side="sell", price="44", fee_amount="0.44"),
            base_asset="TEST",
            quote_asset="EUR",
            lot_id="x",
            at=_ts(2),
        )
        add_expense(
            books,
            Expense("ai", Decimal("0.90"), "EUR", Decimal("0.90"), "EUR", embedded, "real", "accrued", _ts(3)),
        )
        start_books = Books()
        deposit(start_books, "EUR", Decimal("100"), _ts(0), "open")
        start = mark_equity(start_books, reporting="EUR", marks=[], rates=[], at=_ts(0))
        end = mark_equity(books, reporting="EUR", marks=[], rates=[], at=_ts(3))
        result = performance(
            books,
            reporting="EUR",
            marks=[],
            rates=[],
            start=_ts(0),
            end=_ts(3),
            start_view=start,
            end_view=end,
        )
        assert result.economic_pnl == Decimal("2.26")
        return result.trading_pnl

    assert _run(False) == Decimal("3.16")
    assert _run(True) == Decimal("3.16")


def test_deposit_does_not_count_as_profit_and_internal_transfer_is_not_flow() -> None:
    books = Books()
    deposit(books, "EUR", Decimal("100"), _ts(0), "open")
    apply_fill(books, _fill(trade_id="b"), base_asset="TEST", quote_asset="EUR", lot_id="l", at=_ts(1))
    apply_fill(
        books,
        _fill(trade_id="s", side="sell", price="44", fee_amount="0.44"),
        base_asset="TEST",
        quote_asset="EUR",
        lot_id="x",
        at=_ts(2),
    )
    deposit(books, "EUR", Decimal("50"), _ts(3), "topup")
    internal_transfer(books, "EUR", Decimal("10"), _ts(4), "move")
    start_books = Books()
    deposit(start_books, "EUR", Decimal("100"), _ts(0), "open")
    start = mark_equity(start_books, reporting="EUR", marks=[], rates=[], at=_ts(0))
    end = mark_equity(books, reporting="EUR", marks=[], rates=[], at=_ts(4))
    result = performance(
        books,
        reporting="EUR",
        marks=[],
        rates=[],
        start=_ts(0),
        end=_ts(4),
        start_view=start,
        end_view=end,
    )
    assert end.equity == Decimal("153.16")
    assert result.external_flow == Decimal("50")
    assert result.trading_pnl == Decimal("3.16")
    withdraw(books, "EUR", Decimal("50"), _ts(5), "out")
    end2 = mark_equity(books, reporting="EUR", marks=[], rates=[], at=_ts(5))
    result2 = performance(
        books,
        reporting="EUR",
        marks=[],
        rates=[],
        start=_ts(0),
        end=_ts(5),
        start_view=start,
        end_view=end2,
    )
    assert result2.external_flow == Decimal("0")
    assert result2.trading_pnl == Decimal("3.16")


def test_partial_lots_base_fee_and_rebate() -> None:
    books = Books()
    deposit(books, "EUR", Decimal("200"), _ts(0), "open")
    apply_fill(
        books,
        _fill(trade_id="a", quantity="0.4", price="40", fee_amount="0.16"),
        base_asset="TEST",
        quote_asset="EUR",
        lot_id="a",
        at=_ts(1),
    )
    apply_fill(
        books,
        _fill(trade_id="b", quantity="0.6", price="50", fee_amount="0.30"),
        base_asset="TEST",
        quote_asset="EUR",
        lot_id="b",
        at=_ts(2),
    )
    apply_fill(
        books,
        _fill(trade_id="c", side="sell", quantity="0.5", price="60", fee_amount="0"),
        base_asset="TEST",
        quote_asset="EUR",
        lot_id="c",
        at=_ts(3),
    )
    first = next(lot for lot in books.lots if lot.lot_id == "a")
    assert first.open_quantity() == Decimal("0")
    second = next(lot for lot in books.lots if lot.lot_id == "b")
    assert second.open_quantity() == Decimal("0.5")
    base_fee = Books()
    deposit(base_fee, "EUR", Decimal("100"), _ts(0), "open")
    apply_fill(
        base_fee,
        _fill(trade_id="fee-base", quantity="1", price="40", fee_amount="0.01", fee_asset="TEST"),
        base_asset="TEST",
        quote_asset="EUR",
        lot_id="fb",
        at=_ts(1),
    )
    lot = base_fee.lots[0]
    assert lot.quantity_original == Decimal("0.99")
    assert lot.cost_original == Decimal("40")
    rebate = Books()
    deposit(rebate, "EUR", Decimal("100"), _ts(0), "open")
    apply_fill(
        rebate,
        _fill(trade_id="reb", fee_amount="-0.10"),
        base_asset="TEST",
        quote_asset="EUR",
        lot_id="rb",
        at=_ts(1),
    )
    assert rebate.cash_amount("EUR") == Decimal("60.10")
    assert rebate.lots[0].cost_original == Decimal("39.90")


def test_third_asset_fee_is_not_charged_twice() -> None:
    books = Books()
    deposit(books, "USD", Decimal("100"), _ts(0), "open")
    apply_fill(
        books,
        _fill(
            trade_id="fee-buy",
            symbol="FEE/USD",
            quantity="10",
            price="0.5",
            fee_amount="0",
            fee_asset="USD",
        ),
        base_asset="FEE",
        quote_asset="USD",
        lot_id="fee",
        at=_ts(1),
    )
    apply_fill(
        books,
        _fill(
            trade_id="test-buy",
            symbol="TEST/USD",
            quantity="2",
            price="40",
            fee_amount="1",
            fee_asset="FEE",
            fee_identified_rate="0.6",
        ),
        base_asset="TEST",
        quote_asset="USD",
        lot_id="test",
        at=_ts(2),
    )
    assert books.cash_amount("USD") == Decimal("15")
    marks = [
        Mark("FEE", Decimal("0.5"), "USD", _ts(2), False, "m"),
        Mark("TEST", Decimal("40"), "USD", _ts(2), False, "m"),
    ]
    view = mark_equity(books, reporting="USD", marks=marks, rates=[], at=_ts(2))
    assert view.equity == Decimal("99.5")


def test_usd_cash_fx_is_not_strategy_alpha() -> None:
    books = Books()
    deposit(books, "USD", Decimal("10000"), _ts(0), "open")
    rates_a = [FxRate("USD", "EUR", Decimal("0.90"), _ts(1), False, "fixture", "r1")]
    first = mark_equity(books, reporting="EUR", marks=[], rates=rates_a, at=_ts(1))
    assert first.equity == Decimal("9000")
    assert first.baseline == Decimal("9000")
    assert first.equity - first.baseline == Decimal("0")
    rates_b = rates_a + [FxRate("USD", "EUR", Decimal("0.91"), _ts(2), False, "fixture", "r2")]
    second = mark_equity(books, reporting="EUR", marks=[], rates=rates_b, at=_ts(2))
    assert second.equity == Decimal("9100")
    assert second.baseline == Decimal("9100")
    start = first
    result = performance(
        books,
        reporting="EUR",
        marks=[],
        rates=rates_b,
        start=_ts(1),
        end=_ts(2),
        start_view=start,
        end_view=second,
    )
    assert result.trading_pnl == Decimal("100")
    assert result.alpha == Decimal("0")


def test_stale_fx_is_provisional_and_historical_expense_is_kept() -> None:
    books = Books()
    deposit(books, "USD", Decimal("10"), _ts(0), "open")
    add_expense(
        books,
        Expense("ai", Decimal("1"), "USD", Decimal("0.90"), "EUR", False, "real", "accrued", _ts(1)),
    )
    rates = [FxRate("USD", "EUR", Decimal("0.90"), _ts(1), True, "fixture", "r")]
    view = mark_equity(books, reporting="EUR", marks=[], rates=rates, at=_ts(1))
    assert view.provisional is True
    assert view.stale is True
    assert books.expenses[0].reporting_amount == Decimal("0.90")


def test_float_money_rejected() -> None:
    with pytest.raises(ValueError):
        Money(amount=1.2, currency="EUR")


def test_slippage_is_not_a_second_pnl_deduction() -> None:
    books = Books()
    deposit(books, "EUR", Decimal("100"), _ts(0), "open")
    fill = _fill(reference_mid="39")
    apply_fill(books, fill, base_asset="TEST", quote_asset="EUR", lot_id="l", at=_ts(1))
    slippage = (fill.price - fill.reference_mid) * fill.quantity
    assert slippage == Decimal("1")
    marks = [Mark("TEST", Decimal("40"), "EUR", _ts(1), False, "m")]
    view = mark_equity(books, reporting="EUR", marks=marks, rates=[], at=_ts(1))
    assert view.equity == Decimal("99.60")
