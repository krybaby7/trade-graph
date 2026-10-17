"""What the deployed Engineer may not edit (W8-A, D244): the installed Perplexity route, the reviewed network routes,
and the price collector that feeds the coin rule and the price alarms (W6-D left it open)."""

from __future__ import annotations

import pytest

from trade_graph.kernel.authority import path_is_protected


@pytest.mark.parametrize("path", [
    "src/trade_graph/application/perplexity_installation.py",  # key, prices, route money rule, department routing
    "src/trade_graph/application/price_collector.py",  # feeds the coin rule's hourly scan and the price alarms
    "src/trade_graph/application/service_market_data.py",
    "src/trade_graph/kernel/subscription_proxy.py",  # the reviewed route catalogue (already under kernel/)
    "src/trade_graph/kernel/subscription_network.py",
])
def test_the_installed_route_and_its_inputs_are_protected_paths(path) -> None:
    assert path_is_protected(path), path
