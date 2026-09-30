"""CSRF follows the mechanism that authenticated the write, not header presence."""

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_api_security_live import _Runtime

from trade_graph.api.app import create_app
from trade_graph.api.auth import issue_session


@pytest.mark.parametrize("authorization", [None, "Basic arbitrary", "nonsense", "Bearer", "Bearer a b"])
@pytest.mark.parametrize(
    "role,path",
    [
        ("owner", "/api/v1/owner/pause"),
        ("owner", "/api/v1/owner/resume"),
        ("owner", "/api/v1/owner/budgets"),
        ("owner", "/api/v1/owner/enable-live"),
        ("leader", "/api/v1/leader/activate"),
    ],
)
def test_cookie_write_requires_csrf_with_arbitrary_headers(tmp_path, authorization, role, path):
    runtime = _Runtime(tmp_path)
    client = TestClient(create_app(runtime))
    token, _ = issue_session(runtime.database, runtime.clock, role)
    client.cookies.set("tg_session", token)
    before = list(runtime.database.connection.iterdump())
    headers = {} if authorization is None else {"Authorization": authorization}
    response = client.post(path, headers=headers, json={"profile": "MANAGE_ONLY"})
    assert response.status_code == 403
    assert response.json()["detail"] == "csrf"
    assert list(runtime.database.connection.iterdump()) == before


@pytest.mark.parametrize("mechanism", ["cookie", "bearer", "lowercase-bearer"])
def test_legitimate_write_succeeds(tmp_path, mechanism):
    runtime = _Runtime(tmp_path)
    client = TestClient(create_app(runtime))
    token, csrf = issue_session(runtime.database, runtime.clock, "owner")
    client.cookies.set("tg_session", token)
    headers = (
        {"X-CSRF-Token": csrf, "Authorization": "Basic ignored"}
        if mechanism == "cookie"
        else {"Authorization": f"{'Bearer' if mechanism == 'bearer' else 'bearer'} {token}"}
    )
    response = client.post("/api/v1/owner/pause", headers=headers, json={"profile": "MANAGE_ONLY"})
    assert response.status_code == 200
    assert runtime.execution.profile(runtime.portfolio_id) == "MANAGE_ONLY"


def test_valid_leader_csrf_reaches_controller_and_bad_bearer_never_falls_back(tmp_path):
    runtime = _Runtime(tmp_path)
    client = TestClient(create_app(runtime))
    token, csrf = issue_session(runtime.database, runtime.clock, "leader")
    client.cookies.set("tg_session", token)
    assert client.post("/api/v1/leader/activate", headers={"X-CSRF-Token": csrf}, json={}).status_code == 422
    assert (
        client.post("/api/v1/leader/activate", headers={"Authorization": "Bearer invalid"}, json={}).status_code == 401
    )
