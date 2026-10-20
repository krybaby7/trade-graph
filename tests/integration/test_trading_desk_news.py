"""The Trading desk reads the news (W12-A, C12; D280 item 4, D210-D214, D225-D229, D296).

The desk's ``news`` context: the newest Leader news round (summary, headlines, its note and the strategy ideas
addressed to it), the lab inbox (the Leader's strategy ideas for the desk, newest first, with the lab requests that
cite them) and Jev's recent verdicts and would-be alarms about the coins it watches, shadow mode included, within its
share of the request bound. A strategy idea for the desk wakes it with a note and waits in the inbox; software never
queues a lab run from it: the desk decides what to test.

Everything is synthetic: the paper account, the scripted Perplexity round (documented shapes, made-up key), invented
headlines with scripted Jev answers, scripted departments. No network, real key, paid call or real order.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta

from tests.leader_news_support import LeaderSetup, full_round, news_reply
from tests.news_wake_support import NewsDesk, hold_sol
from tests.strategy_lab_support import register
from tests.wake_support import run_all, run_one, scripted, wake_input

from trade_graph.application.trading_desk.news import NEWS_CONTEXT_BYTES, news_context
from trade_graph.contracts.leader_news import LeaderNewsReply

DESK_IDEA = "Buy dips in sideways markets: rest bids under the range and leave falling coins alone."


def _desk_round(refs: list[str]) -> dict:
    return news_reply(refs, strategy_ideas=[
        {"department": "trader", "idea": DESK_IDEA, "source_refs": [refs[4]]},
        {"department": "optimisation", "idea": "Compare maker and taker fees per wake.", "source_refs": [refs[3]]}])


def _leader_round(setup: LeaderSetup) -> str | None:
    setup.engine.step([setup.portfolio])
    return run_one(setup.scheduler, setup.engine, {"leader": setup.rounds})


def _news(setup, coins=("SOL/USD",)) -> dict:
    with setup.database.connect() as connection:
        return news_context(connection, setup.portfolio, coins=coins, at=setup.clock.now())


def test_a_strategy_idea_for_the_desk_wakes_it_and_waits_in_its_lab_inbox_until_it_decides(tmp_path) -> None:
    setup = LeaderSetup(tmp_path, full_round(_desk_round), departments=("leader", "research", "trader"))
    _leader_round(setup)
    [record] = setup.rounds.store.rounds(setup.portfolio)
    ideas = [request for request in record.requests if request.kind == "strategy_idea"]
    desk_idea = next(request for request in ideas if request.asked_department == "trader")
    assert desk_idea.department == "trader" and desk_idea.text == DESK_IDEA
    assert "Trading desk" in desk_idea.note and desk_idea.request_id in desk_idea.note
    # Optimisation is not woken by this engine, so its idea goes to Research, as before.
    assert next(request for request in ideas if request is not desk_idea).department == "research"

    seen: list = []
    run_all(setup.scheduler, setup.engine, {"research": scripted({"summary": "Synthetic."}),
                                            "trader": scripted({"summary": "Read the news."}, seen=seen)},
            [setup.portfolio])
    notes = [trigger["note"] for task in seen for trigger in wake_input(task)["triggers"]]
    assert desk_idea.note in notes
    assert setup.database.execute("SELECT COUNT(*) FROM strategy_lab_requests").fetchone()[0] == 0  # nothing auto

    news = _news(setup)
    assert news["available"] is True
    round_ = news["leader_round"]
    assert round_["round_id"] == record.round_id and round_["summary"] == record.summary
    assert [line["takeaway"] for line in round_["headlines"]] == [
        line.takeaway for line in LeaderNewsReply.model_validate(record.reply).headlines]
    assert "delayed SOL deposits" in round_["desk_note"]["note"]
    assert [item["idea"] for item in round_["strategy_ideas_for_desk"]] == [DESK_IDEA]
    (inbox,) = news["lab_inbox"]
    assert (inbox["request_id"], inbox["idea"], inbox["sent_to"], inbox["lab_requests"]) == (
        desk_idea.request_id, DESK_IDEA, "trader", [])

    # The desk decides: a lab request that cites the idea in its note links them; the inbox shows it.
    setup.engine.request_wake(setup.portfolio, "trader", "Synthetic: decide.", requested_by="leader")
    answer = {"summary": "Test the Leader's idea.",
              "lab_requests": [register(note=f"From the Leader's idea {desk_idea.request_id}.").model_dump(
                  mode="json")]}
    run_all(setup.scheduler, setup.engine, {"trader": scripted(answer)}, [setup.portfolio])
    (inbox,) = _news(setup)["lab_inbox"]
    assert len(inbox["lab_requests"]) == 1 and inbox["lab_requests"][0].startswith("wake:")


def test_an_idea_for_the_desk_goes_to_research_when_software_does_not_wake_the_desk(tmp_path) -> None:
    setup = LeaderSetup(tmp_path, full_round(_desk_round), departments=("leader", "research"))
    _leader_round(setup)
    [record] = setup.rounds.store.rounds(setup.portfolio)
    desk_idea = next(request for request in record.requests
                     if request.kind == "strategy_idea" and request.asked_department == "trader")
    assert desk_idea.department == "research" and "which software does not wake now" in desk_idea.note
    (inbox,) = _news(setup)["lab_inbox"]
    assert inbox["sent_to"] == "research"


def test_jev_verdicts_about_the_desks_coins_reach_it_in_shadow_mode(tmp_path) -> None:
    setup = LeaderSetup(tmp_path, departments=("leader", "trader"))
    account = setup.account
    hold_sol(account)
    desk = NewsDesk(account)
    desk.delisting("SOL")
    desk.delisting("ADA")
    desk.unrelated()
    desk.poll()
    setup.engine.step([setup.portfolio])  # the engine routes the would-wake verdicts, in shadow mode (shipped)
    news = _news(setup, coins=("SOL/USD", "BTC/USD"))
    jev = news["jev"]
    assert jev["mode"] == "shadow" and jev["coins"] == ["BTC", "SOL"]
    (verdict,) = jev["verdicts"]
    assert verdict["subjects"] == ["SOL"] and verdict["event_type"] == "delisting" and verdict["would_wake"] is True
    assert "delist SOL" in verdict["title"] and verdict["evidence"] == "synthetic_fixture"
    assert sorted((route["department"], route["mode"]) for route in verdict["routes"]) == [
        ("leader", "shadow"), ("trader", "shadow")]
    assert jev["verdicts_total"] == 1 and jev["would_wake_total"] == 1
    assert "shadow" in news["instruction"] or "shadow" in jev["note"]
    # A day later the verdict is outside the window: it is counted out, never shown as fresh.
    account.clock.advance(timedelta(hours=25).total_seconds())
    assert _news(setup, coins=("SOL/USD",))["jev"]["verdicts"] == []


def test_the_news_context_stays_inside_its_share_of_the_request_bound(tmp_path) -> None:
    setup = LeaderSetup(tmp_path, departments=("leader", "trader"))
    account = setup.account
    desk = NewsDesk(account)
    for number in range(150):
        desk.headline(f"SOL synthetic headline number {number} " + "x" * 180,
                      f"https://blog.kraken.com/synthetic/{number}", concerns={"SOL": "0.9", "none": "0.1"},
                      event="other", yes="0.9", minutes_ago=30)
    desk.poll()
    setup.engine.step([setup.portfolio])
    for number in range(12):  # twelve rounds, each with four long ideas for the desk
        ideas = [{"department": "trader", "idea": f"Idea {number}.{item} " + "y" * 270,
                  "source_refs": ["REF"]} for item in range(4)]
        setup.respond(*full_round(lambda refs, ideas=ideas: news_reply(
            refs, strategy_ideas=[{**idea, "source_refs": [refs[4]]} for idea in ideas])))
        setup.engine.request_wake(setup.portfolio, "leader", f"Synthetic round {number}.", requested_by="trader")
        setup.engine.step([setup.portfolio])
        run_one(setup.scheduler, setup.engine, {"leader": setup.rounds})
        setup.engine.step([setup.portfolio])
        account.clock.advance(60)
    news = _news(setup)
    size = len(json.dumps(news, separators=(",", ":"), ensure_ascii=False).encode())
    assert size <= NEWS_CONTEXT_BYTES, size
    assert len(news["lab_inbox"]) + news["lab_inbox_omitted"] == 48 and news["lab_inbox_omitted"] > 0
    assert news["jev"]["verdicts_total"] == 150 and news["jev"]["verdicts_omitted"] > 0
    assert len(news["jev"]["verdicts"]) + news["jev"]["verdicts_omitted"] == 150
    assert news["leader_round"]["strategy_ideas_for_desk"]  # the newest round is always shown


def test_a_database_without_news_records_gives_an_empty_news_context(tmp_path) -> None:
    connection = sqlite3.connect(tmp_path / "older.sqlite")
    connection.row_factory = sqlite3.Row
    try:
        news = news_context(connection, "p-1", coins=("SOL/USD",), at=__import__("datetime").datetime(
            2026, 10, 16, tzinfo=__import__("datetime").UTC))
    finally:
        connection.close()
    assert news["available"] is False and news["leader_round"] is None and news["lab_inbox"] == []
    assert news["jev"]["verdicts"] == []
