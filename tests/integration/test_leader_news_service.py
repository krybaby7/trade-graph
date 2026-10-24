"""The Leader's news rounds inside the paper service with the event engine (T38 on T35).

The service wakes the Leader at the day start, the round runs through the real worker, and its Trader note wakes the
Trader at the next step. Under an owner halt the Leader's wake waits and nothing is sent. Synthetic account, scripted
Perplexity answers (made-up key) and scripted Research and Trader; no network, real key, paid call or real order.
"""

from __future__ import annotations

import asyncio

from tests.leader_news_support import LeaderSetup, full_round
from tests.wake_support import scripted

from trade_graph.application.events import EngineOptions
from trade_graph.application.paper_service import PaperService


def _service(setup: LeaderSetup, handlers) -> PaperService:
    service = PaperService(setup.database, setup.account.execution, clock=setup.clock,
                           portfolio_ids=[setup.portfolio], handlers=handlers, event_engine=EngineOptions())
    setup.rounds.engine = service.event_engine  # the service makes its own engine; the brief reads from it
    return service


def _ticks(service: PaperService, count: int) -> None:
    async def run():
        for _ in range(count):
            service.clock.advance(1)
            result = await service.tick(wait_roles=True)
            assert not [failure for failure in result.failures if failure.startswith("wakes")], result.failures
        await service.stop()

    asyncio.run(run())


def test_the_service_wakes_the_leader_for_a_round_and_its_note_wakes_the_trader(tmp_path) -> None:
    setup = LeaderSetup(tmp_path, full_round())
    seen: list = []
    service = _service(setup, {"leader": setup.rounds, "research": scripted({"summary": "Synthetic."}),
                               "trader": scripted({"summary": "Synthetic."}, seen=seen)})
    _ticks(service, 8)
    [record] = setup.rounds.store.rounds(setup.portfolio)
    assert record.status == "completed" and len(record.receipt_ids) == 5
    assert record.brief["awake_departments"] == ["leader", "research", "trader"]
    [note] = [request for request in record.requests if request.kind == "trader_note"]
    notes = [trigger["note"] for task in seen for trigger in task["input"]["wake"]["triggers"]
             if trigger["kind"] == "wake_request"]
    assert notes == [note.note]
    # The wake's task carries the service's allocation; the round's reservations fit inside it.
    [task] = setup.rows("SELECT allocated_spend FROM tasks WHERE task_id = ?", record.task_id)
    assert task["allocated_spend"] == str(setup.account.execution.authority.active_policy().root_paid_limit.amount)
    assert setup.account.execution.pause(setup.portfolio) is None


def test_under_an_owner_halt_the_leaders_wake_waits_and_nothing_is_sent(tmp_path) -> None:
    setup = LeaderSetup(tmp_path, full_round())
    setup.account.execution.set_pause(setup.portfolio, "MANAGE_ONLY", "owner", "synthetic owner halt")
    service = _service(setup, {"leader": setup.rounds})
    _ticks(service, 3)
    assert setup.transport.calls == [] and setup.rounds.store.rounds(setup.portfolio) == []
    [wake] = setup.engine.wakes(setup.portfolio, department="leader")
    assert wake.status == "queued"
