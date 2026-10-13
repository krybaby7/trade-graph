"""Render the generated tables of docs/INDICATOR-MENU.md from the menu contract and the cross-check table.

    uv run python scripts/render_indicator_menu.py            # print the blocks
    uv run python scripts/render_indicator_menu.py --update   # rewrite them inside the document

The document keeps each table between ``<!-- BEGIN name -->`` and ``<!-- END name -->`` markers.
tests/unit/test_indicator_menu_doc.py fails when the document and the sources disagree.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from trade_graph.application.indicators.engine import plan_history
from trade_graph.application.indicators.library import lookback
from trade_graph.application.price_history import _FEATURES, _RETIRED, FEATURE_VERSION, FEATURE_VERSION_V2
from trade_graph.contracts.indicators import INDICATOR_MENU, SETTLE_TOLERANCE, IndicatorSpec, get_indicator

ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = ROOT / "docs" / "INDICATOR-MENU.md"
CROSS_CHECK = ROOT / "tests" / "fixtures" / "indicators" / "cross_check.json"
_MA_TYPE = {0: "simple average"}


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")


def _number(value: float) -> str:
    return f"{value:g}"


def _parameters(spec: IndicatorSpec) -> str:
    parts = [f"`{p.name}` {_number(p.default)} ({_number(p.minimum)} to {_number(p.maximum)})"
             for p in spec.parameters]
    for name, value in spec.fixed_parameters.items():
        label = f" ({_MA_TYPE[value]})" if name.endswith("matype") else ""
        parts.append(f"fixed `{name}`={_number(value)}{label}")
    for rule in spec.constraints:
        relation = "<" if rule.strict else "<="
        parts.append(f"rule: `{rule.smaller}` {relation} `{rule.larger}`")
    return "<br>".join(parts) if parts else "none"


def render_menu_table() -> str:
    rows = ["| Name | Group | Reads | Settings: default (range) | Outputs | Warm-up at defaults | Start-sensitive |",
            "|---|---|---|---|---|---|---|"]
    for spec in INDICATOR_MENU:
        outputs = ", ".join(f"`{o.name}`" for o in spec.outputs)
        count = lookback(spec.name, {})
        warmup = "none (a value from the first candle)" if count == 0 else f"{count} candle{'s' if count > 1 else ''}"
        rows.append(f"| **{spec.name}** (TA-Lib `{spec.talib_function}`) | {spec.group.replace('_', ' ')} | "
                    f"{', '.join(spec.inputs)} | {_cell(_parameters(spec))} | {outputs} | {warmup} | "
                    f"{'yes' if spec.recursive_seed else 'no'} |")
    return "\n".join(rows)


def render_settling_table() -> str:
    rows = ["| Name | Extra history beyond warm-up | At defaults | Why the start matters |", "|---|---|---|---|"]
    for spec in INDICATOR_MENU:
        if not spec.recursive_seed:
            continue
        if spec.settle is None:
            guidance, defaults = "none: it never forgets its start", "-"
        else:
            parameter = next(p for p in spec.parameters if p.name == spec.settle.parameter)
            guidance = f"{spec.settle.periods} x `{spec.settle.parameter}`"
            defaults = f"{spec.settle.periods * int(parameter.default)} candles"
        rows.append(f"| **{spec.name}** | {guidance} | {defaults} | {_cell(spec.seed_note or '')} |")
    return "\n".join(rows)


def render_history_table() -> str:
    rows = ["| Name | Warm-up (asked from TA-Lib) | Seeding history | Candles read for one value |",
            "|---|---|---|---|"]
    for spec in INDICATOR_MENU:
        plan = plan_history(spec.name, {}, 1)
        if spec.settle is not None:
            seeding = f"{spec.settle.periods} x `{spec.settle.parameter}` = {plan.seeding}"
        elif spec.recursive_seed:
            seeding = f"{plan.seeding} (fixed: it never forgets its start)"
        else:
            seeding = "none"
        rows.append(f"| **{spec.name}** | {plan.lookback} | {seeding} | {plan.span} |")
    return "\n".join(rows)


# What v2 computed by hand (words only, for the comparison table) and why each field changed.
_V2_WORDS = {
    "sma_20": "Mean of the last 20 closes, summed and divided in Decimal",
    "sma_50": "Mean of the last 50 closes, summed and divided in Decimal",
    "range_high": "Maximum high of the last 24 candles, scanned in Python",
    "range_low": "Minimum low of the last 24 candles, scanned in Python",
    "midpoint": "(range_high + range_low) / 2 in Decimal",
    "hourly_return": "Latest close / previous close - 1 in Decimal",
    "four_hour_drift": "Latest close / close four hours earlier - 1 in Decimal",
    "highest_high_20": "(new in v3)",
    "pullback_from_high": "(20-hour maximum high - latest close) / maximum high in Decimal",
    "pullback_depth": "Alias of pullback_from_high",
    "midpoint_distance": "(latest close - midpoint) / midpoint in Decimal",
}
_SAME = "Same window and meaning; computed by the library."
_V3_WHY = {
    "sma_20": _SAME, "sma_50": _SAME, "range_high": _SAME, "range_low": _SAME, "midpoint": _SAME,
    "hourly_return": "Same meaning; ROCP is the fractional change, so no percent conversion is needed.",
    "four_hour_drift": "Same meaning; ROCP over 4 hours.",
    "highest_high_20": "The 20-hour high that the pullback ratio was built on. With `latest_close` it gives the "
                       "same information without a hand-written ratio.",
    "pullback_from_high": "No library function gives this ratio, and a hand-written one is an in-house formula (D118). "
                          "Its ingredients are supplied instead.",
    "pullback_depth": "Same as pullback_from_high, of which it was an alias.",
    "midpoint_distance": "No library function gives this ratio (D118). Its ingredients are supplied instead.",
}


def render_hourly_table() -> str:
    rows = [f"| Field | {FEATURE_VERSION_V2} (hand-written) | {FEATURE_VERSION} | Why |", "|---|---|---|---|"]
    for name in [*_FEATURES, *_RETIRED]:
        if name in _FEATURES:
            feature = _FEATURES[name]
            parameters = ", ".join(f"{key} {value}" for key, value in feature.parameters.items())
            new = (f"TA-Lib `{get_indicator(feature.indicator).talib_function}` ({parameters}) over "
                   f"{feature.lookback} completed hourly candles")
        else:
            new = f"**retired**: read {_RETIRED[name]}"
        rows.append(f"| `{name}` | {_V2_WORDS[name]} | {_cell(new)} | {_V3_WHY[name]} |")
    return "\n".join(rows)


def _result(rule: dict) -> str:
    mode = rule["mode"]
    if mode == "not_covered":
        return "not compared"
    if mode == "match":
        applies = ", ".join(f"`{k}`={v}" for k, v in rule.get("applies_when", {}).items())
        bound = f" (within {_number(rule['tolerance'])})" if "tolerance" in rule else ""
        return (f"matches to rounding error{bound}, compared only when {applies}" if applies
                else f"matches to rounding error{bound}")
    if mode == "converges":
        return (f"differs early by up to {_number(rule['early_bound'])} (scaled); within "
                f"{_number(rule['settled_tolerance'])} after {rule['settled_periods']} x `{rule['parameter']}` "
                "candles past the warm-up")
    return (f"path dependent: agrees on at least {int(rule['min_agreement'] * 100)}% of candles, never disagrees "
            f"for more than {rule['max_disagreement_run']} in a row, never by more than "
            f"{_number(rule['max_relative_difference'])} of price")


def render_differences_table() -> str:
    table = json.loads(CROSS_CHECK.read_text(encoding="utf-8"))
    rows = ["| Library | Entry | Result | What differs and why | TradingView |", "|---|---|---|---|---|"]
    for library, body in table["libraries"].items():
        for entry, rule in body["entries"].items():
            rows.append(f"| {library.replace('_', '-')} | **{entry}** | {_cell(_result(rule))} | "
                        f"{_cell(rule['reason'])} | {_cell(rule.get('tradingview', '-'))} |")
    return "\n".join(rows)


def render_blocks() -> dict[str, str]:
    return {
        "menu-table": render_menu_table(),
        "settling-table": render_settling_table(),
        "differences-table": render_differences_table(),
        "history-table": render_history_table(),
        "hourly-table": render_hourly_table(),
    }


def block_pattern(name: str) -> re.Pattern[str]:
    """Group 1 and 3 are the markers; group 2 is the text between them (empty before the first update)."""
    return re.compile(rf"(<!-- BEGIN {re.escape(name)} -->)\n(.*?)\n?(<!-- END {re.escape(name)} -->)", re.DOTALL)


def update_document(path: Path = DOCUMENT) -> None:
    text = path.read_text(encoding="utf-8")
    for name, block in render_blocks().items():
        if not block_pattern(name).search(text):
            raise SystemExit(f"{path} has no BEGIN/END markers for {name}")
        text = block_pattern(name).sub(lambda match: f"{match.group(1)}\n{block}\n{match.group(3)}", text)
    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="rewrite the generated blocks in the document")
    arguments = parser.parse_args()
    if arguments.update:
        update_document()
        print(f"updated {DOCUMENT}")
        return
    for name, block in render_blocks().items():
        print(f"<!-- BEGIN {name} -->\n{block}\n<!-- END {name} -->\n")
    print(f"settle tolerance: {SETTLE_TOLERANCE}")


if __name__ == "__main__":
    main()
