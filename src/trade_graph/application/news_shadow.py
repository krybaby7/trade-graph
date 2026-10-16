"""The shadow news alarm: collect headlines, ask Jev, log whom each would have woken (T44, D137).

A poll is a software check (recommended every 5 minutes), not an agent wake-up. Every headline is stored
before Jev reads it. In shadow mode nobody is woken: ``wakes_sent`` is always 0. Turning verdicts into real
wake-ups is T45, through the event engine's dispatcher.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from trade_graph.adapters.news.feeds import FeedFetcher, FeedRefused, parse_feed
from trade_graph.application.jev_checks import JevCheckRunner
from trade_graph.application.news_store import NewsStore, reference_time
from trade_graph.contracts.news import NewsAlarmSettings, WatchList
from trade_graph.domain.clock import Clock, utc_iso


@dataclass(frozen=True)
class FeedPoll:
    feed_id: str
    outcome: str
    new: int
    repeats: int
    rejected: int


@dataclass
class PollSummary:
    polled_at: str
    jev: str
    feeds: list[FeedPoll] = field(default_factory=list)
    new_headlines: int = 0
    not_checked_old: int = 0
    checks: int = 0
    answered: int = 0
    would_wake: int = 0
    recovered: int = 0
    failures: dict[str, int] = field(default_factory=dict)
    wakes_sent: int = 0  # shadow mode: always zero

    def document(self) -> dict:
        return {"polled_at": self.polled_at, "jev": self.jev, "wakes_sent": self.wakes_sent,
                "feeds": [vars(feed) for feed in self.feeds], "new_headlines": self.new_headlines,
                "not_checked_old": self.not_checked_old, "checks": self.checks, "answered": self.answered,
                "would_wake": self.would_wake, "recovered": self.recovered, "failures": dict(self.failures)}


class NewsAlarmShadow:
    def __init__(self, *, store: NewsStore, fetcher: FeedFetcher, settings: NewsAlarmSettings, clock: Clock,
                 checks: JevCheckRunner | None, watch: Callable[[], WatchList], jev_label: str | None = None) -> None:
        self.store, self.fetcher, self.settings, self.clock = store, fetcher, settings, clock
        self.checks, self.watch = checks, watch
        self.jev_label = jev_label or ("off" if checks is None else "scripted" if checks.adapter.synthetic
                                       else "live")

    def poll_once(self) -> PollSummary:
        summary = PollSummary(polled_at=utc_iso(self.clock.now()), jev=self.jev_label)
        if self.checks is not None:
            summary.recovered = len(self.checks.recover())
        cutoff = self.clock.now() - timedelta(seconds=self.settings.check_window_seconds)
        for feed in self.settings.feeds:
            started = self.clock.now()
            etag, last_modified = self.store.validators(feed.feed_id)
            fetched = self.fetcher.fetch(feed, etag=etag, last_modified=last_modified)
            parsed = None
            if fetched.outcome == "fetched" and fetched.body is not None:
                try:
                    parsed = parse_feed(fetched.body, feed.format)
                except FeedRefused:
                    parsed = None
            record = self.store.record_poll(feed, fetched, parsed, started_at=started)
            summary.feeds.append(FeedPoll(feed.feed_id, record.outcome, len(record.new), record.repeats,
                                          record.rejected))
            summary.new_headlines += len(record.new)
            summary.not_checked_old += sum(1 for headline in record.new if reference_time(headline) < cutoff)
        if self.checks is None:
            return summary
        due = self.store.due_for_check(self.clock.now(), window_seconds=self.settings.check_window_seconds,
                                       max_attempts=self.settings.max_attempts_per_headline)
        if not due:
            return summary
        watch = self.watch()
        failures: Counter[str] = Counter()
        for headline in due:
            record = self.checks.check(headline, watch, self.settings.rule)
            summary.checks += 1
            if record.status == "answered":
                summary.answered += 1
                summary.would_wake += int(bool(record.verdict and record.verdict.would_wake))
            else:
                failures[record.failure or record.status] += 1
            if record.failure == "credentials" or record.status == "budget_refused":
                break  # a bad key or an exhausted money limit stops this poll; the next poll tries again
        summary.failures = dict(failures)
        return summary
