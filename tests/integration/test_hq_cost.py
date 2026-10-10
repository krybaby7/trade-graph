"""HQ reads stay cheap: no expensive projections, and money comes from a fingerprinted cache (spec §B.9)."""

import pytest
from tests.integration.test_dashboard import _client

from trade_graph.api import activity, controls, financial, health, hq, live, review_export
from trade_graph.api.hq_contract import validate_snapshot
from trade_graph.api.owner_expenses import record
from trade_graph.application.service_controller import ServiceController
from trade_graph.domain.clock import utc_iso

POISONED = (
    (activity, "overview"), (activity, "_attempts"), (live, "overview"), (live, "health"), (health, "health"),
    (controls, "configuration"), (review_export, "export"), (ServiceController, "status"),
    (ServiceController, "_readiness"),
)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    client, database, *_ = _client(tmp_path)
    runtime = client.app.state.runtime
    runtime.clock.advance(3600)
    for owner, name in POISONED:
        monkeypatch.setattr(owner, name, lambda *args, _name=name, **kwargs: pytest.fail(f"HQ called {_name}"))
    yield runtime
    database.close()


def test_snapshot_uses_cache_and_never_runs_expensive_projections(runtime, monkeypatch):
    calls = []
    original = financial.overview

    def counted(reader):
        calls.append(reader)
        return original(reader)

    monkeypatch.setattr(financial, "overview", counted)
    viewer = hq.Viewer("owner", "cookie", True)
    first = hq.snapshot(runtime, viewer)
    validate_snapshot(first)
    assert len(calls) == 1
    runtime.clock.advance(20)
    second = hq.snapshot(runtime, viewer)
    assert len(calls) == 1
    assert second["money"]["as_of"] == first["money"]["as_of"]
    assert second["as_of"] != first["as_of"]
    start = runtime.database.execute("SELECT created_at FROM portfolios WHERE portfolio_id=?",
                                     (runtime.portfolio_id,)).fetchone()[0]
    record(runtime, {"record_type": "completeness", "request_id": "hq-cost-fingerprint", "period_start": start,
                     "period_end": utc_iso(runtime.clock.now()), "expense_kinds": ["other"],
                     "evidence_ref": "owner-evidence:synthetic-complete"})
    third = hq.snapshot(runtime, viewer)
    assert len(calls) == 2
    assert third["money"]["as_of"] != first["money"]["as_of"]
    assert "other_costs_unconfirmed" not in [item["code"] for item in third["money"]["pays_for_itself"]["missing"]]


def test_story_page_and_reader_views_are_also_cheap(runtime):
    reader = hq.Viewer("reader", "bearer", False)
    data = hq.snapshot(runtime, reader, calendar_days=14)
    validate_snapshot(data)
    page = hq.story_page(runtime, reader, before=data["story"]["window_start"])
    assert set(page) == {"story"}


def test_money_failure_is_unknown_not_zero(runtime, monkeypatch):
    def broken(*args, **kwargs):
        raise ArithmeticError("synthetic valuation failure")

    monkeypatch.setattr(live, "economics", broken)
    data = hq.snapshot(runtime, hq.Viewer("owner", "cookie", True))
    validate_snapshot(data)
    money = data["money"]
    assert money["available"] is False and money["unavailable_reason"]
    assert money["practice_value"] is None and money["change"]["total"] is None
    assert money["pays_for_itself"] == {"status": "unknown", "net": None, "missing": [], "honest_note": None}
    assert "not known yet" in "".join(segment.get("v", "") for segment in data["now"]["detail"])
