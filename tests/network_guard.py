"""Make an accidental network call fail a test instead of reaching the internet."""

from __future__ import annotations

import socket

import pytest


def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args, **_kwargs):
        raise AssertionError("a test attempted a network call")

    for name in ("create_connection", "getaddrinfo", "gethostbyname"):
        monkeypatch.setattr(socket, name, refuse)
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, refuse)
