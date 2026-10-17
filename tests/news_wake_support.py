"""Helpers for the T45 news wake-up tests: synthetic headlines through the real T44 poll, scripted Jev answers.

Every headline is invented and labelled ``synthetic_fixture``; every Jev answer is scripted and receipted as
synthetic. No network, key, paid call, model or real order. The paper account is T35's synthetic day-plan account
(USD10,000, SOL near 100), so holding a coin is a real paper fill.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from email.utils import format_datetime
from xml.sax.saxutils import escape

from trade_graph.adapters.decisions.jev import JEV_PRICE_CARD, JevAdapter, ScriptedJevHttp
from trade_graph.adapters.news.feeds import FetchResult
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.jev_checks import JevBudgetBinding, JevCheckRunner
from trade_graph.application.news_shadow import NewsAlarmShadow
from trade_graph.application.news_store import NewsStore
from trade_graph.application.news_watchlist import watch_list
from trade_graph.contracts.news import RECOMMENDED_FEEDS, NewsAlarmSettings

KRAKEN_BLOG = next(feed for feed in RECOMMENDED_FEEDS if feed.feed_id == "kraken-blog")
SOL_DELISTING = "https://blog.kraken.com/product/asset-listings/synthetic-sol-delisting"
SOL_DELISTING_TITLE = "Kraken will delist SOL/USD on 30 October (synthetic)"
UNRELATED = "https://blog.kraken.com/news/synthetic-office-opening"
UNRELATED_TITLE = "Kraken opens a new office in a synthetic city (synthetic)"


class SyntheticFeed:
    """One synthetic RSS feed (the Kraken blog's address, invented items); every poll returns all items so far."""

    evidence = "synthetic_fixture"

    def __init__(self) -> None:
        self.items: list[tuple[str, str, str]] = []

    def add(self, title: str, link: str, published) -> None:
        self.items.append((title, link, format_datetime(published)))

    def fetch(self, feed, *, etag, last_modified) -> FetchResult:
        entries = "".join(f"<item><title>{escape(title)}</title><link>{escape(link)}</link><pubDate>{when}</pubDate>"
                          "</item>" for title, link, when in self.items)
        body = (f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Synthetic</title>'
                f"<link>https://blog.kraken.com</link><description>Synthetic test feed</description>{entries}"
                "</channel></rss>").encode()
        return FetchResult(outcome="fetched", http_status=200, body=body, etag=None, last_modified=None,
                           evidence="synthetic_fixture", error_category=None)


class NewsDesk:
    """T44's shadow poll writing into the account's own state database, with scripted Jev answers by link."""

    def __init__(self, account, *, portfolio_id: str | None = None) -> None:
        self.account = account
        database, clock = account.database, account.clock
        budget = BudgetGateway(database, clock)
        budget.seed_card(JEV_PRICE_CARD)
        if database.execute("SELECT 1 FROM deployment_budget WHERE deployment_id = 'deployment'").fetchone() is None:
            amount = Decimal("5")
            budget.configure(deployment_id="deployment", currency="EUR", total=amount, period=amount,
                             priority_reserve=Decimal("0"), daily=amount, root=amount, roles={})
        self.feed = SyntheticFeed()
        self.script = {"schema_version": 1, "evidence": "synthetic_fixture", "model": "jev-1.13.0",
                       "input_tokens": 420, "by_link": {}}
        self.transport = ScriptedJevHttp.from_script(self.script)
        store = NewsStore(database, clock)
        binding = JevBudgetBinding(deployment_id="deployment", price_card_id=JEV_PRICE_CARD.price_card_id,
                                   fx_rate=Decimal("0.9"), fx_buffer=Decimal("1.02"), synthetic=True)
        checks = JevCheckRunner(database, clock, budget, JevAdapter(self.transport), binding, store)
        portfolio = portfolio_id or account.portfolio
        self.shadow = NewsAlarmShadow(store=store, fetcher=self.feed, settings=NewsAlarmSettings(feeds=(KRAKEN_BLOG,)),
                                      clock=clock, checks=checks, watch=lambda: watch_list(database, clock, portfolio))

    def headline(self, title: str, link: str, *, concerns: dict[str, str], event: str = "other", yes: str = "0.9",
                 minutes_ago: int = 5) -> None:
        self.feed.add(title, link, self.account.clock.now() - timedelta(minutes=minutes_ago))
        self.script["by_link"][link] = {"concerns": concerns, "event_type": {event: "1"},
                                        "holder_wants_to_know": {"yes": yes, "no": str(1 - Decimal(yes))}}

    def delisting(self, coin: str = "SOL") -> None:
        title = SOL_DELISTING_TITLE.replace("SOL", coin)
        self.headline(title, SOL_DELISTING.replace("sol", coin.lower()), concerns={coin: "0.91", "none": "0.09"},
                      event="delisting", yes="0.93")

    def unrelated(self) -> None:
        self.headline(UNRELATED_TITLE, UNRELATED, concerns={"none": "0.88", "kraken": "0.12"}, yes="0.08")

    def poll(self):
        return self.shadow.poll_once()


def hold_sol(account, quantity: str = "5") -> str:
    """A real paper buy of SOL that fills at once (the account then holds the coin)."""
    intent = account.authorize("enter", "SOL/USD", quantity)
    account.dispatch()
    account.clock.advance(1)
    account.quote("SOL/USD", "99.9", "100.1")
    assert account.state(intent) == "FILLED"
    return intent


def switch_news_wakes(account, mode: str, revision: str) -> None:
    """The owner's revision as ``news-wakes --confirm`` records it: the active policy with ``news_wakes`` set."""
    policy = account.execution.authority.active_policy()
    account.execution.authority.install_policy(policy.model_copy(update={"revision_id": revision,
                                                                         "news_wakes": mode}), role="owner")


def routes(account) -> list[dict]:
    return [dict(row) for row in account.database.execute("SELECT * FROM news_alarm_wake_routes ORDER BY sequence")]


def news_triggers(account) -> list[dict]:
    return [dict(row) for row in account.database.execute(
        "SELECT * FROM wake_triggers WHERE kind = 'news_alarm' ORDER BY sequence")]


__all__ = ["KRAKEN_BLOG", "SOL_DELISTING", "SOL_DELISTING_TITLE", "UNRELATED", "UNRELATED_TITLE", "NewsDesk",
           "SyntheticFeed", "hold_sol", "news_triggers", "routes", "switch_news_wakes"]
