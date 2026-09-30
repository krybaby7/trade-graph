"""Actual HTTPX exception types exercise durable gateway costs; all HTTP is mocked."""

from decimal import Decimal

import httpx
import pytest
from tests.integration.test_provider_transport import _card, _gateway, _request

from trade_graph.adapters.models.transport import HttpxProviderHttp, ProviderHttpResponseError
from trade_graph.application.ledger import Ledger
from trade_graph.application.scheduler import Scheduler

PRIVATE_SENTINEL = "synthetic-private-header-url-and-body"


def durable_gateway(tmp_path, *, provider="openai", timeout=12):
    gateway, database = _gateway(tmp_path, HttpxProviderHttp(timeout))
    model = "gpt-6-luna" if provider == "openai" else "claude-sonnet-5-5"
    if provider == "anthropic":
        gateway.budget.seed_card(_card().model_copy(update={
            "price_card_id": "anthropic-card", "provider": provider, "model": model,
            "endpoint": "https://api.anthropic.com/v1/messages",
        }))
    portfolio = Ledger(database, gateway.budget.clock).create_portfolio(reporting_currency="EUR")
    task = Scheduler(database, gateway.budget.clock).add_task(
        role="trader", objective="HTTP error handling", portfolio_id=portfolio, allocated_spend=Decimal("1"),
    )
    request = _request(provider=provider, model=model, task_id=task, root_task_id=task, context={}, max_tool_calls=0)
    kwargs = {
        "deployment_id": "deployment", "price_card_id": "card" if provider == "openai" else "anthropic-card",
        "fx_rate": Decimal("1"), "fx_buffer": Decimal("1.02"),
        "invocation_id": "immutable-http-attempt", "portfolio_id": portfolio,
    }
    return gateway, database, request, kwargs


def response(status, payload=None, *, text=None):
    request = httpx.Request("POST", f"https://example.invalid/{PRIVATE_SENTINEL}")
    return httpx.Response(status, json=payload, text=text, request=request)


@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectError, httpx.WriteError,
                                         httpx.RemoteProtocolError])
def test_httpx_request_errors_are_durable_uncertainty_not_worker_exceptions(tmp_path, monkeypatch, error_type):
    gateway, database, request, kwargs = durable_gateway(tmp_path)
    calls = []

    def fail(*args, **options):
        calls.append(options)
        raise error_type(PRIVATE_SENTINEL, request=httpx.Request("POST", f"https://invalid/{PRIVATE_SENTINEL}"))

    monkeypatch.setattr(httpx, "post", fail)
    result = gateway.invoke(request, **kwargs)
    assert result.failure == "timeout_uncertain" and result.usage is None
    assert PRIVATE_SENTINEL not in result.model_dump_json()
    assert database.execute("SELECT state FROM model_invocations").fetchone()[0] == "UNCERTAIN"
    assert database.execute("SELECT state FROM budget_reservations").fetchone()[0] == "UNCERTAIN"
    assert database.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 0
    assert gateway.invoke(request, **kwargs) == result
    assert len(calls) == 1 and calls[0]["timeout"] == request.timeout_seconds


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("status, failure", [
    (400, "validation"), (401, "credentials"), (403, "credentials"), (408, "timeout_uncertain"),
    (429, "rate_limit"), (500, "temporary"), (504, "timeout_uncertain"),
])
def test_http_denials_classify_failure_but_never_invent_zero_usage(tmp_path, monkeypatch, provider, status, failure):
    gateway, database, request, kwargs = durable_gateway(tmp_path, provider=provider)
    payload = {"error": {"message": PRIVATE_SENTINEL}, "output": [{"type": "message", "content": [
        {"type": "output_text", "text": '{"action":"hold"}'},
    ]}]}
    monkeypatch.setattr(httpx, "post", lambda *args, **options: response(status, payload))
    result = gateway.invoke(request, **kwargs)
    assert not result.ok and result.failure == failure and result.usage is None
    assert result.message == f"provider HTTP {status}" and PRIVATE_SENTINEL not in result.model_dump_json()
    assert database.execute("SELECT state FROM model_invocations").fetchone()[0] == "UNCERTAIN"
    assert database.execute("SELECT state FROM budget_reservations").fetchone()[0] == "UNCERTAIN"
    assert database.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 0


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_http_denial_with_reported_usage_has_exactly_one_receipt_and_allocation(tmp_path, monkeypatch, provider):
    gateway, database, request, kwargs = durable_gateway(tmp_path, provider=provider)
    payload = {"id": "synthetic-denial-usage", "model": request.model,
               "usage": {"input_tokens": 7, "output_tokens": 3}, "error": {"message": PRIVATE_SENTINEL}}
    monkeypatch.setattr(httpx, "post", lambda *args, **options: response(429, payload))
    result = gateway.invoke(request, **kwargs)
    assert not result.ok and result.failure == "rate_limit"
    assert result.usage.uncached_input_tokens == 7 and result.usage.billed_output_tokens == 3
    assert result.usage.provider_request_id == "synthetic-denial-usage"
    monkeypatch.setattr(httpx, "post", lambda *args, **options: pytest.fail("denied attempt dispatched twice"))
    assert gateway.invoke(request, **kwargs) == result
    assert database.execute("SELECT state FROM model_invocations").fetchone()[0] == "COMPLETED"
    assert database.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 1
    assert database.execute("SELECT COUNT(*) FROM cost_allocations").fetchone()[0] == 1
    assert database.execute("SELECT synthetic FROM usage_receipts").fetchone()[0] == 1
    assert PRIVATE_SENTINEL not in database.execute("SELECT result_json FROM model_invocations").fetchone()[0]


@pytest.mark.parametrize("usage", [
    {"input_tokens": PRIVATE_SENTINEL, "output_tokens": 3},
    [{"input_tokens": PRIVATE_SENTINEL}],
    {"input_tokens": 3, "input_tokens_details": [PRIVATE_SENTINEL]},
])
def test_malformed_reported_usage_remains_uncertain_and_redacted(tmp_path, monkeypatch, usage):
    gateway, database, request, kwargs = durable_gateway(tmp_path)
    monkeypatch.setattr(httpx, "post", lambda *args, **options: response(429, {
        "usage": usage,
        "error": {"message": PRIVATE_SENTINEL},
    }))
    result = gateway.invoke(request, **kwargs)
    assert result.failure == "rate_limit" and result.usage is None
    assert PRIVATE_SENTINEL not in result.model_dump_json()
    assert database.execute("SELECT state FROM budget_reservations").fetchone()[0] == "UNCERTAIN"


def test_non_json_error_body_cannot_escape_or_leak(tmp_path, monkeypatch):
    gateway, database, request, kwargs = durable_gateway(tmp_path)
    monkeypatch.setattr(httpx, "post", lambda *args, **options: response(502, text=PRIVATE_SENTINEL))
    result = gateway.invoke(request, **kwargs)
    assert result.failure == "temporary" and result.usage is None
    assert PRIVATE_SENTINEL not in result.model_dump_json()
    assert database.execute("SELECT state FROM model_invocations").fetchone()[0] == "UNCERTAIN"


@pytest.mark.parametrize("configured, requested, effective", [(12, 5, 5), (3, 20, 3)])
def test_gateway_forwards_request_timeout_with_transport_maximum(
    tmp_path, monkeypatch, configured, requested, effective,
):
    gateway, _, request, kwargs = durable_gateway(tmp_path, timeout=configured)
    observed = []

    def denial(*args, **options):
        observed.append(options["timeout"])
        return response(401, {})

    monkeypatch.setattr(httpx, "post", denial)
    gateway.invoke(request.model_copy(update={"timeout_seconds": requested}), **kwargs)
    assert observed == [effective]


def test_legacy_three_argument_transport_is_invoked_once_without_timeout_keyword(tmp_path):
    class LegacyTransport:
        def __init__(self):
            self.calls = 0

        def post_json(self, url, body, headers):
            self.calls += 1
            return {"id": "legacy", "usage": {"input_tokens": 1, "output_tokens": 1}, "output": [
                {"type": "message", "content": [{"type": "output_text", "text": '{"action":"hold"}'}]},
            ]}

    transport = LegacyTransport()
    gateway, _ = _gateway(tmp_path, transport)
    result = gateway.invoke(_request(context={}, max_tool_calls=0), deployment_id="deployment", price_card_id="card",
                            fx_rate=Decimal("1"), fx_buffer=Decimal("1.02"))
    assert result.ok and transport.calls == 1


def test_legacy_transport_type_error_after_dispatch_is_never_retried(tmp_path):
    class LegacyTransport:
        def __init__(self):
            self.calls = 0

        def post_json(self, url, body, headers):
            self.calls += 1
            raise TypeError(PRIVATE_SENTINEL)

    transport = LegacyTransport()
    gateway, database = _gateway(tmp_path, transport)
    result = gateway.invoke(_request(context={}, max_tool_calls=0), deployment_id="deployment", price_card_id="card",
                            fx_rate=Decimal("1"), fx_buffer=Decimal("1.02"))
    assert result.failure == "validation" and transport.calls == 1
    assert PRIVATE_SENTINEL not in result.message
    assert database.execute("SELECT state FROM budget_reservations").fetchone()[0] == "UNCERTAIN"


def test_error_contract_retains_only_billing_metadata(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *args, **options: response(403, {
        "id": "synthetic-response", "model": "gpt-6-luna", "usage": {"input_tokens": 2, "output_tokens": 1},
        "error": {"message": PRIVATE_SENTINEL}, "raw_headers": PRIVATE_SENTINEL,
    }))
    with pytest.raises(ProviderHttpResponseError) as caught:
        HttpxProviderHttp().post_json(f"https://invalid/{PRIVATE_SENTINEL}", {}, {"authorization": PRIVATE_SENTINEL})
    assert str(caught.value) == "provider HTTP 403"
    assert set(caught.value.usage_payload) == {"id", "model", "usage"}
    assert PRIVATE_SENTINEL not in str(caught.value.usage_payload)
