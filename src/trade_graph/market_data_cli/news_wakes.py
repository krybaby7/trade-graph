"""Owner command ``trade-graph news-wakes`` (T45): switch news alarm wake-ups between shadow and live.

Discovered here like the other commands, so ``cli.py`` is unchanged. News alarms ship in shadow mode: the event
engine records whom each alarm would wake and why, and wakes nobody. Without ``--live`` the command shows the switch in
force and the shadow evidence; ``--live on`` or ``--live off`` previews the owner-policy revision, and ``--confirm``
records it. Refused while the paper service runs; ``--expect-revision`` takes the revision the preview showed.
Going live is refused until the shadow log holds at least one alarm from a live public feed with a real Jev answer
(decided from shadow evidence after step L3, not from fixtures). Paper only: no paid call, order or live setting.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from trade_graph.domain.errors import TradeGraphError

COMMAND = "news-wakes"
DEFAULT_DATABASE = "runtime/trade_graph.sqlite"
MEANING = {
    "shadow": "news alarms are recorded with whom they would wake and why; nobody is woken",
    "live": ("a news alarm wakes the Leader, and the Trader too when the coin is held; the headline is data and "
             "cannot place, approve or block a trade"),
}


def register(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(COMMAND, help="owner: show the news wake-up switch and its shadow evidence; --live on|off "
                                          "previews a change and --confirm records it (paper only)")
    parser.add_argument("--database", default=DEFAULT_DATABASE, help="the paper state database")
    parser.add_argument("--live", choices=["on", "off"], help="on: news alarms really wake people; off: shadow mode")
    parser.add_argument("--since", help="count evidence from this UTC time, for example 2026-10-20T00:00:00Z")
    parser.add_argument("--expect-revision", help="the owner-policy revision the preview showed; refuse if it changed")
    parser.add_argument("--confirm", action="store_true", help="record the previewed revision")


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int | None:
    if args.command != COMMAND:
        return None
    return _news_wakes(args, parser)


def _since(value: str | None, parser) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parser.error(f"cannot read the time {value!r}; use for example 2026-10-20T00:00:00Z")
    if parsed.tzinfo is None:
        parser.error("times must be UTC, ending in Z")
    return parsed


def _news_wakes(args, parser) -> int:
    from trade_graph.adapters.persistence.db import Database
    from trade_graph.application.authority import AuthorityRecord
    from trade_graph.application.news_wake_switch import propose_news_wakes, record_news_wakes
    from trade_graph.domain.clock import SystemClock

    if args.confirm and args.live is None:
        parser.error("--confirm needs --live on or --live off; nothing was written")
    path = Path(args.database)
    if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
        parser.error("database does not exist; initialize a paper account first")
    if path.parent.stat().st_mode & 0o077:
        parser.error("paper database requires a private directory with mode 0700")
    since = _since(args.since, parser)
    database = None
    try:
        database = Database(path)
        record = AuthorityRecord(database, SystemClock())
        current_mode = record.active_policy().news_wakes or "shadow"
        mode = current_mode if args.live is None else ("live" if args.live == "on" else "shadow")
        revision_id = "owner-" + uuid.uuid4().hex
        if args.confirm:
            proposal = record_news_wakes(record, mode=mode, revision_id=revision_id, since=since,
                                         expect_revision=args.expect_revision)
        else:
            proposal = propose_news_wakes(record, mode=mode, revision_id=revision_id, since=since)
    except (ValueError, LookupError, OSError, sqlite3.DatabaseError, TradeGraphError) as error:
        parser.error(f"news wake-ups refused ({type(error).__name__}: {error}); nothing was written")
    finally:
        if database is not None:
            database.close()
    applied = bool(args.confirm and proposal.policy is not None)
    print(json.dumps({
        "command": COMMAND,
        "applied": applied,
        "current_revision": proposal.current.revision_id,
        "new_revision": proposal.policy.revision_id if applied and proposal.policy is not None else None,
        "mode": {"from": proposal.mode_from, "to": proposal.mode_to},
        "means": MEANING[proposal.mode_to],
        "changes": proposal.changes,
        "refused": proposal.refusal,
        "evidence": proposal.evidence.document(),
        "shadow_logging": "continues in both modes",
        "paid_calls_enabled": False,
        "live_enabled": False,
    }))
    return 0
