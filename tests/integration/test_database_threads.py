"""Idle worker connections preserve SQLite transaction and snapshot boundaries."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from trade_graph.adapters.persistence.db import Database


def test_worker_idle_connection_is_distinct_and_closes_on_failure(tmp_path):
    database = Database(tmp_path / "threads.sqlite")
    main = database.connection

    def worker():
        with pytest.raises(ValueError):
            with database.thread_connection() as connection:
                assert database.connection is connection
                assert connection is not main
                assert not connection.in_transaction
                with database.thread_connection() as nested:
                    assert nested is connection
                database.execute("BEGIN")
                database.execute("CREATE TABLE should_rollback (id TEXT)")
                raise ValueError("interrupted")
        assert database.connection is main
        with pytest.raises(Exception, match="closed database"):
            connection.execute("SELECT 1")

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(worker).result()
    assert database.execute("SELECT name FROM sqlite_master WHERE name = 'should_rollback'").fetchone() is None


def test_worker_connection_keeps_short_transactions_and_consistent_snapshot(tmp_path):
    database = Database(tmp_path / "threads.sqlite")
    database.execute("CREATE TABLE thread_values (id TEXT PRIMARY KEY)")

    def worker():
        with database.thread_connection() as idle:
            with database.immediate() as transaction:
                assert database.connection is transaction
                assert transaction is not idle
                database.execute("INSERT INTO thread_values VALUES ('committed')")
                with pytest.raises(ValueError):
                    with database.immediate():
                        database.execute("INSERT INTO thread_values VALUES ('rolled-back')")
                        raise ValueError("rollback nested savepoint")
            assert database.connection is idle
            with database.snapshot() as snapshot:
                assert snapshot is not idle
                assert database.execute("SELECT count(*) FROM thread_values").fetchone()[0] == 1
                with database._open() as concurrent:
                    concurrent.execute("INSERT INTO thread_values VALUES ('after-snapshot')")
                assert database.execute("SELECT count(*) FROM thread_values").fetchone()[0] == 1
            assert database.connection is idle

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(worker).result()
    assert database.execute("SELECT count(*) FROM thread_values").fetchone()[0] == 2


def test_thread_binding_does_not_replace_an_active_transaction(tmp_path):
    database = Database(tmp_path / "threads.sqlite")
    with database.immediate() as transaction:
        with database.thread_connection() as connection:
            assert connection is transaction
            assert database.connection is transaction
        assert database.connection is transaction
