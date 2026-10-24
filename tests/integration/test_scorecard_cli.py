"""The owner's scorecard command (T42): read only, the same document as the read model, plain text on request.

Synthetic records only (``tests/scorecard_support.py``). The command opens the state database read-only, never
creates one, never writes, and says plainly why a period cannot be scored.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from tests.scorecard_support import PORTFOLIO, START, Paper, golden_day

from trade_graph import cli
from trade_graph.application.scorecard import GATES_STATEMENT, compute_scorecard
from trade_graph.domain.clock import FrozenClock
from trade_graph.market_data_cli import scorecard as command

END = START + timedelta(hours=24)
NOW = START + timedelta(hours=30)


@pytest.fixture
def state(tmp_path, monkeypatch):
    folder = tmp_path / "runtime"
    folder.mkdir(mode=0o700)
    path = folder / "trade_graph.sqlite"
    paper = Paper(path, at=START - timedelta(hours=1))
    golden_day(paper)
    expected = compute_scorecard(paper.database, portfolio_id=PORTFOLIO, end=END).document()
    paper.close()
    monkeypatch.setattr(command, "build_clock", lambda: FrozenClock(NOW))
    return path, expected


def _run(capsys, *argv, code: int = 0):
    assert cli.main(list(argv)) == code
    out = capsys.readouterr().out
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return out


def test_the_command_prints_the_scorecard_without_writing(state, capsys):
    path, expected = state
    before = (path.stat().st_size, path.stat().st_mtime_ns)
    document = _run(capsys, "scorecard", "--database", str(path), "--end", "2026-11-03T00:00:00Z")
    assert document == expected
    assert document["metrics"]["result"] == "210.14" and document["paper_result_pays_for_ai"] is False
    text = _run(capsys, "scorecard", "--database", str(path), "--end", "2026-11-03T00:00:00Z", "--format", "text")
    assert "Result after fees: +210.14 USD" in text and GATES_STATEMENT in text
    assert (path.stat().st_size, path.stat().st_mtime_ns) == before


def test_the_period_defaults_to_the_opening_deposit_until_now_on_the_newest_paper_account(state, capsys):
    path, _ = state
    document = _run(capsys, "scorecard", "--database", str(path))
    assert document["portfolio_id"] == PORTFOLIO
    assert document["period"] == {"start": "2026-11-02T00:00:00.000000Z", "end": "2026-11-03T06:00:00.000000Z",
                                  "start_basis": "opening_deposit", "days": "1.25"}
    # Six hours after the last prices the ETH still held is valued on an old price: provisional, never complete.
    assert document["status"] == "provisional"
    started = _run(capsys, "scorecard", "--database", str(path), "--portfolio-id", PORTFOLIO,
                   "--start", "2026-11-02T02:00:00Z", "--end", "2026-11-03T00:00:00Z")
    assert started["valuation"]["carried_in"] == ["ETH"] and started["metrics"]["result"] == "34.14"


def test_a_period_that_cannot_be_scored_says_why(state, capsys):
    path, _ = state
    document = _run(capsys, "scorecard", "--database", str(path), "--start", "2026-11-01T00:00:00Z", code=1)
    assert document == {"report": "paper-scorecard", "available": False,
                        "reason": "the period starts before the opening deposit, when the account had no money yet",
                        "paid_calls_enabled": False, "live_enabled": False}


def test_bad_requests_are_refused_before_anything_is_read(state, tmp_path, capsys):
    path, _ = state
    with pytest.raises(SystemExit):
        cli.main(["scorecard", "--database", str(path), "--end", "2026-11-04T00:00:00Z"])
    assert "in the future" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["scorecard", "--database", str(path), "--end", "tomorrow"])
    assert "cannot read the time" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["scorecard", "--database", str(path), "--portfolio-id", "nobody"])
    assert "no paper portfolio" in capsys.readouterr().err
    missing = tmp_path / "nothing.sqlite"
    with pytest.raises(SystemExit):
        cli.main(["scorecard", "--database", str(missing)])
    assert not missing.exists()
    assert "only reads one that already exists" in capsys.readouterr().err


def test_another_fee_profile_can_be_named_and_an_unknown_one_says_so(state, capsys):
    path, _ = state
    okx = _run(capsys, "scorecard", "--database", str(path), "--end", "2026-11-03T00:00:00Z",
               "--cost-profile", "okx-eea-spot")
    assert okx["benchmarks"]["hold_btc"]["cost_profile_ref"] == "okx-eea-spot@1"
    assert okx["benchmarks"]["hold_btc"]["taker_rate"] == "0.002"
    unknown = _run(capsys, "scorecard", "--database", str(path), "--cost-profile", "nowhere", code=1)
    assert unknown["available"] is False and "nowhere" in unknown["reason"]
