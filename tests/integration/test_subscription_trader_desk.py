"""The subscription Trader is the Trading desk: its wake turn reads market types, playbook, lab and news (W12-A, C12).

The real protected subscription Trader (synthetic CLI, no model call) is handed the ``playbook`` and ``news`` keys on a
wake, beside ``wake`` (with each coin's market type), ``practice_range`` and ``lab``; its instruction names the desk's
job; its answer's playbook and stances are stored by the event engine. The new keys are in the protected allowlist for
the Trader only; an owner profile that does not list them yet drops them (step L3 adds them). A busy synthetic day
keeps the whole request under the 128 KiB input bound and the 10,000-node JSON bound. Nothing here places an order.
"""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

from tests.integration.test_subscription_departments import flow
from tests.integration.test_subscription_trader_wakes import WAKE_GRAPH, _engine
from tests.strategy_lab_support import TUNE, candle_file, confirm, register, study, submit, tune, worker_for
from tests.trading_desk_support import SIDEWAYS_DIPS, insert_label, insert_verdicts, playbook, stance

from trade_graph.adapters.models.subscription import MAX_INPUT_BYTES, SubscriptionConfig
from trade_graph.application.gateway import _MAX_JSON_NODES
from trade_graph.application.subscription_runtime import ROLE_CONTEXT_KEYS, SubscriptionRuntimeConfig
from trade_graph.application.trading_desk.context import PLAYBOOK_CONTEXT_BYTES
from trade_graph.application.trading_desk.news import NEWS_CONTEXT_BYTES
from trade_graph.application.trading_desk.store import DeskStore
from trade_graph.contracts.wakes import FocusCoin, FocusSelection, ScanReference, ShortlistCoin
from trade_graph.domain.clock import utc_iso

DESK_KEYS = {"playbook", "news"}


def _nodes(value) -> int:
    if isinstance(value, dict):
        return 1 + sum(_nodes(item) for item in value.values())
    if isinstance(value, list):
        return 1 + sum(_nodes(item) for item in value)
    return 1


def _size(value) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode())


def test_a_desk_wake_turn_is_handed_market_types_playbook_and_news_and_its_answer_is_stored(tmp_path) -> None:
    setup, _protected, cli = flow(tmp_path, source=WAKE_GRAPH)
    engine = _engine(setup)
    insert_label(setup.db, "BTC/USD", "falling", setup.clock.now() - timedelta(minutes=10))
    store = DeskStore(setup.db, setup.clock)
    store.record_playbook(setup.pid, playbook(), wake_id="w-0", task_id="t-0")
    engine.request_wake(setup.pid, "trader", "Synthetic: BTC looks weak.", requested_by="leader")
    engine.step([setup.pid])
    cli.outputs["trader"] = {"summary": "Wait on BTC.", "stances": [stance().model_dump(mode="json")],
                             "playbook": {**SIDEWAYS_DIPS, "why": "Synthetic: confirmed again."}}
    assert setup.run("trader") == 1
    (request,) = [item for item in cli.requests if item.role == "trader"]
    context = request.context
    assert DESK_KEYS | {"wake", "lab", "practice_range"} <= set(context)
    assert context["playbook"]["current"]["revision"] == 1
    assert [item["market_type"] for item in context["playbook"]["current"]["choices"]] == ["sideways", "falling"]
    assert context["news"]["available"] is True and context["news"]["lab_inbox"] == []
    assert "market_types" in context["wake"]
    for words in ("Trading desk", "market type", "wait in cash", "buy dips", "follow the trend", "Fees",
                  "stances", "playbook", "lab", "news", "never refuses"):
        assert words in request.instructions, words
    assert {"playbook", "stances"} <= set(request.output_schema["properties"])
    task = setup.db.execute("SELECT status, output_json FROM tasks WHERE role = 'trader'").fetchone()
    assert task["status"] == "SUCCEEDED", task["output_json"]
    engine.step([setup.pid])
    (wake,) = engine.wakes(setup.pid)
    assert wake.status == "completed" and wake.outcome.playbook_revision_id
    assert store.playbook(setup.pid).revision == 2
    (recorded,) = store.stances(setup.pid)
    assert (recorded.symbol, recorded.stance, recorded.label_status, recorded.label_market_type) == (
        "BTC/USD", "wait", "current", "falling")
    assert setup.db.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0


def test_the_desk_keys_are_the_traders_only_and_an_older_owner_profile_drops_them() -> None:
    assert DESK_KEYS <= ROLE_CONTEXT_KEYS["trader"]
    for role in ("research", "learning", "optimisation", "leader", "engineer"):
        assert not DESK_KEYS & ROLE_CONTEXT_KEYS[role]
    older = {role: sorted(keys - DESK_KEYS) for role, keys in ROLE_CONTEXT_KEYS.items()}
    config = SubscriptionRuntimeConfig(subscription=SubscriptionConfig(
        provider="claude_subscription", model="scripted", enabled=True, allowed_context_keys=older))
    assert not DESK_KEYS & set(config.subscription.allowed_context_keys["trader"])  # what step L3 adds


def test_a_busy_desk_day_keeps_the_whole_request_under_the_bounds(tmp_path) -> None:
    """Everything the desk reads at once, at its busiest: 32 labelled coins, 40 playbook revisions with long reasons,
    100 stances, 12 Leader rounds with four long desk ideas each, 150 Jev verdicts on its coins, a full lab and 40
    wake requests with 500-character notes. Each key stays inside its share and the request inside 128 KiB."""
    setup, _protected, cli = flow(tmp_path, source=WAKE_GRAPH)
    engine = _engine(setup)
    account = SimpleNamespace(database=setup.db, clock=setup.clock, portfolio=setup.pid)
    now = setup.clock.now()
    coins = [f"C{number:02d}X/USD" for number in range(40)]
    for number, symbol in enumerate(coins):
        insert_label(setup.db, symbol, ("rising", "sideways", "falling")[number % 3], now - timedelta(minutes=20))
    store = DeskStore(setup.db, setup.clock)
    long_reason = "Synthetic long reason " + "r" * 270
    choices = [{"market_type": kind, "stance": "wait", "strategy_refs": [], "reason": long_reason,
                "report_ids": [f"pr-{item}" for item in range(8)]} for kind in ("rising", "sideways", "falling",
                                                                                   "unclear")]
    for number in range(40):
        store.record_playbook(setup.pid, playbook(why="W" * 500, choices=choices), wake_id=f"w-{number}",
                              task_id=f"t-{number}")
    shown = {}
    for batch in range(4):
        store.record_stances(setup.pid, [stance(symbol, "wait", "falling", reason=long_reason,
                                                news_refs=[f"nh-{item}" for item in range(8)])
                                         for symbol in coins[batch * 10:(batch + 1) * 10] * 1],
                             wake_id=f"w-s{batch}", task_id=None, trading_day_start=now.replace(hour=0),
                             shown=shown, playbook_revision_id=None)
    _rounds(setup, ideas=4, rounds=12)
    insert_verdicts(setup.db, [(coins[number % 32].split("/")[0], f"Synthetic headline {number} " + "t" * 180)
                               for number in range(150)], now - timedelta(minutes=20))
    market, candles = candle_file(tmp_path / "market_data.sqlite")
    requests = [register(), study()]
    for day in range(2, 8):
        requests.append(tune(window=(TUNE[0], TUNE[0].replace(day=day)), profiles=("all",)))
    requests.append(confirm(profiles=("all",)))
    requests += [register(version=100 + number, note="x" * 480) for number in range(40)]
    submit(account, requests)
    worker_for(account, candles).run_until_idle()
    market.close()
    day = engine.trading_day()
    shortlist = ScanReference(status="ok", source="latest_recorded", scan_id="scan-busy", as_of=now,
                              evidence="synthetic", shortlist=tuple(ShortlistCoin(symbol=symbol, position=index + 1)
                                                                    for index, symbol in enumerate(coins)))
    engine.store.begin_step(setup.pid, day.start, day.end, "scan", status="done",
                            detail={"scan": shortlist.model_dump(mode="json")})
    engine.store.record_focus(portfolio_id=setup.pid, day_start=day.start, day_end=day.end, scan_id="scan-busy",
                              selection=FocusSelection(coins=tuple(FocusCoin(symbol=symbol, note="f" * 500)
                                                                   for symbol in coins[:16]), summary="s" * 1000),
                              wake_id=None, task_id=None)
    for number in range(72):
        engine.request_wake(setup.pid, "trader", f"Synthetic busy note {number} " + "n" * 470, requested_by="leader")
    engine.step([setup.pid])
    cli.outputs["trader"] = {"summary": "Busy day: nothing new."}
    assert setup.run("trader") == 1
    task = setup.db.execute("SELECT status, output_json FROM tasks WHERE role = 'trader'").fetchone()
    assert task["status"] == "SUCCEEDED", task["output_json"]
    (request,) = [item for item in cli.requests if item.role == "trader"]
    whole = len(request.model_dump_json().encode())
    assert whole <= MAX_INPUT_BYTES, whole
    assert _nodes(request.context) < _MAX_JSON_NODES
    assert _size(request.context["playbook"]) <= PLAYBOOK_CONTEXT_BYTES
    assert _size(request.context["news"]) <= NEWS_CONTEXT_BYTES
    assert request.context["news"]["lab_inbox_omitted"] > 0 or len(request.context["news"]["lab_inbox"]) == 48
    # On this busiest day the last step trimmed the market types of shortlist-only coins (their word stays on the
    # shortlist) and said how many; the focus coins keep theirs.
    shown = request.context["wake"]["market_types"]
    assert len(shown) + request.context["wake"].get("market_types_omitted", 0) == 32  # the wake's display bound
    assert {item["symbol"] for item in shown} >= {f"C{number:02d}X/USD" for number in range(16)}
    assert request.context["wake"]["triggers_total"] == 73  # 72 notes and the lab's results
    print(f"busy desk request: {whole} bytes, {_nodes(request.context)} nodes; "
          + ", ".join(f"{key} {_size(value)}" for key, value in sorted(request.context.items())))


def _rounds(setup, *, ideas: int, rounds: int) -> None:
    """Completed Leader rounds written as T38 writes them (a started round, its routed requests, then finished)."""
    for number in range(rounds):
        round_id = f"lnr-synthetic-{number}"
        at = utc_iso(setup.clock.now() - timedelta(minutes=rounds - number))
        refs = [f"web:{'a' * 32}:{item}" for item in range(1, 6)]
        reply = {"summary": "Synthetic round " + "s" * 900, "findings": [], "deep_dives": [], "trader_note": None,
                 "headlines": [{"source_ref": refs[0], "takeaway": "h" * 280, "symbols": ["C01X/USD"]}] * 12,
                 "strategy_ideas": [], "next_check_in": {"at_utc": None, "note": None}}
        setup.db.execute(
            """INSERT INTO leader_news_rounds (round_id, portfolio_id, task_id, wake_id, trading_day_start, status,
               run_id, brief_json, brief_sha256, reply_json, output_json, reason, started_at, finished_at)
               VALUES (?, ?, ?, ?, ?, 'started', NULL, '{}', ?, NULL, NULL, NULL, ?, NULL)""",
            (round_id, setup.pid, f"t-round-{number}", f"w-round-{number}", at[:10] + "T00:00:00.000000Z",
             "0" * 64, at))
        for position in range(ideas):
            request_id = f"ldr-{number:04d}{position:04d}"
            document = {"request_id": request_id, "kind": "strategy_idea", "department": "trader",
                        "asked_department": "trader", "text": f"Idea {number}.{position} " + "i" * 280,
                        "source_refs": refs[:3]}
            setup.db.execute(
                """INSERT INTO leader_round_requests (request_id, round_id, portfolio_id, kind, department,
                   asked_department, position, trigger_source_ref, document_json, created_at)
                   VALUES (?, ?, ?, 'strategy_idea', 'trader', 'trader', ?, ?, ?, ?)""",
                (request_id, round_id, setup.pid, position, f"wake-request:w-round-{number}:{position}",
                 json.dumps(document), at))
        setup.db.execute(
            "UPDATE leader_news_rounds SET status = 'completed', reply_json = ?, output_json = '{}', finished_at = ? "
            "WHERE round_id = ?", (json.dumps(reply), at, round_id))


def test_the_last_fit_trims_the_desks_own_parts_in_a_fixed_order_and_counts_them() -> None:
    from trade_graph.application.trading_desk.context import fit_desk_request

    def context() -> dict:
        reading = {"symbol": "X/USD", "status": "current", "sentence": "s" * 300}
        return {
            "wake": {"triggers": [{"note": "n" * 400}] * 40, "triggers_total": 40,
                     "market_types": [{**reading, "seen_as": ["focus"]}] * 4
                     + [{**reading, "seen_as": ["shortlist"]}] * 20},
            "news": {"leader_round": None, "lab_inbox": [{"idea": "i" * 300}] * 20, "lab_inbox_omitted": 0,
                     "jev": {"verdicts": [{"title": "t" * 200}] * 30, "verdicts_omitted": 0}},
            "playbook": {"current": None, "recent_stances": [{"reason": "r" * 300}] * 20,
                         "recent_stances_omitted": 0},
            "market": {"SOL/USD": "m" * 20_000}}

    assert fit_desk_request(context(), 1_000_000) == []  # it fits: nothing is touched
    trimmed = context()
    steps = fit_desk_request(trimmed, _size(trimmed) - 6000)
    assert steps == ["news_half"] and trimmed["news"]["jev"]["verdicts_omitted"] > 0
    trimmed = context()
    steps = fit_desk_request(trimmed, 36_000)
    assert steps[:4] == ["news_half", "stances", "shortlist_market_types", "triggers_20"]
    assert trimmed["wake"]["market_types_omitted"] == 20 and len(trimmed["wake"]["market_types"]) == 4
    assert len(trimmed["wake"]["triggers"]) <= 20 and trimmed["wake"]["triggers_total"] == 40
    assert trimmed["playbook"]["recent_stances_omitted"] > 0
    assert trimmed["market"] == context()["market"]  # other departments' parts are never trimmed here
