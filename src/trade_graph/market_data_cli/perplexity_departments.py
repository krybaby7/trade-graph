"""Owner command ``trade-graph perplexity-departments`` (T37, T38): switch Research and the Leader to Perplexity.

Discovered here like the other commands, so ``cli.py`` is unchanged. In the style of ``mandate-cleanup`` and
``money-limits``: it previews by default and records only with ``--confirm``, refuses while the paper service
runs, takes ``--expect-revision`` (the revision the preview showed) and never rewrites an earlier revision.

The defaults are the owner's decisions, so step L3 is ``trade-graph perplexity-departments --confirm``: Research
and the Leader's news rounds think with Claude Sonnet 5.5 through Perplexity's pay-as-you-go Agent API (D124),
with no money limit while testing (D125, D138 item 4). Every call still gets a reservation and a receipt, unknown
usage stays unresolved (never free), and HQ shows the spend with the USD20 alert. ``--monthly-limit-usd`` later
records the monthly limit (about 1.5 times the observed monthly cost, D138 item 4); reaching it pauses only these
research rounds, never the Trader, protection or records. ``--research off`` / ``--leader off`` switch back.

Recording the switch makes no call by itself, and nothing here enables live trading or withdrawals.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

from trade_graph.domain.errors import TradeGraphError

COMMAND = "perplexity-departments"
DEFAULT_DATABASE = "runtime/trade_graph.sqlite"
HISTORY = "earlier switch revisions stay as they were; each research round records the revision it ran under"
PAID_CALLS = ("Perplexity calls of the departments switched on are paid per call: each gets a reservation before it "
              "is sent and a receipt after, unknown usage stays unresolved and counts at its maximum, and HQ shows "
              "the spend (USD20 testing alert). The Trader stays on its subscription.")


def register(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(COMMAND, help="owner: preview switching Research and the Leader to Perplexity; "
                                          "--confirm records it (paper only)")
    parser.add_argument("--database", default=DEFAULT_DATABASE, help="the paper state database")
    parser.add_argument("--research", choices=["on", "off"], default="on",
                        help="Research's focus coins and refreshes on Perplexity (T37)")
    parser.add_argument("--leader", choices=["on", "off"], default="on",
                        help="the Leader's news rounds on Perplexity (T38)")
    parser.add_argument("--monthly-limit-usd", help="a monthly Perplexity money limit in USD; without it, the "
                                                   "testing mode with no limit (D125)")
    parser.add_argument("--expect-revision", help="the switch revision the preview showed; refuse if it changed")
    parser.add_argument("--confirm", action="store_true", help="record the previewed switch")


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int | None:
    if args.command != COMMAND:
        return None
    return _switch(args, parser)


def _limit(text: str | None, parser: argparse.ArgumentParser) -> Decimal | None:
    if text is None:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        parser.error(f"cannot read --monthly-limit-usd {text!r}; give a plain amount such as 30 or 12.50")
    if not value.is_finite() or value <= 0:
        parser.error("--monthly-limit-usd must be a positive USD amount")
    return value


def _switch(args, parser) -> int:
    from trade_graph.adapters.persistence.db import Database
    from trade_graph.application.perplexity_switch import (
        propose_perplexity_departments,
        record_perplexity_departments,
        summary,
    )
    from trade_graph.domain.clock import SystemClock

    path = Path(args.database)
    if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
        parser.error("database does not exist; initialize a paper account first")
    if path.parent.stat().st_mode & 0o077:
        parser.error("paper database requires a private directory with mode 0700")
    limit = _limit(args.monthly_limit_usd, parser)
    research, leader = args.research == "on", args.leader == "on"
    revision_id = "perplexity-" + uuid.uuid4().hex
    database = None
    try:
        database = Database(path)
        if args.confirm:
            proposal = record_perplexity_departments(database, SystemClock(), research=research, leader=leader,
                                                     monthly_limit_usd=limit, revision_id=revision_id,
                                                     expect_revision=args.expect_revision)
        else:
            proposal = propose_perplexity_departments(database, research=research, leader=leader,
                                                      monthly_limit_usd=limit, revision_id=revision_id)
    except (ValueError, LookupError, OSError, sqlite3.DatabaseError, TradeGraphError) as error:
        parser.error(f"Perplexity switch refused ({type(error).__name__}: {error}); nothing was written")
    finally:
        if database is not None:
            database.close()
    applied = bool(args.confirm and proposal.proposed is not None)
    shown = proposal.proposed or proposal.current
    print(json.dumps({
        "command": COMMAND,
        "applied": applied,
        "current_revision": proposal.current.revision_id,
        "new_revision": proposal.proposed.revision_id if applied and proposal.proposed is not None else None,
        "current": summary(proposal.current),
        "proposed": summary(shown) if proposal.proposed is not None else summary(proposal.current),
        "changes": proposal.changes,
        "paid_calls": PAID_CALLS,
        "history": HISTORY,
        "live_enabled": False,
    }, sort_keys=True))
    return 0
