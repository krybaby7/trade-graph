"""The owner's switch between shadow and live news wake-ups (T45; D137, D138 item 10, D227).

News alarms ship in **shadow** mode: the event engine records whom each would-wake alarm would wake and why, and
wakes nobody (``application/news_wakes.py``). Only the owner turns real wake-ups on, after step L3, from shadow
evidence: ``trade-graph news-wakes --live on --confirm`` records a new owner-policy revision with
``news_wakes: "live"`` (``--live off`` goes back to shadow). In the style of ``money-limits``: a preview by default,
refused while the paper service runs, revision-checked against the revision the preview showed, owner-only, an audit
row naming the revision it replaced, both hashes, the change and the evidence the owner saw; no earlier revision is
rewritten.

Going live is decided from evidence, not from fixtures: ``shadow_evidence`` counts T44's stored headlines, Jev's
answers and would-wake verdicts, the shadow and live routes and the wakes the news alarm caused (HQ's wake counts),
and how many of them come from a live public feed with a real (not scripted) Jev answer. Without at least one such
real shadow route, switching to live is refused. Going back to shadow needs no evidence. Shadow logging continues
either way. Paper only: this changes no order, money limit, paid-call or live-trading setting.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from trade_graph.application.authority import AuthorityRecord
from trade_graph.contracts.models import OwnerPolicy
from trade_graph.contracts.news_wakes import DEFAULT_MODE, NEWS_WAKE_MODES, NewsWakeMode
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState, ValidationFailure

COMMAND: Final = "news-wakes"
SERVICE_LEASES: Final = ("paper-service", "role-worker")
ROUTED: Final = ("leader", "trader")
NO_REAL_EVIDENCE: Final = (
    "going live is decided from shadow evidence: no shadow wake-up has yet come from a live public feed with a real "
    "Jev answer (only synthetic or fixture records so far); keep shadow mode, look at the shadow log and HQ's wake "
    "counts after step L3, then confirm")


@dataclass(frozen=True, slots=True)
class ShadowEvidence:
    """What the owner decides from: T44's log, the routes and the wakes news alarms caused, with what is real."""

    since: datetime | None
    until: datetime | None
    headlines: int
    checked: int
    would_wake: int
    routes: dict[str, dict[str, int]]
    real: dict[str, int]
    news_wakes: dict[str, int]
    wakes: dict[str, int]

    def document(self) -> dict[str, Any]:
        return {"since": utc_iso(self.since) if self.since else None,
                "until": utc_iso(self.until) if self.until else None, "headlines": self.headlines,
                "checked": self.checked, "would_wake": self.would_wake, "routes": self.routes, "real": self.real,
                "news_wakes": self.news_wakes, "wakes": self.wakes,
                "report": "trade-graph news-alarm-report lists each headline, Jev's answers and the would-be alarms"}


def _tables(source) -> set[str]:
    return {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}


def shadow_evidence(source, portfolio_id: str | None, *, since: datetime | None = None,
                    until: datetime | None = None) -> ShadowEvidence:
    """Counts from saved records only (any ``sqlite3`` connection or ``Database``); reading starts nothing."""
    tables = _tables(source)
    low, high = (utc_iso(since) if since else ""), (utc_iso(until) if until else "9999")

    def count(sql: str, *params: Any) -> int:
        return int(source.execute(sql, params).fetchone()[0] or 0)

    headlines = checked = would_wake = real_headlines = real_would_wake = 0
    if {"news_headlines", "news_jev_outcomes", "news_jev_attempts", "news_alarm_verdicts"} <= tables:
        headlines = count("SELECT COUNT(*) FROM news_headlines WHERE received_at >= ? AND received_at < ?", low, high)
        real_headlines = count("""SELECT COUNT(*) FROM news_headlines WHERE evidence = 'live_public'
                                  AND received_at >= ? AND received_at < ?""", low, high)
        checked = count("""SELECT COUNT(*) FROM news_jev_outcomes WHERE status = 'answered'
                           AND finished_at >= ? AND finished_at < ?""", low, high)
        would_wake = count("""SELECT COUNT(*) FROM news_alarm_verdicts WHERE would_wake = 1
                              AND created_at >= ? AND created_at < ?""", low, high)
        real_would_wake = count(
            """SELECT COUNT(*) FROM news_alarm_verdicts v JOIN news_headlines h ON h.headline_id = v.headline_id
               JOIN news_jev_attempts a ON a.attempt_id = v.attempt_id WHERE v.would_wake = 1
               AND h.evidence = 'live_public' AND a.synthetic = 0 AND v.created_at >= ? AND v.created_at < ?""",
            low, high)
    routes = {mode: dict.fromkeys(ROUTED, 0) for mode in NEWS_WAKE_MODES}
    real_routes = 0
    if "news_alarm_wake_routes" in tables:
        for mode, department, number in source.execute(
                """SELECT mode, department, COUNT(*) FROM news_alarm_wake_routes WHERE (? IS NULL OR portfolio_id = ?)
                   AND alarm_at >= ? AND alarm_at < ? GROUP BY mode, department""",
                (portfolio_id, portfolio_id, low, high)).fetchall():
            routes[mode][department] = int(number)
        real_routes = count(
            """SELECT COUNT(*) FROM news_alarm_wake_routes r JOIN news_headlines h ON h.headline_id = r.headline_id
               JOIN news_alarm_verdicts v ON v.verdict_id = r.verdict_id
               JOIN news_jev_attempts a ON a.attempt_id = v.attempt_id
               WHERE r.mode = 'shadow' AND h.evidence = 'live_public' AND a.synthetic = 0
               AND (? IS NULL OR r.portfolio_id = ?) AND r.alarm_at >= ? AND r.alarm_at < ?""",
            portfolio_id, portfolio_id, low, high)
    news_wakes = dict.fromkeys(ROUTED, 0)
    wakes: dict[str, int] = {}
    if {"wakes", "wake_triggers"} <= tables:
        for department, number in source.execute(
                """SELECT w.department, COUNT(DISTINCT w.wake_id) FROM wakes w JOIN wake_triggers t
                   ON t.wake_id = w.wake_id WHERE t.kind = 'news_alarm' AND (? IS NULL OR w.portfolio_id = ?)
                   AND w.created_at >= ? AND w.created_at < ? GROUP BY w.department""",
                (portfolio_id, portfolio_id, low, high)).fetchall():
            news_wakes[department] = int(number)
        wakes = {department: int(number) for department, number in source.execute(
            """SELECT department, COUNT(*) FROM wakes WHERE (? IS NULL OR portfolio_id = ?) AND created_at >= ?
               AND created_at < ? GROUP BY department ORDER BY department""",
            (portfolio_id, portfolio_id, low, high)).fetchall()}
    return ShadowEvidence(since=since, until=until, headlines=headlines, checked=checked, would_wake=would_wake,
                          routes=routes, real={"headlines": real_headlines, "would_wake": real_would_wake,
                                               "shadow_routes": real_routes},
                          news_wakes=news_wakes, wakes=wakes)


@dataclass(frozen=True, slots=True)
class NewsWakeProposal:
    """What the command shows or recorded: the revision in force, the new one (None: nothing changes), the evidence."""

    current: OwnerPolicy
    policy: OwnerPolicy | None
    mode_from: NewsWakeMode
    mode_to: NewsWakeMode
    evidence: ShadowEvidence
    refusal: str | None  # why a confirm would be refused, or None

    @property
    def changes(self) -> dict[str, dict[str, Any]]:
        if self.policy is None:
            return {}
        return {"news_wakes": {"from": self.current.news_wakes, "to": self.policy.news_wakes}}


def _latest_paper_portfolio(database) -> str | None:
    row = database.execute(
        "SELECT portfolio_id FROM portfolios WHERE mode = 'paper' ORDER BY created_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    return None if row is None else str(row[0])


def propose_news_wakes(record: AuthorityRecord, *, mode: str, revision_id: str, portfolio_id: str | None = None,
                       since: datetime | None = None, until: datetime | None = None) -> NewsWakeProposal:
    """The owner-policy revision that would set ``mode``, the evidence and whether a confirm is refused; writes
    nothing."""
    if mode not in NEWS_WAKE_MODES:
        raise ValidationFailure(f"news wake-ups are {' or '.join(NEWS_WAKE_MODES)}")
    current = record.active_policy()
    before: NewsWakeMode = current.news_wakes or DEFAULT_MODE
    portfolio = portfolio_id or _latest_paper_portfolio(record.database)
    evidence = shadow_evidence(record.database, portfolio, since=since, until=until)
    policy = None
    if mode != before:
        policy = OwnerPolicy.model_validate({**current.model_dump(), "revision_id": revision_id, "news_wakes": mode})
    # The gate reads all the shadow evidence; ``since``/``until`` only narrow what the preview counts.
    gate = evidence if since is None and until is None else shadow_evidence(record.database, portfolio)
    refusal = NO_REAL_EVIDENCE if mode == "live" and before != "live" and gate.real["shadow_routes"] < 1 else None
    return NewsWakeProposal(current, policy, before, mode, evidence, refusal)  # type: ignore[arg-type]


def _service_running(database, now: datetime) -> bool:
    marks = ",".join("?" for _ in SERVICE_LEASES)
    return database.execute(f"SELECT 1 FROM process_leases WHERE lease_name IN ({marks}) AND expires_at > ?",
                            (*SERVICE_LEASES, utc_iso(now))).fetchone() is not None


def record_news_wakes(record: AuthorityRecord, *, mode: str, revision_id: str, portfolio_id: str | None = None,
                      since: datetime | None = None, until: datetime | None = None,
                      expect_revision: str | None = None, role: str = "owner") -> NewsWakeProposal:
    """Install the owner's revision and its audit row in one transaction, or nothing when it changes nothing."""
    if role != "owner":
        raise AuthorityDenied("only the owner switches news wake-ups between shadow and live")
    database = record.database
    with database.immediate():
        if _service_running(database, record.clock.now()):
            raise StaleState("the paper service is running; stop it before confirming")
        current = record.active_policy()
        if expect_revision is not None and current.revision_id != expect_revision:
            raise StaleState(f"the owner policy changed since the preview: revision {current.revision_id} is active "
                             "now")
        proposal = propose_news_wakes(record, mode=mode, revision_id=revision_id, portfolio_id=portfolio_id,
                                      since=since, until=until)
        if proposal.refusal is not None:
            raise AuthorityDenied(proposal.refusal)
        if proposal.policy is None:
            return proposal
        record.install_policy(proposal.policy, role="owner")
        previous_sha, new_sha = (database.execute(
            "SELECT content_hash FROM owner_policy_revisions WHERE revision_id = ?", (ref,)).fetchone()[0]
            for ref in (current.revision_id, proposal.policy.revision_id))
        database.execute(
            """INSERT INTO owner_policy_audit (revision_id, previous_revision_id, previous_sha256, revision_sha256,
               command, changes_json, set_by, created_at) VALUES (?, ?, ?, ?, ?, ?, 'owner', ?)""",
            (proposal.policy.revision_id, current.revision_id, previous_sha, new_sha, COMMAND,
             json.dumps({"changes": proposal.changes, "evidence": proposal.evidence.document()}, sort_keys=True),
             utc_iso(record.clock.now())))
    return proposal


__all__ = ["COMMAND", "NO_REAL_EVIDENCE", "NewsWakeProposal", "ShadowEvidence", "propose_news_wakes",
           "record_news_wakes", "shadow_evidence"]
