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


def test_cli_doctor_does_not_require_secrets(capsys) -> None:
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "paid calls disabled" in out
    assert "live trading disabled" in out
    assert "credentials=not-required" in out
    assert "credentialed_providers=pending" in out
