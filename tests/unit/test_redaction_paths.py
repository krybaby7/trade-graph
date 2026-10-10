"""The redaction allowlist for app routes is anchored to whole path segments (S6)."""

import pytest

from trade_graph.api.security import redact


@pytest.mark.parametrize("value", [
    "/journal.sqlite", "/journal-private/notes.txt", "/owner.key", "/progress-x/a",
])
def test_private_paths_that_start_like_routes_are_redacted(value):
    assert redact(value) == "[private path]"


@pytest.mark.parametrize("value", [
    "/journal", "/costs.", "see /costs, then", "Use /owner.", "(/trading)", "/progress#x",
    "/api/v1/hq?calendar_days=7", "/changes/c-1", "/static/hq.css", "/login?next=/",
])
def test_app_routes_survive_redaction(value):
    assert redact(value) == value
