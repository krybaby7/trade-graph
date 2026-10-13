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

from trade_graph.application.indicators.library import lookback
from trade_graph.contracts.indicators import INDICATOR_MENU, SETTLE_TOLERANCE, IndicatorSpec

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
