from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from trade_graph.adapters.brokers.kraken_live import KrakenLiveBroker
from trade_graph.adapters.persistence.db import Database
from trade_graph.adapters.research.fetch import assert_public_url, strip_active_content
from trade_graph.api.app import create_app
from trade_graph.api.auth import issue_session
from trade_graph.application.ledger import Ledger
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import LiveDisabled, UnsafeTarget
from trade_graph.isolation import promote_staged, run_plugin, validate_plugin
from trade_graph.live_gate import evaluate_live_enablement


class _Runtime:
    def __init__(self, tmp_path) -> None:
        self.clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
        self.database = Database(tmp_path / "api.sqlite")
        self.ledger = Ledger(self.database, self.clock)
        self.portfolio_id = self.ledger.create_portfolio(reporting_currency="EUR")
        self.ledger.deposit(self.portfolio_id, "EUR", Decimal("100"), "open")
        self.actual_spend = Decimal("0")
        self.execution = type("E", (), {})()
        from trade_graph.adapters.brokers.paper import PaperBroker
        from trade_graph.application.execution import Execution

        self.execution = Execution(self.database, self.ledger, self.clock, PaperBroker(self.database, self.clock))


def test_dashboard_reconciles_and_rejects_bad_auth(tmp_path) -> None:
    runtime = _Runtime(tmp_path)
    app = create_app(runtime)
    client = TestClient(app)
    assert client.get("/api/v1/overview").status_code == 401
    token, csrf = issue_session(runtime.database, runtime.clock, "owner")
    headers = {"Authorization": f"Bearer {token}"}
    overview = client.get("/api/v1/overview", headers=headers)
    assert overview.status_code == 200
    assert overview.json()["equity"] == "100"
    assert overview.json()["simulated"] is True
    page = client.get("/", headers=headers)
    assert page.status_code == 200
    assert "100" in page.text
    assert "Actual operating spend" in page.text
    leader, _ = issue_session(runtime.database, runtime.clock, "leader")
    denied = client.post("/api/v1/owner/budgets", headers={"Authorization": f"Bearer {leader}"}, json={"total": "9"})
    assert denied.status_code == 403
    client.cookies.set("tg_session", token)
    cookie_denied = client.post("/api/v1/owner/budgets", json={"total": "9"})
    assert cookie_denied.status_code == 403
    allowed = client.post(
        "/api/v1/owner/budgets",
        headers={"X-CSRF-Token": csrf},
        json={"total": "9"},
    )
    assert allowed.status_code == 200
    live = client.post(
        "/api/v1/owner/enable-live",
        headers=headers,
        json={"paper_capital": "10000", "live_allocation": "0"},
    )
    assert live.json()["enabled"] is False
    health = client.get("/api/v1/health")
    assert health.json()["live_enabled"] is False


def test_fetch_blocks_private_and_metadata() -> None:
    def resolver(host: str) -> list[str]:
        return {
            "example.test": ["93.184.216.34"],
            "metadata": ["169.254.169.254"],
            "local": ["127.0.0.1"],
        }[host]

    assert_public_url("https://example.test/a", resolver)
    with pytest.raises(UnsafeTarget):
        assert_public_url("https://metadata/latest", resolver)
    with pytest.raises(UnsafeTarget):
        assert_public_url("https://local/x", resolver)
    with pytest.raises(UnsafeTarget):
        assert_public_url("http://example.test/a", resolver)
    text = strip_active_content("<script>buy</script><p>note</p>")
    assert "buy" not in text
    assert "note" in text


def test_live_gate_and_adapter_have_no_withdrawal(tmp_path) -> None:
    closed = evaluate_live_enablement(
        {
            "paper_capital": "10000",
            "live_allocation": "10000",
            "allocation_copied_from_paper": True,
            "uses_paper_capital_as_live_allocation": True,
            "withdrawals_allowed": False,
        }
    )
    assert closed["enabled"] is False
    diagnostic = evaluate_live_enablement(
        {
            "paper_capital": "10000",
            "live_allocation": "25",
            "allocation_is_owner_set": True,
            "withdrawals_allowed": False,
            "eligibility_confirmed": True,
            "read_only_reconciliation_passed": True,
            "operating_budget_set": True,
            "owner_confirmed": True,
            "diagnostic_pilot": True,
            "economic_verdict": "insufficient_evidence",
        }
    )
    assert diagnostic["enabled"] is True
    assert diagnostic["diagnostic"] is True
    calls = []

    def transport(method: str, body: dict) -> dict:
        calls.append((method, body))
        if method == "AddOrder":
            return {"result": {"txid": ["ABC"]}}
        if method == "QueryOrders":
            return {"result": {"ABC": {"status": "open", "vol_exec": "0"}}}
        return {"result": {}}

    broker = KrakenLiveBroker(transport, live_enabled=False, key_present=False)
    capabilities = __import__("asyncio").run(broker.capabilities())
    assert capabilities.withdrawals is False
    assert capabilities.native_stop_tested is False
    assert not hasattr(broker, "withdraw")
    with pytest.raises(LiveDisabled):
        __import__("asyncio").run(
            broker.submit(
                __import__("trade_graph.contracts.models", fromlist=["AuthorizedOrderIntent"]).AuthorizedOrderIntent(
                    intent_id="i",
                    portfolio_id="p",
                    account_id="a",
                    venue="kraken",
                    mode="live",
                    client_order_id="cid",
                    symbol="BTC/USD",
                    side="buy",
                    order_type="market",
                    quantity="0.01",
                    snapshot_id="s",
                    eligible_after_utc=datetime(2026, 1, 1, tzinfo=UTC),
                )
            )
        )
    broker.live_enabled = True
    broker.key_present = True
    result = __import__("asyncio").run(
        broker.submit(
            __import__("trade_graph.contracts.models", fromlist=["AuthorizedOrderIntent"]).AuthorizedOrderIntent(
                intent_id="i",
                portfolio_id="p",
                account_id="a",
                venue="kraken",
                mode="live",
                client_order_id="cid",
                symbol="BTC/USD",
                side="buy",
                order_type="limit",
                quantity="0.01",
                limit_price="100",
                snapshot_id="s",
                eligible_after_utc=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )
    assert result.venue_order_id == "ABC"
    assert calls[-1][0] == "AddOrder"
    assert "withdraw" not in calls[-1][1]


def test_kernel_process_hides_secret() -> None:
    from multiprocessing import Pipe, Process

    from trade_graph.isolation import kernel_loop

    parent, child = Pipe()
    process = Process(target=kernel_loop, args=(child, "super-secret"))
    process.start()
    parent.send({"op": "get_secret"})
    assert parent.recv()["error"] == "denied"
    parent.send({"op": "withdraw"})
    assert parent.recv()["error"] == "denied"
    parent.send({"op": "pause", "profile": "MANAGE_ONLY"})
    assert parent.recv() == {"ok": True, "profile": "MANAGE_ONLY"}
    parent.send({"op": "stop"})
    assert parent.recv()["ok"] is True
    process.join(5)
    assert process.exitcode == 0


def test_plugin_and_kernel_promotion() -> None:
    with pytest.raises(PermissionError):
        validate_plugin("import os\ndef on_snapshot(s):\n    return os.environ\n")
    result = run_plugin("def on_snapshot(snapshot):\n    return {'value': snapshot['value'] + 1}\n", {"value": 1})
    assert result == {"value": 2}
    with pytest.raises(RuntimeError):
        promote_staged({"content_hash": "abc", "attestation": {"runner": "candidate"}}, controller_healthy=True)
    with pytest.raises(RuntimeError):
        promote_staged(
            {"content_hash": "abc", "attestation": {"runner": "trusted-controller"}},
            controller_healthy=False,
        )
