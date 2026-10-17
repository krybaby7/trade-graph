"""GET /api/v1/hq/wakes (T39): any signed-in role reads the wake log, queries are validated, reading writes nothing.

Synthetic paper account and synthetic wake records written through the event engine's store; no network, key, model,
paid call or real order.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests.chart_support import runtime as app_runtime
from tests.day_plan_support import day_plan_account
from tests.wake_log_support import Wakes, dump_sha256

from trade_graph.api.app import create_app
from trade_graph.api.auth import issue_session
from trade_graph.api.hq_wakes_contract import PAGE_MAX, SCHEMA, validate_wake_log


@pytest.fixture
def served(tmp_path):
    account = day_plan_account(tmp_path)
    wakes = Wakes(account)
    made = [wakes.wake("trader", [wakes.trigger("entry_filled")]) for _ in range(3)]
    owner, _ = issue_session(account.database, account.clock, "owner")
    reader, _ = issue_session(account.database, account.clock, "reader")
    client = TestClient(create_app(app_runtime(account, None)))
    return client, account, owner, reader, made


def test_any_signed_in_role_reads_the_wake_log_and_nobody_else(served) -> None:
    client, _, owner, reader, made = served
    response = client.get("/api/v1/hq/wakes", follow_redirects=False)
    assert response.status_code == 401 and response.json() == {"detail": "authentication required"}
    for token in (owner, reader):
        response = client.get("/api/v1/hq/wakes", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        document = response.json()
        validate_wake_log(document)
        assert document["schema"] == SCHEMA and [item["id"] for item in document["items"]] == made[::-1]
    policy = response.headers["content-security-policy"]
    assert "script-src 'self'" in policy and "default-src 'self'" in policy


def test_an_older_page_continues_below_the_cursor(served) -> None:
    client, _, owner, _, made = served
    headers = {"Authorization": f"Bearer {owner}"}
    first = client.get("/api/v1/hq/wakes", params={"limit": 2}, headers=headers).json()
    assert [item["id"] for item in first["items"]] == made[:0:-1] and first["next_before"] is not None
    older = client.get("/api/v1/hq/wakes", params={"before": first["next_before"], "limit": 2}, headers=headers).json()
    validate_wake_log(older)
    assert [item["id"] for item in older["items"]] == made[:1] and older["day"] is None
    assert older["next_before"] is None


@pytest.mark.parametrize("params", [{"before": 0}, {"before": -3}, {"before": "x"}, {"limit": 0},
                                    {"limit": PAGE_MAX + 1}, {"limit": "many"}])
def test_queries_are_validated(served, params) -> None:
    client, _, owner, *_ = served
    response = client.get("/api/v1/hq/wakes", params=params, headers={"Authorization": f"Bearer {owner}"})
    assert response.status_code == 422
    assert response.json()["detail"] in {"invalid wake log query", "invalid request parameters"}


def test_reading_through_the_route_writes_nothing(served) -> None:
    client, account, owner, *_ = served
    before = dump_sha256(account.database)
    for _ in range(3):
        assert client.get("/api/v1/hq/wakes", headers={"Authorization": f"Bearer {owner}"}).status_code == 200
    assert dump_sha256(account.database) == before
