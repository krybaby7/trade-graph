"""What the deployed Engineer may not edit (W9-A, D259): the news check's installed route (owner files, the Jev key,
the route's money rule and protected budget origins), Jev's receipt collection and the Jev adapter that carries the key
to its one endpoint."""

from __future__ import annotations

import pytest

from trade_graph.kernel.authority import path_is_protected


@pytest.mark.parametrize("path", [
    "src/trade_graph/application/news_installation.py",  # key, route, money rule, origins, the in-service poll
    "src/trade_graph/application/jev_checks.py",  # reservation before the call, receipt or unresolved hold after it
    "src/trade_graph/adapters/decisions/jev.py",  # the key reader and the pinned transport to the one endpoint
    "src/trade_graph/kernel/protected_budget.py",  # the scoped protected origins (already under kernel/)
    "src/trade_graph/kernel/subscription_proxy.py",  # the news_feeds catalogue (already under kernel/)
])
def test_the_news_checks_installed_route_and_receipts_are_protected_paths(path) -> None:
    assert path_is_protected(path), path


def test_reading_and_reporting_headlines_stays_ordinary_code() -> None:
    assert not path_is_protected("src/trade_graph/application/news_report.py")
