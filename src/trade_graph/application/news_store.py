"""Append-only storage for the shadow news alarm (migration 0026).

Every feed request is a poll row. Each headline is stored once, by canonical link, before Jev reads it.
An attempt row is written with its budget reservation before the call, and the reply is stored as received.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from trade_graph.adapters.news.feeds import FetchResult, ParsedFeed
from trade_graph.contracts.decisions import RETRYABLE_FAILURES, DecisionResult, exact_json
from trade_graph.contracts.news import FeedSource, Headline, WatchList, WouldWakeVerdict
from trade_graph.domain.clock import Clock, parse_utc, utc_iso

# Statuses after which a headline is not asked again. Retryable failures are asked again on a later poll.
FINAL_STATUSES = ("answered", "unexpected_model", "malformed_answers", "refused_response")


@dataclass(frozen=True)
class PollRecord:
    poll_id: str
    feed_id: str
    outcome: str
    new: tuple[Headline, ...]
    repeats: int
    rejected: int


def reference_time(headline: Headline) -> datetime:
    """Publication time when the feed gives a plausible one, else the receipt time."""
    published = headline.published_at_utc
    return published if published is not None and published <= headline.received_at_utc else (
        headline.received_at_utc)


def _headline(row) -> Headline:
    return Headline(headline_id=row["headline_id"], feed_id=row["feed_id"], publisher=row["publisher"],
                    title=row["title"], link=row["link"],
                    published_at_utc=parse_utc(row["published_at"]) if row["published_at"] else None,
                    received_at_utc=parse_utc(row["received_at"]), item_sha256=row["item_sha256"],
                    evidence=row["evidence"])


class NewsStore:
    def __init__(self, database, clock: Clock) -> None:
        self.database, self.clock = database, clock

    def validators(self, feed_id: str) -> tuple[str | None, str | None]:
        row = self.database.execute(
            """SELECT etag, last_modified FROM news_feed_polls WHERE feed_id = ? AND outcome = 'fetched'
            ORDER BY started_at DESC, rowid DESC LIMIT 1""", (feed_id,)).fetchone()
        return (row["etag"], row["last_modified"]) if row is not None else (None, None)

    def record_poll(self, feed: FeedSource, fetched: FetchResult, parsed: ParsedFeed | None, *,
                    started_at: datetime) -> PollRecord:
        """One poll row and every headline not seen before, in one transaction."""
        received = self.clock.now()
        outcome = "unreadable" if fetched.outcome == "fetched" and parsed is None else fetched.outcome
        items = parsed.items if parsed is not None else ()
        poll_id = uuid.uuid4().hex
        new: list[Headline] = []
        repeats = 0
        with self.database.immediate() as connection:
            seen: set[str] = set()
            for item in items:
                if item.link in seen or connection.execute("SELECT 1 FROM news_headlines WHERE link = ?",
                                                           (item.link,)).fetchone():
                    repeats += 1
                    continue
                seen.add(item.link)
                new.append(Headline(headline_id=uuid.uuid4().hex, feed_id=feed.feed_id, publisher=feed.publisher,
                                    title=item.title, link=item.link, published_at_utc=item.published_at_utc,
                                    received_at_utc=received, item_sha256=item.item_sha256,
                                    evidence=fetched.evidence))
            body = fetched.body
            connection.execute(
                """INSERT INTO news_feed_polls (poll_id, feed_id, url, publisher, started_at, finished_at, outcome,
                http_status, error_category, evidence, body_sha256, body_bytes, items_seen, items_new, items_repeat,
                items_rejected, etag, last_modified) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (poll_id, feed.feed_id, feed.url, feed.publisher, utc_iso(started_at), utc_iso(received), outcome,
                 fetched.http_status, fetched.error_category or ("unreadable" if outcome == "unreadable" else None),
                 fetched.evidence, hashlib.sha256(body).hexdigest() if body is not None else None,
                 len(body) if body is not None else None, len(items), len(new), repeats,
                 parsed.rejected if parsed is not None else 0, fetched.etag, fetched.last_modified))
            for headline in new:
                connection.execute(
                    """INSERT INTO news_headlines (headline_id, link, feed_id, publisher, title, published_at,
                    received_at, item_sha256, evidence, poll_id) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (headline.headline_id, headline.link, headline.feed_id, headline.publisher, headline.title,
                     utc_iso(headline.published_at_utc) if headline.published_at_utc else None,
                     utc_iso(headline.received_at_utc), headline.item_sha256, headline.evidence, poll_id))
        return PollRecord(poll_id=poll_id, feed_id=feed.feed_id, outcome=outcome, new=tuple(new), repeats=repeats,
                          rejected=parsed.rejected if parsed is not None else 0)

    def due_for_check(self, now: datetime, *, window_seconds: int, max_attempts: int) -> list[Headline]:
        """Recent headlines not yet answered, below the attempt setting and with no call still open."""
        cutoff = now - timedelta(seconds=window_seconds)
        retryable = sorted(RETRYABLE_FAILURES)
        rows = self.database.execute(
            f"""SELECT h.* FROM news_headlines h
            WHERE h.received_at >= ?
              AND NOT EXISTS (SELECT 1 FROM news_jev_attempts a JOIN news_jev_outcomes o USING (attempt_id)
                              WHERE a.headline_id = h.headline_id AND (
                                  o.status IN ({",".join("?" for _ in FINAL_STATUSES)})
                                  OR (o.status = 'http_error' AND o.failure NOT IN
                                      ({",".join("?" for _ in retryable)}))))
              AND NOT EXISTS (SELECT 1 FROM news_jev_attempts a WHERE a.headline_id = h.headline_id
                              AND a.reservation_id IS NOT NULL AND NOT EXISTS (
                                  SELECT 1 FROM news_jev_outcomes o WHERE o.attempt_id = a.attempt_id))
              AND (SELECT COUNT(*) FROM news_jev_attempts a WHERE a.headline_id = h.headline_id
                   AND a.attempt_number IS NOT NULL) < ?
            ORDER BY h.received_at, h.published_at, h.link""",
            (utc_iso(cutoff), *FINAL_STATUSES, *retryable, max_attempts)).fetchall()
        return [headline for headline in map(_headline, rows) if reference_time(headline) >= cutoff]

    def next_attempt_number(self, headline_id: str) -> int:
        row = self.database.execute("SELECT MAX(attempt_number) FROM news_jev_attempts WHERE headline_id = ?",
                                    (headline_id,)).fetchone()
        return (row[0] or 0) + 1

    def insert_attempt(self, headline: Headline, number: int | None, reservation_id: str | None, *,
                       requested_model: str, question_set_version: str, body: dict, watch: WatchList,
                       synthetic: bool) -> str:
        attempt_id = uuid.uuid4().hex
        request_json = exact_json(body)
        with self.database.immediate() as connection:
            connection.execute(
                """INSERT INTO news_jev_attempts (attempt_id, headline_id, attempt_number, reservation_id,
                requested_model, question_set_version, request_sha256, request_json, watch_json, synthetic,
                started_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (attempt_id, headline.headline_id, number, reservation_id, requested_model, question_set_version,
                 hashlib.sha256(request_json.encode()).hexdigest(), request_json,
                 json.dumps(watch.model_dump(mode="json"), sort_keys=True), int(synthetic),
                 utc_iso(self.clock.now())))
        return attempt_id

    def insert_outcome(self, attempt_id: str, *, status: str, result: DecisionResult | None,
                       reservation_id: str | None, reservation_state: str | None, receipt_id: str | None,
                       latency_ms: int | None) -> None:
        usage = result.usage if result is not None else None
        with self.database.immediate() as connection:
            connection.execute(
                """INSERT INTO news_jev_outcomes (attempt_id, status, failure, http_status, answering_model,
                provider_request_id, answers_json, input_tokens, output_tokens, usage_reported, reservation_id,
                reservation_state, receipt_id, latency_ms, finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (attempt_id, status, result.failure if result else None, result.http_status if result else None,
                 result.answering_model if result else None, result.provider_request_id if result else None,
                 exact_json(result.raw_answers) if result is not None and result.raw_answers is not None else None,
                 usage.input_tokens if usage else None, usage.output_tokens if usage else None,
                 int(bool(usage and usage.reported)), reservation_id, reservation_state, receipt_id, latency_ms,
                 utc_iso(self.clock.now())))

    def insert_verdict(self, attempt_id: str, headline: Headline, verdict: WouldWakeVerdict) -> None:
        with self.database.immediate() as connection:
            connection.execute(
                """INSERT INTO news_alarm_verdicts (verdict_id, attempt_id, headline_id, rule_version,
                question_set_version, urgent_threshold, would_wake, wake_roles_json, subjects_json,
                held_subjects_json, event_type, holder_yes, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (uuid.uuid4().hex, attempt_id, headline.headline_id, verdict.rule_version,
                 verdict.question_set_version, str(verdict.urgent_threshold), int(verdict.would_wake),
                 json.dumps(list(verdict.wake_roles)), json.dumps(list(verdict.subjects)),
                 json.dumps(list(verdict.held_subjects)), verdict.event_type, str(verdict.holder_yes),
                 utc_iso(self.clock.now())))

    def unfinished_attempts(self) -> list[tuple[str, str]]:
        rows = self.database.execute(
            """SELECT a.attempt_id, a.reservation_id FROM news_jev_attempts a
            WHERE a.reservation_id IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM news_jev_outcomes o WHERE o.attempt_id = a.attempt_id)
            ORDER BY a.started_at, a.rowid""").fetchall()
        return [(row["attempt_id"], row["reservation_id"]) for row in rows]
