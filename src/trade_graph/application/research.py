"""Cached research. Page text is evidence, never an instruction."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from trade_graph.adapters.persistence.db import Database, atomic
from trade_graph.adapters.research.fetch import assert_public_url, strip_active_content
from trade_graph.contracts.models import ResearchFinding, StrategyProposal
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import StaleState, ValidationFailure
from trade_graph.roles.strategies import template

_INJECTION_MARKERS = (
    "ignore previous",
    "system prompt",
    "you must buy",
    "you must sell",
    "tool call",
)


class ResearchStore:
    def __init__(self, database: Database, clock: Clock) -> None:
        self.database = database
        self.clock = clock

    @atomic
    def ingest_page(
        self,
        portfolio_id: str,
        *,
        url: str,
        html: str,
        resolver,
        publisher: str,
        question: str,
        instruments: list[str],
        published_at: datetime | None,
        expires_at: datetime,
        counterevidence: str,
        redirects: list[str] | None = None,
    ) -> ResearchFinding:
        assert_public_url(url, resolver, redirects=redirects)
        text = " ".join(strip_active_content(html).split())
        if not text:
            raise ValidationFailure("source page has no text after stripping active content")
        untrusted = any(marker in text.casefold() for marker in _INJECTION_MARKERS)
        now = self.clock.now()
        finding = ResearchFinding(
            record_id=str(uuid.uuid4()),
            created_at_utc=now,
            run_id="research",
            task_id="research",
            root_task_id="research",
            portfolio_id=portfolio_id,
            mode="paper",
            system_version_id="research",
            trace_id=str(uuid.uuid4()),
            question=question,
            claim=text[:500],
            source_url=url,
            publisher=publisher,
            published_at_utc=published_at,
            event_at_utc=published_at,
            retrieved_at_utc=now,
            available_at_utc=now,
            source_hash=hashlib.sha256(text.encode()).hexdigest(),
            instruments=instruments,
            relevance="untrusted-page" if untrusted else "source",
            counterevidence=counterevidence,
            expires_at_utc=expires_at,
            invalidation="expires or a newer source version supersedes it",
        )
        self._insert(finding)
        return finding

    def fresh(
        self,
        portfolio_id: str,
        symbol: str,
        *,
        as_of: datetime,
        after: datetime | None = None,
    ) -> list[ResearchFinding]:
        return [
            item
            for item in self._matching(portfolio_id, symbol)
            if item.available_at_utc <= as_of
            and item.expires_at_utc > as_of
            and item.relevance != "untrusted-page"
            and (after is None or item.available_at_utc > after)
        ]

    def stale(self, portfolio_id: str, symbol: str, *, as_of: datetime) -> list[ResearchFinding]:
        return [
            item
            for item in self._matching(portfolio_id, symbol)
            if item.available_at_utc <= as_of and item.expires_at_utc <= as_of
        ]

    def require_fresh(self, portfolio_id: str, finding_id: str, *, as_of: datetime) -> ResearchFinding:
        row = self.database.execute(
            "SELECT document_json FROM findings WHERE finding_id = ? AND portfolio_id = ?",
            (finding_id, portfolio_id),
        ).fetchone()
        if row is None:
            raise StaleState("research finding is not in the cache")
        finding = ResearchFinding.model_validate_json(row["document_json"])
        if finding.expires_at_utc <= as_of or finding.available_at_utc > as_of or finding.relevance == "untrusted-page":
            raise StaleState("research finding is stale or untrusted")
        return finding

    @atomic
    def submit_strategy(self, portfolio_id: str, strategy_id: str) -> StrategyProposal:
        item = template(strategy_id)
        if item is None:
            raise ValidationFailure("unknown strategy template")
        now = self.clock.now()
        proposal = StrategyProposal(
            record_id=str(uuid.uuid4()),
            created_at_utc=now,
            run_id="research",
            task_id="research",
            root_task_id="research",
            portfolio_id=portfolio_id,
            mode="paper",
            system_version_id="research",
            trace_id=str(uuid.uuid4()),
            hypothesis=item.hypothesis,
            mechanism="template hypothesis, not a measured edge",
            features=list(item.features),
            entry_rule=item.entry_rule,
            exit_rule=item.exit_rule,
            invalidation=item.invalidation,
            sizing_rule=item.sizing_rule,
            required_capabilities=["paper-spot"],
            estimated_friction_note="fixed paper taker tier; not a venue fee quote",
            regimes=["unproven"],
            baseline="cash",
            validation_protocol="forward paper only",
            retire_when="the invalidation condition prints at the decision snapshot",
            provenance="repository template",
            verification_status="unverified",
        )
        self.database.execute(
            """INSERT INTO experiments (experiment_id, document_json, created_at)
            VALUES (?, ?, ?)""",
            (proposal.record_id, proposal.model_dump_json(), utc_iso(now)),
        )
        return proposal

    def _insert(self, finding: ResearchFinding) -> None:
        self.database.execute(
            """INSERT INTO findings
            (finding_id, portfolio_id, document_json, source_hash, available_at, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                finding.record_id,
                finding.portfolio_id,
                finding.model_dump_json(),
                finding.source_hash,
                utc_iso(finding.available_at_utc),
                utc_iso(finding.expires_at_utc),
                utc_iso(finding.created_at_utc),
            ),
        )

    def _matching(self, portfolio_id: str, symbol: str) -> list[ResearchFinding]:
        rows = self.database.execute(
            "SELECT document_json FROM findings WHERE portfolio_id = ? ORDER BY created_at, finding_id",
            (portfolio_id,),
        ).fetchall()
        findings = [ResearchFinding.model_validate_json(row["document_json"]) for row in rows]
        return [item for item in findings if symbol in item.instruments]
