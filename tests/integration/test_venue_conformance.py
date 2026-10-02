"""Synthetic HTTPS clients exercise retained-source proofs; no private requests."""

import asyncio
import base64
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from tests.integration.test_kraken_live_adapter import ScriptedRest

from trade_graph.application.venue_conformance import (
    KrakenReadOnlyConformance,
    PinnedReadOnlyAuthority,
    PinnedVenueObservation,
    ReadOnlyObservationGrant,
    VenueObservationScope,
    _canonical,
    _mac,
)
from trade_graph.domain.errors import AuthorityDenied


class Fixture:
    def __init__(self, tmp_path, **changes):
        self.private = tmp_path / "private"
        self.private.mkdir(mode=0o700)
        self.owner_key = hashlib.sha256(b"synthetic owner authority only").digest()
        self.collector_key = hashlib.sha256(b"synthetic protected collector only").digest()
        self.api_key = "synthetic-venue-key"
        self.rest = ScriptedRest()
        self.rest.results["TradeVolume"] = {
            "fees": {"XXBTZUSD": {"fee": "0.2"}}, "fees_maker": {"XXBTZUSD": {"fee": "0.1"}},
        }
        now = datetime.now(UTC)
        self.scope = VenueObservationScope(
            deployment_id="fixture-deployment", portfolio_id="fixture-portfolio",
            account_id="synthetic-private-account", venue="kraken", mode="live", symbol="BTC/USD",
            policy_revision="fixture-policy", policy_sha256="a" * 64,
            deployment_artifact_sha256="b" * 64, system_version_sha256="c" * 64,
        )
        self.grant = ReadOnlyObservationGrant(
            schema_version=1, authorization_id="synthetic-read-only-grant",
            action="observe_read_only_venue_account", scope=self.scope,
            credential_binding_sha256=hashlib.sha256(self.api_key.encode()).hexdigest(),
            not_before=now - timedelta(seconds=1), expires_at=now + timedelta(minutes=5), **changes,
        )
        payload = self.grant.model_dump(mode="json")
        raw = _canonical({"payload": payload, "signature": _mac(self.owner_key, "owner-grant", payload)})
        path = self.private / "grant.json"
        path.write_bytes(raw)
        path.chmod(0o400)
        self.authority = PinnedReadOnlyAuthority(path, hashlib.sha256(raw).hexdigest(), self.owner_key)

    async def collect(self):
        def respond(request):
            method = request.url.path.rsplit("/", 1)[-1]
            if request.method == "POST":
                values = parse_qs(request.content.decode())
                values.pop("nonce")
                parameters = {key: value[0] for key, value in values.items()}
            else:
                parameters = dict(request.url.params)
            return httpx.Response(200, json=self.rest(method, parameters))

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            collector = KrakenReadOnlyConformance(
                self.authority, collector_key=self.collector_key, api_key=self.api_key,
                api_secret=base64.b64encode(b"synthetic credential only").decode(), client=client,
            )
            self.collector = collector
            return await collector.collect(self.private)


def _verify(capture):
    return capture.verify(now=datetime.now(UTC))


def _replace_report(capture, key, change):
    document = json.loads(capture.path.read_bytes())
    change(document["payload"])
    document["signature"] = _mac(key, "observation", document["payload"])
    raw = _canonical(document)
    capture.path.chmod(0o600)
    capture.path.write_bytes(raw)
    capture.path.chmod(0o400)
    return PinnedVenueObservation(capture.path, hashlib.sha256(raw).hexdigest(), key)


def test_retained_injected_sources_normalize_current_facts_but_never_authenticate_account(tmp_path):
    fixture = Fixture(tmp_path)
    capture = asyncio.run(fixture.collect())
    proof = _verify(capture)
    assert proof.source_current
    assert proof.authenticated_reads == ()
    assert proof.observation.scope == fixture.scope
    assert proof.observation.completed_stages == (
        "instruments", "account_fees", "balances", "open_orders", "order_lookups", "native_history",
    )
    assert "withdrawals_absent_unverified" in proof.pending
    assert "key_permission_inventory_unverified" in proof.pending
    assert "authenticated_private_observation_missing" in proof.pending
    assert "native_stop_protection_unverified" in proof.pending
    assert all(receipt.transport_basis == "injected_transport" for receipt in proof.observation.receipts)
    assert capture.path.stat().st_mode & 0o777 == 0o400
    assert capture.path.parent.stat().st_mode & 0o777 == 0o500
    assert not any(method in {"AddOrder", "CancelOrder", "Withdraw"} for method, _ in fixture.rest.calls)
    summary = json.loads((capture.path.parent / "native-summary.json").read_bytes())
    assert summary["balances"]["account_id"] == fixture.scope.account_id
    assert summary["account_fees"]["BTC/USD"]["taker"] == "0.002"
    assert b"synthetic-venue-key" not in capture.path.read_bytes()
    assert b"synthetic credential only" not in b"".join(p.read_bytes() for p in capture.path.parent.iterdir())


def test_signed_report_label_cannot_elevate_injected_wire_into_authenticated_proof(tmp_path):
    fixture = Fixture(tmp_path)
    capture = asyncio.run(fixture.collect())
    changed = _replace_report(capture, fixture.collector_key,
                              lambda payload: payload.update(transport_basis="owned_https"))
    with pytest.raises(ValueError, match="inconsistent"):
        _verify(changed)


@pytest.mark.parametrize("name", ["wire", "native-summary.json", "grant.json", "observation.json"])
def test_changed_retained_sources_invalidate_the_proof(tmp_path, name):
    fixture = Fixture(tmp_path)
    capture = asyncio.run(fixture.collect())
    path = next(capture.path.parent.glob("wire-*.json")) if name == "wire" else capture.path.parent / name
    path.chmod(0o600)
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="changed|differs"):
        _verify(capture)


def test_missing_read_authority_or_wrong_credential_refuses_before_any_request(tmp_path):
    fixture = Fixture(tmp_path)
    fixture.api_key = "different-synthetic-key"
    with pytest.raises(AuthorityDenied, match="credential"):
        asyncio.run(fixture.collect())
    assert fixture.rest.calls == []
    assert list(fixture.private.iterdir()) == [fixture.authority.path]


def test_changed_or_expired_authority_refuses_without_private_or_public_probe(tmp_path):
    fixture = Fixture(tmp_path)
    fixture.authority.path.chmod(0o600)
    fixture.authority.path.write_bytes(fixture.authority.path.read_bytes() + b" ")
    with pytest.raises(AuthorityDenied, match="pin"):
        asyncio.run(fixture.collect())
    assert fixture.rest.calls == []


@pytest.mark.parametrize("problem", ["expired", "future", "invalid_signature"])
def test_dated_signed_owner_authority_is_required_before_capture(tmp_path, problem):
    fixture = Fixture(tmp_path)
    document = json.loads(fixture.authority.path.read_bytes())
    now = datetime.now(UTC)
    if problem == "expired":
        document["payload"].update(not_before=(now - timedelta(minutes=5)).isoformat(),
                                   expires_at=(now - timedelta(seconds=1)).isoformat())
    elif problem == "future":
        document["payload"].update(not_before=(now + timedelta(seconds=1)).isoformat(),
                                   expires_at=(now + timedelta(minutes=5)).isoformat())
    document["signature"] = (_mac(fixture.owner_key, "owner-grant", document["payload"])
                             if problem != "invalid_signature" else "0" * 64)
    raw = _canonical(document)
    fixture.authority.path.chmod(0o600)
    fixture.authority.path.write_bytes(raw)
    fixture.authority.path.chmod(0o400)
    fixture.authority = PinnedReadOnlyAuthority(fixture.authority.path, hashlib.sha256(raw).hexdigest(),
                                               fixture.owner_key)
    with pytest.raises(AuthorityDenied, match="window|signature"):
        asyncio.run(fixture.collect())
    assert fixture.rest.calls == []


def test_default_network_classification_creates_no_request_and_injection_never_elevates():
    from trade_graph.adapters.brokers.kraken_transport import KrakenRestTransport

    async def run():
        default = KrakenRestTransport()
        assert default.observation_basis == "owned_https"
        await default.aclose()
        async with httpx.AsyncClient() as client:
            injected = KrakenRestTransport(client=client)
            assert injected.observation_basis == "injected_transport"
            await injected.aclose()

    asyncio.run(run())


def test_request_bound_retains_partial_public_facts_and_marks_private_checks_pending(tmp_path):
    fixture = Fixture(tmp_path, maximum_requests=2)
    capture = asyncio.run(fixture.collect())
    proof = _verify(capture)
    assert proof.observation.completed_stages == ("instruments",)
    assert [method for method, _ in fixture.rest.calls] == ["Assets", "AssetPairs"]
    assert "account_fees_AuthorityDenied" in proof.pending
    assert proof.observation.checks["account_fees"] == "pending"
    assert proof.authenticated_reads == ()


def test_signed_check_flags_cannot_complete_a_stage_without_native_sources(tmp_path):
    fixture = Fixture(tmp_path, maximum_requests=2)
    capture = asyncio.run(fixture.collect())
    changed = _replace_report(capture, fixture.collector_key,
                              lambda payload: payload["checks"].update(balances="observed"))
    with pytest.raises(ValueError, match="checks do not match"):
        _verify(changed)


def test_explicitly_limited_history_stays_pending_even_if_a_report_omits_the_label(tmp_path):
    fixture = Fixture(tmp_path, history_start_utc=datetime.now(UTC) - timedelta(days=1))
    capture = asyncio.run(fixture.collect())
    changed = _replace_report(capture, fixture.collector_key,
                              lambda payload: payload.update(pending=[]))
    proof = _verify(changed)
    assert "historical_account_scope_limited" in proof.pending
    assert "withdrawals_absent_unverified" in proof.pending


def test_current_source_change_invalidates_source_verification_without_network(tmp_path, monkeypatch):
    import trade_graph.application.venue_conformance as module

    fixture = Fixture(tmp_path)
    capture = asyncio.run(fixture.collect())
    before = list(fixture.rest.calls)
    monkeypatch.setattr(module, "_source_digests", lambda: ("0" * 64, "0" * 64, "0" * 64))
    proof = _verify(capture)
    assert not proof.source_current
    assert "collector_or_adapter_source_changed" in proof.pending
    assert fixture.rest.calls == before


def test_native_margin_refusal_preserves_wire_but_cannot_claim_normalized_balances(tmp_path):
    fixture = Fixture(tmp_path)
    fixture.rest.results["BalanceEx"]["ZUSD"]["credit_used"] = "1"
    capture = asyncio.run(fixture.collect())
    proof = _verify(capture)
    assert proof.observation.completed_stages == ("instruments", "account_fees")
    assert "balances_ValidationFailure" in proof.pending
    assert any(receipt.method == "BalanceEx" for receipt in proof.observation.receipts)
    assert proof.observation.checks["balances"] == "pending"


def test_expired_future_and_naive_verification_times_fail_closed(tmp_path):
    fixture = Fixture(tmp_path)
    capture = asyncio.run(fixture.collect())
    with pytest.raises(ValueError, match="stale"):
        capture.verify(now=datetime.now(UTC) + timedelta(seconds=61))
    with pytest.raises(ValueError, match="window"):
        capture.verify(now=datetime.now(UTC) - timedelta(minutes=5))
    with pytest.raises(ValueError, match="aware"):
        capture.verify(now=datetime.now())


@pytest.mark.parametrize("problem", ["symlink", "hardlink", "public_directory", "public_file"])
def test_source_files_must_remain_private_single_link_regular_objects(tmp_path, problem):
    fixture = Fixture(tmp_path)
    capture = asyncio.run(fixture.collect())
    path = capture.path.parent / "native-summary.json"
    if problem == "symlink":
        capture.path.parent.chmod(0o700)
        original = capture.path.parent / "native-original.json"
        path.rename(original)
        path.symlink_to(original)
    elif problem == "hardlink":
        os.link(path, tmp_path / "linked-summary.json")
    elif problem == "public_directory":
        capture.path.parent.chmod(0o755)
    else:
        path.chmod(0o644)
    with pytest.raises((PermissionError, OSError)):
        _verify(capture)


def test_verify_does_not_start_nested_event_loop_or_network(tmp_path):
    fixture = Fixture(tmp_path)
    capture = asyncio.run(fixture.collect())

    async def inside_loop():
        with pytest.raises(ValueError, match="active async"):
            _verify(capture)

    asyncio.run(inside_loop())


def test_collector_is_single_use_and_receipts_keep_account_scopes_distinct(tmp_path):
    fixture = Fixture(tmp_path)
    capture = asyncio.run(fixture.collect())
    with pytest.raises(ValueError, match="single use"):
        asyncio.run(fixture.collector.collect(fixture.private))
    changed = _replace_report(capture, fixture.collector_key,
                              lambda payload: payload["scope"].update(account_id="foreign-account"))
    with pytest.raises(ValueError, match="authority"):
        _verify(changed)
