"""Explicit protected review hooks never grant live or management authority."""

import pytest
from tests.integration import test_native_incident_resolution as incident_tests

from trade_graph.application.execution import Execution
from trade_graph.application.incident_resolution import ProtectedNativeIncidentResolver
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import StaleState


@pytest.fixture
def incident(tmp_path):
    yield from incident_tests.fixture.__wrapped__(tmp_path)


def bound_execution(value, **changes):
    options = {
        "venue": value.execution.venue,
        "account_id": value.execution.account_id,
        "mode": "live",
        "native_incident_resolver": value.resolver,
        **changes,
    }
    return Execution(value.db, value.ledger, value.clock, value.execution.broker, **options)


def test_default_sticky_latch_and_pending_native_proof_remain_closed(incident):
    hooked = bound_execution(incident)
    assert incident.execution._native_cost_limits_blocked()
    assert hooked._native_cost_limits_blocked()
    assert hooked.profile(incident.runtime.portfolio_id) == "MANAGE_ONLY"


def test_mechanical_retained_receipt_only_clears_explicit_hook_and_reblocks_drift(incident):
    # This pre-existing signed historical incident tests receipt mechanics. It is
    # not an authenticated native collection or live authorization success.
    hooked = bound_execution(incident)
    incident_tests.seed_receipt(incident)
    assert not hooked._native_cost_limits_blocked()
    assert incident.execution._native_cost_limits_blocked()
    hooked._set_reconciliation_health(incomplete=False, reason="synthetic mechanical incident")
    assert hooked.profile(incident.runtime.portfolio_id) == "MANAGE_ONLY"
    assert incident.db.execute("SELECT count(*) FROM order_attempts").fetchone()[0] == 0
    incident.resolver.clock = FrozenClock(incident.clock.now())
    assert hooked._native_cost_limits_blocked()


@pytest.mark.parametrize("change", ["mode", "venue", "account", "clock", "database", "subclass", "callback"])
def test_misbound_or_caller_supplied_review_cannot_replace_sticky_latch(incident, change):
    options = {}
    resolver = incident.resolver
    if change == "mode":
        options["mode"] = "paper"
    elif change == "venue":
        options["venue"] = "other"
    elif change == "account":
        options["account_id"] = "other"
    elif change == "clock":
        resolver.clock = FrozenClock(incident.clock.now())
    elif change == "database":
        resolver.database = object()
    elif change == "subclass":
        class CallerResolver(ProtectedNativeIncidentResolver):
            def blocked(self):
                return False
        options["native_incident_resolver"] = CallerResolver(
            incident.db, incident.clock, scope=resolver.scope, source=None, receipt_key=b"x" * 32,
        )
    else:
        options["native_incident_resolver"] = lambda: False
    with pytest.raises(ValueError, match="exact protected native incident"):
        bound_execution(incident, **options)


def test_review_capacity_failure_keeps_native_facts_and_management_latch(incident, monkeypatch):
    hooked = bound_execution(incident)
    before = [dict(row) for row in incident.db.execute("SELECT * FROM fills")]
    def unavailable(*args):
        raise StaleState("bounded review source unavailable")
    monkeypatch.setattr(incident.resolver, "_rows", unavailable)
    assert hooked._native_cost_limits_blocked()
    hooked._set_reconciliation_health(incomplete=False, reason="incident scan finished")
    assert hooked._reconciliation_blocked()
    assert [dict(row) for row in incident.db.execute("SELECT * FROM fills")] == before
