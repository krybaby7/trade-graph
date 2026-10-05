"""The owner CLI has no credential arguments or synthetic success option."""

import json
import sys
import types
from pathlib import Path

import pytest

from trade_graph.cli import build_parser, main


@pytest.fixture(autouse=True)
def pending_contract(monkeypatch):
    try:
        from trade_graph.api import account_checks
    except ImportError:
        account_checks = types.ModuleType("trade_graph.api.account_checks")
        account_checks.PENDING_LABELS = {
            "owner_eligibility_unverified": "Owner eligibility remains unverified.",
            "native_account_owner_identity_unverified": "Native account ownership remains unverified.",
            "key_permission_inventory_unverified": "Full API key permissions remain unverified.",
            "withdrawals_absent_unverified": "Absence of withdrawal permission remains unverified.",
            "write_cancel_uncertainty_conformance_unverified":
                "Order and cancellation uncertainty checks remain unverified.",
            "native_stop_protection_unverified": "Native stop protection remains unverified.",
            "protected_account_ledger_reconciliation_unverified":
                "Financial account reconciliation remains unverified.",
            "intended_host_dependency_identity_unverified":
                "Intended host and dependency identity remains unverified.",
            "native_partial_fill_reserve_bound_unverified": "Native partial fill reserve bounds remain unverified.",
            "order_lookups_not_requested": "Order lookups were not requested.",
            "additional_checks_pending": "Additional observation checks remain pending.",
        }
        monkeypatch.setitem(sys.modules, "trade_graph.api.account_checks", account_checks)
    return account_checks.PENDING_LABELS


def test_owner_command_parser_has_only_local_path_and_symbol_options():
    args = build_parser().parse_args([
        "kraken-read-only", "--database", "runtime/trade_graph.sqlite",
        "--owner-directory", "~/.local/share/trade-graph-owner/kraken", "--symbol", "BTC/USD",
    ])
    assert args.command == "kraken-read-only"
    assert args.symbol == "BTC/USD"
    assert not hasattr(args, "api_key") and not hasattr(args, "api_secret")
    assert not hasattr(args, "client") and not hasattr(args, "synthetic")


def test_command_calls_only_owner_helper_and_redacts_importer_output(monkeypatch, capsys, pending_contract):
    calls = []

    def run(database, owner_directory, symbol):
        calls.append((database, owner_directory, symbol))
        return {"run_id": "a" * 32, "status": "incomplete", "account_observation": {"symbol": "BTC/USD",
                "verified_completed_stages": ["instruments"],
                "observation_started_at": "2026-10-05T12:00:00Z",
                "observation_finished_at": "2026-10-05T12:00:01Z",
                "transport_basis": "owned_https", "freshness": "fresh",
                "pending_checks": list(pending_contract.values()) + ["synthetic-secret-value"]},
                "started_at": "2026-10-05T12:00:00Z", "finished_at": "2026-10-05T12:00:01Z",
                "transport_basis": "owned_https", "freshness": "fresh",
                "pending": ["protected_account_ledger_reconciliation_unverified"],
                "scope": {"account_id": "synthetic-secret-account"}, "amounts": {"USD": "125"},
                "observation_sha256": "b" * 64, "private_path": "/synthetic/private/path"}

    monkeypatch.setattr("trade_graph.application.kraken_onboarding.run_kraken_read_only", run)
    assert main(["kraken-read-only", "--database", "runtime/trade_graph.sqlite",
                 "--owner-directory", "~/.local/share/trade-graph-owner/kraken", "--symbol", "BTC/USD"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["symbol"] == "BTC/USD"
    assert result["full_reconciliation"] == "pending"
    assert len(result["pending"]) >= 9
    assert result["pending"] == list(pending_contract.values())
    assert "synthetic-secret-value" not in json.dumps(result)
    assert set(result) <= {"run_id", "status", "symbol", "stages", "started_at", "finished_at", "transport_basis",
                           "freshness", "pending", "full_reconciliation"}
    assert calls == [(Path("runtime/trade_graph.sqlite"), Path("~/.local/share/trade-graph-owner/kraken"), "BTC/USD")]


@pytest.mark.parametrize("error", [PermissionError, ValueError, RuntimeError, OSError, KeyboardInterrupt])
def test_owner_failure_outputs_fixed_message_without_exception_details(error, monkeypatch, capsys):
    def run(*args, **kwargs):
        raise error("synthetic credentials and raw provider errors must stay private")

    monkeypatch.setattr("trade_graph.application.kraken_onboarding.run_kraken_read_only", run)
    assert main(["kraken-read-only"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "synthetic credentials" not in output.err and "Traceback" not in output.err
    assert "Read-only observation" in output.err


@pytest.mark.parametrize("option", ["--api-key", "--api-secret", "--synthetic-success"])
def test_forbidden_arguments_refused_without_echoing_supplied_value(option, capsys):
    assert main(["kraken-read-only", option, "synthetic-secret-value"]) == 2
    output = capsys.readouterr()
    assert "synthetic-secret-value" not in output.out + output.err
    assert "unrecognized owner command options" in output.err
