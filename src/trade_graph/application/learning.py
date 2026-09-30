"""Lesson revisions and point-in-time evaluation. Outcome sign is not the grade."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from trade_graph.adapters.market.replay import PointInTimeMarket, quote_features
from trade_graph.adapters.persistence.db import Database, atomic
from trade_graph.contracts.models import LessonRevision, OptimisationProposal
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import AuthorityDenied, ValidationFailure
from trade_graph.roles.judgement import classify_decision


class LearningJournal:
    def __init__(self, database: Database, clock: Clock) -> None:
        self.database = database
        self.clock = clock

    @atomic
    def append(self, portfolio_id: str, lesson: LessonRevision) -> None:
        if lesson.portfolio_id != portfolio_id:
            raise ValidationFailure("lesson portfolio mismatch")
        prior = self._latest(lesson.lesson_id)
        if prior is None and lesson.revision != 1:
            raise ValidationFailure("lesson revisions start at 1")
        if prior is not None:
            if lesson.revision != prior.revision + 1:
                raise ValidationFailure("lesson revision is not the next revision")
            if not set(prior.counterexamples) <= set(lesson.counterexamples):
                raise ValidationFailure("counterevidence cannot be removed")
            if lesson.supersedes != prior.record_id:
                raise ValidationFailure("revision must name the superseded record")
        self.database.execute(
            """INSERT INTO lessons
            (revision_id, lesson_id, portfolio_id, revision, document_json, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                lesson.record_id,
                lesson.lesson_id,
                portfolio_id,
                lesson.revision,
                lesson.model_dump_json(),
                lesson.status,
                utc_iso(self.clock.now()),
            ),
        )

    def history(self, lesson_id: str) -> list[LessonRevision]:
        rows = self.database.execute(
            "SELECT document_json FROM lessons WHERE lesson_id = ? ORDER BY revision",
            (lesson_id,),
        ).fetchall()
        return [LessonRevision.model_validate_json(row["document_json"]) for row in rows]

    def _latest(self, lesson_id: str) -> LessonRevision | None:
        rows = self.history(lesson_id)
        return rows[-1] if rows else None


def grade(process_assessment: str, outcome_sign: str) -> str:
    return classify_decision(process_assessment, outcome_sign)


def no_trade_mark(market: PointInTimeMarket, symbol: str, as_of: datetime) -> dict[str, str]:
    """Features from quotes already available. Later bars are not an entry price."""
    return quote_features(market.visible(symbol, as_of))


class OptimisationReview:
    def __init__(self, database: Database, clock: Clock) -> None:
        self.database = database
        self.clock = clock

    @atomic
    def propose(self, proposal: OptimisationProposal) -> None:
        if proposal.disables_reconciliation:
            raise AuthorityDenied("optimisation cannot disable reconciliation")
        self.database.execute(
            """INSERT INTO experiments (experiment_id, document_json, created_at)
            VALUES (?, ?, ?)""",
            (proposal.record_id, proposal.model_dump_json(), utc_iso(self.clock.now())),
        )


def cost_per_decision(spend: Decimal, decisions: int) -> Decimal:
    if decisions <= 0:
        raise ValidationFailure("cost per decision needs at least one decision")
    return spend / Decimal(decisions)
