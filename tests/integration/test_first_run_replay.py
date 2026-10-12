"""Replay the first paper run (7-10 Oct 2026) end to end with synthetic data: the D114 fixes, together.

One Saturday morning through the deployed subscription path: ``trade-graph init`` (USD 10,000 paper
capital reporting in EUR, default mandate), the assembled paper runtime, the protected subscription
binding and the normal service with its hourly-history refresh, public paper feed and hourly Trader
schedule. Kraken and Frankfurter answer from a scripted transport and the Trader from a synthetic CLI:
no exchange, provider or paid call is made.

In the first run the Trader saw no completed close and an empty feature map, Friday's EUR reference
rate blocked every buy as a 'provisional valuation', and a refused order failed its task and paused
the system. Here, at 09:00 Kraken commits the hour three minutes late and the decision waits for it;
a small buy is authorized despite the stale EUR rate; an over-cap buy at 10:00 is a visible refusal,
not a pause; at 11:00 the candle stays missing past the bounded wait, the Trader decides on stale but
present features and sees its earlier refusal.
"""

import asyncio
import hashlib
import json
from datetime import timedelta
from decimal import Decimal

from tests.integration.test_hq_projection import OWNER, text
from tests.integration.test_native_exposure_stale_reporting_fx import CLOCKS, FRIDAY_REFERENCE, FX_URL
from tests.integration.test_protected_department_graph import GRAPH
from tests.integration.test_subscription_departments import SyntheticCli
from tests.integration.test_trader_latest_close import CLOSES

from trade_graph.adapters.market.paper_feed import PublicPaperFeed
from trade_graph.adapters.models.subscription import SubscriptionAdapter, SubscriptionConfig, SubscriptionReadiness
from trade_graph.api import hq, resources
from trade_graph.application import deployment_runtime, service_controller
from trade_graph.application.deployment_runtime import ProtectedDeploymentBinding
from trade_graph.application.subscription_operation import _HistoryRefreshingFeed, _NormalSubscriptionService
from trade_graph.application.subscription_profile import SubscriptionAdmission
from trade_graph.application.subscription_runtime import SubscriptionRuntimeConfig
from trade_graph.cli import main
from trade_graph.domain.clock import FrozenClock, parse_utc, utc_iso
from trade_graph.kernel.runtime_manifest import ProtectedRuntimeManifest, protected_package_sha256
from trade_graph.paper_runtime import PaperRuntimeConfig, assemble_paper_runtime

SATURDAY = CLOCKS["weekend"]  # 2026-10-10 09:00 UTC: Frankfurter still serves Friday's ECB reference.
HOUR = timedelta(hours=1)
PAIRS = {"BTC/USD": ("XBTUSD", "XXBTZUSD", Decimal(1)), "ETH/USD": ("ETHUSD", "XETHZUSD", Decimal(20))}
ASSET_PAIRS = json.dumps({"error": [], "result": {
    "XXBTZUSD": {"altname": "XBTUSD", "wsname": "XBT/USD", "base": "XXBT", "quote": "ZUSD", "pair_decimals": 1,
                 "lot_decimals": 8, "ordermin": "0.0001", "costmin": "0.5"},
    "XETHZUSD": {"altname": "ETHUSD", "wsname": "ETH/USD", "base": "XETH", "quote": "ZUSD", "pair_decimals": 2,
                 "lot_decimals": 8, "ordermin": "0.002", "costmin": "0.5"}}})
SINGLE_ASSET = "single asset exposure"
FEATURES = ("sma_20", "sma_50", "pullback_from_high")  # the installed slow-trend-pullback template


def _utc(clock_time: str) -> str:
    return f"2026-10-10T{clock_time}.000000Z"


def _choice(action, quantity=None):
    return {"action": action, "symbol": "BTC/USD", "quantity": quantity, "strategy_id": "slow-trend-pullback",
            "rationale": f"Synthetic {action}: the close sits between the 50- and 20-hour averages.",
            "invalidation": "The close falls below the 50-hour average.", "experiment": False,
            "evidence_ids": [], "no_action_reason": None if quantity else "Waiting for the next completed hour."}


# 0.01 BTC is about 633 USD; another 0.1 BTC would put about 6,960 USD of 10,000 into BTC (cap 50%).
ENTER, OVER_CAP, HOLD = _choice("enter", "0.01"), _choice("enter", "0.1"), _choice("hold")


def _close(symbol, close_time):
    """CLOSES end at Saturday 09:00 (sma_50 62348 < close 63300 < sma_20 63695); the climb runs back before."""
    index = 49 + (close_time - SATURDAY) // HOUR
    if index < 0:
        close = Decimal(60000 + 100 * index)
    else:
        close = CLOSES[index] if index < len(CLOSES) else Decimal(63300 + 50 * (index - 49))
    return close / PAIRS[symbol][2]


class PublicMarket:
    """Kraken public REST and Frankfurter, scripted. While ``lagging`` Kraken has not committed the last hour."""

    def __init__(self, clock):
        self.clock, self.lagging, self.urls = clock, False, []

    def get_text(self, url):
        self.urls.append(url)
        if url == FX_URL:
            return FRIDAY_REFERENCE
        if "/AssetPairs?" in url:
            return ASSET_PAIRS
        symbol = next(symbol for symbol, (pair, _key, _scale) in PAIRS.items() if f"pair={pair}" in url)
        _pair, key, scale = PAIRS[symbol]
        if "/Ticker?" in url:
            mid = Decimal(63300) / scale
            return json.dumps({"error": [], "result": {key: {
                "a": [str(mid + 10 / scale), "1", "1.5"], "b": [str(mid - 10 / scale), "1", "2.5"],
                "c": [str(mid), "0.01"], "v": ["40", "90"]}}})
        assert "/OHLC?" in url, url
        hour = self.clock.now().replace(minute=0, second=0, microsecond=0)
        committed = hour - HOUR if self.lagging else hour
        rows = []
        # Kraken always appends the still-open hour as its last row; the adapter discards it.
        for close_time in [committed - back * HOUR for back in range(200, -1, -1)] + [committed + HOUR]:
            close, opened = _close(symbol, close_time), _close(symbol, close_time - HOUR)
            rows.append([int((close_time - HOUR).timestamp()), str(opened), str(max(opened, close) + 50 / scale),
                         str(min(opened, close) - 50 / scale), str(close), str(close), "12.5", 9])
        return json.dumps({"error": [], "result": {key: rows, "last": rows[-1][0]}})


def _owned_subscription(monkeypatch) -> SyntheticCli:
    """The owner-pinned subscription admission of test_owned_subscription_service, without a provider login."""
    manifest = ProtectedRuntimeManifest(
        schema_version=1, protected_package_sha256=protected_package_sha256(), deployment_id="deployment",
        approved_source_sha256=(hashlib.sha256(GRAPH.encode()).hexdigest(),),
        operations=("submit_decision", "invoke_model", "apply_role_result"))
    monkeypatch.setattr(deployment_runtime, "_owner_bundle",
                        lambda _directory: (manifest, GRAPH, b"synthetic-owner-only-key-32-bytes-41"))
    config = SubscriptionRuntimeConfig(subscription=SubscriptionConfig(
        provider="claude_subscription", model="claude-sonnet-5-5", enabled=True))
    readiness = SubscriptionReadiness("claude_subscription", "2.1.285", True, (), {},
                                      "subscription", "linux-bubblewrap")
    cli = SyntheticCli()
    admission = SubscriptionAdmission(config, SubscriptionAdapter(config.subscription, readiness, cli), {
        **readiness.public_status(), "selected_provider": "claude_subscription", "inference_attempts": 0}, "1" * 64)
    monkeypatch.setattr("trade_graph.application.subscription_profile.load_subscription_profile",
                        lambda _directory: admission)
    monkeypatch.setattr("trade_graph.application.subscription_profile.subscription_profile_unchanged",
                        lambda _directory, _digest: True)
    return cli


def _deployed(tmp_path, monkeypatch):
    """Built as run_subscription_operation builds it; only transports and the owner bundle are synthetic."""
    clock = FrozenClock(SATURDAY - timedelta(minutes=10))
    monkeypatch.setattr("trade_graph.domain.clock.SystemClock", lambda: clock)
    path = tmp_path / "paper.sqlite"
    assert main(["init", "--database", str(path)]) == 0
    config = PaperRuntimeConfig(public_data_enabled=True, schedule_intervals={"trader": 3600},
                                tick_interval_seconds=0.01)
    runtime = assemble_paper_runtime(path, config=config, clock=clock, api_keys={})
    market = PublicMarket(clock)
    runtime.public_feed = PublicPaperFeed(runtime.execution, [runtime.portfolio_id], config.paper_symbols,
                                          interval_seconds=config.public_poll_interval_seconds, transport=market)
    cli = _owned_subscription(monkeypatch)
    binding = ProtectedDeploymentBinding(runtime, tmp_path / "owner", api_keys={})
    runtime.prepare_runtime, runtime.runtime_ready = binding.prepare, binding.ready
    feed = _HistoryRefreshingFeed(runtime.public_feed, runtime, hours=config.public_history_hours,
                                  interval_seconds=config.public_history_interval_seconds)
    service = _NormalSubscriptionService(
        runtime.database, runtime.execution, clock=clock, portfolio_ids=[runtime.portfolio_id], handlers={},
        artifact_runtime=runtime.artifact_runtime, public_feed=feed, secretary=runtime.secretary,
        schedule_intervals=config.schedule_intervals, automatic_schedule=config.automatic_schedule,
        optimisation_manual_only=config.optimisation_manual_only, tick_interval_seconds=config.tick_interval_seconds,
        prepare_runtime=binding.prepare, runtime_ready=binding.ready,
        subscription_provider=runtime.subscription_provider)
    return clock, runtime, market, cli, service


def _seen(request) -> dict:
    """What one Trader turn was shown about BTC/USD, read leniently so an older context still compares."""
    context = request.context
    history = context.get("market", {}).get("BTC/USD", {}).get("history", {})
    latest, candle = history.get("latest_close") or {}, history.get("expected_candle") or {}
    refusals = context.get("portfolio", {}).get("recent_order_refusals", [])
    return {
        "as_of": context.get("as_of"),
        "latest_close": (latest.get("close_time_utc"), latest.get("close"), latest.get("available_at_utc")),
        "expected_candle": (candle.get("status"), candle.get("waited_seconds")),
        "features": {name: (feature.get("status"), name in history.get("values", {}))
                     for name, feature in history.get("features", {}).items()},
        "refusals_shown": [item.get("reason") for item in refusals],
    }


def _outcome(row) -> tuple:
    output = json.loads(row["output_json"] or "{}")
    return (row["status"], row["created_at"], output.get("action"), output.get("intent_id") is not None,
            (output.get("refusal") or {}).get("reason"))


def _trader_tasks(database):
    return database.execute("SELECT * FROM tasks WHERE role = 'trader' ORDER BY created_at, rowid").fetchall()


def test_first_paper_run_replays_without_its_failures(tmp_path, monkeypatch):
    clock, runtime, market, cli, service = _deployed(tmp_path, monkeypatch)
    db, pid = runtime.database, runtime.portfolio_id
    held = {}

    def trader_requests():
        return [request for request in cli.requests if request.role == "trader"]

    def hold_state():
        row = _trader_tasks(db)[-1]
        return row["status"], row["attempts_used"], len(trader_requests()), json.loads(row["output_json"] or "{}")

    async def tick(clock_time, **changes):
        clock.advance((parse_utc(_utc(clock_time)) - clock.now()).total_seconds())
        market.lagging = changes.get("lagging", market.lagging)
        cli.outputs["trader"] = changes.get("reply", cli.outputs.get("trader"))
        await service.tick(wait_roles=True, wait_feed=True)

    async def session():
        await service.start()
        try:
            await tick("09:00:05", lagging=True, reply=ENTER)  # the hourly review is due; 09:00 not committed
            held["09:00:05"] = hold_state()
            await tick("09:03:00", lagging=False)  # Kraken commits the 09:00 hour
            await tick("09:03:30")  # the next quote fills the practice order
            await tick("10:00:20", reply=OVER_CAP)  # a late tick; the 10:00 hour is already committed
            await tick("11:00:05", lagging=True, reply=HOLD)  # the 11:00 hour stays uncommitted
            held["11:00:05"] = hold_state()
            await tick("11:05:00")
        finally:
            await service.stop()

    try:
        asyncio.run(session())
        turns, tasks = trader_requests(), _trader_tasks(db)
        intents = db.execute("SELECT * FROM order_intents ORDER BY created_at").fetchall()
        events = {kind: [json.loads(row[0]) for row in db.execute(
            "SELECT payload_json FROM activity_events WHERE kind = ? ORDER BY rowid", (kind,))]
            for kind in ("order_refused", "role_failed")}
        ready = {name: ("ready", True) for name in FEATURES}  # (status, value supplied)
        stale = {name: ("stale", True) for name in FEATURES}

        # One comparison first, so a regression shows every first-run symptom at once.
        assert {
            "mandate strategy_ids": runtime.execution.authority.active_mandate(pid).strategy_ids,
            "09:00:05 review before its candle": held["09:00:05"][:3],
            "trader turns": [_seen(request) for request in turns],
            "trader tasks": [_outcome(row) for row in tasks],
            "profile": runtime.execution.profile(pid),
            "order intents": [row["state"] for row in intents],
            "events": {kind: len(rows) for kind, rows in events.items()},
        } == {
            "mandate strategy_ids": ["slow-trend-pullback"],
            "09:00:05 review before its candle": ("QUEUED", 0, 0),
            "trader turns": [
                {"as_of": _utc("09:03:00"), "latest_close": (_utc("09:00:00"), "63300", _utc("09:03:00")),
                 "expected_candle": ("present", 175), "features": ready, "refusals_shown": []},
                {"as_of": _utc("10:00:20"), "latest_close": (_utc("10:00:00"), "63350", _utc("10:00:20")),
                 "expected_candle": ("present", 0), "features": ready, "refusals_shown": []},
                {"as_of": _utc("11:05:00"), "latest_close": (_utc("10:00:00"), "63350", _utc("10:00:20")),
                 "expected_candle": ("missing_after_wait", 295), "features": stale, "refusals_shown": [SINGLE_ASSET]},
            ],
            "trader tasks": [
                ("SUCCEEDED", _utc("09:00:05"), "enter", True, None),
                ("SUCCEEDED", _utc("10:00:20"), "enter", False, SINGLE_ASSET),
                ("SUCCEEDED", _utc("11:00:05"), "hold", False, None),
            ],
            "profile": "RUNNING",
            "order intents": ["FILLED"],
            "events": {"order_refused": 1, "role_failed": 0},
        }

        # (6) `trade-graph init` wrote the cleaned default mandate; the Trader sees only installed templates.
        mandate = runtime.execution.authority.active_mandate(pid)
        assert mandate.revision == 1 and mandate.discretionary_experiment is True
        assert all(list(request.context["active_strategy_templates"]) == ["slow-trend-pullback"] for request in turns)
        assert db.execute("SELECT COUNT(*) FROM mandates WHERE portfolio_id = ?", (pid,)).fetchone()[0] == 1

        # (4) Friday's reference rate is stored as production stores it: dated Friday, flagged stale. EUR views
        # stay flagged while the USD buy was measured in native USD.
        rates = db.execute("SELECT * FROM fx_rates ORDER BY retrieved_at").fetchall()
        assert rates and rates[0]["retrieved_at"] == _utc("09:00:05")
        assert {(row["base"], row["quote"], row["rate"], row["valid_as_of"], row["source"], row["stale"])
                for row in rates} == {("USD", "EUR", "0.86", "2026-10-09T00:00:00.000000Z",
                                       "frankfurter:ECB:2026-10-09", 1)}
        view = runtime.ledger.equity(pid)  # USD 10,000 opening capital at Friday's 0.86
        assert (view.reporting_currency, view.baseline, view.provisional, view.stale) == (
            "EUR", Decimal("8600"), True, True)
        assert db.execute("SELECT COUNT(*) FROM activity_events WHERE payload_json LIKE '%provisional valuation%'"
                          ).fetchone()[0] == 0
        assert all("provisional valuation" not in (row["output_json"] or "") for row in tasks)

        # (3) The 09:00:05 review waited, before any attempt or snapshot, for the hour Kraken committed at 09:03.
        assert held["09:00:05"][3]["_candle_wait"] == {
            "expected_close_time_utc": _utc("09:00:00"), "missing_symbols": ["BTC/USD", "ETH/USD"],
            "waiting_since_utc": _utc("09:00:05"), "wait_deadline_utc": _utc("09:05:00")}
        first = turns[0].context["market"]["BTC/USD"]
        assert first["history"]["expected_candle"]["note"] == (
            "The hourly candle that closed at 09:00 UTC is stored; this decision waited 175 seconds for it.")

        # (1) The latest completed close and the 24 recent candles, only those received by as_of.
        recent = first["history"]["recent_candles"]
        assert [row["close"] for row in recent] == [str(close) for close in CLOSES[-24:]]
        assert recent[-1]["close_time_utc"] == _utc("09:00:00")
        assert all(row["available_at_utc"] <= turns[0].context["as_of"] for row in recent)
        assert first["history"]["recent_missing_close_times_utc"] == []

        # (2) Ready features from their own windows: the slow-trend-pullback shape is visible to the Trader.
        values = first["history"]["values"]
        assert (values["sma_20"], values["sma_50"]) == ("63695", "62348")
        assert first["features"]["sma_20"] == values["sma_20"] and Decimal(values["pullback_from_high"]) > 0
        assert Decimal(values["sma_50"]) < Decimal(first["history"]["latest_close"]["close"]) < Decimal(
            values["sma_20"])

        # (4) The small buy was authorized and filled at the scripted ask despite the stale EUR rate.
        entry = json.loads(tasks[0]["output_json"])
        assert entry["intent_id"] == intents[0]["intent_id"] and "refusal" not in entry
        assert runtime.execution.owned_quantity(pid, "BTC") == Decimal("0.01")

        # (5) The over-cap buy is a saved, visible refusal: nothing sent or held, no failed task, no pause.
        refused = json.loads(tasks[1]["output_json"])
        refusal = refused["refusal"]
        assert events["order_refused"] == [refusal]
        decision = db.execute("SELECT * FROM decisions WHERE decision_id = ?", (refused["decision_id"],)).fetchone()
        assert (decision["action"], decision["task_id"], refused["intent_id"]) == ("enter", tasks[1]["task_id"], None)
        assert {key: value for key, value in refusal.items() if key not in {"decision_id", "snapshot_id"}} == {
            "task_id": tasks[1]["task_id"], "action": "enter", "side": "buy", "symbol": "BTC/USD",
            "quantity": "0.1", "order_type": "market", "limit_price": None, "strategy_id": "slow-trend-pullback",
            "code": "authority_denied", "error": "AuthorityDenied", "reason": SINGLE_ASSET,
            "refused_at": _utc("10:00:20")}
        assert (refusal["decision_id"], refusal["snapshot_id"]) == (decision["decision_id"], decision["snapshot_id"])
        assert db.execute("SELECT COUNT(*) FROM broker_orders").fetchone()[0] == 1  # only the 09:03 buy was sent
        assert {row[0] for row in db.execute("SELECT intent_id FROM position_reservations")} <= {
            intents[0]["intent_id"]}
        assert runtime.execution.pause(pid) is None
        report = json.loads(db.execute("SELECT document_json FROM secretary_reports WHERE report_id = ?",
                                       (refused["report_id"],)).fetchone()[0])
        assert report["kind"] == "decision" and report["summary"].endswith(f"\nOrder not placed: {SINGLE_ASSET}")

        # (3) At 11:00 the hour stays missing: the review waits to its bounded deadline, then says so plainly.
        assert held["11:00:05"][:3] == ("QUEUED", 0, 2)
        third = turns[2].context
        history = third["market"]["BTC/USD"]["history"]
        assert history["expected_candle"]["note"] == (
            "The hourly candle that closed at 11:00 UTC was still not stored after waiting 295 seconds; "
            "the latest stored hourly candle closed at 10:00 UTC.")
        assert history["recent_missing_close_times_utc"] == [_utc("11:00:00")]
        # (2) Stale, not hidden: the same windows through 10:00 the 10:00:20 turn saw as ready.
        assert history["values"] == turns[1].context["market"]["BTC/USD"]["history"]["values"]

        # (5) The next turn shows the refusal with its reason.
        assert third["portfolio"]["recent_order_refusals"] == [{key: refused["refusal"][key] for key in (
            "decision_id", "refused_at", "action", "symbol", "quantity", "order_type", "limit_price", "code",
            "reason")}]

        # (3) The schedule kept its 09:00:05 phase through the late 10:00:20 tick.
        schedule = db.execute("SELECT * FROM schedules WHERE portfolio_id = ? AND name = 'trader-review'",
                              (pid,)).fetchone()
        assert schedule["next_due_at"] == _utc("12:00:05")
        assert [row["dedup_key"] for row in tasks] == [
            f"schedule:{schedule['schedule_id']}:{_utc(due)}" for due in ("09:00:05", "10:00:05", "11:00:05")]

        # (5) HQ tells the owner in plain words, and raises no safety stop.
        monkeypatch.setattr(service_controller, "process_identity", lambda _pid: None)
        monkeypatch.setattr(resources, "observation",
                            lambda runtime: {"observed_at": utc_iso(runtime.clock.now()), "warnings": []})
        data = hq.snapshot(runtime, OWNER)
        story = {item["id"]: item for item in data["story"]["items"]}
        told = story[f"decision:{refused['decision_id']}"]
        assert text(told["consequence"]) == ("The software didn't place this practice order: it would have put more "
                                             "than the allowed share of the money into one coin. No money moved.")
        assert told["problem"] is False
        assert "safety_stop" not in [alert["id"].split(":", 1)[0] for alert in data["attention"]]
        assert not [item_id for item_id in story if item_id.startswith("task:")]

        # Synthetic evidence only: every request went to the scripted public transport; no paid-call record.
        assert {url.split("?")[0] for url in market.urls} == {
            "https://api.kraken.com/0/public/AssetPairs", "https://api.kraken.com/0/public/Ticker",
            "https://api.kraken.com/0/public/OHLC", FX_URL}
        assert db.execute("SELECT COUNT(*) FROM model_invocations").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 0
    finally:
        runtime.database.close()
