"""Shadow news alarm commands (T44). Discovered here like the candle commands, so no shared list changes.

``news-alarm-poll`` reads the headline feeds, stores each new headline, asks Jev the fixed questions when
the owner has switched Jev calls on (or a scripted file is given) and logs whom each headline would have
woken. Nobody is woken. ``news-alarm-report`` only reads the log.

Jev calls stay off by default. They need the owner's settings file with ``jev.calls_enabled`` and a key in
the owner's private folder (``typesafe.key``, mode 0600 in a 0700 folder). The key is never printed.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from trade_graph.domain.clock import Clock, SystemClock
from trade_graph.domain.errors import TradeGraphError

POLL = "news-alarm-poll"
REPORT = "news-alarm-report"
DEFAULT_DATABASE = "runtime/trade_graph.sqlite"


def build_clock() -> Clock:
    return SystemClock()


def build_sleep():
    return time.sleep


def build_monotonic():
    return time.monotonic


def register(sub: argparse._SubParsersAction) -> None:
    poll = sub.add_parser(POLL, help="shadow news alarm: read headline feeds, ask Jev if switched on, wake nobody")
    poll.add_argument("--database", default=DEFAULT_DATABASE, help="the paper state database")
    poll.add_argument("--config", help="owner news alarm settings (JSON); recommended defaults when absent")
    poll.add_argument("--fixture-feeds", help="read feeds from a labelled fixture folder instead of the network")
    poll.add_argument("--scripted-jev", help="scripted Jev replies (synthetic test data, never a real call)")
    poll.add_argument("--polls", type=int, default=1, help="polls to run, one per poll interval (default 1)")
    report = sub.add_parser(REPORT, help="read-only shadow news alarm report")
    report.add_argument("--database", default=DEFAULT_DATABASE, help="the paper state database")
    report.add_argument("--config", help="owner news alarm settings (for the alert amount and check window)")
    report.add_argument("--since", help="UTC start, for example 2026-10-16T00:00:00Z (default: 24 hours ago)")
    report.add_argument("--until", help="UTC end (default: now)")
    report.add_argument("--format", choices=["json", "text"], default="json")


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int | None:
    if args.command == POLL:
        return _poll(args, parser)
    if args.command == REPORT:
        return _report(args, parser)
    return None


def _settings(path: str | None, parser: argparse.ArgumentParser):
    from pydantic import ValidationError

    from trade_graph.contracts.news import NewsAlarmSettings

    if path is None:
        return NewsAlarmSettings()
    try:
        return NewsAlarmSettings.model_validate_json(Path(path).read_text())
    except (OSError, ValidationError, ValueError) as error:
        fields = sorted({".".join(map(str, item["loc"])) for item in error.errors()}) if isinstance(
            error, ValidationError) else [type(error).__name__]
        parser.error(f"news alarm settings refused ({', '.join(fields)}); nothing was read or sent")


def _state_database(path: str, parser: argparse.ArgumentParser) -> Path:
    target = Path(path)
    if target.is_symlink() or target.parent.is_symlink() or not target.is_file():
        parser.error(f"no paper state database at {path}; initialise the paper account first")
    if target.parent.stat().st_mode & 0o077:
        parser.error("the paper state database requires a private directory with mode 0700")
    return target


def _card_fresh(card, today: date) -> bool:
    try:
        verified, effective = date.fromisoformat(card.verified_at), date.fromisoformat(card.effective_at)
    except ValueError:
        return False
    return verified <= today and effective <= today and (today - verified).days <= 30


def _poll(args, parser) -> int:
    from trade_graph.adapters.decisions.jev import (
        JEV_PRICE_CARD,
        JevAdapter,
        PinnedJevHttp,
        ScriptedJevHttp,
        load_jev_key,
    )
    from trade_graph.adapters.news.feeds import FixtureFeedFetcher, HttpxFeedFetcher
    from trade_graph.adapters.persistence.db import Database
    from trade_graph.application.budget import BudgetGateway
    from trade_graph.application.jev_checks import JevBudgetBinding, JevCheckRunner
    from trade_graph.application.news_shadow import NewsAlarmShadow
    from trade_graph.application.news_store import NewsStore
    from trade_graph.application.news_watchlist import watch_list

    settings = _settings(args.config, parser)
    if args.polls < 1:
        parser.error("--polls must be at least 1")
    jev = settings.jev
    if args.scripted_jev and jev.calls_enabled:
        parser.error("scripted Jev replies cannot be mixed with real Jev calls; switch calls_enabled off for a "
                     "scripted run")
    key = None
    if jev.calls_enabled:
        try:
            key = load_jev_key(jev.key_directory)
        except (FileNotFoundError, PermissionError) as error:
            parser.error(f"no Jev key could be read from the owner's private folder ({error}); Jev calls stay "
                         "off and nothing was sent")
    card = jev.price_card or JEV_PRICE_CARD
    if card.model != jev.model:
        parser.error("the configured Jev version has no dated price card; add one to the settings file")
    clock = build_clock()
    if jev.calls_enabled and not _card_fresh(card, clock.now().date()):
        parser.error("the Jev price card is older than 30 days or not yet effective; record a newer dated card")
    try:
        transport = (ScriptedJevHttp.from_fixture(args.scripted_jev) if args.scripted_jev
                     else PinnedJevHttp(timeout=jev.timeout_seconds) if jev.calls_enabled else None)
        fetcher = FixtureFeedFetcher(args.fixture_feeds) if args.fixture_feeds else HttpxFeedFetcher()
    except (OSError, ValueError, KeyError) as error:
        parser.error(f"cannot read the fixture files ({type(error).__name__}); nothing was sent")
    path = _state_database(args.database, parser)
    database = None
    summaries = []
    try:
        database = Database(path)
        budget = BudgetGateway(database, clock)
        budget.seed_card(card)
        row = database.execute("SELECT portfolio_id FROM portfolios WHERE mode = 'paper' ORDER BY created_at DESC "
                               "LIMIT 1").fetchone()
        portfolio_id = row["portfolio_id"] if row is not None else None
        store = NewsStore(database, clock)
        checks = None
        if transport is not None:
            binding = JevBudgetBinding(deployment_id=jev.deployment_id, price_card_id=card.price_card_id,
                                       fx_rate=jev.fx_rate, fx_buffer=jev.fx_buffer, synthetic=transport.synthetic,
                                       fx_rate_id=jev.fx_rate_id, portfolio_id=portfolio_id,
                                       timeout_seconds=jev.timeout_seconds)
            checks = JevCheckRunner(database, clock, budget, JevAdapter(transport, key=key), binding, store,
                                    monotonic=build_monotonic())
        shadow = NewsAlarmShadow(
            store=store, fetcher=fetcher, settings=settings, clock=clock, checks=checks,
            watch=lambda: watch_list(database, clock, portfolio_id, settings.extra_watch_coins))
        sleep = build_sleep()
        try:
            for number in range(args.polls):
                if number:
                    sleep(settings.poll_interval_seconds)
                summaries.append(shadow.poll_once().document())
        except KeyboardInterrupt:
            pass  # the polls already recorded are kept; an open Jev call was recorded as interrupted
        label = shadow.jev_label
    except (ValueError, LookupError, OSError, sqlite3.DatabaseError, TradeGraphError) as error:
        parser.error(f"news alarm poll stopped ({type(error).__name__}); records written so far are kept")
    finally:
        if database is not None:
            database.close()
    print(json.dumps({"command": POLL, "mode": "shadow", "wakes_sent": 0, "jev": label,
                      "paid_calls": label == "live",
                      "feed_evidence": getattr(fetcher, "evidence", "live_public"), "polls": summaries}))
    return 0


def _when(value: str | None, parser, default: datetime) -> datetime:
    if value is None:
        return default
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parser.error(f"cannot read the time {value!r}; use for example 2026-10-16T00:00:00Z")
    if parsed.tzinfo is None:
        parser.error("times must be UTC, ending in Z")
    return parsed


def _report(args, parser) -> int:
    from trade_graph.application.news_report import format_report_text, news_alarm_report

    settings = _settings(args.config, parser)
    target = Path(args.database)
    if not target.is_file() or target.stat().st_size == 0:
        parser.error(f"no state database at {args.database}; the report only reads one that already exists")
    now = build_clock().now()
    until = _when(args.until, parser, now)
    since = _when(args.since, parser, until - timedelta(days=1))
    try:
        connection = sqlite3.connect(f"{target.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.DatabaseError:
        parser.error(f"cannot open {args.database} read-only")
    connection.row_factory = sqlite3.Row
    try:
        report = news_alarm_report(connection, since=since, until=until, alert_usd=settings.testing_alert_usd,
                                   check_window_seconds=settings.check_window_seconds)
    except sqlite3.DatabaseError as error:
        parser.error(f"cannot read the news alarm log ({type(error).__name__})")
    finally:
        connection.close()
    print(format_report_text(report) if args.format == "text" else json.dumps(report))
    return 0
