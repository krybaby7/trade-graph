"""Root-distributed profile admission uses metadata only and fails closed."""

import hashlib
import json

from trade_graph.application.subscription_profile import load_subscription_profile
from trade_graph.kernel.runtime_manifest import protected_package_sha256


def profile_files(tmp_path, provider="claude_subscription"):
    model = "gpt-6.1-sol" if provider == "codex_subscription" else "claude-sonnet-5-5"
    evidence = {"schema_version": 1, "kind": "actual-linux-boundary",
                "native_cli_sha256": "a" * 64, "protected_package_sha256": protected_package_sha256(),
                "checks": ["private_files_denied", "windows_mounts_denied", "host_proc_denied",
                           "api_environment_denied", "descendants_killed", "tools_disabled"]}
    raw = json.dumps(evidence).encode()
    (tmp_path / "subscription-isolation.json").write_bytes(raw)
    profile = {"schema_version": 1, "runtime": {"subscription": {"provider": provider, "model": model,
                       "enabled": True}}, "native_binary": "/usr/local/bin/claude", "native_sha256": "a" * 64,
               "official_login_file": "/native-login/.credentials.json", "extra_usage_disabled": True,
               "isolation_evidence_sha256": hashlib.sha256(raw).hexdigest()}
    (tmp_path / "subscription-profile.json").write_text(json.dumps(profile))
    return profile


def trusted_files(monkeypatch, tmp_path):
    import trade_graph.application.subscription_profile as module
    monkeypatch.setattr(module, "read_owner_file", lambda directory, name, limit: (directory / name).read_bytes())
    monkeypatch.setattr(module, "assert_boot_environment", lambda: None)
    monkeypatch.setattr(module.NativeCliPin, "verify", lambda pin: pin.binary)
    monkeypatch.setattr(module.LinuxSubscriptionExecutor, "__init__", lambda instance, pin, login: None)
    metadata_calls = []

    def metadata(config, pin, login, **kwargs):
        metadata_calls.append((config.provider, pin.sha256, str(login)))
        return {"provider": config.provider, "cli_version": "2.1.285", "ready": True, "blockers": [],
                "quota": {}, "authentication": "subscription", "isolation": "linux-bubblewrap"}

    monkeypatch.setattr(module, "probe_isolated_claude", metadata)
    return metadata_calls


def test_unprotected_profile_is_not_admitted(tmp_path):
    profile_files(tmp_path)
    admission = load_subscription_profile(tmp_path)
    assert not admission.status["ready"] and admission.adapter is None
    assert "protected" in " ".join(admission.status["blockers"])


def test_missing_profile_is_management_only_without_inference(tmp_path):
    admission = load_subscription_profile(tmp_path)
    assert not admission.status["ready"] and admission.adapter is None
    assert admission.status["inference_attempts"] == 0


def test_native_profile_binds_exact_binary_login_and_immutable_roles(tmp_path, monkeypatch):
    profile_files(tmp_path)
    calls = trusted_files(monkeypatch, tmp_path)
    admission = load_subscription_profile(tmp_path)
    assert admission.status["ready"] and admission.adapter is not None
    assert admission.config.subscription.provider == "claude_subscription"
    assert calls == [("claude_subscription", "a" * 64, "/native-login/.credentials.json")]
    assert admission.status["inference_attempts"] == 0
    assert "native-login" not in json.dumps(admission.status)


def test_codex_profile_never_uses_claude_or_invokes_native_inference(tmp_path, monkeypatch):
    profile_files(tmp_path, "codex_subscription")
    calls = trusted_files(monkeypatch, tmp_path)
    admission = load_subscription_profile(tmp_path)
    assert not admission.status["ready"] and admission.adapter is None and not calls
    assert admission.status["selected_provider"] == "codex_subscription"
    assert "retries" in " ".join(admission.status["blockers"])


def test_changed_isolation_proof_is_not_replaced_by_boolean(tmp_path, monkeypatch):
    profile_files(tmp_path)
    calls = trusted_files(monkeypatch, tmp_path)
    (tmp_path / "subscription-isolation.json").write_text('{}')
    admission = load_subscription_profile(tmp_path)
    assert not admission.status["ready"] and admission.adapter is None and not calls


def test_refused_protected_boot_remains_management_only(tmp_path, monkeypatch):
    import trade_graph.application.subscription_profile as module
    from trade_graph.domain.errors import AuthorityDenied
    profile_files(tmp_path)
    calls = trusted_files(monkeypatch, tmp_path)

    def denied():
        raise AuthorityDenied("synthetic image host proof refused")

    monkeypatch.setattr(module, "assert_boot_environment", denied)
    admission = load_subscription_profile(tmp_path)
    assert not admission.status["ready"] and admission.adapter is None and not calls


def test_extra_usage_disable_is_a_strict_owner_boolean(tmp_path, monkeypatch):
    profile = profile_files(tmp_path)
    calls = trusted_files(monkeypatch, tmp_path)
    profile["extra_usage_disabled"] = "true"
    (tmp_path / "subscription-profile.json").write_text(json.dumps(profile))
    admission = load_subscription_profile(tmp_path)
    assert not admission.status["ready"] and admission.adapter is None and not calls
