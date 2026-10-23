"""Synthetic owner collector through the actual historical dashboard projection."""

import asyncio
import base64
import importlib
import json
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.integration.test_kraken_live_adapter import ScriptedRest

from trade_graph.api.app import create_app
from trade_graph.api.auth import issue_session
from trade_graph.application.venue_conformance import KrakenReadOnlyConformance
from trade_graph.cli import main
from trade_graph.dashboard import dashboard_runtime


@pytest.mark.parametrize("partial", [False, True])
def test_owner_collection_to_authenticated_mission_preserves_financial_state(tmp_path, capsys, partial):
    onboarding = importlib.import_module("trade_graph.application.kraken_onboarding")
    runtime_path = tmp_path / "runtime"
    runtime_path.mkdir(mode=0o700)
    database = runtime_path / "paper.sqlite"
    assert main(["init", "--database", str(database)]) == 0
    capsys.readouterr()
    runtime = dashboard_runtime(database)
    token, _ = issue_session(runtime.database, runtime.clock, "owner")
    client = TestClient(create_app(runtime))
    headers = {"Authorization": f"Bearer {token}"}
    baseline = client.get("/api/v1/progress", headers=headers).json()
    tables = ("portfolios", "journal_transactions", "journal_postings", "ledger_events",
              "owner_policy_revisions", "mandates", "order_intents", "model_invocations")

    def counts():
        return {table: runtime.database.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in tables}

    before = counts()
    rest = ScriptedRest()
    rest.results["TradeVolume"] = {
        "fees": {"XXBTZUSD": {"fee": "0.2"}}, "fees_maker": {"XXBTZUSD": {"fee": "0.1"}},
    }
    key = "synthetic-owner-onboarding-key"
    secret = base64.b64encode(b"synthetic-owner-onboarding-secret").decode()

    def factory(authority, **kwargs):
        def respond(request):
            method = request.url.path.rsplit("/", 1)[-1]
            if partial and method == "BalanceEx":
                return httpx.Response(200, json={"error": ["EAPI:Permission denied"], "result": {}})
            values = parse_qs(request.content.decode()) if request.method == "POST" else dict(request.url.params)
            if request.method == "POST":
                values.pop("nonce")
                values = {name: value[0] for name, value in values.items()}
            return httpx.Response(200, json=rest(method, values))

        transport = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        collector = KrakenReadOnlyConformance(authority, **kwargs, client=transport)

        class FixtureCollector:
            async def collect(self, directory):
                try:
                    return await collector.collect(directory)
                finally:
                    await transport.aclose()

        return FixtureCollector()

    owner = tmp_path / "owner"
    try:
        result = asyncio.run(onboarding._collect_owner_read_only(
            database, owner, api_key=key, api_secret=secret,
            authorization=onboarding.AUTHORIZATION, collector_factory=factory,
        ))
        response = client.get("/api/v1/progress", headers=headers)
        assert response.status_code == 200
        current = response.json()
        run = next(item for item in current["test_runs"] if item["run_id"] == result["run_id"])
        account = run["account_observation"]
        assert run["label"] == "Kraken read-only account check"
        assert run["synthetic"] and run["status"] == "incomplete"
        assert account["historical"] and account["freshness"] == "fresh"
        assert account["transport_basis"] == "injected_transport"
        assert account["authenticated_private_read_count"] == 0
        assert account["verified_completed_stages"] == (
            ["instruments", "account_fees"] if partial else
            ["instruments", "account_fees", "balances", "open_orders", "native_history"]
        )
        assert "Financial account reconciliation remains unverified." in account["pending_checks"]
        cli = importlib.import_module("trade_graph.cli")
        summary = cli._kraken_summary(result)
        assert "Financial account reconciliation remains unverified." in summary["pending"]
        assert "Order lookups were not requested." in summary["pending"] or partial
        assert current["project"] == baseline["project"]
        assert counts() == before
        assert client.get("/api/v1/health", headers=headers).json()["live_enabled"] is False
        check = next(item for item in current["checks"] if item["id"] == "kraken-account")
        assert check["available"] is False
        assert client.post("/api/v1/progress/tests/kraken-account/run", headers=headers).status_code == 400
        page = client.get("/progress", headers=headers)
        assert page.status_code == 200 and "Kraken read-only account check" in page.text
        published = response.text + page.text + json.dumps(result) + json.dumps(summary)
        stored = (runtime_path / "progress-runs.sqlite").read_bytes()
        for private in (key, secret, "owner-local-kraken", str(owner), "available_balances", "held_balances"):
            assert private not in published and private.encode() not in stored
        assert "EAPI:Permission denied" not in published
    finally:
        runtime.database.close()
