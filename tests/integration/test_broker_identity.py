"""Durable ownership is scoped and both external identities must agree."""

import json
from contextlib import closing

import pytest
from tests.integration.test_kraken_live_adapter import NOW, _intent

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.broker_identity import DurableBrokerIdentity
from trade_graph.application.ledger import Ledger
from trade_graph.domain.clock import FrozenClock, utc_iso
from trade_graph.domain.errors import ValidationFailure


def _store(database, *, name="intent-1", client="client-1", venue_order="order-1", **changes):
    ledger = Ledger(database, FrozenClock(NOW))
    portfolio = ledger.create_portfolio(reporting_currency="USD", mode=changes.get("mode", "live"))
    intent = _intent(intent_id=name, client_order_id=client, portfolio_id=portfolio, **changes)
    payload = intent.model_dump(mode="json")
    if venue_order is not None:
        payload["venue_order_id"] = venue_order
    now = utc_iso(NOW)
    database.execute(
        "INSERT INTO order_intents VALUES (?, ?, ?, 'FILLED', ?, ?, ?, ?)",
        (name, portfolio, client, intent.symbol, json.dumps(payload), now, now),
    )


def _resolver(database):
    return DurableBrokerIdentity(database, venue="kraken", account_id="synthetic-account", mode="live")


def test_cold_terminal_ownership_requires_persisted_venue_id_or_verified_client(tmp_path):
    with closing(Database(tmp_path / "identity.sqlite")) as database:
        _store(database)
        resolver = _resolver(database)
        assert resolver(None, "order-1") == "intent-1"
        assert resolver("client-1", "order-1") == "intent-1"
        assert resolver(None, "foreign-order") is None
        assert resolver("foreign-client", "foreign-order") is None
        _store(database, name="unbound", client="client-2", venue_order=None)
        assert resolver(None, "unbound-order") is None
        assert resolver("client-2", "unbound-order") == "unbound"


@pytest.mark.parametrize("client,venue_order", [("foreign-client", "order-1"), ("client-1", "foreign-order")])
def test_one_matching_identity_cannot_hide_a_conflicting_identity(tmp_path, client, venue_order):
    with closing(Database(tmp_path / "identity.sqlite")) as database:
        _store(database)
        with pytest.raises(ValidationFailure, match="identities conflict"):
            _resolver(database)(client, venue_order)


def test_client_and_venue_matches_for_different_intents_are_never_chosen_by_precedence(tmp_path):
    with closing(Database(tmp_path / "identity.sqlite")) as database:
        _store(database)
        _store(database, name="other", client="client-2", venue_order="order-2")
        with pytest.raises(ValidationFailure, match="identities conflict"):
            _resolver(database)("client-2", "order-1")


@pytest.mark.parametrize("changes", [{"venue": "other"}, {"account_id": "other"}, {"mode": "paper"}])
def test_foreign_execution_scope_never_establishes_ownership(tmp_path, changes):
    with closing(Database(tmp_path / "identity.sqlite")) as database:
        _store(database, **changes)
        assert _resolver(database)("client-1", "order-1") is None


def test_uuid_client_aliases_resolve_but_colliding_aliases_are_ambiguous(tmp_path):
    client = "12345678-1234-1234-1234-123456789abc"
    with closing(Database(tmp_path / "identity.sqlite")) as database:
        _store(database, client=client)
        assert _resolver(database)(client.replace("-", "").upper(), "order-1") == "intent-1"
        _store(database, name="collision", client=client.replace("-", ""), venue_order="order-2")
        with pytest.raises(ValidationFailure, match="ambiguous"):
            _resolver(database)(client, "order-1")
        with pytest.raises(ValidationFailure, match="ambiguous"):
            _resolver(database)(None, "order-1")


def test_duplicate_durable_venue_order_identity_is_ambiguous_even_without_client(tmp_path):
    with closing(Database(tmp_path / "identity.sqlite")) as database:
        _store(database)
        _store(database, name="collision", client="client-2")
        with pytest.raises(ValidationFailure, match="ambiguous"):
            _resolver(database)(None, "order-1")


@pytest.mark.parametrize(
    "field,value",
    [("intent_id", "foreign"), ("portfolio_id", "foreign"), ("client_order_id", "foreign"), ("quantity", "NaN")],
)
def test_immutable_intent_and_index_corruption_fail_closed(tmp_path, field, value):
    with closing(Database(tmp_path / "identity.sqlite")) as database:
        _store(database)
        row = database.execute("SELECT payload_json FROM order_intents").fetchone()
        payload = json.loads(row["payload_json"])
        payload[field] = value
        database.execute("UPDATE order_intents SET payload_json = ?", (json.dumps(payload),))
        with pytest.raises(ValidationFailure, match="ownership record"):
            _resolver(database)("client-1", "order-1")


def test_resolver_never_mutates_journal_or_grants_missing_identity(tmp_path):
    with closing(Database(tmp_path / "identity.sqlite")) as database:
        _store(database)
        resolver = _resolver(database)
        before = database.connection.total_changes
        assert resolver(None, "order-1") == "intent-1"
        assert resolver(None, "foreign") is None
        with pytest.raises(ValidationFailure):
            resolver("client-1", "order-1,foreign")
        assert database.connection.total_changes == before
