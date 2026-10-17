"""News alarms wake the Leader, and the Trader when the coin is held (T45; D137, D138 item 10, D116, D225-D229).

T44's shadow news alarm asks Jev about each new headline and records whom it would wake (``news_alarm_verdicts``).
``NewsWakeRouter`` is a trigger source of the event engine (T35): at each engine step it routes every would-wake
verdict not yet routed, once per portfolio, and writes one ``news_alarm_wake_routes`` row per department (state
migration 0042):

- the **Leader** for every would-wake verdict;
- the **Trader** too when the headline concerns a coin the portfolio holds: held when Jev was asked (the verdict
  already says so), held now when the alarm is routed, or a headline about Kraken itself while anything is held.
  Either is enough: ambiguity adds wake-ups, it never removes one (D116).

The owner's switch decides what a route is (``news_wake_setting``, read from the owner policy, D227). In **shadow**
mode (shipped, and what an unreadable switch counts as) the route records exactly whom the alarm would wake and why,
and nobody is woken. In **live** mode each route adds one ``news_alarm`` trigger through ``EventEngine.notify`` in the
same transaction, so the dispatcher wakes the department like any other reason, coalescing it with whatever else is
waiting and never dropping it. Live applies to alarms recorded from the moment the owner switched it on; an older
alarm still waiting is routed in shadow, so switching on never floods anyone with a backlog. A verdict that would wake
nobody gets no route: an unrelated headline wakes nobody.

The woken department reads the headline as data inside a fixed note (``contracts/news_wakes.compose_note``). Nothing
here touches orders, plans, money limits, check-ins, owner policy or another trigger; reads are bounded per step, which
is a read bound and never a cap on alarms or wakes.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError

from trade_graph.contracts.alarms import SYMBOL_PATTERN
from trade_graph.contracts.models import OwnerPolicy
from trade_graph.contracts.news import SUBJECT_KRAKEN, SUBJECT_NONE, coin_code
from trade_graph.contracts.news_wakes import (
    DEFAULT_MODE,
    ROUTE_RULE_VERSION,
    NewsWakeMode,
    compose_note,
    route_source_ref,
)
from trade_graph.domain.clock import Clock, parse_utc, utc_iso
from trade_graph.domain.errors import TradeGraphError

if TYPE_CHECKING:
    from trade_graph.application.events.engine import EventEngine

BATCH: Final = 500  # verdicts read per portfolio and step; the rest at the next step (a read bound, not a cap)
POLICY_LOOKBACK: Final = 1000  # owner revisions read back to find when live began (a read bound)
BECAUSE: Final = ("alarm", "held_at_check", "held_now", "kraken_while_holding")  # migration 0042's words
_TABLES: Final = frozenset({"news_alarm_verdicts", "news_headlines", "news_jev_attempts", "news_alarm_wake_routes"})


@dataclass(frozen=True, slots=True)
class NewsWakeSetting:
    """The owner's switch in force: the mode, the owner revision that holds it and, when live, since when."""

    mode: NewsWakeMode
    revision_id: str | None
    live_since: datetime | None


def _mode_of(document: str) -> NewsWakeMode | None:
    try:
        policy = OwnerPolicy.model_validate_json(document)
    except (ValidationError, ValueError):
        return None
    return policy.news_wakes or DEFAULT_MODE


def news_wake_setting(source) -> NewsWakeSetting:
    """The switch as the newest owner-policy revision records it (``OwnerPolicy.news_wakes``). Fails closed.

    ``source`` is a ``Database`` or any ``sqlite3.Connection`` (HQ's read-only one too). No revision, an older revision
    without the field, or one that cannot be read all count as shadow. When live, ``live_since`` is when the unbroken
    run of live revisions began, so a later owner revision that keeps the switch (money limits, for example) does not
    move it.
    """
    shadow = NewsWakeSetting(DEFAULT_MODE, None, None)
    try:
        if source.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'owner_policy_revisions'"
                          ).fetchone() is None:
            return shadow
        rows = [tuple(row) for row in source.execute(
            "SELECT revision_id, document_json, created_at FROM owner_policy_revisions "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?", (POLICY_LOOKBACK,)).fetchall()]
    except sqlite3.DatabaseError:
        return shadow
    if not rows:
        return shadow
    newest = _mode_of(rows[0][1])
    if newest is None:
        return shadow
    if newest != "live":
        return NewsWakeSetting(newest, rows[0][0], None)
    since = rows[0][2]
    for _revision, document, created_at in rows[1:]:
        if _mode_of(document) != "live":
            break
        since = created_at
    try:
        started = parse_utc(since)
    except (TypeError, ValueError):
        return shadow
    return NewsWakeSetting("live", rows[0][0], started)


def route_id_for(verdict_id: str, portfolio_id: str, department: str) -> str:
    """One route per verdict, portfolio and department; the same inputs always give the same id (restart-safe)."""
    return hashlib.sha256(f"{verdict_id}|{portfolio_id}|{department}".encode()).hexdigest()[:32]


def _codes(values: Any) -> list[str]:
    return [value for value in values if isinstance(value, str)] if isinstance(values, list) else []


class NewsWakeRouter:
    """The event engine's news source: routes would-wake verdicts in the mode the owner's switch is in."""

    def __init__(self, database, clock: Clock, execution) -> None:
        self.database, self.clock, self.execution = database, clock, execution
        self._ready = False

    def available(self) -> bool:
        """False on a database without the news alarm's records (before migrations 0026 and 0042)."""
        if not self._ready:
            names = {row[0] for row in self.database.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
            self._ready = _TABLES <= names
        return self._ready

    def collect(self, engine: EventEngine, portfolios: Sequence[str]) -> int:
        """Route each would-wake verdict once per portfolio; returns how many triggers were added (live only)."""
        if not self.available():
            return 0
        added = 0
        for portfolio_id in portfolios:
            rows = self._unrouted(portfolio_id)
            if not rows:
                continue
            setting = news_wake_setting(self.database)
            held = self._held(portfolio_id)
            for row in rows:
                added += self._route(engine, portfolio_id, row, setting, held)
        return added

    # --- reading ------------------------------------------------------------------------------------------------

    def _unrouted(self, portfolio_id: str) -> list:
        return self.database.execute(
            """SELECT v.verdict_id, v.headline_id, v.rule_version, v.urgent_threshold, v.wake_roles_json,
                      v.subjects_json, v.held_subjects_json, v.event_type, v.holder_yes, v.created_at,
                      h.title, h.link, h.publisher, h.feed_id, h.evidence, a.synthetic, a.requested_model
               FROM news_alarm_verdicts v
               JOIN news_headlines h ON h.headline_id = v.headline_id
               JOIN news_jev_attempts a ON a.attempt_id = v.attempt_id
               WHERE v.would_wake = 1 AND NOT EXISTS (
                   SELECT 1 FROM news_alarm_wake_routes r WHERE r.verdict_id = v.verdict_id AND r.portfolio_id = ?)
               ORDER BY v.rowid LIMIT ?""", (portfolio_id, BATCH)).fetchall()

    def _held(self, portfolio_id: str) -> frozenset[str]:
        """The coins the portfolio holds now (open lots), as the news alarm names coins."""
        held = set()
        for lot in self.execution.ledger.books(portfolio_id).lots:
            if lot.open_quantity() > Decimal("0"):
                try:
                    held.add(coin_code(lot.asset))
                except ValueError:
                    continue
        return frozenset(held)

    def _symbol(self, portfolio_id: str, subjects: list[str]) -> str | None:
        """The pair of the one coin a headline concerns (for the woken department's market data), or None."""
        coins = [subject for subject in subjects if subject not in (SUBJECT_KRAKEN, SUBJECT_NONE)]
        if len(coins) != 1:
            return None
        authority = self.execution.authority
        try:
            listed = [symbol for symbol in authority.active_mandate(portfolio_id).symbols
                      if symbol.split("/", 1)[0] == coins[0]]
        except TradeGraphError:
            listed = []
        if listed:
            return listed[0]
        try:
            scope = authority.active_policy().coin_scope
        except TradeGraphError:
            scope = None
        symbol = f"{coins[0]}/{scope.quote_asset if scope is not None else 'USD'}"
        return symbol if re.fullmatch(SYMBOL_PATTERN, symbol) else None

    # --- routing --------------------------------------------------------------------------------------------------

    @staticmethod
    def _trader_because(roles: list[str], held_at_check: list[str], subjects: list[str],
                        held: frozenset[str]) -> str | None:
        if "trader" in roles:
            return "held_at_check" if held_at_check else "kraken_while_holding"
        if any(subject in held for subject in subjects):
            return "held_now"
        if SUBJECT_KRAKEN in subjects and held:
            return "kraken_while_holding"
        return None

    def _route(self, engine: EventEngine, portfolio_id: str, row, setting: NewsWakeSetting,
               held: frozenset[str]) -> int:
        subjects = _codes(json.loads(row["subjects_json"]))
        held_at_check = _codes(json.loads(row["held_subjects_json"]))
        roles = _codes(json.loads(row["wake_roles_json"]))
        held_now = sorted(subject for subject in subjects if subject in held)
        departments = [("leader", "alarm")]
        trader = self._trader_because(roles, held_at_check, subjects, held)
        if trader is not None:
            departments.append(("trader", trader))
        alarm_at = parse_utc(row["created_at"])
        live = setting.mode == "live" and setting.live_since is not None and alarm_at >= setting.live_since
        mode: NewsWakeMode = "live" if live else "shadow"
        symbol = self._symbol(portfolio_id, subjects)
        note = compose_note(headline_id=row["headline_id"], publisher=row["publisher"], title=row["title"],
                            link=row["link"], subjects=subjects, event_type=row["event_type"],
                            holder_yes=row["holder_yes"])
        reason = {"subjects": subjects, "held_at_check": held_at_check, "held_now": held_now,
                  "wake_roles_at_check": roles, "event_type": row["event_type"], "holder_yes": row["holder_yes"],
                  "urgent_threshold": row["urgent_threshold"], "verdict_rule": row["rule_version"],
                  "feed_id": row["feed_id"], "headline_evidence": row["evidence"],
                  "jev_model": row["requested_model"], "jev_synthetic": bool(row["synthetic"]),
                  "switch": {"mode": setting.mode, "revision_id": setting.revision_id,
                             "live_since": utc_iso(setting.live_since) if setting.live_since else None}}
        added = 0
        with self.database.immediate() as connection:
            if connection.execute("SELECT 1 FROM news_alarm_wake_routes WHERE verdict_id = ? AND portfolio_id = ?",
                                  (row["verdict_id"], portfolio_id)).fetchone() is not None:
                return 0  # routed meanwhile (another engine on the same records); never twice
            for department, because in departments:
                route_id = route_id_for(row["verdict_id"], portfolio_id, department)
                source = route_source_ref(route_id)
                trigger_id = None
                if live:
                    trigger = engine.notify(portfolio_id, department, kind="news_alarm", source_ref=source,
                                            note=note, symbol=symbol, occurred_at=alarm_at)
                    trigger_id = trigger.trigger_id
                    added += 1
                connection.execute(
                    """INSERT INTO news_alarm_wake_routes (route_id, verdict_id, headline_id, portfolio_id, department,
                       mode, because, rule_version, policy_revision, reason_json, note, symbol, source_ref, trigger_id,
                       alarm_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (route_id, row["verdict_id"], row["headline_id"], portfolio_id, department, mode, because,
                     ROUTE_RULE_VERSION, setting.revision_id, json.dumps(reason, sort_keys=True), note, symbol, source,
                     trigger_id, row["created_at"], utc_iso(self.clock.now())))
        return added


__all__ = ["BATCH", "BECAUSE", "NewsWakeRouter", "NewsWakeSetting", "news_wake_setting", "route_id_for"]
