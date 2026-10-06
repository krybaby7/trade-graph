"""Protected CLI control arguments never implicitly authorize live or API spend."""

from trade_graph.cli import build_parser


def test_dashboard_accepts_explicit_private_runtime_configuration():
    args = build_parser().parse_args(["dashboard", "--config", "/private/paper.json"])
    assert args.config == "/private/paper.json"


def test_run_accepts_durable_service_run_identity():
    args = build_parser().parse_args(["run", "--service-run-id", "synthetic-service"])
    assert args.service_run_id == "synthetic-service"
    assert args.mode == "paper"
    assert args.public_data is False
    assert args.protected_owner is None


def test_cli_lifecycle_identity_reaches_worker_and_finishes(tmp_path, capsys):
    import json

    from trade_graph.adapters.persistence.db import Database
    from trade_graph.cli import main

    path = tmp_path / "paper.sqlite"
    assert main(["init", "--database", str(path)]) == 0
    db = Database(path)
    pid = db.execute("SELECT portfolio_id FROM portfolios").fetchone()[0]
    db.execute("INSERT INTO graph_service_runs(run_id,portfolio_id,mode,status,requested_at) "
               "VALUES (?,?,'paper','STARTING',?)", ("synthetic-run", pid, "2026-10-06T00:00:00Z"))
    db.close()
    capsys.readouterr()
    assert main(["run", "--database", str(path), "--service-run-id", "synthetic-run", "--once"]) == 0
    output = json.loads(capsys.readouterr().out.strip())
    assert output["paid_calls_enabled"] is False
    db = Database(path)
    row = db.execute("SELECT * FROM graph_service_runs WHERE run_id='synthetic-run'").fetchone()
    assert row["status"] == "STOPPED"
    assert row["finished_at"] is not None
    assert db.execute("SELECT COUNT(*) FROM graph_service_runs").fetchone()[0] == 1
    db.close()


def test_cli_failed_start_records_sanitized_failure_before_worker(tmp_path, monkeypatch, capsys):
    import pytest

    from trade_graph.adapters.persistence.db import Database
    from trade_graph.cli import main

    path = tmp_path / "paper.sqlite"
    assert main(["init", "--database", str(path)]) == 0
    db = Database(path)
    pid = db.execute("SELECT portfolio_id FROM portfolios").fetchone()[0]
    db.execute("INSERT INTO graph_service_runs(run_id,portfolio_id,mode,status,requested_at) "
               "VALUES (?,?,'paper','STARTING',?)", ("synthetic-failed", pid, "2026-10-06T00:00:00Z"))
    db.close()
    capsys.readouterr()

    def failure(config):
        raise ValueError("private data must not be recorded")

    monkeypatch.setattr("trade_graph.paper_runtime.load_runtime_config", failure)
    with pytest.raises(SystemExit):
        main(["run", "--database", str(path), "--service-run-id", "synthetic-failed"])
    assert "private data" not in capsys.readouterr().err
    db = Database(path)
    row = db.execute("SELECT * FROM graph_service_runs WHERE run_id='synthetic-failed'").fetchone()
    assert row["status"] == "FAILED"
    assert row["error_type"] == "ValueError"
    db.close()
