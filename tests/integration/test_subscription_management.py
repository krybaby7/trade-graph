"""Synthetic public feed with real paper assembly, protection and OS ownership."""

import hashlib
import json
import signal
from types import SimpleNamespace

import pytest
from tests.integration.test_paper_service import Feed
from tests.integration.test_subscription_departments import flow
from tests.security.test_subscription_network import profile

from trade_graph.contracts.models import Observation
from trade_graph.domain.errors import StaleState


@pytest.fixture
def management_flow(tmp_path, monkeypatch):
    from trade_graph.application import subscription_management as management
    from trade_graph.application import subscription_profile
    setup, protected, cli = flow(tmp_path)
    raw = b'{"public_data_enabled":true,"public_history_enabled":false,"tick_interval_seconds":0.01}'
    manifest = protected.financial.manifest
    pinned = profile(deployment_id=manifest.deployment_id, manifest_sha256=manifest.sha256,
        protected_package_sha256=manifest.protected_package_sha256,
        paper_config_sha256=hashlib.sha256(raw).hexdigest())
    calls, feeds = [], []
    monkeypatch.setattr(management, "assert_boot_environment", lambda: manifest)
    monkeypatch.setattr(management, "load_subscription_network_profile", lambda _owner: pinned)
    monkeypatch.setattr(management, "load_subscription_seccomp", lambda *_a: {"defaultAction": "SCMP_ACT_ERRNO"})
    monkeypatch.setattr(management, "read_owner_file", lambda _owner, _name, _bound: raw)
    def no_auth(*_a, **_kwargs):
        raise AssertionError("management must never inspect subscription authentication")
    monkeypatch.setattr(subscription_profile, "load_subscription_profile", no_auth)
    assemble = management.assemble_paper_runtime
    def assemble_without_credentials(path, **kwargs):
        calls.append(kwargs)
        assert kwargs.get("protected_owner") is None
        assert kwargs["config"].models is None and not kwargs["config"].price_cards
        assert kwargs["config"].public_data_enabled is False
        return assemble(path, clock=setup.clock, **kwargs)
    monkeypatch.setattr(management, "assemble_paper_runtime", assemble_without_credentials)
    def feed(execution, portfolio_ids, symbols, *, interval_seconds, transport):
        assert transport.proxy == pinned.market_proxy_url and transport.trust_env is False
        assert portfolio_ids == [setup.pid] and symbols == ["BTC/USD", "ETH/USD"]
        result = Feed([Observation(observation_id="synthetic-public-management-quote", venue="paper",
            symbol="BTC/USD", event_time_utc=setup.clock.now(), available_at_utc=setup.clock.now(),
            bid="99", ask="100", kind="quote", source="kraken_public_rest:paper_reference:receipt_time")])
        feeds.append(result)
        return result
    monkeypatch.setattr(management, "PublicPaperFeed", feed)
    yield SimpleNamespace(module=management, setup=setup, cli=cli, manifest=manifest, pinned=pinned,
        raw=raw, owner=tmp_path / "owner", calls=calls, feeds=feeds)
    setup.db.close()


def run(h, ticks=1):
    return h.module.run_subscription_management(h.setup.db.path, h.owner, maximum_ticks=ticks)


def test_management_runs_without_auth_or_model_tasks_and_closes_cleanly(management_flow, monkeypatch):
    h = management_flow
    queued = {role: h.setup.add(role) for role in ("research", "trader", "leader", "learning", "engineer")}
    before_tasks = h.setup.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    before_budget = [tuple(row) for row in h.setup.db.execute("SELECT * FROM deployment_budget")]
    signals = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    result = run(h, 2)
    assert result["status"] == "management_stopped" and result["ticks"] == 2
    assert result["scheduled"] == result["completed"] == result["inference_attempts"] == 0
    assert result["observations"] == 1 and result["live_authorization"] is False
    assert result["position_management"] == "MANAGE_ONLY" and h.feeds[0].closed
    assert h.setup.db.execute("SELECT COUNT(*) FROM process_leases").fetchone()[0] == 0
    assert h.setup.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == before_tasks
    assert all(h.setup.row(task_id)["status"] == "QUEUED" for task_id in queued.values())
    assert h.setup.db.execute("SELECT COUNT(*) FROM subscription_invocations").fetchone()[0] == 0
    assert h.setup.db.execute("SELECT COUNT(*) FROM model_invocations").fetchone()[0] == 0
    assert before_budget == [tuple(row) for row in h.setup.db.execute("SELECT * FROM deployment_budget")]
    assert h.cli.requests == []
    assert {number: signal.getsignal(number) for number in signals} == signals


@pytest.mark.parametrize("status", ["ok", "partial"])
def test_management_refreshes_completed_history_through_pinned_market_route_without_ai(
        management_flow, monkeypatch, status):
    from trade_graph.application import subscription_operation
    from trade_graph.domain.clock import utc_iso

    h = management_flow
    raw = json.dumps({**json.loads(h.raw), "public_history_enabled": True, "public_history_hours": 168}).encode()
    pinned = h.pinned.model_copy(update={"paper_config_sha256": hashlib.sha256(raw).hexdigest()})
    monkeypatch.setattr(h.module, "read_owner_file", lambda *_a: raw)
    monkeypatch.setattr(h.module, "load_subscription_network_profile", lambda _owner: pinned)
    feed_factory, order = h.module.PublicPaperFeed, []

    def public_feed(*args, **kwargs):
        feed = feed_factory(*args, **kwargs)
        feed.symbols, feed.transport = ["BTC/USD", "ETH/USD"], kwargs["transport"]
        poll = feed.poll
        def quote():
            order.append("quote")
            return poll()
        feed.poll = quote
        return feed

    monkeypatch.setattr(h.module, "PublicPaperFeed", public_feed)
    requested_at = utc_iso(h.setup.clock.now())
    def history(database, clock, transport, **kwargs):
        assert transport.proxy == pinned.market_proxy_url and transport.trust_env is False
        assert kwargs == {"hours": 168, "symbols": ("BTC/USD", "ETH/USD")}
        assert h.cli.requests == [] and h.setup.office.execution.profile(h.setup.pid) == "MANAGE_ONLY"
        order.append("history")
        clock.advance(2)
        return {"status": status, "source": "kraken_public_ohlc", "symbols": {
            symbol: {"status": "ok", "fetched": 168, "available_at_utc": utc_iso(clock.now()),
                     "missing_hours": 0 if status == "ok" else 1}
            for symbol in ("BTC/USD", "ETH/USD")}}

    monkeypatch.setattr(subscription_operation, "collect_public_hourly_history", history)
    queued = h.setup.add("research")
    before_budget = [tuple(row) for row in h.setup.db.execute("SELECT * FROM deployment_budget")]
    result = run(h, 2)
    report = result["public_history"]
    assert order[:2] == ["history", "quote"] and order.count("history") == 1
    assert report["refresh_requested_at_utc"] == requested_at
    assert report["refresh_finished_at_utc"] == utc_iso(h.setup.clock.now())
    assert report["symbols"]["BTC/USD"]["available_at_utc"] == report["refresh_finished_at_utc"]
    retained = h.setup.db.execute(
        "SELECT payload_json FROM activity_events WHERE kind='public_history_refresh'").fetchall()
    assert len(retained) == 1 and json.loads(retained[0][0]) == report
    assert h.setup.row(queued)["status"] == "QUEUED" and h.cli.requests == []
    assert result["inference_attempts"] == result["scheduled"] == result["completed"] == 0
    assert result["position_management"] == "MANAGE_ONLY" and h.feeds[0].closed
    assert before_budget == [tuple(row) for row in h.setup.db.execute("SELECT * FROM deployment_budget")]


@pytest.mark.parametrize("pause", ["PAUSE_DECISIONS", "NO_NEW_EXPOSURE", "MANAGE_ONLY",
                                   "CANCEL_ALL", "FLATTEN", "STOPPED"])
def test_management_preserves_every_existing_nonrunning_owner_pause(management_flow, pause):
    h = management_flow
    h.setup.office.execution.set_pause(h.setup.pid, pause, "owner", "Synthetic retained owner control.")
    run(h)
    actual = h.setup.office.execution.pause(h.setup.pid)
    assert (actual["profile"], actual["originator"], actual["reason"]) == (
        pause, "owner", "Synthetic retained owner control.")


def test_management_refuses_competing_service_without_changing_pause(management_flow):
    from trade_graph.application.paper_service import PaperService
    h = management_flow
    service = PaperService(h.setup.db, h.setup.office.execution, clock=h.setup.clock, schedule_intervals={})
    service._acquire()
    before = h.setup.office.execution.pause(h.setup.pid)
    try:
        with pytest.raises(StaleState):
            run(h)
        assert h.setup.office.execution.pause(h.setup.pid) == before
    finally:
        service._release()


def test_profile_mismatch_refuses_before_opening_database(management_flow, monkeypatch):
    h = management_flow
    monkeypatch.setattr(h.module, "load_subscription_network_profile", lambda _owner:
        h.pinned.model_copy(update={"paper_config_sha256": "f" * 64}))
    with pytest.raises(PermissionError, match="configuration/profile"):
        run(h)
    assert h.calls == []


@pytest.mark.parametrize("ticks", [0, -1, True, 1.5])
def test_invalid_tick_limit_never_probes_or_opens_runtime(monkeypatch, tmp_path, ticks):
    from trade_graph.application import subscription_management as management
    calls = []
    monkeypatch.setattr(management, "assert_boot_environment", lambda: calls.append("boot"))
    with pytest.raises(ValueError, match="maximum_ticks"):
        management.run_subscription_management(tmp_path / "absent.sqlite", tmp_path / "owner", maximum_ticks=ticks)
    assert calls == []


def test_market_outage_keeps_management_and_reports_degradation(management_flow, monkeypatch):
    h = management_flow
    monkeypatch.setattr(h.module, "PublicPaperFeed", lambda *_a, **_k: Feed(OSError("synthetic public outage")))
    result = run(h)
    assert result["status"] == "management_degraded" and result["failures"]
    assert h.setup.office.execution.profile(h.setup.pid) == "MANAGE_ONLY"
    assert h.setup.db.execute("SELECT COUNT(*) FROM process_leases").fetchone()[0] == 0
    assert h.cli.requests == []


def test_management_latches_pause_before_first_reconcile_or_dispatch(management_flow, monkeypatch):
    from trade_graph.application.execution import Execution
    h = management_flow
    observed = []
    reconcile, dispatch = Execution.reconcile, Execution.dispatch
    async def managed_reconcile(execution):
        observed.append(execution.profile(h.setup.pid))
        return await reconcile(execution)
    async def managed_dispatch(execution):
        observed.append(execution.profile(h.setup.pid))
        return await dispatch(execution)
    monkeypatch.setattr(Execution, "reconcile", managed_reconcile)
    monkeypatch.setattr(Execution, "dispatch", managed_dispatch)
    run(h)
    assert observed and set(observed) == {"MANAGE_ONLY"}


def test_management_keeps_existing_paper_stop_protection_active(management_flow, monkeypatch):
    import asyncio
    from decimal import Decimal

    from tests.integration.test_execution import _decision, _quote, _rules
    h = management_flow
    execution = h.setup.office.execution
    ledger = execution.ledger
    execution.register_instrument(_rules())
    ledger.deposit(h.setup.pid, "USD", Decimal("10000"), "synthetic-protection-capital")
    ledger.observe_fx(base="USD", quote="EUR", rate=Decimal("0.9"), source="synthetic", kind="reference", stale=False)
    execution.save_observation(_quote(h.setup.clock, "99", "100", observation_id="synthetic-entry"))
    execution.authorize(h.setup.pid, _decision(h.setup.clock, h.setup.pid,
        strategy_id="slow-trend-pullback", policy_revision=execution.authority.active_policy().revision_id))
    asyncio.run(execution.dispatch())
    h.setup.clock.advance(1)
    execution.on_observation(_quote(h.setup.clock, "99", "100", observation_id="synthetic-fill"))
    ledger.observe_mark(h.setup.pid, "BTC", Decimal("100"), "USD", source="synthetic")
    protection = execution.place_protection(
        h.setup.pid, "BTC/USD", Decimal("0.01"), Decimal("90"), "synthetic-snapshot")
    asyncio.run(execution.dispatch())
    h.setup.clock.advance(1)
    monkeypatch.setattr(h.module, "PublicPaperFeed", lambda *_a, **_k:
        Feed([_quote(h.setup.clock, "80", "81", observation_id="synthetic-protection-trigger")]))
    result = run(h)
    assert result["position_management"] == "MANAGE_ONLY" and result["inference_attempts"] == 0
    assert execution.intent_state(protection) == "FILLED" and execution.owned_quantity(h.setup.pid, "BTC") == 0
    assert h.cli.requests == []
