"""Root-distributed profile admission uses metadata only and fails closed."""

import hashlib
import json
from types import SimpleNamespace

from trade_graph.application.subscription_profile import load_subscription_profile
from trade_graph.kernel.runtime_manifest import protected_package_sha256


def profile_files(tmp_path, provider="claude_subscription"):
    model = "gpt-6.1-sol" if provider == "codex_subscription" else "claude-sonnet-5-5"
    evidence = {"schema_version": 1, "kind": "actual-linux-boundary",
                "native_cli_sha256": "a" * 64, "protected_package_sha256": protected_package_sha256(),
                "checks": ["private_files_denied", "windows_mounts_denied", "host_proc_denied",
                           "api_environment_denied", "descendants_killed", "tools_disabled",
                           "direct_egress_denied", "provider_route_restricted", "market_route_restricted"]}
    raw = json.dumps(evidence).encode()
    (tmp_path / "subscription-isolation.json").write_bytes(raw)
    profile = {"schema_version": 1, "runtime": {"subscription": {"provider": provider, "model": model,
                       "enabled": True}}, "native_binary": "/usr/local/bin/claude", "native_sha256": "a" * 64,
               "official_login_file": ("/native-login/auth.json" if provider == "codex_subscription"
                                       else "/native-login/.credentials.json"), "extra_usage_disabled": True,
               "isolation_evidence_sha256": hashlib.sha256(raw).hexdigest()}
    if provider == "codex_subscription":
        profile["model_catalog_file"] = "codex-model-catalog.json"
        (tmp_path / "codex-model-catalog.json").write_text(json.dumps({"models":[{"slug":model,
            "shell_type":"disabled","apply_patch_tool_type":None,"experimental_supported_tools":[],
            "tool_mode":"direct","node_repl_disabled":True,"supports_search_tool":False,
            "multi_agent_version":None,"multi_agent_reasoning_effort":None}]}))
    (tmp_path / "subscription-profile.json").write_text(json.dumps(profile))
    return profile


def trusted_files(monkeypatch, tmp_path):
    import trade_graph.application.subscription_profile as module
    monkeypatch.setattr(module, "read_owner_file", lambda directory, name, limit: (directory / name).read_bytes())
    monkeypatch.setattr(module, "assert_boot_environment", lambda: SimpleNamespace(
        sha256='m' * 64, deployment_id='deployment', protected_package_sha256=protected_package_sha256()))
    monkeypatch.setattr(module.NativeCliPin, "verify", lambda pin: pin.binary)
    monkeypatch.setattr(module.LinuxSubscriptionExecutor, "__init__", lambda instance, pin, login, **kwargs: None)
    monkeypatch.setattr(module, "verify_subscription_egress", lambda *args: SimpleNamespace(
        proxy_url='http://172.30.0.2:8080'))
    metadata_calls = []

    def metadata(config, pin, login, **kwargs):
        metadata_calls.append((config.provider, pin.sha256, str(login)))
        return {"provider": config.provider,
                "cli_version": "0.160.1" if config.provider == "codex_subscription" else "2.1.285",
                "ready": True, "blockers": [],
                "quota": {"ordinary_usage_allowed":True,"credits_balance":"0","remaining_percent":75}
                    if config.provider == "codex_subscription" else {},
                "authentication": "chatgpt" if config.provider == "codex_subscription" else "subscription",
                "isolation": "linux-bubblewrap"}

    monkeypatch.setattr(module, "probe_isolated_claude", metadata)
    monkeypatch.setattr(module, "probe_isolated_codex", metadata, raising=False)
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


def test_codex_profile_uses_official_chatgpt_auth_mount_without_inference(tmp_path, monkeypatch):
    profile_files(tmp_path, "codex_subscription")
    calls = trusted_files(monkeypatch, tmp_path)
    admission = load_subscription_profile(tmp_path)
    assert admission.status["ready"] and admission.adapter is not None
    assert calls == [("codex_subscription", "a" * 64, "/native-login/auth.json")]
    assert admission.status["selected_provider"] == "codex_subscription"


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


def test_missing_egress_admission_blocks_before_native_auth_or_inference(tmp_path, monkeypatch):
    import trade_graph.application.subscription_profile as module
    profile_files(tmp_path)
    calls = trusted_files(monkeypatch, tmp_path)
    def denied(*args):
        raise PermissionError('protected egress unavailable')
    monkeypatch.setattr(module, 'verify_subscription_egress', denied)
    admission = load_subscription_profile(tmp_path)
    assert not admission.status['ready'] and admission.adapter is None and not calls


def test_only_independently_ready_fallback_profile_is_enabled_and_hash_binds_it(tmp_path, monkeypatch):
    from trade_graph.application.subscription_profile import subscription_profile_unchanged
    profile=profile_files(tmp_path)
    fallback=tmp_path / "fallback-codex"
    fallback.mkdir()
    profile_files(fallback,"codex_subscription")
    profile["fallback_profiles"]=["fallback-codex"]
    (tmp_path/"subscription-profile.json").write_text(json.dumps(profile))
    trusted_files(monkeypatch,tmp_path)
    admission=load_subscription_profile(tmp_path)
    assert admission.status["ready"] and len(admission.adapter.fallbacks)==1
    assert admission.status["application_automatic_fallback"]
    assert subscription_profile_unchanged(tmp_path,admission.profile_sha256)
    (fallback/"subscription-profile.json").write_text('{}')
    assert not subscription_profile_unchanged(tmp_path,admission.profile_sha256)


def test_unready_fallback_is_reported_without_blocking_ready_primary(tmp_path, monkeypatch):
    profile=profile_files(tmp_path)
    profile["fallback_profiles"]=["missing-route"]
    (tmp_path/"subscription-profile.json").write_text(json.dumps(profile))
    trusted_files(monkeypatch,tmp_path)
    admission=load_subscription_profile(tmp_path)
    assert admission.status["ready"] and not admission.adapter.fallbacks
    assert not admission.status["fallback_routes"][0]["ready"]


def test_codex_quota_owner_evidence_is_sanitized(tmp_path, monkeypatch):
    profile=profile_files(tmp_path,"codex_subscription")
    profile["quota_evidence_file"]="subscription-quota.json"
    (tmp_path/"subscription-profile.json").write_text(json.dumps(profile))
    (tmp_path/"subscription-quota.json").write_text(json.dumps({"remaining_percent":76,
        "ordinary_usage_allowed":True,"credits_balance":"0","private_account":"excluded"}))
    trusted_files(monkeypatch,tmp_path)
    import trade_graph.application.subscription_profile as module
    monkeypatch.setattr(module,"probe_isolated_codex",lambda *args,**kwargs:{
        "cli_version":"0.160.1","authentication":"chatgpt","quota":{}})
    admission=load_subscription_profile(tmp_path)
    assert admission.status["ready"] and admission.status["quota"]["remaining_percent"]==76
    assert "private_account" not in json.dumps(admission.status)


def test_codex_catalog_must_remove_all_filesystem_tools_before_admission(tmp_path,monkeypatch):
    profile_files(tmp_path,"codex_subscription")
    calls=trusted_files(monkeypatch,tmp_path)
    (tmp_path / "codex-model-catalog.json").write_text(json.dumps({"models":[{"slug":"gpt-6.1-sol",
        "shell_type":"disabled","apply_patch_tool_type":"freeform","experimental_supported_tools":[]}]}))
    admission=load_subscription_profile(tmp_path)
    assert not admission.status["ready"] and not admission.adapter
    assert not calls


def test_temporarily_blocked_primary_preserves_verified_fallback_and_metadata_refresh(tmp_path, monkeypatch):
    import trade_graph.application.subscription_profile as module
    profile=profile_files(tmp_path)
    route=tmp_path / "fallback-codex"
    route.mkdir()
    profile_files(route,"codex_subscription")
    profile["fallback_profiles"]=["fallback-codex"]
    (tmp_path / "subscription-profile.json").write_text(json.dumps(profile))
    trusted_files(monkeypatch,tmp_path)
    monkeypatch.setattr(module,"probe_isolated_claude",lambda *args,**kwargs:{
        "cli_version":"2.1.292","authentication":"none","quota":{}})
    admission=load_subscription_profile(tmp_path)
    assert admission.status["ready"] and admission.adapter is not None
    assert not admission.adapter.readiness.ready and len(admission.adapter.fallbacks)==1
    assert admission.adapter.public_status(refresh=False)["available_subscription_routes"]==["codex_subscription"]


def test_valid_boundary_with_temporarily_missing_login_status_can_refresh_without_reassembly(tmp_path, monkeypatch):
    import trade_graph.application.subscription_profile as module
    profile_files(tmp_path)
    trusted_files(monkeypatch,tmp_path)
    monkeypatch.setattr(module,"probe_isolated_claude",lambda *args,**kwargs:{
        "cli_version":"2.1.292","authentication":"none","quota":{}})
    admission=load_subscription_profile(tmp_path)
    assert not admission.status["ready"] and admission.adapter is not None
    assert callable(admission.adapter.readiness_probe)
