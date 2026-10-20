"""A scripted week of the Trading desk with fills, for HQ's Trading desk room and the scorecard's split (W13-A, C13).

It is W12-A's scripted week (``tests/integration/test_trading_desk_week.py``), run the same way: synthetic hourly
candles labelled each day by the protected labeller (SOL/USD ``sideways``, BTC/USD ``falling``), a daily Leader news
round on a scripted Perplexity (the first with a strategy idea for the desk), Jev verdicts on BTC in shadow mode, the
real Trader handler on a scripted desk and the real lab worker, here with its ``strategy_lab`` activities recorded as
the installed service records them. On top of it, the desk's SOL day plans meet the market: on day 2 the dip buy fills
and its target sells (a win), on day 4 it fills and its stop sells (a loss), on day 6 it fills and is still held.

Everything is synthetic and labelled ``synthetic_fixture``: no network, real key, paid call or real order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from tests.day_plan_action_support import fill_quote
from tests.day_plan_support import day_plan_account, manage
from tests.integration.test_trading_desk_week import DAY0, DAYS, HOURLY_START, Desk, _round, _run_due
from tests.leader_news_support import LeaderSetup, ScriptedScan
from tests.market_type_support import closes_falling, closes_range, series, store_series
from tests.money_limit_support import synthetic_policy
from tests.news_wake_support import NewsDesk
from tests.practice_range_support import open_store, save
from tests.strategy_lab_support import swinging_candles
from tests.trading_desk_support import DeskRuntime
from trade_graph.api.hq_desk_contract import (
    FAMILIES_MAX,
    HEADLINES_MAX,
    IDEAS_MAX,
    INBOX_MAX,
    LAB_REPORTS_MAX,
    LAB_RESULTS_MAX,
    SPLIT_ROWS_MAX,
    STANCES_MAX,
    VERDICTS_MAX,
)
from trade_graph.application.market_types.hourly import label_after_scan
from trade_graph.application.market_types.store import MarketTypeStore, StoredMarketType
from trade_graph.application.scorecard import MARKET_TYPES_AT_ENTRY, STANCES_AT_ENTRY, EntryChoice, split_scorecard
from trade_graph.application.strategy_lab.worker import ACTIVITY_KIND, StrategyLabWorker
from trade_graph.contracts.trading_desk import PlaybookChoice, PlaybookRecord, StanceRecord
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.money import Money
from trade_graph.kernel.money_limits import TradingDay
from trade_graph.kernel.trade_metrics import TradeFill, summarize

# Which day's dip buy fills, and what sells it: the target (a win), the stop (a loss) or nothing (still held).
FILLS = {1: ("104.1", "104.3"), 3: ("96.9", "97.1"), 5: None}


@dataclass
class DeskWeek:
    account: Any
    setup: Any
    desk: Desk
    runtime: DeskRuntime
    candles: Any
    market: Any

    def close(self) -> None:
        self.market.close()


def _fill_the_dip(account, sells: tuple[str, str] | None) -> None:
    """The desk's SOL plan of today: the resting buy fills, then its safety exit sells or keeps holding."""
    account.dispatch()
    fill_quote(account)  # the ask reaches the resting bid at 99.5
    manage(account)
    if sells is not None:
        account.clock.advance(60)
        account.quote("SOL/USD", *sells)
        manage(account)
    account.clock.advance(60)
    account.quote("SOL/USD", "99.9", "100.1")
    account.quote("BTC/USD", "49990", "50010")


def desk_week(tmp_path, *, days: int = DAYS, fills: dict[int, tuple[str, str] | None] | None = None) -> DeskWeek:
    """Run the scripted week (``days`` trading days) and return its records' owner; the caller closes it."""
    fills = FILLS if fills is None else fills
    account = day_plan_account(tmp_path)
    policy = synthetic_policy("synthetic-desk-week")
    envelope = Money(amount="50", currency="EUR")  # synthetic paid-call envelope; every call is scripted
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
    labels = MarketTypeStore(account.database, account.clock)

    def record(payload: dict[str, Any]) -> None:  # as installation.installed_strategy_lab records them
        account.ledger._activity(account.portfolio, ACTIVITY_KIND, payload)

    lab = StrategyLabWorker(account.database, account.clock, candles=candles, record=record)
    jev = NewsDesk(account)
    for day in range(days):
        if day:
            account.clock.advance((DAY0 + timedelta(days=day, minutes=30) - account.clock.now()).total_seconds())
        label_after_scan(candles=candles, labels=labels, scan=None, as_of=account.clock.now(),
                         day_symbols=("SOL/USD", "BTC/USD"), source="synthetic")
        account.quote("SOL/USD", "99.9", "100.1")
        account.quote("BTC/USD", "49990", "50010")
        if day in (0, 3):
            jev.headline(f"Bitcoin slides for a {day + 2}th day as outflows continue (synthetic)",
                         f"https://www.coindesk.com/synthetic/btc-slide-{day}",
                         concerns={"BTC": "0.9", "none": "0.1"}, event="other", yes="0.8")
            jev.poll()
        setup.respond(*_round(day))
        _run_due(setup, runtime, lab)
        if day in fills and account.database.execute(
                "SELECT COUNT(*) FROM day_plans WHERE symbol = 'SOL/USD' AND trading_day_start <= ? "
                "AND trading_day_end > ?", (account.clock.now().isoformat(),) * 2).fetchone()[0]:
            _fill_the_dip(account, fills[day])
            _run_due(setup, runtime, lab)  # the fills wake the desk; it answers
    return DeskWeek(account=account, setup=setup, desk=desk, runtime=runtime, candles=candles, market=market)


# --- the busiest room: every list at its bound, the longest words (the payload budget) -------------------------------


LONG = "Synthetic and deliberately long: " + "the desk weighs fees, the range, the trend and the news; " * 12


def _long(limit: int) -> str:
    return LONG[:limit]


def _playbook(number: int, created: datetime, previous: str | None) -> PlaybookRecord:
    choices = [PlaybookChoice(market_type=word, stance="wait" if word == "falling" else "buy_dips",
                              strategy_refs=() if word == "falling" else tuple(f"family-{word}-{n:02d}@{n + 1}"
                                                                              for n in range(8)),
                              reason=_long(300), report_ids=tuple(f"run-{word}-{n}" for n in range(8)))
               for word in ("rising", "sideways", "falling", "unclear")]
    return PlaybookRecord(revision_id=f"pb-{number:032x}", portfolio_id="paper-synthetic", revision=number,
                          previous_revision_id=previous, set_by="trader", wake_id=f"wake-{number}", task_id=None,
                          why=_long(500), choices=tuple(choices), document_sha256="0" * 64, created_at=created)


def busiest_inputs(now: datetime) -> dict[str, Any]:
    """Synthetic inputs with every list longer than the room shows and every text at its longest."""
    first = _playbook(1, now - timedelta(days=3), None)
    current = _playbook(2, now - timedelta(hours=2), first.revision_id)
    day = TradingDay(start=now.replace(hour=0, minute=0, second=0, microsecond=0),
                     end=now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
    stances, labels = {}, {}
    for number in range(STANCES_MAX + 24):
        symbol = f"LONGCOIN{number:02d}/USDT"
        stances[symbol] = StanceRecord(
            stance_id=f"st-{number}", portfolio_id="paper-synthetic", wake_id=f"wake-{number}", task_id=None,
            trading_day_start=day.start, symbol=symbol,
            stance=("wait", "buy_dips", "follow_trend", "other")[number % 4], market_type_seen="sideways",
            label_status=("current", "stale", "missing")[number % 3],
            label_market_type=None if number % 3 == 2 else "sideways", label_id=None,
            strategy_ref=None if number % 4 == 0 else f"family-long-name-{number:02d}@12", news_refs=tuple(
                f"news-{number}-{n}" for n in range(8)), reason=_long(300), playbook_revision_id=current.revision_id,
            created_at=now - timedelta(minutes=number))
        labels[symbol] = StoredMarketType(
            label_id=f"mt-{number:032x}", sequence=number, symbol=symbol,
            as_of=now - timedelta(minutes=30 + 7 * number), market_type="sideways", reason=None, candle_close=None,
            lag_bars=0, interval_minutes=60,
            source="synthetic", rules_ref="market-type-rules@1", rules_sha256="0" * 64, scan_id=None,
            evidence="synthetic_fixture", document={}, recorded_at=now)
    brief = {"kind": "confirm", "strategy_ref": "family-long-name-00@12", "symbol": "LONGCOIN00/USDT",
             "study_id": "study-with-a-long-name-00"}
    results, metrics = [], {}
    for number in range(LAB_RESULTS_MAX + 30):
        reports = [{"run_id": f"run-{number}-{n}", "cost_profile_ref": "kraken-spot-after-10k-volume-tier@1"}
                   for n in range(LAB_REPORTS_MAX + 2)]
        status = ("done", "refused", "failed")[number % 3] if number % 5 == 0 else "done"
        results.append({"request_id": f"wake:wake-with-a-long-id-{number:04d}:{number}", "kind": "confirm",
                        "status": status, "brief": brief, "finished_at": utc_iso(now - timedelta(minutes=number)),
                        "seconds": "123.456", "attempts": 1, "window": {"start": "2026-09-08", "end": "2026-10-15"},
                        **({"reports": reports} if status == "done" else {"reason": _long(300)})})
        for report in reports:
            metrics[report["run_id"]] = {"symbol": "LONGCOIN00/USDT", "profile": report["cost_profile_ref"],
                                         "metrics": {"currency": "USD", "result": "-12345.6789012345", "trades": 1234,
                                                     "benchmarks": {"hold_coin": {
                                                         "symbol": "LONGCOIN00/USDT", "result": "-23456.789012345",
                                                         "versus": "11111.1101111105"}}}}
    families = [{"strategy_id": f"family-long-name-{number:02d}", "versions": 123, "studies": 45,
                 "runs": {"tune": 678, "confirm": 90},
                 "confirmed_versions": [f"family-long-name-{number:02d}@{n}" for n in range(5)]}
                for number in range(FAMILIES_MAX + 4)]
    lab = {"available": True, "queue": {"queued": 40, "running": 1, "next": [
        {"request_id": f"wake:w:{n}", "status": "running" if n == 0 else "queued", "brief": brief}
        for n in range(10)]}, "results": results, "results_total": {"done": 300, "refused": 40, "failed": 3},
        "families": families, "families_omitted": 7}
    news = {"available": True, "leader_round": {
        "round_id": "lr-1", "finished_at": utc_iso(now - timedelta(minutes=50)), "summary": _long(600),
        "headlines": [{"source_ref": f"https://www.longpublisher-name-{n}.example/synthetic/item",
                       "takeaway": _long(300), "symbols": ["BTC/USD"]} for n in range(HEADLINES_MAX + 7)],
        "desk_note": {"request_id": "lr-1:note", "note": _long(600)},
        "strategy_ideas_for_desk": [{"request_id": f"lr-1:idea-{n}", "idea": _long(500)}
                                    for n in range(IDEAS_MAX + 3)]},
        "lab_inbox": [{"request_id": f"lr-{n}:idea", "idea": _long(500),
                       "received_at": utc_iso(now - timedelta(days=n)), "sent_to": "trader",
                       "lab_requests": [f"wake:w:{k}" for k in range(8)]}
                      for n in range(INBOX_MAX + 5)],
        "lab_inbox_omitted": 4,
        "jev": {"mode": "shadow", "verdicts": [{
            "headline_id": f"nh-{n}", "publisher": "A publisher with a very long name indeed", "title": _long(200),
            "subjects": ["BTC", "ETH", "SOL", "kraken"], "would_wake": True,
            "checked_at": utc_iso(now - timedelta(hours=1)),
            "routes": [{"department": department, "mode": "shadow", "because": "held"}
                       for department in ("leader", "trader", "research")]} for n in range(VERDICTS_MAX + 20)],
            "verdicts_total": VERDICTS_MAX + 20, "verdicts_omitted": 9, "would_wake_total": VERDICTS_MAX + 20}}
    return {"playbook": current, "revisions": [first, current], "stances": stances, "labels_now": labels, "day": day,
            "lab": lab, "metrics": metrics, "news": news, "read_at": now - timedelta(minutes=5),
            "scorecard": {"document": _busiest_scorecard(now)}}


def _busiest_scorecard(now: datetime) -> dict:
    """A scorecard document whose split has more rows than the room shows, from the real split function."""
    fills, choices, at = [], [], now - timedelta(days=20)
    versions = [f"family-long-name-{n:02d}@{n + 1}" for n in range(SPLIT_ROWS_MAX + 6)]
    for number in range(60):
        at += timedelta(minutes=30)
        fills.append(TradeFill(fill_ref=f"b{number}", symbol="LONGCOIN00/USDT", side="buy",
                               quantity=Decimal("1.23456"), quote_amount=Decimal("123.456789"), fee=Decimal("0.4938"),
                               at=at))
        at += timedelta(minutes=30)
        fills.append(TradeFill(fill_ref=f"s{number}", symbol="LONGCOIN00/USDT", side="sell",
                               quantity=Decimal("1.23456"), quote_amount=Decimal("120.98765") + number,
                               fee=Decimal("0.9679"), at=at))
        choices.append(EntryChoice(strategy=versions[number % len(versions)],
                                   market_type=MARKET_TYPES_AT_ENTRY[number % 7], stance=STANCES_AT_ENTRY[number % 6]))
    result = sum((fill.quote_amount * (1 if fill.side == "sell" else -1) - fill.fee for fill in fills), Decimal("0"))
    metrics = summarize(currency="USD", starting_equity=Decimal("10000"), ending_equity=Decimal("10000") + result,
                        fills=fills, equity_points=[])
    split = split_scorecard(metrics, trade_choices=choices, open_choices=(), open_values=())
    return {"currency": "USD", "period": {"start": utc_iso(now - timedelta(days=21))}, "split": split.document()}


def busiest_parts(now: datetime) -> dict[str, Any]:
    """The room's five parts built by the real view functions from ``busiest_inputs``."""
    from trade_graph.api import hq_desk

    inputs = busiest_inputs(now)
    return {"playbook": hq_desk.playbook_view(inputs["playbook"], inputs["revisions"]),
            "stances": hq_desk.stances_view(inputs["stances"], inputs["labels_now"], inputs["day"], now),
            "lab": hq_desk.lab_view(inputs["lab"], inputs["metrics"]),
            "news": hq_desk.news_view(inputs["news"], inputs["read_at"], turn=True),
            "split": hq_desk.split_view(inputs["scorecard"], paper=True)}


__all__ = ["FILLS", "LONG", "DeskWeek", "busiest_inputs", "busiest_parts", "desk_week"]
