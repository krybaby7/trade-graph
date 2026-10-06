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
    admission = SubscriptionAdmission(None, object(), {
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
