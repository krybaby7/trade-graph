"""Owner review check-ins: an owner-only, append-only store and its projection (spec §B.10).

The owner plans a time to review the run with a separate chat, may move it, and closes
it with ``done``, ``skip`` or ``cancel``. Each request appends one ``owner_checkins`` row
(migration 0025). Replaying a ``request_id`` with the same canonical body returns the
current check-in with ``replayed: true``; a different body is a conflict.

Recording never touches ``dashboard_control_state``, ``dashboard_commands`` or
``activity_events`` and never bumps the owner revision. Only the owner's HQ snapshot and
the routes below read the table: not the review export, Secretary routing, or any agent
context. A module boundary does not stop a compromised process sharing the SQLite file;
that residual risk is accepted for non-financial owner notes.

Non-terminal states are derived at read time from ``runtime.clock``: ``planned`` until
15 min before ``planned_at``, ``due`` until 60 min after, ``overdue`` until 6 h after,
then ``missed``. Stored times are ``utc_iso``; returned times are ``hq_contract.iso_ms``.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Final

from fastapi import HTTPException, Query, Request
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from trade_graph.api.hq_contract import (
    CHECKIN_BODY_MAX_BYTES,
    CHECKIN_DUE_AFTER_SECONDS,
    CHECKIN_DUE_BEFORE_SECONDS,
    CHECKIN_MISSED_AFTER_SECONDS,
    CHECKIN_NOTE_MAX_CHARS,
    CHECKIN_PLAN_FUTURE_DAYS,
    CHECKIN_PLAN_PAST_DAYS,
    CHECKIN_QUERY_DEFAULT_FUTURE_DAYS,
    CHECKIN_QUERY_DEFAULT_PAST_DAYS,
    CHECKIN_QUERY_MAX_DAYS,
    CHECKIN_TITLE_MAX_CHARS,
    CHECKIN_WINDOW_MAX_ITEMS,
    IDENTIFIER_PATTERN,
    CheckinAction,
    CheckinPromptKind,
    iso_ms,
)
from trade_graph.api.security import redact
from trade_graph.domain.clock import parse_utc, utc_iso

PATH: Final = "/api/v1/owner/checkins"
_TRIGGER_CONFLICTS: Final = frozenset({"unknown check-in", "check-in already planned", "check-in already closed"})
_INSTANT_MAX_CHARS: Final = 64


class CheckinConflict(ValueError):
    """The request_id was used for a different change, or the check-in's history refuses this action."""


def _safe_identifier(value: str) -> str:
    if redact(value) != value:
        raise ValueError("use a safe opaque identifier")
    return value


def _safe_text(value: str) -> str:
    # Same rule as owner bill labels (owner_expenses.py): no paths, secrets or control characters.
    if redact(value) != value or any(ord(character) < 32 for character in value):
        raise ValueError("use plain text without paths, secrets or control characters")
    return value


def _stripped(value: str) -> str:
    if value.strip() != value:
        raise ValueError("titles have no leading or trailing spaces")
    return value


def _aware(value: str) -> datetime:
    """A timezone-aware ISO-8601 instant, in UTC. Naive or unparseable values are refused."""
    if type(value) is not str or len(value) > _INSTANT_MAX_CHARS:
        raise ValueError("expected an ISO-8601 instant")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("instants need a timezone")
    try:
        return parsed.astimezone(UTC)
    except OverflowError:
        raise ValueError("instant out of range") from None


def _instant(value: str) -> str:
    return utc_iso(_aware(value))


Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=IDENTIFIER_PATTERN),
                       AfterValidator(_safe_identifier)]
Instant = Annotated[str, AfterValidator(_instant)]
Title = Annotated[str, Field(min_length=1, max_length=CHECKIN_TITLE_MAX_CHARS), AfterValidator(_safe_text),
                  AfterValidator(_stripped)]
Note = Annotated[str, Field(min_length=1, max_length=CHECKIN_NOTE_MAX_CHARS), AfterValidator(_safe_text)]


class CheckinCommand(BaseModel):
    """One owner request. Field presence per action mirrors the table's CHECK constraints."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    request_id: Identifier
    checkin_id: Identifier
    action: CheckinAction
    planned_at: Instant | None = None
    prompt_kind: CheckinPromptKind | None = None
    title: Title | None = None
    note: Note | None = None

    @field_validator("note", mode="before")
    @classmethod
    def _empty_note_is_no_note(cls, value):
        return None if value == "" else value

    @model_validator(mode="after")
    def _fields_for_action(self):
        if (self.action in ("plan", "reschedule")) != (self.planned_at is not None):
            raise ValueError("plan and reschedule, and only they, carry planned_at")
        if self.action == "plan" and (self.prompt_kind is None or self.title is None):
            raise ValueError("a plan names its prompt kind and title")
        if self.action != "plan" and (self.prompt_kind is not None or self.title is not None):
            raise ValueError("only a plan sets the prompt kind and title")
        if self.note is not None and self.action not in ("done", "skip"):
            raise ValueError("only done or skip carry a note")
        return self


def _deployment(runtime) -> str:
    return getattr(runtime, "deployment_id", "deployment")


def _rows(runtime, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(row) for row in runtime.database.execute(sql, params).fetchall()]


def available(runtime) -> bool:
    """True when the check-in table exists (migration 0025 applied)."""
    return runtime.database.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='owner_checkins'").fetchone() is not None


def _state(planned_at: str, now: datetime) -> str:
    offset = now - parse_utc(planned_at)
    if offset < -timedelta(seconds=CHECKIN_DUE_BEFORE_SECONDS):
        return "planned"
    if offset < timedelta(seconds=CHECKIN_DUE_AFTER_SECONDS):
        return "due"
    if offset < timedelta(seconds=CHECKIN_MISSED_AFTER_SECONDS):
        return "overdue"
    return "missed"


def _project(entries: list[dict], now: datetime) -> dict:
    """Fold one check-in's rows, in ``sequence`` order, into a contract ``Checkin``."""
    plan = next(entry for entry in entries if entry["action"] == "plan")
    planned_at, closed, done_at, note, history = plan["planned_at"], None, None, None, []
    for entry in entries:
        action = entry["action"]
        history.append({"action": action, "at": iso_ms(entry["created_at"]),
                        "planned_at": None if entry["planned_at"] is None else iso_ms(entry["planned_at"])})
        if action in ("plan", "reschedule"):
            planned_at = entry["planned_at"]
        elif action == "done":
            closed, done_at, note = "done", iso_ms(entry["created_at"]), entry["note"]
        elif action == "skip":
            closed, note = "skipped", entry["note"]
        else:
            closed = "cancelled"
    return {"checkin_id": plan["checkin_id"], "title": plan["title"], "prompt_kind": plan["prompt_kind"],
            "planned_at": iso_ms(planned_at), "state": closed or _state(planned_at, now), "done_at": done_at,
            "note": note, "history": history}


def _checkin(runtime, checkin_id: str, now: datetime) -> dict:
    entries = _rows(runtime, "SELECT * FROM owner_checkins WHERE deployment_id=? AND checkin_id=? ORDER BY sequence",
                    (_deployment(runtime), checkin_id))
    return _project(entries, now)


def _window(runtime, start: datetime, end: datetime, now: datetime) -> list[dict]:
    deployment = _deployment(runtime)
    entries = _rows(runtime, """WITH current AS (
            SELECT item.checkin_id, item.planned_at AS current_at, item.sequence AS current_sequence
            FROM owner_checkins AS item
            WHERE item.deployment_id=? AND item.action IN ('plan','reschedule')
              AND item.sequence=(SELECT MAX(later.sequence) FROM owner_checkins AS later
                                 WHERE later.deployment_id=item.deployment_id AND later.checkin_id=item.checkin_id
                                   AND later.action IN ('plan','reschedule'))
              AND item.planned_at>=? AND item.planned_at<?
            ORDER BY item.planned_at, item.sequence LIMIT ?)
        SELECT entry.* FROM current JOIN owner_checkins AS entry
          ON entry.deployment_id=? AND entry.checkin_id=current.checkin_id
        ORDER BY current.current_at, current.current_sequence, entry.sequence""",
        (deployment, utc_iso(start), utc_iso(end), CHECKIN_WINDOW_MAX_ITEMS, deployment))
    grouped: dict[str, list[dict]] = {}
    for entry in entries:
        grouped.setdefault(entry["checkin_id"], []).append(entry)
    return [_project(rows, now) for rows in grouped.values()]


def window(runtime, start: str, end: str) -> list[dict]:
    """Check-ins whose current ``planned_at`` is in ``[start, end)``: at most 200, soonest first.

    ``start``/``end`` are timezone-aware ISO-8601 strings. States are derived at
    ``runtime.clock.now()``. Raises when the table is absent; check :func:`available` first.
    """
    low, high = _aware(start), _aware(end)
    if high < low:
        raise ValueError("the check-in window ends before it starts")
    return _window(runtime, low, high, runtime.clock.now())


def last_done_at(runtime) -> str | None:
    """``done_at`` of the most recently recorded ``done`` check-in, or None when there is none."""
    row = runtime.database.execute(
        "SELECT created_at FROM owner_checkins WHERE deployment_id=? AND action='done' ORDER BY sequence DESC LIMIT 1",
        (_deployment(runtime),)).fetchone()
    return None if row is None else iso_ms(row["created_at"])


def parse(body: dict) -> CheckinCommand:
    if type(body) is not dict:
        raise ValueError("a check-in request is an object")
    if len(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()) > CHECKIN_BODY_MAX_BYTES:
        raise ValueError("check-in request too large")
    return CheckinCommand.model_validate(body)


def record(runtime, body: dict) -> dict:
    """Append one owner request, or answer its exact replay: ``{"checkin": Checkin, "replayed": bool}``."""
    command = parse(body)
    serialized = json.dumps(command.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    deployment = _deployment(runtime)
    now = runtime.clock.now()
    with runtime.database.immediate():
        prior = _rows(runtime, "SELECT document_json FROM owner_checkins WHERE deployment_id=? AND request_id=?",
                      (deployment, command.request_id))
        if prior:
            if prior[0]["document_json"] != serialized:
                raise CheckinConflict("request_id already used for a different check-in change")
            # A replay is answered even after its planned time left the planning window.
            return {"checkin": _checkin(runtime, command.checkin_id, now), "replayed": True}
        if command.planned_at is not None:
            offset = parse_utc(command.planned_at) - now
            if not -timedelta(days=CHECKIN_PLAN_PAST_DAYS) <= offset <= timedelta(days=CHECKIN_PLAN_FUTURE_DAYS):
                raise ValueError("check-ins are planned from one day ago to sixty days ahead")
        try:
            runtime.database.execute("""INSERT INTO owner_checkins
                (record_id,deployment_id,request_id,checkin_id,action,planned_at,prompt_kind,title,note,
                 document_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), deployment, command.request_id, command.checkin_id, command.action,
                 command.planned_at, command.prompt_kind, command.title, command.note, serialized, utc_iso(now)))
        except sqlite3.IntegrityError as exc:
            reason = str(exc)
            raise CheckinConflict(reason if reason in _TRIGGER_CONFLICTS
                                  else "check-in change conflicts with its history") from None
        return {"checkin": _checkin(runtime, command.checkin_id, now), "replayed": False}


def _query_period(now: datetime, start: str | None, end: str | None) -> tuple[datetime, datetime]:
    low = _aware(start) if start is not None else now - timedelta(days=CHECKIN_QUERY_DEFAULT_PAST_DAYS)
    high = _aware(end) if end is not None else now + timedelta(days=CHECKIN_QUERY_DEFAULT_FUTURE_DAYS)
    if high < low or high - low > timedelta(days=CHECKIN_QUERY_MAX_DAYS):
        raise ValueError("check-in queries span at most 120 days")
    return low, high


def _require_store(runtime) -> None:
    if not available(runtime):
        raise HTTPException(503, "check-ins are unavailable until the database is upgraded")


async def _bounded_body(request: Request) -> bytes:
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > CHECKIN_BODY_MAX_BYTES:
            raise ValueError("check-in request too large")
        chunks.append(chunk)
    return b"".join(chunks)


def register(app, runtime, identity, owner_write) -> None:
    """Add ``GET`` and ``POST /api/v1/owner/checkins``. Both need owner authority.

    ``identity`` is accepted for the shared ``register`` signature; ``owner_write`` already
    authenticates and, for cookie sessions writing, checks ``X-CSRF-Token``.
    """

    @app.get(PATH)
    def checkins(request: Request, start: Annotated[str | None, Query(alias="from")] = None,
                 end: Annotated[str | None, Query(alias="to")] = None):
        owner_write(request)
        with runtime.database.snapshot():
            _require_store(runtime)
            now = runtime.clock.now()
            try:
                low, high = _query_period(now, start, end)
                return redact({"as_of": iso_ms(now), "checkins": _window(runtime, low, high, now)})
            except (ValueError, TypeError, ArithmeticError):
                # Never echo the submitted parameters.
                raise HTTPException(422, "invalid check-in query") from None

    @app.post(PATH)
    async def record_checkin(request: Request):
        owner_write(request)
        _require_store(runtime)
        try:
            return redact(record(runtime, json.loads(await _bounded_body(request))))
        except CheckinConflict as exc:
            raise HTTPException(409, str(exc)) from None
        except (ValueError, TypeError, ArithmeticError, RecursionError):
            # Validation messages can contain the submitted text.
            raise HTTPException(422, "invalid check-in request") from None
