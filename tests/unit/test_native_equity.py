"""Native-currency equity for exposure checks (D114); reporting FX stays a reporting concern."""

from datetime import UTC, datetime
from decimal import Decimal

from trade_graph.contracts.models import FillRecord
from trade_graph.kernel.books import Books, FxRate, Mark, apply_fill, deposit, mark_equity, native_equity, withdraw

D = Decimal
AT = "2026-10-10T09:00:00.000000Z"
FRIDAY = "2026-10-09T00:00:00.000000Z"
STALE_USD_EUR = FxRate("USD", "EUR", D("0.86"), FRIDAY, True, "fixture", "usd-eur", AT)


def _holding_eth() -> Books:
    books = Books()
    deposit(books, "USD", D("10000"), FRIDAY, "seed")
    fill = FillRecord.model_validate({
        "venue": "paper", "account_id": "paper", "trade_id": "eth", "intent_id": None, "symbol": "ETH/USD",
        "side": "buy", "quantity": "40", "price": "100", "fee_amount": "0", "fee_asset": "USD",
        "liquidity": "taker", "filled_at_utc": datetime(2026, 10, 9, tzinfo=UTC), "heuristic": False,
    })
    apply_fill(books, fill, base_asset="ETH", quote_asset="USD", lot_id="eth", at=FRIDAY)
    return books


def test_same_currency_equity_ignores_stale_reporting_fx() -> None:
    books = _holding_eth()
    marks = [Mark("ETH", D("110"), "USD", AT, False, "fixture")]
    view = native_equity(books, currency="USD", marks=marks, rates=[STALE_USD_EUR], at=AT)
    assert (view.equity, view.cash, view.inventory) == (D("10400"), D("6000"), D("4400"))
    assert view.stale is False
    assert view.missing == ()
    assert mark_equity(books, reporting="EUR", marks=marks, rates=[STALE_USD_EUR], at=AT).provisional is True


def test_missing_mark_is_unknown_not_zero() -> None:
    view = native_equity(_holding_eth(), currency="USD", marks=[], rates=[], at=AT)
    assert view.equity is None
    assert view.inventory is None
    assert view.missing == ("ETH",)


def test_flagged_stale_mark_marks_native_equity_stale() -> None:
    marks = [Mark("ETH", D("110"), "USD", AT, True, "fixture")]
    view = native_equity(_holding_eth(), currency="USD", marks=marks, rates=[], at=AT)
    assert view.equity == D("10400")
    assert view.stale is True


def test_other_currency_cash_needs_an_identified_conversion() -> None:
    books = Books()
    deposit(books, "USD", D("100"), FRIDAY, "seed")
    deposit(books, "EUR", D("10"), FRIDAY, "eur")
    missing = native_equity(books, currency="USD", marks=[], rates=[STALE_USD_EUR], at=AT)
    assert missing.equity is None
    assert missing.missing == ("EUR",)
    stale_eur_usd = FxRate("EUR", "USD", D("1.16"), FRIDAY, True, "fixture", "eur-usd", AT)
    view = native_equity(books, currency="USD", marks=[], rates=[stale_eur_usd], at=AT)
    assert view.equity == D("111.60")
    assert view.stale is True


def test_zero_other_currency_balance_needs_no_fx() -> None:
    books = Books()
    deposit(books, "USD", D("100"), FRIDAY, "seed")
    deposit(books, "EUR", D("10"), FRIDAY, "eur")
    withdraw(books, "EUR", D("10"), FRIDAY, "eur-out")
    view = native_equity(books, currency="USD", marks=[], rates=[], at=AT)
    assert view.equity == D("100")
    assert view.missing == ()
    assert view.stale is False
