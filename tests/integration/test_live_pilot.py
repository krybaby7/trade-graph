"""Synthetic retained lifecycle fixtures; no approval, dispatch, or private call.

Historical ACTIVE rows below are explicitly seeded test states, never produced by
activation. All production activation calls continue to use the closed evaluator.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from decimal import Decimal, localcontext
from types import SimpleNamespace

import pytest
from tests.integration.test_live_readiness import Fixture

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import InstrumentRules
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState, ValidationFailure
from trade_graph.domain.money import Money
from trade_graph.live_evidence import LiveUpstreamSources
from trade_graph.live_gate import evaluate_live_readiness
from trade_graph.live_pilot import ProtectedPilotLifecycle, validate_pilot_envelope


@pytest.fixture
def fixture(tmp_path):
    value = Fixture(tmp_path)
    health = json.loads(value.db.execute("SELECT payload_json FROM activity_events "
                                        "WHERE kind='execution_reconciliation_health'").fetchone()[0])
    health["observation_scope"] = "owned_intent_fill_history"
    health["observed_at"] = utc_iso(value.clock.now())
    value.db.execute("UPDATE activity_events SET payload_json=? WHERE kind='execution_reconciliation_health'",
                     (json.dumps(health),))
    value.source_pin = value.source()
    value.lifecycle = ProtectedPilotLifecycle(value.db, value.clock, scope=value.scope, source=value.source_pin)
    value.authorization_id = value.lifecycle.prepare()
    value.grant = value.source_pin.load().owner_authorization.payload
    yield value
    value.db.close()


def rules(fixture):
    value = InstrumentRules(venue=fixture.scope.venue, symbol=fixture.scope.symbol, base_asset="BTC", quote_asset="USD",
                            price_increment="0.01", quantity_increment="0.01", min_quantity="0.01", min_notional="1",
                            synthetic=True)
    fixture.db.execute("INSERT INTO instruments VALUES (?,?,?,?)",
                       (value.venue, value.symbol, value.model_dump_json(), utc_iso(fixture.clock.now())))


def intent(fixture, *, intent_id="synthetic-intent", state="SUBMISSION_PENDING", cost="2"):
    payload = {"intent_id": intent_id, "portfolio_id": fixture.pid, "account_id": fixture.scope.account_id,
               "venue": fixture.scope.venue, "symbol": fixture.scope.symbol, "mode": "live", "side": "buy",
               "order_type": "limit", "limit_price": "1", "quantity": "1", "reduce_only": False,
               "policy_revision": fixture.scope.policy_revision, "decision_id": f"decision-{intent_id}",
               "reserve_asset": "USD", "reserve_amount": cost}
    now = utc_iso(fixture.clock.now())
    fixture.db.execute("INSERT INTO order_intents VALUES (?,?,?,?,?,?,?,?)",
                       (intent_id, fixture.pid, f"client-{intent_id}", state, fixture.scope.symbol,
                        json.dumps(payload), now, now))
    fixture.db.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,NULL)",
                       (payload["decision_id"], fixture.pid, "enter", "{}", "1", fixture.scope.policy_revision,
                        "synthetic-snapshot", "evidence-only", now))
    fixture.db.execute("INSERT INTO position_reservations VALUES (?,?,?,?,?,'held',?)",
                       (f"reserve-{intent_id}", fixture.pid, intent_id, "USD", cost, now))
    return payload


def seed_effect(fixture, *, effect_state="PREPARED", order_state="SUBMISSION_PENDING", cost="2"):
    intent(fixture, state=order_state, cost=cost)
    now = utc_iso(fixture.clock.now())
    fixture.db.execute("UPDATE live_pilot_grants SET state='ACTIVE',generation=1 WHERE authorization_id=?",
                       (fixture.authorization_id,))
    fixture.db.execute("INSERT INTO live_pilot_effects VALUES (?,?,?,?,?,?,?, ?,?,?)",
                       ("synthetic-effect", fixture.authorization_id, "synthetic-intent", 1, "0" * 64,
                        "USD", cost, effect_state, now, now))
    return "synthetic-effect"


def synthetic_later_account_history(fixture):
    """A synthetic post-effect fact for mechanical release tests, never authority."""
    fixture.clock.advance(1)
    health = json.loads(fixture.db.execute("SELECT payload_json FROM activity_events "
                                          "WHERE kind='execution_reconciliation_health'").fetchone()[0])
    health["observed_at"] = utc_iso(fixture.clock.now())
    fixture.db.execute("UPDATE activity_events SET created_at=?,payload_json=? "
                       "WHERE kind='execution_reconciliation_health'",
                       (utc_iso(fixture.clock.now()), json.dumps(health)))


def envelope(fixture, *, held=(), proposed="1", expenses=(), now=None):
    return validate_pilot_envelope(
        fixture.grant, native_commitments=tuple(Money(amount=value, currency="USD") for value in held),
        proposed_native_cost=Money(amount=proposed, currency="USD"), actual_expenses=expenses,
        now=now or fixture.clock.now(),
    )


def test_prepare_is_idempotent_and_activation_remains_closed(fixture):
    policy = list(fixture.db.execute("SELECT * FROM owner_policy_revisions"))
    assert fixture.lifecycle.prepare() == fixture.authorization_id
    assert fixture.db.execute("SELECT COUNT(*) FROM live_pilot_grants").fetchone()[0] == 1
    result = fixture.lifecycle.activate(fixture.authorization_id)
    assert result == {"activated": False, "state": "PENDING", "code": "protected_prerequisites_incomplete"}
    assert list(fixture.db.execute("SELECT * FROM owner_policy_revisions")) == policy
    assert fixture.db.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0
    assert fixture.db.execute("SELECT kind FROM live_pilot_events ORDER BY rowid DESC LIMIT 1").fetchone()[0] == (
        "activation_refused"
    )
    assert evaluate_live_readiness(fixture.db, fixture.clock, scope=fixture.scope,
                                  source=fixture.source_pin)["ready"] is False


def test_even_seeded_active_state_cannot_prepare_or_begin_external_effect(fixture):
    rules(fixture)
    effect = seed_effect(fixture)
    with pytest.raises(AuthorityDenied, match="active independently verified"):
        fixture.lifecycle.reserve_increase(fixture.authorization_id, "synthetic-intent")
    with pytest.raises(AuthorityDenied, match="revoked, consumed, or stale"):
        fixture.lifecycle.begin_submission(effect)
    assert fixture.db.execute("SELECT state FROM live_pilot_effects").fetchone()[0] == "PREPARED"
    assert fixture.db.execute("SELECT state FROM order_intents").fetchone()[0] == "SUBMISSION_PENDING"


def test_full_loss_boundary_is_exact_and_never_replenished_by_sales(fixture):
    assert envelope(fixture, held=("3",), proposed="2")["native_remaining"].amount == 0
    with pytest.raises(AuthorityDenied, match="full-loss envelope"):
        envelope(fixture, held=("3",), proposed="2.000000000000000001")
    assert fixture.grant.allocation.amount == 25
    with pytest.raises(AuthorityDenied, match="full-loss envelope"):
        envelope(fixture, proposed="5.000000000000000001")


def test_fixed_point_arithmetic_does_not_use_ambient_precision(fixture):
    with localcontext() as context:
        context.prec = 3
        result = envelope(fixture, held=("4.000000000000000001",), proposed="0.999999999999999998")
    assert result["native_remaining"].amount == Decimal("0.000000000000000001")


@pytest.mark.parametrize("amount", ["-1", "1e1000", "0.0000000000000000001"])
def test_malformed_native_history_fails_closed(fixture, amount):
    with pytest.raises(ValidationFailure):
        envelope(fixture, held=(amount,))


def test_currency_and_future_usage_fail_closed(fixture):
    with pytest.raises(ValidationFailure, match="currency"):
        validate_pilot_envelope(fixture.grant, native_commitments=(Money(amount="1", currency="EUR"),),
                                proposed_native_cost=Money(amount="1", currency="USD"), actual_expenses=(),
                                now=fixture.clock.now())
    with pytest.raises(ValidationFailure, match="dated EUR"):
        envelope(fixture, expenses=((Money(amount="1", currency="EUR"), fixture.clock.now() + timedelta(seconds=1)),))


def test_expense_total_and_utc_daily_boundaries(fixture):
    now = fixture.clock.now()
    expense = Money(amount="1.5", currency="EUR")
    assert envelope(fixture, expenses=((expense, now),))["daily_remaining"].amount == 0
    with pytest.raises(AuthorityDenied, match="daily"):
        envelope(fixture, expenses=((Money(amount="1.500000000000000001", currency="EUR"), now),))
    with pytest.raises(AuthorityDenied, match="operating"):
        envelope(fixture, expenses=((Money(amount="5.000000000000000001", currency="EUR"),
                                     now - timedelta(days=1)),))
    assert envelope(fixture, expenses=((Money(amount="5", currency="EUR"),
                                       now - timedelta(days=1)),))["operating_remaining"].amount == 0


def test_expired_authorization_cannot_activate_but_can_revoke(fixture):
    fixture.clock.advance(86400)
    with pytest.raises(AuthorityDenied, match="freshness"):
        fixture.lifecycle.activate(fixture.authorization_id)
    with pytest.raises(AuthorityDenied, match="expired"):
        envelope(fixture)
    fixture.lifecycle.revoke(fixture.authorization_id, reason="synthetic expiry management")
    assert fixture.db.execute("SELECT state FROM live_pilot_grants").fetchone()[0] == "REVOKED"


def test_source_pin_drift_blocks_activation_and_does_not_block_stop(fixture):
    fixture.source_pin.path.write_bytes(b"{}")
    with pytest.raises(AuthorityDenied, match="source"):
        fixture.lifecycle.activate(fixture.authorization_id)
    fixture.lifecycle.stop(fixture.authorization_id, reason="synthetic source invalidation")
    assert fixture.db.execute("SELECT profile FROM pause_states").fetchone()[0] == "MANAGE_ONLY"


@pytest.mark.parametrize("state", ["PREPARED", "SUBMITTING", "UNKNOWN", "COMMITTED"])
def test_stop_and_revocation_retain_every_effect_hold(fixture, state):
    seed_effect(fixture, effect_state=state)
    fixture.lifecycle.stop(fixture.authorization_id, reason="synthetic stop")
    fixture.lifecycle.revoke(fixture.authorization_id, reason="synthetic revocation")
    row = fixture.db.execute("SELECT * FROM live_pilot_grants").fetchone()
    assert row["state"] == "REVOKED" and row["generation"] == 3
    assert fixture.db.execute("SELECT state,maximum_native_cost FROM live_pilot_effects").fetchone()[:] == (state, "2")
    with pytest.raises(AuthorityDenied):
        fixture.lifecycle.activate(fixture.authorization_id)
    fixture.lifecycle.revoke(fixture.authorization_id, reason="repeat synthetic revocation")
    assert fixture.db.execute("SELECT generation FROM live_pilot_grants").fetchone()[0] == 3


def test_recovery_survives_database_reopen_and_cannot_resume(fixture):
    seed_effect(fixture, effect_state="SUBMITTING", order_state="UNKNOWN")
    reopened = Database(fixture.db.path)
    try:
        controller = ProtectedPilotLifecycle(reopened, fixture.clock, scope=fixture.scope, source=fixture.source_pin)
        controller.recover(fixture.authorization_id)
        assert reopened.execute("SELECT state FROM live_pilot_grants").fetchone()[0] == "RECOVERY_REQUIRED"
        assert reopened.execute("SELECT state FROM live_pilot_effects").fetchone()[0] == "UNKNOWN"
        assert reopened.execute("SELECT profile,originator FROM pause_states").fetchone()[:] == ("MANAGE_ONLY", "owner")
        with pytest.raises(AuthorityDenied):
            controller.activate(fixture.authorization_id)
    finally:
        reopened.close()


def test_recovery_never_replaces_stop_or_revocation(fixture):
    seed_effect(fixture, effect_state="SUBMITTING", order_state="UNKNOWN")
    fixture.lifecycle.stop(fixture.authorization_id, reason="synthetic stop")
    fixture.lifecycle.recover(fixture.authorization_id)
    assert fixture.db.execute("SELECT state FROM live_pilot_grants").fetchone()[0] == "STOPPING"
    fixture.lifecycle.revoke(fixture.authorization_id, reason="synthetic revoke")
    fixture.lifecycle.recover(fixture.authorization_id)
    assert fixture.db.execute("SELECT state FROM live_pilot_grants").fetchone()[0] == "REVOKED"


def test_management_cannot_weaken_owner_flatten(fixture):
    fixture.db.execute("UPDATE pause_states SET profile='FLATTEN',originator='owner'")
    fixture.lifecycle.stop(fixture.authorization_id, reason="synthetic manage-only stop")
    assert fixture.db.execute("SELECT profile FROM pause_states").fetchone()[0] == "FLATTEN"


@pytest.mark.parametrize("order_state,expected", [("UNKNOWN", "UNKNOWN"), ("CANCEL_PENDING", "UNKNOWN"),
                                                  ("OPEN", "COMMITTED"), ("FILLED", "COMMITTED"),
                                                  ("REJECTED", "RELEASED"), ("CANCELLED", "RELEASED")])
def test_effect_reconciliation_uses_durable_order_state(fixture, order_state, expected):
    effect = seed_effect(fixture, effect_state="UNKNOWN", order_state=order_state)
    if expected == "RELEASED":
        synthetic_later_account_history(fixture)
    assert fixture.lifecycle.reconcile_effect(effect) == expected


def test_healthy_history_is_required_to_release_unknown_hold(fixture):
    effect = seed_effect(fixture, effect_state="UNKNOWN", order_state="REJECTED")
    fixture.clock.advance(61)
    with pytest.raises(StaleState, match="fresh account"):
        fixture.lifecycle.reconcile_effect(effect)
    assert fixture.db.execute("SELECT state FROM live_pilot_effects").fetchone()[0] == "UNKNOWN"


def test_acknowledged_exposure_never_replenishes_full_loss_quota(fixture):
    effect = seed_effect(fixture, effect_state="COMMITTED", order_state="CANCELLED")
    assert fixture.lifecycle.reconcile_effect(effect) == "COMMITTED"


def test_late_fill_does_not_remain_released(fixture):
    effect = seed_effect(fixture, effect_state="RELEASED", order_state="CANCELLED")
    fixture.db.execute("INSERT INTO fills VALUES ('synthetic-fill',?,?,?,?,?,'{}',?)",
                       (fixture.scope.venue, fixture.scope.account_id, "synthetic-trade", fixture.pid,
                        "synthetic-intent", utc_iso(fixture.clock.now())))
    assert fixture.lifecycle.reconcile_effect(effect) == "COMMITTED"


def test_stop_completion_requires_no_unknown_order_or_inventory(fixture):
    rules(fixture)
    effect = seed_effect(fixture, effect_state="UNKNOWN", order_state="UNKNOWN")
    fixture.lifecycle.stop(fixture.authorization_id, reason="synthetic flatten review")
    with pytest.raises(StaleState, match="flat"):
        fixture.lifecycle.complete_stop(fixture.authorization_id)
    fixture.db.execute("UPDATE order_intents SET state='CANCELLED'")
    synthetic_later_account_history(fixture)
    fixture.lifecycle.reconcile_effect(effect)
    Ledger(fixture.db, fixture.clock).deposit(fixture.pid, "BTC", Decimal("0.01"), "synthetic-position")
    with pytest.raises(StaleState, match="flat"):
        fixture.lifecycle.complete_stop(fixture.authorization_id)
    Ledger(fixture.db, fixture.clock).withdraw(fixture.pid, "BTC", Decimal("0.01"), "synthetic-position-close")
    synthetic_later_account_history(fixture)
    # A flat local book and a complete owned-history scan do not establish the
    # whole venue account. The concrete current collector lacks that proof.
    with pytest.raises(StaleState, match="flat"):
        fixture.lifecycle.complete_stop(fixture.authorization_id)
    assert fixture.db.execute("SELECT state FROM live_pilot_grants").fetchone()[0] == "STOPPING"
    assert fixture.db.execute("SELECT profile FROM pause_states").fetchone()[0] == "MANAGE_ONLY"


def test_revocation_does_not_allow_replacement_with_unresolved_effects(fixture):
    seed_effect(fixture, effect_state="UNKNOWN", order_state="UNKNOWN")
    fixture.lifecycle.revoke(fixture.authorization_id, reason="synthetic revocation")
    synthetic_later_account_history(fixture)
    fixture.document["owner_authorization"]["payload"]["authorization_id"] = "synthetic-replacement"
    source = fixture.source()
    replacement = ProtectedPilotLifecycle(fixture.db, fixture.clock, scope=fixture.scope, source=source)
    with pytest.raises(AuthorityDenied, match="prior pilot account exposure"):
        replacement.prepare()
    assert fixture.db.execute("SELECT COUNT(*) FROM live_pilot_grants").fetchone()[0] == 1


def test_revocation_does_not_allow_replacement_with_existing_inventory(fixture):
    fixture.lifecycle.revoke(fixture.authorization_id, reason="synthetic revocation")
    Ledger(fixture.db, fixture.clock).deposit(fixture.pid, "BTC", Decimal("0.01"), "synthetic-inventory")
    fixture.document["owner_authorization"]["payload"]["authorization_id"] = "synthetic-replacement"
    source = fixture.source()
    replacement = ProtectedPilotLifecycle(fixture.db, fixture.clock, scope=fixture.scope, source=source)
    with pytest.raises(AuthorityDenied, match="initially flat"):
        replacement.prepare()


def test_pilot_events_are_append_only(fixture):
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        fixture.db.execute("DELETE FROM live_pilot_events")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        fixture.db.execute("UPDATE live_pilot_events SET kind='forged'")


def test_persisted_scope_and_grant_tampering_are_refused(fixture):
    fixture.db.execute("UPDATE live_pilot_grants SET authorization_sha256=?", ("0" * 64,))
    with pytest.raises(AuthorityDenied, match="authorization changed"):
        fixture.lifecycle.stop(fixture.authorization_id, reason="synthetic corrupted row")


def test_native_intent_reader_binds_reservation_and_selected_version(fixture):
    rules(fixture)
    payload = intent(fixture)
    _, cost, _ = fixture.lifecycle._intent("synthetic-intent", fixture.grant)
    assert cost == Money(amount="2", currency="USD")
    fixture.db.execute("UPDATE active_versions SET artifact_hash=?", ("f" * 64,))
    with pytest.raises(AuthorityDenied, match="version"):
        fixture.lifecycle._intent("synthetic-intent", fixture.grant)
    fixture.db.execute("UPDATE active_versions SET artifact_hash=?", (fixture.scope.system_version_sha256,))
    payload["reserve_amount"] = "1"
    fixture.db.execute("UPDATE order_intents SET payload_json=?", (json.dumps(payload),))
    with pytest.raises(AuthorityDenied, match="complete held"):
        fixture.lifecycle._intent("synthetic-intent", fixture.grant)


@pytest.mark.parametrize("key,value", [("account_id", "different-account"), ("order_type", "market"),
                                       ("policy_revision", "different-policy"), ("reduce_only", 0),
                                       ("mode", "paper"), ("venue", "different-venue"), ("symbol", "ETH/USD")])
def test_unbounded_or_mismatched_native_intent_is_refused(fixture, key, value):
    rules(fixture)
    payload = intent(fixture)
    payload[key] = value
    fixture.db.execute("UPDATE order_intents SET payload_json=?", (json.dumps(payload),))
    with pytest.raises(AuthorityDenied, match="exactly scoped"):
        fixture.lifecycle._intent("synthetic-intent", fixture.grant)


def test_committed_and_unknown_holds_both_consume_full_native_envelope(fixture):
    seed_effect(fixture, effect_state="COMMITTED", cost="4")
    with pytest.raises(AuthorityDenied, match="full-loss"):
        fixture.lifecycle._envelope(fixture.authorization_id, fixture.grant, Money(amount="1.01", currency="USD"))
    fixture.db.execute("UPDATE live_pilot_effects SET state='UNKNOWN'")
    with pytest.raises(AuthorityDenied, match="full-loss"):
        fixture.lifecycle._envelope(fixture.authorization_id, fixture.grant, Money(amount="1.01", currency="USD"))


def test_expenses_are_all_real_deployment_costs_synthetic_receipts_excluded(fixture):
    now = utc_iso(fixture.clock.now())
    for reservation_id, amount, synthetic in [("synthetic-cost", "999", 1), ("actual-unknown", "1.5", 0)]:
        fixture.db.execute(
            """INSERT INTO budget_reservations
            (reservation_id,deployment_id,role,amount,currency,state,price_card_id,purpose,synthetic,created_at,updated_at)
            VALUES (?,?,'leader',?,'EUR','UNCERTAIN','synthetic-card','synthetic-test',?,?,?)""",
            (reservation_id, fixture.scope.deployment_id, amount, synthetic, now, now),
        )
    fixture.lifecycle._envelope(fixture.authorization_id, fixture.grant, Money(amount="1", currency="USD"))
    fixture.db.execute("UPDATE budget_reservations SET amount='1.500000000000000001' WHERE synthetic=0")
    with pytest.raises(AuthorityDenied, match="daily"):
        fixture.lifecycle._envelope(fixture.authorization_id, fixture.grant, Money(amount="1", currency="USD"))


def test_owner_authorization_precision_revalidated_in_envelope(fixture):
    malformed = fixture.grant.model_copy(update={"maximum_loss": Money(amount="1e1000", currency="USD")})
    with pytest.raises(ValueError):
        validate_pilot_envelope(
            malformed, native_commitments=(), proposed_native_cost=Money(amount="1", currency="USD"),
            actual_expenses=(), now=fixture.clock.now(),
        )


def test_revoked_flat_grant_cannot_be_replaced_using_stale_account_history(fixture):
    fixture.lifecycle.revoke(fixture.authorization_id, reason="synthetic revocation")
    fixture.clock.advance(61)
    fixture.document["owner_authorization"]["payload"]["authorization_id"] = "synthetic-replacement"
    source = fixture.source()
    replacement = ProtectedPilotLifecycle(fixture.db, fixture.clock, scope=fixture.scope, source=source)
    with pytest.raises(StaleState, match="authenticated complete account"):
        replacement.prepare()


def test_reconciliation_cannot_release_a_different_account_effect(fixture):
    effect = seed_effect(fixture, effect_state="UNKNOWN", order_state="REJECTED")
    payload = json.loads(fixture.db.execute("SELECT payload_json FROM order_intents").fetchone()[0])
    payload["account_id"] = "other-account"
    fixture.db.execute("UPDATE order_intents SET payload_json=?", (json.dumps(payload),))
    with pytest.raises(AuthorityDenied, match="account binding"):
        fixture.lifecycle.reconcile_effect(effect)
    assert fixture.db.execute("SELECT state FROM live_pilot_effects").fetchone()[0] == "UNKNOWN"


def test_history_bound_refuses_incomplete_envelope(fixture):
    with pytest.raises(ValidationFailure, match="bounded window"):
        envelope(fixture, held=("0",) * 10001)


def test_acknowledged_commitment_survives_unknown_then_terminal_cancel(fixture):
    effect = seed_effect(fixture, effect_state="UNKNOWN", order_state="OPEN", cost="4")
    assert fixture.lifecycle.reconcile_effect(effect) == "COMMITTED"
    fixture.db.execute("UPDATE order_intents SET state='UNKNOWN'")
    assert fixture.lifecycle.reconcile_effect(effect) == "COMMITTED"
    fixture.db.execute("UPDATE order_intents SET state='CANCELLED'")
    assert fixture.lifecycle.reconcile_effect(effect) == "COMMITTED"
    with pytest.raises(AuthorityDenied, match="full-loss"):
        fixture.lifecycle._envelope(fixture.authorization_id, fixture.grant, Money(amount="1.01", currency="USD"))


@pytest.mark.parametrize("profile", ["PAUSE_DECISIONS", "NO_NEW_EXPOSURE", "MANAGE_ONLY", "CANCEL_ALL", "FLATTEN",
                                     "STOPPED"])
@pytest.mark.parametrize("operation", ["stop", "revoke", "recover"])
def test_older_grant_preserves_every_current_owner_pause_semantic(fixture, profile, operation):
    seed_effect(fixture, order_state="UNKNOWN")
    fixture.db.execute("UPDATE pause_states SET profile=?,originator='owner',reason='newer synthetic owner action',"
                       "achieved='existing owner achieved state',details_json='{}'", (profile,))
    before = tuple(fixture.db.execute("SELECT * FROM pause_states").fetchone())
    if operation == "recover":
        fixture.lifecycle.recover(fixture.authorization_id)
    else:
        getattr(fixture.lifecycle, operation)(fixture.authorization_id, reason="older synthetic grant stop policy")
    assert tuple(fixture.db.execute("SELECT * FROM pause_states").fetchone()) == before


def test_fresh_history_before_or_simultaneous_with_effect_cannot_release_it(fixture):
    effect = seed_effect(fixture, effect_state="UNKNOWN", order_state="CANCELLED")
    # The initial fixture health and new effect share a timestamp. That equality
    # does not prove the account scan happened after the possibly unknown effect.
    with pytest.raises(StaleState, match="fresh account"):
        fixture.lifecycle.reconcile_effect(effect)
    fixture.clock.advance(1)
    fixture.db.execute("UPDATE order_intents SET updated_at=?", (utc_iso(fixture.clock.now()),))
    with pytest.raises(StaleState, match="fresh account"):
        fixture.lifecycle.reconcile_effect(effect)
    synthetic_later_account_history(fixture)
    assert fixture.lifecycle.reconcile_effect(effect) == "RELEASED"


def test_later_native_attempt_invalidates_an_earlier_still_fresh_account_scan(fixture):
    effect = seed_effect(fixture, effect_state="UNKNOWN", order_state="CANCELLED")
    synthetic_later_account_history(fixture)
    fixture.clock.advance(1)
    fixture.db.execute("INSERT INTO order_attempts VALUES ('synthetic-attempt','synthetic-intent','cancel',?,'{}')",
                       (utc_iso(fixture.clock.now()),))
    with pytest.raises(StaleState, match="fresh account"):
        fixture.lifecycle.reconcile_effect(effect)
    synthetic_later_account_history(fixture)
    assert fixture.lifecycle.reconcile_effect(effect) == "RELEASED"


def test_a_legacy_bare_complete_label_is_not_owned_history_proof(fixture):
    effect = seed_effect(fixture, effect_state="UNKNOWN", order_state="CANCELLED")
    synthetic_later_account_history(fixture)
    health = json.loads(fixture.db.execute("SELECT payload_json FROM activity_events "
                                          "WHERE kind='execution_reconciliation_health'").fetchone()[0])
    health.pop("observation_scope")
    fixture.db.execute("UPDATE activity_events SET payload_json=? WHERE kind='execution_reconciliation_health'",
                       (json.dumps(health),))
    with pytest.raises(StaleState, match="fresh account"):
        fixture.lifecycle.reconcile_effect(effect)


def test_a_full_account_label_alone_cannot_finish_a_flat_pilot(fixture):
    rules(fixture)
    fixture.lifecycle.stop(fixture.authorization_id, reason="synthetic flat local stop")
    health = json.loads(fixture.db.execute("SELECT payload_json FROM activity_events "
                                          "WHERE kind='execution_reconciliation_health'").fetchone()[0])
    health["observation_scope"] = "complete_account"
    fixture.db.execute("UPDATE activity_events SET payload_json=? WHERE kind='execution_reconciliation_health'",
                       (json.dumps(health),))
    with pytest.raises(StaleState, match="flat"):
        fixture.lifecycle.complete_stop(fixture.authorization_id)
    assert fixture.db.execute("SELECT state FROM live_pilot_grants").fetchone()[0] == "STOPPING"


def test_arbitrary_external_verifier_callback_is_never_account_authority(fixture):
    invoked = []
    health = json.loads(fixture.db.execute("SELECT payload_json FROM activity_events "
                                          "WHERE kind='execution_reconciliation_health'").fetchone()[0])
    health["observation_scope"] = "complete_account"
    fixture.db.execute("UPDATE activity_events SET payload_json=? WHERE kind='execution_reconciliation_health'",
                       (json.dumps(health),))
    source = SimpleNamespace(verify=lambda **kwargs: invoked.append(kwargs))
    controller = ProtectedPilotLifecycle(fixture.db, fixture.clock, scope=fixture.scope, source=fixture.source_pin,
                                        upstream=LiveUpstreamSources(venue=source))
    assert controller._verified_account_state() is False
    assert invoked == []


def test_later_event_timestamp_cannot_republish_an_older_native_observation(fixture):
    effect = seed_effect(fixture, effect_state="UNKNOWN", order_state="CANCELLED")
    fixture.clock.advance(1)
    fixture.db.execute("UPDATE activity_events SET created_at=? WHERE kind='execution_reconciliation_health'",
                       (utc_iso(fixture.clock.now()),))
    with pytest.raises(StaleState, match="fresh account"):
        fixture.lifecycle.reconcile_effect(effect)
    synthetic_later_account_history(fixture)
    assert fixture.lifecycle.reconcile_effect(effect) == "RELEASED"
