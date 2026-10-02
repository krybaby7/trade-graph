import json
from pathlib import Path

import pytest

from trade_graph.cli import main


def test_pause_backup_reconcile_and_paper_run(tmp_path: Path, capsys) -> None:
    database = tmp_path / "paper.sqlite"
    destination = tmp_path / "backups" / "paper.sqlite"
    assert main(
        [
            "init",
            "--database",
            str(database),
            "--capital",
            "10000",
            "--capital-currency",
            "USD",
            "--reporting-currency",
            "EUR",
        ]
    ) == 0
    assert main(["pause", "--database", str(database), "--profile", "manage-only"]) == 0
    paused = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert paused["profile"] == "MANAGE_ONLY"
    assert paused["originator"] == "owner"
    assert main(["backup", "--database", str(database), "--destination", str(destination)]) == 0
    backed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert Path(backed["destination"]).exists()
    assert (destination.with_suffix(destination.suffix + ".sha256")).exists()
    assert main(["reconcile", "--database", str(database)]) == 0
    reconciled = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert reconciled["reconciled"] is True
    assert reconciled["live_enabled"] is False
    assert main(["run", "--mode", "paper", "--database", str(database), "--once"]) == 0
    started = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert started["new_decisions"] is False
    assert started["paid_calls_enabled"] is False
    assert started["service"]["ticks"] == 1
    assert started["service"]["stopped"] is True
    assert main(["report", "--format", "json", "--database", str(database)]) == 0
    report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert report["verification"]["funded_paper_soak"] == "pending"


def test_run_refuses_live_and_unknown_pause(capsys) -> None:
    with pytest.raises(SystemExit) as live:
        main(["run", "--mode", "live"])
    assert live.value.code == 2
    assert "live trading stays disabled" in capsys.readouterr().err
    with pytest.raises(SystemExit) as pause:
        main(["pause", "--profile", "invented"])
    assert pause.value.code == 2
    assert "unknown pause profile" in capsys.readouterr().err
