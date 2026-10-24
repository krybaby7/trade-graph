"""A fair test, kept by protected software (W11-B, C11; D280, D294): the deployed Engineer cannot change the practice
range's rules (tune earlier, confirm once on untouched later days, every try listed), the replay, the report or the
strategy lab that runs the Trading desk's requests."""

from __future__ import annotations

import pytest

from trade_graph.kernel.authority import PROTECTED_PATH_PREFIXES, path_is_protected


@pytest.mark.parametrize("path", [
    "src/trade_graph/contracts/practice_range.py",  # the grammar and the study windows
    "src/trade_graph/application/practice_range/store.py",  # the study rules and the walk-forward offer
    "src/trade_graph/application/practice_range/replay.py",  # the replay
    "src/trade_graph/application/practice_range/service.py",  # the report and its hash
    "src/trade_graph/application/practice_range/summary.py",  # what the desk reads
    "src/trade_graph/contracts/strategy_lab.py",  # the lab requests
    "src/trade_graph/application/strategy_lab/queue.py",
    "src/trade_graph/application/strategy_lab/worker.py",
    "src/trade_graph/application/strategy_lab/context.py",
    "src/trade_graph/application/strategy_lab/starting.py",
    "src/trade_graph/application/strategy_lab/installation.py",
    "src/trade_graph/application/strategy_lab/starting/slow-trend-pullback.v1.json",  # the packaged examples
    "src/trade_graph/adapters/persistence/strategy_lab_schema.py",  # migration 0051 (already under persistence/)
    "src/trade_graph/kernel/trade_metrics.py",  # the result sums (already under kernel/)
])
def test_the_practice_range_and_the_strategy_lab_are_protected_paths(path) -> None:
    assert path_is_protected(path), path


def test_the_prefixes_name_the_whole_packages() -> None:
    assert "src/trade_graph/application/practice_range/" in PROTECTED_PATH_PREFIXES
    assert "src/trade_graph/application/strategy_lab/" in PROTECTED_PATH_PREFIXES
    assert not path_is_protected("src/trade_graph/application/practice_range_notes.py")
