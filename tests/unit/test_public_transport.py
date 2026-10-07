"""Public acquisition is bounded; scripted HTTP does not verify a live endpoint."""

import httpx
import pytest

from trade_graph.adapters.market import public
from trade_graph.domain.errors import ValidationFailure


class Chunks(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False
        self.reads = 0

    def __iter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk

    def close(self):
        self.closed = True


def _client(monkeypatch, response, *, trust_env=True, proxy=None):
    original = httpx.Client
    requests = []

    def handler(request):
        requests.append(request)
        return response

    def factory(*args, **kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is trust_env
        assert kwargs.pop("proxy") == proxy
        return original(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)
    return requests


def test_public_transport_decodes_bounded_stream_and_closes(monkeypatch):
    stream = Chunks([b'{"rate":', b"0.9}"])
    calls = _client(monkeypatch, httpx.Response(200, stream=stream))
    text = public.HttpxTextTransport(maximum_response_bytes=12).get_text("https://api.frankfurter.dev/v2/rates")
    assert text == '{"rate":0.9}'
    assert len(calls) == 1 and calls[0].headers["accept-encoding"] == "identity"
    assert stream.closed


def test_protected_public_transport_uses_pinned_proxy_without_environment_fallback(monkeypatch):
    stream = Chunks([b'{"rate":0.9}'])
    proxy = "http://protected-proxy.invalid:8080"
    calls = _client(monkeypatch, httpx.Response(200, stream=stream), trust_env=False, proxy=proxy)
    text = public.HttpxTextTransport(proxy=proxy, trust_env=False).get_text("https://api.frankfurter.dev/v2/rates")
    assert text == '{"rate":0.9}'
    assert len(calls) == 1 and stream.closed


@pytest.mark.parametrize("headers", [{}, {"content-length": "4"}])
def test_public_transport_limits_actual_decoded_bytes_even_when_length_understates(monkeypatch, headers):
    stream = Chunks([b"1234", b"5", b"unused"])
    _client(monkeypatch, httpx.Response(200, headers=headers, stream=stream))
    with pytest.raises(ValidationFailure, match="byte limit"):
        public.HttpxTextTransport(maximum_response_bytes=4).get_text("https://api.kraken.com/0/public/Time")
    assert stream.reads == 2
    assert stream.closed


@pytest.mark.parametrize("length", ["-1", "5", "invalid"])
def test_public_transport_rejects_invalid_or_excessive_declared_length_before_body(monkeypatch, length):
    stream = Chunks([b"1234"])
    _client(monkeypatch, httpx.Response(200, headers={"content-length": length}, stream=stream))
    with pytest.raises(ValidationFailure):
        public.HttpxTextTransport(maximum_response_bytes=4).get_text("https://api.kraken.com/0/public/Time")
    assert stream.reads == 0 and stream.closed


def test_public_transport_rejects_trickled_body_at_elapsed_deadline(monkeypatch):
    stream = Chunks([b"1", b"2", b"3"])
    _client(monkeypatch, httpx.Response(200, stream=stream))
    times = iter([0, 0, 1, 5])
    monkeypatch.setattr(public, "monotonic", lambda: next(times))
    with pytest.raises(ValidationFailure, match="elapsed deadline"):
        public.HttpxTextTransport(timeout=5).get_text("https://api.kraken.com/0/public/Time")
    assert stream.reads == 2 and stream.closed


def test_public_transport_refuses_redirect_without_following_or_reading_body(monkeypatch):
    stream = Chunks([b"redirect"])
    calls = _client(monkeypatch, httpx.Response(302, headers={"location": "https://other.invalid/"}, stream=stream))
    with pytest.raises(httpx.HTTPStatusError):
        public.HttpxTextTransport().get_text("https://api.kraken.com/0/public/Time")
    assert len(calls) == 1 and stream.reads == 0 and stream.closed


def test_public_transport_refuses_compression_bomb_before_decoding(monkeypatch):
    import gzip

    stream = Chunks([gzip.compress(b"1" * 1_048_576)])
    _client(monkeypatch, httpx.Response(200, headers={"content-encoding": "gzip"}, stream=stream))
    with pytest.raises(ValidationFailure, match="unsupported content encoding"):
        public.HttpxTextTransport(maximum_response_bytes=4).get_text("https://api.kraken.com/0/public/Time")
    assert stream.reads == 0 and stream.closed


@pytest.mark.parametrize("arguments", [
    {"timeout": True}, {"timeout": 0}, {"timeout": float("inf")}, {"timeout": float("nan")},
    {"maximum_response_bytes": True}, {"maximum_response_bytes": 0}, {"maximum_response_bytes": 16_777_217},
])
def test_public_transport_refuses_unbounded_configuration(arguments):
    with pytest.raises(ValueError):
        public.HttpxTextTransport(**arguments)
