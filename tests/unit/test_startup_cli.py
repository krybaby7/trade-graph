"""Protected CLI control arguments never implicitly authorize live or API spend."""

from pathlib import Path

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


def test_subscription_status_is_read_only_and_requires_no_model(monkeypatch, capsys):
    import json

    from trade_graph.cli import main

    calls = []

    def status(provider):
        calls.append(provider)
        return {"provider": "codex_subscription", "ready": False, "selected_model": None,
                "quota": {"weekly": {"remaining_percent": 91}}, "cost_status": "unknown"}

    monkeypatch.setattr("trade_graph.adapters.models.subscription_process.native_subscription_status", status)
    assert main(["subscription-status", "--provider", "codex"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert calls == ["codex"]
    assert result["ready"] is False
    assert result["selected_model"] is None
    assert result["cost_status"] == "unknown"


def test_dashboard_accepts_explicit_protected_live_binding():
    args = build_parser().parse_args(["dashboard", "--mode", "live", "--protected-owner", "/owner"])
    assert args.mode == "live"
    assert args.protected_owner == "/owner"


def test_live_cli_routes_only_to_protected_factory(tmp_path, monkeypatch, capsys):
    import json
    from types import SimpleNamespace

    from trade_graph.cli import main

    seen = []

    class DB:
        def close(self):
            seen.append("closed")

    async def run(*, max_ticks):
        seen.append(("ticks", max_ticks))
        return {"failures": []}

    def assemble(path, **kwargs):
        seen.append((path, kwargs))
        return SimpleNamespace(database=DB(), portfolio_id="synthetic-live")

    def service(runtime, *, service_run_id):
        seen.append(("run_id", service_run_id))
        return SimpleNamespace(run=run)

    monkeypatch.setattr("trade_graph.live_runtime.assemble_live_runtime", assemble)
    monkeypatch.setattr("trade_graph.application.live_service.LiveService", service)
    path = tmp_path / "separate-live.sqlite"
    assert main(["run", "--mode", "live", "--database", str(path), "--protected-owner", "/owner",
                 "--service-run-id", "live-synthetic", "--once"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "live"
    assert result["paid_calls_enabled"] is False
    assert seen[0] == (path, {"portfolio_id": None, "config": None, "protected_owner": Path("/owner")})
    assert ("ticks", 1) in seen
    assert seen[-1] == "closed"


def test_live_cli_refusal_retains_start_failure_without_credentials(tmp_path, capsys):
    import pytest

    from trade_graph.adapters.persistence.db import Database
    from trade_graph.cli import main

    path = tmp_path / "paper.sqlite"
    main(["init", "--database", str(path)])
    db = Database(path)
    pid = db.execute("SELECT portfolio_id FROM portfolios").fetchone()[0]
    db.execute("INSERT INTO graph_service_runs(run_id,portfolio_id,mode,status,requested_at) "
               "VALUES (?,?,'live','STARTING',?)", ("live-refused", pid, "2026-10-06T00:00:00Z"))
    db.close()
    capsys.readouterr()
    with pytest.raises(SystemExit):
        main(["run", "--mode", "live", "--database", str(path), "--service-run-id", "live-refused"])
    assert "live trading stays disabled" in capsys.readouterr().err
    db = Database(path)
    row = db.execute("SELECT * FROM graph_service_runs WHERE run_id='live-refused'").fetchone()
    assert row["status"] == "FAILED"
    assert row["error_type"] == "AuthorityDenied"
    assert db.execute("SELECT COUNT(*) FROM order_attempts").fetchone()[0] == 0
    db.close()


def test_protected_dashboard_bind_is_not_a_public_host_option():
    import pytest

    from trade_graph.cli import main

    args = build_parser().parse_args(["dashboard", "--mode", "live", "--protected-owner", "/owner",
                                      "--protected-network-bind"])
    assert args.protected_network_bind is True
    with pytest.raises(SystemExit):
        main(["dashboard", "--protected-network-bind"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["dashboard", "--host", "0.0.0.0"])
