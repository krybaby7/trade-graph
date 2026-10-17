"""Long-lived caching for the vendored chart library (W8-C, D253; W5-B's follow-up).

HQ sends ``Cache-Control: no-store`` everywhere. The one exception is the pinned, vendored TradingView Lightweight
Charts release asked for by its version (``?v=<VENDOR.json version>``, the URL hq-charts.js loads): it never changes
under that URL, so a browser may keep it for a year instead of fetching 198 KB on every page load. Everything else,
the same file without its version and any other version, stays ``no-store``. Synthetic account; no network call.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.hq_trading_support import runtime_for
from tests.money_limit_support import open_account

from trade_graph.api import static_cache
from trade_graph.api.app import create_app
from trade_graph.api.auth import issue_session

WEB = Path(__file__).resolve().parents[2] / "src" / "trade_graph" / "web"
VENDOR = WEB / "static" / "vendor" / "lightweight-charts"
SCRIPT = "/static/vendor/lightweight-charts/lightweight-charts.standalone.production.js"
VERSION = json.loads((VENDOR / "VENDOR.json").read_text(encoding="utf-8"))["version"]
LONG = "public, max-age=31536000, immutable"


@pytest.fixture
def client(tmp_path):
    account = open_account(tmp_path)
    token, _ = issue_session(account.database, account.clock, "owner")
    yield TestClient(create_app(runtime_for(account))), token


def test_the_pinned_library_asked_for_by_its_version_is_cached_for_a_year(client) -> None:
    http, _ = client
    response = http.get(SCRIPT, params={"v": VERSION})
    assert response.status_code == 200 and response.headers["cache-control"] == LONG
    assert response.content == (VENDOR / "lightweight-charts.standalone.production.js").read_bytes()
    # The page's other protections stay on the cached file too.
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "script-src 'self'" in response.headers["content-security-policy"]


@pytest.mark.parametrize("url", [SCRIPT, f"{SCRIPT}?v=0.0.1", f"{SCRIPT}?v={VERSION}&x=1",
                                 "/static/vendor/lightweight-charts/LICENSE.txt",
                                 f"/static/vendor/lightweight-charts/VENDOR.json?v={VERSION}",
                                 f"/static/hq.js?v={VERSION}", f"/static/vendor/elsewhere/x.js?v={VERSION}"])
def test_everything_else_stays_no_store(client, url) -> None:
    http, _ = client
    assert http.get(url).headers["cache-control"] == "no-store"


def test_the_api_and_the_page_stay_no_store(client) -> None:
    http, token = client
    for url in ("/api/v1/hq/plans", "/api/v1/hq"):
        response = http.get(url, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert http.get("/api/v1/hq/plans", params={"v": VERSION}).headers["cache-control"] == "no-store"


def test_the_loader_asks_for_the_vendored_version() -> None:
    loader = (WEB / "static" / "hq-charts.js").read_text(encoding="utf-8")
    match = re.search(r'src: "([^"]+)"', loader)
    assert match and match.group(1) == f"{SCRIPT}?v={VERSION}"
    assert static_cache.vendor_versions(WEB / "static" / "vendor") == {"lightweight-charts": VERSION}


def test_a_request_with_no_or_a_malformed_version_is_never_long_lived() -> None:
    versions = {"lightweight-charts": VERSION}
    rule = static_cache.cache_control
    assert rule(SCRIPT, [("v", VERSION)], 200, versions) == LONG
    assert rule(SCRIPT, [("v", VERSION)], 304, versions) == LONG
    assert rule(SCRIPT, [("v", VERSION)], 404, versions) == "no-store"
    assert rule(SCRIPT, [], 200, versions) == "no-store"
    assert rule(SCRIPT, [("v", VERSION), ("v", VERSION)], 200, versions) == "no-store"
    assert rule("/static/vendor/lightweight-charts/../hq.js", [("v", VERSION)], 200, versions) == "no-store"
    assert rule(SCRIPT, [("v", VERSION)], 200, {}) == "no-store"
