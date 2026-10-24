"""The runtime inventory refuses a linked directory it would hash, and ignores links among the directories it
excludes anyway: Homebrew's Python links site-packages into the standard library tree."""

import pytest

from trade_graph.adapters.engineering.plugin_runtime import runtime_environment_sha256


def fake_stdlib(root):
    (root / "json").mkdir(parents=True)
    (root / "os.py").write_text("x = 1\n")
    (root / "json" / "__init__.py").write_text("y = 2\n")
    return root


def test_a_linked_excluded_directory_does_not_block_the_inventory(tmp_path):
    stdlib = fake_stdlib(tmp_path / "stdlib")
    elsewhere = tmp_path / "homebrew-site-packages"
    elsewhere.mkdir()
    (stdlib / "site-packages").symlink_to(elsewhere, target_is_directory=True)
    digest = runtime_environment_sha256(stdlib=stdlib)
    assert len(digest) == 64 and int(digest, 16) >= 0


def test_a_linked_directory_that_would_be_hashed_is_still_refused(tmp_path):
    stdlib = fake_stdlib(tmp_path / "stdlib")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (stdlib / "encodings").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(PermissionError, match="linked dependency directories"):
        runtime_environment_sha256(stdlib=stdlib)
