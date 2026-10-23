"""Dashboard admission never invokes models and preserves the protected worker binding."""

from types import SimpleNamespace

import pytest
from tests.integration.test_api_security_live import _Runtime

from trade_graph.application.subscription_profile import SubscriptionAdmission
from trade_graph.dashboard import dashboard_runtime


def test_protected_paper_dashboard_admission_is_fixed_and_pin_change_blocks_ai(tmp_path, monkeypatch):
    fixture = _Runtime(tmp_path)
    owner = tmp_path / "owner"
    owner.mkdir()
    observed = []
    monkeypatch.setattr("trade_graph.application.deployment_runtime._owner_bundle",
                        lambda path: (SimpleNamespace(assert_current=lambda: None), "synthetic", b"x" * 32))
    monkeypatch.setattr("trade_graph.kernel.deployment_image.read_owner_file",
                        lambda *args: b"{}")
    admission = SubscriptionAdmission(None, SimpleNamespace(public_status=lambda **_kw: {
        "ready": True, "available_subscription_routes": ["claude_subscription"]}), {
        "ready": True, "selected_provider": "claude_subscription", "blockers": [], "quota": {}}, "1" * 64)
    monkeypatch.setattr("trade_graph.application.subscription_profile.load_subscription_profile",
                        lambda path: observed.append(path) or admission)
    current = [True]
    monkeypatch.setattr("trade_graph.application.subscription_profile.subscription_profile_unchanged",
                        lambda *args: current[0])
    runtime = dashboard_runtime(fixture.database.path, protected_owner=owner)
    status = runtime.service_controller.status()
    assert status["prerequisites"]["ai_available"] is True
    assert status["prerequisites"]["selected_provider"] == "claude_subscription"
    assert runtime.service_controller.protected_owner == owner
    assert observed == [owner]
    current[0] = False
    status = runtime.service_controller.status()
    assert status["prerequisites"]["ai_available"] is False
    assert status["prerequisites"]["paper_available"] is True
    assert observed == [owner]
    assert fixture.database.execute("SELECT COUNT(*) FROM subscription_invocations").fetchone()[0] == 0
    runtime.database.close()


def test_protected_dashboard_rejects_ambient_config_before_admission(tmp_path):
    with pytest.raises(ValueError, match="owner directory"):
        dashboard_runtime(tmp_path / "missing.sqlite", protected_owner=tmp_path, config_path=tmp_path / "api.json")


def test_dashboard_rechecks_admitted_routes_and_projects_reserve_without_new_admission(tmp_path, monkeypatch):
    fixture = _Runtime(tmp_path)
    owner = tmp_path / "owner"
    owner.mkdir()
    monkeypatch.setattr("trade_graph.application.deployment_runtime._owner_bundle",
                        lambda path: (SimpleNamespace(assert_current=lambda: None), "synthetic", b"x" * 32))
    monkeypatch.setattr("trade_graph.kernel.deployment_image.read_owner_file", lambda *args: b"{}")
    observed = []
    state = {"ready": True, "available_subscription_routes": ["codex_subscription"], "blockers": [],
        "quota_reserve_policy": {"enabled": True, "admitted": True, "reserve_remaining_percent": 30,
            "execution_headroom_percent": 10, "admission_threshold_remaining_percent": 40},
        "quota_policy_routes": [], "quota_admission_blocked": False}
    class Adapter:
        def public_status(self, *, journal=None):
            if journal:
                assert journal.database.path == fixture.database.path
            observed.append(journal)
            return dict(state)
    admission = SubscriptionAdmission(None, Adapter(), {
        "ready": True, "selected_provider": "claude_subscription", "blockers": [], "quota": {}}, "1" * 64)
    monkeypatch.setattr("trade_graph.application.subscription_profile.load_subscription_profile",
                        lambda path: admission)
    monkeypatch.setattr("trade_graph.application.subscription_profile.subscription_profile_unchanged",
                        lambda *args: True)
    runtime = dashboard_runtime(fixture.database.path, protected_owner=owner)
    runtime.database.execute("INSERT INTO subscription_provider_state VALUES (?,?,?,?,?)",
        ("claude_subscription", 1, "subscription quota exhausted", "{}", "2026-10-07T00:00:00Z"))
    status = runtime.service_controller.status()
    assert status["prerequisites"]["ai_available"] is True
    assert status["ai_usage"]["quota_reserve_policy"]["reserve_remaining_percent"] == 30
    assert status["ai_usage"]["available_quota"] == "unknown"
    state["quota_policy_routes"] = [{"readings": [{"window": "primary", "remaining_percent": 39,
        "window_duration_mins": 10080}], "blockers": ["weekly reserve reached"]}]
    state.update(ready=False, available_subscription_routes=[], quota_admission_blocked=True,
        blockers=["weekly remaining allowance is within reserve and execution headroom"])
    state["quota_reserve_policy"] = {**state["quota_reserve_policy"], "admitted": False}
    status = runtime.service_controller.status()
    assert status["prerequisites"]["ai_available"] is False
    assert status["ai_usage"]["quota_admission_blocked"] is True
    assert status["ai_usage"]["available_quota"] == "provider_reported"
    assert "reserve" in status["prerequisites"]["reasons"][0]
    state["quota_policy_routes"][0]["provider_admission"] = {"occupied": True}
    assert any("admission is active" in reason
               for reason in runtime.service_controller.status()["prerequisites"]["reasons"])
    assert any(item is not None for item in observed)
    assert runtime.database.execute("SELECT COUNT(*) FROM subscription_attempts").fetchone()[0] == 0
    runtime.database.close()
