"""Bounded public-only collection command preserves an existing paper account."""

import json

import pytest

from trade_graph.adapters.persistence.db import Database
from trade_graph.cli import build_parser, main


def test_history_command_has_bounded_defaults_and_no_credential_options():
    args = build_parser().parse_args(["collect-kraken-history"])
    assert args.hours == 168
    assert args.database == "runtime/trade_graph.sqlite"
    assert not hasattr(args, "owner_directory")
    assert not hasattr(args, "paid_calls_enabled")
    assert not hasattr(args, "mode")


@pytest.mark.parametrize("hours", ["0", "-1", "720", "100000"])
def test_history_command_rejects_out_of_bounds_before_creating_database(tmp_path, hours, capsys):
    path = tmp_path / "absent.sqlite"
    with pytest.raises(SystemExit) as refused:
        main(["collect-kraken-history", "--database", str(path), "--hours", hours])
    assert refused.value.code == 2
    assert "between 1 and 719" in capsys.readouterr().err
    assert not path.exists()


def test_history_command_refuses_missing_database(tmp_path, capsys):
    path = tmp_path / "absent.sqlite"
    assert main(["collect-kraken-history", "--database", str(path)]) == 1
    assert not path.exists()
    assert "failed or refused" in capsys.readouterr().err


def test_history_command_preserves_account_and_requests_fixed_public_pairs(tmp_path, monkeypatch, capsys):
    path = tmp_path / "paper.sqlite"
    assert main(["init", "--database", str(path)]) == 0
    capsys.readouterr()
    db = Database(path)
    prior = {
        table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}").fetchall()]
        for table in ("portfolios", "ledger_events", "owner_policy_revisions", "mandates", "active_versions")
    }
    db.close()
    calls = []

    def collect(database, clock, transport, *, hours, symbols):
        calls.append((hours, symbols, transport.timeout, transport.maximum_response_bytes))
        return {"status": "ok", "symbols": list(symbols), "paid_calls_enabled": False, "live_enabled": False}

    monkeypatch.setattr("trade_graph.application.collect_price_history.collect_public_hourly_history", collect)
    assert main(["collect-kraken-history", "--database", str(path), "--hours", "50"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["symbols"] == ["BTC/USD", "ETH/USD"]
    assert report["paid_calls_enabled"] is report["live_enabled"] is False
    assert calls == [(50, ("BTC/USD", "ETH/USD"), 10, 1_048_576)]
    db = Database(path)
    for table, rows in prior.items():
        assert [tuple(row) for row in db.execute(f"SELECT * FROM {table}").fetchall()] == rows
    db.close()


def test_history_command_never_prints_transport_exception_contents(tmp_path, monkeypatch, capsys):
    path = tmp_path / "paper.sqlite"
    assert main(["init", "--database", str(path)]) == 0
    capsys.readouterr()

    def failed(*args, **kwargs):
        raise OSError("unexpected payload detail must remain private")

    monkeypatch.setattr("trade_graph.application.collect_price_history.collect_public_hourly_history", failed)
    assert main(["collect-kraken-history", "--database", str(path)]) == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert "unexpected payload" not in out.err
    assert "failed or refused" in out.err


def test_history_command_refuses_nonpaper_before_network(tmp_path, monkeypatch, capsys):
    path = tmp_path / "paper.sqlite"
    assert main(["init", "--database", str(path)]) == 0
    capsys.readouterr()
    db = Database(path)
    db.execute("UPDATE portfolios SET mode = 'live'")
    db.close()
    calls = []
    monkeypatch.setattr("trade_graph.application.collect_price_history.collect_public_hourly_history",
                        lambda *a, **k: calls.append(True))
    assert main(["collect-kraken-history", "--database", str(path)]) == 1
    assert calls == []
