"""POSIX SQLite locks survive auxiliary inode ownership and identity handles."""

import errno
import fcntl
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from tests.integration.test_execution import _stack
from tests.integration.test_financial_checkpoint import prepared
from tests.integration.test_service_controller import runtime_stack

from trade_graph.adapters.persistence.db import Database, exclusive_database_path
from trade_graph.application.paper_service import PaperService
from trade_graph.application.service_controller import ServiceController
from trade_graph.domain.errors import StaleState


def reserved_lock_available(path):
    probe = """
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR)
try:
    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, 1073741825, os.SEEK_SET)
    except BlockingIOError:
        print('blocked')
    else:
        print('available')
finally:
    os.close(fd)
"""
    result = subprocess.run([sys.executable, "-c", probe, str(path)], check=True, capture_output=True, text=True)
    return result.stdout.strip() == "available"


@contextmanager
def sqlite_reserved(database):
    database.connection.execute("PRAGMA journal_mode=DELETE")
    database.connection.execute("BEGIN IMMEDIATE")
    try:
        assert not reserved_lock_available(database.path)
        yield
    finally:
        database.connection.rollback()


def test_status_probe_does_not_drop_existing_sqlite_reserved_lock(tmp_path):
    runtime, _broker = runtime_stack(tmp_path)
    with sqlite_reserved(runtime.database):
        ServiceController(runtime).status()
        assert not reserved_lock_available(runtime.database.path)


def test_service_release_unlocks_flock_without_dropping_sqlite_reserved_lock(tmp_path):
    _clock, ledger, execution, _broker, _portfolio = _stack(tmp_path)
    service = PaperService(ledger.database, execution, schedule_intervals={})
    service._acquire()
    with sqlite_reserved(ledger.database):
        service._release()
        assert not reserved_lock_available(ledger.database.path)


def test_protected_controller_release_preserves_sqlite_reserved_lock(tmp_path):
    database, _clock, _ledger, _execution, _broker, _portfolio, runtime = prepared(tmp_path)
    database.connection.execute("PRAGMA journal_mode=DELETE")
    with runtime._exclusive_controller():
        database.connection.execute("BEGIN IMMEDIATE")
    try:
        assert not reserved_lock_available(database.path)
    finally:
        database.connection.rollback()


def test_identity_probe_preserves_sqlite_reserved_lock(tmp_path):
    database = Database(tmp_path / "identity.sqlite")
    assert callable(getattr(database, "file_identity", None))
    with sqlite_reserved(database):
        assert database.file_identity() == [database.path.stat().st_dev, database.path.stat().st_ino]
        assert not reserved_lock_available(database.path)
    database.close()


def test_raw_descriptors_outlive_root_close_until_worker_sqlite_connection_closes(tmp_path):
    database = Database(tmp_path / "worker.sqlite")
    assert callable(getattr(database, "retained_descriptor", None))
    descriptor = database.retained_descriptor("synthetic-lifetime")
    database.connection.execute("PRAGMA journal_mode=DELETE")
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        database.close()
        assert fcntl.fcntl(descriptor, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
        assert not reserved_lock_available(database.path)
        connection.rollback()
    with pytest.raises(OSError) as failure:
        os.fstat(descriptor)
    assert failure.value.errno == errno.EBADF


def test_raw_close_waits_for_sqlite_connections_in_other_database_owner(tmp_path):
    first = Database(tmp_path / "shared.sqlite")
    # Establish rollback-journal mode before reopening the second SQLite
    # connection; changing WAL mode with two active handles is itself locked.
    first.connection.close()
    second = Database(first.path)
    second.connection.execute("PRAGMA journal_mode=DELETE")
    first._connection = first._open()
    assert callable(getattr(first, "retained_descriptor", None))
    descriptor = first.retained_descriptor("synthetic-first-owner")
    second.connection.execute("BEGIN IMMEDIATE")
    try:
        first.close()
        assert not reserved_lock_available(second.path)
        os.fstat(descriptor)
    finally:
        second.connection.rollback()
        second.close()
    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_distinct_owners_purposes_and_nested_leases_cannot_share_exclusive_flock(tmp_path):
    first = Database(tmp_path / "exclusive.sqlite")
    second = Database(first.path)
    assert callable(getattr(first, "exclusive_lock", None))
    with first.exclusive_lock("controller"):
        for owner, purpose in ((first, "controller"), (first, "other"), (second, "controller")):
            with pytest.raises(StaleState):
                with owner.exclusive_lock(purpose):
                    pytest.fail("two controllers acquired the same database")
    with second.exclusive_lock("controller"):
        pass
    first.close()
    second.close()


@pytest.mark.parametrize("attack", ["leaf_symlink", "parent_symlink", "hardlink", "replacement"])
def test_cached_identity_rechecks_no_follow_path_and_single_inode(tmp_path, attack):
    directory = tmp_path / "storage"
    directory.mkdir()
    database = Database(directory / "identity.sqlite")
    assert callable(getattr(database, "file_identity", None))
    database.file_identity()
    saved = tmp_path / "saved"
    linked = tmp_path / "linked.sqlite"
    if attack == "parent_symlink":
        directory.rename(saved)
        directory.symlink_to(saved, target_is_directory=True)
    elif attack == "hardlink":
        os.link(database.path, linked)
    else:
        database.path.rename(saved)
        if attack == "leaf_symlink":
            database.path.symlink_to(saved)
        else:
            database.path.touch(mode=0o600)
    try:
        with pytest.raises(StaleState):
            database.file_identity()
    finally:
        if attack == "parent_symlink":
            directory.unlink()
            saved.rename(directory)
        elif attack == "hardlink":
            linked.unlink()
        else:
            database.path.unlink()
            saved.rename(database.path)
        database.close()


def test_partially_initialized_connection_finalizer_does_not_raise():
    from trade_graph.adapters.persistence.db import _TrackedConnection

    connection = _TrackedConnection.__new__(_TrackedConnection)
    connection.__del__()


def test_pre_migration_path_lease_preserves_other_sqlite_connection_locks(tmp_path):
    database = Database(tmp_path / "before-migration.sqlite")
    with sqlite_reserved(database):
        with exclusive_database_path(database.path, "pre-migration"):
            with pytest.raises(StaleState):
                with database.exclusive_lock("controller"):
                    pytest.fail("pre-migration ownership was bypassed")
        assert not reserved_lock_available(database.path)
    database.close()


def test_same_instance_cross_thread_exclusive_lease_is_not_reentrant(tmp_path):
    database = Database(tmp_path / "thread-exclusive.sqlite")
    def contender():
        with pytest.raises(StaleState):
            with database.exclusive_lock("controller"):
                pytest.fail("another thread reused an owned file description")
    with database.exclusive_lock("controller"):
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(contender).result(timeout=5)
    database.close()


def test_failed_acquisition_race_retains_opened_main_file_descriptor(tmp_path, monkeypatch):
    database = Database(tmp_path / "raced.sqlite")
    saved = tmp_path / "saved.sqlite"
    original_open = os.open
    def raced_open(path, flags, *args, **kwargs):
        descriptor = original_open(path, flags, *args, **kwargs)
        if path == database.path.name:
            database.path.rename(saved)
            database.path.touch(mode=0o600)
        return descriptor
    with sqlite_reserved(database):
        with monkeypatch.context() as patch:
            patch.setattr(os, "open", raced_open)
            try:
                with pytest.raises(StaleState):
                    database.retained_descriptor("raced-acquisition")
            finally:
                database.path.unlink()
                saved.rename(database.path)
        assert not reserved_lock_available(database.path)
    database.close()


def test_close_waits_for_in_progress_sqlite_connection_registration(tmp_path, monkeypatch):
    import sqlite3

    database = Database(tmp_path / "opening.sqlite")
    descriptor = database.retained_descriptor("opening-race")
    entered, finish = threading.Event(), threading.Event()
    original_connect = sqlite3.connect
    def delayed_connect(*args, **kwargs):
        entered.set()
        assert finish.wait(5)
        return original_connect(*args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", delayed_connect)
    with ThreadPoolExecutor(max_workers=2) as pool:
        opening = pool.submit(database._open)
        assert entered.wait(5)
        close_started, close_finished = threading.Event(), threading.Event()
        def close_database():
            close_started.set()
            database.close()
            close_finished.set()
        closing = pool.submit(close_database)
        try:
            assert close_started.wait(5)
            assert not close_finished.wait(0.05)
        finally:
            finish.set()
        connection = opening.result(timeout=5)
        closing.result(timeout=5)
    os.fstat(descriptor)
    connection.close()
    with pytest.raises(OSError):
        os.fstat(descriptor)
