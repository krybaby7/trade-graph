"""The exclusive lease lives on a sidecar file, so it never collides with SQLite's own POSIX locks.

On Linux flock() and fcntl() locks are independent; on macOS and the BSDs they share one
table, so a flock on the database inode fails whenever SQLite holds a lock in the same process.
"""

import os
import stat

from trade_graph.adapters.persistence.db import Database


def test_exclusive_lock_holds_a_private_sidecar_file_not_the_database_inode(tmp_path):
    database = Database(tmp_path / "ledger.sqlite")
    try:
        with database.exclusive_lock("controller") as descriptor:
            held = os.fstat(descriptor)
            main = database.path.stat()
            assert (held.st_dev, held.st_ino) != (main.st_dev, main.st_ino)
            sidecar = tmp_path / "ledger.sqlite.lock"
            assert sidecar.is_file() and not sidecar.is_symlink()
            assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600
            assert (sidecar.stat().st_dev, sidecar.stat().st_ino) == (held.st_dev, held.st_ino)
    finally:
        database.close()


def test_exclusive_lock_coexists_with_an_open_sqlite_write_transaction(tmp_path):
    database = Database(tmp_path / "ledger.sqlite")
    try:
        database.connection.execute("BEGIN IMMEDIATE")
        try:
            with database.exclusive_lock("controller"):
                pass
        finally:
            database.connection.rollback()
    finally:
        database.close()
