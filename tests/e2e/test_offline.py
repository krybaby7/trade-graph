from decimal import Decimal

from trade_graph.demo import run_offline


def test_offline_loop(tmp_path) -> None:
    report = run_offline(tmp_path / "work")
    assert report["finding_did_not_create_order"] is True
    assert report["failure_is_not_hold"] is True
    assert report["valid_loss_not_bad_grade"] is True
    assert report["invalid_win_not_good_grade"] is True
    assert report["activated_hash"] != report["baseline_hash"]
    assert report["new_decision_version"] == report["activated_hash"]
    assert report["restart_decision_version"] == report["activated_hash"]
    assert report["restart_context_cap"] == 5
    assert report["observation_state_before_fault"] == "ACTIVE"
    assert report["rollback_decision_version"] == report["baseline_hash"]
    assert report["rollback_context_cap"] == 8
    assert report["rollback_preserved_receipts"] is True
    assert report["worker_completed"] == 2
    assert report["leader_activation_decision_id"] != report["leader_decision_id"]
    assert report["engineer_worker_completed"] == 2
    assert len(report["engineer_usage_reservations"]) == 2
    assert report["engineer_candidate_id"]
    assert report["context_cap"] == 5
    assert report["always_include"] == ["mandate_obligations", "active_safety"]
    assert report["rejected_change"] is True
    assert report["rejected_receipt"]
    assert report["budget_exhausted"] is True
    assert report["fill_count_after_restart"] == report["fill_count"] == 4
    assert report["restart_submit_count"] == 0
    assert report["duplicate_fill_rejected"] is True
    assert Decimal(report["owned_btc_after_restart"]) == Decimal("0.01")
    assert Decimal(report["a43_at_090"]) == Decimal("9000")
    assert Decimal(report["a43_at_091"]) == Decimal("9100")
    assert Decimal(report["a43_alpha"]) == Decimal("0")
    assert report["a44_remaining_unchanged"] is True
    assert report["a44_synthetic_ignored"] is True
    assert Decimal(report["shared_allocated"]) == Decimal(report["shared_receipt"])
    assert report["leader_trade_approvals"] == 0
    assert report["evaluation"]["verdict"] == "insufficient_evidence"
