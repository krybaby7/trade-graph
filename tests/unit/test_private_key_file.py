"""The Perplexity key is read only from the owner's private file (T36, D125). Made-up keys only."""

import os
from pathlib import Path

import pytest

from trade_graph.application.private_key_file import read_private_key

FAKE = "pplx-synthetic-0123456789abcdef"


def _write(path: Path, text: str = FAKE + "\n", mode: int = 0o600) -> Path:
    path.write_text(text, encoding="ascii")
    path.chmod(mode)
    return path


def test_reads_an_owner_only_file_outside_git_and_never_shows_the_value(tmp_path) -> None:
    key = read_private_key(_write(tmp_path / "perplexity.key"))
    assert key.get_secret_value() == FAKE
    assert FAKE not in repr(key) and FAKE not in str(key) and FAKE not in f"{key}"


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o660, 0o644])
def test_refuses_a_file_others_can_read(tmp_path, mode) -> None:
    with pytest.raises(PermissionError, match="owner-only") as caught:
        read_private_key(_write(tmp_path / "perplexity.key", mode=mode))
    assert FAKE not in str(caught.value)


def test_refuses_a_symlink_a_hard_link_and_a_directory(tmp_path) -> None:
    target = _write(tmp_path / "real.key")
    (tmp_path / "link.key").symlink_to(target)
    with pytest.raises(PermissionError):
        read_private_key(tmp_path / "link.key")
    os.link(target, tmp_path / "second-name.key")
    with pytest.raises(PermissionError, match="single"):
        read_private_key(target)
    (tmp_path / "folder.key").mkdir(mode=0o700)
    with pytest.raises(PermissionError):
        read_private_key(tmp_path / "folder.key")


def test_refuses_a_file_inside_a_git_checkout(tmp_path) -> None:
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    (checkout / "private").mkdir(mode=0o700)
    with pytest.raises(PermissionError, match="Git"):
        read_private_key(_write(checkout / "private" / "perplexity.key"))


def test_refuses_this_repository(tmp_path) -> None:
    inside = Path(__file__).resolve().parents[2] / "runtime" / "never-created.key"
    with pytest.raises(PermissionError, match="Git"):
        read_private_key(inside)


def test_refuses_relative_paths_and_writable_folders(tmp_path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        read_private_key(Path("perplexity.key"))
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o777)
    with pytest.raises(PermissionError, match="folder"):
        read_private_key(_write(shared / "perplexity.key"))


@pytest.mark.parametrize("text", ["", "\n", "short-key\n", "pplx with spaces in the middle\n",
                                  "pplx-é-0123456789abcdef", "x" * 5000, FAKE + "\n" + FAKE + "\n"])
def test_refuses_content_that_is_not_one_plain_key(tmp_path, text) -> None:
    path = tmp_path / "perplexity.key"
    path.write_bytes(text.encode("utf-8"))
    path.chmod(0o600)
    with pytest.raises((PermissionError, ValueError)) as caught:
        read_private_key(path)
    assert FAKE not in str(caught.value)


def test_missing_file_is_a_plain_error(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        read_private_key(tmp_path / "absent.key")
