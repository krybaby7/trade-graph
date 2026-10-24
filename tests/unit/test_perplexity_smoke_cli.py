"""The credentialed Perplexity smoke call is opt-in and labelled by what really ran (T36, step L2).

These tests never reach Perplexity: the preview makes no call, and the confirmed run uses an
injected scripted transport, so its report says "synthetic" and must never count as a paid test.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from trade_graph.adapters.models.transport import HttpxProviderHttp, provider_payload
from trade_graph.cli import build_parser, main
from trade_graph.perplexity_smoke import run_smoke

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "perplexity"
FAKE = "pplx-synthetic-smoke-0123456789"


class _Refusing:
    def post_json(self, url, body, headers, *, timeout_seconds=None):  # pragma: no cover - must not run
        raise AssertionError("the preview must not call Perplexity")


class _Scripted:
    def __init__(self) -> None:
        self.calls = []
        final = provider_payload((FIXTURES / "agent_missing_usage.json").read_bytes())
        final["usage"] = {"input_tokens": 800, "output_tokens": 40,
                          "cost": {"currency": "USD", "total_cost": 0.002}}
        self.responses = [provider_payload((FIXTURES / "agent_tool_call.json").read_bytes()),
                          provider_payload((FIXTURES / "search_results.json").read_bytes()), final]

    def post_json(self, url, body, headers, *, timeout_seconds=None):
        self.calls.append((url, dict(headers)))
        return self.responses.pop(0)


def _key(tmp_path: Path) -> Path:
    folder = tmp_path / "private"
    folder.mkdir(mode=0o700)
    path = folder / "perplexity.key"
    path.write_text(FAKE + "\n")
    path.chmod(0o600)
    return path


def _args(tmp_path: Path, *extra: str):
    return build_parser().parse_args(["perplexity-smoke", "--key-file", str(_key(tmp_path)),
                                      "--database", str(tmp_path / "smoke" / "smoke.sqlite"), *extra])


def test_without_confirmation_it_only_previews(tmp_path, capsys) -> None:
    assert run_smoke(_args(tmp_path), transport=_Refusing()) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "not_run" and "--confirm-paid-call" in report["reason"]
    assert report["key_file"] == "readable, owner-only, outside Git"
    assert report["endpoints"] == ["https://api.perplexity.ai/v1/agent", "https://api.perplexity.ai/search"]
    assert not (tmp_path / "smoke").exists()
    assert FAKE not in json.dumps(report)


def test_a_bad_key_file_is_refused_before_anything_else(tmp_path, capsys) -> None:
    args = _args(tmp_path, "--confirm-paid-call")
    Path(args.key_file).chmod(0o644)
    assert run_smoke(args, transport=_Refusing()) == 2
    assert "owner-only" in capsys.readouterr().err


def test_the_installation_database_is_refused(tmp_path, capsys) -> None:
    from trade_graph.adapters.persistence.db import Database

    installation = tmp_path / "runtime" / "trade_graph.sqlite"
    Database(installation).execute("INSERT INTO portfolios VALUES ('p','paper','EUR','e','active',NULL,'t')")
    args = _args(tmp_path, "--confirm-paid-call")
    args.database = str(installation)
    assert run_smoke(args, transport=_Refusing()) == 2
    assert "separate smoke database" in capsys.readouterr().err


def test_a_scripted_run_is_labelled_synthetic_with_receipts(tmp_path, capsys) -> None:
    transport = _Scripted()
    report_path = tmp_path / "smoke-report.json"
    assert run_smoke(_args(tmp_path, "--confirm-paid-call", "--report", str(report_path)),
                     transport=transport) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == json.loads(report_path.read_text())
    assert report["label"] == "synthetic" and report["real_paid_call"] is False
    assert report["outcome"]["status"] == "completed"
    assert [(item["price_card_id"], item["status"]) for item in report["receipts"]] == [
        ("perplexity-agent-claude-sonnet-5-5-2026-10-12", "committed"),
        ("perplexity-search-api-2026-10-16", "committed"),
        ("perplexity-agent-claude-sonnet-5-5-2026-10-12", "committed")]
    assert report["receipts"][1]["native_cost_usd"] == "0.005"
    assert [step["reported_total_cost_usd"] for step in report["steps"]] == ["0.0028", "0.002"]
    assert report["steps"][1]["receipt_native_cost_usd"] == "0.002"
    assert {headers["Authorization"] for _, headers in transport.calls} == {f"Bearer {FAKE}"}
    assert FAKE not in json.dumps(report)
    database = tmp_path / "smoke" / "smoke.sqlite"
    assert database.stat().st_mode & 0o077 == 0
    assert FAKE not in "\n".join(sqlite3.connect(database).iterdump())


def test_the_command_is_registered_and_never_on_by_default(tmp_path, capsys) -> None:
    args = build_parser().parse_args(["perplexity-smoke", "--key-file", "/k", "--database", "/d"])
    assert args.confirm_paid_call is False and args.limit_usd == "0.25"
    assert main(["perplexity-smoke", "--key-file", str(_key(tmp_path)), "--database",
                 str(tmp_path / "s.sqlite")]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "not_run"


def test_the_real_transport_is_pinned_to_perplexity() -> None:
    from trade_graph import perplexity_smoke

    transport = perplexity_smoke.real_transport()
    assert type(transport) is HttpxProviderHttp
    assert transport.allowed_urls == ("https://api.perplexity.ai/v1/agent", "https://api.perplexity.ai/search")
    with pytest.raises(ValueError, match="outside the owner-pinned"):
        transport.post_json("https://example.org/", {}, {})
