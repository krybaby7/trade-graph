"""T35's scripted end-to-end day: the scanner, Research's focus coins, the Trader's day plan, an alarm wake, a fill
wake, a protective-exit wake and the day-end review, through the real paper service with a restart in the middle.

Everything is synthetic and labelled so: a USD10,000 paper account with synthetic money limits, synthetic hourly
candles and a synthetic ticker for the real coin scanner (T31), the real price watcher (T33), day plans (T34) on the
paper broker, a scripted quote feed and scripted Research, Trader and Learning departments. No network, model, key,
paid call or real order.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tests.coin_scanner_support import hourly, new_store, record, save_candles, save_catalog, ticker, tight_quote
from tests.day_plan_support import broker_orders, day_plan_account, plan
from tests.integration.test_coin_scanner import ELIGIBLE, RULES

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.alarms.book import AlarmBook
from trade_graph.application.alarms.watcher import PriceWatcher
from trade_graph.application.coin_scanner.scanner import CoinScanner
from trade_graph.application.coin_scanner.store import CoinScanStore
from trade_graph.application.events import EngineOptions
from trade_graph.application.events.scan import CoinScanStep
from trade_graph.application.paper_service import PaperService
from trade_graph.contracts.models import Observation

D = Decimal
DAY = datetime(2026, 10, 16, 0, 0, tzinfo=UTC)
START = DAY + timedelta(seconds=30)
BASES = ("SOL", "BTC", "ETH", "ADA")


class Feed:
    """Scripted public quotes; like the paper feed, each quote also marks the coin (so money limits can value it)."""

    def __init__(self, account) -> None:
        self.account, self.batches, self.sent = account, [], 0

    def quote(self, bid: str, ask: str, *, volume: str | None = None) -> None:
        self.batches.append((bid, ask, volume))

    def poll(self):
        if not self.batches:
            return []
        bid, ask, volume = self.batches.pop(0)
        now = self.account.clock.now()
        self.sent += 1
        self.account.ledger.observe_mark(self.account.portfolio, "SOL", (D(bid) + D(ask)) / 2, "USD",
                                         source="synthetic-scripted-day")
        return [Observation(observation_id=f"day-{self.sent}", venue="paper", symbol="SOL/USD", event_time_utc=now,
                            available_at_utc=now, bid=bid, ask=ask, bid_size="1000", ask_size="1000", volume=volume,
                            kind="quote", source="synthetic-scripted-day")]

    def close(self) -> None:
        pass


def _scanner_inputs(tmp_path, clock):
    store = new_store(tmp_path / "market")
    save_catalog(store, [record(base) for base in BASES], observed_at=DAY - timedelta(hours=2))
    for base in BASES:
        save_candles(store, hourly(f"{base}/USD", ELIGIBLE[base][0], last_close=DAY, delay_seconds=20))
    quotes = {f"{base}/USD": tight_quote(ELIGIBLE[base][0].level, ELIGIBLE[base][1]) for base in BASES}
    return store, lambda: ticker(quotes, received_at=clock.now() - timedelta(seconds=10))


def _departments(log: list):
    """Scripted Research, Trader and Learning: each records its wake and answers by its purpose."""

    def research(task):
        wake = task["input"]["wake"]
        log.append(("research", wake["purpose"], tuple(wake["reasons"])))
        if wake["purpose"] != "focus":
            return {}
        shortlist = [coin["symbol"] for coin in wake["scan"]["shortlist"]]
        assert "SOL/USD" in shortlist
        return {"wake_outcome": {"focus": {"coins": [{"symbol": "SOL/USD", "note": "Synthetic: liquid, rising.",
                                                      "evidence_refs": [wake["scan"]["scan_id"]]}],
                                           "summary": "Synthetic focus: SOL today."}}}

    def trader(task):
        wake = task["input"]["wake"]
        triggers = wake["triggers"]
        log.append(("trader", wake["purpose"], tuple(wake["reasons"]),
                    tuple(trigger.get("exit_leg") for trigger in triggers if trigger["kind"] == "exit_filled")))
        if wake["purpose"] != "day_plan":
            return {"wake_outcome": {"summary": "Synthetic: the plan stands."}}
        assert wake["focus"]["selection"]["coins"][0]["symbol"] == "SOL/USD"
        day_plan = plan(
            alarms=[{"kind": "quote_price", "symbol": "SOL/USD", "relation": "crosses_below", "level": "99.8",
                     "note": "the price is coming down to the entry"}],
            entries=[{"quantity": "5", "limit_price": "99.5", "stop_price": "97", "take_profit_price": "104"}])
        return {"wake_outcome": {"day_plans": [day_plan.model_dump(mode="json")], "summary": "Synthetic SOL plan."}}

    def learning(task):
        wake = task["input"]["wake"]
        log.append(("learning", wake["purpose"], tuple(sorted(wake["reasons"]))))
        return {"wake_outcome": {"summary": "Synthetic day review."}}

    return {"research": research, "trader": trader, "learning": learning}


def _service(account, feed, handlers, options) -> PaperService:
    return PaperService(account.database, account.execution, clock=account.clock, portfolio_ids=[account.portfolio],
                        handlers=handlers, public_feed=feed, event_engine=options)


async def _ticks(service, count: int) -> None:
    for _ in range(count):
        service.clock.advance(1)
        result = await service.tick(wait_feed=True, wait_roles=True)
        assert not [failure for failure in result.failures if failure.startswith("wakes")], result.failures


def test_a_scripted_day_runs_scan_focus_plan_alarm_fill_exit_and_the_day_end_review(tmp_path) -> None:
    account = day_plan_account(tmp_path, now=START)
    candles, synthetic_ticker = _scanner_inputs(tmp_path, account.clock)
    scan = CoinScanStep(CoinScanner(candles, clock=account.clock, rules=RULES),
                        CoinScanStore(account.database, account.clock), ticker=synthetic_ticker)
    options = EngineOptions(scan=scan, watcher=PriceWatcher(AlarmBook(account.database, account.clock), candles,
                                                            account.clock))
    log: list = []
    handlers = _departments(log)
    feed = Feed(account)
    first = _service(account, feed, handlers, options)

    async def morning():
        for _ in range(4):  # the day start: scan, focus, Learning's check-in, then the Trader's plan
            feed.quote("99.9", "100.1")
            await _ticks(first, 1)
        feed.quote("99.9", "100.1")
        await _ticks(first, 1)  # the entry is sent and rests; the alarm has seen a price above its level
        await first.stop()

    asyncio.run(morning())
    assert [order["status"] for order in broker_orders(account, side="buy")] == ["open"]
    # A restart in the middle of the day: a new service on the same records loses and repeats nothing.
    second = _service(account, feed, handlers, options)

    async def the_day():
        account.clock.advance(timedelta(hours=10).total_seconds())
        feed.quote("99.6", "99.8")  # mid 99.7: the alarm crosses below 99.8
        await _ticks(second, 2)
        account.clock.advance(60)
        feed.quote("99.4", "99.5", volume="100")  # the resting entry fills; its protective exit goes out at once
        await _ticks(second, 2)
        account.clock.advance(timedelta(hours=3).total_seconds())
        feed.quote("104.2", "104.4")  # the take-profit leg of the protective exit fills
        await _ticks(second, 2)
        account.clock.advance(timedelta(hours=11).total_seconds())  # 00:01:30 the next day: day end, next start
        feed.quote("103.9", "104.1")
        await _ticks(second, 5)
        await second.stop()

    asyncio.run(the_day())
    morning_log, events, next_day = log[:3], log[3:6], log[6:]
    # Research's focus wake and Learning's day-start check-in are made at the same moment and run in either order;
    # the Trader's day-plan wake comes only after the focus step.
    assert sorted(morning_log) == sorted([("learning", "check_in", ("day_start",)),
                                          ("research", "focus", ("focus_step",)),
                                          ("trader", "day_plan", ("day_plan_step",), ())])
    assert morning_log.index(("research", "focus", ("focus_step",))) < morning_log.index(
        ("trader", "day_plan", ("day_plan_step",), ()))
    assert events == [("trader", "event", ("alarm_fired",), ()),
                      ("trader", "event", ("entry_filled",), ()),
                      ("trader", "event", ("exit_filled",), ("take_profit",))]
    assert ("learning", "day_end_review", ("day_end_review", "day_start")) in next_day
    engine = second.event_engine
    steps = engine.cycle(account.portfolio, DAY)
    assert {name: item["status"] for name, item in steps.items()} == {
        "day_start": "done", "scan": "done", "focus": "done", "day_plan": "done", "day_end_review": "done"}
    scan_record = CoinScanStore(account.database, account.clock).get(steps["scan"]["detail"]["scan"]["scan_id"])
    assert "SOL/USD" in [entry["symbol"] for entry in scan_record.entries]  # a real scan, on synthetic inputs
    assert engine.focus(account.portfolio, DAY).selection.symbols() == ("SOL/USD",)
    (applied,) = steps["day_plan"]["detail"]["plans"]
    assert applied["status"] == "installed"
    # The position opened and closed with its protective exit; nothing is left held or unprotected.
    assert account.held("SOL") == D("0")
    sells = broker_orders(account, side="sell")
    assert [(order["status"], order["take_profit_price"]) for order in sells] == [("filled", "104")]
    assert account.ledger.journal_balanced(account.portfolio)
    # Every trigger of the day reached exactly one wake; none was dropped or delivered twice.
    database: Database = account.database
    day_triggers = database.execute(
        "SELECT status, COUNT(*) FROM wake_triggers WHERE occurred_at < ? GROUP BY status",
        ("2026-10-17T00:00:00.000000Z",)).fetchall()
    assert {row[0]: row[1] for row in day_triggers} == {"delivered": 6}  # focus, check-in, plan, alarm, fill, exit
    assert database.execute("SELECT status FROM wake_triggers WHERE kind = 'day_end_review'").fetchone()[0] == \
        "delivered"
    duplicates = database.execute(
        "SELECT COUNT(*) FROM (SELECT task_id FROM wakes GROUP BY task_id HAVING COUNT(*) > 1)").fetchone()[0]
    assert duplicates == 0
    assert all(wake.allowance.status == "unknown" for wake in engine.wakes(account.portfolio))
