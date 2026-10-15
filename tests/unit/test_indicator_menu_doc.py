"""docs/INDICATOR-MENU.md must agree with the menu contract, the cross-check table and the locked versions."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import re
from pathlib import Path

import pytest

from trade_graph.application.indicators.library import library_versions
from trade_graph.contracts.indicators import INDICATOR_MENU, SETTLE_TOLERANCE

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "render_indicator_menu.py"
_SPEC = importlib.util.spec_from_file_location("render_indicator_menu", _SCRIPT)
_RENDER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_RENDER)
_DOC = (_ROOT / "docs" / "INDICATOR-MENU.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(_RENDER.render_blocks()))
def test_generated_table_is_current(name) -> None:
    match = _RENDER.block_pattern(name).search(_DOC)
    assert match, f"markers for {name} are missing"
    assert match.group(2) == _RENDER.render_blocks()[name], (
        f"the {name} table is stale; run: uv run python scripts/render_indicator_menu.py --update")


def test_every_menu_entry_appears_in_the_document() -> None:
    for spec in INDICATOR_MENU:
        assert f"**{spec.name}**" in _DOC


def test_document_states_the_locked_versions_and_the_findings() -> None:
    versions = library_versions()
    assert f"**{importlib.metadata.version('TA-Lib')}**" in _DOC
    assert f"**{versions['ta_lib_c']}**" in _DOC
    assert f"**{versions['numpy']}**" in _DOC
    for distribution in ("pandas-ta-classic", "talipp"):
        assert f"{distribution} | {importlib.metadata.version(distribution)}," in _DOC.replace("**", "")
    assert str(SETTLE_TOLERANCE).replace("e-05", "e-5") in _DOC
    for heading in ("## The menu", "## Dependency findings", "## Why pandas-ta-classic is the second library",
                    "### Documented differences", "## Reference values", "## Changing the menu"):
        assert heading in _DOC
    assert re.search(r"No published numeric worked example|no published numeric worked example", _DOC)


def test_prose_carries_no_hand_counted_totals_that_can_go_stale() -> None:
    """Counts of entries, comparisons and reference cases are generated (coverage-counts); prose must not repeat them.

    The prose once said 'All 25', '23 of 25' and '47 cases' after the menu had grown to 26 entries and 50 cases."""
    prose = re.sub(r"<!-- BEGIN.*?<!-- END [a-z-]+ -->", "", _DOC, flags=re.DOTALL)
    assert not re.search(r"\b(?:all|every) \d+\b|\b\d+ of (?:the )?\d+ entries|\b\d+ of \d+\b|\b\d+ cases\b|"
                         r"\bmatches on \d+\b|\b\d+ (?:menu )?entries\b", prose, re.IGNORECASE)
    assert f"{len(INDICATOR_MENU)} standard indicators" in prose  # the one plain-words total, checked against the menu


def test_document_has_no_stray_markers() -> None:
    assert _DOC.count("<!-- BEGIN") == _DOC.count("<!-- END") == len(_RENDER.render_blocks())


def test_document_describes_the_engine_and_the_hourly_features_with_the_numbers_the_code_uses() -> None:
    from trade_graph.application.indicators import engine
    from trade_graph.application.indicators.library import SNAPSHOT_DIGITS
    from trade_graph.application.price_history import FEATURE_VERSION, FEATURE_VERSION_V2

    text = " ".join(_DOC.split())  # the document wraps lines
    for heading in ("## The engine", "### Rules", "### History read at the default settings",
                    "## Hourly price features v3", "## How the Trader is meant to use the menu"):
        assert heading in _DOC
    assert f"{engine.MAX_HISTORY_CANDLES:,}" in text and "13,499" in text
    assert f"from 1 to {engine.MAX_OUTPUT_POINTS}" in text
    assert f"fixed {engine.START_DEPENDENT_HISTORY} candles" in text
    assert f"{SNAPSHOT_DIGITS} significant digits" in text and f"rounding at {SNAPSHOT_DIGITS}" in text
    assert FEATURE_VERSION in text and FEATURE_VERSION_V2 in text
    assert f"first {engine.DOCUMENT_MISSING_LIMIT}" in text
    for relation in engine.RELATIONS:
        assert f"`{relation}`" in text
