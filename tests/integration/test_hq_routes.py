"""HQ is the signed-in home; the old home moves unchanged to /journal (spec §A.1, §A.2, §B.11, §E.1 WP4)."""

import json
import re
from pathlib import Path

import pytest
from tests.integration.test_dashboard import _client

from trade_graph.api import resources
from trade_graph.api.hq_contract import SCHEMA, validate_snapshot
from trade_graph.api.security import redact
from trade_graph.domain.clock import utc_iso

WEB = Path(__file__).resolve().parents[2] / "src" / "trade_graph" / "web"
ISLAND = re.compile(r'<script type="application/json" id="hq-snapshot">(.*?)</script>', re.DOTALL)


@pytest.fixture
def env(tmp_path, monkeypatch):
    client, database, portfolio, token, csrf, leader = _client(tmp_path)
    client.app.state.runtime.clock.advance(86400)
    # The host's own resource counters never leak into these tests (review T14).
    monkeypatch.setattr(resources, "observation",
                        lambda runtime: {"observed_at": utc_iso(runtime.clock.now()), "warnings": []})
    yield client, token, csrf, leader
    database.close()


def _island(page: str) -> dict:
    match = ISLAND.search(page)
    assert match, "the HQ page carries its JSON island"
    return json.loads(match.group(1))


def test_home_is_hq_for_owner_bearer_and_cookie(env):
    client, token, csrf, _ = env
    bearer = client.get("/", headers={"Authorization": f"Bearer {token}"})
    assert bearer.status_code == 200
    assert "data-hq-root" in bearer.text and 'id="hq-snapshot"' in bearer.text
    assert '<meta name="csrf-token"' not in bearer.text
    island = _island(bearer.text)
    assert island["schema"] == SCHEMA and island["viewer"]["auth"] == "bearer"
    validate_snapshot(island)
    client.cookies.set("tg_session", token)
    cookie = client.get("/")
    assert cookie.status_code == 200
    assert "data-hq-root" in cookie.text and 'id="hq-snapshot"' in cookie.text
    assert f'<meta name="csrf-token" content="{csrf}">' in cookie.text
    island = _island(cookie.text)
    assert island["viewer"]["can_act"] is True and island["viewer"]["auth"] == "cookie"
    validate_snapshot(island)


def test_home_csp_and_private_headers(env):
    client, token, *_ = env
    response = client.get("/", headers={"Authorization": f"Bearer {token}"})
    policy = response.headers["content-security-policy"]
    assert "script-src 'self'" in policy and "style-src 'self'" in policy and "default-src 'self'" in policy
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"


def test_leader_sees_hq_read_only_without_csrf_meta(env):
    client, _, _, leader = env
    response = client.get("/", headers={"Authorization": f"Bearer {leader}"})
    assert response.status_code == 200
    island = _island(response.text)
    assert island["viewer"]["role"] == "leader" and island["viewer"]["can_act"] is False
    assert island["decisions"] == [] and island["checkins"] is None
    assert '<meta name="csrf-token"' not in response.text
    assert 'href="/owner"' not in response.text


def test_journal_is_the_old_home_unchanged(env):
    client, token, *_ = env
    headers = {"Authorization": f"Bearer {token}"}
    page = client.get("/journal", headers=headers)
    assert page.status_code == 200
    for marker in ("Operational health", "Trading covers costs", "data-live-home", "review-export"):
        assert marker in page.text
    assert 'aria-current="page">Account journal (detailed numbers)</a>' in page.text
    assert "subject to protected owner policy" in page.text  # the journal keeps the base footer (U15)
    assert client.get("/journal", params={"end_at": "2026-01-01T12:00:00Z"}, headers=headers).status_code == 200
    for invalid in ("bad", "2026-01-01T12:00:00", "2025-01-01T00:00:00Z", "2030-01-01T00:00:00Z"):
        response = client.get("/journal", params={"end_at": invalid}, headers=headers)
        assert response.status_code == 422, invalid
        assert response.json() == {"detail": "invalid reporting cutoff"}


def test_hq_json_routes_authenticate_and_validate_parameters(env):
    client, token, *_ = env
    for path in ("/api/v1/hq", "/api/v1/hq/story?before=2026-01-01T00:00:00Z"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 401
        assert response.json() == {"detail": "authentication required"}
    headers = {"Authorization": f"Bearer {token}"}
    data = client.get("/api/v1/hq", headers=headers)
    assert data.status_code == 200 and data.headers["cache-control"] == "no-store"
    validate_snapshot(data.json())
    assert len(data.content) <= 80 * 1024
    assert client.get("/api/v1/hq", params={"calendar_days": 14}, headers=headers).status_code == 200
    for days in ("0", "15", "x"):
        response = client.get("/api/v1/hq", params={"calendar_days": days}, headers=headers)
        assert response.status_code == 422 and response.json() == {"detail": "invalid request parameters"}
    story = client.get("/api/v1/hq/story", params={"before": "2026-01-02T00:00:00Z", "limit": 5}, headers=headers)
    assert story.status_code == 200 and isinstance(story.json()["story"]["items"], list)
    for params in ({"before": "bad"}, {"before": "2026-01-02T00:00:00"}, {},
                   {"before": "2026-01-02T00:00:00Z", "limit": 0},
                   {"before": "2026-01-02T00:00:00Z", "limit": 61}):
        response = client.get("/api/v1/hq/story", params=params, headers=headers)
        assert response.status_code == 422, params
        assert "bad" not in response.text


@pytest.mark.parametrize("before", ["0001-01-02T00:00:00Z", "0001-01-01T00:00:00+00:00",
                                    "9999-12-31T23:59:59-01:00"])
def test_story_route_refuses_out_of_range_dates_without_a_server_error(env, before):
    client, token, *_ = env
    response = client.get("/api/v1/hq/story", params={"before": before},
                          headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid story query"}
    assert "Traceback" not in response.text


def test_journal_survives_redaction_and_is_linked(env):
    client, token, *_ = env
    assert redact("/journal") == "/journal"
    assert redact("Open /journal?end_at=2026-01-01T00:00:00Z") == "Open /journal?end_at=2026-01-01T00:00:00Z"
    assert redact("/home/private/notes") == "[private path]"
    data = client.get("/api/v1/hq", headers={"Authorization": f"Bearer {token}"}).json()
    assert data["links"]["journal"] == "/journal"
    assert "[private path]" not in json.dumps(data)


def test_navigation_lists_hq_and_detailed_views(env):
    client, token, *_ = env
    headers = {"Authorization": f"Bearer {token}"}
    home = client.get("/", headers=headers).text
    assert 'aria-label="Trade Graph HQ"' in home and "Trade Graph HQ<small>Paper operations</small>" in home
    assert 'href="/" aria-current="page">HQ</a>' in home
    assert '<details class="nav-more">' in home and "Detailed views</summary>" in home
    for path, label in (("/journal", "Account journal (detailed numbers)"),
                        ("/progress", "Mission Control (build progress &amp; tests)"),
                        ("/trading", "Trading records"), ("/organization", "Organisation records"),
                        ("/costs", "Costs"), ("/changes", "Improvements"), ("/owner", "Owner controls")):
        assert f'href="{path}">{label}</a>' in home, path
    progress = client.get("/progress", headers=headers).text
    assert 'href="/progress" aria-current="page">Mission Control (build progress &amp; tests)</a>' in progress
    assert 'class="nav-link active">Detailed views</summary>' in progress


def test_journal_links_point_to_journal_not_home():
    live_js = (WEB / "static" / "live.js").read_text()
    assert '"/journal?end_at="' in live_js and '"/?end_at="' not in live_js
    home = (WEB / "templates" / "live_home.html").read_text()
    assert '<a href="/journal">Through now</a>' in home and '<a href="/">' not in home
