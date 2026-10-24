"""macOS keeps /etc, /tmp and /var as root-owned links into /private; the no-follow walk must still reach them."""

import os
import sys
from pathlib import Path

import pytest

from trade_graph.adapters.engineering.artifact_files import canonical_system_path, open_directory


def readlink_like_macos(path):
    targets = {"/etc": "private/etc", "/tmp": "private/tmp", "/var": "private/var", "/opt": "/Volumes/other"}
    if path in targets:
        return targets[path]
    raise OSError(22, "not a link")


@pytest.mark.parametrize("given, expected", [
    ("/var/folders/ab/T/pytest-1/x.sqlite", "/private/var/folders/ab/T/pytest-1/x.sqlite"),
    ("/tmp/trade-graph/runtime", "/private/tmp/trade-graph/runtime"),
    ("/etc/ssl/certs", "/private/etc/ssl/certs"),
])
def test_apple_root_links_are_rewritten_to_their_private_targets(given, expected):
    assert canonical_system_path(Path(given), platform="darwin", readlink=readlink_like_macos) == Path(expected)


@pytest.mark.parametrize("given", ["/Users/owner/runtime/x.sqlite", "/opt/other/x.sqlite", "/private/var/x", "/"])
def test_other_paths_are_left_alone_even_when_they_are_links(given):
    assert canonical_system_path(Path(given), platform="darwin", readlink=readlink_like_macos) == Path(given)


def test_nothing_is_rewritten_on_linux():
    path = Path("/var/lib/trade-graph/x.sqlite")
    assert canonical_system_path(path, platform="linux", readlink=readlink_like_macos) == path


def test_a_root_link_with_an_unexpected_target_is_not_rewritten():
    def hostile(path):
        return "/Volumes/attacker/var"

    path = Path("/var/lib/x")
    assert canonical_system_path(path, platform="darwin", readlink=hostile) == path


@pytest.mark.skipif(sys.platform != "darwin", reason="exercises the real Apple root links")
def test_open_directory_reaches_the_private_target_on_macos():
    descriptor = open_directory(Path("/tmp"))
    try:
        held = os.fstat(descriptor)
        real = os.stat("/private/tmp")
        assert (held.st_dev, held.st_ino) == (real.st_dev, real.st_ino)
    finally:
        os.close(descriptor)
