"""A synthetic paper ledger for the scorecard tests (T42): real ``Ledger`` records, made-up prices and fills.

Every price, fill, fee and rate here is invented. Fills, marks and reference rates are written through the real
``Ledger`` exactly as the paper broker, the public paper feed and the reference-rate refresh write them, so the
scorecard is tested against the records the paper installation really keeps. Nothing is fetched or sent.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tests.spending_support import Spending
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import FillRecord
from trade_graph.domain.clock import FrozenClock

START = datetime(2026, 11, 2, tzinfo=UTC)
PORTFOLIO = "paper-scorecard-synthetic"
MAKER = Decimal("0.004")
TAKER = Decimal("0.008")


class Paper:
    """One paper portfolio in a fresh state database, on a frozen clock that the test moves forward."""

    def __init__(self, path, *, at: datetime = START, reporting: str = "EUR") -> None:
        self.clock = FrozenClock(at)
        self.database = Database(path)
        self.ledger = Ledger(self.database, self.clock)
        self.portfolio = self.ledger.create_portfolio(reporting_currency=reporting, portfolio_id=PORTFOLIO)
        self.spending = Spending(self.database, self.clock)
        self._ids = itertools.count(1)  # per account, so trade ids (and the golden document) never depend on order

    def close(self) -> None:
        self.database.close()

    def at(self, moment: datetime) -> Paper:
        seconds = (moment - self.clock.now()).total_seconds()
        if seconds < 0:
            raise ValueError("the test clock only moves forward")
        self.clock.advance(seconds)
        return self

    def hours(self, hours: float) -> datetime:
        return START + timedelta(hours=hours)

    def deposit(self, amount: str, asset: str = "USD", ref: str = "opening") -> None:
        self.ledger.deposit(self.portfolio, asset, Decimal(amount), ref)

    def withdraw(self, amount: str, asset: str = "USD", ref: str = "withdrawal") -> None:
        self.ledger.withdraw(self.portfolio, asset, Decimal(amount), ref)

    def mark(self, asset: str, price: str, *, quote: str = "USD", stale: bool = False) -> None:
        self.ledger.observe_mark(self.portfolio, asset, Decimal(price), quote,
                                 source="synthetic:paper_reference:receipt_time", stale=stale)

    def fx(self, rate: str, *, stale: bool = False) -> None:
        self.ledger.observe_fx(base="USD", quote="EUR", rate=Decimal(rate), source="synthetic-reference",
                               kind="reference", stale=stale)

    def fill(self, symbol: str, side: str, quantity: str, price: str, *, fee: str | None = None,
             fee_asset: str | None = None, liquidity: str = "maker", **updates) -> FillRecord:
        base, quote = symbol.split("/")
        if fee is None:
            rate = MAKER if liquidity == "maker" else TAKER
            fee = str(Decimal(quantity) * Decimal(price) * rate)
        record = FillRecord(venue="paper", account_id="paper", trade_id=f"synthetic-{next(self._ids)}", intent_id=None,
                            symbol=symbol, side=side, quantity=quantity, price=price, fee_amount=fee,
                            fee_asset=fee_asset or quote, liquidity=liquidity, filled_at_utc=self.clock.now(),
                            **updates)
        self.ledger.apply_fill(self.portfolio, record, base_asset=base, quote_asset=quote)
        return record


def golden_day(paper: Paper) -> dict[str, str]:
    """The hand-checked day of ``tests/integration/test_scorecard.py`` (see the arithmetic there).

    USD10,000 at 00:00; ETH bought in two partial maker fills and sold in two partial taker fills (a win); BTC
    bought and stopped out (a loss); ETH bought again and still held at the end, 24 hours later. Reference rates
    0.90 at the start and 0.92 from 20:00. AI calls at 04:00, one before the start and one after the end.
    """
    paper.at(START - timedelta(hours=1))
    paper.spending.call("research")  # before the period: not counted
    paper.at(START)
    paper.fx("0.90")
    paper.deposit("10000")
    paper.mark("BTC", "60000")
    paper.mark("ETH", "3000")
    paper.at(paper.hours(1))
    paper.fill("ETH/USD", "buy", "1.2", "3000")  # principal 3600, maker fee 14.4
    paper.at(paper.hours(1 + 5 / 60))
    paper.fill("ETH/USD", "buy", "0.8", "3000")  # principal 2400, maker fee 9.6
    paper.at(paper.hours(1.5))
    paper.mark("ETH", "2950")
    paper.at(paper.hours(2))
    paper.mark("ETH", "3100")
    paper.at(paper.hours(3))
    paper.fill("ETH/USD", "sell", "1.5", "3150", liquidity="taker")  # principal 4725, taker fee 37.8
    paper.at(paper.hours(3 + 5 / 60))
    paper.mark("ETH", "3150")
    paper.at(paper.hours(3 + 10 / 60))
    paper.fill("ETH/USD", "sell", "0.5", "3140", liquidity="taker")  # principal 1570, taker fee 12.56
    paper.at(paper.hours(4))
    paper.spending.call("research")
    paper.spending.call("research", search=True)
    calls = {"conservative": paper.spending.call("leader", "conservative"),
             "uncertain": paper.spending.call("research", "uncertain")}
    paper.spending.call(service="jev")
    paper.spending.call("research", synthetic=True)
    paper.at(paper.hours(5))
    paper.fill("BTC/USD", "buy", "0.03", "61000")  # principal 1830, maker fee 7.32
    paper.at(paper.hours(6))
    paper.mark("BTC", "60000")
    paper.at(paper.hours(7))
    paper.mark("BTC", "59000")
    paper.at(paper.hours(8))
    paper.fill("BTC/USD", "sell", "0.03", "59500", liquidity="taker")  # principal 1785, taker fee 14.28
    paper.at(paper.hours(10))
    paper.fill("ETH/USD", "buy", "0.5", "3050")  # principal 1525, maker fee 6.1
    paper.at(paper.hours(12))
    paper.mark("ETH", "2900")
    paper.at(paper.hours(20))
    paper.fx("0.92")
    paper.at(paper.hours(24))
    paper.mark("ETH", "3200")
    paper.mark("BTC", "62000")
    paper.at(paper.hours(25))
    paper.spending.call("research")  # after the period: not counted
    return calls
