"""GET /api/v1/hq/chart and the dashboard wiring (T41): signed-in reads only, validated queries, the HQ page's policy.

Synthetic paper account and synthetic candles stored under the collector's source name in a run labelled
``synthetic_fixture`` (the route always reads that source); no network, key, model or exchange call.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.chart_support import candles, chart_now, market_file, planned_account, runtime, table_counts, write_candles

from trade_graph.api.app import create_app
from trade_graph.api.auth import issue_session
from trade_graph.api.hq_chart import ChartCandleFile
from trade_graph.api.hq_chart_contract import SCHEMA, validate_chart

WEB = Path(__file__).resolve().parents[2] / "src" / "trade_graph" / "web"


@pytest.fixture
def served(tmp_path):
    account, _ = planned_account(tmp_path)
    path = market_file(tmp_path)
    write_candles(path, candles("SOL/USD", 15, chart_now(account), 2400, source="kraken_public_ohlc"))
    handle = ChartCandleFile(path)
    app_runtime = runtime(account, None)
    app_runtime.chart_candles = handle
    owner, _ = issue_session(account.database, account.clock, "owner")
    reader, _ = issue_session(account.database, account.clock, "reader")
    client = TestClient(create_app(app_runtime))
    yield client, account, owner, reader, path
    handle.close()


def test_any_signed_in_role_reads_a_chart_and_nobody_else(served) -> None:
    client, _, owner, reader, _ = served
    response = client.get("/api/v1/hq/chart", follow_redirects=False)
    assert response.status_code == 401 and response.json() == {"detail": "authentication required"}
    for token in (owner, reader):
        response = client.get("/api/v1/hq/chart", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        document = response.json()
        validate_chart(document)
        assert document["schema"] == SCHEMA and document["status"] == "ok"
        assert document["source"] == "kraken_public_ohlc" and len(document["candles"]) == 300
    policy = response.headers["content-security-policy"]
    assert "script-src 'self'" in policy and "style-src 'self'" in policy and "default-src 'self'" in policy


@pytest.mark.parametrize("params", [{"interval": 30}, {"interval": 0}, {"interval": "x"}, {"symbol": "sol/usd"},
                                    {"symbol": "SOLUSD"}, {"symbol": "A" * 30}, {"symbol": "BTC/USD/EUR"}])
def test_queries_are_validated(served, params) -> None:
    client, _, owner, *_ = served
    response = client.get("/api/v1/hq/chart", params=params, headers={"Authorization": f"Bearer {owner}"})
    assert response.status_code == 422
    assert response.json()["detail"] in {"invalid chart query", "invalid request parameters"}


def test_a_chosen_coin_and_size_are_drawn_and_reading_writes_nothing(served) -> None:
    import sqlite3

    client, account, owner, _, path = served
    state = table_counts(account.database.execute("SELECT 1").connection)
    candle_file = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    stored = table_counts(candle_file)
    headers = {"Authorization": f"Bearer {owner}"}
    for interval in (1, 5, 15, 60):
        document = client.get("/api/v1/hq/chart", params={"symbol": "SOL/USD", "interval": interval},
                              headers=headers).json()
        assert (document["symbol"], document["interval_minutes"]) == ("SOL/USD", interval)
    assert client.get("/api/v1/hq/chart", params={"symbol": "ETH/USD"}, headers=headers).json()["candles"] == []
    assert table_counts(account.database.execute("SELECT 1").connection) == state
    assert table_counts(candle_file) == stored
    candle_file.close()


def test_the_vendored_library_and_its_licence_are_served_from_hq_itself(served) -> None:
    client, *_ = served
    script = client.get("/static/vendor/lightweight-charts/lightweight-charts.standalone.production.js")
    assert script.status_code == 200 and "javascript" in script.headers["content-type"]
    assert script.content == (WEB / "static/vendor/lightweight-charts/lightweight-charts.standalone.production.js"
                              ).read_bytes()
    for name in ("LICENSE.txt", "NOTICE.txt"):
        text = client.get(f"/static/vendor/lightweight-charts/{name}")
        assert text.status_code == 200 and text.headers["content-type"].startswith("text/plain")


def test_the_dashboard_runtime_reads_the_candle_file_it_is_given(tmp_path) -> None:
    from trade_graph.cli import build_parser
    from trade_graph.dashboard import dashboard_runtime

    account, _ = planned_account(tmp_path)
    path = market_file(tmp_path)
    built = dashboard_runtime(Path(account.database.path), account.portfolio, market_database=path)
    try:
        assert isinstance(built.chart_candles, ChartCandleFile) and built.chart_candles.open() == (None, "missing")
        assert not path.exists()
    finally:
        built.database.close()
    arguments = build_parser().parse_args(["dashboard"])
    assert arguments.market_database == "runtime/market_data.sqlite"
    assert build_parser().parse_args(["dashboard", "--market-database", "x.sqlite"]).market_database == "x.sqlite"
