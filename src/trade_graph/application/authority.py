"""Persisted owner-policy and mandate authority. Callers do not supply exposure caps."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from trade_graph.adapters.persistence.db import Database
from trade_graph.contracts.models import Decision, Mandate, OwnerPolicy
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import AuthorityDenied, DuplicateRecord, StaleState, ValidationFailure
from trade_graph.domain.money import Money
from trade_graph.kernel.authority import assert_action, mandate_within_owner
from trade_graph.kernel.riba import refuse_riba_symbol

# ``coin_rule(symbol, as_of=..., max_age=...)`` answers like ``CoinScanStore.coin_rule`` (W2-B, D174): an object
# with ``passed`` (True, False or None for unknown), ``status`` and ``reasons``.
CoinRule = Callable[..., Any]

# The paper default names only strategies with an installed template. Earlier
# defaults also carried two test-fixture IDs; databases that hold them are
# cleaned by an owner-confirmed new mandate revision, never by rewriting one.
DEFAULT_PAPER_STRATEGY_IDS = ("slow-trend-pullback",)
LEGACY_TEST_STRATEGY_IDS = frozenset({"slow-trend", "s"})


def _digest(document: str) -> str:
    return hashlib.sha256(document.encode()).hexdigest()


class AuthorityRecord:
    def __init__(self, database: Database, clock: Clock, *, coin_rule: CoinRule | None = None) -> None:
        self.database = database
        self.clock = clock
        self._coin_rule = coin_rule

    def install_policy(self, policy: OwnerPolicy, *, role: str) -> None:
        if role != "owner":
            raise AuthorityDenied("only the owner can revise owner policy")
        if policy.withdrawals_allowed:
            raise AuthorityDenied("withdrawals are not a runtime permission")
        if policy.leverage_allowed:
            raise AuthorityDenied("margin and leverage are refused (D127)")
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

    def legacy_strategy_revision(self, portfolio_id: str) -> Mandate | None:
        """Propose the next revision without test-fixture strategy IDs; writes nothing."""
        current = self.active_mandate(portfolio_id)
        kept = [item for item in current.strategy_ids if item not in LEGACY_TEST_STRATEGY_IDS]
        if kept == current.strategy_ids:
            return None
        # MAX, not active + 1: an older revision can be re-activated.
        latest = self.database.execute(
            "SELECT MAX(revision) FROM mandates WHERE portfolio_id = ?", (portfolio_id,),
        ).fetchone()[0]
        revision = int(latest) + 1
        # An empty list would switch off the named-strategy check.
        proposed = current.model_copy(update={
            "revision": revision,
            "mandate_id": f"paper-{portfolio_id}-{revision}",
            "strategy_ids": kept or list(DEFAULT_PAPER_STRATEGY_IDS),
        })
        # The preview refuses what install_mandate would refuse.
        mandate_within_owner(proposed, self.active_policy())
        if self.mandate_expired(proposed):
            raise ValidationFailure("mandate is already expired")
        return proposed

    def drop_legacy_strategy_ids(self, portfolio_id: str, *, role: str) -> Mandate | None:
        """Install the proposal as a new active revision; older rows only lose the active flag."""
        if role != "owner":
            raise AuthorityDenied("only the owner can revise the mandate strategy list")
        with self.database.immediate():
            revised = self.legacy_strategy_revision(portfolio_id)
            if revised is not None:
                self.install_mandate(revised, role="owner")
        return revised

    @staticmethod
    def _require_listed(policy: OwnerPolicy, mandate: Mandate, symbol: str) -> None:
        """The list part of the coin scope. Under the owner's coin rule a rule mandate names no list."""
        if mandate.coin_scope == "owner_rule":
            if policy.coin_scope is None:
                raise AuthorityDenied("symbol is outside the active mandate: it follows a coin rule the owner "
                                      "policy does not have")
            return
        if symbol not in mandate.symbols or (policy.coin_scope is None and symbol not in policy.allowed_symbols):
            raise AuthorityDenied("symbol is outside the active mandate")

    def coin_rule_answer(self, policy: OwnerPolicy, symbol: str):
        """The scanner's answer for ``symbol`` now (D138 item 3); None when the policy keeps a list."""
        scope = policy.coin_scope
        if scope is None:
            return None
        rule = self._coin_rule
        if rule is None:
            from trade_graph.application.coin_scanner.store import CoinScanStore

            rule = CoinScanStore(self.database, self.clock).coin_rule
        return rule(symbol, as_of=self.clock.now(), max_age=timedelta(seconds=scope.max_scan_age_seconds))

    def require_coin_rule(self, policy: OwnerPolicy, symbol: str) -> None:
        """Entries only: a pair that passed the scanner's checks at this moment. Unknown refuses, as stale."""
        scope = policy.coin_scope
        if scope is None:
            return
        if not symbol.endswith("/" + scope.quote_asset):
            raise AuthorityDenied(f"symbol is outside the owner's coin rule: only {scope.quote_asset} pairs")
        answer = self.coin_rule_answer(policy, symbol)
        if answer.passed is True:
            return
        if answer.passed is False:
            reasons = ", ".join(answer.reasons) if answer.reasons else answer.status
            raise AuthorityDenied(f"symbol is outside the owner's coin rule: {reasons}")
        raise StaleState(f"the owner's coin rule can't be checked: {answer.status}")

    def entry_scope(self, policy: OwnerPolicy, mandate: Mandate, symbol: str) -> str:
        """For an unsent entry: "submit", "reject" (outside the scope) or "hold" (the coin rule is unknown)."""
        try:
            self._require_listed(policy, mandate, symbol)
            self.require_coin_rule(policy, symbol)
        except AuthorityDenied:
            return "reject"
        except StaleState:
            return "hold"
        return "submit"

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
        if decision.symbol is not None:
            self._require_listed(policy, mandate, decision.symbol)
            if decision.action == "enter" and policy.coin_scope is not None:
                self.require_coin_rule(policy, decision.symbol)
        if decision.action == "enter" and self.mandate_expired(mandate):
            raise AuthorityDenied("expired mandate allows management only")
        order_type = "limit" if decision.limit_price is not None else "market"
        if decision.action in {"enter", "exit"} and order_type not in mandate.allowed_order_types:
            raise AuthorityDenied("order type is outside the active mandate")
        # D18 names the strategies that may add exposure. Exits only reduce
        # long-only spot holdings, as under an expired mandate.
        if (
            decision.action == "enter"
            and mandate.strategy_ids
            and not mandate.discretionary_experiment
            and decision.strategy_id not in mandate.strategy_ids
        ):
            raise AuthorityDenied("strategy is outside the active mandate")
        return policy, mandate


def owner_scope_covers(policy: OwnerPolicy, symbol: str) -> bool:
    """Can the owner's coin list ever include ``symbol``? Listed, or under the coin rule a spot pair in its quote
    currency (whether it may be bought at a given moment is the scanner's answer, checked per entry)."""
    if policy.coin_scope is None:
        return symbol in policy.allowed_symbols
    try:
        refuse_riba_symbol(symbol)
    except AuthorityDenied:
        return False
    return symbol in policy.allowed_symbols or symbol.endswith("/" + policy.coin_scope.quote_asset)


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
        strategy_ids=strategy_ids or list(DEFAULT_PAPER_STRATEGY_IDS),
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
