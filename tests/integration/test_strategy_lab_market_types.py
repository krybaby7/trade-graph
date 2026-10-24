"""Wave 11 integration: W11-A's market types inside W11-B's strategy lab (D289, D290-D292).

The lab's replays use the current market-type rules like ``PracticeRange.run``, so every lab report carries the split
of its trades by market type at entry and the desk's ``lab`` context reads it out; and a version that requires a
market type at entry can be tuned and confirmed in the lab. The synthetic series has 15-minute candles only, too few
hourly candles for a label, so every decision is ``unclear``: a report, never an error. Synthetic candles
(``synthetic_fixture``) and the synthetic day-plan account; no network, model, key, paid call or order.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from tests.day_plan_support import day_plan_account
from tests.practice_range_support import cross_strategy
from tests.strategy_lab_support import candle_file, confirm, register, statuses, study, submit, tune, worker_for

from trade_graph.application.strategy_lab.context import lab_context
from trade_graph.domain.market_types import MARKET_TYPES
from trade_graph.kernel.market_type import CURRENT_MARKET_TYPE_RULES

DONE = [("register_strategy", "done"), ("create_study", "done"), ("tune", "done"), ("confirm", "done")]


@pytest.fixture
def lab(tmp_path):
    account = day_plan_account(tmp_path)
    market, candles = candle_file(tmp_path / "market_data.sqlite")
    yield account, candles
    market.close()


def _reports(account) -> list[dict]:
    context = lab_context(account.database, account.clock, account.portfolio)
    return [report for result in context["results"] for report in result.get("reports", ())]


def test_the_lab_reports_split_by_market_type_and_the_desk_reads_it(lab) -> None:
    account, candles = lab
    submit(account, [register(), study(), tune(), confirm()])
    worker_for(account, candles).run_until_idle()
    assert statuses(account) == DONE
    reports = _reports(account)
    assert len(reports) >= 2 and all("missing" not in report for report in reports)
    for report in reports:
        overview = report["overview"]
        assert overview["market_type_rules"] == CURRENT_MARKET_TYPE_RULES.ref
        rows = overview["by_market_type"]
        assert [row["market_type"] for row in rows] == list(MARKET_TYPES)
        assert sum(row["trades"] for row in rows) == overview["trades"]
        assert sum((Decimal(row["result_after_fees"]) for row in rows), Decimal("0")) == Decimal(overview["result"])
        # 15-minute candles only: no hourly label can be made, so every trade is in "unclear" and the report says so
        assert all(row["trades"] == 0 for row in rows if row["market_type"] != "unclear")
        assert any(line.startswith("By market type at entry") for line in report["lines"])
        assert any("so every market type is unclear" in line for line in report["lines"])


def test_a_version_that_requires_a_market_type_runs_in_the_lab(lab) -> None:
    account, candles = lab
    entry = cross_strategy().canonical_document()["entry"]
    entry["all_of"] = [*entry["all_of"], {"market_type": "falling", "relation": "is_not"}]
    submit(account, [register(entry=entry), study(), tune(), confirm()])
    worker_for(account, candles).run_until_idle()
    assert statuses(account) == DONE
    reports = _reports(account)
    assert reports and all(report["overview"]["market_type_rules"] == CURRENT_MARKET_TYPE_RULES.ref
                           for report in reports)
