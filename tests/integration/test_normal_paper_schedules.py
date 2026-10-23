"""Normal recurring paper work preserves control, journal and scheduling history."""
import asyncio

from tests.integration.test_execution import _stack

from trade_graph.application.paper_service import PaperService
from trade_graph.application.scheduler import Scheduler


def test_optimisation_and_research_recur_without_manual_request(tmp_path):
    clock, ledger, execution, broker, pid = _stack(tmp_path)
    seen = []
    service = PaperService(ledger.database, execution,
        handlers={role: lambda task: seen.append(task["role"]) or {} for role in ("research", "optimisation")},
        schedule_intervals={"research": 60, "optimisation": 60})
    async def scenario():
        first = await service.tick(wait_roles=True)
        assert first.scheduled == 2 and first.completed == 1
        await service.tick(wait_roles=True)
        clock.advance(60)
        second = await service.tick(wait_roles=True)
        assert second.scheduled == 2
        await service.tick(wait_roles=True)
        await service.stop()
    asyncio.run(scenario())
    assert seen.count("research") == seen.count("optimisation") == 2
    assert broker.submit_count == 0
    assert execution.profile(pid) == "RUNNING"


def test_start_preserves_preexisting_automatic_tree_and_receipts(tmp_path):
    clock, ledger, execution, _broker, pid = _stack(tmp_path)
    scheduler = Scheduler(ledger.database, clock)
    parent = scheduler.add_task(role="optimisation", objective="automatic-review", portfolio_id=pid)
    child = scheduler.add_task(role="leader", objective="followup", parent_id=parent, portfolio_id=pid)
    ledger.database.execute("UPDATE tasks SET output_json=? WHERE task_id=?",
        ('{"retained":"uncertain evidence"}', child))
    service = PaperService(ledger.database, execution, handlers={}, schedule_intervals={})
    async def scenario():
        await service.start()
        await service.stop()
    asyncio.run(scenario())
    rows = ledger.database.execute("SELECT status FROM tasks").fetchall()
    assert {row[0] for row in rows} == {"QUEUED"}
    saved = ledger.database.execute("SELECT output_json FROM tasks WHERE task_id=?", (child,)).fetchone()[0]
    assert saved == '{"retained":"uncertain evidence"}'


def test_manual_only_is_explicit_configuration_not_default(tmp_path):
    clock, ledger, execution, _broker, pid = _stack(tmp_path)
    service = PaperService(ledger.database, execution, handlers={"optimisation": lambda task: {}},
        schedule_intervals={"optimisation": 60}, optimisation_manual_only=True)
    async def scenario():
        result = await service.tick(wait_roles=True)
        assert result.scheduled == result.completed == 0
        await service.stop()
    asyncio.run(scenario())
    assert ledger.database.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert execution.profile(pid) == "RUNNING"


def test_automatic_schedule_switch_preserves_queued_work(tmp_path):
    clock, ledger, execution, _broker, pid = _stack(tmp_path)
    scheduler = Scheduler(ledger.database, clock)
    existing = scheduler.add_task(role="research", objective="owner-queued", portfolio_id=pid)
    seen = []
    service = PaperService(ledger.database, execution, handlers={"research": lambda task: seen.append(task) or {}},
        schedule_intervals={"research": 1}, automatic_schedule=False)
    async def scenario():
        tick = await service.tick(wait_roles=True)
        assert tick.scheduled == 0 and tick.completed == 1
        await service.stop()
    asyncio.run(scenario())
    assert [task["task_id"] for task in seen] == [existing]
    assert ledger.database.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


def test_subscription_schedules_and_material_leader_routes_do_not_allocate_api_money(tmp_path):
    clock, ledger, execution, _broker, pid = _stack(tmp_path)
    service = PaperService(ledger.database, execution,
        handlers={role: lambda task: {} for role in ("research", "leader", "optimisation")},
        schedule_intervals={"research": 60, "leader": 60, "optimisation": 60},
        subscription_provider="claude_subscription")
    ledger._activity(pid, "execution_failed", {"synthetic": True})
    before_budget = [tuple(row) for row in ledger.database.execute("SELECT * FROM deployment_budget")]
    async def scenario():
        await service.tick(wait_roles=True)
        await service.stop()
    asyncio.run(scenario())
    rows = ledger.database.execute("SELECT allocated_spend FROM tasks").fetchall()
    assert rows and all(row[0] == "0" for row in rows)
    assert before_budget == [tuple(row) for row in ledger.database.execute("SELECT * FROM deployment_budget")]


def test_admitted_router_readiness_allows_verified_fallback_from_paused_primary(tmp_path):
    clock, ledger, execution, _broker, pid = _stack(tmp_path)
    ledger.database.execute("INSERT INTO subscription_provider_state VALUES (?,?,?,?,?)",
        ("claude_subscription", 1, "subscription quota exhausted", "{}", "2026-10-07T00:00:00Z"))
    seen = []
    service = PaperService(ledger.database, execution,
        handlers={"research": lambda task: seen.append(task) or {}}, schedule_intervals={"research": 60},
        subscription_provider="claude_subscription", runtime_ready=lambda: True)
    async def scenario():
        tick = await service.tick(wait_roles=True)
        assert tick.scheduled == tick.completed == 1
        await service.stop()
    asyncio.run(scenario())
    assert len(seen) == 1
