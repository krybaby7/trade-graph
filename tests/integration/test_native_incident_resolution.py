"""Actual SQLite review mechanics with retained synthetic native captures.

Synthetic transport never resolves an incident. Signed historical receipts below
are explicitly seeded mechanical fixtures, not created by the authority service.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_kraken_live_adapter import _broker, _ledgers, _trade
from tests.integration.test_venue_conformance import Fixture as VenueFixture

from trade_graph.adapters.persistence.db import Database
from trade_graph.adapters.persistence.native_incident_schema import STATEMENTS
from trade_graph.api.app import create_app
from trade_graph.api.auth import issue_session
from trade_graph.api.controls import _Commands
from trade_graph.application.authority import AuthorityRecord, paper_owner_policy
from trade_graph.application.broker_identity import DurableBrokerIdentity
from trade_graph.application.execution import Execution
from trade_graph.application.incident_resolution import (
    NativeIncidentResolutionCommand,
    ProtectedNativeIncidentResolver,
    incident_controller_sha256,
)
from trade_graph.application.ledger import Ledger
from trade_graph.application.venue_conformance import PinnedReadOnlyAuthority, _canonical, _mac
from trade_graph.domain.clock import FrozenClock, utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState
from trade_graph.live_gate import LivePilotScope


@pytest.fixture
def fixture(tmp_path):
    venue = VenueFixture(tmp_path)
    clock = FrozenClock(datetime.now(UTC) - timedelta(seconds=3))
    db = Database(tmp_path / "incident.sqlite")
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='native_incident_resolutions'").fetchone():
        for statement in STATEMENTS:
            db.execute(statement)
    ledger = Ledger(db, clock)
    portfolio = ledger.create_portfolio(reporting_currency="USD", mode="live", portfolio_id="fixture-portfolio")
    ledger.deposit(portfolio, "USD", Decimal(100), "synthetic-opening")
    policy = paper_owner_policy(venues=["kraken"], symbols=["BTC/USD"])
    AuthorityRecord(db, clock).install_policy(policy, role="owner")
    scope = LivePilotScope(
        **dict(
            venue.scope.model_dump(exclude={"mode"}),
            policy_revision=policy.revision_id,
            policy_sha256=hashlib.sha256(policy.model_dump_json().encode()).hexdigest(),
        )
    )
    venue.scope = venue.scope.model_copy(
        update={"policy_revision": scope.policy_revision, "policy_sha256": scope.policy_sha256}
    )
    venue.grant = venue.grant.model_copy(update={"scope": venue.scope})
    payload = venue.grant.model_dump(mode="json")
    raw = _canonical({"payload": payload, "signature": _mac(venue.owner_key, "owner-grant", payload)})
    venue.authority.path.chmod(0o600)
    venue.authority.path.write_bytes(raw)
    venue.authority.path.chmod(0o400)
    venue.authority = PinnedReadOnlyAuthority(venue.authority.path, hashlib.sha256(raw).hexdigest(), venue.owner_key)
    db.execute(
        "INSERT INTO active_versions (portfolio_id,version_id,artifact_hash,fingerprint_json,activated_at) "
        "VALUES (?, 'synthetic-version', ?, '{}', ?)",
        (portfolio, scope.system_version_sha256, utc_iso(clock.now())),
    )
    broker = _broker(venue.rest)
    broker.account_id = scope.account_id
    broker.clock = clock
    venue.rest.results["AssetPairs"]["XXBTZUSD"]["cost_decimals"] = 2
    execution = Execution(db, ledger, clock, broker, venue=scope.venue, account_id=scope.account_id, mode="live")
    execution.register_instrument(asyncio.run(broker.instruments())[0])
    intent = {
        "intent_id": "synthetic-intent",
        "portfolio_id": portfolio,
        "venue": "kraken",
        "mode": "live",
        "account_id": scope.account_id,
        "symbol": "BTC/USD",
        "side": "buy",
        "order_type": "limit",
        "quantity": "0.1",
        "limit_price": "99.9",
        "client_order_id": "client-1",
        "snapshot_id": "synthetic",
        "eligible_after_utc": utc_iso(clock.now()),
        "reserve_asset": "USD",
        "reserve_amount": "10.08",
        "venue_order_id": "order-1",
    }
    db.execute(
        "INSERT INTO order_intents VALUES (?,?,?,?,?,?,?,?)",
        (
            "synthetic-intent",
            portfolio,
            "client-1",
            "CANCELLED",
            "BTC/USD",
            json.dumps(intent),
            utc_iso(clock.now()),
            utc_iso(clock.now()),
        ),
    )
    db.execute(
        "INSERT INTO position_reservations VALUES (?,?,?,?,?,?,?)",
        (
            "synthetic-reserve",
            portfolio,
            "synthetic-intent",
            "USD",
            "10.08",
            "held",
            utc_iso(clock.now()),
        ),
    )
    clock.advance(1)
    trade = _trade(1, time=str(clock.now().timestamp()))
    trade["price"] = "99.99"
    venue.rest.results["TradesHistory"] = {"trades": {"trade-1": trade}, "count": 1}
    venue.rest.results["QueryLedgers"] = _ledgers(1)
    fill = asyncio.run(broker.fills_since(None)).fills[0].model_copy(update={"intent_id": "synthetic-intent"})
    execution.record_fill(fill)
    execution.set_pause(portfolio, "MANAGE_ONLY", "owner", "synthetic review")
    venue.rest.results["BalanceEx"] = {
        "ZUSD": {"balance": "89.92", "hold_trade": "0"},
        "XXBT": {"balance": "0.1", "hold_trade": "0"},
    }
    source = asyncio.run(venue.collect())
    clock.advance(4)
    resolver = ProtectedNativeIncidentResolver(db, clock, scope=scope, source=source, receipt_key=b"synthetic-key-" * 4)
    row = db.execute(
        "SELECT event_id FROM activity_events WHERE kind='native_fill_execution_limit_discrepancy'"
    ).fetchone()
    runtime = SimpleNamespace(
        database=db,
        clock=clock,
        ledger=ledger,
        execution=execution,
        portfolio_id=portfolio,
        deployment_id=scope.deployment_id,
        native_incident_resolver=resolver,
    )
    value = SimpleNamespace(
        db=db,
        ledger=ledger,
        clock=clock,
        resolver=resolver,
        runtime=runtime,
        execution=execution,
        incident_id=row[0],
        source=source,
        venue=venue,
    )
    yield value
    db.close()


def body(fixture, **changes):
    values = dict(
        request_id="synthetic-review",
        expected_revision=0,
        incident_id=fixture.incident_id,
        expected_incident_sha256=fixture.resolver._incident(fixture.incident_id)[1],
        expected_financial_sha256=fixture.resolver.current_financial_sha256(),
        expected_generation=0,
        acknowledgement="accept_preserved_native_effects_after_review",
        reason="synthetic private owner review",
    )
    values.update(changes)
    return NativeIncidentResolutionCommand(**values)


def seed_receipt(fixture):
    """Seed a historical signed test receipt without claiming native authority."""
    receipt = {
        "schema_version": 1,
        "resolution_id": "synthetic-resolution",
        "incident_id": fixture.incident_id,
        "generation": 1,
        "command_id": "synthetic-review",
        "owner_revision": 1,
        "scope": fixture.resolver.scope.model_dump(mode="json"),
        "incident_sha256": fixture.resolver._incident(fixture.incident_id)[1],
        "financial_sha256": fixture.resolver.current_financial_sha256(),
        "observation_sha256": fixture.source.observation_sha256,
        "controller_sha256": incident_controller_sha256(),
        "observed_at": utc_iso(fixture.clock.now()),
        "created_at": utc_iso(fixture.clock.now()),
        "execution_authority": False,
        "native_transport_authenticated": True,
        "clearance": "VERIFIED_NATIVE_EFFECTS",
        "complete_account_verified": False,
    }
    command = body(fixture)
    with fixture.db.immediate():
        commands = _Commands(fixture.runtime)
        commands.begin(
            f"owner:{fixture.resolver.scope.deployment_id}", command, "resolve-native-incident", lambda: None
        )
        receipt["owner_request_sha256"] = fixture.db.execute(
            "SELECT request_hash FROM dashboard_commands WHERE command_id=?", (receipt["command_id"],)
        ).fetchone()[0]
        fixture.db.execute(
            "INSERT INTO native_incident_resolutions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                receipt["resolution_id"],
                receipt["incident_id"],
                1,
                receipt["command_id"],
                1,
                fixture.resolver.scope.model_dump_json(),
                receipt["incident_sha256"],
                receipt["financial_sha256"],
                receipt["observation_sha256"],
                receipt["controller_sha256"],
                _canonical(receipt).decode(),
                fixture.resolver._mac("resolution", receipt),
                receipt["created_at"],
            ),
        )
        commands.finish(
            "synthetic-review", 1, {"resolution_id": receipt["resolution_id"], "execution_authority": False}
        )
    return receipt


def client(fixture):
    # Use the production session, role and CSRF middleware.
    app = create_app(fixture.runtime)
    token, csrf = issue_session(fixture.db, fixture.clock, "owner")
    leader, _ = issue_session(fixture.db, fixture.clock, "leader")
    return TestClient(app), token, csrf, leader


def test_current_real_books_and_retained_synthetic_native_facts_agree_without_external_authority(fixture):
    result = fixture.resolver.verify_current_projection()
    assert result.local_projection_consistent
    assert result.native_transport_authenticated is False
    assert result.complete_account_verified is False
    assert fixture.resolver.blocked()
    assert fixture.ledger.books(fixture.runtime.portfolio_id).cash_amount("USD") == Decimal("89.92")


def test_owner_review_retains_pending_synthetic_facts_without_clearing_incident(fixture):
    command = body(fixture)
    commands = _Commands(fixture.runtime)
    with fixture.db.immediate():
        commands.begin(
            f"owner:{fixture.resolver.scope.deployment_id}", command, "resolve-native-incident", lambda: None
        )
        result = fixture.resolver.resolve(command)
        commands.finish(command.request_id, 1, result)
    assert result["clearance"] == "PENDING_NATIVE_PROOF"
    assert result["incident_cleared"] is False
    assert fixture.db.execute("SELECT COUNT(*) FROM native_incident_resolutions").fetchone()[0] == 1
    assert fixture.db.execute("SELECT COUNT(*) FROM dashboard_commands").fetchone()[0] == 1
    assert fixture.resolver.blocked()
    assert fixture.execution.profile(fixture.runtime.portfolio_id) == "MANAGE_ONLY"


def test_no_direct_caller_role_or_success_boolean_can_resolve(fixture):
    with pytest.raises(AuthorityDenied, match="transaction"):
        fixture.resolver.resolve(body(fixture))
    with pytest.raises(AuthorityDenied, match="typed"):
        fixture.resolver.resolve({"owner": True, "verified": True})
    with pytest.raises(ValueError, match="exact"):
        ProtectedNativeIncidentResolver(
            fixture.db,
            fixture.clock,
            scope=fixture.resolver.scope,
            source=SimpleNamespace(verify=lambda **_: True),
            receipt_key=b"x" * 32,
        )


@pytest.mark.parametrize("endpoint", ["resolve-native-incident", "revoke-native-incident-resolution"])
def test_actual_http_sessions_require_owner_role_and_csrf_before_review(fixture, endpoint):
    api, owner, csrf, leader = client(fixture)
    document = (
        body(fixture).model_dump(mode="json")
        if endpoint == "resolve-native-incident"
        else {
            "request_id": "synthetic-revoke",
            "expected_revision": 0,
            "resolution_id": "unknown",
            "reason": "synthetic review",
        }
    )
    url = f"/api/v1/owner/{endpoint}"
    assert api.post(url, json=document).status_code == 401
    assert api.post(url, headers={"Authorization": f"Bearer {leader}"}, json=document).status_code == 403
    api.cookies.set("tg_session", owner)
    assert api.post(url, json=document).status_code == 403
    response = api.post(url, headers={"x-csrf-token": csrf}, json=document)
    assert response.status_code == (200 if endpoint == "resolve-native-incident" else 403)
    if response.status_code == 200:
        assert response.json()["incident_cleared"] is False
    assert fixture.resolver.blocked()


def test_historical_receipt_survives_restart_without_repeating_effect_or_resuming(fixture):
    receipt = seed_receipt(fixture)
    assert fixture.resolver.blocked() is False
    financial = fixture.resolver.current_financial_sha256()
    recovered = ProtectedNativeIncidentResolver(
        fixture.db, fixture.clock, scope=fixture.resolver.scope, source=None, receipt_key=fixture.resolver._key
    )
    assert recovered.blocked() is False
    assert recovered.current_financial_sha256() == financial
    assert fixture.execution.profile(fixture.runtime.portfolio_id) == "MANAGE_ONLY"
    assert fixture.db.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1
    assert fixture.db.execute("SELECT COUNT(*) FROM native_incident_resolutions").fetchone()[0] == 1
    assert receipt["execution_authority"] is False


def test_owner_revocation_is_atomic_idempotent_and_reblocks_across_restart(fixture):
    seed_receipt(fixture)
    api, owner, _, _ = client(fixture)
    headers = {"Authorization": f"Bearer {owner}"}
    document = {
        "request_id": "synthetic-revoke",
        "expected_revision": 1,
        "resolution_id": "synthetic-resolution",
        "reason": "synthetic owner revoked review",
    }
    response = api.post("/api/v1/owner/revoke-native-incident-resolution", headers=headers, json=document)
    assert response.status_code == 200, response.text
    assert response.json()["revoked"] is True
    assert fixture.resolver.blocked()
    assert (
        api.post("/api/v1/owner/revoke-native-incident-resolution", headers=headers, json=document).json()
        == response.json()
    )
    assert fixture.db.execute("SELECT COUNT(*) FROM native_incident_resolution_revocations").fetchone()[0] == 1
    assert fixture.db.execute("SELECT revision FROM dashboard_control_state").fetchone()[0] == 2
    altered = dict(document, reason="different review")
    assert api.post("/api/v1/owner/revoke-native-incident-resolution", headers=headers, json=altered).status_code == 409


@pytest.mark.parametrize(
    "mutation", ["fill", "event", "posting", "hold", "unknown", "scope", "controller", "key", "command"]
)
def test_new_or_corrupt_relevant_facts_reblock_historical_acknowledgement(fixture, mutation, monkeypatch):
    seed_receipt(fixture)
    assert fixture.resolver.blocked() is False
    if mutation == "fill":
        row = fixture.db.execute("SELECT document_json FROM fills").fetchone()
        document = json.loads(row[0])
        document["price"] = "100.1"
        fixture.db.execute("UPDATE fills SET document_json=?", (json.dumps(document),))
    elif mutation == "event":
        fixture.ledger.deposit(fixture.runtime.portfolio_id, "USD", Decimal(1), "synthetic-new-fact")
    elif mutation == "posting":
        fixture.db.execute("UPDATE journal_postings SET account='damaged' WHERE account='cash'")
    elif mutation == "hold":
        fixture.db.execute("UPDATE position_reservations SET amount='1',state='held'")
    elif mutation == "unknown":
        fixture.db.execute("UPDATE order_intents SET state='UNKNOWN'")
    elif mutation == "scope":
        fixture.resolver.scope = fixture.resolver.scope.model_copy(update={"policy_revision": "obsolete"})
    elif mutation == "controller":
        monkeypatch.setattr("trade_graph.application.incident_resolution.incident_controller_sha256", lambda: "f" * 64)
    elif mutation == "command":
        fixture.db.execute("UPDATE dashboard_commands SET request_hash=?", ("0" * 64,))
    else:
        fixture.resolver._key = b"different synthetic key" * 2
    assert fixture.resolver.blocked()
    assert fixture.db.execute("SELECT COUNT(*) FROM native_incident_resolutions").fetchone()[0] == 1


@pytest.mark.parametrize("table", ["native_incident_resolutions", "native_incident_resolution_revocations"])
def test_protected_receipt_tables_refuse_update_and_delete(fixture, table):
    seed_receipt(fixture)
    if table.endswith("revocations"):
        api, token, _, _ = client(fixture)
        result = api.post(
            "/api/v1/owner/revoke-native-incident-resolution",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "request_id": "revoke",
                "expected_revision": 1,
                "resolution_id": "synthetic-resolution",
                "reason": "synthetic",
            },
        )
        assert result.status_code == 200
    import sqlite3

    for statement in (f"UPDATE {table} SET created_at='damaged'", f"DELETE FROM {table}"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            fixture.db.execute(statement)


def test_current_projection_rejects_native_balance_difference_and_stale_source(fixture):
    fixture.ledger.deposit(fixture.runtime.portfolio_id, "USD", Decimal(1), "synthetic-unmatched-funding")
    with pytest.raises(StaleState, match="postdate"):
        fixture.resolver.verify_current_projection()
    fixture.clock.advance(61)
    with pytest.raises(ValueError, match="stale"):
        fixture.resolver.verify_current_projection()


def test_reconstructed_journal_comparison_ignores_ambient_decimal_precision(fixture):
    with localcontext() as context:
        context.prec = 3
        assert fixture.resolver.verify_current_projection().local_projection_consistent


def test_actual_owner_pending_receipt_retries_once_and_newer_revision_fences_changes(fixture):
    api, owner, _, _ = client(fixture)
    headers = {"Authorization": f"Bearer {owner}"}
    command = body(fixture).model_dump(mode="json")
    endpoint = "/api/v1/owner/resolve-native-incident"
    first = api.post(endpoint, headers=headers, json=command)
    assert first.status_code == 200, first.text
    assert first.json()["incident_cleared"] is False
    assert api.post(endpoint, headers=headers, json=command).json() == first.json()
    assert fixture.db.execute("SELECT COUNT(*) FROM native_incident_resolutions").fetchone()[0] == 1
    stale = dict(command, request_id="stale")
    assert api.post(endpoint, headers=headers, json=stale).status_code == 409
    new_generation = dict(command, request_id="next-review", expected_revision=1, expected_generation=1)
    next_result = api.post(endpoint, headers=headers, json=new_generation)
    assert next_result.status_code == 200, next_result.text
    assert next_result.json()["generation"] == 2
    assert fixture.resolver.blocked()


@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_incident_sha256", "f" * 64),
        ("expected_financial_sha256", "f" * 64),
        ("expected_generation", 2),
        ("incident_id", "not-an-incident"),
    ],
)
def test_wrong_compare_and_set_pins_abort_owner_receipt_atomically(fixture, field, value):
    api, owner, _, _ = client(fixture)
    document = body(fixture, **{field: value}).model_dump(mode="json")
    result = api.post(
        "/api/v1/owner/resolve-native-incident", headers={"Authorization": f"Bearer {owner}"}, json=document
    )
    assert result.status_code in {403, 409}
    assert fixture.db.execute("SELECT COUNT(*) FROM dashboard_commands").fetchone()[0] == 0
    assert fixture.db.execute("SELECT COUNT(*) FROM native_incident_resolutions").fetchone()[0] == 0
    assert fixture.resolver.blocked()


def test_unknown_pilot_hold_and_actual_native_balance_disagreement_block_review(fixture):
    # Append a past-dated local movement so freshness alone does not explain
    # refusal: actual native balance evidence fails the economic comparison.
    fixture.clock.advance(-4)
    fixture.ledger.deposit(fixture.runtime.portfolio_id, "USD", Decimal(1), "synthetic-unmatched-movement")
    fixture.clock.advance(4)
    with pytest.raises(StaleState, match="balances differ"):
        fixture.resolver.verify_current_projection()
    assert fixture.resolver.blocked()


def test_owner_running_management_cannot_acknowledge_incident(fixture):
    fixture.execution.set_pause(fixture.runtime.portfolio_id, "RUNNING", "owner", "synthetic")
    api, owner, _, _ = client(fixture)
    result = api.post(
        "/api/v1/owner/resolve-native-incident",
        headers={"Authorization": f"Bearer {owner}"},
        json=body(fixture).model_dump(mode="json"),
    )
    assert result.status_code == 403
    assert fixture.db.execute("SELECT COUNT(*) FROM native_incident_resolutions").fetchone()[0] == 0


CRASH_CHILD = r"""
import json, os, sys
from types import SimpleNamespace
from trade_graph.adapters.persistence.db import Database
from trade_graph.api.controls import _Commands
from trade_graph.application.execution import Execution
from trade_graph.application.incident_resolution import NativeIncidentResolutionCommand, ProtectedNativeIncidentResolver
from trade_graph.application.ledger import Ledger
from trade_graph.application.venue_conformance import PinnedVenueObservation
from trade_graph.domain.clock import FrozenClock, parse_utc
from trade_graph.live_gate import LivePilotScope
from pathlib import Path
configuration = json.loads(Path(sys.argv[1]).read_text())
db = Database(configuration['database'])
clock = FrozenClock(parse_utc(configuration['now']))
scope = LivePilotScope.model_validate(configuration['scope'])
source = PinnedVenueObservation(Path(configuration['source_path']), configuration['source_sha256'],
                                bytes.fromhex(configuration['collector_key']))
resolver = ProtectedNativeIncidentResolver(db, clock, scope=scope, source=source,
                                          receipt_key=bytes.fromhex(configuration['receipt_key']))
ledger = Ledger(db, clock)
execution = SimpleNamespace(pause=lambda portfolio: db.execute(
    'SELECT * FROM pause_states WHERE portfolio_id=?', (portfolio,)).fetchone())
runtime = SimpleNamespace(database=db, clock=clock, ledger=ledger, execution=execution,
                          portfolio_id=scope.portfolio_id, deployment_id=scope.deployment_id)
commands = _Commands(runtime)
body = NativeIncidentResolutionCommand.model_validate(configuration['body'])
with db.immediate():
    commands.begin('owner:' + scope.deployment_id, body, 'resolve-native-incident', lambda: None)
    result = resolver.resolve(body)
    commands.finish(body.request_id, 1, result)
    if configuration['phase'] == 'before_commit':
        os._exit(73)
os._exit(73)
"""


@pytest.mark.parametrize("phase", ["before_commit", "after_commit"])
def test_real_process_death_cannot_separate_owner_receipt_from_pending_acknowledgement(fixture, tmp_path, phase):
    configuration = {
        "database": str(fixture.db.path),
        "now": utc_iso(fixture.clock.now()),
        "scope": fixture.resolver.scope.model_dump(mode="json"),
        "source_path": str(fixture.source.path),
        "source_sha256": fixture.source.observation_sha256,
        "collector_key": fixture.source.collector_key.hex(),
        "receipt_key": fixture.resolver._key.hex(),
        "body": body(fixture).model_dump(mode="json"),
        "phase": phase,
    }
    private = tmp_path / "synthetic-child.json"
    private.write_text(json.dumps(configuration))
    private.chmod(0o600)
    result = subprocess.run(
        [sys.executable, "-c", CRASH_CHILD, str(private)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 73, result.stdout + result.stderr
    expected = 0 if phase == "before_commit" else 1
    assert fixture.db.execute("SELECT COUNT(*) FROM dashboard_commands").fetchone()[0] == expected
    assert fixture.db.execute("SELECT COUNT(*) FROM native_incident_resolutions").fetchone()[0] == expected
    assert fixture.resolver.blocked()
    api, owner, _, _ = client(fixture)
    response = api.post(
        "/api/v1/owner/resolve-native-incident",
        headers={"Authorization": f"Bearer {owner}"},
        json=configuration["body"],
    )
    assert response.status_code == 200, response.text
    assert fixture.db.execute("SELECT COUNT(*) FROM native_incident_resolutions").fetchone()[0] == 1
    assert fixture.db.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1


def add_fee_reservation(fixture, *, state="released", current="0", original="0.08", plan="a" * 64):
    fixture.db.execute(
        "INSERT INTO native_fee_reservations VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "synthetic-secondary-fee",
            fixture.runtime.portfolio_id,
            "synthetic-intent",
            "USD",
            original,
            current,
            state,
            plan,
            utc_iso(fixture.clock.now()),
        ),
    )


@pytest.mark.parametrize("mutation", ["new_released", "held", "negative", "excess", "plan"])
def test_secondary_fee_authority_history_is_bound_and_cannot_be_ignored(fixture, mutation):
    seed_receipt(fixture)
    if mutation == "new_released":
        add_fee_reservation(fixture)
        assert fixture.resolver.current_financial_sha256()
    elif mutation == "held":
        add_fee_reservation(fixture, state="held", current="0.01")
    elif mutation == "negative":
        add_fee_reservation(fixture, current="-0.01")
    elif mutation == "excess":
        add_fee_reservation(fixture, current="0.09")
    else:
        add_fee_reservation(fixture, plan="not-a-protected-plan")
    assert fixture.resolver.blocked()
    assert fixture.db.execute("SELECT COUNT(*) FROM native_incident_resolutions").fetchone()[0] == 1


def test_chronological_fill_restatement_retains_exact_journal_groups_and_owner_pending_review(fixture):
    db, clock, execution, venue = fixture.db, fixture.clock, fixture.execution, fixture.venue
    previous_event = json.loads(db.execute("SELECT payload_json FROM ledger_events WHERE kind='fill'").fetchone()[0])
    previous_fill = previous_event["fill"]
    native_at = datetime.fromisoformat(previous_fill["filled_at_utc"].replace("Z", "+00:00")) - timedelta(seconds=0.5)
    # A real later recording time; the source read below must strictly postdate it.
    target = datetime.now(UTC) - timedelta(seconds=1)
    clock.advance((target - clock.now()).total_seconds())
    old_events = [dict(row) for row in db.execute("SELECT * FROM ledger_events ORDER BY sequence")]
    old_postings = [dict(row) for row in db.execute("SELECT * FROM journal_postings ORDER BY rowid")]
    old_groups = fixture.ledger.books(fixture.runtime.portfolio_id).groups
    payload = json.loads(db.execute("SELECT payload_json FROM order_intents").fetchone()[0])
    payload.update(
        intent_id="synthetic-late",
        client_order_id="client-2",
        limit_price="50.1",
        reserve_amount="5.04",
        venue_order_id="order-2",
    )
    db.execute(
        "INSERT INTO order_intents VALUES (?,?,?,?,?,?,?,?)",
        (
            "synthetic-late",
            fixture.runtime.portfolio_id,
            "client-2",
            "UNKNOWN",
            "BTC/USD",
            json.dumps(payload),
            utc_iso(clock.now()),
            utc_iso(clock.now()),
        ),
    )
    db.execute(
        "INSERT INTO position_reservations VALUES (?,?,?,?,?,?,?)",
        (
            "synthetic-late-hold",
            fixture.runtime.portfolio_id,
            "synthetic-late",
            "USD",
            "5.04",
            "held",
            utc_iso(clock.now()),
        ),
    )
    trade = _trade(2, time=str(native_at.timestamp()), fee="0.04")
    trade.update(price="50", cost="5")
    venue.rest.results["TradesHistory"]["trades"]["trade-2"] = trade
    venue.rest.results["TradesHistory"]["count"] = 2
    legs = _ledgers(2, fee="0.04")
    legs["quote-2"]["amount"] = "-5"
    venue.rest.results["QueryLedgers"].update(legs)
    execution.broker.intent_resolver = DurableBrokerIdentity(
        db, venue="kraken", account_id=fixture.resolver.scope.account_id, mode="live"
    )
    asyncio.run(execution.reconcile())
    events = [dict(row) for row in db.execute("SELECT * FROM ledger_events ORDER BY sequence")]
    assert events[: len(old_events)] == old_events
    assert [row["kind"] for row in events[-2:]] == ["fill", "fill_chronological_replay"]
    assert [dict(row) for row in db.execute("SELECT * FROM journal_postings ORDER BY rowid")][
        : len(old_postings)
    ] == old_postings
    assert fixture.ledger.books(fixture.runtime.portfolio_id).groups[: len(old_groups)] == old_groups
    venue.rest.results["BalanceEx"] = {
        "ZUSD": {"balance": "84.88", "hold_trade": "0"},
        "XXBT": {"balance": "0.2", "hold_trade": "0"},
    }
    source = asyncio.run(venue.collect())
    clock.advance((datetime.now(UTC) - clock.now()).total_seconds() + 0.1)
    fixture.resolver.source = source
    verification = fixture.resolver.verify_current_projection()
    assert verification.local_projection_consistent
    assert verification.native_transport_authenticated is False
    api, token, _, _ = client(fixture)
    result = api.post(
        "/api/v1/owner/resolve-native-incident",
        headers={"Authorization": f"Bearer {token}"},
        json=body(fixture).model_dump(mode="json"),
    )
    assert result.status_code == 200, result.text
    assert result.json()["clearance"] == "PENDING_NATIVE_PROOF"
    assert result.json()["incident_cleared"] is False
    assert fixture.resolver.blocked()
    assert fixture.ledger.books(fixture.runtime.portfolio_id).cash_amount("USD") == Decimal("84.88")
