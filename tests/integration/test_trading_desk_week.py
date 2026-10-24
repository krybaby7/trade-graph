"""The Trading desk's scripted week: the W12-A acceptance test (C12; D280, D295-D299).

Seven trading days of the whole loop, scripted and synthetic:

- **Market types**: synthetic hourly candles (``synthetic_fixture``) for a range-bound SOL/USD and a steadily falling
  BTC/USD, labelled each day by W11-A's protected labeller (the live function), so SOL is ``sideways`` and BTC
  ``falling``.
- **News**: the Leader's news round every day on a scripted Perplexity (documented shapes, made-up key), the first one
  with a strategy idea addressed to the desk; Jev verdicts on scripted answers about BTC in shadow mode.
- **The desk**: the real Trader handler (its real context, instruction, reply schema and 128 KiB request bound) on a
  scripted model whose answers are computed from what the request shows it: a stance per coin, its playbook, lab
  requests for the Leader's idea, and day plans.
- **The lab**: the real queue and worker replay the desk's dip-buying version on synthetic 15-minute candles.

Done when: the coin labelled ``falling`` gets a "wait" stance every time; the Leader's idea becomes a lab run; the
confirmed dip-buying version is used on the ``sideways`` coin; each day plan records its market type and playbook
revision; and every request stays under the size limit. No network, real key, paid call, real order or deployment.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from tests.day_plan_support import day_plan_account, plan
from tests.leader_news_support import LeaderSetup, ScriptedScan, full_round, news_reply
from tests.market_type_support import closes_falling, closes_range, series, store_series
from tests.money_limit_support import synthetic_policy
from tests.news_wake_support import NewsDesk
from tests.practice_range_support import cross_strategy, open_store, save
from tests.strategy_lab_support import CONFIRM, TUNE, swinging_candles, worker_for
from tests.trading_desk_support import MAX_REQUEST_BYTES, DeskRuntime

from trade_graph.application.market_types.hourly import label_after_scan
from trade_graph.application.market_types.store import MarketTypeStore
from trade_graph.application.practice_range.store import PracticeRangeStore
from trade_graph.application.trading_desk.store import DeskStore, read_day_plan_desk_records
from trade_graph.application.worker import RoleWorker
from trade_graph.contracts.strategy_lab import LabRequest
from trade_graph.domain.money import Money

DAY0 = datetime(2026, 10, 16, tzinfo=UTC)
DAYS = 7
HOURLY_START = datetime(2026, 7, 20, tzinfo=UTC)  # 863 hours before the lab's September windows, so they are labelled
DIP = "dip-rebound@1"
DESK_IDEA = ("Buy dips in sideways markets: rest bids after a dip below the range and leave falling coins alone; "
             "fees decide, so trade less.")
DIP_STRATEGY = cross_strategy(
    strategy_id="dip-rebound", title="Buy the dip back above 98",
    hypothesis="In a sideways market a close back above 98 after a dip starts a rebound; a test strategy only.",
    entry={"all_of": [{"left": {"candle": "close"}, "relation": "crosses_above", "right": {"level": "98"}},
                      {"market_type": "sideways", "relation": "is"}],
           "order": {"kind": "market", "expires_after_bars": 1}})


def _research(task: dict) -> dict:
    wake = task["input"]["wake"]
    if wake["purpose"] == "focus":
        return {"wake_outcome": {"summary": "SOL and BTC today.", "focus": {
            "summary": "SOL and BTC today.",
            "coins": [{"symbol": "SOL/USD", "note": "Synthetic: range-bound."},
                      {"symbol": "BTC/USD", "note": "Synthetic: sliding."}]}}}
    return {"wake_outcome": {"summary": "Synthetic Research: noted."}}


class Desk:
    """The scripted Trading desk: it reads only what its request shows and answers as the desk would."""

    def __init__(self) -> None:
        self.turns: list[dict] = []

    def __call__(self, context: dict) -> dict:
        wake, news, lab = context["wake"], context["news"], context["lab"]
        readings = {item["symbol"]: item for item in wake.get("market_types") or ()}
        confirmed = [item for item in context["practice_range"] if item["strategy_ref"] == DIP]
        reports = [report["run_id"] for item in confirmed for report in item["reports"]][:4]
        btc_news = [item["headline_id"] for item in news["jev"]["verdicts"] if "BTC" in item["subjects"]][:3]
        answer: dict = {"summary": "Scripted desk turn.", "stances": []}
        for symbol in ("SOL/USD", "BTC/USD"):
            seen = (readings.get(symbol) or {}).get("market_type") or "none"
            if seen == "falling":
                answer["stances"].append({"symbol": symbol, "stance": "wait", "market_type_seen": seen,
                                          "strategy_ref": None, "news_refs": btc_news if symbol == "BTC/USD" else [],
                                          "reason": "It is falling: the playbook waits in cash, which costs no fee."})
            elif seen == "sideways":
                answer["stances"].append({"symbol": symbol, "stance": "buy_dips", "market_type_seen": seen,
                                          "strategy_ref": DIP if confirmed else None, "news_refs": [],
                                          "reason": "Range-bound: buy dips with the confirmed version."
                                          if confirmed else "Range-bound: buy dips once a version is confirmed."})
        sideways = {"market_type": "sideways", "stance": "buy_dips", "strategy_refs": [DIP] if confirmed else [],
                    "reason": "Rest maker bids after dips inside the range.", "report_ids": reports}
        wanted = [{"market_type": "rising", "stance": "follow_trend", "strategy_refs": ["slow-trend-pullback@1"],
                   "reason": "Follow the trend with the pullback version.", "report_ids": []},
                  sideways,
                  {"market_type": "falling", "stance": "wait", "strategy_refs": [],
                   "reason": "The seed strategies lost more than cash after fees in falling months.", "report_ids": []},
                  {"market_type": "unclear", "stance": "wait", "strategy_refs": [],
                   "reason": "No known type, no trade.", "report_ids": []}]
        current = (context["playbook"].get("current") or {}).get("choices")
        if current != wanted:
            answer["playbook"] = {"choices": wanted, "why": "The dip version is confirmed now." if confirmed
                                  else "Wait in falling markets; buy dips in sideways ones once confirmed."}
        registered = any(family["strategy_id"] == "dip-rebound" for family in lab.get("families") or ())
        for item in news["lab_inbox"]:
            if not item["lab_requests"] and not registered:
                answer["lab_requests"] = [request.model_dump(mode="json") for request in _lab(item["request_id"])]
                registered = True
        planned = {item["symbol"] for item in wake["plans"]}
        if confirmed and (readings.get("SOL/USD") or {}).get("market_type") == "sideways" and "SOL/USD" not in planned:
            answer["day_plans"] = [plan(strategy_ref=DIP, rationale="Buy the dip in a sideways SOL with the "
                                        "confirmed version.").model_dump(mode="json")]
        self.turns.append({"purpose": wake["purpose"], "reasons": wake["reasons"], "answer": answer})
        return answer


def _lab(idea_id: str) -> list[LabRequest]:
    note = f"Testing the Leader's idea {idea_id}."
    common = {"study_id": "dip-sep", "strategy_ref": DIP, "symbol": "SOL/USD", "source": "synthetic",
              "cost_profiles": ("okx-eea-spot",), "note": note}
    return [LabRequest(kind="register_strategy", strategy_json=json.dumps(DIP_STRATEGY.canonical_document()),
                       note=note),
            LabRequest(kind="create_study", study_id="dip-sep", strategy_id="dip-rebound", tune_start=TUNE[0],
                       tune_end=TUNE[1], confirm_start=CONFIRM[0], confirm_end=CONFIRM[1], note=note),
            LabRequest(kind="tune", **common),
            LabRequest(kind="confirm", **common)]


def _round(day: int):
    def build(refs: list[str]) -> dict:
        ideas = [{"department": "trader", "idea": DESK_IDEA, "source_refs": [refs[4]]}] if day == 0 else []
        return news_reply(refs, strategy_ideas=ideas, next_check_in={"at_utc": None, "note": "tomorrow"},
                          summary=f"Synthetic round of day {day + 1}: Bitcoin slides; Kraken delays SOL deposits.")
    return full_round(build)


@pytest.fixture
def week(tmp_path):
    account = day_plan_account(tmp_path)
    # Synthetic paid-call envelope for the scripted Leader and desk (paid calls stay off: both are scripted).
    policy = synthetic_policy("synthetic-desk-week")
    envelope = Money(amount="50", currency="EUR")
    account.install(policy.model_copy(update={"monthly_operating": envelope, "daily_paid_limit": envelope,
                                              "root_paid_limit": envelope}))
    setup = LeaderSetup(tmp_path, account=account, departments=("leader", "research", "trader"),
                        scan=ScriptedScan("SOL/USD", "BTC/USD"))
    desk = Desk()
    runtime = DeskRuntime(account, setup.scheduler, reply=desk, workspace=tmp_path / "desk", owner="week")
    setup.engine.task_settings = runtime.task_settings
    market, candles = open_store(tmp_path / "market_data.sqlite")
    hours = int((DAY0 + timedelta(days=DAYS + 1) - HOURLY_START).total_seconds() // 3600)
    store_series(candles, series("SOL/USD", closes_range(hours), start=HOURLY_START))
    store_series(candles, series("BTC/USD", closes_falling(hours, base="400", step="0.1"), start=HOURLY_START))
    save(candles, swinging_candles("SOL/USD"), label="synthetic_fixture")
    yield account, setup, desk, runtime, candles
    market.close()


def _run_due(setup, runtime, lab, limit: int = 60) -> None:
    """Step the engine and run every due turn (the Leader's rounds, Research, the desk) and the lab, until idle."""
    others = RoleWorker(setup.scheduler, owner="week", system_version_id="wake-tests", reconcile=lambda: None)
    for _ in range(limit):
        setup.engine.step([setup.portfolio])
        lease = setup.scheduler.claim("week", roles={"leader", "research", "trader"})
        if lease is None:
            if lab.run_until_idle()[0]["status"] == "idle":
                setup.engine.step([setup.portfolio])
                lease = setup.scheduler.claim("week", roles={"leader", "research", "trader"})
                if lease is None:
                    return
            else:
                continue
        row = setup.scheduler.leased_row(lease)
        verdict = setup.engine.before_run(row, reclaimed=lease.reclaimed)
        if verdict is not None:
            setup.scheduler.skip(lease, {"reason": verdict, "obsolete_wake": True})
        elif row["role"] == "trader":
            runtime.run(lease)
        else:
            others._run_lease(lease, {"leader": setup.rounds, "research": _research})
        setup.engine.finish_task(lease.task_id)
    raise AssertionError("turns kept appearing")


def test_a_scripted_week_of_the_trading_desk(week) -> None:
    account, setup, desk, runtime, candles = week
    labels = MarketTypeStore(account.database, account.clock)
    lab = worker_for(account, candles)
    jev = NewsDesk(account)
    for day in range(DAYS):
        if day:
            account.clock.advance((DAY0 + timedelta(days=day, minutes=30) - account.clock.now()).total_seconds())
        labelled = {item.symbol: item.market_type for item in label_after_scan(
            candles=candles, labels=labels, scan=None, as_of=account.clock.now(), day_symbols=("SOL/USD", "BTC/USD"),
            source="synthetic").stored}
        assert labelled == {"SOL/USD": "sideways", "BTC/USD": "falling"}, labelled
        account.quote("SOL/USD", "99.9", "100.1")
        account.quote("BTC/USD", "49990", "50010")
        if day in (0, 3):
            jev.headline(f"Bitcoin slides for a {day + 2}th day as outflows continue (synthetic)",
                         f"https://www.coindesk.com/synthetic/btc-slide-{day}",
                         concerns={"BTC": "0.9", "none": "0.1"}, event="other", yes="0.8")
            jev.poll()
        setup.respond(*_round(day))
        _run_due(setup, runtime, lab)

    store = DeskStore(account.database, account.clock)
    stances = store.stances(account.portfolio)
    trader_tasks = account.database.execute("SELECT status, output_json FROM tasks WHERE role = 'trader'").fetchall()
    assert trader_tasks and all(row["status"] == "SUCCEEDED" for row in trader_tasks), [
        row["output_json"][:200] for row in trader_tasks if row["status"] != "SUCCEEDED"]

    # 1. The coin labelled falling gets a "wait" stance, every time, with the label software showed.
    btc = [item for item in stances if item.symbol == "BTC/USD"]
    assert len(btc) >= DAYS and {item.stance for item in btc} == {"wait"}
    assert {(item.market_type_seen, item.label_status, item.label_market_type) for item in btc} == {
        ("falling", "current", "falling")}
    assert any(item.news_refs for item in btc)  # the Jev verdicts it weighed are named
    assert account.database.execute("SELECT COUNT(*) FROM day_plans WHERE symbol = 'BTC/USD'").fetchone()[0] == 0

    # 2. The Leader's idea for the desk became a lab run: the desk queued it, citing the idea; the lab confirmed it.
    (idea,) = [row for row in account.database.execute(
        "SELECT request_id, department FROM leader_round_requests WHERE kind = 'strategy_idea'").fetchall()]
    assert idea["department"] == "trader"
    runs = account.database.execute(
        "SELECT kind, status FROM strategy_lab_requests WHERE instr(document_json, ?) > 0 ORDER BY sequence",
        (idea["request_id"],)).fetchall()
    assert [(row["kind"], row["status"]) for row in runs] == [
        ("register_strategy", "done"), ("create_study", "done"), ("tune", "done"), ("confirm", "done")]
    confirmations = PracticeRangeStore(account.database, account.clock).reports_for(DIP, phase="confirm")
    assert confirmations and {report.symbol for report in confirmations} == {"SOL/USD"}

    # 3. The confirmed dip-buying version is used on the sideways coin, every day once it was confirmed.
    plans = account.database.execute(
        "SELECT plan_id, symbol, strategy_ref, trading_day_start FROM day_plans ORDER BY sequence").fetchall()
    assert len(plans) == DAYS and {(row["symbol"], row["strategy_ref"]) for row in plans} == {("SOL/USD", DIP)}
    sol = [item for item in stances if item.symbol == "SOL/USD"]
    assert {item.stance for item in sol} == {"buy_dips"} and DIP in {item.strategy_ref for item in sol}

    # 4. Each day plan records its market type and the playbook revision in force when it was installed.
    revisions = [record for record in _revisions(account)]
    assert [record.revision for record in revisions] == [1, 2]
    assert revisions[0].choice_for("sideways").strategy_refs == ()
    assert revisions[1].choice_for("sideways").strategy_refs == (DIP,)
    assert revisions[1].choice_for("sideways").report_ids  # the confirmation it rests on
    with account.database.connect() as connection:
        records = read_day_plan_desk_records(connection, portfolio_id=account.portfolio)
    assert set(records) == {row["plan_id"] for row in plans}
    for record in records.values():
        assert (record.market_type, record.label_status, record.market_type_in_force) == (
            "sideways", "current", "sideways")
        assert (record.playbook_revision, record.playbook_revision_id) == (2, revisions[1].revision_id)
        assert record.stance == "buy_dips"

    # 5. Every request of the desk stayed under the 128 KiB bound.
    assert len(runtime.sizes) == len(trader_tasks) and runtime.largest < MAX_REQUEST_BYTES
    print(f"scripted week: {len(runtime.sizes)} desk requests, the largest {runtime.largest} bytes; "
          f"{len(stances)} stances, {len(plans)} day plans, {len(desk.turns)} desk turns")

    # No paid call left the test: the Leader's calls went to the scripted transport only; orders stay on paper.
    assert {call["url"].split("/")[2] for call in setup.transport.calls} == {"api.perplexity.ai"}
    assert account.execution.venue == "paper"
    assert account.database.execute("SELECT COUNT(*) FROM order_intents WHERE symbol = 'BTC/USD'").fetchone()[0] == 0


def _revisions(account):
    from trade_graph.application.trading_desk.store import read_playbook_revisions

    with account.database.connect() as connection:
        return read_playbook_revisions(connection, account.portfolio)
