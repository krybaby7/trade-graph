from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from trade_graph.contracts.models import Decision, Mandate, OwnerPolicy, export_schemas
from trade_graph.domain.money import Money
from trade_graph.kernel.authority import assert_action, mandate_within_owner, path_is_protected


def _policy() -> OwnerPolicy:
    return OwnerPolicy(
        revision_id="p1",
        virtual_capital=Money(amount="10000", currency="USD"),
        monthly_operating=Money(amount="5", currency="EUR"),
        priority_reserve=Money(amount="1", currency="EUR"),
        daily_paid_limit=Money(amount="1.50", currency="EUR"),
        root_paid_limit=Money(amount="1.50", currency="EUR"),
        maximum_gross_exposure_fraction="0.80",
        maximum_single_asset_exposure_fraction="0.50",
        allowed_change_classes=["artifact_config"],
        allowed_venues=["kraken_public"],
        allowed_symbols=["BTC/USD", "ETH/USD"],
    )


def test_unknown_field_and_float_rejected() -> None:
    with pytest.raises(ValidationError):
        Money(amount="1", currency="EUR", extra=True)
    schemas = export_schemas()
    assert "Decision" in schemas
    assert schemas["Decision"]["additionalProperties"] is False


def test_decision_requires_envelope_mode() -> None:
    with pytest.raises(ValidationError):
        Decision.model_validate(
            {
                "record_id": "d1",
                "created_at_utc": datetime(2026, 1, 1, tzinfo=UTC),
                "run_id": "r",
                "mode": "live-ish",
                "system_version_id": "v",
                "trace_id": "t",
                "action": "hold",
                "rationale": "wait",
                "invalidation": "none",
                "horizon_seconds": 1,
                "strategy_id": "s",
                "snapshot_id": "snap",
                "mandate_revision": "m",
                "policy_revision": "p",
            }
        )


def test_leader_cannot_raise_budget_or_lift_owner_halt() -> None:
    with pytest.raises(Exception):
        assert_action("leader", "raise_owner_budget", owner_halt=False)
    with pytest.raises(Exception):
        assert_action("leader", "resume_pause", owner_halt=True)
    assert_action("leader", "resume_pause", owner_halt=False)
    assert path_is_protected("src/trade_graph/kernel/books.py")
    assert not path_is_protected("artifacts/context_policy.json")


def test_mandate_must_fit_owner_envelope() -> None:
    policy = _policy()
    mandate = Mandate(
        mandate_id="m1",
        portfolio_id="p",
        revision=1,
        strategy_ids=["slow"],
        symbols=["BTC/USD"],
        allowed_order_types=["market", "limit"],
        max_gross_exposure_fraction=Decimal("0.80"),
        max_single_asset_exposure_fraction=Decimal("0.50"),
        decision_horizon_seconds=14400,
        max_quote_age_seconds=15,
        expires_at_utc=datetime(2026, 2, 1, tzinfo=UTC),
        resource_note="paper",
    )
    mandate_within_owner(mandate, policy)
    wider = mandate.model_copy(update={"max_gross_exposure_fraction": Decimal("0.95")})
    with pytest.raises(Exception):
        mandate_within_owner(wider, policy)
