"""Owner optimisation preflight refreshes metadata; browser reads reuse it."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
from tests.integration.test_api_security_live import _Runtime

from trade_graph.adapters.models.subscription import (
    SubscriptionAdapter,
    SubscriptionConfig,
    SubscriptionJournal,
    SubscriptionReadiness,
)
from trade_graph.application.authority import seed_paper_authority
from trade_graph.application.service_controller import service_started
from trade_graph.application.subscription_profile import SubscriptionAdmission
from trade_graph.dashboard import dashboard_runtime
from trade_graph.domain.clock import SystemClock
from trade_graph.domain.errors import AuthorityDenied


def _reading(clock, remaining=75):
    return SubscriptionReadiness("codex_subscription", "0.160.1", True, (), {
        "ordinary_usage_allowed": True, "credits_balance": "0", "source": "codex-app-server",
        "observed_at": clock.now().isoformat(), "windows": {
            "primary": {"remaining_percent": remaining, "window_duration_mins": 10080}, "secondary": None},
        "unavailable_windows": ["secondary"]}, "chatgpt", "linux-bubblewrap")


@pytest.mark.parametrize("case", ["stale-cache", "fresh-reserve", "occupied-journal"])
def test_optimisation_command_uses_fresh_metadata_and_occupied_journal(tmp_path, monkeypatch, case):
    fixture = _Runtime(tmp_path)
    owner = tmp_path / "owner"
    owner.mkdir()
    monkeypatch.setattr("trade_graph.application.deployment_runtime._owner_bundle",
                        lambda path: (SimpleNamespace(assert_current=lambda: None), "synthetic", b"x" * 32))
    monkeypatch.setattr("trade_graph.kernel.deployment_image.read_owner_file", lambda *args: b"{}")
    clock, probes, observations = SystemClock(), [], []
    remaining = [75]
    def probe():
        probes.append(True)
        return _reading(clock, remaining[0])
    adapter = SubscriptionAdapter(SubscriptionConfig(model="gpt-6.1-sol", enabled=True, quota_policy_enabled=True),
        _reading(clock), SimpleNamespace(execute=lambda *_args, **_kwargs: pytest.fail("no inference")),
        readiness_probe=probe)
    public_status = adapter.public_status
    def observed_status(*, journal=None, refresh=True):
        observations.append((journal, refresh))
        return public_status(journal=journal, refresh=refresh)
    monkeypatch.setattr(adapter, "public_status", observed_status)
    admission = SubscriptionAdmission(None, adapter, {
        "ready": True, "selected_provider": "codex_subscription", "blockers": [], "quota": {}}, "1" * 64)
    monkeypatch.setattr("trade_graph.application.subscription_profile.load_subscription_profile",
                        lambda path: admission)
    monkeypatch.setattr("trade_graph.application.subscription_profile.subscription_profile_unchanged",
                        lambda *args: True)
    runtime = dashboard_runtime(fixture.database.path, protected_owner=owner)
    try:
        seed_paper_authority(runtime.database, runtime.clock, runtime.portfolio_id)
        service_started(runtime.database, runtime.clock, runtime.portfolio_id, "paper", None, ai_available=True)
        if case == "stale-cache":
            adapter.readiness = replace(adapter.readiness, quota={**adapter.readiness.quota,
                "observed_at": (clock.now() - timedelta(seconds=60)).isoformat()})
        elif case == "fresh-reserve":
            remaining[0] = 39
        else:
            journal = SubscriptionJournal(runtime.database, runtime.clock)
            assert journal.acquire_provider_admission("codex_subscription", "other-inflight", maximum_seconds=600)
        adapter._last_readiness = float("-inf")
        probes.clear()
        observations.clear()
        control = runtime.service_controller
        expected_cached = case != "stale-cache"
        assert control.status()["prerequisites"]["ai_available"] is expected_cached
        assert observations and all(journal is None and not refresh for journal, refresh in observations)
        assert not probes
        observations.clear()
        if case == "stale-cache":
            result = control.start_optimisation("fresh-command")
            assert result["optimisation"]["status"] == "QUEUED"
            task = runtime.database.execute("SELECT * FROM tasks WHERE task_id=?", (result["task_id"],)).fetchone()
            assert task["objective"] == "owner-optimisation-cycle" and task["status"] == "QUEUED"
            calls = len(probes)
            assert control.start_optimisation("fresh-command")["replayed"]
            assert len(probes) == calls
        else:
            with pytest.raises(AuthorityDenied, match="isolated subscription"):
                control.start_optimisation("fresh-command")
            assert runtime.database.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
            assert runtime.database.execute("SELECT count(*) FROM service_control_requests").fetchone()[0] == 0
        assert len(probes) == 1
        assert len(observations) == 1 and observations[0][0] is not None and observations[0][1] is True
        assert observations[0][0].database.path == runtime.database.path
        assert runtime.database.execute("SELECT count(*) FROM subscription_invocations").fetchone()[0] == 0
        assert runtime.database.execute("SELECT count(*) FROM subscription_attempts").fetchone()[0] == 0
    finally:
        runtime.database.close()
        fixture.database.close()
