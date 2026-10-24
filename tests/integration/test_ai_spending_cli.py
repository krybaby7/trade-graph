"""Owner commands for AI spending (T40): a read-only report, and recording a prepaid credit reading or a testing
alert amount. Previews by default; only ``--confirm`` writes, and nothing either command does pauses or limits
any call. Synthetic records only.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tests.spending_support import Spending

from trade_graph import cli
from trade_graph.adapters.persistence.db import Database
from trade_graph.domain.clock import FrozenClock
from trade_graph.market_data_cli import ai_spending as command

NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)


@pytest.fixture
def state(tmp_path, monkeypatch):
    folder = tmp_path / "runtime"
    folder.mkdir(mode=0o700)
    path = folder / "trade_graph.sqlite"
    database = Database(path)
    clock = FrozenClock(NOW)
    spending = Spending(database, clock)
    spending.run("unlimited_testing")
    spending.call("research")
    spending.call("research", "uncertain")
    spending.call(service="jev", input_tokens=4000, output_tokens=10)
    spending.codex_turn("trader", used=2000)
    database.close()
    monkeypatch.setattr(command, "build_clock", lambda: FrozenClock(NOW))
    return path


def _run(capsys, *argv) -> dict | str:
    assert cli.main(list(argv)) == 0
    out = capsys.readouterr().out
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return out


def _records(path) -> list:
    database = Database(path)
    try:
        return [dict(row) for row in database.execute("SELECT * FROM ai_spending_owner_records ORDER BY sequence")]
    finally:
        database.close()


def test_the_report_reads_without_writing(state, capsys):
    before = (state.stat().st_size, state.stat().st_mtime_ns)
    report = _run(capsys, "ai-spending-report", "--database", str(state))
    assert report["report"] == "ai-spending" and report["paper_money_used"] is False
    perplexity = report["services"]["perplexity"]
    assert perplexity["status"] == "testing" and perplexity["testing"]["actual_usd"] == "0.004"
    assert perplexity["testing"]["unresolved_calls"] == 1 and Decimal(perplexity["testing"]["unresolved_usd"]) > 0
    assert report["services"]["jev"]["testing"]["actual_usd"] == "0.000168"
    assert report["codex"]["departments"]["trader"]["share_percent"] == "100"
    text = _run(capsys, "ai-spending-report", "--database", str(state), "--format", "text")
    assert "Perplexity web research: testing" in text and "not known yet" in text
    assert "Practice money is never counted." in text
    assert (state.stat().st_size, state.stat().st_mtime_ns) == before


def test_the_report_never_creates_a_database(tmp_path, capsys):
    missing = tmp_path / "nothing.sqlite"
    with pytest.raises(SystemExit):
        cli.main(["ai-spending-report", "--database", str(missing)])
    assert not missing.exists()
    assert "only reads one that already exists" in capsys.readouterr().err


def test_recording_a_credit_previews_first_and_confirm_appends_one_row(state, capsys):
    preview = _run(capsys, "ai-spending-record", "--database", str(state), "--service", "perplexity",
                   "--prepaid-credit", "50.00", "--checked-at", "2026-10-16T11:30:00Z")
    assert preview["applied"] is False and preview["amount_usd"] == "50"
    assert preview["kind"] == "prepaid_credit" and preview["observed_at"] == "2026-10-16T11:30:00.000000Z"
    assert "information only" in preview["effect"] and preview["paid_calls_enabled"] is False
    assert _records(state) == []
    applied = _run(capsys, "ai-spending-record", "--database", str(state), "--service", "perplexity",
                   "--prepaid-credit", "50.00", "--checked-at", "2026-10-16T11:30:00Z", "--confirm")
    assert applied["applied"] is True and applied["previous"] is None
    [row] = _records(state)
    assert (row["service"], row["kind"], row["amount_usd"], row["set_by"]) == ("perplexity", "prepaid_credit", "50",
                                                                             "owner")
    again = _run(capsys, "ai-spending-record", "--database", str(state), "--service", "perplexity",
                 "--prepaid-credit", "45", "--confirm")
    assert again["previous"] == {"amount_usd": "50", "observed_at": "2026-10-16T11:30:00.000000Z"}
    assert len(_records(state)) == 2  # history is kept; the newest reading wins
    report = _run(capsys, "ai-spending-report", "--database", str(state))
    assert report["services"]["perplexity"]["credit"]["amount_usd"] == "45"


def test_recording_a_testing_alert_amount(state, capsys):
    applied = _run(capsys, "ai-spending-record", "--database", str(state), "--service", "jev", "--testing-alert",
                   "2.5", "--confirm")
    assert applied["kind"] == "testing_alert" and applied["amount_usd"] == "2.5"
    report = _run(capsys, "ai-spending-report", "--database", str(state))
    assert report["services"]["jev"]["alert"]["threshold_usd"] == "2.5"
    assert report["services"]["jev"]["alert"]["source"] == "owner"


@pytest.mark.parametrize("argv", [
    ["--service", "perplexity", "--prepaid-credit", "-1"],
    ["--service", "perplexity", "--prepaid-credit", "abc"],
    ["--service", "perplexity", "--prepaid-credit", "NaN"],
    ["--service", "perplexity", "--testing-alert", "0"],
    ["--service", "perplexity", "--prepaid-credit", "5", "--checked-at", "2026-10-17T00:00:00Z"],  # future
    ["--service", "perplexity", "--prepaid-credit", "5", "--checked-at", "2026-10-16T11:00:00"],  # no zone
    ["--service", "perplexity", "--prepaid-credit", "5", "--testing-alert", "5"],
    ["--service", "perplexity"],
    ["--service", "openai", "--prepaid-credit", "5"],
    ["--service", "perplexity", "--testing-alert", "5", "--checked-at", "2026-10-16T11:00:00Z"],
])
def test_bad_values_are_refused_and_nothing_is_written(state, capsys, argv):
    with pytest.raises(SystemExit):
        cli.main(["ai-spending-record", "--database", str(state), *argv, "--confirm"])
    capsys.readouterr()
    assert _records(state) == []


def test_recording_needs_a_private_existing_database(tmp_path, state, capsys):
    os.chmod(state.parent, 0o755)
    with pytest.raises(SystemExit):
        cli.main(["ai-spending-record", "--database", str(state), "--service", "jev", "--prepaid-credit", "5",
                  "--confirm"])
    assert "mode 0700" in capsys.readouterr().err
    os.chmod(state.parent, 0o700)
    with pytest.raises(SystemExit):
        cli.main(["ai-spending-record", "--database", str(tmp_path / "missing.sqlite"), "--service", "jev",
                  "--prepaid-credit", "5", "--confirm"])
    assert not (tmp_path / "missing.sqlite").exists()
