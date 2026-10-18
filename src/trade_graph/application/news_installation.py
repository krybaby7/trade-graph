"""The 5-minute news check next to the installed service (W9-A, C6; decisions D255-D259).

T44's shadow news alarm (``news_shadow.NewsAlarmShadow``) runs inside the installed subscription service, in the
protected parent process, in its own thread beside the price collector (D255). Every five minutes it reads the
owner-approved news feeds, stores each new headline before anything reads it, asks Jev the three fixed questions when
the owner has switched Jev calls on, and writes T44's verdicts into the installation's state database, where the event
engine's news router (T45) records whom each headline would wake. Nothing here places an order, wakes anyone, or
changes a setting, a money limit or the owner policy. Headlines are data, never instructions.

Four protected parts, all from the owner's root-owned folder or this package:

1. **Network** (D256). The owner's ``subscription-network-profile.json`` lists the reviewed route ``news_feeds``
   (``kernel/subscription_proxy.py``: the five feed hosts and ``api.typesafe.ai`` only) with its own proxy port. Feeds
   are fetched only through that port, only at addresses the profile admits, without environment proxies or a local
   DNS lookup; Jev is called only at its one official endpoint through the same port. Without the route nothing is
   fetched and a ``news_route`` activity says why.
2. **Settings and key** (D257). ``news-alarm.json`` in the owner folder (optional; a copy of
   ``config/news-alarm.example.json``) holds the feeds, the interval and the Jev switch ``jev.calls_enabled``; without
   it the recommended defaults apply and Jev stays off. ``typesafe.key`` in the owner folder, read through the
   protected owner-file reader, is read only when Jev is switched on and is sent only in the request header.
3. **Money and protected origins** (D258). Each Jev check reserves on the route's own deployment
   (``<deployment>:news_alarm``) under the owner policy's paid-call limits (``daily_paid_limit`` per UTC day,
   ``monthly_operating`` per UTC month), and its reservation gets an authenticated protected budget origin in the same
   transaction, before anything is sent; dispatch and settlement validate it, and a receipt seals it. Unknown usage
   stays unresolved at the full hold.
4. **Reporting** (D259). Each poll's outcome per feed is stored; a feed that cannot be read is reported as lost in a
   ``news_poll`` activity (when an outage starts and ends), never taken as "no news".
"""

from __future__ import annotations

import os
import sqlite3
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any, Final

from pydantic import ValidationError

from trade_graph.adapters.decisions.jev import (
    JEV_ENDPOINT,
    JEV_KEY_FILENAME,
    JEV_PRICE_CARD,
    JEV_PROVIDER,
    JevAdapter,
    JevKey,
    PinnedJevHttp,
)
from trade_graph.adapters.news.feeds import HttpxFeedFetcher
from trade_graph.adapters.persistence.db import atomic
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.jev_checks import PURPOSE, ROLE, JevBudgetBinding, JevCheckRunner
from trade_graph.application.news_shadow import NewsAlarmShadow, PollSummary
from trade_graph.application.news_store import NewsStore
from trade_graph.application.news_watchlist import service_watch_list
from trade_graph.contracts.models import PriceCard
from trade_graph.contracts.news import NewsAlarmSettings, WatchList
from trade_graph.domain.clock import Clock, parse_utc, utc_iso
from trade_graph.domain.errors import BudgetExhausted, StaleState, TradeGraphError
from trade_graph.kernel.protected_budget import ProtectedBudgetOrigins
from trade_graph.kernel.subscription_proxy import NEWS_FEEDS

ROUTE: Final = NEWS_FEEDS
SETTINGS_FILE: Final = "news-alarm.json"
KEY_FILE: Final = JEV_KEY_FILENAME
STALE_AFTER_DAYS: Final = 30  # as the command and the model router: a card verified more than 30 days ago is stale
ROUTE_KIND: Final = "news_route"
POLL_KIND: Final = "news_poll"
READ_OUTCOMES: Final = frozenset({"fetched", "not_modified"})
AUDIT_SECONDS: Final = 5
AUDIT_ROWS: Final = 500_000
AUDIT_SWEEP: Final = 500  # this month's reservations checked per poll, in turn (about 0.5 ms each)
_REASON_WORDS: Final = {
    "transport_error": "could not connect",
    "http_error": "the site answered with an error",
    "unreadable": "the answer was not a readable feed",
    "refused": "refused before anything was sent",
}
_CATEGORY_WORDS: Final = {
    "route_not_listed": "its address is not on your reviewed news route",
    "too_large": "the answer was too large",
    "unsafe_target": "its address is not a public one",
    "redirect": "the site answered with a redirect, which is not followed",
}


def route_deployment(deployment_id: str) -> str:
    """The deployment id of the news route's Jev reservations, apart from the subscription deployment's books."""
    return f"{deployment_id}:news_alarm"


# --- the owner's files ----------------------------------------------------------------------------------------------

def read_owner_settings(directory: Path) -> tuple[NewsAlarmSettings, bool]:
    """The owner's news alarm settings, or the recommended defaults (Jev off) when the owner installed none."""
    from trade_graph.kernel import deployment_image

    try:
        raw = deployment_image.read_owner_file(directory, SETTINGS_FILE, 262144)
    except FileNotFoundError:
        return NewsAlarmSettings(), False
    return NewsAlarmSettings.model_validate_json(raw), True


def read_owner_jev_key(directory: Path) -> JevKey:
    """The Jev key from the owner's protected folder (D257). The error never includes the file's content."""
    from trade_graph.kernel import deployment_image

    raw = deployment_image.read_owner_file(directory, KEY_FILE, 4096)
    try:
        info = os.stat(Path(directory) / KEY_FILE, follow_symlinks=False)
    except OSError:
        info = None  # the protected reader already opened it without following links
    if info is not None and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        raise PermissionError("the Jev key must be one regular file with a single name")
    text = raw[:-2] if raw.endswith(b"\r\n") else raw[:-1] if raw.endswith(b"\n") else raw
    if not 16 <= len(text) <= 4096 or any(byte < 33 or byte > 126 for byte in text):
        raise ValueError("the Jev key file must hold one key of 16 or more visible ASCII characters")
    return JevKey(text.decode("ascii"))


def card_problem(card: PriceCard, today: date) -> str | None:
    """Why a dated Jev card cannot price a real call today, or None. Stale is refused, never priced as free."""
    try:
        verified, effective = date.fromisoformat(card.verified_at), date.fromisoformat(card.effective_at)
    except ValueError:
        return f"the Jev price card {card.price_card_id} has unreadable dates"
    if card.provider != JEV_PROVIDER or card.endpoint != JEV_ENDPOINT or not card.source_id:
        return f"the Jev price card {card.price_card_id} does not name TypeSafe's official endpoint and its source"
    if effective > today or verified > today:
        return f"the Jev price card {card.price_card_id} is not effective yet"
    if (today - verified).days > STALE_AFTER_DAYS:
        return (f"the Jev price card {card.price_card_id} was last checked against TypeSafe's prices on "
                f"{verified.isoformat()}, more than {STALE_AFTER_DAYS} days ago; Jev calls are refused until a "
                "re-checked dated card is in the owner's news alarm settings")
    return None


def _fields(error: Exception) -> str:
    # Pydantic errors can quote the input; only the field names leave this function.
    if isinstance(error, ValidationError):
        return ", ".join(sorted({".".join(map(str, item["loc"])) or "document" for item in error.errors()}))[:200]
    return type(error).__name__


# --- the transports -------------------------------------------------------------------------------------------------

def pinned_feed_fetcher(profile) -> HttpxFeedFetcher:
    """The real feed fetcher: only through the reviewed route's port, only at addresses the owner's profile admits."""
    return HttpxFeedFetcher(proxy=profile.route_url(ROUTE), admits=lambda url: profile.route_admits(ROUTE, url))


def pinned_jev_transport(profile, *, timeout: int) -> PinnedJevHttp:
    """The real Jev transport: the one official endpoint, through the reviewed route's port, no environment proxy."""
    return PinnedJevHttp(timeout=timeout, proxy=profile.route_url(ROUTE), trust_env=False)


# --- money and protected origins ------------------------------------------------------------------------------------

class NewsRouteRefused(BudgetExhausted):
    """The news route refused a Jev reservation before anything was sent; T44 records it as ``budget_refused``."""


class NewsRouteBudget(BudgetGateway):
    """Reservations of the in-service Jev checks, each bound to an authenticated protected budget origin (D258).

    Admitted only for the route's own deployment, role, purpose and fresh dated card, without a task, while the
    protected controller runs. The owner policy's paid-call limits are the money rule; the deployment's EUR allowance,
    which a subscription installation does not use, is not consulted. Verification is per reservation, so it never
    grows with the history; ``audit`` checks the route's history, bounded."""

    def __init__(self, database, clock: Clock, *, deployment_id: str, price_card_id: str, portfolio_id: str,
                 origins: ProtectedBudgetOrigins, controller, authority) -> None:
        super().__init__(database, clock)
        if origins.deployment_id != deployment_id:
            raise ValueError("the news route's origins must be scoped to its own deployment")
        self.deployment_id, self.price_card_id, self.portfolio_id = deployment_id, price_card_id, portfolio_id
        self.origins, self.controller, self.authority = origins, controller, authority
        self._sweep_after = 0

    def controller_problem(self) -> str | None:
        try:
            status = self.controller.status()
        except Exception as error:  # noqa: BLE001 - an unreadable controller never admits a paid call
            return f"the protected controller cannot be read ({type(error).__name__})"
        if status.get("status") != "RUNNING":
            return "the protected controller is not running (management only); Jev checks wait until it runs"
        return None

    def admit(self, *, deployment_id: str, role: str, task_id: str | None, root_task_id: str | None,
              price_card_id: str, purpose: str, **_: Any) -> None:
        if (deployment_id != self.deployment_id or role != ROLE or purpose != PURPOSE
                or price_card_id != self.price_card_id or task_id is not None or root_task_id is not None):
            raise NewsRouteRefused("only the news route's own Jev checks may reserve on it")
        problem = card_problem(self.card(price_card_id), self.clock.now().date()) or self.controller_problem()
        if problem is not None:
            raise NewsRouteRefused(problem)

    @atomic
    def reserve(self, **kwargs: Any) -> str:
        self.admit(**kwargs)
        reservation_id = super().reserve(**kwargs)
        # The original authority is retained in the same transaction as the reservation and the attempt row.
        self.origins.record_origin(reservation_id, instance_id=self.controller.instance_id,
                                   portfolio_id=self.portfolio_id)
        return reservation_id

    def _assert_room(self, conn, *, deployment_id: str, amount, now: str, **_: Any) -> None:
        """The owner policy's paid-call limits over this route's real reservations: ``daily_paid_limit`` per UTC day
        and ``monthly_operating`` per UTC month. Held (unresolved) amounts count until they settle."""
        policy = self.authority.active_policy()
        room = None
        for limit, prefix in ((policy.daily_paid_limit, now[:10]), (policy.monthly_operating, now[:7])):
            if limit.currency != "EUR":
                raise NewsRouteRefused("the owner's paid-call limits must be EUR amounts")
            left = limit.amount - self._sum(conn, deployment_id, synthetic=0, prefix=prefix)
            room = left if room is None else min(room, left)
        if amount > room:
            raise NewsRouteRefused(f"the owner's paid-call limits leave EUR {room} for Jev checks; one check holds "
                                   f"EUR {amount}")

    def protect_dispatch(self, reservation_id: str) -> None:
        self.origins.validate(reservation_id)

    def protect_settlement(self, reservation_id: str) -> None:
        self.origins.validate(reservation_id)

    @atomic
    def commit(self, reservation_id, usage, **kwargs):
        self.origins.validate(reservation_id)
        receipt = super().commit(reservation_id, usage, **kwargs)
        self.origins.record_receipt(reservation_id)
        self.origins.validate(reservation_id, require_allocations=False)
        return receipt

    @atomic
    def allocate(self, receipt_id, weights) -> None:
        row = self.database.execute("SELECT reservation_id FROM usage_receipts WHERE receipt_id = ?",
                                    (receipt_id,)).fetchone()
        if row is None:
            raise StaleState("a news route allocation lacks its receipt")
        self.origins.validate(row["reservation_id"], require_allocations=False)
        super().allocate(receipt_id, weights)
        self.origins.validate(row["reservation_id"])

    def audit(self, *, since: str | None = None, sweep_since: str | None = None) -> None:
        """Check the route's history, bounded, without re-verifying all of it each time.

        Every route reservation has its origin and every origin its reservation (two indexed queries); the open
        reservations and those created ``since`` (today's, which today's limit sums) validate; and the next
        ``AUDIT_SWEEP`` reservations created ``sweep_since`` (this month's, which the monthly limit sums) validate in
        turn, so a changed amount anywhere in the month is found within a few polls. Bounded like the witness."""
        database = self.database
        if database.execute("""SELECT 1 FROM budget_reservations r WHERE r.deployment_id = ? AND NOT EXISTS (
                SELECT 1 FROM protected_financial_budget_origins o WHERE o.reservation_id = r.reservation_id
                AND o.event_kind = 'ORIGIN') LIMIT 1""", (self.deployment_id,)).fetchone():
            raise StaleState("a news route reservation has no protected origin")
        if database.execute("""SELECT 1 FROM protected_financial_budget_origins o LEFT JOIN budget_reservations r
                ON r.reservation_id = o.reservation_id WHERE o.deployment_id = ? AND (r.reservation_id IS NULL
                OR r.deployment_id != o.deployment_id) LIMIT 1""", (self.deployment_id,)).fetchone():
            raise StaleState("a news route's protected budget authority disappeared")
        clause, params = "state IN ('RESERVED', 'UNCERTAIN')", [self.deployment_id]
        if since is not None:
            clause, params = clause + " OR created_at >= ?", [self.deployment_id, since]
        rows = database.execute(f"SELECT rowid, reservation_id FROM budget_reservations WHERE deployment_id = ? "
                                f"AND ({clause}) ORDER BY rowid", params).fetchall()
        if sweep_since is not None:
            swept = database.execute("""SELECT rowid, reservation_id FROM budget_reservations WHERE deployment_id = ?
                AND created_at >= ? AND rowid > ? ORDER BY rowid LIMIT ?""",
                                     (self.deployment_id, sweep_since, self._sweep_after, AUDIT_SWEEP)).fetchall()
            self._sweep_after = swept[-1]["rowid"] if len(swept) == AUDIT_SWEEP else 0  # from the start once through
            rows = [*rows, *swept]
        started, count = monotonic(), 0
        for row in rows:
            count += 1
            if count > AUDIT_ROWS or monotonic() - started >= AUDIT_SECONDS:
                raise StaleState("the news route's protected budget audit exceeds its bound")
            self.origins.validate(row["reservation_id"])


def _day_start(now: datetime) -> str:
    return utc_iso(now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0))


def _month_start(now: datetime) -> str:
    return utc_iso(now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0))


def recover_interrupted(database, budget: BudgetGateway, store: NewsStore) -> int:
    """Attempts whose process stopped after the reservation: unresolved at the full hold, never free (T44)."""
    recovered = 0
    for attempt_id, reservation_id in store.unfinished_attempts():
        with database.immediate():
            budget.mark_uncertain(reservation_id)
            state = database.execute("SELECT state FROM budget_reservations WHERE reservation_id = ?",
                                     (reservation_id,)).fetchone()
            store.insert_outcome(attempt_id, status="interrupted", result=None, reservation_id=reservation_id,
                                 reservation_state=state["state"] if state is not None else None, receipt_id=None,
                                 latency_ms=None)
        recovered += 1
    return recovered


# --- the poll -------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class JevRoute:
    """Jev checks on the news route, or the plain reason they stay off (then nothing is sent to TypeSafe)."""

    checks: JevCheckRunner | None
    budget: NewsRouteBudget | None
    reason: str | None


def _lost_feed_words(database, feed_id: str, publisher: str) -> dict[str, Any]:
    row = database.execute("""SELECT outcome, http_status, error_category FROM news_feed_polls WHERE feed_id = ?
        ORDER BY started_at DESC, rowid DESC LIMIT 1""", (feed_id,)).fetchone()
    last = database.execute("""SELECT MAX(started_at) FROM news_feed_polls WHERE feed_id = ?
        AND outcome IN ('fetched', 'not_modified')""", (feed_id,)).fetchone()[0]
    outcome = row["outcome"] if row is not None else "unknown"
    words = _CATEGORY_WORDS.get(row["error_category"] if row is not None else "", _REASON_WORDS.get(outcome, outcome))
    if outcome == "http_error" and row["http_status"] is not None and row["error_category"] != "redirect":
        words = f"{words} (HTTP {row['http_status']})"
    return {"feed_id": feed_id, "publisher": publisher, "outcome": outcome,
            "error_category": row["error_category"] if row is not None else None, "why": words,
            "last_read_at_utc": last}


class ServiceNewsPoll:
    """The news check of the service process: call ``step`` from its own thread; it never touches the books.

    A poll is due ``poll_interval_seconds`` after the previous poll started (read from the stored polls at the first
    step, so a restart does not poll twice within the interval), by the injected clock; a step that is not due
    returns at once. A stop ends a poll between feeds and between headlines."""

    def __init__(self, *, database, clock: Clock, settings: NewsAlarmSettings, fetcher, store: NewsStore,
                 portfolio_id: str | None, jev: JevRoute, record: Callable[[dict[str, Any]], None] | None = None,
                 recovery_budget: BudgetGateway | None = None) -> None:
        self.database, self.clock, self.settings, self.fetcher, self.store = database, clock, settings, fetcher, store
        self.portfolio_id, self.jev, self.record = portfolio_id, jev, record
        self.recovery_budget = recovery_budget or BudgetGateway(database, clock)
        self.stop = threading.Event()
        self.polls = 0
        self._last_started: datetime | None = None
        self._loaded = False
        self._reported: tuple | None = None
        self._watch_trimmed = 0

    def request_stop(self) -> None:
        self.stop.set()

    def resume(self) -> None:
        self.stop.clear()

    def due(self, now: datetime) -> bool:
        if not self._loaded:
            row = self.database.execute("SELECT MAX(started_at) FROM news_feed_polls").fetchone()
            self._last_started = parse_utc(row[0]) if row is not None and row[0] else None
            self._loaded = True
        last = self._last_started
        # A clock that stepped back polls once and starts the interval again from there.
        return last is None or now < last or now >= last + timedelta(seconds=self.settings.poll_interval_seconds)

    def _watch(self) -> WatchList:
        watch, self._watch_trimmed = service_watch_list(self.database, self.clock, self.portfolio_id,
                                                        self.settings.extra_watch_coins)
        return watch

    def _jev_now(self, now: datetime) -> tuple[JevCheckRunner | None, str | None]:
        checks, budget = self.jev.checks, self.jev.budget
        if checks is None or budget is None:
            return None, self.jev.reason
        problem = card_problem(checks.card, now.date()) or budget.controller_problem()
        if problem is None:
            try:
                budget.audit(since=_day_start(now), sweep_since=_month_start(now))
            except (StaleState, sqlite3.DatabaseError) as error:
                problem = f"the news route's protected budget origins were refused ({error})"[:300]
        return (None, problem) if problem is not None else (checks, None)

    def step(self) -> dict[str, Any]:
        """Poll once when due and report it; ``idle`` when not due, ``stopped`` after a stop."""
        if self.stop.is_set():
            return {"status": "stopped"}
        now = self.clock.now()
        if not self.due(now):
            return {"status": "idle"}
        self._last_started = now
        recovered = recover_interrupted(self.database, self.jev.budget or self.recovery_budget, self.store)
        checks, reason = self._jev_now(now)
        shadow = NewsAlarmShadow(store=self.store, fetcher=self.fetcher, settings=self.settings, clock=self.clock,
                                 checks=checks, watch=self._watch, should_stop=self.stop.is_set)
        summary = shadow.poll_once()
        self.polls += 1
        return self._report(summary, reason, recovered)

    def _report(self, summary: PollSummary, jev_reason: str | None, recovered: int) -> dict[str, Any]:
        publishers = {feed.feed_id: feed.publisher for feed in self.settings.feeds}
        outcomes = {feed.feed_id: feed.outcome for feed in summary.feeds}
        lost = [_lost_feed_words(self.database, feed_id, publishers[feed_id])
                for feed_id, outcome in outcomes.items() if outcome not in READ_OUTCOMES]
        stopped = self.stop.is_set() and len(outcomes) < len(self.settings.feeds)
        status = ("stopped" if stopped else "failed" if lost and len(lost) == len(outcomes)
                  else "partial" if lost else "ok")
        payload = {"status": status, "polled_at_utc": summary.polled_at, "route": ROUTE, "feeds": outcomes,
                   "lost_feeds": lost, "new_headlines": summary.new_headlines,
                   "not_checked_old": summary.not_checked_old, "jev": summary.jev, "jev_reason": jev_reason,
                   "checks": summary.checks, "answered": summary.answered, "would_wake": summary.would_wake,
                   "failures": dict(summary.failures), "recovered": recovered + summary.recovered,
                   "watch_trimmed": self._watch_trimmed, "wakes_sent": 0, "note": self._note(status, lost, summary,
                                                                                           jev_reason)}
        key = (status, tuple(item["feed_id"] for item in lost), summary.jev, jev_reason)
        if self.record is not None and (key != self._reported or summary.would_wake or summary.failures
                                        or payload["recovered"]):
            self.record(payload)
        self._reported = key
        return payload

    @staticmethod
    def _note(status: str, lost: list[dict[str, Any]], summary: PollSummary, jev_reason: str | None) -> str:
        if status == "stopped":
            text = "The news check stopped with the service; what it had read is kept."
        elif lost:
            names = "; ".join(f"{item['publisher']} ({item['why']}"
                              + (f", last read {item['last_read_at_utc']}" if item["last_read_at_utc"]
                                 else ", not read yet") + ")" for item in lost)
            text = (f"The news check could not read {names}. A feed that cannot be read is reported, never taken as "
                    "\"no news\"; its headlines are unknown until it is read again.")
        else:
            text = f"The news check read every feed: {summary.new_headlines} new headlines."
        if summary.jev == "off":
            text += f" Jev is off: {jev_reason}." if jev_reason else " Jev is off."
        else:
            text += f" Jev checked {summary.checks}; {summary.would_wake} would wake someone (shadow: nobody woken)."
        return text[:1000]


# --- assembly -------------------------------------------------------------------------------------------------------

def _jev_route(runtime, protected, owner_directory: Path, profile, settings: NewsAlarmSettings,
               store: NewsStore) -> JevRoute:
    jev = settings.jev
    if not jev.calls_enabled:
        return JevRoute(None, None, "Jev calls are off in the owner's news alarm settings (jev.calls_enabled)")
    if not profile.route_admits(ROUTE, JEV_ENDPOINT):
        return JevRoute(None, None, "the reviewed news_feeds route does not list api.typesafe.ai")
    card = jev.price_card or JEV_PRICE_CARD
    if card.model != jev.model:
        return JevRoute(None, None, "the configured Jev version has no dated price card")
    clock = runtime.clock
    problem = card_problem(card, clock.now().date())
    if problem is not None:
        return JevRoute(None, None, problem)
    if protected is None:
        return JevRoute(None, None, "the protected runtime is not prepared")
    try:
        key = read_owner_jev_key(owner_directory)
    except FileNotFoundError:
        return JevRoute(None, None, f"no Jev key: the owner folder has no {KEY_FILE}")
    except (OSError, ValueError) as error:
        return JevRoute(None, None, f"the Jev key file is refused ({type(error).__name__})")
    database = runtime.database
    deployment = route_deployment(runtime.deployment_id)
    try:
        origins = ProtectedBudgetOrigins(protected.financial, deployment_id=deployment)
        budget = NewsRouteBudget(database, clock, deployment_id=deployment, price_card_id=card.price_card_id,
                                 portfolio_id=runtime.portfolio_id, origins=origins, controller=protected.controller,
                                 authority=protected.financial.authority)
        with database.immediate():
            budget.seed_card(card)
        budget.audit(since=_day_start(clock.now()), sweep_since=_month_start(clock.now()))
        transport = pinned_jev_transport(profile, timeout=jev.timeout_seconds)
        binding = JevBudgetBinding(deployment_id=deployment, price_card_id=card.price_card_id, fx_rate=jev.fx_rate,
                                   fx_buffer=jev.fx_buffer, synthetic=bool(getattr(transport, "synthetic", False)),
                                   fx_rate_id=jev.fx_rate_id, portfolio_id=runtime.portfolio_id,
                                   timeout_seconds=jev.timeout_seconds)
        checks = JevCheckRunner(database, clock, budget, JevAdapter(transport, key=key), binding, store)
    except (ValueError, TradeGraphError, sqlite3.DatabaseError) as error:
        return JevRoute(None, None, f"the news route's Jev checks are refused ({type(error).__name__})")
    return JevRoute(checks, budget, None)


def installed_news_poll(runtime, protected_owner: Path) -> ServiceNewsPoll | None:
    """The installed service's news check, built once at start (D255), or None with the reason recorded.

    It is built when the owner's network profile lists the reviewed ``news_feeds`` route; otherwise nothing is read
    or fetched and a ``news_route`` activity says why. Jev checks are added only when the owner switched them on and
    every prerequisite holds; otherwise headlines are collected and nothing is sent to TypeSafe. It never holds up
    the service's start: an unexpected error leaves the news check off with its type recorded."""

    def record_route(payload: dict[str, Any]) -> None:
        runtime.ledger._activity(runtime.portfolio_id, ROUTE_KIND, {"route": ROUTE, **payload})

    def not_ready(reason: str) -> None:
        record_route({"status": "not_ready", "reason": reason[:500]})

    try:
        return _installed_news_poll(runtime, protected_owner, record_route, not_ready)
    except Exception as error:  # noqa: BLE001 - the news check is never a reason the service cannot start
        try:
            not_ready(f"the news check could not be prepared ({type(error).__name__}); it is off until the next "
                      "start")
        except Exception:  # noqa: BLE001 - recording the reason is best effort here
            pass
        return None


def _installed_news_poll(runtime, protected_owner: Path, record_route, not_ready) -> ServiceNewsPoll | None:
    from trade_graph.kernel import subscription_network

    database, clock = runtime.database, runtime.clock
    try:
        profile = subscription_network.load_subscription_network_profile(protected_owner)
    except (OSError, ValueError, ValidationError) as error:
        not_ready(f"the owner's network profile can't be read ({type(error).__name__}); the news check is off")
        return None
    if ROUTE not in profile.reviewed_routes:
        not_ready("the owner's network profile lists no reviewed news_feeds route; the news check is off")
        return None
    try:
        settings, from_owner = read_owner_settings(protected_owner)
    except (OSError, ValueError, ArithmeticError) as error:
        not_ready(f"the owner's news alarm settings are refused ({_fields(error)}); the news check is off")
        return None
    admitted = [feed.feed_id for feed in settings.feeds if profile.route_admits(ROUTE, feed.url)]
    if not admitted:
        not_ready("the reviewed news_feeds route admits none of the configured feeds; the news check is off")
        return None
    protected = getattr(getattr(runtime, "protected_deployment", None), "protected", None)
    store = NewsStore(database, clock)
    jev = _jev_route(runtime, protected, protected_owner, profile, settings, store)
    fetcher = pinned_feed_fetcher(profile)
    portfolio_id = runtime.portfolio_id

    def record(payload: dict[str, Any]) -> None:
        runtime.ledger._activity(portfolio_id, POLL_KIND, payload)

    poll = ServiceNewsPoll(database=database, clock=clock, settings=settings, fetcher=fetcher, store=store,
                           portfolio_id=portfolio_id, jev=jev, record=record)
    record_route({"status": "ready", "reason": None, "settings": "owner" if from_owner else "recommended_defaults",
                  "feeds": admitted,
                  "feeds_not_on_route": [feed.feed_id for feed in settings.feeds if feed.feed_id not in admitted],
                  "poll_interval_seconds": settings.poll_interval_seconds,
                  "jev": "on" if jev.checks is not None else "off", "jev_reason": jev.reason,
                  "deployment_id": route_deployment(runtime.deployment_id) if jev.checks is not None else None,
                  "price_card_id": jev.checks.card.price_card_id if jev.checks is not None else None})
    return poll


__all__ = ["KEY_FILE", "POLL_KIND", "ROUTE", "ROUTE_KIND", "SETTINGS_FILE", "STALE_AFTER_DAYS", "JevRoute",
           "NewsRouteBudget", "NewsRouteRefused", "ServiceNewsPoll", "card_problem", "installed_news_poll",
           "pinned_feed_fetcher", "pinned_jev_transport", "read_owner_jev_key", "read_owner_settings",
           "recover_interrupted", "route_deployment"]
