"""Concrete closed gate in execution; historical active rows are synthetic only."""

import asyncio
import json
import sqlite3
from decimal import Decimal
from types import SimpleNamespace

import pytest
from tests.integration.test_execution import _decision
from tests.integration.test_execution_reconciliation_ordering import HistoryBroker, _intent
from tests.integration.test_live_pilot import fixture as fixture
from tests.integration.test_live_pilot import seed_effect

from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import CancelResult, OrderLookupResult
from trade_graph.domain.clock import FrozenClock, utc_iso
from trade_graph.domain.errors import AuthorityDenied


class ObservedBroker(HistoryBroker):
    def __init__(self, scope):
        super().__init__(venue=scope.venue, account=scope.account_id, mode="live")
        self.capability_calls = 0

    async def capabilities(self):
        self.capability_calls += 1
        return await super().capabilities()

    async def cancel(self, request):
        self.calls.append(("cancel", request.intent_id))
        self.statuses[request.client_order_id] = OrderLookupResult(status="cancelled")
        return CancelResult(status="cancelled")


def execution(fixture, *, attached=True):
    broker = ObservedBroker(fixture.scope)
    arguments = ({"pilot_lifecycle": fixture.lifecycle, "pilot_authorization_id": fixture.authorization_id}
                 if attached else {})
    service = Execution(fixture.db, Ledger(fixture.db, fixture.clock), fixture.clock, broker,
                        venue=fixture.scope.venue, account_id=fixture.scope.account_id, mode="live", **arguments)
    return service, broker


@pytest.mark.parametrize("attached,state", [(False, "PENDING"), (True, "PENDING"), (True, "ACTIVE"), (True, "REVOKED")])
def test_live_increase_refuses_before_any_intent_financial_reservation_or_outbox(fixture, attached, state):
    service, broker = execution(fixture, attached=attached)
    fixture.db.execute("UPDATE live_pilot_grants SET state=?", (state,))
    before = {table: fixture.db.execute("SELECT count(*) FROM " + table).fetchone()[0]
              for table in ("decisions", "order_intents", "position_reservations", "outbox", "order_attempts",
                            "live_pilot_effects", "live_pilot_events")}
    decision = _decision(fixture.clock, fixture.pid, mode="live")
    with pytest.raises(AuthorityDenied, match="pilot"):
        service.authorize(fixture.pid, decision)
    assert {table: fixture.db.execute("SELECT count(*) FROM " + table).fetchone()[0] for table in before} == before
    assert broker.calls == [] and broker.capability_calls == 0


@pytest.mark.parametrize("attached", [False, True])
def test_existing_unsent_live_increase_cannot_reach_capabilities_attempt_or_submit(fixture, attached):
    service, broker = execution(fixture, attached=attached)
    fixture.db.execute("UPDATE live_pilot_grants SET state='ACTIVE',generation=1")
    old = _intent(fixture.db, fixture.clock, fixture.pid, "synthetic-old-pending", status="SUBMISSION_PENDING",
                  account=fixture.scope.account_id, venue=fixture.scope.venue, mode="live")
    before = tuple(fixture.db.execute("SELECT * FROM order_intents WHERE intent_id=?", (old.intent_id,)).fetchone())
    assert asyncio.run(service.dispatch()) == 0
    after = tuple(fixture.db.execute("SELECT * FROM order_intents WHERE intent_id=?", (old.intent_id,)).fetchone())
    assert after == before
    assert fixture.db.execute("SELECT state FROM position_reservations").fetchone()[0] == "held"
    assert fixture.db.execute("SELECT status FROM outbox").fetchone()[0] == "pending"
    assert fixture.db.execute("SELECT count(*) FROM order_attempts").fetchone()[0] == 0
    assert broker.calls == [] and broker.capability_calls == 0
    with pytest.raises(AuthorityDenied, match="pilot"):
        service._mark_submitting(old.intent_id)
    assert fixture.db.execute("SELECT count(*) FROM order_attempts").fetchone()[0] == 0


def test_startup_closes_historical_grant_and_reconciles_unknown_without_replacement(fixture):
    service, broker = execution(fixture)
    old = _intent(fixture.db, fixture.clock, fixture.pid, "synthetic-unknown", status="UNKNOWN",
                  account=fixture.scope.account_id, venue=fixture.scope.venue, mode="live")
    now = utc_iso(fixture.clock.now())
    fixture.db.execute("UPDATE live_pilot_grants SET state='ACTIVE',generation=1")
    fixture.db.execute("INSERT INTO live_pilot_effects VALUES (?,?,?,?,?,?,?,'SUBMITTING',?,?)",
                       ("synthetic-old-effect", fixture.authorization_id, old.intent_id, 1, "0" * 64,
                        "USD", "1", now, now))
    asyncio.run(service.startup())
    assert fixture.db.execute("SELECT state FROM live_pilot_grants").fetchone()[0] == "RECOVERY_REQUIRED"
    assert fixture.db.execute("SELECT state FROM live_pilot_effects").fetchone()[0] == "UNKNOWN"
    assert fixture.db.execute("SELECT profile FROM pause_states").fetchone()[0] == "MANAGE_ONLY"
    assert service.intent_state(old.intent_id) == "UNKNOWN"
    assert fixture.db.execute("SELECT count(*) FROM order_intents").fetchone()[0] == 1
    assert fixture.db.execute("SELECT count(*) FROM order_attempts").fetchone()[0] == 0
    assert broker.calls == [("status", old.client_order_id), ("fills", None)]
    assert broker.capability_calls == 0


@pytest.mark.parametrize("mismatch", ["callback", "clock", "account", "venue", "mode", "missing_id"])
def test_model_callback_or_mismatched_execution_scope_cannot_be_adopted(fixture, mismatch):
    broker = ObservedBroker(fixture.scope)
    arguments = {"pilot_lifecycle": fixture.lifecycle, "pilot_authorization_id": fixture.authorization_id,
                 "account_id": fixture.scope.account_id, "venue": fixture.scope.venue, "mode": "live"}
    clock = fixture.clock
    if mismatch == "callback":
        arguments["pilot_lifecycle"] = SimpleNamespace(assert_increase_authority=lambda *a, **k: True)
    elif mismatch == "clock":
        clock = FrozenClock(fixture.clock.now())
    elif mismatch == "missing_id":
        arguments.pop("pilot_authorization_id")
    else:
        arguments["account_id" if mismatch == "account" else mismatch] = "other" if mismatch != "mode" else "paper"
    with pytest.raises(ValueError, match="pilot"):
        Execution(fixture.db, Ledger(fixture.db, clock), clock, broker, **arguments)
    assert broker.calls == [] and broker.capability_calls == 0


def test_scoped_pilot_cannot_authorize_another_symbol_or_portfolio(fixture):
    service, _ = execution(fixture)
    fixture.db.execute("UPDATE live_pilot_grants SET state='ACTIVE',generation=1")
    for portfolio, symbol in (("other-portfolio", fixture.scope.symbol), (fixture.pid, "ETH/USD")):
        with pytest.raises(AuthorityDenied, match="binding mismatch"):
            service._assert_pilot_increase(portfolio, symbol)


@pytest.mark.parametrize("mismatch", ["mode", "account", "venue"])
def test_other_execution_scope_cannot_directly_begin_a_retained_live_intent(fixture, mismatch):
    old = _intent(fixture.db, fixture.clock, fixture.pid, "synthetic-other-scope", status="SUBMISSION_PENDING",
                  account=fixture.scope.account_id, venue=fixture.scope.venue, mode="live")
    mode = "paper" if mismatch == "mode" else "live"
    broker = ObservedBroker(fixture.scope)
    service = Execution(fixture.db, Ledger(fixture.db, fixture.clock), fixture.clock, broker,
                        venue="other" if mismatch == "venue" else fixture.scope.venue,
                        account_id="other" if mismatch == "account" else fixture.scope.account_id, mode=mode)
    with pytest.raises(AuthorityDenied, match="execution scope"):
        service._mark_submitting(old.intent_id)
    assert service.intent_state(old.intent_id) == "SUBMISSION_PENDING"
    assert fixture.db.execute("SELECT count(*) FROM order_attempts").fetchone()[0] == 0
    assert broker.calls == []


def test_durable_attempt_binding_is_idempotent_and_cannot_choose_another_attempt(fixture):
    effect = seed_effect(fixture, effect_state="SUBMITTING", order_state="SUBMITTING")
    now = utc_iso(fixture.clock.now())
    fixture.db.execute("INSERT INTO order_attempts VALUES ('synthetic-attempt','synthetic-intent','submit',?,'{}')",
                       (now,))
    fixture.lifecycle.record_submission_attempt(effect, "synthetic-attempt")
    fixture.lifecycle.record_submission_attempt(effect, "synthetic-attempt")
    assert fixture.db.execute("SELECT count(*) FROM live_pilot_events WHERE kind='attempt_bound'").fetchone()[0] == 1
    fixture.db.execute("INSERT INTO order_attempts VALUES ('synthetic-new-attempt','synthetic-intent','submit',?,'{}')",
                       (now,))
    with pytest.raises(AuthorityDenied, match="replacement attempt"):
        fixture.lifecycle.record_submission_attempt(effect, "synthetic-new-attempt")
    with pytest.raises(AuthorityDenied, match="actual persisted"):
        fixture.lifecycle.record_submission_attempt(effect, "nonexistent-attempt")


def test_revoked_attempt_cannot_bind_or_restart_dispatch(fixture):
    effect = seed_effect(fixture, effect_state="SUBMITTING", order_state="SUBMITTING")
    fixture.lifecycle.revoke(fixture.authorization_id, reason="synthetic before transport")
    fixture.db.execute("INSERT INTO order_attempts VALUES ('synthetic-attempt','synthetic-intent','submit',?,'{}')",
                       (utc_iso(fixture.clock.now()),))
    with pytest.raises(AuthorityDenied, match="revoked"):
        fixture.lifecycle.record_submission_attempt(effect, "synthetic-attempt")
    assert fixture.db.execute("SELECT state FROM live_pilot_effects").fetchone()[0] == "SUBMITTING"


def test_pending_native_terminal_release_waits_an_actual_later_scan(fixture):
    service, _ = execution(fixture)
    effect = seed_effect(fixture, effect_state="UNKNOWN", order_state="CANCELLED")
    service._sync_pilot_effect("synthetic-intent")
    row = fixture.db.execute("SELECT state FROM live_pilot_effects WHERE effect_id=?", (effect,)).fetchone()
    assert row[0] == "UNKNOWN"
    assert service.intent_state("synthetic-intent") == "CANCELLED"
    assert fixture.grant.maximum_loss.amount == Decimal("5")


def test_closed_increase_does_not_interrupt_existing_reduction_management(fixture):
    service, broker = execution(fixture)
    fixture.lifecycle.revoke(fixture.authorization_id, reason="synthetic management-only grant")
    buy = _intent(fixture.db, fixture.clock, fixture.pid, "synthetic-buy", status="SUBMISSION_PENDING",
                  account=fixture.scope.account_id, venue=fixture.scope.venue, mode="live")
    sell = _intent(fixture.db, fixture.clock, fixture.pid, "synthetic-owned-reduction", side="sell",
                   status="SUBMISSION_PENDING", account=fixture.scope.account_id,
                   venue=fixture.scope.venue, mode="live")
    # This is an already owned, synthetic reduction exercising the existing
    # management path through a scripted broker; no new live approval is issued.
    assert asyncio.run(service.dispatch()) == 1
    assert service.intent_state(buy.intent_id) == "SUBMISSION_PENDING"
    assert service.intent_state(sell.intent_id) == "OPEN"
    assert broker.calls == [("submit", sell.intent_id)] and broker.capability_calls == 1
    assert fixture.db.execute("SELECT count(*) FROM order_attempts").fetchone()[0] == 1


@pytest.mark.parametrize("failure", ["unknown_id", "scope_tamper", "malformed_grant", "source_drift", "expired"])
def test_invalid_pilot_authority_startup_preserves_holds_and_existing_management(fixture, failure):
    service, broker = execution(fixture)
    fixture.db.execute("UPDATE live_pilot_grants SET state='ACTIVE',generation=1")
    buy = _intent(fixture.db, fixture.clock, fixture.pid, "synthetic-closed-buy", status="SUBMISSION_PENDING",
                  account=fixture.scope.account_id, venue=fixture.scope.venue, mode="live")
    unknown = _intent(fixture.db, fixture.clock, fixture.pid, "synthetic-existing-unknown", status="UNKNOWN",
                      account=fixture.scope.account_id, venue=fixture.scope.venue, mode="live")
    sell = _intent(fixture.db, fixture.clock, fixture.pid, "synthetic-existing-reduction", side="sell",
                   status="SUBMISSION_PENDING", account=fixture.scope.account_id,
                   venue=fixture.scope.venue, mode="live")
    now = utc_iso(fixture.clock.now())
    fixture.db.execute("INSERT INTO live_pilot_effects VALUES (?,?,?,?,?,?,?,'UNKNOWN',?,?)",
                       ("synthetic-management-hold", fixture.authorization_id, unknown.intent_id, 1,
                        "0" * 64, "USD", "1", now, now))
    if failure == "unknown_id":
        service._pilot_authorization_id = "synthetic-missing-authorization"
    elif failure == "scope_tamper":
        fixture.db.execute("UPDATE live_pilot_grants SET scope_json='{}'")
    elif failure == "malformed_grant":
        fixture.db.execute("UPDATE live_pilot_grants SET authorization_json='{}'")
    elif failure == "source_drift":
        fixture.source_pin.path.write_bytes(b"{}")
    else:
        fixture.clock.advance((fixture.grant.expires_at - fixture.clock.now()).total_seconds() + 1)
    grant_before = tuple(fixture.db.execute("SELECT * FROM live_pilot_grants").fetchone())
    asyncio.run(service.startup())
    assert service.intent_state(buy.intent_id) == "SUBMISSION_PENDING"
    assert service.intent_state(unknown.intent_id) == "UNKNOWN"
    assert service.intent_state(sell.intent_id) == "OPEN"
    assert fixture.db.execute("SELECT state FROM live_pilot_effects").fetchone()[0] == "UNKNOWN"
    assert fixture.db.execute("SELECT state FROM position_reservations WHERE intent_id=?",
                              (unknown.intent_id,)).fetchone()[0] == "held"
    assert ("status", unknown.client_order_id) in broker.calls
    assert ("fills", None) in broker.calls
    assert [value for kind, value in broker.calls if kind == "submit"] == [sell.intent_id]
    incidents = fixture.db.execute("SELECT payload_json FROM activity_events "
                                   "WHERE kind='pilot_management_authority_unavailable'").fetchall()
    if failure in {"unknown_id", "scope_tamper", "malformed_grant"}:
        assert tuple(fixture.db.execute("SELECT * FROM live_pilot_grants").fetchone()) == grant_before
        assert incidents
        for row in incidents:
            payload = json.loads(row[0])
            assert set(payload) == {"mode", "reason", "operation"}
            assert payload["mode"] == "live" and payload["reason"] == "pilot_authority_invalid"
            assert payload["operation"] in {"recover", "reconcile_effect"}
    else:
        assert fixture.db.execute("SELECT state FROM live_pilot_grants").fetchone()[0] == "RECOVERY_REQUIRED"
    # Independently approved native cancellation does not consume pilot authority.
    asyncio.run(service.cancel(unknown.intent_id))
    assert ("cancel", unknown.intent_id) in broker.calls
    assert service.intent_state(unknown.intent_id) == "CANCELLED"
    assert fixture.db.execute("SELECT state FROM live_pilot_effects").fetchone()[0] == "UNKNOWN"


def test_effect_scope_drift_is_recorded_without_blocking_native_management(fixture):
    service, broker = execution(fixture)
    unknown = _intent(fixture.db, fixture.clock, fixture.pid, "synthetic-drifted-native", status="UNKNOWN",
                      account=fixture.scope.account_id, venue=fixture.scope.venue, mode="live")
    payload = json.loads(fixture.db.execute("SELECT payload_json FROM order_intents").fetchone()[0])
    payload["symbol"] = "ETH/USD"
    fixture.db.execute("UPDATE order_intents SET payload_json=?", (json.dumps(payload),))
    now = utc_iso(fixture.clock.now())
    fixture.db.execute("INSERT INTO live_pilot_effects VALUES (?,?,?,?,?,?,?,'UNKNOWN',?,?)",
                       ("synthetic-drifted-hold", fixture.authorization_id, unknown.intent_id, 0,
                        "0" * 64, "USD", "1", now, now))
    effect_before = tuple(fixture.db.execute("SELECT * FROM live_pilot_effects").fetchone())
    asyncio.run(service.reconcile())
    assert tuple(fixture.db.execute("SELECT * FROM live_pilot_effects").fetchone()) == effect_before
    assert service.intent_state(unknown.intent_id) == "UNKNOWN"
    assert broker.calls == [("status", unknown.client_order_id), ("fills", None)]
    incident = fixture.db.execute("SELECT payload_json FROM activity_events "
                                  "WHERE kind='pilot_management_authority_unavailable'").fetchone()
    assert json.loads(incident[0])["operation"] == "reconcile_effect"


def test_pilot_database_failure_is_not_hidden_as_successful_management(fixture):
    service, broker = execution(fixture)
    fixture.db.execute("UPDATE live_pilot_grants SET state='ACTIVE',generation=1")
    before = tuple(fixture.db.execute("SELECT * FROM live_pilot_grants").fetchone())
    fixture.db.execute("DROP TABLE live_pilot_events")
    with pytest.raises(sqlite3.OperationalError):
        asyncio.run(service.startup())
    assert tuple(fixture.db.execute("SELECT * FROM live_pilot_grants").fetchone()) == before
    assert broker.calls == []
