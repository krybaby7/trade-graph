"""The owner's protected switch that moves Research and the Leader to Perplexity (T37, T38; D124, D125, D138 item 4).

It ships switched off. Only the owner command ``trade-graph perplexity-departments`` records it: a preview by
default, a record only with ``--confirm``, refused while the paper service runs, revision-checked, and never
rewriting an earlier setting. ``perplexity_enabled_for(database, department)`` is the one read the departments use
(Research here, the Leader in W6-B). Synthetic databases only; no network, model, key or paid call.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.authority import AuthorityRecord, paper_mandate, paper_owner_policy
from trade_graph.application.ledger import Ledger
from trade_graph.application.perplexity_switch import (
    perplexity_enabled_for,
    perplexity_setting,
    propose_perplexity_departments,
    record_perplexity_departments,
)
from trade_graph.cli import main
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import AuthorityDenied, StaleState, ValidationFailure

D = Decimal
CLOCK = FrozenClock(datetime(2026, 10, 16, 9, 0, tzinfo=UTC))


def _database(tmp_path) -> str:
    folder = tmp_path / "runtime"
    folder.mkdir(mode=0o700)
    path = folder / "trade_graph.sqlite"
    database = Database(path)
    ledger = Ledger(database, CLOCK)
    portfolio = ledger.create_portfolio(reporting_currency="EUR")
    record = AuthorityRecord(database, CLOCK)
    record.install_policy(paper_owner_policy(revision_id="initial-paper-policy"), role="owner")
    record.install_mandate(paper_mandate(portfolio), role="owner")
    database.close()
    return str(path)


def _open(path: str) -> Database:
    return Database(path)


def _run(capsys, *arguments) -> dict:
    assert main(["perplexity-departments", *arguments]) == 0
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _rows(path: str) -> list[dict]:
    database = _open(path)
    try:
        return [dict(row) for row in database.execute(
            "SELECT * FROM perplexity_department_settings ORDER BY sequence")]
    finally:
        database.close()


def test_it_ships_switched_off_even_in_a_database_from_before_the_switch(tmp_path) -> None:
    path = _database(tmp_path)
    database = _open(path)
    try:
        assert perplexity_setting(database).revision_id is None
        assert perplexity_enabled_for(database, "research") is False
        assert perplexity_enabled_for(database, "leader") is False
        with pytest.raises(ValidationFailure):
            perplexity_enabled_for(database, "trader")  # the Trader stays on its subscription (D124)
    finally:
        database.close()
    older = sqlite3.connect(tmp_path / "older.sqlite")  # no 0038 tables at all
    try:
        assert perplexity_enabled_for(older, "research") is False
        assert perplexity_setting(older).spend_policy is None
    finally:
        older.close()


def test_a_preview_writes_nothing_and_a_confirm_records_one_revision(tmp_path) -> None:
    path = _database(tmp_path)
    database = _open(path)
    try:
        preview = propose_perplexity_departments(database, research=True, leader=True, monthly_limit_usd=None,
                                                 revision_id="perplexity-1")
        assert preview.current.revision_id is None and preview.proposed.revision_id == "perplexity-1"
        assert set(preview.changes) == {"research", "leader", "spend"}
        assert database.execute("SELECT COUNT(*) FROM perplexity_department_settings").fetchone()[0] == 0
        recorded = record_perplexity_departments(database, CLOCK, research=True, leader=True,
                                                 monthly_limit_usd=None, revision_id="perplexity-1")
        assert recorded.proposed is not None
        setting = perplexity_setting(database)
        assert (setting.revision_id, setting.research, setting.leader) == ("perplexity-1", True, True)
        # D125 and D138 item 4: no money limit while testing; every call still has a reservation and receipt.
        assert setting.spend_policy.mode == "unlimited_testing" and setting.spend_policy.decision_ref == "D125"
        assert perplexity_enabled_for(database, "research") and perplexity_enabled_for(database, "leader")
    finally:
        database.close()
    (row,) = _rows(path)
    assert (row["set_by"], row["command"], row["previous_revision_id"]) == ("owner", "perplexity-departments", None)


def test_later_settings_add_revisions_and_keep_the_history(tmp_path) -> None:
    path = _database(tmp_path)
    database = _open(path)
    try:
        record_perplexity_departments(database, CLOCK, research=True, leader=False, monthly_limit_usd=None,
                                      revision_id="perplexity-1")
        same = record_perplexity_departments(database, CLOCK, research=True, leader=False, monthly_limit_usd=None,
                                             revision_id="perplexity-2")
        assert same.proposed is None and same.changes == {}  # nothing changed, nothing recorded
        limited = record_perplexity_departments(database, CLOCK, research=True, leader=True,
                                                monthly_limit_usd=D("30"), revision_id="perplexity-3",
                                                expect_revision="perplexity-1")
        assert set(limited.changes) == {"leader", "spend"}
        setting = perplexity_setting(database)
        assert setting.spend_policy.mode == "monthly_limit" and setting.spend_policy.monthly_limit_usd == D("30")
        record_perplexity_departments(database, CLOCK, research=False, leader=False, monthly_limit_usd=None,
                                      revision_id="perplexity-4")
        assert not perplexity_enabled_for(database, "research") and perplexity_setting(database).spend_policy is None
    finally:
        database.close()
    assert [(row["revision_id"], row["previous_revision_id"], row["research"], row["leader"])
            for row in _rows(path)] == [("perplexity-1", None, 1, 0), ("perplexity-3", "perplexity-1", 1, 1),
                                        ("perplexity-4", "perplexity-3", 0, 0)]


def test_only_the_owner_records_it_with_the_service_stopped_and_the_previewed_revision(tmp_path) -> None:
    path = _database(tmp_path)
    database = _open(path)
    try:
        for role in ("leader", "research", "trader", "engineer"):
            with pytest.raises(AuthorityDenied):
                record_perplexity_departments(database, CLOCK, research=True, leader=True, monthly_limit_usd=None,
                                              revision_id=f"by-{role}", role=role)
        with pytest.raises(StaleState, match="changed since the preview"):
            record_perplexity_departments(database, CLOCK, research=True, leader=True, monthly_limit_usd=None,
                                          revision_id="perplexity-1", expect_revision="perplexity-0")
        with pytest.raises(ValidationFailure):
            record_perplexity_departments(database, CLOCK, research=True, leader=True, monthly_limit_usd=D("0"),
                                          revision_id="perplexity-1")
        database.execute("INSERT INTO process_leases (lease_name, owner, expires_at) VALUES (?, ?, ?)",
                         ("paper-service", "boot-1", "2999-01-01T00:00:00.000000Z"))
        with pytest.raises(StaleState, match="running"):
            record_perplexity_departments(database, CLOCK, research=True, leader=True, monthly_limit_usd=None,
                                          revision_id="perplexity-1")
        assert database.execute("SELECT COUNT(*) FROM perplexity_department_settings").fetchone()[0] == 0
    finally:
        database.close()


def test_an_edited_record_reads_as_switched_off(tmp_path) -> None:
    path = _database(tmp_path)
    database = _open(path)
    try:
        record_perplexity_departments(database, CLOCK, research=True, leader=False, monthly_limit_usd=None,
                                      revision_id="perplexity-1")
    finally:
        database.close()
    connection = sqlite3.connect(path)
    connection.execute("DROP TRIGGER perplexity_department_settings_no_update")
    connection.execute("UPDATE perplexity_department_settings SET leader = 1, "
                       "document_json = replace(document_json, '\"leader\":false', '\"leader\":true')")
    connection.commit()
    connection.close()
    database = _open(path)
    try:
        assert perplexity_enabled_for(database, "leader") is False  # fails closed
        assert perplexity_enabled_for(database, "research") is False
        with pytest.raises(StaleState, match="hash"):
            perplexity_setting(database)
    finally:
        database.close()


def test_a_read_only_connection_can_read_the_switch(tmp_path) -> None:
    path = _database(tmp_path)
    database = _open(path)
    try:
        record_perplexity_departments(database, CLOCK, research=True, leader=False, monthly_limit_usd=None,
                                      revision_id="perplexity-1")
    finally:
        database.close()
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        assert perplexity_enabled_for(connection, "research") is True
        assert perplexity_enabled_for(connection, "leader") is False
    finally:
        connection.close()


def test_the_owner_command_previews_by_default_and_records_with_confirm(tmp_path, capsys) -> None:
    path = _database(tmp_path)
    preview = _run(capsys, "--database", path)
    assert preview["applied"] is False and preview["new_revision"] is None and preview["current_revision"] is None
    assert preview["current"] == {"research": False, "leader": False, "spend": None}
    assert preview["proposed"]["research"] is True and preview["proposed"]["leader"] is True
    assert preview["proposed"]["spend"]["mode"] == "unlimited_testing"
    assert preview["live_enabled"] is False and "receipt" in preview["paid_calls"]
    assert _rows(path) == []
    applied = _run(capsys, "--database", path, "--confirm")
    assert applied["applied"] is True and applied["new_revision"].startswith("perplexity-")
    (row,) = _rows(path)
    assert row["revision_id"] == applied["new_revision"] and (row["research"], row["leader"]) == (1, 1)
    again = _run(capsys, "--database", path, "--confirm")
    assert again["applied"] is False and again["changes"] == {}
    off = _run(capsys, "--database", path, "--leader", "off", "--monthly-limit-usd", "30",
               "--expect-revision", applied["new_revision"], "--confirm")
    assert off["applied"] is True and off["proposed"]["leader"] is False
    assert off["proposed"]["spend"] == {"mode": "monthly_limit", "monthly_limit_usd": "30", "decision_ref": "D138"}
    assert len(_rows(path)) == 2


def test_the_owner_command_refuses_a_running_service_a_bad_limit_and_a_missing_database(tmp_path, capsys) -> None:
    path = _database(tmp_path)
    database = _open(path)
    database.execute("INSERT INTO process_leases (lease_name, owner, expires_at) VALUES (?, ?, ?)",
                     ("role-worker", "boot-1", "2999-01-01T00:00:00.000000Z"))
    database.close()
    assert _run(capsys, "--database", path)["applied"] is False  # a preview is still fine
    with pytest.raises(SystemExit) as refused:
        main(["perplexity-departments", "--database", path, "--confirm"])
    assert refused.value.code == 2 and "running" in capsys.readouterr().err
    for limit in ("0", "-5", "lots", "NaN"):
        with pytest.raises(SystemExit) as refused:
            main(["perplexity-departments", "--database", path, "--monthly-limit-usd", limit])
        assert refused.value.code == 2
    assert _rows(path) == []
    with pytest.raises(SystemExit):
        main(["perplexity-departments", "--database", str(tmp_path / "absent.sqlite")])
    (tmp_path / "runtime").chmod(0o755)
    with pytest.raises(SystemExit):
        main(["perplexity-departments", "--database", path])
