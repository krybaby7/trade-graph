"""T29 commands on synthetic data: register, study, run, report, verify, and the OKX import and fetch.

No network call: the OKX fetch runs on a scripted transport that labels itself synthetic.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.okx_support import FIXTURE
from tests.practice_range_support import cross_strategy, golden_btc, golden_candles, open_store, save

from trade_graph.cli import main
from trade_graph.domain.clock import FrozenClock

NOW = datetime(2026, 10, 2, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def setup(tmp_path, monkeypatch, capsys):
    from trade_graph.market_data_cli import okx, practice_range

    monkeypatch.setattr(practice_range, "build_clock", lambda: FrozenClock(NOW))
    monkeypatch.setattr(okx, "build_clock", lambda: FrozenClock(NOW))
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    database = private / "trade_graph.sqlite"
    assert main(["init", "--database", str(database)]) == 0
    market_path = private / "market" / "market_data.sqlite"
    market, candles = open_store(market_path)
    save(candles, golden_candles())
    save(candles, golden_btc())
    market.close()
    strategy = tmp_path / "cross-hundred.v1.json"
    strategy.write_text(cross_strategy().model_dump_json(indent=2))
    capsys.readouterr()
    return database, market_path, strategy


def _json(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _study(database: Path, study_id: str = "cross-1") -> list[str]:
    return ["practice-range-study", "--database", str(database), "--study-id", study_id, "--strategy-id",
            "cross-hundred", "--tune-start", "2026-07-01", "--tune-end", "2026-07-02", "--confirm-start",
            "2026-07-02", "--confirm-end", "2026-07-03"]


def _golden_run(database: Path, market: Path, *extra: str) -> list[str]:
    return ["practice-range-run", "--database", str(database), "--market-database", str(market), "--study-id",
            "cross-1", "--phase", "tune", "--strategy", "cross-hundred@1", "--symbol", "SYN/USD", "--source",
            "synthetic", "--profile", "kraken-spot-starting-tier", "--price-increment", "0.01",
            "--quantity-increment", "1", "--min-notional", "10", *extra]


def test_register_study_run_report_and_verify(setup, capsys) -> None:
    database, market, strategy = setup
    assert main(["practice-range-register", "--database", str(database), "--strategy", str(strategy)]) == 0
    registered = _json(capsys)
    assert registered["strategy_ref"] == "cross-hundred@1" and registered["created"] is True
    assert main(["practice-range-register", "--database", str(database), "--strategy", str(strategy)]) == 0
    assert _json(capsys)["created"] is False
    assert main(_study(database)) == 0
    assert _json(capsys)["study"]["meets_three_month_minimum"] is False
    assert main(_golden_run(database, market)) == 0
    ran = _json(capsys)
    assert ran["paid_calls"] == 0 and ran["orders_sent"] == 0
    (overview,) = ran["reports"]
    assert overview["result"] == "-185.58736" and overview["trades"] == 2 and overview["win_rate"] == "0.5"
    assert overview["evidence"] == ["synthetic_fixture"] and overview["label"] == "evidence_for_trader_discretion"
    run_id = overview["run_id"]
    assert main(["practice-range-report", "--database", str(database), "--run-id", run_id, "--format", "text"]) == 0
    text = capsys.readouterr().out
    assert "Trades: 2 (1 won, 1 lost; win rate 50.00%)" in text
    assert "Result after fees: -185.59 USD (-1.86%) from 10,000.00 USD." in text
    assert "Worst drawdown: 3.45% (351.18 USD)" in text and "Still held at the end: 48" in text
    assert "synthetic test data" in text and "not a pass mark" in text and "less than the three months" in text
    assert main(["practice-range-report", "--database", str(database), "--strategy", "cross-hundred@1"]) == 0
    assert [item["run_id"] for item in _json(capsys)["reports"]] == [run_id]
    assert main(["practice-range-verify", "--database", str(database), "--market-database", str(market),
                 "--run-id", run_id]) == 0
    verified = _json(capsys)
    assert verified["reproduced"] is True and verified["differences"] == []


def test_the_confirmation_window_cannot_be_tuned_on_from_the_command_line(setup, capsys) -> None:
    database, market, strategy = setup
    main(["practice-range-register", "--database", str(database), "--strategy", str(strategy)])
    main(_study(database))
    capsys.readouterr()
    with pytest.raises(SystemExit) as stopped:
        main(_golden_run(database, market, "--window-start", "2026-07-01", "--window-end", "2026-07-03"))
    assert stopped.value.code == 2
    assert "inside the study's tuning window" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["practice-range-study", "--database", str(database), "--study-id", "cross-2", "--strategy-id",
              "cross-hundred", "--tune-start", "2026-07-01", "--tune-end", "2026-07-03", "--confirm-start",
              "2026-07-03", "--confirm-end", "2026-07-04"])
    assert "never reused for tuning" in capsys.readouterr().err


def test_a_file_outside_the_grammar_registers_nothing(setup, tmp_path, capsys) -> None:
    database, _, _ = setup
    bad = tmp_path / "bad.json"
    document = json.loads(cross_strategy().model_dump_json())
    document["sizing"]["equity_fraction"] = "3"
    bad.write_text(json.dumps(document))
    with pytest.raises(SystemExit) as stopped:
        main(["practice-range-register", "--database", str(database), "--strategy", str(bad)])
    assert stopped.value.code == 2 and "outside the grammar" in capsys.readouterr().err
    connection = sqlite3.connect(database)
    assert connection.execute("SELECT COUNT(*) FROM practice_strategy_versions").fetchone()[0] == 0
    connection.close()


def test_the_report_never_creates_a_database(tmp_path, capsys) -> None:
    missing = tmp_path / "nothing.sqlite"
    with pytest.raises(SystemExit) as stopped:
        main(["practice-range-report", "--database", str(missing)])
    assert stopped.value.code == 2 and not missing.exists()


def test_the_packaged_examples_register(setup, capsys) -> None:
    database, _, _ = setup
    for path in sorted((ROOT / "strategies" / "practice").glob("*.json")):
        assert main(["practice-range-register", "--database", str(database), "--strategy", str(path)]) == 0
        assert _json(capsys)["created"] is True


class _ScriptedOkx:
    synthetic = True

    def get_text(self, url: str) -> str:
        if "after=" in url:
            return json.dumps({"code": "0", "msg": "", "data": []})
        return (FIXTURE / "page-001.json").read_text()


def test_okx_import_and_fetch_commands(setup, tmp_path, monkeypatch, capsys) -> None:
    from trade_graph.market_data_cli import okx

    _, market, _ = setup
    assert main(["candles-okx-import", "--market-database", str(market), "--recording", str(FIXTURE)]) == 0
    imported = _json(capsys)
    assert imported["candles"] == 600 and imported["evidence_label"] == "synthetic_fixture"
    monkeypatch.setattr(okx, "build_transport", lambda: _ScriptedOkx())
    other = tmp_path / "other" / "market_data.sqlite"
    assert main(["candles-okx-fetch", "--market-database", str(other), "--inst-id", "SOL-USDT", "--pages", "3",
                 "--record", str(tmp_path / "recorded")]) == 0
    fetched = _json(capsys)
    assert fetched["candles"] == 99 and fetched["requests"] == 2 and fetched["status"] == "ok"
    manifest = json.loads((tmp_path / "recorded" / "manifest.json").read_text())
    assert manifest["evidence_label"] == "synthetic_fixture"
    with pytest.raises(SystemExit):
        main(["candles-okx-fetch", "--market-database", str(other), "--inst-id", "SOL/USDT"])
