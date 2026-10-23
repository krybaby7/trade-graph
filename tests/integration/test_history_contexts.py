"""Completed hourly inputs reach the actual retained role request and snapshot."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from tests.integration.test_artifact_consumers import _choice
from tests.leadership_support import stack

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.activation import VersionController
from trade_graph.application.artifact_runtime import ArtifactRuntime
from trade_graph.application.authority import paper_mandate
from trade_graph.application.ledger import Ledger
from trade_graph.application.model_invocations import MAX_REQUEST_BYTES
from trade_graph.application.runtime_models import RuntimeModelConfig, assemble_handlers
from trade_graph.application.worker import RoleWorker
from trade_graph.contracts.models import ModelRequest, Observation
from trade_graph.domain.clock import FrozenClock
from trade_graph.paper_runtime import installed_artifacts


class HistoryFlow:
    def __init__(self, tmp_path, *, strategy_ids=None, templates=None):
        self.clock = FrozenClock(datetime(2026, 1, 3, 12, 30, tzinfo=UTC))
        self.db = Database(tmp_path / "history.sqlite")
        self.ledger = Ledger(self.db, self.clock)
        self.pid = self.ledger.create_portfolio(reporting_currency="EUR")
        self.office, self.secretary, _, _ = stack(self.db, self.clock, self.ledger, self.pid)
        self.office.execution.authority.install_mandate(paper_mandate(
            self.pid, revision=2, strategy_ids=strategy_ids or ["slow-trend-pullback", "range-reversion"]),
            role="owner")
        self.versions = VersionController(self.db, self.clock)
        self.files = templates or installed_artifacts()
        self.versions.register_baseline(self.pid, "installed-test", self.files)
        self.baseline = self.versions.current_hash(self.pid)
        self.ref = self.secretary.report(self.pid, role="research", kind="initial", summary="Synthetic setup.",
                                         evidence_refs=[self.baseline], source_key="initial")
        self.bind()
        self.office.execution.save_observation(Observation(
            observation_id="public-quote", venue="paper", symbol="BTC/USD", event_time_utc=self.clock.now(),
            available_at_utc=self.clock.now(), bid="100", ask="101", kind="quote", source="kraken_public_ticker"))

    def bind(self):
        self.runtime = ArtifactRuntime(self.versions, self.office.scheduler)
        config = RuntimeModelConfig(approved_price_card_ids=["scripted-review"],
                                   role_routes={role: "scripted-review" for role in ["trader", "research"]})
        self.assembly = assemble_handlers(self.office, self.secretary, SimpleNamespace(), self.runtime, config,
                                         workspace_root=self.db.path.parent / "engineering", api_keys={})
        self.assembly.gateway.scripted.outputs["trader"] = _choice()
        self.assembly.gateway.scripted.outputs["research"] = {
            "evidence_refs": [self.ref], "summary": "Inspected historical sources.",
            "outcome": "Unproven.", "findings": []}
        self.worker = RoleWorker(self.office.scheduler, owner="history-worker", system_version_id=self.baseline,
                                 reconcile=lambda: None, artifact_runtime=self.runtime)

    def run(self, role):
        task_id = self.office.scheduler.add_task(role=role, objective="Inspect completed history.",
                                                portfolio_id=self.pid, payload={"evidence_refs": [self.ref]},
                                                max_attempts=1, allocated_spend=Decimal("1"))
        assert self.worker.run_available({role: self.assembly.handlers[role]}) == 1
        row = self.db.execute("SELECT status, output_json FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        assert row["status"] == "SUCCEEDED", row["output_json"]
        return task_id, self.retained(task_id)

    def retained(self, task_id):
        row = self.db.execute("SELECT request_json FROM model_invocations WHERE task_id=?", (task_id,)).fetchone()
        request = ModelRequest.model_validate_json(row[0])
        snapshot = json.loads(self.db.execute("SELECT payload_json FROM snapshots WHERE snapshot_id=?",
                                             (request.run_id,)).fetchone()[0])
        assert request.context["market"] == snapshot["market"]
        return request, snapshot

    def reopen(self):
        from trade_graph.adapters.brokers.paper import PaperBroker
        from trade_graph.application.budget import BudgetGateway
        from trade_graph.application.execution import Execution
        from trade_graph.application.leader import LeaderOffice, Secretary
        from trade_graph.application.scheduler import Scheduler

        path = self.db.path
        self.db.close()
        self.db = Database(path)
        self.ledger = Ledger(self.db, self.clock)
        execution = Execution(self.db, self.ledger, self.clock, PaperBroker(self.db, self.clock))
        scheduler = Scheduler(self.db, self.clock)
        self.office = LeaderOffice(execution, scheduler, BudgetGateway(self.db, self.clock))
        self.secretary = Secretary(execution, scheduler)
        self.versions = VersionController(self.db, self.clock)
        self.bind()


def _candles(flow, *, symbol="BTC/USD", source="kraken_public_ohlc", offset=Decimal("0"), omit=None):
    from trade_graph.contracts.price_history import HourlyCandle

    end = flow.clock.now().replace(minute=0, second=0, microsecond=0)
    result = []
    for index in range(50):
        if index == omit:
            continue
        close_time = end - timedelta(hours=49 - index)
        close = Decimal("100") + index + offset
        result.append(HourlyCandle(
            symbol=symbol, open_time_utc=close_time - timedelta(hours=1), close_time_utc=close_time,
            available_at_utc=close_time + timedelta(minutes=1), open=close, high=close + 1, low=close - 1,
            close=close, vwap=close, volume=Decimal("0.125"), trade_count=7, source=source,
            source_ref=f"urn:synthetic-test-ohlc:{symbol}:{index}:{offset}",
            source_hash=hashlib.sha256(f"synthetic-{symbol}-{index}-{offset}".encode()).hexdigest()))
    return result


def _save(flow, candles):
    from trade_graph.application.price_history import HistoryStore

    return HistoryStore(flow.db).save(candles)


@pytest.mark.parametrize("role", ["research", "trader"])
def test_actual_context_reports_installed_feature_requirements_without_history(tmp_path, role):
    flow = HistoryFlow(tmp_path)
    try:
        _, (request, snapshot) = flow.run(role)
        history = request.context["market"]["BTC/USD"]["history"]
        assert set(history["features"]) == {
            "sma_20", "sma_50", "pullback_from_high", "range_high", "range_low", "midpoint"}
        assert all(feature["status"] == "insufficient_history" for feature in history["features"].values())
        assert history["values"] == {}
        assert history["source"] == "kraken_public_ohlc"
        assert history["source_ref"] not in request.context["evidence_refs"]
        assert request.context["market"] == snapshot["market"]
        assert flow.assembly.gateway.paid_calls_enabled is False
        assert flow.office.execution.authority.active_policy().paid_calls_enabled is False
        assert flow.office.execution.authority.active_policy().live_enabled is False
        assert all(row[0] == 1 for row in flow.db.execute("SELECT synthetic FROM usage_receipts"))
    finally:
        flow.db.close()


@pytest.mark.parametrize("role", ["research", "trader"])
def test_completed_history_reaches_actual_request_and_remains_pinned_after_restart(tmp_path, role):
    flow = HistoryFlow(tmp_path)
    try:
        original_at = flow.clock.now()
        _save(flow, _candles(flow))
        _save(flow, _candles(flow, symbol="ETH/USD", offset=Decimal("1000")))
        task_id, (request, snapshot) = flow.run(role)
        before_request = request.model_dump_json()
        before_snapshot = json.dumps(snapshot, sort_keys=True)
        context = request.context
        assert len(request.model_dump_json().encode()) < MAX_REQUEST_BYTES
        assert len(json.dumps(context).encode()) < 80_000
        assert context["market"]["BTC/USD"]["features"]["sma_20"] == "139.5"
        assert context["market"]["BTC/USD"]["features"]["sma_50"] == "124.5"
        assert context["market"]["ETH/USD"]["features"]["sma_50"] == "1124.5"
        assert context["market"]["BTC/USD"]["features"]["range_high"] == "150"
        assert context["market"]["BTC/USD"]["features"]["range_low"] == "125"
        assert context["market"]["BTC/USD"]["features"]["midpoint"] == "137.5"
        history = context["market"]["BTC/USD"]["history"]
        assert history["source_ref"] in context["evidence_refs"]
        assert all(feature["status"] == "ready" for feature in history["features"].values())
        assert history["stale"] is False
        if role == "research":
            source = next(source for source in context["sources"] if source["source_ref"] == history["source_ref"])
            assert source["kind"] == "historical_features"
            assert source["document"] == history
        last = _candles(flow)[-1]
        later = last.model_copy(update={"close": Decimal("200"), "high": Decimal("201"), "low": Decimal("148"),
                                       "source_ref": "urn:synthetic-test:later-correction",
                                       "source_hash": "a" * 64, "available_at_utc": original_at + timedelta(hours=1)})
        future = last.model_copy(update={"open_time_utc": last.close_time_utc,
                                        "close_time_utc": last.close_time_utc + timedelta(hours=1),
                                        "available_at_utc": last.close_time_utc + timedelta(hours=1, minutes=1),
                                        "close": Decimal("300"), "high": Decimal("301"), "low": Decimal("148"),
                                        "source_ref": "urn:synthetic-test:future", "source_hash": "b" * 64})
        _save(flow, [later, future])
        flow.reopen()
        retained, pinned = flow.retained(task_id)
        assert retained.model_dump_json() == before_request
        assert json.dumps(pinned, sort_keys=True) == before_snapshot
        _, (unchanged, _) = flow.run(role)
        assert unchanged.context["market"]["BTC/USD"]["history"] == history
        assert flow.clock.now() == original_at
        flow.clock.advance(3600)
        _, (changed, _) = flow.run(role)
        assert changed.context["market"]["BTC/USD"]["features"]["sma_20"] != "139.5"
        assert flow.retained(task_id)[0].model_dump_json() == before_request
        assert flow.assembly.gateway.paid_calls_enabled is False
    finally:
        flow.db.close()


@pytest.mark.parametrize("role", ["research", "trader"])
def test_earlier_available_history_changes_next_context_without_rewriting_retained_input(tmp_path, role):
    flow = HistoryFlow(tmp_path)
    try:
        task_id, (before, _) = flow.run(role)
        assert "sma_20" not in before.context["market"]["BTC/USD"]["features"]
        _save(flow, _candles(flow))
        _, (after, _) = flow.run(role)
        assert after.context["market"]["BTC/USD"]["features"]["sma_20"] == "139.5"
        assert flow.retained(task_id)[0] == before
    finally:
        flow.db.close()


@pytest.mark.parametrize("role", ["research", "trader"])
def test_context_exposes_gap_and_stale_history_without_ready_trend_values(tmp_path, role):
    flow = HistoryFlow(tmp_path)
    try:
        _save(flow, _candles(flow, omit=48))
        _, (request, _) = flow.run(role)
        history = request.context["market"]["BTC/USD"]["history"]
        assert history["features"]["sma_20"]["status"] == "gapped_history"
        assert history["features"]["sma_20"]["missing_close_times_utc"]
        assert "sma_20" not in request.context["market"]["BTC/USD"]["features"]
        flow.clock.advance(7200)
        _, (stale, _) = flow.run(role)
        assert stale.context["market"]["BTC/USD"]["history"]["stale"] is True
        assert stale.context["market"]["BTC/USD"]["history"]["features"]["sma_20"]["status"] == "gapped_history"
    finally:
        flow.db.close()


@pytest.mark.parametrize("role", ["research", "trader"])
def test_only_active_template_requirements_select_history_features(tmp_path, role):
    flow = HistoryFlow(tmp_path, strategy_ids=["range-reversion"])
    try:
        _save(flow, _candles(flow))
        _, (request, _) = flow.run(role)
        assert set(request.context["active_strategy_templates"]) == {"range-reversion"}
        assert set(request.context["market"]["BTC/USD"]["history"]["features"]) == {
            "range_high", "range_low", "midpoint"}
        assert "sma_20" not in request.context["market"]["BTC/USD"]["features"]
    finally:
        flow.db.close()


@pytest.mark.parametrize("role", ["research", "trader"])
@pytest.mark.parametrize("quote_source,expected", [("kraken_public_ticker", "124.5"), ("synthetic", "1124.5")])
def test_public_and_explicit_synthetic_context_sources_never_mix(tmp_path, role, quote_source, expected):
    flow = HistoryFlow(tmp_path)
    try:
        _save(flow, _candles(flow))
        _save(flow, _candles(flow, source="synthetic", offset=Decimal("1000")))
        quote = flow.office.execution.latest_observation("BTC/USD", flow.office.execution.now(), "paper")
        flow.office.execution.save_observation(quote.model_copy(update={
            "observation_id": "selected-source", "source": quote_source}))
        _, (request, _) = flow.run(role)
        assert request.context["market"]["BTC/USD"]["features"]["sma_50"] == expected
    finally:
        flow.db.close()


def test_history_findings_keep_source_receipt_time_without_inventing_publication(tmp_path, monkeypatch):
    flow = HistoryFlow(tmp_path)
    try:
        _save(flow, _candles(flow))
        complete = flow.assembly.gateway.scripted.complete

        def analyze(request):
            source = next(source for source in request.context["sources"]
                          if source["kind"] == "historical_features")
            result = {"evidence_refs": [source["source_ref"]], "summary": "Inspect completed candles.",
                      "outcome": "Unproven trend hypothesis.", "findings": [{
                          "source_ref": source["source_ref"], "question": "Does the average establish an edge?",
                          "claim": "Completed candle mean is observable.", "counterevidence": "No future validation.",
                          "invalidation": "Later evidence disagrees.", "expires_after_seconds": 3600}]}
            return complete(request.model_copy(update={"context": {**request.context, "scripted_result": result}}))

        monkeypatch.setattr(flow.assembly.gateway.scripted, "complete", analyze)
        _, (request, _) = flow.run("research")
        history = request.context["market"]["BTC/USD"]["history"]
        finding = json.loads(flow.db.execute("SELECT document_json FROM findings").fetchone()[0])
        assert finding["published_at_utc"] is None
        assert datetime.fromisoformat(finding["event_at_utc"].replace("Z", "+00:00")) == datetime.fromisoformat(
            history["event_time_utc"].replace("Z", "+00:00"))
        assert datetime.fromisoformat(finding["retrieved_at_utc"].replace("Z", "+00:00")) == datetime.fromisoformat(
            history["available_at_utc"].replace("Z", "+00:00"))
        assert datetime.fromisoformat(finding["available_at_utc"].replace("Z", "+00:00")) == flow.clock.now()
        assert history["source_ref"] in finding["source_url"]
    finally:
        flow.db.close()


@pytest.mark.parametrize("role", ["research", "trader"])
def test_synthetic_history_is_not_fallback_for_public_context(tmp_path, role):
    flow = HistoryFlow(tmp_path)
    try:
        _save(flow, _candles(flow, source="synthetic", offset=Decimal("1000")))
        _, (request, _) = flow.run(role)
        history = request.context["market"]["BTC/USD"]["history"]
        assert history["source"] == "kraken_public_ohlc"
        assert history["values"] == {}
        assert all(feature["status"] == "insufficient_history" for feature in history["features"].values())
    finally:
        flow.db.close()


@pytest.mark.parametrize("role", ["research", "trader"])
def test_unsupported_history_symbol_preserves_quote_context(tmp_path, role):
    flow = HistoryFlow(tmp_path)
    try:
        authority = flow.office.execution.authority
        authority.install_policy(authority.active_policy().model_copy(update={
            "revision_id": "test-other-symbol", "allowed_symbols": ["BTC/USD", "ETH/USD", "TEST/EUR"]}),
            role="owner")
        authority.install_mandate(paper_mandate(flow.pid, revision=3, symbols=["TEST/EUR"],
                                              strategy_ids=["slow-trend-pullback"]), role="owner")
        flow.office.execution.save_observation(Observation(
            observation_id="other-quote", venue="paper", symbol="TEST/EUR", event_time_utc=flow.clock.now(),
            available_at_utc=flow.clock.now(), bid="2", ask="3", kind="quote", source="synthetic"))
        _, (request, _) = flow.run(role)
        market = request.context["market"]["TEST/EUR"]
        assert market["features"]["mid"] == "2.5"
        assert market["history"]["values"] == {}
        assert all(feature["status"] == "unsupported" for feature in market["history"]["features"].values())
    finally:
        flow.db.close()


def test_research_context_uses_one_clock_time_and_one_database_snapshot(tmp_path, monkeypatch):
    flow = HistoryFlow(tmp_path)
    writer = Database(flow.db.path)
    try:
        original_at = flow.clock.now()
        _save(flow, _candles(flow))
        eth = _candles(flow, symbol="ETH/USD", offset=Decimal("1000"))
        _save(flow, eth)
        from trade_graph.application.price_history import HistoryStore

        latest = flow.office.execution.latest_observation
        changed = False

        def write_after_first_quote(*args, **kwargs):
            nonlocal changed
            quote = latest(*args, **kwargs)
            if not changed:
                changed = True
                correction = eth[-1].model_copy(update={
                    "close": Decimal("2000"), "high": Decimal("2001"), "source_hash": "c" * 64,
                    "source_ref": "urn:synthetic-test:concurrent", "available_at_utc": original_at})
                HistoryStore(writer).save([correction])
                flow.clock.advance(10)
            return quote

        monkeypatch.setattr(flow.office.execution, "latest_observation", write_after_first_quote)
        _, (request, _) = flow.run("research")
        assert datetime.fromisoformat(request.context["as_of"].replace("Z", "+00:00")) == original_at
        assert request.context["market"]["ETH/USD"]["features"]["sma_50"] == "1124.5"
        assert request.context["market"]["BTC/USD"]["fresh"] is True
        assert all(datetime.fromisoformat(item["history"]["as_of_utc"].replace("Z", "+00:00")) == original_at
                   for item in request.context["market"].values())
        assert changed
    finally:
        writer.close()
        flow.db.close()


def test_trader_can_cite_history_from_its_retained_context(tmp_path, monkeypatch):
    flow = HistoryFlow(tmp_path)
    try:
        _save(flow, _candles(flow))
        complete = flow.assembly.gateway.scripted.complete

        def cite(request):
            history_ref = request.context["market"]["BTC/USD"]["history"]["source_ref"]
            result = {**_choice(), "evidence_ids": [history_ref]}
            return complete(request.model_copy(update={"context": {**request.context, "scripted_result": result}}))

        monkeypatch.setattr(flow.assembly.gateway.scripted, "complete", cite)
        task_id, (request, _) = flow.run("trader")
        decision = json.loads(flow.db.execute("SELECT payload_json FROM decisions WHERE task_id=?",
                                             (task_id,)).fetchone()[0])
        assert decision["evidence_refs"] == [request.context["market"]["BTC/USD"]["history"]["source_ref"]]
        assert flow.db.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0
    finally:
        flow.db.close()


def test_trader_denies_invented_history_reference(tmp_path):
    flow = HistoryFlow(tmp_path)
    try:
        _save(flow, _candles(flow))
        flow.assembly.gateway.scripted.outputs["trader"] = {
            **_choice(), "evidence_ids": ["hourly-features:" + "f" * 64]}
        task_id = flow.office.scheduler.add_task(role="trader", objective="Inspect completed history.",
                                                portfolio_id=flow.pid, max_attempts=1, allocated_spend=Decimal("1"))
        assert flow.worker.run_available({"trader": flow.assembly.handlers["trader"]}) == 1
        row = flow.db.execute("SELECT status, output_json FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        assert row["status"] == "FAILED"
        assert "outside its persisted snapshot" in row["output_json"]
        assert flow.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
        assert flow.db.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0
    finally:
        flow.db.close()


def test_feature_citation_keeps_existing_research_freshness_checks(tmp_path, monkeypatch):
    from trade_graph.application.research import ResearchStore

    flow = HistoryFlow(tmp_path)
    try:
        _save(flow, _candles(flow))
        finding = ResearchStore(flow.db, flow.clock).ingest_page(
            flow.pid, url="https://example.com/synthetic-test", html="<p>Retained synthetic finding.</p>",
            resolver=lambda _host: ["1.1.1.1"], publisher="synthetic-test", question="One source?",
            instruments=["BTC/USD"], published_at=None, expires_at=flow.clock.now() + timedelta(seconds=5),
            counterevidence="No economic validation.")
        complete = flow.assembly.gateway.scripted.complete

        def cite_after_expiry(request):
            history_ref = request.context["market"]["BTC/USD"]["history"]["source_ref"]
            assert finding.record_id in request.context["evidence_refs"]
            flow.clock.advance(10)
            result = {**_choice(), "evidence_ids": [history_ref, finding.record_id]}
            return complete(request.model_copy(update={"context": {**request.context, "scripted_result": result}}))

        monkeypatch.setattr(flow.assembly.gateway.scripted, "complete", cite_after_expiry)
        task_id = flow.office.scheduler.add_task(role="trader", objective="Inspect completed history.",
                                                portfolio_id=flow.pid, max_attempts=1, allocated_spend=Decimal("1"))
        assert flow.worker.run_available({"trader": flow.assembly.handlers["trader"]}) == 1
        row = flow.db.execute("SELECT status, output_json FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        assert row["status"] == "FAILED"
        assert "research finding is stale or untrusted" in row["output_json"]
        assert flow.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    finally:
        flow.db.close()
