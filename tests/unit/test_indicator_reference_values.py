"""Fixed reference values: hand-derived numbers with their derivations, checked against TA-Lib via the wrapper."""

from __future__ import annotations

import json
import math
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from trade_graph.application.indicators.library import compute_raw
from trade_graph.contracts.indicators import INDICATOR_MENU, get_indicator

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "indicators" / "reference_values.json"
_DOCUMENT = json.loads(_FIXTURE.read_text(encoding="utf-8"))
_CASES = _DOCUMENT["cases"]
_BASES = {"hand_derived", "definition_boundary"}
_TOLERANCE = 1e-9


def _id(case) -> str:
    return case["id"]


def test_fixture_is_honest_about_published_examples() -> None:
    assert _DOCUMENT["schema_version"] == 1
    assert "None" in _DOCUMENT["published_numeric_examples"]
    assert "not" in _DOCUMENT["published_numeric_examples"].lower()
    assert len({case["id"] for case in _CASES}) == len(_CASES)


@pytest.mark.parametrize("case", _CASES, ids=_id)
def test_case_is_documented(case) -> None:
    assert case["basis"] in _BASES
    source = case["source"]
    assert len(source["definition"]) > 20
    assert len(source["derivation"]) > 30
    spec = get_indicator(case["entry"])
    assert set(case["expected"]) <= {output.name for output in spec.outputs}
    length = len(next(iter(case["columns"].values())))
    assert all(len(column) == length for column in case["columns"].values())
    assert all(len(values) == length for values in case["expected"].values())
    assert set(spec.requires) <= set(case["columns"])


@pytest.mark.parametrize("case", _CASES, ids=_id)
def test_library_reproduces_the_reference_values(case) -> None:
    columns = {name: tuple(Decimal(value) for value in values) for name, values in case["columns"].items()}
    result = compute_raw(case["entry"], case["params"], columns)
    for output, expected in case["expected"].items():
        assert len(result[output]) == len(expected)
        for index, text in enumerate(expected):
            got = float(result[output][index])
            if text is None:
                assert math.isnan(got), (case["id"], output, index, got)
                continue
            want = float(Fraction(text))
            assert not math.isnan(got), (case["id"], output, index, "library gave no value")
            assert abs(got - want) <= _TOLERANCE * max(1.0, abs(want)), (case["id"], output, index, got, want)


def test_every_menu_entry_and_every_output_has_a_hand_derived_reference() -> None:
    covered: dict[str, set[str]] = {}
    for case in _CASES:
        if case["basis"] == "hand_derived":
            covered.setdefault(case["entry"], set()).update(case["expected"])
    for spec in INDICATOR_MENU:
        assert covered.get(spec.name, set()) == {output.name for output in spec.outputs}, spec.name


def test_references_have_defined_values_not_only_undefined_ones() -> None:
    for case in _CASES:
        for output, values in case["expected"].items():
            assert any(value is not None for value in values), (case["id"], output)
            assert np.isfinite([float(Fraction(value)) for value in values if value is not None]).all()
