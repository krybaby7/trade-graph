"""Installed artifacts, private configuration and actual public-feed ingestion paths."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from trade_graph.adapters.market.paper_feed import PublicPaperFeed
from trade_graph.cli import main
from trade_graph.domain.clock import FrozenClock
from trade_graph.paper_runtime import assemble_paper_runtime, installed_artifacts, load_runtime_config


class PublicResponses:
    def __init__(self, clock):
        self.clock, self.calls, self.failed = clock, [], False

    def get_text(self, url):
        self.calls.append(url)
        if self.failed:
            raise TimeoutError("public fixture outage")
        if "/AssetPairs?" in url:
            return json.dumps({"error": [], "result": {"XXBTZUSD": {
                "wsname": "XBT/USD", "pair_decimals": 1, "lot_decimals": 8,
                "ordermin": "0.0001", "costmin": "1",
            }}})
        if "/Ticker?" in url:
            self.clock.advance(2)
            return json.dumps({"error": [], "result": {"XXBTZUSD": {
                "a": ["100.2", "2", "2"], "b": ["100", "2", "2"],
                "c": ["100.1", "1"], "v": ["2", "5"],
            }}})
        assert url.endswith("/rate/usd/eur")
        return json.dumps({"base": "USD", "quote": "EUR", "rate": "0.9", "date": "2026-10-01"})


def test_installed_baseline_and_default_assembly_need_no_checkout_or_keys(tmp_path, capsys, monkeypatch):
    path = tmp_path / "paper.sqlite"
    assert main(["init", "--database", str(path)]) == 0
    capsys.readouterr()
    monkeypatch.chdir(tmp_path)
    runtime = assemble_paper_runtime(path)
    try:
        bundle = runtime.versions.load_active(runtime.portfolio_id)
        assert bundle["files"] == installed_artifacts()
        assert runtime.handlers == {}
        assert runtime.public_feed is None
        assert runtime.paid_calls_enabled is False
        assert runtime.live_enabled is False
        assert runtime.execution.authority.active_mandate(runtime.portfolio_id).symbols == ["BTC/USD", "ETH/USD"]
        assert runtime.ledger.books(runtime.portfolio_id).cash["USD"] == Decimal("10000")
        assert runtime.database.execute("SELECT COUNT(*) FROM deployment_budget").fetchone()[0] == 0
    finally:
        runtime.database.close()


def test_public_reference_preserves_sources_receipt_time_and_prior_fx_date(tmp_path, capsys, monkeypatch):
    path = tmp_path / "paper.sqlite"
    clock = FrozenClock(datetime(2026, 10, 2, tzinfo=UTC))
    monkeypatch.setattr("trade_graph.domain.clock.SystemClock", lambda: clock)
    main(["init", "--database", str(path)])
    capsys.readouterr()
    runtime = assemble_paper_runtime(path, clock=clock)
    transport = PublicResponses(clock)
    feed = PublicPaperFeed(runtime.execution, [runtime.portfolio_id], ["BTC/USD"], transport=transport)
    try:
        quotes = feed.poll()
        assert len(quotes) == 1
        assert quotes[0].venue == "paper"
        assert quotes[0].source == "kraken_public_rest:paper_reference:receipt_time"
        assert quotes[0].event_time_utc == clock.now()
        runtime.execution.on_observation(quotes[0])
        assert runtime.execution.instrument("paper", "BTC/USD").synthetic
        assert not runtime.execution.instrument("kraken", "BTC/USD").synthetic
        fx = runtime.database.execute("SELECT * FROM fx_rates").fetchone()
        assert fx["observed_at"].startswith("2026-10-01")
        assert fx["retrieved_at"].startswith("2026-10-02")
        assert fx["stale"] == 1
        assert runtime.ledger.equity(runtime.portfolio_id).equity == Decimal("9000")
        assert runtime.ledger.equity(runtime.portfolio_id).provisional
        assert feed.poll() == []
        assert len(transport.calls) == 3
        clock.advance(11)
        transport.failed = True
        with pytest.raises(TimeoutError):
            feed.poll()
        assert runtime.database.execute("SELECT COUNT(*) FROM valuation_marks").fetchone()[0] == 1
        assert runtime.database.execute("SELECT COUNT(*) FROM fx_rates").fetchone()[0] == 1
    finally:
        runtime.database.close()


def test_runtime_configuration_is_private_bounded_and_errors_do_not_echo_values(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"unexpected": "private-value-never-log"}')
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private regular file"):
        load_runtime_config(path)
    path.chmod(0o600)
    with pytest.raises(ValueError) as error:
        load_runtime_config(path)
    assert "private-value" not in str(error.value)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="private regular file"):
        load_runtime_config(link)
    path.write_text(' ' * 262145)
    with pytest.raises(ValueError, match="bounded input"):
        load_runtime_config(path)


def test_initialization_rejects_nonfinite_capital_and_shared_storage_before_funding(tmp_path, capsys):
    for amount in ("NaN", "Infinity", "0", "-1"):
        path = tmp_path / f"{amount}.sqlite"
        with pytest.raises(SystemExit) as error:
            main(["init", "--database", str(path), "--capital", amount])
        assert error.value.code == 2
        assert not path.exists()
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    with pytest.raises(SystemExit) as error:
        main(["init", "--database", str(shared / "paper.sqlite")])
    assert error.value.code == 2
    assert not (shared / "paper.sqlite").exists()


def test_package_artifact_bytes_match_tracked_source_defaults():
    root = Path(__file__).resolve().parents[2]
    for name, text in installed_artifacts().items():
        assert (root / name).read_text() == text
