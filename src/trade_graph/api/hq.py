"""Trade Graph HQ read model: orchestration only (spec §B.1).

``snapshot()`` builds one ``trade-graph.hq.v1`` document (``api/hq_contract.py``)
inside a single database snapshot. It reads only bounded records (``hq_facts``) and
the 60-second money cache (``hq_money``); it never calls the activity, live or
health projections, the service controller, or a provider (spec §B.9).

Wiring (WP4, ``api/app.py``)::

    @app.get("/", response_class=HTMLResponse)
    def page(request: Request):
        identity(request)
        with runtime.database.snapshot():
            return render(request, "hq", hq.snapshot(runtime, hq.viewer_for(runtime, request)))

    hq.register(app, runtime, identity)   # GET /api/v1/hq, GET /api/v1/hq/story
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import HTTPException, Query, Request

from trade_graph.api import controls, hq_calendar, hq_facts, hq_money, hq_state, hq_story, live
from trade_graph.api.auth import csrf_for_token
from trade_graph.api.hq_contract import (
    CALENDAR_DAYS_DEFAULT,
    CALENDAR_DAYS_MAX,
    POLL_SECONDS,
    SCHEMA,
    STORY_MAX_ITEMS,
    STORY_WINDOW_HOURS,
    iso_ms,
)
from trade_graph.api.security import redact
from trade_graph.domain.clock import utc_iso

STORY_WINDOW = timedelta(hours=STORY_WINDOW_HOURS)
CALENDAR_PAST = timedelta(hours=48)
PROMPT_DEFAULT = timedelta(hours=24)
CalendarDays = Annotated[int, Query(ge=1, le=CALENDAR_DAYS_MAX)]
StoryLimit = Annotated[int, Query(ge=1, le=STORY_MAX_ITEMS)]


@dataclass(frozen=True)
class Viewer:
    role: str  # owner, leader or reader
    auth: str  # cookie or bearer
    csrf_present: bool


def viewer_for(runtime, request: Request) -> Viewer:
    """The authenticated viewer, as ``identity()`` left it on ``request.state`` (same CSRF rule as render())."""
    auth = request.state.auth_mechanism
    csrf = csrf_for_token(runtime.database, request.state.token) if auth == "cookie" else ""
    return Viewer(role=request.state.role, auth=auth, csrf_present=bool(csrf))


def _checkins(runtime, viewer: Viewer, now: datetime, calendar_days: int) -> dict:
    start, end = now - CALENDAR_PAST, now + timedelta(days=calendar_days)
    return hq_facts.checkins(runtime, iso_ms(start), iso_ms(end), owner=viewer.role == "owner")


def _projection(runtime, viewer: Viewer, now: datetime, calendar_days: int) -> tuple[hq_state.Projection, dict]:
    checkins = _checkins(runtime, viewer, now, calendar_days)
    last_done = hq_facts.parse(checkins.get("last_done_at"))
    facts = hq_facts.collect(runtime, now, story_start=now - STORY_WINDOW,
                             prompt_from=min(last_done or now, now - PROMPT_DEFAULT), owner=viewer.role == "owner")
    cache = hq_money.money(runtime, utc_iso(now))
    return hq_state.Projection(runtime, facts, cache, viewer, checkins), checkins


def snapshot(runtime, viewer: Viewer, *, calendar_days: int = CALENDAR_DAYS_DEFAULT) -> dict:
    """One HQ snapshot for ``viewer``. Callers redact it (``render()`` and the JSON route both do)."""
    if not 1 <= calendar_days <= CALENDAR_DAYS_MAX:
        raise ValueError("calendar_days must be between 1 and 14")
    with runtime.database.snapshot():
        now = runtime.clock.now()
        p, checkins = _projection(runtime, viewer, now, calendar_days)
        items = checkins["items"] if checkins["available"] else []
        attention = hq_state.attention(p, items)
        checkin_block = hq_state.checkins_block(p)
        barriers = []
        if p.can_act and p.profile != "RUNNING":
            barriers = controls._resume_barriers(live.projection_runtime(runtime))
        money = hq_state.money_block(p)
        story = hq_story.story(p, window_start=now - STORY_WINDOW, window_end=now + timedelta(milliseconds=1),
                               checkins=items)
        return {
            "schema": SCHEMA, "as_of": p.as_of, "poll_seconds": POLL_SECONDS, "mode": p.mode,
            "account": hq_state.account(p), "viewer": hq_state.viewer(p),
            "now": hq_state.now_block(p, attention), "team": hq_state.team(p), "attention": attention,
            "departments": hq_state.departments(p), "quests": hq_state.quests(p),
            "calendar": hq_calendar.calendar(p, calendar_days, items), "story": story, "money": money,
            "market": hq_state.market(p), "decisions": hq_state.decisions(p, attention, checkin_block, barriers),
            "checkins": checkin_block, "prompt_facts": hq_state.prompt_facts(p, attention, story["items"], money),
            "notices": hq_state.notices(p), "links": hq_state.links(p),
        }


def story_page(runtime, viewer: Viewer, *, before: str, limit: int = STORY_MAX_ITEMS) -> dict:
    """Earlier story items: the 72 hours before ``before`` (a timezone-aware ISO instant)."""
    end = hq_facts.parse(before)
    if end is None or not 1 <= limit <= STORY_MAX_ITEMS:
        raise ValueError("a timezone-aware 'before' instant and a limit of 1 to 60 are required")
    with runtime.database.snapshot():
        now = runtime.clock.now()
        end = min(end, now)
        start = end - STORY_WINDOW
        p, checkins = _projection(runtime, viewer, now, CALENDAR_DAYS_DEFAULT)
        p.use_story_inputs(hq_facts.story_before(runtime, start=start, before=end))
        page = hq_story.story(p, window_start=start, window_end=end,
                              checkins=checkins["items"] if checkins["available"] else [], limit=limit)
        return {"story": page}


def register(app, runtime, identity) -> None:
    """Add ``GET /api/v1/hq`` and ``GET /api/v1/hq/story`` (any signed-in role)."""

    @app.get("/api/v1/hq")
    def hq_snapshot(request: Request, calendar_days: CalendarDays = CALENDAR_DAYS_DEFAULT):
        identity(request)
        return redact(snapshot(runtime, viewer_for(runtime, request), calendar_days=calendar_days))

    @app.get("/api/v1/hq/story")
    def hq_story_page(request: Request, before: str, limit: StoryLimit = STORY_MAX_ITEMS):
        identity(request)
        try:
            return redact(story_page(runtime, viewer_for(runtime, request), before=before, limit=limit))
        except ValueError:
            raise HTTPException(422, "invalid story query") from None
