"""Owner command ``trade-graph money-limits`` (T32): record the owner's money limits as an owner-policy revision.

Discovered here like the other commands, so ``cli.py`` is unchanged. In the style of ``mandate-cleanup``: it
previews by default and writes only with ``--confirm``, refuses while the paper service runs, and never rewrites
an earlier revision, mandate or decision. One confirm records, in one transaction:

- a new owner-policy revision with the daily loss limit, the trading-day start, the amount per coin, the total
  and the coin list (the scanner rule, or the current list);
- a matching mandate revision for the paper portfolio (the Leader may narrow it again later, never raise it);
- an audit row naming the revision it replaced, both document hashes and every changed field.

The defaults are the owner's D138 settings (3% a day, 20% per coin, 80% in total, 00:00 UTC, the rule), so the
owner can run it at step L3 as ``trade-graph money-limits --confirm``. ``--expect-revision`` takes the revision
the preview showed and refuses if another one became active since. Paper only: live trading, paid calls,
withdrawals and leverage stay off.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

from trade_graph.domain.errors import TradeGraphError
from trade_graph.domain.money import canonical_decimal

COMMAND = "money-limits"
DEFAULT_DATABASE = "runtime/trade_graph.sqlite"
HISTORY = ("earlier policy revisions, mandates and decisions stay as they were; each decision keeps the revision "
           "it was made under")


def register(sub: argparse._SubParsersAction) -> None:
    from trade_graph.application.money_limits import D138_VALUES

    def percent(value: Decimal) -> str:
        return canonical_decimal(value * 100)

    parser = sub.add_parser(COMMAND, help="owner: preview your money limits and coin list as a policy revision; "
                                          "--confirm records it (paper only)")
    parser.add_argument("--database", default=DEFAULT_DATABASE, help="the paper state database")
    parser.add_argument("--portfolio-id", help="the paper portfolio whose mandate follows (default: the newest)")
    parser.add_argument("--daily-loss-percent", default=percent(D138_VALUES.daily_loss_limit_fraction),
                        help="loss from the day-start value, fees included, at which new buying stops for the day")
    parser.add_argument("--per-coin-percent", default=percent(D138_VALUES.per_coin_fraction),
                        help="most of the account in one coin, counting resting entries as if filled")
    parser.add_argument("--total-percent", default=percent(D138_VALUES.total_fraction),
                        help="most of the account in coins altogether")
    parser.add_argument("--day-start", default=D138_VALUES.trading_day_start_utc,
                        help="UTC time HH:MM when the trading day starts and the loss limit resets")
    parser.add_argument("--coins", choices=["rule", "list"], default=D138_VALUES.coin_list,
                        help="rule: any Kraken USD pair passing the coin scanner's checks; list: keep the list")
    parser.add_argument("--expect-revision", help="the policy revision the preview showed; refuse if it changed")
    parser.add_argument("--confirm", action="store_true", help="record the previewed revision")


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int | None:
    if args.command != COMMAND:
        return None
    return _money_limits(args, parser)


def _fraction(text: str, name: str, parser: argparse.ArgumentParser) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation:
        parser.error(f"cannot read {name} {text!r}; give a plain percentage such as 3 or 2.5")
    if not value.is_finite() or value < 0:
        parser.error(f"{name} must be a percentage of 0 or more")
    return value / 100


def _money_limits(args, parser) -> int:
    from trade_graph.adapters.persistence.db import Database
    from trade_graph.application.authority import AuthorityRecord
    from trade_graph.application.money_limits import (
        MoneyLimitValues,
        propose_money_limits,
        record_money_limits,
    )
    from trade_graph.domain.clock import SystemClock

    path = Path(args.database)
    if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
        parser.error("database does not exist; initialize a paper account first")
    if path.parent.stat().st_mode & 0o077:
        parser.error("paper database requires a private directory with mode 0700")
    try:
        values = MoneyLimitValues(
            _fraction(args.daily_loss_percent, "--daily-loss-percent", parser),
            _fraction(args.per_coin_percent, "--per-coin-percent", parser),
            _fraction(args.total_percent, "--total-percent", parser),
            args.day_start, args.coins)
    except (ValueError, TradeGraphError) as error:
        parser.error(f"money limits refused ({type(error).__name__}: {error}); nothing was written")
    database = None
    try:
        database = Database(path)
        portfolio = args.portfolio_id or _latest_paper_portfolio(database)
        if database.execute("SELECT 1 FROM portfolios WHERE portfolio_id = ? AND mode = 'paper'",
                            (portfolio,)).fetchone() is None:
            raise LookupError("no such paper portfolio")
        record = AuthorityRecord(database, SystemClock())
        revision_id = "owner-" + uuid.uuid4().hex
        if args.confirm:
            revision = record_money_limits(record, portfolio, values, revision_id=revision_id,
                                           expect_revision=args.expect_revision)
        else:
            revision = propose_money_limits(record, portfolio, values, revision_id=revision_id)
    except (ValueError, LookupError, OSError, sqlite3.DatabaseError, TradeGraphError) as error:
        parser.error(f"money limits refused ({type(error).__name__}: {error}); nothing was written")
    finally:
        if database is not None:
            database.close()
    applied = bool(args.confirm and (revision.policy is not None or revision.mandate is not None))
    recorded = revision.current_policy.money_limits is not None or (applied and revision.policy is not None)
    print(json.dumps({
        "command": COMMAND,
        "applied": applied,
        "portfolio_id": portfolio,
        "current_revision": revision.current_policy.revision_id,
        "new_revision": revision.policy.revision_id if applied and revision.policy is not None else None,
        "values": {
            "daily_loss_limit_percent": canonical_decimal(values.daily_loss_limit_fraction * 100),
            "per_coin_percent": canonical_decimal(values.per_coin_fraction * 100),
            "total_percent": canonical_decimal(values.total_fraction * 100),
            "trading_day_start_utc": values.trading_day_start_utc,
            "coin_list": values.coin_list,
        },
        "changes": revision.changes,
        "mandate": {"current_revision": revision.current_mandate.revision,
                    "new_revision": None if revision.mandate is None else revision.mandate.revision},
        "day_plan_orders": ("day-plan orders may be installed within these limits" if recorded else
                            "day-plan orders stay off until you confirm money limits"),
        "history": HISTORY,
        "paid_calls_enabled": False,
        "live_enabled": False,
    }))
    return 0


def _latest_paper_portfolio(database) -> str:
    row = database.execute(
        "SELECT portfolio_id FROM portfolios WHERE mode = 'paper' ORDER BY created_at DESC LIMIT 1").fetchone()
    if row is None:
        raise LookupError("no paper portfolio")
    return str(row["portfolio_id"])
