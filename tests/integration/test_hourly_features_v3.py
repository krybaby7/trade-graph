"""hourly-price-features-v3: every number comes from the indicator library; retained v2 snapshots stay readable.

The decision (D118): no indicator formula is written in the repository. The hourly fields that were hand-written
decimal arithmetic in v2 are re-expressed through TA-Lib, and the three that were ratios of library outputs are
retired in favour of the library outputs themselves.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import random
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, localcontext
from pathlib import Path

import pytest
from tests.integration.test_price_history import _candle, _database

from trade_graph.api.evidence import _MARKET_FEATURES, _retained_inputs
from trade_graph.application import price_history
from trade_graph.application.indicators.library import SNAPSHOT_DIGITS, rounded_decimal
from trade_graph.application.market_context import _compact_history
from trade_graph.application.price_history import (
    FEATURE_VERSION,
    FEATURE_VERSION_V2,
    HistoryStore,
)
from trade_graph.contracts.indicators import get_indicator
from trade_graph.contracts.price_history import HourlyCandle
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import ValidationFailure
from trade_graph.domain.money import canonical_decimal
from trade_graph.kernel.department_gateway import public_context

END = datetime(2026, 10, 5, 12, tzinfo=UTC)
AS_OF = END + timedelta(minutes=30)
_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hourly" / "snapshots_v2.json"
V3_FIELDS = ["sma_20", "sma_50", "highest_high_20", "range_high", "range_low", "midpoint", "hourly_return",
             "four_hour_drift"]
V2_ONLY_FIELDS = ["pullback_from_high", "pullback_depth", "midpoint_distance"]
V2_FIELDS = [name for name in V3_FIELDS if name != "highest_high_20"] + V2_ONLY_FIELDS


# --- the numbers -------------------------------------------------------------------------------------------------


def test_versions_are_distinct_and_the_v2_constant_survives() -> None:
    assert FEATURE_VERSION == "hourly-price-features-v3"
    assert FEATURE_VERSION_V2 == "hourly-price-features-v2" != FEATURE_VERSION


def _oracle(candles: list[HourlyCandle]) -> dict[str, Decimal]:
    """The frozen v2 definitions in exact Decimal arithmetic. Test-only: it checks the library path against the
    arithmetic the v2 code was verified with, and is the only place those formulas still live."""
    exact = Context(prec=60)
    closes, highs, lows = ([getattr(c, field) for c in candles] for field in ("close", "high", "low"))
    last24 = slice(-24, None)
    midpoint = exact.divide(max(highs[last24]) + min(lows[last24]), Decimal(2))
    return {
        "sma_20": exact.divide(sum(closes[-20:], Decimal(0)), Decimal(20)),
        "sma_50": exact.divide(sum(closes[-50:], Decimal(0)), Decimal(50)),
        "highest_high_20": max(highs[-20:]),
        "range_high": max(highs[last24]), "range_low": min(lows[last24]), "midpoint": midpoint,
        "hourly_return": exact.divide(closes[-1], closes[-2]) - 1,
        "four_hour_drift": exact.divide(closes[-1], closes[-5]) - 1,
    }


def _random_candles(seed: int, *, decimals: int, base: int) -> list[HourlyCandle]:
    rng = random.Random(seed)
    price = base * 10**decimals
    candles = []
    for index in range(1, 61):
        opened = price
        price = max(10, price + rng.randint(-price // 50, price // 50))
        high = max(opened, price) + rng.randint(0, price // 100)
        low = max(1, min(opened, price) - rng.randint(0, price // 100))
        scale = -decimals
        candles.append(_candle(
            index, count=60, open=Decimal(opened).scaleb(scale), high=Decimal(high).scaleb(scale),
            low=Decimal(low).scaleb(scale), close=Decimal(price).scaleb(scale), vwap=Decimal(price).scaleb(scale)))
    return candles


@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize("decimals,base", [(1, 63000), (2, 2600), (5, 3)])
def test_library_values_agree_with_the_v2_exact_definitions_to_twelve_digits(tmp_path, seed, decimals, base) -> None:
    database = _database(tmp_path)
    store = HistoryStore(database)
    candles = _random_candles(seed, decimals=decimals, base=base)
    store.save(candles)
    result = store.snapshot("BTC/USD", AS_OF, V3_FIELDS, source="synthetic")
    assert sorted(result["values"]) == sorted(V3_FIELDS)
    for name, exact in _oracle(candles).items():
        got = Decimal(result["values"][name])
        assert abs(got - exact) <= abs(exact) * Decimal("6e-12"), (name, got, exact)
    for name in ("highest_high_20", "range_high", "range_low", "midpoint"):
        # Values that are an input price, or half the sum of two, are exact: no rounding at all.
        assert Decimal(result["values"][name]) == _oracle(candles)[name], name
    database.close()


def test_values_are_exact_decimal_strings_and_the_snapshot_hash_is_stable(tmp_path) -> None:
    database = _database(tmp_path)
    store = HistoryStore(database)
    store.save(_random_candles(3, decimals=1, base=63000))
    first = store.snapshot("BTC/USD", AS_OF, V3_FIELDS, source="synthetic")
    with localcontext() as context:
        context.prec = 4
        context.rounding = "ROUND_DOWN"
        second = store.snapshot("BTC/USD", AS_OF, V3_FIELDS, source="synthetic")
    assert first == second and first["source_ref"] == second["source_ref"]
    for text in first["values"].values():
        assert type(text) is str and "e" not in text.lower() and text not in {"", "-0"}
        assert Decimal(text).as_tuple().exponent is not None
        assert len(Decimal(text).normalize().as_tuple().digits) <= SNAPSHOT_DIGITS  # no binary-float tail
    body = {key: value for key, value in first.items() if key != "source_ref"}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    assert first["source_ref"] == "hourly-features:" + hashlib.sha256(encoded).hexdigest()
    json.dumps(first, allow_nan=False)
    database.close()


def test_every_hourly_field_is_a_menu_entry_with_the_stated_library_function() -> None:
    expected = {
        "sma_20": ("SMA", "SMA", {"timeperiod": 20}), "sma_50": ("SMA", "SMA", {"timeperiod": 50}),
        "highest_high_20": ("HIGHEST_HIGH", "MAX", {"timeperiod": 20}),
        "range_high": ("HIGHEST_HIGH", "MAX", {"timeperiod": 24}),
        "range_low": ("LOWEST_LOW", "MIN", {"timeperiod": 24}),
        "midpoint": ("MIDPRICE", "MIDPRICE", {"timeperiod": 24}),
        "hourly_return": ("ROCP", "ROCP", {"timeperiod": 1}),
        "four_hour_drift": ("ROCP", "ROCP", {"timeperiod": 4}),
    }
    assert set(price_history._FEATURES) == set(expected)
    for name, (menu, function, parameters) in expected.items():
        feature = price_history._FEATURES[name]
        assert (feature.indicator, dict(feature.parameters)) == (menu, parameters), name
        assert get_indicator(menu).talib_function == function
        assert function in feature.definition


def test_window_lengths_are_the_librarys_lookback_plus_one() -> None:
    assert {name: feature.lookback for name, feature in price_history._FEATURES.items()} == {
        "sma_20": 20, "sma_50": 50, "highest_high_20": 20, "range_high": 24, "range_low": 24, "midpoint": 24,
        "hourly_return": 2, "four_hour_drift": 5}


def test_price_history_contains_no_indicator_arithmetic() -> None:
    """The module may select candles and call the library; it may not average, divide or scan prices itself."""
    source = Path(price_history.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    operators = {type(node.op).__name__ for node in ast.walk(tree) if isinstance(node, ast.BinOp)}
    assert not operators & {"Div", "Mult", "Pow", "MatMult"}, operators
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert not names & {"sum", "statistics", "mean", "fmean"}
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert not imported & {"statistics", "math", "numpy", "talib"}
    from_modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert "trade_graph.application.indicators.library" in from_modules
    assert "_ARITHMETIC" not in source and "ROUND_HALF_EVEN" not in source  # the rounding lives with the library


def test_rounded_decimal_rule_is_deterministic_and_independent_of_the_ambient_context() -> None:
    assert SNAPSHOT_DIGITS == 12
    cases = {40.5: "40.5", 0.1 + 0.2: "0.3", 62348.70000000001: "62348.7", 0.0204081632653061: "0.0204081632653",
             0.05555555555555555: "0.0555555555556", 1e-7: "0.0000001", -0.5: "-0.5", 0.0: "0",
             123456789012345.0: "123456789012000", 1234.5678901234567: "1234.56789012"}
    with localcontext() as context:
        context.prec = 3
        context.rounding = "ROUND_UP"
        for number, text in cases.items():
            assert rounded_decimal(number) == Decimal(text), number
        assert canonical_decimal(rounded_decimal(-0.0)) == "0"  # no negative zero in stored text
    # Exact ties (the binary value is exactly half) round half-even at the twelfth significant digit.
    assert rounded_decimal(1234567890.125) == Decimal("1234567890.12")
    assert rounded_decimal(1234567890.375) == Decimal("1234567890.38")
    for bad in (float("nan"), float("inf"), -float("inf"), 1, "1", None, Decimal(1), True):
        with pytest.raises(ValidationFailure):
            rounded_decimal(bad)  # type: ignore[arg-type]


def test_a_library_failure_is_never_swallowed_into_a_value(tmp_path, monkeypatch) -> None:
    database = _database(tmp_path)
    store = HistoryStore(database)
    store.save([_candle(index) for index in range(1, 51)])
    from trade_graph.application.indicators.library import IndicatorComputationError

    def broken(*args, **kwargs):
        raise IndicatorComputationError("TA-Lib SMA failed: simulated")

    monkeypatch.setattr(price_history, "compute_raw", broken)
    with pytest.raises(IndicatorComputationError):
        store.snapshot("BTC/USD", AS_OF, ["sma_20"], source="synthetic")
    database.close()


# --- retired fields ----------------------------------------------------------------------------------------------


def test_retired_fields_are_unsupported_with_a_pointer_and_are_not_part_of_the_feature_table(tmp_path) -> None:
    database = _database(tmp_path)
    store = HistoryStore(database)
    store.save([_candle(index) for index in range(1, 51)])
    result = store.snapshot("BTC/USD", AS_OF, V2_ONLY_FIELDS, source="synthetic")
    assert result["values"] == {} and set(price_history._FEATURES).isdisjoint(V2_ONLY_FIELDS)
    for name in V2_ONLY_FIELDS:
        feature = result["features"][name]
        assert feature["status"] == "unsupported" and feature["candle_ids"] == []
        assert "retired" in feature["definition"].lower()
    # Asking for them does not widen the lookback or change what the other fields return.
    mixed = store.snapshot("BTC/USD", AS_OF, ["sma_20", *V2_ONLY_FIELDS], source="synthetic")
    alone = store.snapshot("BTC/USD", AS_OF, ["sma_20"], source="synthetic")
    assert mixed["values"] == alone["values"] and mixed["features"]["sma_20"] == alone["features"]["sma_20"]
    database.close()


def test_the_installed_strategy_templates_only_request_supported_fields() -> None:
    from trade_graph.paper_runtime import installed_artifacts
    from trade_graph.roles.strategies import TEMPLATES

    requested = set()
    for path, text in installed_artifacts().items():
        if path.startswith("strategies/templates/"):
            requested |= set(json.loads(text)["features"])
    for template in TEMPLATES.values():
        requested |= set(template.features)
    assert requested and requested <= set(price_history._FEATURES), requested - set(price_history._FEATURES)
    assert "highest_high_20" in requested and not requested & set(V2_ONLY_FIELDS)


def test_the_artifact_grammar_accepts_new_templates_and_still_accepts_retained_ones() -> None:
    from trade_graph.adapters.engineering.artifact_policy import validate
    from trade_graph.paper_runtime import installed_artifacts

    files = dict(installed_artifacts())
    assert validate(files) == []
    old = json.loads(files["strategies/templates/slow_trend_pullback.json"])
    old["features"] = ["sma_20", "sma_50", "pullback_from_high"]  # a template retained from a v2 installation
    files["strategies/templates/slow_trend_pullback.json"] = json.dumps(old)
    assert validate(files) == []


# --- retained v2 snapshots ---------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def stored_v2() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_is_three_real_v2_snapshots_with_their_original_hashes(stored_v2) -> None:
    assert set(stored_v2) == {"complete_history", "pending_tail", "gapped_and_insufficient"}
    statuses = {feature["status"] for document in stored_v2.values() for feature in document["features"].values()}
    assert statuses == {"ready", "stale", "gapped_history"}
    for name, document in stored_v2.items():
        assert document["feature_version"] == FEATURE_VERSION_V2, name
        body = {key: value for key, value in document.items() if key != "source_ref"}
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        assert document["source_ref"] == "hourly-features:" + hashlib.sha256(encoded).hexdigest(), name
    complete = stored_v2["complete_history"]
    assert sorted(complete["values"]) == sorted(V2_FIELDS)
    assert complete["values"]["pullback_from_high"] == "0.01960784313725490196078431372549019607843137254902"


def test_v2_snapshots_pass_through_the_context_compaction_unchanged(stored_v2) -> None:
    for name, document in stored_v2.items():
        before = copy.deepcopy(document)
        compact = _compact_history(document)
        assert document == before, "the reader must not modify the stored document"
        assert compact["values"] == document["values"] and compact["feature_version"] == FEATURE_VERSION_V2
        assert compact["provenance"]["feature_snapshot_ref"] == document["source_ref"]
        assert compact["provenance"]["retained_candle_count"] == len(document["candle_ids"])
        assert all("candle_ids" not in feature for feature in compact["features"].values())
        assert set(compact["features"]) == set(document["features"]), name


def test_v2_contexts_pass_through_the_department_context_filter(stored_v2) -> None:
    for document in stored_v2.values():
        context = {"market": {"BTC/USD": {"history": _compact_history(copy.deepcopy(document)),
                                          "features": dict(document["values"]), "fresh": True}}}
        assert public_context(context) == context


def test_the_evidence_reader_keeps_every_v2_field_name_and_value(stored_v2) -> None:
    assert set(V3_FIELDS + V2_ONLY_FIELDS) <= set(_MARKET_FEATURES)  # new names added, none removed
    for name, document in stored_v2.items():
        compact = _compact_history(copy.deepcopy(document))
        payload = {"as_of": document["as_of_utc"], "market": {"BTC/USD": {
            "history": compact, "features": dict(document["values"]), "fresh": True}}}
        retained = _retained_inputs(payload, None)["market"]["BTC/USD"]
        assert retained["features"] == document["values"], name
        history = retained["history"]
        assert history["feature_version"] == FEATURE_VERSION_V2 and history["values"] == document["values"]
        assert history["source_ref"] == document["source_ref"]
        assert set(history["features"]) == set(document["features"])
        for feature, original in document["features"].items():
            assert history["features"][feature]["status"] == original["status"]
            assert history["features"][feature]["definition"] == original["definition"]  # the v2 wording is kept


def test_a_v2_and_a_v3_snapshot_of_the_same_candles_differ_only_where_the_definition_changed(
        stored_v2, tmp_path) -> None:
    database = _database(tmp_path)
    store = HistoryStore(database)
    store.save([_candle(index) for index in range(1, 51)])
    v3 = store.snapshot("BTC/USD", AS_OF, [*V3_FIELDS, *V2_ONLY_FIELDS], source="synthetic")
    v2 = stored_v2["complete_history"]
    assert v3["feature_version"] != v2["feature_version"]
    for name in ("sma_20", "sma_50", "range_high", "range_low", "midpoint"):
        assert v3["values"][name] == v2["values"][name], name  # exactly representable: no digit changes
    assert v3["candle_ids"] == v2["candle_ids"] and v3["provenance"] == v2["provenance"]
    assert v3["latest_close"] == v2["latest_close"] and v3["recent_candles"] == v2["recent_candles"]
    for name in ("hourly_return", "four_hour_drift"):
        assert abs(Decimal(v3["values"][name]) - Decimal(v2["values"][name])) < Decimal("1e-12")
    assert utc_iso(END) == v3["latest_close_time_utc"]
    database.close()
