"""T44: the news-alarm CLI on synthetic fixtures with scripted Jev replies. No network call and no key."""

from __future__ import annotations

import json
import os
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from tests.news_support import FIXTURES, NOW

from trade_graph.cli import main
from trade_graph.domain.clock import FrozenClock

SCRIPTED = str(FIXTURES / "jev-scripted-answers.json")
FAKE_KEY = "fixture-only-not-a-real-key-0000"


@pytest.fixture
def paper(tmp_path, monkeypatch, capsys):
    from trade_graph.market_data_cli import news_alarm

    monkeypatch.setattr(news_alarm, "build_clock", lambda: FrozenClock(NOW))
    monkeypatch.setattr(news_alarm, "build_sleep", lambda: (lambda seconds: None))
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    database = private / "trade_graph.sqlite"
    assert main(["init", "--database", str(database)]) == 0
    capsys.readouterr()
    return database


def _settings(tmp_path: Path, **jev) -> Path:
    path = tmp_path / "news-alarm.json"
    path.write_text(json.dumps({"schema_version": 1, "extra_watch_coins": ["EXMPL"], "jev": jev}))
    return path


def _last_json(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_a_fixture_poll_with_scripted_jev_logs_would_be_alarms_and_wakes_nobody(paper, tmp_path, capsys):
    code = main(["news-alarm-poll", "--database", str(paper), "--config", str(_settings(tmp_path)),
                 "--fixture-feeds", str(FIXTURES), "--scripted-jev", SCRIPTED])
    assert code == 0
    summary = _last_json(capsys)
    assert summary["mode"] == "shadow" and summary["wakes_sent"] == 0 and summary["jev"] == "scripted"
    assert summary["feed_evidence"] == "synthetic_fixture" and summary["paid_calls"] is False
    (poll,) = summary["polls"]
    assert poll["new_headlines"] == 8 and poll["checks"] == 7 and poll["would_wake"] == 2
    connection = sqlite3.connect(paper)
    roles = {row[0]: json.loads(row[1]) for row in connection.execute(
        "SELECT h.link, v.wake_roles_json FROM news_alarm_verdicts v JOIN news_headlines h USING (headline_id) "
        "WHERE v.would_wake = 1")}
    connection.close()
    # A fresh paper account holds nothing yet, so a would-be alarm names only the Leader.
    assert len(roles) == 2 and all(value == ["leader"] for value in roles.values())


def test_the_report_command_reads_the_log_in_plain_english_and_json(paper, tmp_path, capsys):
    main(["news-alarm-poll", "--database", str(paper), "--config", str(_settings(tmp_path)),
          "--fixture-feeds", str(FIXTURES), "--scripted-jev", SCRIPTED])
    capsys.readouterr()
    window = ["--since", "2026-10-15T12:00:00Z", "--until", "2026-10-16T12:01:00Z"]
    assert main(["news-alarm-report", "--database", str(paper), *window, "--format", "text"]) == 0
    text = capsys.readouterr().out
    assert "Shadow mode: nobody was woken." in text
    assert "Kraken will delist EXMPL/USD on 30 October (synthetic)" in text
    assert "Would have woken the Leader" in text
    assert main(["news-alarm-report", "--database", str(paper), *window]) == 0
    report = _last_json(capsys)
    assert report["summary"]["headlines"] == 8 and report["summary"]["would_wake"] == 2
    assert report["spend"]["days"][0]["synthetic_usd"] == "0.00012348"


def test_the_report_never_creates_or_upgrades_a_database(tmp_path, capsys):
    missing = tmp_path / "private" / "trade_graph.sqlite"
    with pytest.raises(SystemExit) as raised:
        main(["news-alarm-report", "--database", str(missing)])
    assert raised.value.code == 2 and not missing.exists()
    capsys.readouterr()
    older = tmp_path / "older.sqlite"
    connection = sqlite3.connect(older)
    connection.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    connection.execute("INSERT INTO schema_migrations VALUES ('0025', '2026-10-01T00:00:00.000000Z')")
    connection.commit()
    connection.close()
    before = older.read_bytes()
    assert main(["news-alarm-report", "--database", str(older)]) == 0
    report = _last_json(capsys)
    assert report["headlines"] == [] and "no news alarm records" in report["notes"][0].lower()
    assert older.read_bytes() == before


def test_switching_jev_on_without_a_key_sends_nothing_and_says_so(paper, tmp_path, capsys):
    settings = _settings(tmp_path, calls_enabled=True, key_directory=str(tmp_path / "no-key-here"))
    with pytest.raises(SystemExit) as raised:
        main(["news-alarm-poll", "--database", str(paper), "--config", str(settings),
              "--fixture-feeds", str(FIXTURES)])
    assert raised.value.code == 2
    assert "no Jev key" in capsys.readouterr().err
    connection = sqlite3.connect(paper)
    assert connection.execute("SELECT COUNT(*) FROM news_headlines").fetchone()[0] == 0
    connection.close()


def test_scripted_replies_cannot_be_mixed_with_real_jev_calls(paper, tmp_path, capsys):
    settings = _settings(tmp_path, calls_enabled=True)
    with pytest.raises(SystemExit) as raised:
        main(["news-alarm-poll", "--database", str(paper), "--config", str(settings),
              "--fixture-feeds", str(FIXTURES), "--scripted-jev", SCRIPTED])
    assert raised.value.code == 2
    assert "scripted" in capsys.readouterr().err


def test_a_moving_alias_cannot_be_configured(paper, tmp_path, capsys):
    with pytest.raises(SystemExit) as raised:
        main(["news-alarm-poll", "--database", str(paper), "--config", str(_settings(tmp_path, model="jev-latest")),
              "--fixture-feeds", str(FIXTURES)])
    assert raised.value.code == 2


def test_with_a_key_installed_the_poll_uses_only_the_pinned_endpoint(paper, tmp_path, capsys, monkeypatch):
    """Wiring check with a stand-in for the HTTPS client. It is not a Jev or paid-provider test."""
    from trade_graph.adapters.decisions import jev
    from trade_graph.adapters.models.transport import HttpxProviderHttp
    from trade_graph.application.budget import BudgetGateway

    key_directory = tmp_path / "owner" / "typesafe"
    key_directory.mkdir(parents=True)
    (key_directory / jev.JEV_KEY_FILENAME).write_text(FAKE_KEY)
    os.chmod(key_directory / jev.JEV_KEY_FILENAME, 0o600)
    os.chmod(key_directory, 0o700)
    from trade_graph.adapters.persistence.db import Database

    database = Database(paper)
    BudgetGateway(database, FrozenClock(NOW)).configure(
        deployment_id="deployment", currency="EUR", total=Decimal("5"), period=Decimal("5"),
        priority_reserve=Decimal("0"), daily=Decimal("5"), root=Decimal("5"), roles={})
    database.close()
    sent = []
    scripted = jev.ScriptedJevHttp.from_script(json.loads(Path(SCRIPTED).read_text()))

    def stand_in(self, url, body, headers, *, timeout_seconds=None, observe=None):
        sent.append({"url": url, "headers": dict(headers), "body": body})
        return scripted.post_json(url, body, {}, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(HttpxProviderHttp, "post_json", stand_in)
    settings = _settings(tmp_path, calls_enabled=True, key_directory=str(key_directory))
    assert main(["news-alarm-poll", "--database", str(paper), "--config", str(settings),
                 "--fixture-feeds", str(FIXTURES)]) == 0
    output = capsys.readouterr()
    summary = json.loads(output.out.strip().splitlines()[-1])
    assert summary["jev"] == "live" and summary["paid_calls"] is True and summary["wakes_sent"] == 0
    assert len(sent) == 7 and {item["url"] for item in sent} == {jev.JEV_ENDPOINT}
    assert all(item["headers"]["authorization"] == f"Bearer {FAKE_KEY}" for item in sent)
    assert FAKE_KEY not in output.out + output.err
    for stored in (paper, paper.with_name(paper.name + "-wal")):
        assert not stored.exists() or FAKE_KEY.encode() not in stored.read_bytes()
    connection = sqlite3.connect(paper)
    synthetic = {row[0] for row in connection.execute("SELECT synthetic FROM usage_receipts")}
    connection.close()
    assert synthetic == {0}
