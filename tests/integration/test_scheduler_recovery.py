"""Real SQLite recovery/concurrency regressions; no model, exchange or network calls."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import pytest

from trade_graph.adapters.persistence import migrate
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.scheduler import Scheduler
from trade_graph.domain.clock import FrozenClock, utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState, ValidationFailure

NOW = datetime(2026, 9, 30, tzinfo=UTC)


@pytest.fixture
def stack(tmp_path):
    database = Database(tmp_path / "scheduler.sqlite")
    clock = FrozenClock(NOW)
    scheduler = Scheduler(database, clock)
    yield database, clock, scheduler
    database.close()


def task(scheduler, **updates):
    return scheduler.add_task(**{"role": "trader", "objective": "fixture", "portfolio_id": "p", **updates})


def race(fn):
    barrier = Barrier(2)

    def work(n):
        barrier.wait(timeout=5)
        return fn(n)

    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(work, range(2)))


@pytest.mark.parametrize("reclaimed", [False, True])
def test_exact_portfolio_claim_leaves_other_high_priority_queued_or_expired_work_untouched(stack, reclaimed):
    database, clock, scheduler = stack
    other = task(scheduler, portfolio_id="other-paper")
    database.execute("UPDATE tasks SET priority=100 WHERE task_id=?", (other,))
    if reclaimed:
        scheduler.claim("other-worker", ttl_seconds=1, portfolio_id="other-paper")
        clock.advance(1)
    previous = dict(database.execute("SELECT * FROM tasks WHERE task_id=?", (other,)).fetchone())
    own = task(scheduler, portfolio_id="p")
    lease = scheduler.claim("bounded-runtime", roles={"trader"}, portfolio_id="p")
    assert lease.task_id == own
    assert dict(database.execute("SELECT * FROM tasks WHERE task_id=?", (other,)).fetchone()) == previous
    assert scheduler.claim("bounded-runtime", roles={"trader"}, portfolio_id="p") is None
    unscoped = scheduler.claim("legacy-worker", roles={"trader"})
    assert unscoped.task_id == other and unscoped.reclaimed is reclaimed


def test_portfolio_claim_combines_exact_identity_with_role_filter(stack):
    _, _, scheduler = stack
    task(scheduler, role="research", portfolio_id="p")
    task(scheduler, role="trader", portfolio_id="p-prefix")
    selected = task(scheduler, role="trader", portfolio_id="p")
    assert scheduler.claim("scoped", roles={"trader"}, portfolio_id="p").task_id == selected


@pytest.mark.parametrize("started", [False, True])
def test_expired_work_is_reclaimed_after_database_reopen(stack, started):
    database, clock, scheduler = stack
    identifier = task(scheduler)
    original = scheduler.claim("worker", ttl_seconds=5)
    assert original.task_id == identifier
    if started:
        scheduler.note_attempt(original)
    assert scheduler.claim("other") is None
    clock.advance(5)
    other_database = Database(database.path)
    try:
        recovered = Scheduler(other_database, clock)
        lease = recovered.claim("worker")
        assert lease.task_id == identifier and lease.token != original.token
        assert other_database.execute("SELECT attempts_used FROM tasks").fetchone()[0] == int(started)
        recovered.succeed(lease, {"recovered": True})
    finally:
        other_database.close()
    with pytest.raises(StaleState):
        scheduler.succeed(original, {"stale": True})
    assert json.loads(database.execute("SELECT output_json FROM tasks").fetchone()[0]) == {"recovered": True}


@pytest.mark.parametrize("operation", ["renew", "note_attempt", "succeed"])
@pytest.mark.parametrize("replacement_owner", ["old", "new"])
def test_stale_fencing_token_cannot_mutate_reclaimed_work(stack, operation, replacement_owner):
    _, clock, scheduler = stack
    task(scheduler)
    old = scheduler.claim("old", ttl_seconds=1)
    clock.advance(1)
    new = scheduler.claim(replacement_owner)
    with pytest.raises(StaleState):
        getattr(scheduler, operation)(old, *([{}] if operation == "succeed" else []))
    scheduler.succeed(new, {"ok": True})


def test_expired_lease_cannot_resurrect_itself(stack):
    _, clock, scheduler = stack
    task(scheduler)
    lease = scheduler.claim("a", ttl_seconds=5)
    clock.advance(5)
    with pytest.raises(StaleState):
        scheduler.renew(lease)


def test_renewal_extends_active_lease(stack):
    _, clock, scheduler = stack
    task(scheduler)
    lease = scheduler.claim("a", ttl_seconds=5)
    clock.advance(4)
    scheduler.renew(lease, ttl_seconds=5)
    clock.advance(1)
    assert scheduler.claim("b") is None
    clock.advance(4)
    assert scheduler.claim("b").task_id == lease.task_id


@pytest.mark.parametrize("ttl", [0, -1, True, 0.5])
def test_invalid_lease_duration_is_rejected(stack, ttl):
    _, _, scheduler = stack
    task(scheduler)
    with pytest.raises(ValidationFailure):
        scheduler.claim("a", ttl_seconds=ttl)


def test_attempt_limit_commits_dead_letter_before_raising(stack):
    database, clock, scheduler = stack
    task(scheduler, max_attempts=1)
    lease = scheduler.claim("a")
    scheduler.note_attempt(lease)
    with pytest.raises(ValidationFailure, match="attempt limit"):
        scheduler.note_attempt(lease)
    other = Database(database.path)
    try:
        row = other.execute("SELECT * FROM tasks").fetchone()
        assert row["status"] == "DEAD_LETTER" and row["attempts_used"] == 1
        assert row["lease_token"] is None and row["lease_owner"] is None
        clock.advance(60)
        assert Scheduler(other, clock).claim("b") is None
    finally:
        other.close()


def test_concurrent_workers_cannot_claim_the_same_task(stack):
    _, _, scheduler = stack
    identifier = task(scheduler)
    leases = race(lambda n: scheduler.claim(str(n)))
    assert [lease.task_id for lease in leases if lease] == [identifier]


def test_waiting_external_and_future_work_are_not_retried(stack):
    database, clock, scheduler = stack
    identifier = task(scheduler)
    database.execute("UPDATE tasks SET status = 'WAITING_EXTERNAL', lease_expires_at = ?", (utc_iso(NOW),))
    task(scheduler, due_at="2026-10-01T00:00:00.000000Z")
    clock.advance(1)
    assert scheduler.claim("a") is None
    status = database.execute("SELECT status FROM tasks WHERE task_id = ?", (identifier,)).fetchone()[0]
    assert status == "WAITING_EXTERNAL"


def test_success_is_terminal(stack):
    _, _, scheduler = stack
    task(scheduler)
    lease = scheduler.claim("a")
    scheduler.succeed(lease, {"result": 1})
    with pytest.raises(StaleState):
        scheduler.succeed(lease, {"result": 2})
    assert scheduler.claim("a") is None


def test_higher_priority_due_task_is_claimed_first(stack):
    database, _, scheduler = stack
    task(scheduler)
    high = task(scheduler)
    database.execute("UPDATE tasks SET priority = 10 WHERE task_id = ?", (high,))
    assert scheduler.claim("a").task_id == high


def test_subhourly_occurrences_are_distinct(stack):
    _, clock, scheduler = stack
    scheduler.ensure_schedule("p", "five-minutes", 300, "coalesce")
    first = scheduler.coalesce_due("p", "five-minutes", "trader")
    assert scheduler.coalesce_due("p", "five-minutes", "trader") is None
    clock.advance(300)
    second = scheduler.coalesce_due("p", "five-minutes", "trader")
    assert first and second and first != second


def test_missed_occurrences_coalesce_into_one_current_task(stack):
    database, clock, scheduler = stack
    scheduler.ensure_schedule("p", "five-minutes", 300, "coalesce")
    clock.advance(86400)
    assert scheduler.coalesce_due("p", "five-minutes", "trader")
    assert scheduler.coalesce_due("p", "five-minutes", "trader") is None
    assert database.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
    assert database.execute("SELECT due_at FROM tasks").fetchone()[0] == scheduler.now()


def test_schedule_and_task_commit_or_rollback_together(stack):
    database, _, scheduler = stack
    scheduler.ensure_schedule("p", "routine", 300, "coalesce")
    before = dict(database.execute("SELECT * FROM schedules").fetchone())
    database.execute("CREATE TRIGGER fail_schedule BEFORE UPDATE ON schedules BEGIN SELECT RAISE(ABORT, 'test'); END")
    with pytest.raises(sqlite3.IntegrityError):
        scheduler.coalesce_due("p", "routine", "trader")
    assert database.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert dict(database.execute("SELECT * FROM schedules").fetchone()) == before
    database.execute("DROP TRIGGER fail_schedule")
    assert scheduler.coalesce_due("p", "routine", "trader")


def test_schedule_creation_and_coalescing_are_serialized(stack):
    database, _, scheduler = stack
    race(lambda _: scheduler.ensure_schedule("p", "routine", 300, "coalesce"))
    assert database.execute("SELECT COUNT(*) FROM schedules").fetchone()[0] == 1
    results = race(lambda _: scheduler.coalesce_due("p", "routine", "trader"))
    assert sum(result is not None for result in results) == 1
    assert database.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


@pytest.mark.parametrize("portfolio", [None, "p"])
def test_global_and_portfolio_tasks_deduplicate_under_race(stack, portfolio):
    database, _, scheduler = stack
    results = race(lambda _: task(scheduler, portfolio_id=portfolio, dedup_key="once"))
    assert results[0] == results[1]
    assert database.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


def test_children_inherit_budget_root_and_cannot_switch_scope(stack):
    database, _, scheduler = stack
    root = task(scheduler)
    child = task(scheduler, parent_id=root)
    assert database.execute("SELECT root_task_id FROM tasks WHERE task_id = ?", (child,)).fetchone()[0] == root
    with pytest.raises(AuthorityDenied):
        task(scheduler, parent_id=child, root_task_id="different")
    with pytest.raises(AuthorityDenied):
        task(scheduler, parent_id=child, portfolio_id="other")
    with pytest.raises(ValidationFailure):
        task(scheduler, parent_id="missing")
    with pytest.raises(ValidationFailure):
        task(scheduler, root_task_id=root)


def test_descendant_cap_counts_children_not_root_and_is_atomic(stack):
    database, _, scheduler = stack
    root = task(scheduler)
    for n in range(11):
        task(scheduler, parent_id=root, dedup_key=str(n))

    def attempt(n):
        try:
            return task(scheduler, parent_id=root, dedup_key=f"race-{n}")
        except AuthorityDenied:
            return None

    results = race(attempt)
    assert sum(result is not None for result in results) == 1
    assert database.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 13  # root + 12 children
    assert task(scheduler, parent_id=root, dedup_key="0")  # idempotent retry still works at the cap


def test_depth_limit_still_applies(stack):
    _, _, scheduler = stack
    parent = task(scheduler)
    for _ in range(3):
        parent = task(scheduler, parent_id=parent)
    with pytest.raises(AuthorityDenied):
        task(scheduler, parent_id=parent)


def test_additive_migration_preserves_old_tasks_and_is_repeatable(tmp_path):
    path = tmp_path / "legacy.sqlite"
    connection = sqlite3.connect(path, isolation_level=None)
    for statement in migrate.STATEMENTS[0][1]:
        connection.execute(statement)
    connection.execute("INSERT INTO schema_migrations VALUES ('0001', ?)", (utc_iso(NOW),))
    connection.execute("""INSERT INTO tasks (task_id, root_task_id, role, objective, status, priority,
        max_steps, max_attempts, attempts_used, input_json, created_at, lease_owner, lease_expires_at)
        VALUES ('old', 'old', 'trader', 'recover', 'RUNNING', 0, 3, 3, 1, '{}', ?, 'old-worker', ?)""",
                       (utc_iso(NOW), utc_iso(NOW)))
    connection.close()
    for _ in range(2):
        database = Database(path)
        assert database.execute("SELECT attempts_used FROM tasks WHERE task_id = 'old'").fetchone()[0] == 1
        assert migrate.applied_versions(database.connection) == {
            "0001", "0002", "0003", "0004", "0005", "0006", "0007", "0008", "0009", "0010", "0011", "0012",
        }
        database.close()
    database = Database(path)
    try:
        scheduler = Scheduler(database, FrozenClock(NOW))
        assert scheduler.claim("new").task_id == "old"
    finally:
        database.close()


@pytest.mark.parametrize("nested", [False, True])
def test_failed_migration_rolls_back_schema_and_version_together(tmp_path, monkeypatch, nested):
    connection = sqlite3.connect(tmp_path / "atomic.sqlite", isolation_level=None)
    if nested:
        connection.execute("BEGIN")
        connection.execute("CREATE TABLE caller_owned (value TEXT)")
    monkeypatch.setattr(migrate, "STATEMENTS", [*migrate.STATEMENTS, ("bad", [
        "CREATE TABLE partial_schema (value TEXT)", "INSERT INTO missing_table VALUES (1)",
    ])])
    with pytest.raises(sqlite3.OperationalError):
        migrate.apply_migrations(connection, utc_iso(NOW))
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == ({"caller_owned"} if nested else set())
    assert connection.in_transaction is nested
    if nested:
        connection.execute("ROLLBACK")
    connection.close()
