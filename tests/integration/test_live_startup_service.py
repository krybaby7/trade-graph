"""Synthetic live-capable controller tests; no Kraken/network/authentication.

Test monkeypatches represent an independently commissioned installation solely
to exercise source/control contracts. They are never production admission proof.
"""

import asyncio
import hashlib
import hmac
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from tests.integration.test_execution_reconciliation_ordering import _intent
from tests.integration.test_live_pilot import fixture as fixture
from tests.integration.test_live_pilot_dispatch import ObservedBroker, execution

from trade_graph.application import protected_live
from trade_graph.application.live_service import LiveService
from trade_graph.application.paper_service import PaperService
from trade_graph.application.protected_live import ProtectedLiveRuntime
from trade_graph.application.scheduler import Scheduler
from trade_graph.application.secretary import Secretary
from trade_graph.contracts.models import BalanceSnapshot, InstrumentRules
from trade_graph.domain.errors import AuthorityDenied, StaleState
from trade_graph.kernel import live_commission
from trade_graph.kernel.live_commission import (
    CommissionReview,
    LiveCommissionProfile,
    PinnedLiveCommission,
    SignedCommissionReview,
)
from trade_graph.kernel.runtime_manifest import ProtectedRuntimeManifest, canonical_json, protected_package_sha256
from trade_graph.live_evidence import LiveUpstreamSources
from trade_graph.live_runtime import (
    LiveRuntimeConfig,
    assemble_live_dashboard_runtime,
    assemble_live_runtime,
    live_configuration_digest,
    live_startup_prerequisites,
)


def test_disabled_live_default_has_no_credentials_transport_or_database_mutation(tmp_path, monkeypatch):
    attempted = []
    monkeypatch.setattr(live_commission, "read_owner_file", lambda *a: attempted.append(a))
    missing = tmp_path / "never-created.sqlite"
    with pytest.raises(AuthorityDenied, match="protected_owner_commission_required"):
        assemble_live_runtime(missing)
    with pytest.raises(AuthorityDenied, match="owner_live_enablement_missing"):
        assemble_live_runtime(missing, config=LiveRuntimeConfig(), protected_owner=tmp_path)
    assert attempted == [] and not missing.exists()
    assert live_startup_prerequisites(missing)["ready"] is False


def test_ordinary_interpreter_or_temp_directory_cannot_claim_actual_os_admission(tmp_path):
    with pytest.raises(AuthorityDenied, match="protected read-only owner mount"):
        live_commission.assert_live_process_boundary(tmp_path, "a" * 64)


def test_commission_configuration_can_be_pinned_without_circular_hashes(fixture):
    config = LiveRuntimeConfig(live_enabled=True, scope=fixture.scope, readiness_path=fixture.source_pin.path,
                               readiness_bundle_sha256=fixture.source_pin.bundle_sha256)
    profile = LiveCommissionProfile(
        schema_version=1, scope_sha256=hashlib.sha256(canonical_json(fixture.scope.model_dump(mode="json"))
                                                    .encode()).hexdigest(),
        authorization_id=fixture.authorization_id, readiness_bundle_sha256=fixture.source_pin.bundle_sha256,
        runtime_manifest_sha256="a" * 64, protected_package_sha256=protected_package_sha256(),
        venue_observation_path=fixture.private / "venue.json", venue_observation_sha256="b" * 64,
        proxy_ipv4="172.22.0.2", proxy_port=3128,
        reviews=[{"filename": f"commission-{issuer}.json", "sha256": "c" * 64}
                 for issuer in ("venue", "operations", "economics")],
        kraken_key_sha256="d" * 64, kraken_secret_sha256="e" * 64,
        live_config_sha256=live_configuration_digest(config),
    )
    profile_bytes = profile.model_dump_json().encode()
    final = config.model_copy(update={"commission_sha256": hashlib.sha256(profile_bytes).hexdigest()})
    assert live_configuration_digest(final) == profile.live_config_sha256
    altered = final.model_copy(update={"tick_interval_seconds": 2})
    assert live_configuration_digest(altered) != profile.live_config_sha256


def test_signed_synthetic_reviews_never_satisfy_production_commission(fixture, monkeypatch):
    package = protected_package_sha256()
    scope_hash = hashlib.sha256(canonical_json(fixture.scope.model_dump(mode="json")).encode()).hexdigest()
    profile = LiveCommissionProfile(
        schema_version=1, scope_sha256=scope_hash, authorization_id=fixture.authorization_id,
        readiness_bundle_sha256=fixture.source_pin.bundle_sha256, runtime_manifest_sha256="a" * 64,
        protected_package_sha256=package, venue_observation_path=fixture.private / "venue.json",
        venue_observation_sha256="b" * 64, proxy_ipv4="172.22.0.2", proxy_port=3128,
        reviews=[{"filename": f"commission-{issuer}.json", "sha256": "c" * 64}
                 for issuer in ("venue", "operations", "economics")],
        kraken_key_sha256="d" * 64, kraken_secret_sha256="e" * 64, live_config_sha256="f" * 64,
    )
    blobs = {}
    pins = []
    for issuer in ("venue", "operations", "economics"):
        key = hashlib.sha256(("SYNTHETIC COMMISSION " + issuer).encode()).digest()
        review = CommissionReview(
            issuer=issuer, basis="synthetic", scope_sha256=scope_hash, authorization_id=fixture.authorization_id,
            readiness_bundle_sha256=profile.readiness_bundle_sha256,
            runtime_manifest_sha256=profile.runtime_manifest_sha256, protected_package_sha256=package,
            verified_at=fixture.clock.now(), expires_at=fixture.clock.now() + timedelta(days=1),
            assertions=tuple(live_commission._ASSERTIONS[issuer]),
            sources=[{"filename": "reviewed-source.json", "sha256": "1" * 64}], verdict="passed",
        )
        signature = hmac.new(key, live_commission._DOMAIN + canonical_json(review.model_dump(mode="json")).encode(),
                             hashlib.sha256).hexdigest()
        raw = SignedCommissionReview(payload=review, signature=signature).model_dump_json().encode()
        name = f"commission-{issuer}.json"
        blobs[name], blobs[f"commission-{issuer}.key"] = raw, key
        pins.append({"filename": name, "sha256": hashlib.sha256(raw).hexdigest()})
    profile = profile.model_copy(update={"reviews": tuple(live_commission.CommissionSourcePin(**pin) for pin in pins)})
    blobs["live-commission.json"] = profile.model_dump_json().encode()
    commission = PinnedLiveCommission(fixture.private, hashlib.sha256(blobs["live-commission.json"]).hexdigest())
    monkeypatch.setattr(live_commission, "read_owner_file", lambda _directory, name, _bound: blobs[name])
    monkeypatch.setattr(live_commission, "assert_live_process_boundary", lambda *_args: None)
    with pytest.raises(AuthorityDenied, match="independent actual"):
        commission.verify(fixture.scope, fixture.source_pin.load(), fixture.clock)
    assert "kraken.key" not in blobs


class ScriptedAccountBroker(ObservedBroker):
    async def instruments(self):
        return [InstrumentRules(venue=self.venue, symbol="BTC/USD", base_asset="BTC", quote_asset="USD",
                                price_increment="0.01", quantity_increment="0.01", min_quantity="0.01",
                                min_notional="1", synthetic=True)]

    async def balances(self):
        return BalanceSnapshot(venue=self.venue, account_id=self.account, as_of_utc=self.clock.now(), amounts={})

    async def open_orders(self):
        return []


def scripted_service(fixture):
    service, _ = execution(fixture)
    broker = ScriptedAccountBroker(fixture.scope)
    broker.clock = fixture.clock
    service.broker = broker
    scheduler = Scheduler(fixture.db, fixture.clock)
    commission = PinnedLiveCommission(fixture.private, "a" * 64)
    fixture.lifecycle.upstream = LiveUpstreamSources(commission=commission)
    runtime = SimpleNamespace(
        database=fixture.db, execution=service, clock=fixture.clock, portfolio_id=fixture.pid,
        lifecycle=fixture.lifecycle, authorization_id=fixture.authorization_id, commission=commission,
        artifact_runtime=None, secretary=Secretary(service, scheduler), public_feed=None,
        config=LiveRuntimeConfig(tick_interval_seconds=0.01), runtime_ready=lambda: False,
        prepare_runtime=lambda: {}, ledger=service.ledger,
    )
    fixture.db.execute("UPDATE live_pilot_grants SET state='ACTIVE',generation=1")
    worker = LiveService(runtime)
    return runtime, broker, worker


def test_live_controller_owns_same_db_fence_as_paper_and_repeated_start_attaches(fixture):
    runtime, broker, service = scripted_service(fixture)
    competing = PaperService(fixture.db, runtime.execution, service_mode="live", schedule_intervals={})

    async def scenario():
        await service.start()
        await service.start()
        with pytest.raises(StaleState, match="owns this database"):
            await competing.start()
        assert fixture.db.execute("SELECT count(*) FROM process_leases").fetchone()[0] == 2
        assert service.status()["account_ready"]
        await service.stop()

    asyncio.run(scenario())
    assert fixture.db.execute("SELECT count(*) FROM process_leases").fetchone()[0] == 0
    assert broker.calls and not any(kind == "submit" for kind, _ in broker.calls)


def test_live_restart_unknown_outcome_keeps_hold_and_never_submits_replacement(fixture):
    runtime, broker, service = scripted_service(fixture)
    intent = _intent(fixture.db, fixture.clock, fixture.pid, "synthetic-lost-ack", status="UNKNOWN",
                     account=fixture.scope.account_id, venue=fixture.scope.venue, mode="live")
    asyncio.run(service.run(max_ticks=2, install_signal_handlers=False))
    assert runtime.execution.intent_state(intent.intent_id) == "UNKNOWN"
    held = fixture.db.execute("SELECT state FROM position_reservations WHERE intent_id=?",
                              (intent.intent_id,)).fetchone()
    assert held[0] == "held"
    assert fixture.db.execute("SELECT count(*) FROM order_attempts").fetchone()[0] == 0
    assert not any(kind == "submit" for kind, _ in broker.calls)
    assert fixture.db.execute("SELECT state FROM live_pilot_grants").fetchone()[0] == "RECOVERY_REQUIRED"
    assert fixture.db.execute("SELECT profile FROM pause_states").fetchone()[0] == "MANAGE_ONLY"


def test_live_dashboard_scope_has_no_private_transport_or_worker(fixture, monkeypatch):
    from trade_graph import live_runtime

    config = LiveRuntimeConfig(scope=fixture.scope)
    monkeypatch.setattr(live_runtime, "load_live_runtime_config", lambda _path: config)
    runtime = assemble_live_dashboard_runtime(fixture.db.path, config=config, protected_owner=fixture.private)
    assert runtime.execution.mode == "live"
    assert runtime.execution.account_id == fixture.scope.account_id
    assert not hasattr(runtime, "private_transport")
    with pytest.raises(live_runtime.LiveDisabled, match="exclusive live service"):
        asyncio.run(runtime.execution.broker.balances())
    runtime.database.close()


def test_live_broker_failure_is_visible_and_blocks_graph_without_losing_maintenance(fixture):
    runtime, broker, service = scripted_service(fixture)

    async def failed_balances():
        raise TimeoutError("private raw details must not be logged")

    broker.balances = failed_balances
    result = asyncio.run(service.run(max_ticks=1, install_signal_handlers=False))
    assert "account:TimeoutError" in result["failures"]
    assert not service.status()["account_ready"]
    rows = fixture.db.execute("SELECT payload_json FROM activity_events WHERE kind='live_account_health'").fetchall()
    assert rows and "private raw details" not in json.dumps([row[0] for row in rows])
    assert ("fills", None) in broker.calls


def test_expired_live_authorization_restart_keeps_existing_management(fixture):
    runtime, broker, service = scripted_service(fixture)
    unknown = _intent(fixture.db, fixture.clock, fixture.pid, "synthetic-expired-unknown", status="UNKNOWN",
                      account=fixture.scope.account_id, venue=fixture.scope.venue, mode="live")
    fixture.clock.advance(86401)
    result = asyncio.run(service.run(max_ticks=1, install_signal_handlers=False))
    assert result["completed"] == 0
    assert runtime.execution.intent_state(unknown.intent_id) == "UNKNOWN"
    assert ("status", unknown.client_order_id) in broker.calls and ("fills", None) in broker.calls
    assert not any(kind == "submit" for kind, _ in broker.calls)
    assert fixture.db.execute("SELECT state FROM live_pilot_grants").fetchone()[0] == "RECOVERY_REQUIRED"


def test_live_mutable_graph_uses_real_confined_rpc_and_cannot_read_private_file(fixture, monkeypatch):
    runtime, _broker, _service = scripted_service(fixture)
    source = "def decide(context):\n    open('/etc/passwd').read()\n    return {}\n"
    manifest = ProtectedRuntimeManifest(
        schema_version=1, protected_package_sha256=protected_package_sha256(),
        deployment_id=fixture.scope.deployment_id,
        approved_source_sha256=(hashlib.sha256(source.encode()).hexdigest(),),
    )
    # Synthetic independent-admission stand-in, not actual host/venue evidence.
    monkeypatch.setattr(PinnedLiveCommission, "verify", lambda *a, **k: {"status": "synthetic-test-only"})
    monkeypatch.setattr(protected_live, "evaluate_live_readiness", lambda *a, **k: {"ready": True})
    confined = ProtectedLiveRuntime(database=fixture.db, clock=fixture.clock, execution=runtime.execution,
        manifest=manifest, capability_key=b"SYNTHETIC PRIVATE KEY ONLY 32 BYTES", instance_id="synthetic-live-child",
        commission=runtime.commission)
    confined.controller.admit_release(release_id="synthetic-live-graph", source_text=source)
    confined.controller.activate_release("synthetic-live-graph")
    # Supply only the allowlisted quote snapshot; the actual private DB, policy,
    # keys and execution services never enter the child.
    from tests.integration.test_execution import _quote

    rules = asyncio.run(runtime.execution.broker.instruments())[0]
    runtime.execution.register_instrument(rules)
    runtime.execution.save_observation(_quote(fixture.clock, "1", "1.01").model_copy(update={"venue": "kraken"}))
    result = confined.controller.run_active(fixture.pid, "BTC/USD")
    assert result["status"] == "MUTABLE_REJECTED"
    assert result["recovery"] == "MANAGE_ONLY"
    assert result["process"]["exit_code"] != 0
    assert fixture.db.execute("SELECT count(*) FROM order_attempts").fetchone()[0] == 0


@pytest.mark.parametrize("factory", [assemble_live_runtime, assemble_live_dashboard_runtime])
@pytest.mark.parametrize("extra_mode", ["live", "paper"])
def test_live_scope_refuses_extra_portfolio_before_exchange_key_or_transport(fixture, monkeypatch, factory, extra_mode):
    from trade_graph import live_runtime
    from trade_graph.application.ledger import Ledger

    Ledger(fixture.db, fixture.clock).create_portfolio(reporting_currency="EUR", mode=extra_mode)
    config = LiveRuntimeConfig(live_enabled=True, scope=fixture.scope,
        readiness_path=fixture.source_pin.path, readiness_bundle_sha256=fixture.source_pin.bundle_sha256,
        commission_sha256="a" * 64)
    monkeypatch.setattr(live_runtime, "load_live_runtime_config", lambda _path: config)
    # Exact scope refusal must precede commission keys and transport creation.
    monkeypatch.setattr(live_runtime.PinnedLiveCommission, "load", lambda _self: pytest.fail("scope read keys"))
    monkeypatch.setattr(live_runtime, "read_owner_file", lambda *_args: pytest.fail("scope read keys"))
    monkeypatch.setattr(live_runtime, "KrakenRestTransport", lambda **_kwargs: pytest.fail("scope made transport"))
    with pytest.raises(AuthorityDenied, match="exact.*live.*portfolio|dedicated live financial journal"):
        factory(fixture.db.path, config=config, protected_owner=fixture.private)


def test_static_live_prerequisites_refuse_changed_config_and_extra_portfolio(fixture, monkeypatch):
    from trade_graph import live_runtime

    config = LiveRuntimeConfig(live_enabled=True, scope=fixture.scope,
        readiness_path=fixture.source_pin.path, readiness_bundle_sha256=fixture.source_pin.bundle_sha256,
        commission_sha256="a" * 64)
    profile = SimpleNamespace(runtime_manifest_sha256="a" * 64, live_config_sha256=live_configuration_digest(config))
    monkeypatch.setattr(live_runtime, "load_live_runtime_config", lambda _path: config)
    monkeypatch.setattr(live_runtime.PinnedLiveCommission, "load", lambda _self: profile)
    monkeypatch.setattr(live_commission, "assert_live_process_boundary", lambda *_args: None)
    status = live_startup_prerequisites(fixture.db.path, protected_owner=fixture.private)
    assert status["ready"] is True and status["status"] == "requires_final_service_admission"
    profile.live_config_sha256 = "b" * 64
    assert live_startup_prerequisites(fixture.db.path, protected_owner=fixture.private)["ready"] is False
    profile.live_config_sha256 = live_configuration_digest(config)
    from trade_graph.application.ledger import Ledger

    Ledger(fixture.db, fixture.clock).create_portfolio(reporting_currency="EUR", mode="live")
    assert live_startup_prerequisites(fixture.db.path, protected_owner=fixture.private)["ready"] is False


def test_static_live_prerequisites_refuse_nonfinancial_schema_without_mutation(fixture, monkeypatch):
    import sqlite3

    from trade_graph import live_runtime

    config = LiveRuntimeConfig(live_enabled=True, scope=fixture.scope,
        readiness_path=fixture.source_pin.path, readiness_bundle_sha256=fixture.source_pin.bundle_sha256,
        commission_sha256="a" * 64)
    profile = SimpleNamespace(runtime_manifest_sha256="a" * 64, live_config_sha256=live_configuration_digest(config))
    monkeypatch.setattr(live_runtime, "load_live_runtime_config", lambda _path: config)
    monkeypatch.setattr(live_runtime.PinnedLiveCommission, "load", lambda _self: profile)
    monkeypatch.setattr(live_commission, "assert_live_process_boundary", lambda *_args: None)
    path = fixture.private / "not-financial.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE unrelated (value TEXT)")
    connection.close()
    before = path.read_bytes()
    assert live_startup_prerequisites(path, protected_owner=fixture.private)["ready"] is False
    assert path.read_bytes() == before
    with pytest.raises(AuthorityDenied, match="existing_live_schema_required"):
        assemble_live_runtime(path, config=config, protected_owner=fixture.private)


@pytest.mark.parametrize("admitted", [False, True])
def test_expired_commission_factory_requires_prior_admission_and_only_manages_existing_intents(
    fixture, monkeypatch, admitted,
):
    from trade_graph import live_runtime

    unknown = _intent(fixture.db, fixture.clock, fixture.pid, "synthetic-expired-factory-unknown", status="UNKNOWN",
                      account=fixture.scope.account_id, venue=fixture.scope.venue, mode="live")
    fixture.db.execute("UPDATE live_pilot_grants SET state='ACTIVE',generation=1")
    fixture.clock.advance(86401)
    config = LiveRuntimeConfig(live_enabled=True, scope=fixture.scope, readiness_path=fixture.source_pin.path,
        readiness_bundle_sha256=fixture.source_pin.bundle_sha256, commission_sha256="a" * 64,
        tick_interval_seconds=0.01)
    verified, credential_reads = [], []
    monkeypatch.setattr(PinnedLiveCommission, "load", lambda _self: SimpleNamespace(
        live_config_sha256=live_configuration_digest(config), proxy_url="http://172.29.17.2:8443"))
    monkeypatch.setattr(live_runtime, "read_owner_file", lambda _directory, name, _maximum:
        config.model_dump_json().encode() if name == "live-config.json"
        else fixture.keys[name.removeprefix("readiness-").removesuffix(".key")])
    monkeypatch.setattr(live_runtime, "evaluate_live_readiness", lambda *_a, **_k: {"ready": False})
    # Synthetic actual-admission stand-ins test only the restart branch. They
    # must never be accepted as independent production commissioning evidence.
    monkeypatch.setattr(PinnedLiveCommission, "admitted", lambda *_a: admitted)
    monkeypatch.setattr(PinnedLiveCommission, "verify", lambda *_a, **kwargs: verified.append(kwargs))
    monkeypatch.setattr(PinnedLiveCommission, "credentials", lambda *_a:
        credential_reads.append("synthetic-only") or ("SYNTHETIC KEY", "SYNTHETIC SECRET"))
    broker = ScriptedAccountBroker(fixture.scope)
    broker.clock = fixture.clock

    class SyntheticTransport:
        async def aclose(self):
            pass

    monkeypatch.setattr(live_runtime, "KrakenRestTransport", lambda **_kwargs: SyntheticTransport())
    monkeypatch.setattr(live_runtime, "KrakenLiveBroker", lambda *_a, **_kwargs: broker)
    monkeypatch.setattr(live_runtime, "ProtectedLiveDeploymentBinding", lambda *_a, **_kwargs:
        SimpleNamespace(prepare=lambda: {}, ready=lambda: False))
    if not admitted:
        with pytest.raises(AuthorityDenied, match="actual_host_account_economics_or_owner_evidence_missing"):
            assemble_live_runtime(fixture.db.path, config=config, protected_owner=fixture.private, clock=fixture.clock)
        assert verified == [] and credential_reads == [] and broker.calls == []
        return
    runtime = assemble_live_runtime(fixture.db.path, config=config, protected_owner=fixture.private,
                                    clock=fixture.clock)
    runtime.public_feed = None
    try:
        assert verified and verified[0]["management_only"] is True
        result = asyncio.run(LiveService(runtime).run(max_ticks=1, install_signal_handlers=False))
        assert result["completed"] == 0 and runtime.execution.intent_state(unknown.intent_id) == "UNKNOWN"
        assert ("status", unknown.client_order_id) in broker.calls and ("fills", None) in broker.calls
        assert not any(kind == "submit" for kind, _ in broker.calls)
        assert runtime.database.execute("SELECT state FROM live_pilot_grants").fetchone()[0] == "RECOVERY_REQUIRED"
        assert runtime.database.execute("SELECT state FROM position_reservations WHERE intent_id=?",
                                        (unknown.intent_id,)).fetchone()[0] == "held"
    finally:
        runtime.database.close()


@pytest.mark.parametrize("originator", ["owner", "system", "leader"])
@pytest.mark.parametrize("profile", ["FLATTEN", "CANCEL_ALL", "STOPPED"])
def test_live_restart_never_weakens_existing_position_management(fixture, originator, profile):
    runtime, broker, service = scripted_service(fixture)
    runtime.execution.set_pause(fixture.pid, profile, originator, "synthetic stronger management")
    asyncio.run(service.run(max_ticks=1, install_signal_handlers=False))
    assert runtime.execution.pause(fixture.pid)["profile"] == profile
    assert runtime.execution.pause(fixture.pid)["originator"] == originator
    assert not any(kind == "submit" for kind, _ in broker.calls)


def test_live_start_recovers_interrupted_owner_receipt_under_os_fence_without_replaying(fixture, monkeypatch):
    import fcntl
    import os

    from trade_graph.api.controls import PauseCommand, _Commands
    from trade_graph.application import live_service
    from trade_graph.application.owner_commands import command_scopes, recover_owner_commands

    runtime, broker, service = scripted_service(fixture)
    runtime.deployment_id = fixture.scope.deployment_id
    command = PauseCommand(request_id="synthetic-interrupted-live-pause", expected_revision=0, profile="MANAGE_ONLY")
    _Commands(runtime).begin(command_scopes(runtime)[0], command, "pause",
        lambda: runtime.execution.set_pause(fixture.pid, "MANAGE_ONLY", "owner", "synthetic paused command"))
    runtime.execution.set_pause(fixture.pid, "FLATTEN", "owner", "synthetic subsequent stronger pause")
    fixture.db.execute("INSERT INTO dashboard_commands VALUES ('unrelated-live-request',?, 'opaque', NULL,"
                       "'PROCESSING',?)", ("owner:other-deployment", fixture.clock.now().isoformat()))
    fence_observed = []

    def recover_with_fence(value):
        descriptor = os.open(value.database.path, os.O_RDONLY)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(descriptor)
        fence_observed.append(True)
        return recover_owner_commands(value)

    monkeypatch.setattr(live_service, "recover_owner_commands", recover_with_fence, raising=False)
    asyncio.run(service.run(max_ticks=1, install_signal_handlers=False))
    assert fence_observed == [True]
    row = fixture.db.execute("SELECT status,response_json FROM dashboard_commands WHERE command_id=?",
                             (command.request_id,)).fetchone()
    assert row["status"] == "FAILED:409"
    result = json.loads(row["response_json"])
    assert result["detail"]["needs_review"] and result["detail"]["replayed"] is False
    assert runtime.execution.pause(fixture.pid)["profile"] == "FLATTEN"
    assert fixture.db.execute("SELECT status FROM dashboard_commands WHERE command_id='unrelated-live-request'"
                              ).fetchone()[0] == "PROCESSING"
    assert not any(kind == "submit" for kind, _ in broker.calls)
