"""The Trading desk's records (state migration 0052, W12-A, C12; D280, D295, D296, D298, D299).

``DeskStore`` writes; the ``read_*`` functions read on any ``sqlite3.Row`` connection, a read-only one included, and
give nothing (``None``, ``[]`` or ``{}``) on a database before migration 0052. They are the reads HQ's Trading desk
room and the scorecard's split by strategy version, market type at entry and stance use (W13-A):

- ``read_playbook(connection, portfolio_id, as_of=None)``: the playbook revision in force at a moment (the newest one
  made by then), like a lesson revision; ``read_playbook_revisions`` lists them with who, when, why and the revision
  each replaced. Documents are hash-checked on read: an edited record is refused rather than trusted.
- ``read_stances`` and ``read_latest_stances``: the desk's stances per coin, with the label software showed.
- ``read_day_plan_desk_records``: each day plan's market type at installation, the playbook revision in force and the
  desk's stance on the coin in the same answer.

None of this is financial state, and nothing here refuses a day plan or an order (D280 item 5).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.market_types.store import read_latest_market_types
from trade_graph.application.trading_desk.market_types import reading
from trade_graph.contracts.trading_desk import (
    CoinMarketType,
    CoinStance,
    DayPlanDeskRecord,
    PlaybookChoice,
    PlaybookRecord,
    PlaybookRevision,
    StanceRecord,
    canonical_json,
    one_stance_per_coin,
)
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import StaleState, ValidationFailure

TABLES = ("desk_playbook_revisions", "desk_stances", "day_plan_desk_records")


def _has(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                              (table,)).fetchone() is not None


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def _iso(value: datetime | None) -> str | None:
    return None if value is None else utc_iso(value)


# --- reading --------------------------------------------------------------------------------------------------------

def _playbook(row: sqlite3.Row) -> PlaybookRecord:
    if _sha256(row["document_json"]) != row["document_sha256"]:
        raise StaleState(f"playbook revision {row['revision_id']} no longer matches its stored hash")
    document = json.loads(row["document_json"])
    return PlaybookRecord(
        revision_id=row["revision_id"], portfolio_id=row["portfolio_id"], revision=row["revision"],
        previous_revision_id=row["previous_revision_id"], set_by=row["set_by"], wake_id=row["wake_id"],
        task_id=row["task_id"], why=row["why"],
        choices=tuple(PlaybookChoice.model_validate(item) for item in document.get("choices", ())),
        document_sha256=row["document_sha256"], created_at=row["created_at"])


def read_playbook(connection: sqlite3.Connection, portfolio_id: str, *,
                  as_of: datetime | None = None) -> PlaybookRecord | None:
    """The playbook revision in force at ``as_of`` (now when omitted): the newest revision made by then, or None."""
    if not _has(connection, "desk_playbook_revisions"):
        return None
    row = connection.execute(
        """SELECT * FROM desk_playbook_revisions WHERE portfolio_id = ? AND (? IS NULL OR created_at <= ?)
           ORDER BY revision DESC LIMIT 1""", (portfolio_id, _iso(as_of), _iso(as_of))).fetchone()
    return None if row is None else _playbook(row)


def read_playbook_revisions(connection: sqlite3.Connection, portfolio_id: str, *, since: datetime | None = None,
                            until: datetime | None = None) -> list[PlaybookRecord]:
    """Every revision of a portfolio's playbook in order (optionally made in ``[since, until]``)."""
    if not _has(connection, "desk_playbook_revisions"):
        return []
    rows = connection.execute(
        """SELECT * FROM desk_playbook_revisions WHERE portfolio_id = ? AND (? IS NULL OR created_at >= ?)
           AND (? IS NULL OR created_at <= ?) ORDER BY revision""",
        (portfolio_id, _iso(since), _iso(since), _iso(until), _iso(until))).fetchall()
    return [_playbook(row) for row in rows]


def _stance(row: sqlite3.Row) -> StanceRecord:
    return StanceRecord(
        stance_id=row["stance_id"], portfolio_id=row["portfolio_id"], wake_id=row["wake_id"], task_id=row["task_id"],
        trading_day_start=row["trading_day_start"], symbol=row["symbol"], stance=row["stance"],
        market_type_seen=row["market_type_seen"], label_status=row["label_status"],
        label_market_type=row["label_market_type"], label_id=row["label_id"], strategy_ref=row["strategy_ref"],
        news_refs=tuple(json.loads(row["news_refs_json"])), reason=row["reason"],
        playbook_revision_id=row["playbook_revision_id"], created_at=row["created_at"])


def read_stances(connection: sqlite3.Connection, portfolio_id: str, *, since: datetime | None = None,
                 until: datetime | None = None, symbols: Iterable[str] | None = None,
                 wake_id: str | None = None) -> list[StanceRecord]:
    """The desk's stances in the order it gave them, optionally of some coins, of one wake or in a time range."""
    if not _has(connection, "desk_stances"):
        return []
    wanted = None if symbols is None else sorted(set(symbols))
    if wanted == []:
        return []
    clauses = ["portfolio_id = ?", "(? IS NULL OR created_at >= ?)", "(? IS NULL OR created_at <= ?)",
               "(? IS NULL OR wake_id = ?)"]
    params: list[Any] = [portfolio_id, _iso(since), _iso(since), _iso(until), _iso(until), wake_id, wake_id]
    if wanted is not None:
        clauses.append(f"symbol IN ({', '.join('?' for _ in wanted)})")
        params.extend(wanted)
    rows = connection.execute(f"SELECT * FROM desk_stances WHERE {' AND '.join(clauses)} ORDER BY sequence",
                              tuple(params)).fetchall()
    return [_stance(row) for row in rows]


def read_latest_stances(connection: sqlite3.Connection, portfolio_id: str, *, as_of: datetime | None = None,
                        since: datetime | None = None) -> dict[str, StanceRecord]:
    """The desk's newest stance on each coin made at or before ``as_of`` (and at or after ``since``), by symbol."""
    if not _has(connection, "desk_stances"):
        return {}
    rows = connection.execute(
        """SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY sequence DESC) AS newest
           FROM desk_stances WHERE portfolio_id = ? AND (? IS NULL OR created_at <= ?)
           AND (? IS NULL OR created_at >= ?)) WHERE newest = 1 ORDER BY symbol""",
        (portfolio_id, _iso(as_of), _iso(as_of), _iso(since), _iso(since))).fetchall()
    return {row["symbol"]: _stance(row) for row in rows}


def _record(row: sqlite3.Row) -> DayPlanDeskRecord:
    return DayPlanDeskRecord(
        plan_id=row["plan_id"], portfolio_id=row["portfolio_id"], symbol=row["symbol"],
        label_status=row["label_status"], market_type=row["market_type"], label_id=row["label_id"],
        labelled_at=row["labelled_at"], rules_ref=row["rules_ref"], playbook_revision_id=row["playbook_revision_id"],
        playbook_revision=row["playbook_revision"], stance_id=row["stance_id"], stance=row["stance"],
        recorded_at=row["recorded_at"])


def read_day_plan_desk_records(connection: sqlite3.Connection, *, portfolio_id: str | None = None,
                               plan_ids: Iterable[str] | None = None) -> dict[str, DayPlanDeskRecord]:
    """What was recorded about the desk when each day plan was installed, by plan id. A plan installed before
    migration 0052 has no record (absent here): its market type and playbook revision were not recorded."""
    if not _has(connection, "day_plan_desk_records"):
        return {}
    wanted = None if plan_ids is None else sorted(set(plan_ids))
    if wanted == []:
        return {}
    clauses, params = ["(? IS NULL OR portfolio_id = ?)"], [portfolio_id, portfolio_id]
    if wanted is not None:
        clauses.append(f"plan_id IN ({', '.join('?' for _ in wanted)})")
        params.extend(wanted)
    rows = connection.execute(f"SELECT * FROM day_plan_desk_records WHERE {' AND '.join(clauses)} "
                              "ORDER BY recorded_at, plan_id", tuple(params)).fetchall()
    return {row["plan_id"]: _record(row) for row in rows}


# --- writing ------------------------------------------------------------------------------------------------------

class DeskStore:
    def __init__(self, database: Database, clock: Clock, *, new_id: Callable[[str], str] | None = None) -> None:
        self.database, self.clock = database, clock
        self._new_id = new_id or (lambda prefix: f"{prefix}-{uuid.uuid4().hex}")
        self._ready = False

    def available(self) -> bool:
        """False on a database before migration 0052."""
        if not self._ready:
            with self.database.connect() as connection:
                self._ready = all(_has(connection, table) for table in TABLES)
        return self._ready

    def record_playbook(self, portfolio_id: str, revision: PlaybookRevision, *, wake_id: str | None,
                        task_id: str | None) -> PlaybookRecord:
        """Store a new revision after the portfolio's newest (append-only; the database keeps the chain)."""
        if not isinstance(revision, PlaybookRevision):
            raise ValidationFailure("a PlaybookRevision is required")
        document = revision.canonical_document()
        text = canonical_json(document)
        revision_id = self._new_id("pb")
        with self.database.immediate() as connection:
            newest = read_playbook(connection, portfolio_id)
            created = utc_iso(self.clock.now())
            if newest is not None and created < utc_iso(newest.created_at):
                created = utc_iso(newest.created_at)  # a clock that stepped back never reorders the chain
            connection.execute(
                """INSERT INTO desk_playbook_revisions (revision_id, portfolio_id, revision, previous_revision_id,
                   set_by, wake_id, task_id, why, document_json, document_sha256, created_at)
                   VALUES (?, ?, ?, ?, 'trader', ?, ?, ?, ?, ?, ?)""",
                (revision_id, portfolio_id, 1 if newest is None else newest.revision + 1,
                 None if newest is None else newest.revision_id, wake_id, task_id, revision.why, text, _sha256(text),
                 created))
            row = connection.execute("SELECT * FROM desk_playbook_revisions WHERE revision_id = ?",
                                     (revision_id,)).fetchone()
        return _playbook(row)

    def record_stances(self, portfolio_id: str, stances: Sequence[CoinStance], *, wake_id: str,
                       task_id: str | None, trading_day_start: datetime, shown: Mapping[str, CoinMarketType],
                       playbook_revision_id: str | None) -> list[StanceRecord]:
        """Store the stances of one answer, each with the label its wake showed for the coin (missing when the wake
        showed none). One per coin and wake."""
        try:
            one_stance_per_coin(tuple(stances))
        except ValueError as error:
            raise ValidationFailure(str(error)) from None
        created = utc_iso(self.clock.now())
        ids = []
        with self.database.immediate() as connection:
            for item in stances:
                label = shown.get(item.symbol)
                status = "missing" if label is None else label.status
                stance_id = self._new_id("st")
                connection.execute(
                    """INSERT INTO desk_stances (stance_id, portfolio_id, wake_id, task_id, trading_day_start, symbol,
                       stance, market_type_seen, label_status, label_market_type, label_id, strategy_ref,
                       news_refs_json, reason, playbook_revision_id, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (stance_id, portfolio_id, wake_id, task_id, utc_iso(trading_day_start), item.symbol,
                     item.stance, item.market_type_seen, status,
                     None if label is None else label.newest_label, None if label is None else label.label_id,
                     item.strategy_ref, json.dumps(list(item.news_refs)), item.reason, playbook_revision_id,
                     created))
                ids.append(stance_id)
            rows = connection.execute(
                f"SELECT * FROM desk_stances WHERE stance_id IN ({', '.join('?' for _ in ids)}) ORDER BY sequence",
                tuple(ids)).fetchall() if ids else []
        return [_stance(row) for row in rows]

    def record_day_plan(self, connection: sqlite3.Connection, *, plan_id: str, portfolio_id: str, symbol: str,
                        task_id: str | None, at: datetime) -> DayPlanDeskRecord | None:
        """Inside the plan's installation transaction: the coin's market type at ``at`` (its newest label, current or
        stale; missing when none), the playbook revision in force and the desk's stance on the coin in the answer
        that wrote the plan (``task_id``). Records, never checks: nothing here can refuse the plan (D280 item 5).
        Returns None on a database before 0052."""
        if not all(_has(connection, table) for table in TABLES):
            return None
        label = read_latest_market_types(connection, symbols=[symbol], as_of=at).get(symbol)
        shown = reading(symbol, label, at)
        playbook = read_playbook(connection, portfolio_id, as_of=at)
        stance = None
        if task_id is not None:
            stance = connection.execute(
                "SELECT stance_id, stance FROM desk_stances WHERE portfolio_id = ? AND task_id = ? AND symbol = ? "
                "ORDER BY sequence DESC LIMIT 1", (portfolio_id, task_id, symbol)).fetchone()
        connection.execute(
            """INSERT INTO day_plan_desk_records (plan_id, portfolio_id, symbol, label_status, market_type, label_id,
               labelled_at, rules_ref, playbook_revision_id, playbook_revision, stance_id, stance, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (plan_id, portfolio_id, symbol, shown.status, shown.newest_label, shown.label_id,
             _iso(shown.labelled_at), shown.rules_ref, None if playbook is None else playbook.revision_id,
             None if playbook is None else playbook.revision, None if stance is None else stance["stance_id"],
             None if stance is None else stance["stance"], utc_iso(at)))
        row = connection.execute("SELECT * FROM day_plan_desk_records WHERE plan_id = ?", (plan_id,)).fetchone()
        return _record(row)

    # --- reads through the service's database -------------------------------------------------------------------

    def playbook(self, portfolio_id: str, *, as_of: datetime | None = None) -> PlaybookRecord | None:
        with self.database.connect() as connection:
            return read_playbook(connection, portfolio_id, as_of=as_of)

    def stances(self, portfolio_id: str, **options: Any) -> list[StanceRecord]:
        with self.database.connect() as connection:
            return read_stances(connection, portfolio_id, **options)

    def day_plan_record(self, plan_id: str) -> DayPlanDeskRecord | None:
        with self.database.connect() as connection:
            return read_day_plan_desk_records(connection, plan_ids=[plan_id]).get(plan_id)


__all__ = ["TABLES", "DeskStore", "read_day_plan_desk_records", "read_latest_stances", "read_playbook",
           "read_playbook_revisions", "read_stances"]
