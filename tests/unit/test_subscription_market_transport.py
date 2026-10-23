"""Protected paper data uses the reviewed market listener, never ambient proxies."""

from types import SimpleNamespace

import pytest

from trade_graph.paper_runtime import subscription_market_transport


def test_market_transport_has_fixed_proxy_and_no_environment(monkeypatch, tmp_path):
    import trade_graph.kernel.subscription_network as module
    monkeypatch.setattr(module, 'load_subscription_network_profile', lambda directory:
        SimpleNamespace(market_proxy_url='http://172.30.0.2:8081'))
    transport = subscription_market_transport(tmp_path)
    assert transport.proxy == 'http://172.30.0.2:8081'
    assert transport.trust_env is False


def test_missing_protected_market_route_never_uses_host_network(monkeypatch, tmp_path):
    import trade_graph.kernel.subscription_network as module
    def absent(directory):
        raise FileNotFoundError
    monkeypatch.setattr(module, 'load_subscription_network_profile', absent)
    with pytest.raises(FileNotFoundError):
        subscription_market_transport(tmp_path)
