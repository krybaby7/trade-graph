"""Owner identity declarations use exact private pins and never grant live use."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from trade_graph.domain.errors import AuthorityDenied
from trade_graph.live_gate import LivePilotScope, _canonical
from trade_graph.live_mapping import PaperLiveMapping, PinnedPaperLiveMapping


@pytest.fixture
def fixture(tmp_path):
    private = tmp_path / "owner-pins"
    private.mkdir(mode=0o700)
    now = datetime(2026, 10, 4, tzinfo=UTC)
    scope = LivePilotScope(
        deployment_id="synthetic-deployment",
        portfolio_id="synthetic-live",
        account_id="synthetic-account",
        venue="kraken",
        symbol="BTC/USD",
        policy_revision="synthetic-owner-policy",
        policy_sha256="a" * 64,
        deployment_artifact_sha256="b" * 64,
        system_version_sha256="c" * 64,
    )
    document = {
        "schema_version": 1,
        "action": "map_paper_evidence_to_live_scope",
        "mapping_id": "synthetic-mapping",
        "live_scope": scope.model_dump(mode="json"),
        "paper_deployment_id": scope.deployment_id,
        "trial_id": "synthetic-trial",
        "protocol_sha256": "d" * 64,
        "database_identity": [1, 2],
        "producer_binding_sha256": "e" * 64,
        "producer_controller_sha256": "f" * 64,
        "market_stream_id": "synthetic-untouched-stream",
        "symbols": ["BTC/USD"],
        "data_policy_sha256": "1" * 64,
        "friction_policy_sha256": "2" * 64,
        "regime_classifier_sha256": "3" * 64,
        "arms": [
            {
                "arm": arm,
                "portfolio_id": "synthetic-paper-" + arm,
                "version_id": "version-" + arm,
                "artifact_sha256": "c" * 64 if arm == "agent" else "4" * 64,
            }
            for arm in ("agent", "cash", "buy_and_hold", "deterministic")
        ],
        "not_before": (now - timedelta(hours=1)).isoformat(),
        "expires_at": (now + timedelta(days=1)).isoformat(),
    }
    key = b"synthetic owner mapping authority" * 2
    payload = PaperLiveMapping.model_validate(document).model_dump(mode="json")
    signature = hmac.new(
        key, b"trade-graph.paper-live-owner-mapping.v1\0" + _canonical(payload), hashlib.sha256
    ).hexdigest()
    raw = _canonical({"payload": payload, "signature": signature})
    path = private / "mapping.json"
    path.write_bytes(raw)
    path.chmod(0o400)
    source = PinnedPaperLiveMapping(path, hashlib.sha256(raw).hexdigest(), key)
    return SimpleNamespace(now=now, scope=scope, document=document, path=path, source=source, key=key)


def test_actual_private_owner_identity_declaration_is_exact_and_has_no_authority_field(fixture):
    mapping = fixture.source.load(scope=fixture.scope, now=fixture.now)
    assert mapping.live_scope == fixture.scope
    assert mapping.arms[0].portfolio_id != fixture.scope.portfolio_id
    assert mapping.database_identity == (1, 2)
    assert "live_enabled" not in mapping.model_dump()
    assert "actual_external_provenance_verified" not in mapping.model_dump()


@pytest.mark.parametrize(
    "field,value",
    [
        ("account_id", "other-account"),
        ("venue", "other-venue"),
        ("symbol", "ETH/USD"),
        ("policy_revision", "other-policy"),
        ("policy_sha256", "5" * 64),
        ("deployment_artifact_sha256", "5" * 64),
        ("system_version_sha256", "5" * 64),
        ("deployment_id", "other-host"),
        ("portfolio_id", "other-live"),
    ],
)
def test_same_market_alias_or_favorable_trial_never_infers_live_identity(fixture, field, value):
    scope = fixture.scope.model_copy(update={field: value})
    with pytest.raises(AuthorityDenied, match="scope"):
        fixture.source.load(scope=scope, now=fixture.now)


@pytest.mark.parametrize(
    "mutation", ["same_portfolio", "duplicate_arm", "wrong_version", "missing_symbol", "wide_window"]
)
def test_owner_mapping_contract_requires_four_separate_exact_arms_and_bounded_window(fixture, mutation):
    document = json.loads(json.dumps(fixture.document))
    if mutation == "same_portfolio":
        document["arms"][0]["portfolio_id"] = fixture.scope.portfolio_id
    elif mutation == "duplicate_arm":
        document["arms"][1]["arm"] = "agent"
    elif mutation == "wrong_version":
        document["arms"][0]["artifact_sha256"] = "5" * 64
    elif mutation == "missing_symbol":
        document["symbols"] = ["ETH/USD"]
    else:
        document["expires_at"] = (fixture.now + timedelta(days=32)).isoformat()
    with pytest.raises(ValueError):
        PaperLiveMapping.model_validate(document)


@pytest.mark.parametrize("mutation", ["source", "signature", "key", "expired", "future", "caller_type"])
def test_owner_mapping_rejects_changed_source_signature_key_and_time(fixture, mutation):
    source, scope, now = fixture.source, fixture.scope, fixture.now
    if mutation in {"source", "signature"}:
        fixture.path.chmod(0o600)
        raw = fixture.path.read_bytes()
        if mutation == "source":
            raw += b" "
        else:
            envelope = json.loads(raw)
            envelope["signature"] = "0" * 64
            raw = _canonical(envelope)
            source = PinnedPaperLiveMapping(fixture.path, hashlib.sha256(raw).hexdigest(), fixture.key)
        fixture.path.write_bytes(raw)
        fixture.path.chmod(0o400)
    elif mutation == "key":
        source = PinnedPaperLiveMapping(fixture.path, fixture.source.mapping_sha256, b"other synthetic owner key" * 2)
    elif mutation == "expired":
        now += timedelta(days=1)
    elif mutation == "future":
        now -= timedelta(hours=2)
    else:
        scope = SimpleNamespace(**fixture.scope.model_dump())
    with pytest.raises(AuthorityDenied):
        source.load(scope=scope, now=now)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "public_file", "public_directory"])
def test_private_owner_mapping_fd_checks_reject_unsafe_file_shapes_without_blocking(fixture, kind):
    path = fixture.path
    if kind == "symlink":
        target = path.with_name("real.json")
        path.rename(target)
        path.symlink_to(target)
    elif kind == "hardlink":
        os.link(path, path.with_name("duplicate.json"))
    elif kind == "fifo":
        path.unlink()
        os.mkfifo(path, 0o600)
    elif kind == "public_file":
        path.chmod(0o644)
    else:
        path.parent.chmod(0o755)
    with pytest.raises((OSError, ValueError)):
        fixture.source.load(scope=fixture.scope, now=fixture.now)
