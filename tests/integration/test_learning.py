"""Lesson provenance and point-in-time evaluation. No model calls."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trade_graph.adapters.market.replay import PointInTimeMarket
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.learning import (
    LearningJournal,
    OptimisationReview,
    cost_per_decision,
    grade,
    no_trade_mark,
)
from trade_graph.contracts.models import InstrumentRules, LessonRevision, Observation, OptimisationProposal
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import AuthorityDenied, ValidationFailure


def _lesson(clock, portfolio, **updates) -> LessonRevision:
    payload = {
        "record_id": "lesson-r1",
        "created_at_utc": clock.now(),
        "run_id": "learn",
        "task_id": "learn",
        "root_task_id": "learn",
        "portfolio_id": portfolio,
        "mode": "paper",
        "system_version_id": "v1",
        "trace_id": "trace",
        "lesson_id": "lesson-1",
        "revision": 1,
        "observation": "The entry used the quote then available.",
        "supporting_cases": ["decision-1"],
        "counterexamples": ["later bar reversed"],
        "explanation": "The thesis matched the visible drift.",
        "proposed_improvement": "Keep the invalidation explicit.",
        "validation_method": "next forward decision",
        "scope": "BTC/USD paper",
        "sample_note": "one decision",
        "confidence_category": "tentative",
        "linked_decisions": ["decision-1"],
        "status": "tentative",
        "process_assessment": "valid_thesis",
        "outcome_sign": "loss",
    }
    payload.update(updates)
    return LessonRevision.model_validate(payload)


def test_revisions_keep_counterevidence_and_survive_restart(tmp_path) -> None:
    path = tmp_path / "learn.sqlite"
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    database = Database(path)
    journal = LearningJournal(database, clock)
    journal.append("p", _lesson(clock, "p"))
    dropped = _lesson(
        clock,
        "p",
        record_id="lesson-r2",
        revision=2,
        supersedes="lesson-r1",
        counterexamples=[],
        status="contradicted",
        system_version_id="v2",
    )
    with pytest.raises(ValidationFailure, match="counterevidence"):
        journal.append("p", dropped)
    journal.append(
        "p",
        _lesson(
            clock,
            "p",
            record_id="lesson-r2",
            revision=2,
            supersedes="lesson-r1",
            counterexamples=["later bar reversed", "a second reversal"],
            status="contradicted",
            system_version_id="v2",
            process_assessment="invalid_process",
            outcome_sign="gain",
        ),
    )
    database.close()
    reopened = LearningJournal(Database(path), clock)
    history = reopened.history("lesson-1")
    assert [item.revision for item in history] == [1, 2]
    assert history[1].status == "contradicted"
    assert history[1].system_version_id == "v2"
    assert "later bar reversed" in history[1].counterexamples
    assert grade("invalid_process", "gain") == "invalid_process"
    assert grade("valid_thesis", "loss") == "valid_thesis"
    assert grade("invalid_process", "gain") != "good"


def test_no_trade_evaluation_ignores_later_prices(tmp_path) -> None:
    early = datetime(2026, 1, 1, tzinfo=UTC)
    later = datetime(2026, 1, 1, 1, tzinfo=UTC)
    rules = InstrumentRules(
        venue="replay",
        symbol="BTC/USD",
        base_asset="BTC",
        quote_asset="USD",
        price_increment="0.1",
        quantity_increment="0.00000001",
        min_quantity="0.0001",
        min_notional="1",
        synthetic=True,
    )
    market = PointInTimeMarket(
        [rules],
        [
            Observation(
                observation_id="early",
                venue="replay",
                symbol="BTC/USD",
                event_time_utc=early,
                available_at_utc=early,
                bid="99",
                ask="101",
                kind="quote",
                source="fixture",
            ),
            Observation(
                observation_id="later-high",
                venue="replay",
                symbol="BTC/USD",
                event_time_utc=later,
                available_at_utc=later,
                bid="199",
                ask="201",
                kind="quote",
                source="fixture",
            ),
        ],
    )
    mark = no_trade_mark(market, "BTC/USD", early)
    assert mark["mid"] == "100"
    assert mark["observation_id"] == "early"
    same_bar = PointInTimeMarket(
        [rules],
        [
            Observation(
                observation_id="bar-close",
                venue="replay",
                symbol="BTC/USD",
                event_time_utc=early,
                available_at_utc=later,
                bid="150",
                ask="160",
                kind="quote",
                source="fixture",
            )
        ],
    )
    with pytest.raises(ValidationFailure):
        no_trade_mark(same_bar, "BTC/USD", early)


def test_optimisation_cannot_disable_reconciliation_and_records_cost(tmp_path) -> None:
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    database = Database(tmp_path / "opt.sqlite")
    review = OptimisationReview(database, clock)
    blocked = OptimisationProposal(
        record_id="opt-blocked",
        created_at_utc=clock.now(),
        run_id="opt",
        task_id="opt",
        root_task_id="opt",
        portfolio_id="p",
        mode="paper",
        system_version_id="v1",
        trace_id="trace",
        issue="too many lessons",
        resources="eight lessons",
        interval="2026-01",
        modification="drop reconciliation",
        expected_benefit="fewer tokens",
        quality_risk="missed fills",
        validation_metrics=["decision_quality"],
        disables_reconciliation=True,
    )
    with pytest.raises(AuthorityDenied):
        review.propose(blocked)
    review.propose(
        blocked.model_copy(update={"record_id": "opt-ok", "disables_reconciliation": False})
    )
    assert database.execute("SELECT COUNT(*) AS n FROM experiments").fetchone()["n"] == 1
    assert cost_per_decision(Decimal("1.5"), 3) == Decimal("0.5")
