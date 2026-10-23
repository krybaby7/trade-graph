"""A provider CLI receives only the root-reviewed private proxy endpoint."""

import hashlib
from pathlib import Path

import pytest

from trade_graph.adapters.models.subscription_process import LinuxFilesystemBoundary, NativeCliPin


def pin():
    binary = Path('/usr/bin/python3').resolve()
    return NativeCliPin(binary, hashlib.sha256(binary.read_bytes()).hexdigest(), require_root_owner=False)


def test_fixed_provider_proxy_is_the_only_child_egress_setting():
    boundary = LinuxFilesystemBoundary(pin(), share_network=True, proxy_url='http://172.30.0.2:8080')
    command = boundary.command(['--version'])
    assert command[command.index('HTTPS_PROXY') + 1] == 'http://172.30.0.2:8080'
    assert 'HTTP_PROXY' not in command and 'ALL_PROXY' not in command


@pytest.mark.parametrize('proxy', ['https://example.com', 'http://127.0.0.1:8080',
    'http://169.254.169.254:8080', 'http://172.30.0.2:8080/path',
    'http://user:pass@172.30.0.2:8080', 'http://8.8.8.8:8080'])
def test_provider_proxy_rejects_credentials_dns_public_and_metadata_destinations(proxy):
    with pytest.raises(ValueError, match='proxy'):
        LinuxFilesystemBoundary(pin(), share_network=True, proxy_url=proxy)


def test_an_offline_boundary_cannot_silently_enable_proxy_network():
    with pytest.raises(ValueError, match='proxy'):
        LinuxFilesystemBoundary(pin(), share_network=False, proxy_url='http://172.30.0.2:8080')
