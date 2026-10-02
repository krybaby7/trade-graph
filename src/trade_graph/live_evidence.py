"""Current-source upstream checks for the closed live-readiness projection.

These references are assembled by the protected runtime. They are not a request
schema, a trust callback, or permission to perform any external observation.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from trade_graph.domain.errors import TradeGraphError
from trade_graph.evaluation_registry import document_hash

_PUBLIC_RUNTIME_GAPS = frozenset({
    "external_transport_provenance_missing", "complete_provider_invoice_export_missing",
    "baseline_runtime_collection_missing", "independence_and_regime_source_verification_pending",
    "embedded_or_external_ledger_expense_receipt_link_requires_verification", "synthetic_runtime_receipts",
    "unresolved_usage_or_invoice_cost", "durable_provider_invocation_missing", "synthetic_provider_invocation",
    "provider_response_identifier_missing", "unresolved_or_unreceipted_attempt",
    "unexplained_provider_invoice_difference", "receipt_fx_source_link_missing",
    "synthetic_or_paper_market_reference", "synthetic_price_source", "provider_transport_attempt_missing",
    "provider_transport_outcome_unresolved", "unverified_or_synthetic_provider_transport",
    "decision_market_reference_missing", "provider_invocation_version_link_missing",
})
_PUBLIC_VENUE_GAPS = frozenset({
    "owner_eligibility_unverified", "native_account_owner_identity_unverified",
    "key_permission_inventory_unverified", "withdrawals_absent_unverified",
    "write_cancel_uncertainty_conformance_unverified", "native_stop_protection_unverified",
    "protected_account_ledger_reconciliation_unverified", "intended_host_dependency_identity_unverified",
    "authenticated_private_observation_missing", "native_observation_incomplete", "historical_account_scope_limited",
})


def _fingerprint(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)

if TYPE_CHECKING:
    from trade_graph.application.operations_evidence import HostObservationCollector
    from trade_graph.application.venue_conformance import PinnedVenueObservation
    from trade_graph.domain.clock import Clock
    from trade_graph.live_gate import LivePilotScope, ReadinessBundle
    from trade_graph.runtime_evidence import RuntimeEvidenceCapture, RuntimeEvidenceCollector


@dataclass(frozen=True)
class EconomicUpstreamSource:
    """Retained collector references and independently pinned paper scope."""

    collector: RuntimeEvidenceCollector = field(repr=False)
    capture: RuntimeEvidenceCapture = field(repr=False)
    paper_portfolio_id: str = field(repr=False)
    capture_sha256: str


@dataclass(frozen=True)
class HostUpstreamSource:
    collector: HostObservationCollector = field(repr=False)
    observation_path: Path = field(repr=False)
    observation_sha256: str
    intended_host_sha256: str
    protected_package_sha256: str
    paper_portfolio_id: str = field(repr=False)


@dataclass(frozen=True)
class LiveUpstreamSources:
    economics: EconomicUpstreamSource | None = field(default=None, repr=False)
    host: HostUpstreamSource | None = field(default=None, repr=False)
    venue: PinnedVenueObservation | None = field(default=None, repr=False)


def verify_live_upstream(
    scope: LivePilotScope, bundle: ReadinessBundle, clock: Clock, sources: LiveUpstreamSources | None,
) -> dict:
    """Re-read retained facts; authentic external observation remains separate.

    Returned codes are bounded program-defined names. Private account/receipt/
    path/source records never appear in the readiness output.
    """
    checks: dict[str, bool] = {}
    unresolved: list[dict] = []

    def pending(source: str, code: str, *, status: str = "pending", count: int | None = None) -> None:
        item = {"source": source, "code": code, "status": status}
        if count is not None:
            item["count"] = count
        unresolved.append(item)

    def check(code: str, ok: bool, *, source: str = "forward_runtime") -> None:
        checks[code] = ok
        if not ok:
            pending(source, code, status="refused")

    economic_source = sources.economics if type(sources) is LiveUpstreamSources else None
    if economic_source is None:
        pending("forward_runtime", "retained_runtime_capture_missing")
    else:
        try:
            # The verifier is trusted implementation code, not an arbitrary
            # caller-provided predicate or object with a matching method name.
            from trade_graph.runtime_evidence import RuntimeEvidenceCapture, RuntimeEvidenceCollector

            if (type(economic_source) is not EconomicUpstreamSource
                    or type(economic_source.collector) is not RuntimeEvidenceCollector
                    or type(economic_source.capture) is not RuntimeEvidenceCapture):
                raise ValueError("protected collector type required")
            capture = economic_source.capture
            snapshot = capture.evaluation_snapshot
            capture_digest = document_hash(capture.model_dump_json())
            check("retained_capture_pin", capture_digest == economic_source.capture_sha256)
            check("forward_deployment_scope", capture.binding.deployment_id == scope.deployment_id)
            check("forward_paper_portfolio_scope", capture.binding.portfolio_id == economic_source.paper_portfolio_id
                  and snapshot.portfolio_id == economic_source.paper_portfolio_id)
            check("forward_live_portfolio_separation", snapshot.portfolio_id != scope.portfolio_id)
            check("forward_selected_version", snapshot.selected_version_sha256 == scope.system_version_sha256)
            check("runtime_capture_fresh", timedelta(0) <= clock.now() - capture.captured_at <= timedelta(hours=24))
            proof = next((entry.payload for entry in bundle.evidence
                          if entry.payload.kind == "economic_evaluation"), None)
            check("issuer_bound_runtime_capture", bool(proof and capture_digest in proof.source_sha256))
            check("issuer_bound_evaluation_snapshot", bool(proof
                  and proof.registry_snapshot_sha256 == capture.evaluation_snapshot_sha256))
            check("issuer_bound_protocol_report_inventory", bool(proof
                  and proof.forward_protocol_sha256 == snapshot.protocol_sha256
                  and proof.forward_report_sha256 == snapshot.report_sha256
                  and proof.sealed_inventory_sha256 == snapshot.inventory_sha256))
            check("issuer_bound_economic_verdict", bool(proof
                  and proof.economic_verdict == json.loads(snapshot.report_json)["verdict"]))
            check("issuer_observed_after_capture", bool(proof and proof.verified_at >= capture.captured_at))
            verification = economic_source.collector.verify(capture)
            check("current_runtime_and_registry_sources", verification.database_consistent)
            check("verified_snapshot_identity", verification.evaluation_snapshot_sha256
                  == capture.evaluation_snapshot_sha256)
            inventories = json.loads(snapshot.report_json)["cost_inventory"]["imports"]
            check("sealed_complete_runtime_export", len(inventories) == 1
                  and snapshot.ledger_export_sha256 == capture.source_sha256)
            check("complete_runtime_receipt_inventory", len(inventories) == 1
                  and set(inventories[0]["receipt_ids"]) == set(verification.receipt_ids)
                  and not verification.unresolved_reservation_ids)
            if verification.synthetic_receipt_ids:
                pending("forward_runtime", "synthetic_receipts_are_not_actual_costs",
                        count=len(verification.synthetic_receipt_ids))
            if verification.unresolved_reservation_ids:
                pending("forward_runtime", "unresolved_real_cost_inventory",
                        count=len(verification.unresolved_reservation_ids))
            if verification.reasons:
                # Preserve the existence/count without leaking receipt or private
                # input values interpolated into an upstream diagnostic.
                pending("forward_runtime", "runtime_inventory_or_provenance_incomplete",
                        count=len(verification.reasons))
                for code in sorted(set(verification.reasons) & _PUBLIC_RUNTIME_GAPS):
                    pending("forward_runtime", code)
            if not verification.actual_external_provenance_verified:
                pending("forward_runtime", "authenticated_complete_external_provenance_missing")
            # Current preregistration binds market-stream/version, but has no
            # authenticated mapping to the live account/instrument/owner policy.
            pending("forward_runtime", "economic_live_account_instrument_policy_binding_missing")
        except ImportError:
            pending("forward_runtime", "protected_runtime_collector_unavailable")
        except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError, OSError, RecursionError,
                sqlite3.DatabaseError, TradeGraphError):
            pending("forward_runtime", "runtime_capture_stale_invalid_or_unretained", status="refused")

    host_source = sources.host if type(sources) is LiveUpstreamSources else None
    if host_source is None:
        pending("intended_host", "retained_intended_host_operations_evidence_missing")
    else:
        try:
            from trade_graph.application.operations_evidence import HostObservationCollector

            if (type(host_source) is not HostUpstreamSource
                    or type(host_source.collector) is not HostObservationCollector):
                raise ValueError("protected host collector required")
            observation = host_source.collector.verify(host_source.observation_path, host_source.observation_sha256)
            document = observation.document
            binding = document["scope"]
            proof = next((entry.payload for entry in bundle.evidence
                          if entry.payload.kind == "host_backup_alerts"), None)
            host_digests = {observation.sha256, document["collector_artifact_sha256"],
                            document["protected_package_sha256"]}
            check("issuer_bound_host_observation", bool(proof and host_digests <= set(proof.source_sha256)),
                  source="intended_host")
            check("host_deployment_policy_scope", (
                binding["deployment_id"], binding["policy_revision"], binding["policy_sha256"],
            ) == (scope.deployment_id, scope.policy_revision, scope.policy_sha256), source="intended_host")
            check("host_collection_paper_scope", binding["portfolio_id"] == host_source.paper_portfolio_id,
                  source="intended_host")
            check("host_current_graph_version", binding["artifact_sha256"] == scope.system_version_sha256,
                  source="intended_host")
            check("configured_intended_host_fingerprint", _fingerprint(host_source.intended_host_sha256)
                  and document["observed_host_sha256"] == host_source.intended_host_sha256,
                  source="intended_host")
            check("configured_protected_package_fingerprint", _fingerprint(host_source.protected_package_sha256)
                  and document["protected_package_sha256"]
                  == host_source.protected_package_sha256, source="intended_host")
            from trade_graph.domain.clock import parse_utc

            ended = parse_utc(document["ended_at"])
            check("host_observation_fresh", timedelta(0) <= clock.now() - ended <= timedelta(hours=24),
                  source="intended_host")
            check("host_issuer_observed_after_capture", bool(proof and proof.verified_at >= ended),
                  source="intended_host")
            for name in ("storage", "service", "backup_restore", "off_host_backup", "alerts",
                         "immutable_image", "immutable_mounts"):
                fact = document["checks"].get(name, {})
                if fact.get("status") != "observed":
                    pending("intended_host", f"host_{name}_verification_pending",
                            status="refused" if fact.get("status") == "refused" else "pending")
            service_facts = document["checks"].get("service", {}).get("facts", {})
            if service_facts.get("unit_configuration_verified") is not True:
                pending("intended_host", "host_service_unit_configuration_binding_pending")
            restore_facts = document["checks"].get("backup_restore", {}).get("facts", {})
            if restore_facts.get("restart_verification") != "observed":
                pending("intended_host", "host_service_restart_reconciliation_pending")
            # 'funded_setup' is preflight configuration, never actual paid work.
            pending("funded_paper", "credentialed_soak_and_invoice_collection_pending")
            pending("intended_host", "independent_owner_intended_host_designation_missing")
        except ImportError:
            pending("intended_host", "protected_host_collector_unavailable")
        except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError, OSError, RecursionError,
                sqlite3.DatabaseError, TradeGraphError):
            pending("intended_host", "host_observation_stale_invalid_or_unretained", status="refused")

    venue_source = sources.venue if type(sources) is LiveUpstreamSources else None
    if venue_source is None:
        pending("venue_account", "retained_authenticated_read_only_evidence_missing")
    else:
        try:
            from trade_graph.application.venue_conformance import PinnedVenueObservation

            if type(venue_source) is not PinnedVenueObservation:
                raise ValueError("protected venue observation required")
            verification = venue_source.verify(now=clock.now(), maximum_age_seconds=60)
            observation = verification.observation
            observed_scope = observation.scope.model_dump(mode="json")
            mode = observed_scope.pop("mode")
            check("venue_exact_live_scope", mode == "live" and observed_scope == scope.model_dump(mode="json"),
                  source="venue_account")
            check("venue_current_source_artifacts", verification.source_current, source="venue_account")
            kinds = ("funding", "key_permissions", "venue_metadata_fees",
                     "read_only_reconciliation", "broker_conformance")
            for kind in kinds:
                proof = next((entry.payload for entry in bundle.evidence if entry.payload.kind == kind), None)
                venue_digests = {verification.source_sha256, observation.collector_sha256,
                                observation.adapter_sha256, observation.wire_contract_sha256}
                check(f"issuer_bound_{kind}_wire_observation", bool(proof
                      and venue_digests <= set(proof.source_sha256)), source="venue_account")
                check(f"issuer_observed_after_{kind}_capture", bool(proof
                      and proof.verified_at >= observation.finished_at), source="venue_account")
            if not verification.authenticated_reads:
                pending("venue_account", "synthetic_or_unverified_reads_are_not_authenticated_proof")
            if verification.pending:
                pending("venue_account", "eligibility_permissions_write_protection_conformance_pending",
                        count=len(verification.pending))
                for code in sorted(set(verification.pending) & _PUBLIC_VENUE_GAPS):
                    pending("venue_account", code)
        except ImportError:
            pending("venue_account", "protected_venue_collector_unavailable")
        except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError, OSError, RecursionError,
                sqlite3.DatabaseError, TradeGraphError):
            pending("venue_account", "venue_observation_stale_invalid_or_unretained", status="refused")
    pending("live_authority", "protected_pilot_grant_stop_revocation_lifecycle_pending")
    pending("protected_kernel_deployment", "independent_actual_host_isolation_recovery_proof_missing")
    return {"status": "refused" if any(item["status"] == "refused" for item in unresolved) else "pending",
            "checks": checks, "unresolved": unresolved,
            "authoritative_external_verification": False, "live_authorization": False}
