"""``news-alarm-poll`` refuses Jev checks against a protected installation's database (W9-A, D258).

The command's reservations carry the settings' deployment id (``deployment``, the installed manifest's own) without a
protected budget origin, and the installation's financial witness re-verifies every reservation of its deployment: one
such reservation would leave the protected service management-only. Inside an installation the news check runs in the
service, where each Jev reservation gets its origin. Synthetic fixtures and scripted Jev replies; no network or key.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from tests.integration.test_news_alarm_cli import SCRIPTED, _settings, paper  # noqa: F401
from tests.news_support import FIXTURES

from trade_graph.cli import main


def _protect(database) -> None:
    connection = sqlite3.connect(database)
    connection.execute("INSERT INTO protected_runtime_instances (instance_id, manifest_sha256, status, updated_at) "
                       "VALUES ('deployed-synthetic', ?, 'RUNNING', '2026-10-16T00:00:00.000000Z')", ("0" * 64,))
    connection.commit()
    connection.close()


def _count(database, table) -> int:
    connection = sqlite3.connect(database)
    try:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        connection.close()


def test_scripted_or_real_jev_checks_are_refused_on_a_protected_installation(paper, tmp_path, capsys):  # noqa: F811
    _protect(paper)
    with pytest.raises(SystemExit) as exc:
        main(["news-alarm-poll", "--database", str(paper), "--config", str(_settings(tmp_path)),
              "--fixture-feeds", str(FIXTURES), "--scripted-jev", SCRIPTED])
    assert exc.value.code == 2
    assert "runs inside the installed service" in capsys.readouterr().err
    assert _count(paper, "budget_reservations") == 0 and _count(paper, "news_feed_polls") == 0


def test_a_feeds_only_poll_still_runs_on_a_protected_installation(paper, tmp_path, capsys):  # noqa: F811
    _protect(paper)
    assert main(["news-alarm-poll", "--database", str(paper), "--config", str(_settings(tmp_path)),
                 "--fixture-feeds", str(FIXTURES)]) == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["jev"] == "off" and summary["polls"][0]["new_headlines"] == 8
    assert _count(paper, "budget_reservations") == 0


def test_an_ordinary_paper_database_keeps_the_scripted_command(paper, tmp_path, capsys):  # noqa: F811
    assert main(["news-alarm-poll", "--database", str(paper), "--config", str(_settings(tmp_path)),
                 "--fixture-feeds", str(FIXTURES), "--scripted-jev", SCRIPTED]) == 0
    assert _count(paper, "budget_reservations") == 7
