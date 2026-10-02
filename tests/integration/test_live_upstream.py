"""Retained mechanical facts are synthetic fixtures, never paid/live proof."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from tests.integration.test_forward_evaluation import REGISTERED, protocol
from tests.integration.test_live_readiness import Fixture
from tests.integration.test_venue_conformance import Fixture as VenueFixture

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.authority import AuthorityRecord
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.ledger import Ledger
from trade_graph.application.operations_evidence import HostObservationCollector
from trade_graph.application.venue_conformance import PinnedReadOnlyAuthority, VenueObservationScope, _canonical, _mac
from trade_graph.contracts.models import ModelUsage, PriceCard
from trade_graph.domain.clock import utc_iso
from trade_graph.evaluation_contracts import CostInventory, ExpenseEvidence, Window
from trade_graph.evaluation_registry import TrialRegistry, document_hash
from trade_graph.live_evidence import EconomicUpstreamSource, HostUpstreamSource, LiveUpstreamSources
from trade_graph.live_gate import evaluate_live_readiness
from trade_graph.runtime_evidence import RuntimeEvidenceCollector


@pytest.fixture
def fixture(tmp_path):
    value = Fixture(tmp_path)
    yield value
    value.db.close()


@pytest.fixture
def collected(fixture):
    database = Database(fixture.private / "paper.sqlite")
    database.path.chmod(0o600)
    ledger = Ledger(database, fixture.clock)
    paper_id = ledger.create_portfolio(reporting_currency="EUR", mode="paper")
    ledger.deposit(paper_id, "USD", Decimal("10000"), "synthetic-paper-opening")
    policy = AuthorityRecord(fixture.db, fixture.clock).active_policy()
    AuthorityRecord(database, fixture.clock).install_policy(policy, role="owner")
    BudgetGateway(database, fixture.clock).configure(
        deployment_id=fixture.scope.deployment_id, currency="EUR", total=Decimal(5), period=Decimal(5),
        priority_reserve=Decimal(1), daily=Decimal("1.5"), root=Decimal("1.5"), roles={"trader": Decimal(5)},
    )
    database.execute("INSERT INTO active_versions "
                     "(portfolio_id,version_id,artifact_hash,fingerprint_json,activated_at) "
                     "VALUES (?, 'fixture', ?, '{}', ?)",
                     (paper_id, fixture.scope.system_version_sha256, utc_iso(fixture.clock.now())))
    declared = protocol(count=2, portfolio_id=paper_id, selected_version_sha256=fixture.scope.system_version_sha256,
                        allowed_versions_sha256=(fixture.scope.system_version_sha256,))
    shift = fixture.clock.now() - REGISTERED
    values = declared.model_dump()
    for key in ("development", "validation"):
        window = getattr(declared, key)
        values[key] = Window(start=window.start + shift, end=window.end + shift)
    values["forward_blocks"] = tuple(Window(start=window.start + shift, end=window.end + shift)
                                      for window in declared.forward_blocks)
    declared = type(declared).model_validate(values)
    registry = TrialRegistry(fixture.private / "forward.sqlite", fixture.clock)
    registry.register(declared)
    collector = RuntimeEvidenceCollector(database, registry, fixture.clock, deployment_id=fixture.scope.deployment_id)
    collector.bind(declared.trial_id)
    fixture.clock.advance((declared.forward_blocks[-1].end - fixture.clock.now()).total_seconds())
    initial = collector.capture(declared.trial_id)
    registry.seal_cost_inventory(declared.trial_id, CostInventory(
        source_ref="synthetic-complete-runtime-export", ledger_export_sha256=initial.source_sha256,
        source_cutoff=fixture.clock.now(), receipt_ids=(),
    ))
    capture = collector.capture(declared.trial_id)
    for entry in [fixture.document["owner_authorization"], *fixture.document["evidence"]]:
        entry["payload"]["verified_at"] = fixture.clock.now().isoformat()
        entry["payload"]["expires_at"] = (fixture.clock.now() + timedelta(days=1)).isoformat()
    fixture.document["owner_authorization"]["payload"]["purpose"] = "diagnostic_execution_measurement"
    proof = fixture.evidence("economic_evaluation")
    proof.update(source_sha256=[document_hash(capture.model_dump_json())],
                 forward_protocol_sha256=capture.evaluation_snapshot.protocol_sha256,
                 forward_report_sha256=capture.evaluation_snapshot.report_sha256,
                 sealed_inventory_sha256=capture.evaluation_snapshot.inventory_sha256,
                 registry_snapshot_sha256=capture.evaluation_snapshot_sha256,
                 economic_verdict=json.loads(capture.evaluation_snapshot.report_json)["verdict"])
    fixture.db.execute("UPDATE activity_events SET created_at=? WHERE kind='execution_reconciliation_health'",
                       (utc_iso(fixture.clock.now()),))
    source = EconomicUpstreamSource(collector, capture, paper_id, document_hash(capture.model_dump_json()))
    yield SimpleNamespace(database=database, ledger=ledger, registry=registry, collector=collector,
                          capture=capture, source=source, declared=declared)
    registry.close()
    database.close()


def project(fixture, upstream=None):
    return evaluate_live_readiness(fixture.db, fixture.clock, scope=fixture.scope,
                                  source=fixture.source(), upstream=upstream)


def codes(result):
    return {item["code"] for item in result["upstream_verification"]["unresolved"]}


def refresh_capture(fixture, collected):
    capture = collected.collector.capture(collected.declared.trial_id)
    fixture.evidence("economic_evaluation").update(
        source_sha256=[document_hash(capture.model_dump_json())],
        forward_protocol_sha256=capture.evaluation_snapshot.protocol_sha256,
        forward_report_sha256=capture.evaluation_snapshot.report_sha256,
        sealed_inventory_sha256=capture.evaluation_snapshot.inventory_sha256,
        registry_snapshot_sha256=capture.evaluation_snapshot_sha256,
        economic_verdict=json.loads(capture.evaluation_snapshot.report_json)["verdict"],
    )
    return EconomicUpstreamSource(collected.collector, capture, collected.source.paper_portfolio_id,
                                  document_hash(capture.model_dump_json()))


def reserve_synthetic(fixture, collected):
    budget = BudgetGateway(collected.database, fixture.clock)
    budget.seed_card(PriceCard(
        price_card_id="synthetic-inventory-card", provider="scripted", model="scripted",
        endpoint="https://example.invalid", currency="EUR", input_per_million="1", output_per_million="0",
        effective_at="2026-01-01", verified_at="2026-01-01", source_id="synthetic-inventory",
        tier="standard", context_band="short",
    ))
    reservation = budget.reserve(
        deployment_id=fixture.scope.deployment_id, role="trader", task_id="synthetic-inventory-task",
        root_task_id="synthetic-inventory-root", price_card_id="synthetic-inventory-card",
        max_input=10000, max_output=0, max_tools=0, fx_rate=Decimal(1), fx_buffer=Decimal(1), priority=False,
        synthetic=True, purpose="synthetic receipt completeness fixture", system_version_id="fixture",
    )
    return budget, reservation


@pytest.fixture
def host_source(fixture, collected, monkeypatch):
    for suffix in ("-wal", "-shm"):
        path = collected.database.path.with_name(collected.database.path.name + suffix)
        if path.exists():
            path.chmod(0o600)
    collector = HostObservationCollector(collected.database.path, signing_key=b"synthetic-host-key-32-bytes-onlyxx",
                                         clock=fixture.clock)
    monkeypatch.setattr(collector, "_service", lambda: {"status": "pending", "facts": {"systemd": False}})
    retained = collector.capture(fixture.private / "host.json", backup_restore=True)
    document = collector.verify(retained.path, retained.sha256).document
    fixture.evidence("host_backup_alerts")["source_sha256"] = [
        retained.sha256, document["collector_artifact_sha256"], document["protected_package_sha256"],
    ]
    return HostUpstreamSource(collector, retained.path, retained.sha256, document["observed_host_sha256"],
                              document["protected_package_sha256"], collected.source.paper_portfolio_id)


@pytest.fixture
def venue_source(fixture, tmp_path):
    directory = tmp_path / "venue"
    directory.mkdir(mode=0o700)
    producer = VenueFixture(directory)
    producer.scope = VenueObservationScope(**fixture.scope.model_dump(), mode="live")
    producer.grant = producer.grant.model_copy(update={"scope": producer.scope})
    payload = producer.grant.model_dump(mode="json")
    raw = _canonical({"payload": payload, "signature": _mac(producer.owner_key, "owner-grant", payload)})
    producer.authority.path.chmod(0o600)
    producer.authority.path.write_bytes(raw)
    producer.authority.path.chmod(0o400)
    producer.authority = PinnedReadOnlyAuthority(producer.authority.path, hashlib.sha256(raw).hexdigest(),
                                                producer.owner_key)
    retained = asyncio.run(producer.collect())
    observation = retained.verify(now=datetime.now(UTC)).observation
    fixture.clock.advance((observation.finished_at - fixture.clock.now()).total_seconds())
    for entry in [fixture.document["owner_authorization"], *fixture.document["evidence"]]:
        entry["payload"]["verified_at"] = fixture.clock.now().isoformat()
        entry["payload"]["expires_at"] = (fixture.clock.now() + timedelta(days=1)).isoformat()
    fixture.db.execute("UPDATE activity_events SET created_at=? WHERE kind='execution_reconciliation_health'",
                       (utc_iso(fixture.clock.now()),))
    for kind in ("funding", "key_permissions", "venue_metadata_fees", "read_only_reconciliation", "broker_conformance"):
        fixture.evidence(kind)["source_sha256"] = [
            retained.observation_sha256, observation.collector_sha256, observation.adapter_sha256,
            observation.wire_contract_sha256,
        ]
    return retained


def test_missing_collector_sources_are_structured_without_invented_verification(fixture):
    result = project(fixture)
    assert result["recorded_checks_passed"] is True
    assert {"retained_runtime_capture_missing", "retained_intended_host_operations_evidence_missing",
            "retained_authenticated_read_only_evidence_missing"} <= codes(result)
    assert result["ready"] is False and result["enabled"] is False


def test_current_retained_runtime_inventory_is_checked_but_not_external_authentication(fixture, collected):
    result = project(fixture, LiveUpstreamSources(economics=collected.source))
    upstream = result["upstream_verification"]
    assert result["recorded_checks_passed"] is True
    assert all(upstream["checks"].values())
    assert {"external_transport_provenance_missing", "complete_provider_invoice_export_missing",
            "baseline_runtime_collection_missing", "authenticated_complete_external_provenance_missing",
            "economic_live_account_instrument_policy_binding_missing"} <= (
                codes(result))
    assert result["economic_evidence"] == "insufficient_evidence"
    assert upstream["authoritative_external_verification"] is False
    assert result["ready"] is False and result["enabled"] is False
    assert str(collected.database.path) not in json.dumps(upstream)
    assert collected.source.paper_portfolio_id not in json.dumps(upstream)


def test_upstream_projection_rereads_without_writing_financial_or_registry_records(fixture, collected):
    source = fixture.source()
    runtime_before = collected.collector._read()
    registry_before = list(collected.registry.connection.iterdump())
    live_before = list(fixture.db.connection.iterdump())
    bundle_before = source.path.read_bytes()
    evaluate_live_readiness(fixture.db, fixture.clock, scope=fixture.scope, source=source,
                           upstream=LiveUpstreamSources(economics=collected.source))
    assert collected.collector._read() == runtime_before
    assert list(collected.registry.connection.iterdump()) == registry_before
    assert list(fixture.db.connection.iterdump()) == live_before
    assert source.path.read_bytes() == bundle_before


def test_runtime_change_invalidates_previously_retained_capture(fixture, collected):
    collected.ledger.deposit(collected.source.paper_portfolio_id, "EUR", Decimal(1), "later-synthetic-flow")
    result = project(fixture, LiveUpstreamSources(economics=collected.source))
    assert result["upstream_verification"]["status"] == "refused"
    assert "runtime_capture_stale_invalid_or_unretained" in codes(result)


def test_new_registry_expense_invalidates_capture_even_with_same_runtime_bytes(fixture, collected):
    collected.registry.record_expense(ExpenseEvidence(
        receipt_id="synthetic-later-cost", source_ref="fixture-only:cost", conversion_ref="fixture-only:EUR",
        incurred_at=fixture.clock.now(), amount_eur="1", evidence_kind="synthetic",
        cost_class="setup_engineering", outcome="failed",
    ))
    assert "runtime_capture_stale_invalid_or_unretained" in codes(
        project(fixture, LiveUpstreamSources(economics=collected.source)))


@pytest.mark.parametrize("field", ["forward_protocol_sha256", "forward_report_sha256", "sealed_inventory_sha256",
                                   "registry_snapshot_sha256", "economic_verdict"])
def test_signed_issuer_cannot_override_actual_retained_report_bindings(fixture, collected, field):
    fixture.evidence("economic_evaluation")[field] = "supported" if field == "economic_verdict" else "0" * 64
    result = project(fixture, LiveUpstreamSources(economics=collected.source))
    assert result["upstream_verification"]["status"] == "refused"
    assert result["ready"] is False and result["enabled"] is False
    assert result["economic_evidence"] == "insufficient_evidence"
    assert result["declared_economic_verdict"] == fixture.evidence("economic_evaluation")["economic_verdict"]


def test_capture_pin_and_paper_identity_are_protected_runtime_bindings(fixture, collected):
    altered = EconomicUpstreamSource(collected.collector, collected.capture, "other-paper", "0" * 64)
    result = project(fixture, LiveUpstreamSources(economics=altered))
    assert result["upstream_verification"]["checks"]["retained_capture_pin"] is False
    assert result["upstream_verification"]["checks"]["forward_paper_portfolio_scope"] is False


def test_arbitrary_verifier_callback_cannot_mint_source_authenticity(fixture, collected):
    called = []
    fake = SimpleNamespace(verify=lambda capture: called.append(capture) or True)
    source = EconomicUpstreamSource(fake, collected.capture, collected.source.paper_portfolio_id,
                                    collected.source.capture_sha256)
    assert "runtime_capture_stale_invalid_or_unretained" in codes(
        project(fixture, LiveUpstreamSources(economics=source)))
    assert called == []


def test_current_declared_support_without_external_provenance_stays_insufficient(fixture):
    result = project(fixture)
    assert result["declared_economic_verdict"] == "supported"
    assert result["economic_evidence"] == "insufficient_evidence"
    assert result["ready"] is False and result["enabled"] is False


def test_stale_economic_capture_cannot_be_refreshed_by_a_new_signature(fixture, collected):
    fixture.clock.advance(24 * 3600 + 1)
    for entry in [fixture.document["owner_authorization"], *fixture.document["evidence"]]:
        entry["payload"]["verified_at"] = fixture.clock.now().isoformat()
        entry["payload"]["expires_at"] = (fixture.clock.now() + timedelta(days=1)).isoformat()
    result = project(fixture, LiveUpstreamSources(economics=collected.source))
    assert result["upstream_verification"]["checks"]["runtime_capture_fresh"] is False
    assert result["economic_evidence"] == "insufficient_evidence"


def test_new_known_receipt_cannot_be_omitted_from_current_capture_inventory(fixture, collected):
    budget, reservation = reserve_synthetic(fixture, collected)
    budget.commit(reservation, ModelUsage(uncached_input_tokens=10000, billed_output_tokens=0,
                                         provider_request_id="synthetic-inventory-response"),
                  provider="scripted", model="scripted", fx_rate=Decimal(1))
    source = refresh_capture(fixture, collected)
    result = project(fixture, LiveUpstreamSources(economics=source))
    assert result["upstream_verification"]["checks"]["current_runtime_and_registry_sources"] is True
    assert result["upstream_verification"]["checks"]["complete_runtime_receipt_inventory"] is False
    assert result["upstream_verification"]["checks"]["sealed_complete_runtime_export"] is False
    assert "synthetic_receipts_are_not_actual_costs" in codes(result)
    assert result["ready"] is False and result["enabled"] is False


def test_current_unresolved_attempt_cannot_be_hidden_by_empty_sealed_receipt_list(fixture, collected):
    reserve_synthetic(fixture, collected)
    source = refresh_capture(fixture, collected)
    result = project(fixture, LiveUpstreamSources(economics=source))
    assert result["upstream_verification"]["checks"]["complete_runtime_receipt_inventory"] is False
    assert "unresolved_real_cost_inventory" in codes(result)


def test_host_mechanical_backup_facts_leave_actual_service_host_and_alert_proofs_pending(fixture, host_source):
    result = project(fixture, LiveUpstreamSources(host=host_source))
    assert all(result["upstream_verification"]["checks"].values())
    assert {"host_service_unit_configuration_binding_pending", "host_service_restart_reconciliation_pending",
            "host_alerts_verification_pending", "host_off_host_backup_verification_pending",
            "host_immutable_image_verification_pending", "credentialed_soak_and_invoice_collection_pending",
            "independent_owner_intended_host_designation_missing"} <= (
                codes(result))
    assert result["ready"] is False and result["enabled"] is False
    assert str(host_source.observation_path) not in json.dumps(result)


def test_small_deeply_nested_private_host_source_is_refused_without_parser_detail(fixture, host_source):
    from dataclasses import replace

    raw = b'{"malformed_private_record":' + b"[" * 1200 + b"0" + b"]" * 1200 + b"}"
    host_source.observation_path.write_bytes(raw)
    altered = replace(host_source, observation_sha256=hashlib.sha256(raw).hexdigest())
    result = project(fixture, LiveUpstreamSources(host=altered))
    assert "host_observation_stale_invalid_or_unretained" in codes(result)
    assert result["upstream_verification"]["status"] == "refused"
    assert "malformed_private_record" not in json.dumps(result)
    assert str(host_source.observation_path) not in json.dumps(result)


@pytest.mark.parametrize("changed", ["database", "pin", "host", "package", "paper", "time"])
def test_host_source_current_state_and_protected_pins_are_required(fixture, collected, host_source, changed):
    if changed == "database":
        collected.ledger.deposit(collected.source.paper_portfolio_id, "EUR", Decimal(1), "synthetic-host-drift")
    elif changed == "time":
        fixture.clock.advance(24 * 3600 + 1)
    else:
        from dataclasses import replace

        field = {"pin": "observation_sha256", "host": "intended_host_sha256",
                 "package": "protected_package_sha256", "paper": "paper_portfolio_id"}[changed]
        host_source = replace(host_source, **{field: "0" * 64})
    result = project(fixture, LiveUpstreamSources(host=host_source))
    assert result["upstream_verification"]["status"] == "refused"
    assert result["ready"] is False and result["enabled"] is False


def test_retained_scripted_venue_facts_leave_account_auth_and_permission_proofs_pending(fixture, venue_source):
    result = project(fixture, LiveUpstreamSources(venue=venue_source))
    assert all(result["upstream_verification"]["checks"].values())
    assert {"synthetic_or_unverified_reads_are_not_authenticated_proof", "owner_eligibility_unverified",
            "key_permission_inventory_unverified", "withdrawals_absent_unverified",
            "write_cancel_uncertainty_conformance_unverified", "native_stop_protection_unverified"} <= codes(result)
    assert result["ready"] is False and result["enabled"] is False
    assert str(venue_source.path) not in json.dumps(result)


@pytest.mark.parametrize("changed", ["scope", "declaration", "wire", "time"])
def test_venue_scope_wire_declaration_binding_and_freshness_are_required(fixture, venue_source, changed):
    if changed == "scope":
        fixture.scope = fixture.scope.model_copy(update={"account_id": "different-synthetic-account"})
    elif changed == "declaration":
        fixture.evidence("funding")["source_sha256"] = ["0" * 64]
    elif changed == "wire":
        path = next(venue_source.path.parent.glob("wire-*.json"))
        path.chmod(0o600)
        path.write_bytes(path.read_bytes() + b" ")
    else:
        fixture.clock.advance(61)
    result = project(fixture, LiveUpstreamSources(venue=venue_source))
    assert result["upstream_verification"]["status"] == "refused"
    assert result["ready"] is False and result["enabled"] is False
