"""Persisted owner-policy and mandate authority. Callers do not supply exposure caps."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trade_graph.adapters.persistence.db import Database
from trade_graph.contracts.models import Decision, Mandate, OwnerPolicy
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import AuthorityDenied, DuplicateRecord, ValidationFailure
from trade_graph.domain.money import Money
from trade_graph.kernel.authority import assert_action, mandate_within_owner


def _digest(document: str) -> str:
    return hashlib.sha256(document.encode()).hexdigest()


class AuthorityRecord:
    def __init__(self, database: Database, clock: Clock) -> None:
        self.database = database
        self.clock = clock

    def install_policy(self, policy: OwnerPolicy, *, role: str) -> None:
        if role != "owner":
            raise AuthorityDenied("only the owner can revise owner policy")
        if policy.withdrawals_allowed:
            raise AuthorityDenied("withdrawals are not a runtime permission")
        document = policy.model_dump_json()
        digest = _digest(document)
        with self.database.immediate() as conn:
            row = conn.execute(
                "SELECT content_hash FROM owner_policy_revisions WHERE revision_id = ?",
                (policy.revision_id,),
            ).fetchone()
            if row is not None:
                if row["content_hash"] != digest:
                    raise DuplicateRecord("owner policy revision is immutable")
                return
            conn.execute(
                """INSERT INTO owner_policy_revisions
                (revision_id, document_json, content_hash, created_at)
                VALUES (?, ?, ?, ?)""",
                (policy.revision_id, document, digest, utc_iso(self.clock.now())),
            )

    def install_mandate(self, mandate: Mandate, *, role: str) -> None:
        if role not in {"owner", "leader"}:
            raise AuthorityDenied("mandate installation is not permitted for this role")
        assert_action(role, "set_mandate", owner_halt=False)
        policy = self.active_policy()
        mandate_within_owner(mandate, policy)
        if mandate.expires_at_utc <= self.clock.now():
            raise ValidationFailure("mandate is already expired")
        document = mandate.model_dump_json()
        with self.database.immediate() as conn:
            row = conn.execute(
                """SELECT mandate_id, document_json FROM mandates
                WHERE portfolio_id = ? AND revision = ?""",
                (mandate.portfolio_id, mandate.revision),
            ).fetchone()
            if row is not None:
                stored = Mandate.model_validate_json(row["document_json"])
                if stored.model_dump() != mandate.model_dump() or row["mandate_id"] != mandate.mandate_id:
                    raise DuplicateRecord("mandate revision is immutable")
                conn.execute(
                    "UPDATE mandates SET active = 0 WHERE portfolio_id = ?",
                    (mandate.portfolio_id,),
                )
                conn.execute(
                    "UPDATE mandates SET active = 1 WHERE mandate_id = ?",
                    (mandate.mandate_id,),
                )
                return
            conn.execute(
                "UPDATE mandates SET active = 0 WHERE portfolio_id = ?",
                (mandate.portfolio_id,),
            )
            conn.execute(
                """INSERT INTO mandates
                (mandate_id, portfolio_id, revision, document_json, active, expires_at, created_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)""",
                (
                    mandate.mandate_id,
                    mandate.portfolio_id,
                    mandate.revision,
                    document,
                    utc_iso(mandate.expires_at_utc),
                    utc_iso(self.clock.now()),
                ),
            )

    def active_policy(self) -> OwnerPolicy:
        row = self.database.execute(
            """SELECT document_json FROM owner_policy_revisions
            ORDER BY created_at DESC, rowid DESC LIMIT 1"""
        ).fetchone()
        if row is None:
            raise AuthorityDenied("no active owner policy")
        return OwnerPolicy.model_validate_json(row["document_json"])

    def active_mandate(self, portfolio_id: str) -> Mandate:
        row = self.database.execute(
            "SELECT document_json FROM mandates WHERE portfolio_id = ? AND active = 1",
            (portfolio_id,),
        ).fetchone()
        if row is None:
            raise AuthorityDenied("no active mandate")
        return Mandate.model_validate_json(row["document_json"])

    def mandate_expired(self, mandate: Mandate) -> bool:
        return mandate.expires_at_utc <= self.clock.now()

    def limits(self, policy: OwnerPolicy, mandate: Mandate) -> tuple[Decimal, Decimal, int]:
        return (
            min(policy.maximum_gross_exposure_fraction, mandate.max_gross_exposure_fraction),
            min(policy.maximum_single_asset_exposure_fraction, mandate.max_single_asset_exposure_fraction),
            min(policy.maximum_quote_age_seconds, mandate.max_quote_age_seconds),
        )

    def require_for_decision(self, decision: Decision, *, venue: str, mode: str) -> tuple[OwnerPolicy, Mandate]:
        if decision.portfolio_id is None:
            raise AuthorityDenied("decision has no portfolio")
        policy = self.active_policy()
        mandate = self.active_mandate(decision.portfolio_id)
        if decision.policy_revision != policy.revision_id:
            raise AuthorityDenied("decision policy revision is not active")
        if decision.mandate_revision != str(mandate.revision):
            raise AuthorityDenied("decision mandate revision is not active")
        if decision.mode == "live" or mode == "live":
            if not policy.live_enabled:
                raise AuthorityDenied("live trading is not enabled")
        if decision.mode != mode:
            raise AuthorityDenied("decision mode mismatch")
        if venue not in policy.allowed_venues:
            raise AuthorityDenied("venue is outside the owner policy")
        if decision.symbol is not None and (
            decision.symbol not in mandate.symbols or decision.symbol not in policy.allowed_symbols
        ):
            raise AuthorityDenied("symbol is outside the active mandate")
        if decision.action == "enter" and self.mandate_expired(mandate):
            raise AuthorityDenied("expired mandate allows management only")
        order_type = "limit" if decision.limit_price is not None else "market"
        if decision.action in {"enter", "exit"} and order_type not in mandate.allowed_order_types:
            raise AuthorityDenied("order type is outside the active mandate")
        if (
            mandate.strategy_ids
            and not mandate.discretionary_experiment
            and decision.strategy_id not in mandate.strategy_ids
        ):
            raise AuthorityDenied("strategy is outside the active mandate")
        return policy, mandate


def paper_owner_policy(
    *,
    revision_id: str = "1",
    gross: str = "0.80",
    asset: str = "0.50",
    quote_age: int = 30,
    symbols: list[str] | None = None,
    venues: list[str] | None = None,
) -> OwnerPolicy:
    return OwnerPolicy(
        revision_id=revision_id,
        live_enabled=False,
        withdrawals_allowed=False,
        leverage_allowed=False,
        paid_calls_enabled=False,
        reporting_currency="EUR",
        virtual_capital=Money(amount="10000", currency="USD"),
        monthly_operating=Money(amount="5", currency="EUR"),
        priority_reserve=Money(amount="1", currency="EUR"),
        daily_paid_limit=Money(amount="1.50", currency="EUR"),
        root_paid_limit=Money(amount="1.50", currency="EUR"),
        maximum_gross_exposure_fraction=gross,
        maximum_single_asset_exposure_fraction=asset,
        allowed_change_classes=["artifact_config"],
        allowed_venues=venues or ["paper"],
        allowed_symbols=symbols or ["BTC/USD", "ETH/USD"],
        maximum_quote_age_seconds=quote_age,
    )


def paper_mandate(
    portfolio_id: str,
    *,
    revision: int = 1,
    mandate_id: str | None = None,
    gross: str = "0.80",
    asset: str = "0.50",
    quote_age: int = 30,
    symbols: list[str] | None = None,
    expires_at: datetime | None = None,
    discretionary_experiment: bool = True,
    strategy_ids: list[str] | None = None,
) -> Mandate:
    return Mandate(
        mandate_id=mandate_id or f"paper-{portfolio_id}-{revision}",
        portfolio_id=portfolio_id,
        revision=revision,
        strategy_ids=strategy_ids or ["slow-trend", "slow-trend-pullback", "s"],
        symbols=symbols or ["BTC/USD", "ETH/USD"],
        allowed_order_types=["market", "limit"],
        max_gross_exposure_fraction=gross,
        max_single_asset_exposure_fraction=asset,
        decision_horizon_seconds=14400,
        order_types_long_only=True,
        discretionary_experiment=discretionary_experiment,
        max_quote_age_seconds=quote_age,
        expires_at_utc=expires_at or datetime(2030, 1, 1, tzinfo=UTC),
        resource_note="paper mandate inside the owner envelope",
    )


def seed_paper_authority(database: Database, clock: Clock, portfolio_id: str) -> AuthorityRecord:
    record = AuthorityRecord(database, clock)
    record.install_policy(paper_owner_policy(), role="owner")
    record.install_mandate(paper_mandate(portfolio_id), role="owner")
    return record


def paper_expiry(clock: Clock, *, seconds: int = 30) -> datetime:
    return clock.now() + timedelta(seconds=seconds)
