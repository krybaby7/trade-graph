"""Dashboard rendering preserves financial labels and escaped evidence navigation."""

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "trade_graph" / "web" / "templates"


def render(name: str, data: dict | None = None, **context) -> str:
    environment = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    return environment.get_template(name).render(data=data or {}, page=name.removesuffix(".html"), **context)


@pytest.mark.parametrize(
    "name", ["overview", "trading", "organization", "costs", "changes", "evidence", "owner", "login"]
)
def test_pages_render_empty_journal_without_invented_values(name: str) -> None:
    page = render(f"{name}.html", role="owner", csrf="test-csrf", auth_mechanism="cookie")
    assert '<main id="main"' in page
    assert '<meta name="viewport"' in page
    assert 'href="/static/dashboard.css"' in page
    assert 'src="/static/dashboard.js"' in page
    assert "Paper results are simulated" in page
    assert "10000" not in page


def test_overview_keeps_precise_values_and_actual_expense_currency() -> None:
    page = render(
        "overview.html",
        {
            "reporting_currency": "USD",
            "equity": "10000.000000000000000001",
            "provisional": True,
            "actual_spend": "9.12",
            "actual_spend_currency": "EUR",
            "performance": {"trading_pnl": "-0.000000000000000001", "net_economic_pnl": None},
        },
    )
    assert "10000.000000000000000001" in page
    assert "-0.000000000000000001" in page
    assert "Provisional financial result" in page
    assert "Simulated trading P&amp;L" in page
    assert "Simulated net-economic P&amp;L" in page
    actual_metric = page.split('class="metric actual"', 1)[1].split("</article>", 1)[0]
    assert "9.12" in actual_metric and "EUR" in actual_metric and "USD" not in actual_metric


def test_decision_and_change_evidence_are_escaped_and_navigable() -> None:
    payload = '<img src=x onerror="alert(1)">'
    decision = render(
        "evidence.html",
        {
            "decision": {"decision_id": "d1", "rationale": payload, "action": "HOLD"},
            "research": [{"summary": payload}],
            "orders": [{"decision_id": "d1", "state": "UNKNOWN"}],
        },
    )
    assert payload not in decision
    assert "&lt;img" in decision
    assert 'href="/decisions/d1"' in decision
    change = render(
        "evidence.html",
        {"candidate": {"candidate_id": "c1", "state": "TESTED"}, "artifacts": [{"path": "x", "diff": payload}]},
    )
    assert payload not in change and "&lt;img" in change
    assert "No independent attestation is recorded" in change


@pytest.mark.parametrize(
    "role,mechanism,exposed",
    [("owner", "cookie", True), ("leader", "cookie", True), ("owner", "bearer", False), ("reader", "cookie", False)],
)
def test_csrf_is_exposed_only_for_cookie_write_identity(role: str, mechanism: str, exposed: bool) -> None:
    page = render(
        "overview.html", role=role, csrf="csrf-public-proof", auth_mechanism=mechanism, token="private-session-token"
    )
    assert ("csrf-public-proof" in page) is exposed
    assert "private-session-token" not in page


def test_tasks_show_real_lease_states_and_leader_controls_only() -> None:
    data = {
        "leader_revision": 7,
        "tasks": [
            {
                "task_id": "t1",
                "role": "research",
                "objective": "Observe a market",
                "status": "BLOCKED",
                "lease_owner": "worker-1",
                "lease_expired": True,
                "overdue": True,
            }
        ],
        "decisions": [{"decision_id": "d1", "action": "HOLD", "rationale": "Await recorded input"}],
    }
    leader = render("organization.html", data, role="leader", csrf="proof")
    assert "worker-1" in leader and "Lease expired" in leader and "Overdue" in leader
    assert 'data-leader-task data-revision="7"' in leader
    assert 'href="/decisions/d1"' in leader
    reader = render("organization.html", data, role="reader")
    assert "data-leader-task" not in reader


def test_cost_and_order_projections_preserve_uncertainty_and_native_units() -> None:
    costs = render(
        "costs.html",
        {
            "actual_spend": "2.50",
            "currency": "EUR",
            "provisional": True,
            "uncertain_reservations": 1,
            "receipts": [{"receipt_id": "r1", "role": "engineer", "synthetic": True, "status": "uncertain"}],
        },
    )
    assert "Provisional cost evidence" in costs and "does not refill" in costs
    assert "Unresolved usage is not free" in costs and "Synthetic" in costs and "engineer" in costs
    trading = render(
        "trading.html",
        {
            "balances": [{"asset": "BTC", "owned": "0.123456789", "reserved": "0.01", "available": "0.113456789"}],
            "orders": [{"symbol": "BTC/USD", "state": "UNKNOWN", "degraded": True}],
        },
    )
    assert "0.123456789" in trading and "BTC" in trading
    assert "UNKNOWN" in trading and "Degraded" in trading
    assert "An unknown outcome is not a rejection" in trading


def test_receipt_native_expense_keeps_currency_separate_from_reporting_value() -> None:
    page = render(
        "costs.html",
        {
            "receipts": [
                {"native_cost": "1.25", "native_currency": "USD", "reporting_cost": "1.10", "reporting_currency": "EUR"}
            ]
        },
    )
    native = page.split("<dt>Native expense</dt>", 1)[1].split("</dd>", 1)[0]
    reporting = page.split("<dt>Reporting expense</dt>", 1)[1].split("</dd>", 1)[0]
    assert "1.25" in native and "USD" in native and "EUR" not in native
    assert "1.10" in reporting and "EUR" in reporting


def test_research_and_lessons_display_the_persisted_contract() -> None:
    page = render(
        "organization.html",
        {
            "research": {
                "findings": [{"question": "What changed?", "claim": "Observed spread widened", "stale": True}]
            },
            "lessons": {"lessons": [{"observation": "A missed fill", "explanation": "Queue position was uncertain"}]},
        },
    )
    assert "What changed?" in page and "Observed spread widened" in page
    assert "A missed fill" in page and "Queue position was uncertain" in page


def test_owner_has_explicit_emergency_manage_only_control() -> None:
    page = render("owner.html", role="owner", csrf="proof")
    assert "data-emergency" in page
    emergency = page.split("data-emergency", 1)[1].split("</form>", 1)[0]
    assert 'value="MANAGE_ONLY"' in emergency
    assert "FLATTEN" not in emergency
