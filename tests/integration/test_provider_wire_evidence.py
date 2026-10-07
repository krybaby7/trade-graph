"""Real gateway/HTTPX boundaries with injected streams, never paid network calls."""

import hashlib
import json

import httpx
import pytest
from tests.integration.test_httpx_provider_failures import PRIVATE_SENTINEL, durable_gateway, mock_stream, response

from trade_graph.adapters.models.transport import provider_request_bytes


def test_error_wire_digest_retains_exact_dispatch_link_without_headers_or_bodies(tmp_path, monkeypatch):
    gateway, database, request, kwargs = durable_gateway(tmp_path)
    gateway.api_keys = {"openai": PRIVATE_SENTINEL}
    payload = {"id": "synthetic-cost-response", "model": request.model,
               "usage": {"input_tokens": 7, "output_tokens": 3}, "error": {"message": PRIVATE_SENTINEL}}
    received = response(429, payload)
    expected_response = hashlib.sha256(received.content).hexdigest()
    sent = []

    def denial(url, **options):
        pending = dict(database.execute("SELECT * FROM provider_transport_attempts").fetchone())
        assert pending["outcome"] == "DISPATCH_POSSIBLE" and pending["finished_at"] is None
        assert pending["request_sha256"] == hashlib.sha256(options["content"]).hexdigest()
        assert pending["endpoint_sha256"] == hashlib.sha256(url.encode()).hexdigest()
        sent.append(options)
        return received

    mock_stream(monkeypatch, denial)
    result = gateway.invoke(request, **kwargs)
    assert result.failure == "rate_limit" and result.usage is not None
    journal = dict(database.execute("SELECT * FROM provider_transport_attempts").fetchone())
    receipt = dict(database.execute("SELECT * FROM usage_receipts").fetchone())
    assert journal["reservation_id"] == receipt["reservation_id"]
    assert journal["invocation_id"] == kwargs["invocation_id"]
    assert journal["outcome"] == "HTTP_RESPONSE" and journal["status_code"] == 429
    assert journal["response_sha256"] == expected_response
    assert journal["transport_basis"] == "protected_httpx_observation"
    assert journal["synthetic"] == 0  # Key presence is still not external authentication.
    assert PRIVATE_SENTINEL not in json.dumps(journal)
    assert sent[0]["headers"]["accept-encoding"] == "identity" and sent[0]["follow_redirects"] is False
    assert gateway.invoke(request, **kwargs) == result and len(sent) == 1
    database.close()


def test_crash_after_observed_response_preserves_wire_facts_and_never_repeats_dispatch(tmp_path, monkeypatch):
    gateway, database, request, kwargs = durable_gateway(tmp_path)
    received = response(200, {"id": "synthetic-response", "model": request.model,
        "usage": {"input_tokens": 7, "output_tokens": 3}, "output": [{"type": "message", "content": [
            {"type": "output_text", "text": '{"action":"hold"}'}]}]})
    mock_stream(monkeypatch, lambda *args, **options: received)
    monkeypatch.setattr(gateway.budget, "commit", lambda *args, **options: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        gateway.invoke(request, **kwargs)
    wire = dict(database.execute("SELECT * FROM provider_transport_attempts").fetchone())
    assert wire["outcome"] == "HTTP_RESPONSE" and wire["response_sha256"]
    assert database.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 0
    mock_stream(monkeypatch, lambda *args, **options: pytest.fail("recovered dispatch repeated"))
    recovered = gateway.invoke(request, **kwargs)
    assert recovered.failure == "timeout_uncertain"
    assert database.execute("SELECT state FROM budget_reservations").fetchone()[0] == "UNCERTAIN"
    assert dict(database.execute("SELECT * FROM provider_transport_attempts").fetchone()) == wire
    database.close()


@pytest.mark.parametrize("status,headers,raw", [
    (302, {"location": "https://invalid/" + PRIVATE_SENTINEL}, b"secret"),
    (200, {"content-encoding": "gzip"}, b"not-a-decompression-input"),
    (200, {"content-length": "1048577"}, b"secret"),
    (200, {"content-length": "-1"}, b"secret"),
    (200, {}, b"a" * 1025),
])
def test_refused_response_never_creates_usage_or_complete_response_digest(tmp_path, monkeypatch, status, headers, raw):
    gateway, database, request, kwargs = durable_gateway(tmp_path)
    gateway.transport.maximum_response_bytes = 1024
    fake = httpx.Response(status, headers=headers, stream=httpx.ByteStream(raw))
    mock_stream(monkeypatch, lambda *args, **options: fake)
    result = gateway.invoke(request, **kwargs)
    assert not result.ok and result.usage is None
    journal = dict(database.execute("SELECT * FROM provider_transport_attempts").fetchone())
    assert journal["outcome"] == "REFUSED_RESPONSE" and journal["response_sha256"] is None
    assert PRIVATE_SENTINEL not in json.dumps(journal)
    assert database.execute("SELECT state FROM budget_reservations").fetchone()[0] == "UNCERTAIN"
    assert database.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 0
    database.close()


@pytest.mark.parametrize("body", [b'{"usage":{},"usage":{"input_tokens":1}}', b'{"count":NaN}'])
def test_ambiguous_json_remains_uncertain_with_retained_complete_wire_digest(tmp_path, monkeypatch, body):
    gateway, database, request, kwargs = durable_gateway(tmp_path)
    fake = httpx.Response(200, content=body)
    mock_stream(monkeypatch, lambda *args, **options: fake)
    result = gateway.invoke(request, **kwargs)
    assert not result.ok and result.usage is None
    journal = dict(database.execute("SELECT * FROM provider_transport_attempts").fetchone())
    assert journal["outcome"] == "HTTP_RESPONSE" and journal["response_sha256"] == hashlib.sha256(body).hexdigest()
    assert database.execute("SELECT state FROM budget_reservations").fetchone()[0] == "UNCERTAIN"
    database.close()


def test_trickled_stream_has_elapsed_deadline_and_unknown_billing(tmp_path, monkeypatch):
    gateway, database, request, kwargs = durable_gateway(tmp_path)

    class Trickle(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"id":'
            yield b'"not-complete"}'

    fake = httpx.Response(200, stream=Trickle())
    ticks = iter([0, 1, 6])
    monkeypatch.setattr("trade_graph.adapters.models.transport.monotonic", lambda: next(ticks))
    mock_stream(monkeypatch, lambda *args, **options: fake)
    result = gateway.invoke(request, **kwargs)
    assert result.failure == "timeout_uncertain"
    journal = dict(database.execute("SELECT * FROM provider_transport_attempts").fetchone())
    assert journal["outcome"] == "UNCERTAIN" and journal["error_category"] == "deadline"
    assert journal["response_sha256"] is None and journal["response_bytes"] == 6
    database.close()


def test_request_memory_bound_rejects_oversized_data_before_dispatch():
    with pytest.raises(ValueError, match="byte bound"):
        provider_request_bytes({"prompt": "x" * 1048577})
    with pytest.raises(ValueError, match="structural bound"):
        body = {}
        for _ in range(65):
            body = {"next": body}
        provider_request_bytes(body)


@pytest.mark.parametrize("value", [None, "invalid-fixture", []])
def test_nondict_fixture_key_cannot_bypass_wire_journal_or_synthetic_classification(tmp_path, monkeypatch, value):
    gateway, database, request, kwargs = durable_gateway(tmp_path)
    request = request.model_copy(update={"context": {"http_fixture": value}})

    def deny(*args, **options):
        attempt = dict(database.execute("SELECT * FROM provider_transport_attempts").fetchone())
        assert attempt["outcome"] == "DISPATCH_POSSIBLE" and attempt["synthetic"] == 1
        return response(403, {})

    mock_stream(monkeypatch, deny)
    result = gateway.invoke(request, **kwargs)
    assert result.failure == "credentials"
    assert database.execute("SELECT synthetic FROM budget_reservations").fetchone()[0] == 1
    assert database.execute("SELECT outcome FROM provider_transport_attempts").fetchone()[0] == "HTTP_RESPONSE"
    database.close()
