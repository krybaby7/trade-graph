"""Actual paper execution and protected derivation with synthetic market fixtures."""

import asyncio
import hashlib
import hmac
import json
import os
import sqlite3
from datetime import timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from tests.integration.test_cost_acceptance import _stack
from tests.integration.test_execution import _decision, _quote, _rules
from tests.integration.test_forward_evaluation import REGISTERED, protocol
from tests.integration.test_live_readiness import Fixture as LiveFixture
from tests.integration.test_runtime_evidence import receipt

from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.application.activation import VersionController
from trade_graph.application.authority import AuthorityRecord, paper_mandate, paper_owner_policy
from trade_graph.application.execution import Execution
from trade_graph.contracts.models import FillFeeRecord, FillRecord
from trade_graph.domain.clock import utc_iso
from trade_graph.evaluation_contracts import ARMS, CostInventory
from trade_graph.evaluation_registry import TrialRegistry, document_hash
from trade_graph.live_evidence import EconomicUpstreamSource, LiveUpstreamSources, verify_live_upstream
from trade_graph.live_gate import LivePilotScope, ReadinessBundle, _canonical
from trade_graph.live_mapping import PaperLiveMapping, PinnedPaperLiveMapping
from trade_graph.paper_forward_producer import (
    PaperBlockReceipt,
    PaperForwardProducer,
    PaperProducerPolicy,
    paper_producer_controller_sha256,
)
from trade_graph.runtime_evidence import RuntimeEvidenceCollector


@pytest.fixture
def run(tmp_path):
    runtime = _stack(tmp_path)
    runtime.clock.advance((REGISTERED - runtime.clock.now()).total_seconds())
    declared = protocol(count=2, portfolio_id=runtime.portfolio_id, capital_eur="100")
    registry = TrialRegistry(tmp_path / "trial.sqlite", runtime.clock)
    registry.register(declared)
    collector = RuntimeEvidenceCollector(runtime.database, registry, runtime.clock, deployment_id="fixture")
    collector.bind(declared.trial_id)
    portfolios = {"agent": runtime.portfolio_id}
    for arm in ARMS[1:]:
        pid = runtime.ledger.create_portfolio(reporting_currency="EUR", portfolio_id=f"synthetic-{arm}")
        runtime.ledger.deposit(pid, "EUR", Decimal("100"), f"opening:{arm}")
        portfolios[arm] = pid
    versions = VersionController(runtime.database, runtime.clock)
    authority = AuthorityRecord(runtime.database, runtime.clock)
    authority.install_policy(paper_owner_policy(symbols=["BTC/EUR"]), role="owner")
    for pid in portfolios.values():
        versions.ensure(pid, "v1", declared.selected_version_sha256)
        authority.install_mandate(paper_mandate(pid, symbols=["BTC/EUR"]), role="owner")
    broker = PaperBroker(runtime.database, runtime.clock)
    execution = Execution(runtime.database, runtime.ledger, runtime.clock, broker)
    execution.register_instrument(_rules().model_copy(update={"symbol": "BTC/EUR", "quote_asset": "EUR"}))
    policy = PaperProducerPolicy(sampling_seconds=1800, symbols=("BTC/EUR",), maximum_quote_age_seconds=60)
    producer = PaperForwardProducer(
        collector,
        tmp_path / "private" / "producer.sqlite",
        policy=policy,
        authentication_key=b"synthetic independent key".ljust(32, b"."),
        expected_controller_sha256=paper_producer_controller_sha256(),
    )
    producer.bind(declared.trial_id, portfolios)
    result = SimpleNamespace(
        runtime=runtime,
        registry=registry,
        collector=collector,
        declared=declared,
        portfolios=portfolios,
        producer=producer,
        execution=execution,
        broker=broker,
        policy=policy,
    )
    yield result
    producer.close()
    registry.close()
    runtime.database.close()


def market(run, *, bid="99", ask="100", identity=None):
    runtime = run.runtime
    identity = identity or f"fixture:{utc_iso(runtime.clock.now())}"
    quote = _quote(runtime.clock, bid, ask, observation_id=identity).model_copy(update={"symbol": "BTC/EUR"})
    run.execution.on_observation(quote)
    for pid in run.portfolios.values():
        runtime.ledger.observe_mark(
            pid, "BTC", (Decimal(bid) + Decimal(ask)) / 2, "EUR", source="synthetic common midpoint"
        )
    return quote


def move(run, at, **prices):
    run.runtime.clock.advance((at - run.runtime.clock.now()).total_seconds())
    return market(run, **prices)


def trade(run, arm, *, action="enter", identity=None):
    runtime = run.runtime
    pid = run.portfolios[arm]
    identity = identity or f"decision:{arm}:{utc_iso(runtime.clock.now())}"
    quote = run.execution.latest_observation("BTC/EUR", utc_iso(runtime.clock.now()), "paper")
    snapshot = f"snapshot:{identity}"
    context = {"system_version_id": "v1", "market": {quote.symbol: {"observation": quote.model_dump(mode="json")}}}
    runtime.database.execute(
        "INSERT INTO snapshots VALUES (?,?,?,?,?)",
        (snapshot, pid, utc_iso(runtime.clock.now()), json.dumps(context), utc_iso(runtime.clock.now())),
    )
    decision = _decision(
        runtime.clock,
        pid,
        record_id=identity,
        snapshot_id=snapshot,
        action=action,
        symbol="BTC/EUR",
        horizon_seconds=60,
    )
    intent = run.execution.authorize(pid, decision)
    asyncio.run(run.execution.dispatch())
    runtime.clock.advance(1)
    market(run)
    return intent


def first_block(run, *, actual_fills=True):
    binding = run.producer.binding(run.declared.trial_id)
    move(run, binding.sampling_times[0])
    run.producer.checkpoint(run.declared.trial_id)
    if actual_fills:
        for arm in ("agent", "buy_and_hold", "deterministic"):
            trade(run, arm)
    move(run, binding.sampling_times[1], bid="109", ask="110")
    run.producer.checkpoint(run.declared.trial_id)
    move(run, binding.sampling_times[2], bid="119", ask="120")
    run.producer.checkpoint(run.declared.trial_id)
    return run.producer.collect_block(run.declared.trial_id, 0)


def test_actual_four_arm_paper_fill_derivation_and_expenses_do_not_manufacture_forward_acceptance(run):
    _, _, paid_receipt = receipt(run.runtime, synthetic=True)
    retained = first_block(run)
    assert run.broker.submit_count == 3
    assert run.runtime.database.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 3
    agent, cash, hold, deterministic = retained.observation.arms
    assert agent.opening_equity_eur == cash.opening_equity_eur == hold.opening_equity_eur == Decimal("100")
    assert agent.closing_equity_eur == hold.closing_equity_eur == deterministic.closing_equity_eur
    assert agent.turnover_eur == Decimal("1")
    assert agent.trading_fees_eur == Decimal("0.008")
    assert agent.measured_slippage_eur == Decimal("0.005")
    assert cash.closing_equity_eur == Decimal("100")
    assert retained.actual_external_provenance_verified is False
    assert retained.production_host_authorization is False
    assert retained.observation.independence_status == "unassessed"
    assert retained.observation.regime == "unassessed"
    assert all(decision.useful is False for decision in retained.observation.decisions)
    run.producer.publish(retained)
    run.producer.publish(retained)
    assert len(run.registry.observations(run.declared.trial_id)) == 1
    assert run.producer.verify(retained).observation_published is True
    run.collector.import_expenses(run.collector.capture(run.declared.trial_id))
    assert paid_receipt in {item.receipt_id for item in run.registry.expenses()}
    report = run.registry.report(run.declared.trial_id)
    assert report["verdict"] == "insufficient_evidence"
    assert report["verification_basis"] == "unverified_imports"
    assert report["live_authorization"] is False


def test_later_legitimate_financial_writes_preserve_old_four_arm_block_and_restart_verification(run):
    retained = first_block(run)
    run.producer.publish(retained)
    run.runtime.clock.advance(1)
    receipt(run.runtime, synthetic=True)
    verified = run.producer.verify(retained)
    assert verified.historical_sources_consistent is True
    reopened = PaperForwardProducer(
        run.collector,
        run.producer.path,
        policy=run.policy,
        authentication_key=run.producer.key,
        expected_controller_sha256=paper_producer_controller_sha256(),
    )
    assert reopened.verify(retained) == verified
    reopened.close()


def test_two_blocks_use_same_boundary_checkpoint_and_actual_continuous_native_equity(run):
    first = first_block(run)
    run.producer.publish(first)
    binding = run.producer.binding(run.declared.trial_id)
    for index in (3, 4):
        move(run, binding.sampling_times[index], bid="119", ask="120")
        run.producer.checkpoint(run.declared.trial_id)
    second = run.producer.collect_block(run.declared.trial_id, 1)
    assert first.checkpoint_sha256[-1] == second.checkpoint_sha256[0]
    assert tuple(item.closing_equity_eur for item in first.observation.arms) == tuple(
        item.opening_equity_eur for item in second.observation.arms
    )
    run.producer.publish(second)
    assert len(run.registry.observations(run.declared.trial_id)) == 2


@pytest.mark.parametrize("what", ["portfolios", "policy", "key", "controller"])
def test_binding_portfolio_policy_key_and_controller_are_not_caller_trust_inputs(run, what):
    if what == "portfolios":
        changed = {**run.portfolios, "cash": run.portfolios["agent"]}
        with pytest.raises(ValueError, match="mapping cannot change"):
            run.producer.bind(run.declared.trial_id, changed)
    elif what == "policy":
        run.producer.policy = run.policy.model_copy(update={"maximum_quote_age_seconds": 300})
        with pytest.raises(ValueError, match="scope"):
            run.producer.binding(run.declared.trial_id)
    elif what == "key":
        run.producer.key = b"a different synthetic private key" * 2
        with pytest.raises(ValueError, match="authentication"):
            run.producer.binding(run.declared.trial_id)
    else:
        run.producer.controller_sha256 = "0" * 64
        with pytest.raises(ValueError, match="controller pin"):
            run.producer.binding(run.declared.trial_id)


@pytest.mark.parametrize("advance", [-1, 6, 1800])
def test_checkpoint_cannot_be_collected_early_late_or_skip_sample(run, advance):
    at = run.producer.binding(run.declared.trial_id).sampling_times[0]
    run.runtime.clock.advance((at - run.runtime.clock.now()).total_seconds() + advance)
    with pytest.raises(ValueError, match="sampling window"):
        run.producer.checkpoint(run.declared.trial_id)
    assert (
        run.producer.connection.execute(
            "SELECT COUNT(*) FROM paper_producer_records WHERE kind='checkpoint'"
        ).fetchone()[0]
        == 0
    )


def test_missing_sample_and_out_of_order_block_cannot_be_omitted(run):
    at = run.producer.binding(run.declared.trial_id).sampling_times[2]
    move(run, at)
    with pytest.raises(ValueError, match="missing preregistered"):
        run.producer.collect_block(run.declared.trial_id, 0)
    with pytest.raises(ValueError, match="chronological"):
        run.producer.collect_block(run.declared.trial_id, 1)


@pytest.mark.parametrize("fact", ["mark", "quote", "receipt", "blob", "receipt_mac"])
def test_forged_prices_receipts_source_objects_and_authentication_refuse_history(run, fact):
    receipt(run.runtime, synthetic=True)
    retained = first_block(run)
    if fact == "mark":
        run.runtime.database.execute("UPDATE valuation_marks SET mark='200'")
    elif fact == "quote":
        run.runtime.database.execute("UPDATE observations SET document_json=replace(document_json,'fixture','forged')")
    elif fact == "receipt":
        run.runtime.database.execute("UPDATE usage_receipts SET native_cost='0'")
    elif fact == "blob":
        run.producer.connection.execute("DROP TRIGGER paper_producer_source_no_update")
        run.producer.connection.execute("UPDATE paper_producer_sources SET compressed_data=x'00'")
    else:
        run.producer.connection.execute("DROP TRIGGER paper_producer_record_no_update")
        run.producer.connection.execute(
            "UPDATE paper_producer_records SET authentication=? WHERE kind='block'", ("0" * 64,)
        )
    with pytest.raises(ValueError, match="history changed|source changed|authentication"):
        run.producer.verify(retained)


def test_semantically_rehashed_but_unretained_observation_cannot_publish(run):
    from trade_graph.evaluation_registry import document_hash

    retained = first_block(run)
    observation = retained.observation.model_copy(update={"regime": "calm"})
    forged = PaperBlockReceipt.model_validate(
        {
            **retained.model_dump(),
            "observation": observation.model_dump(),
            "observation_sha256": document_hash(observation.model_dump_json()),
        }
    )
    with pytest.raises(ValueError, match="not retained"):
        run.producer.publish(forged)
    assert not run.registry.observations(run.declared.trial_id)


def test_trust_boolean_cannot_promote_external_or_intended_host_authentication(run):
    retained = first_block(run, actual_fills=False)
    with pytest.raises(ValidationError):
        PaperBlockReceipt.model_validate({**retained.model_dump(), "actual_external_provenance_verified": True})
    with pytest.raises(ValidationError):
        PaperBlockReceipt.model_validate({**retained.model_dump(), "production_host_authorization": True})


@pytest.mark.parametrize("limit", ["MAX_RECORDS", "MAX_STORED_BYTES", "MAX_EXPANDED_BYTES"])
def test_complete_source_and_receipt_bounds_roll_back_checkpoint_atomically(run, monkeypatch, limit):
    import trade_graph.paper_forward_producer as module

    move(run, run.producer.binding(run.declared.trial_id).sampling_times[0])
    sources = run.producer.connection.execute("SELECT COUNT(*) FROM paper_producer_sources").fetchone()[0]
    records = run.producer.connection.execute("SELECT COUNT(*) FROM paper_producer_records").fetchone()[0]
    monkeypatch.setattr(module, limit, 1)
    with pytest.raises(ValueError, match="bounds"):
        run.producer.checkpoint(run.declared.trial_id)
    assert run.producer.connection.execute("SELECT COUNT(*) FROM paper_producer_sources").fetchone()[0] == sources
    assert run.producer.connection.execute("SELECT COUNT(*) FROM paper_producer_records").fetchone()[0] == records


@pytest.mark.parametrize("storage", ["symlink", "hardlink", "fifo", "public", "runtime", "registry"])
def test_private_sidecar_refuses_nonregular_shared_or_source_storage(run, tmp_path, storage):
    root = tmp_path / "other-private"
    root.mkdir(mode=0o700)
    path = root / "unsafe.sqlite"
    if storage == "symlink":
        path.symlink_to(run.producer.path)
    elif storage == "hardlink":
        os.link(run.producer.path, path)
    elif storage == "fifo":
        os.mkfifo(path, 0o600)
    elif storage == "public":
        path.touch(mode=0o644)
    elif storage == "runtime":
        path = run.runtime.database.path
    else:
        path = Path(run.registry.connection.execute("PRAGMA database_list").fetchone()[2])
    with pytest.raises((ValueError, OSError)):
        PaperForwardProducer(
            run.collector,
            path,
            policy=run.policy,
            authentication_key=run.producer.key,
            expected_controller_sha256=paper_producer_controller_sha256(),
        )


def test_replacing_sidecar_with_identical_copy_invalidates_open_controller(run):
    original = run.producer.path
    replacement = original.with_name("replacement.sqlite")
    copy = sqlite3.connect(replacement)
    run.producer.connection.backup(copy)
    copy.close()
    replacement.chmod(0o600)
    replacement.replace(original)
    with pytest.raises(ValueError, match="identity"):
        run.producer.binding(run.declared.trial_id)


def test_external_decimal_context_cannot_change_money_or_report_derivation(run):
    retained = first_block(run)
    with localcontext() as context:
        context.prec = 2
        assert run.producer.verify(retained).historical_sources_consistent is True


@pytest.mark.parametrize("changed", ["version", "reset", "mode", "currency", "scope"])
def test_actual_baseline_or_paper_scope_changes_cannot_be_relabelled_as_same_arm(run, changed):
    pid = run.portfolios["cash"]
    if changed == "version":
        run.runtime.database.execute("UPDATE active_versions SET artifact_hash=? WHERE portfolio_id=?", ("b" * 64, pid))
    elif changed == "reset":
        run.runtime.database.execute(
            "UPDATE portfolios SET reset_of=? WHERE portfolio_id=?", (run.portfolios["agent"], pid)
        )
    elif changed == "mode":
        run.runtime.database.execute("UPDATE portfolios SET mode='live' WHERE portfolio_id=?", (pid,))
    elif changed == "currency":
        run.runtime.database.execute("UPDATE portfolios SET reporting_currency='USD' WHERE portfolio_id=?", (pid,))
    else:
        run.runtime.database.execute("UPDATE portfolios SET status='closed' WHERE portfolio_id=?", (pid,))
    move(run, run.producer.binding(run.declared.trial_id).sampling_times[0])
    with pytest.raises(ValueError, match="changed|EUR-valued"):
        run.producer.checkpoint(run.declared.trial_id)


def owner_mapping(run):
    binding = run.producer.binding(run.declared.trial_id)
    scope = LivePilotScope(
        deployment_id="fixture",
        portfolio_id="synthetic-live-portfolio",
        account_id="synthetic-private-account",
        venue="kraken",
        symbol="BTC/EUR",
        policy_revision="1",
        policy_sha256="b" * 64,
        deployment_artifact_sha256="d" * 64,
        system_version_sha256=run.declared.selected_version_sha256,
    )
    mapping = PaperLiveMapping(
        schema_version=1,
        action="map_paper_evidence_to_live_scope",
        mapping_id="synthetic-mapping",
        live_scope=scope,
        paper_deployment_id=binding.deployment_id,
        trial_id=binding.trial_id,
        protocol_sha256=binding.protocol_sha256,
        database_identity=binding.database_identity,
        producer_binding_sha256=document_hash(binding.model_dump_json()),
        producer_controller_sha256=binding.controller_sha256,
        market_stream_id=binding.market_stream_id,
        symbols=binding.policy.symbols,
        data_policy_sha256=binding.data_policy_sha256,
        friction_policy_sha256=binding.friction_policy_sha256,
        regime_classifier_sha256=binding.regime_classifier_sha256,
        arms=tuple(item.model_dump() for item in binding.arms),
        not_before=run.runtime.clock.now(),
        expires_at=run.runtime.clock.now() + timedelta(days=1),
    )
    key = b"synthetic owner key".ljust(32, b".")
    signature = hmac.new(
        key, b"trade-graph.paper-live-owner-mapping.v1\0" + _canonical(mapping.model_dump(mode="json")), hashlib.sha256
    ).hexdigest()
    raw = _canonical({"payload": mapping.model_dump(mode="json"), "signature": signature})
    path = run.producer.path.with_name("owner-mapping.json")
    path.write_bytes(raw)
    path.chmod(0o600)
    pinned = PinnedPaperLiveMapping(path, hashlib.sha256(raw).hexdigest(), key)
    return binding, scope, pinned


def test_exact_actual_producer_binding_matches_owner_paper_live_mapping_without_external_grant(run):
    binding, scope, pinned = owner_mapping(run)
    declaration = pinned.verify_binding(binding, scope=scope, now=run.runtime.clock.now())
    assert declaration.producer_binding_sha256 == document_hash(binding.model_dump_json())
    assert declaration.live_scope == scope


def test_native_vector_charges_and_rebates_remain_separate_and_already_in_actual_equity(run):
    binding = run.producer.binding(run.declared.trial_id)
    move(run, binding.sampling_times[0])
    run.producer.checkpoint(run.declared.trial_id)
    run.runtime.clock.advance(1)
    fill = FillRecord(
        venue="paper",
        account_id="paper",
        trade_id="synthetic-native-vector",
        intent_id=None,
        symbol="BTC/EUR",
        side="buy",
        quantity="0.01",
        price="100",
        quote_cost="1",
        fee_amount="0",
        fee_asset="EUR",
        liquidity="taker",
        filled_at_utc=run.runtime.clock.now(),
        fee_components=(
            FillFeeRecord(
                asset="EUR", amount="0.03", source_ref="synthetic-charge", effective_at_utc=run.runtime.clock.now()
            ),
            FillFeeRecord(
                asset="EUR", amount="-0.05", source_ref="synthetic-rebate", effective_at_utc=run.runtime.clock.now()
            ),
        ),
    )
    run.runtime.ledger.apply_fill(run.portfolios["agent"], fill, base_asset="BTC", quote_asset="EUR")
    run.runtime.database.execute(
        "INSERT INTO fills VALUES (?,?,?,?,?,?,?,?)",
        (
            "synthetic-fill-id",
            fill.venue,
            fill.account_id,
            fill.trade_id,
            run.portfolios["agent"],
            None,
            fill.model_dump_json(),
            utc_iso(run.runtime.clock.now()),
        ),
    )
    for index in (1, 2):
        move(run, binding.sampling_times[index], bid="119", ask="120")
        run.producer.checkpoint(run.declared.trial_id)
    retained = run.producer.collect_block(run.declared.trial_id, 0)
    agent = retained.observation.arms[0]
    assert agent.trading_fees_eur == Decimal("0.03")
    assert agent.trading_rebates_eur == Decimal("0.05")
    assert retained.signed_native_fee_totals_eur[0] == "-0.02"
    assert agent.closing_equity_eur == Decimal("100.215")
    run.producer.publish(retained)
    report = run.registry.report(run.declared.trial_id)
    metrics = report["financial_metrics"]["agent"]
    assert metrics["fees_already_in_equity_eur"] == "0.03"
    assert metrics["rebates_already_in_equity_eur"] == "0.05"
    assert metrics["trading_after_friction_eur"] == "0.215"
    assert report["sensitivity"][0]["arms"]["agent"]["additional_fees_eur"] == "0.03"


@pytest.mark.parametrize("changed", ["account", "instrument", "owner_policy", "controller", "source", "arm", "market"])
def test_owner_mapping_cannot_relabel_account_instrument_policy_source_or_actual_arm_identity(run, changed):
    from trade_graph.domain.errors import AuthorityDenied

    binding, scope, pinned = owner_mapping(run)
    if changed == "account":
        scope = scope.model_copy(update={"account_id": "different-account"})
    elif changed == "instrument":
        scope = scope.model_copy(update={"symbol": "ETH/EUR"})
    elif changed == "owner_policy":
        scope = scope.model_copy(update={"policy_sha256": "c" * 64})
    elif changed == "controller":
        binding = binding.model_copy(update={"controller_sha256": "c" * 64})
    elif changed == "source":
        binding = binding.model_copy(update={"initial_source_sha256": "c" * 64})
    elif changed == "market":
        binding = binding.model_copy(update={"market_stream_id": "different-market-stream"})
    else:
        arms = list(binding.arms)
        arms[1] = arms[1].model_copy(update={"portfolio_id": "different-cash-portfolio"})
        binding = binding.model_copy(update={"arms": tuple(arms)})
    with pytest.raises(AuthorityDenied):
        pinned.verify_binding(binding, scope=scope, now=run.runtime.clock.now())


def test_mapped_historical_four_arm_economics_allow_later_ops_but_not_new_omitted_expenses(run, tmp_path):
    first = first_block(run)
    run.producer.publish(first)
    binding = run.producer.binding(run.declared.trial_id)
    for index in (3, 4):
        move(run, binding.sampling_times[index], bid="119", ask="120")
        run.producer.checkpoint(run.declared.trial_id)
    run.producer.publish(run.producer.collect_block(run.declared.trial_id, 1))
    initial = run.collector.capture(run.declared.trial_id)
    run.registry.seal_cost_inventory(
        run.declared.trial_id,
        CostInventory(
            source_ref="synthetic-complete-export",
            ledger_export_sha256=initial.source_sha256,
            source_cutoff=run.runtime.clock.now(),
            receipt_ids=(),
        ),
    )
    capture = run.collector.capture(run.declared.trial_id)
    _, scope, mapping = owner_mapping(run)
    fixture_root = tmp_path / "readiness"
    fixture_root.mkdir(mode=0o700)
    live = LiveFixture(fixture_root)
    try:
        proof = live.evidence("economic_evaluation")
        proof.update(
            source_sha256=[document_hash(capture.model_dump_json())],
            forward_protocol_sha256=capture.evaluation_snapshot.protocol_sha256,
            forward_report_sha256=capture.evaluation_snapshot.report_sha256,
            sealed_inventory_sha256=capture.evaluation_snapshot.inventory_sha256,
            registry_snapshot_sha256=capture.evaluation_snapshot_sha256,
            economic_verdict=json.loads(capture.evaluation_snapshot.report_json)["verdict"],
        )
        bundle = ReadinessBundle.model_validate(live.document)
        source = EconomicUpstreamSource(
            run.collector,
            capture,
            run.portfolios["agent"],
            document_hash(capture.model_dump_json()),
            run.producer,
            mapping,
        )
        run.runtime.clock.advance(1)
        run.runtime.ledger.deposit("synthetic-cash", "EUR", Decimal(1), "later-operational-fixture-flow")
        result = verify_live_upstream(scope, bundle, run.runtime.clock, LiveUpstreamSources(economics=source))
        assert result["checks"]["economic_owner_paper_live_mapping"] is True
        assert result["checks"]["complete_protected_four_arm_collection"] is True
        assert result["checks"]["retained_runtime_history_consistent"] is True
        assert result["checks"]["current_economic_registry_sources"] is True
        codes = {item["code"] for item in result["unresolved"]}
        assert "economic_live_account_instrument_policy_binding_missing" not in codes
        assert "authenticated_complete_external_provenance_missing" in codes
        assert result["live_authorization"] is False
        assert result["authoritative_external_verification"] is False
        receipt(run.runtime, synthetic=True)
        later = verify_live_upstream(scope, bundle, run.runtime.clock, LiveUpstreamSources(economics=source))
        assert later["checks"]["complete_runtime_receipt_inventory"] is False
        assert later["live_authorization"] is False
    finally:
        live.db.close()
