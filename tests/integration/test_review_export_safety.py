"""Complete safe record exports omit prompt bytes and tolerate unknown quota windows."""

import json

from tests.integration.test_dashboard import _client


def test_snapshot_export_selects_retained_business_inputs_without_prompts(tmp_path):
    client, database, portfolio, token, *_ = _client(tmp_path)
    payload = {
        "artifact": {"version_id": "version-1", "artifact_hash": "hash-1"},
        "source_instruction": "PRIVATE_PROMPT_SENTINEL",
        "strategy_templates": {"trend": {"strategy_id": "trend", "hypothesis": "Trend persists",
                                         "features": ["four_hour_drift"], "prompt": "PRIVATE_TEMPLATE_PROMPT"}},
        "active_strategy_templates": {"trend": {"strategy_id": "trend", "hypothesis": "Trend persists",
                                                "features": ["four_hour_drift"]}},
        "market": {"BTC/USD": {"fresh": True, "observation": {"bid": "99", "ask": "101"}}},
        "portfolio": {"cash": {"USD": "10000"}, "inventory": {}},
        "selected_context": {"lessons": [], "always_include": ["active_safety"]},
    }
    database.execute("INSERT INTO snapshots (snapshot_id,portfolio_id,as_of,payload_json,created_at) "
                     "VALUES (?,?,?,?,?)",
                     ("snapshot-audit", portfolio, "2026-01-01T00:00:00Z", json.dumps(payload),
                      "2026-01-01T00:00:00Z"))
    response = client.get("/api/v1/review-export", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    snapshot = response.json()["records"]["snapshots"]["records"][0]
    assert snapshot["payload"]["market"]["BTC/USD"]["observation"]["bid"] == "99"
    assert snapshot["payload"]["portfolio"]["cash"]["USD"] == "10000"
    assert snapshot["payload"]["artifact"]["artifact_hash"] == "hash-1"
    assert snapshot["payload"]["strategy_templates"]["trend"]["hypothesis"] == "Trend persists"
    assert "PRIVATE" not in response.text
    assert "source_instruction" not in snapshot["payload"]


def test_work_report_exports_keep_safe_completion_and_summary_without_unknown_prompt_fields(tmp_path):
    client, database, portfolio, token, *_ = _client(tmp_path)
    database.execute("INSERT INTO role_results VALUES (?,?,?,?,?,?)", (
        "failed-task", portfolio, "research", "FAILED", json.dumps({"reason": "Source contract failed",
        "report_id": "report-audit", "instructions": "PRIVATE_RESULT_INSTRUCTIONS",
        "result": {"raw_redacted": "PRIVATE_RAW_RESULT"}}), "2026-01-01T00:00:00Z"))
    report = {"report_id": "report-audit", "role": "research", "kind": "finding", "summary": "Source summary",
              "evidence_refs": ["finding-1"], "source_instruction": "PRIVATE_REPORT_PROMPT"}
    database.execute("INSERT INTO secretary_reports "
                     "(report_id,portfolio_id,role,kind,document_json,material,created_at) "
                     "VALUES (?,?,?,?,?,?,?)", ("report-audit", portfolio, "research", "finding", json.dumps(report),
                                              0, "2026-01-01T00:00:00Z"))
    database.execute("INSERT INTO secretary_digests VALUES (?,?,?,?,?,?)", (
        "digest-audit", portfolio, 0, 1, json.dumps({"digest_id": "digest-audit", "portfolio_id": portfolio,
        "groups": {"research:finding": ["report-audit"]}, "reports": [report], "evidence_refs": ["report-audit"],
        "messages": ["PRIVATE_DIGEST_CONVERSATION"]}), "2026-01-01T00:00:00Z"))
    response = client.get("/api/v1/review-export", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    records = response.json()["records"]
    assert records["role_results"]["records"][0]["document"]["reason"] == "Source contract failed"
    assert records["secretary_reports"]["records"][0]["document"]["summary"] == "Source summary"
    assert records["secretary_digests"]["records"][0]["document"]["reports"][0]["summary"] == "Source summary"
    assert "PRIVATE" not in response.text


def test_null_retained_quota_window_renders_as_unavailable(tmp_path):
    client, database, _, token, *_ = _client(tmp_path)
    quota = {"observed_at": "2025-12-31T23:59:00+00:00", "windows": {"primary": None},
             "unavailable_windows": ["primary"]}
    database.execute("INSERT INTO subscription_provider_state (provider,quota_json,updated_at) VALUES (?,?,?)",
                     ("codex_subscription", json.dumps(quota), "2025-12-31T23:59:00Z"))
    response = client.get("/", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert "primary: unavailable" in response.text.lower()
    assert "token usage cover all retained lifetime records through the current snapshot" in response.text
