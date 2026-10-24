import json
from decimal import Decimal

from trade_graph import (
    CAPITAL,
    CAPITAL_CURRENCY,
    LIVE_ENABLED,
    MODE,
    PAID_CALLS_ENABLED,
    REPORTING_CURRENCY,
)
from trade_graph.cli import main


def test_package_defaults_to_paper_without_credentials() -> None:
    assert MODE == "paper"
    assert PAID_CALLS_ENABLED is False
    assert LIVE_ENABLED is False
    assert CAPITAL == Decimal("10000")
    assert CAPITAL_CURRENCY == "USD"
    assert REPORTING_CURRENCY == "EUR"


def test_cli_doctor_does_not_require_secrets(capsys, tmp_path) -> None:
    path = tmp_path / "not-initialized.sqlite"
    assert main(["doctor", "--database", str(path)]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "error"
    assert out["paid_calls_enabled"] is False
    assert out["live_enabled"] is False
    assert out["credentialed_providers"]["status"] == "pending"
    assert not path.exists()
