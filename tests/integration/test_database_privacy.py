"""New SQLite files remain private without changing existing owner storage."""

import hashlib
import os
import stat

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.operations import doctor_report
from trade_graph.cli import main


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_new_database_and_reopened_sidecars_are_private_under_umask_022(tmp_path):
    path = tmp_path / "private.sqlite"
    previous = os.umask(0o022)
    database = None
    try:
        database = Database(path)
        database.execute("CREATE TABLE retained (value TEXT)")
        database.execute("INSERT INTO retained VALUES ('synthetic-retained-row')")
        for file in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            assert file.is_file()
            assert _mode(file) == 0o600
        assert os.umask(0o022) == 0o022
        database.close()
        database = None
        assert not path.with_name(path.name + "-wal").exists()
        assert not path.with_name(path.name + "-shm").exists()
        database = Database(path)
        assert database.execute("SELECT value FROM retained").fetchone()[0] == "synthetic-retained-row"
        for file in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            assert _mode(file) == 0o600
        assert os.umask(0o022) == 0o022
    finally:
        if database is not None:
            database.close()
        os.umask(previous)


def test_existing_public_database_is_not_replaced_or_chmodded_and_doctor_flags_it(tmp_path, capsys):
    tmp_path.chmod(0o700)
    path = tmp_path / "existing.sqlite"
    assert main(["init", "--database", str(path)]) == 0
    capsys.readouterr()
    path.chmod(0o644)
    inode = path.stat().st_ino
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    database = Database(path)
    try:
        assert database.execute("SELECT count(*) FROM portfolios").fetchone()[0] == 1
        assert database.execute("SELECT count(*) FROM journal_transactions").fetchone()[0] == 1
        assert _mode(path) == 0o644
        assert path.stat().st_ino == inode
    finally:
        database.close()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    report = doctor_report(path)
    assert report["database"]["storage"]["private_directory"] is True
    assert report["database"]["storage"]["private_file"] is False
    assert _mode(path) == 0o644
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_memory_database_creates_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    database = Database(":memory:")
    try:
        assert database.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] > 0
        assert not (tmp_path / ":memory:").exists()
    finally:
        database.close()
