"""A syntax-tree guard shared by the tests that keep indicator formulas out of a module.

The first guard banned a few operators and names, and a hand-written ``max(c.high for c in candles[-24:])`` passed it.
This one looks at what the arithmetic is done to: no binary operation, and no call of an aggregating builtin, may
touch a candle price or volume field. Delegation itself is proved by a test that replaces the library with a
sentinel (``test_every_feature_is_whatever_the_library_returns_for_its_window``); this guard only catches the
obvious ways back to an in-house formula.
"""

from __future__ import annotations

import ast
from pathlib import Path

PRICE_FIELDS = frozenset({"open", "high", "low", "close", "volume", "vwap"})
AGGREGATES = frozenset({"max", "min", "abs", "sorted", "sum", "round", "pow", "divmod"})


def _touches_price(node: ast.AST) -> bool:
    return any(isinstance(child, ast.Attribute) and child.attr in PRICE_FIELDS for child in ast.walk(node))


def arithmetic_on_prices(path: Path) -> list[str]:
    """Where a module does arithmetic or aggregation on a candle price or volume field, as 'line: source'."""
    source = path.read_text(encoding="utf-8")
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.BinOp, ast.AugAssign, ast.UnaryOp)) and _touches_price(node):
            found.append(node)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in AGGREGATES
              and any(_touches_price(argument) for argument in [*node.args, *(k.value for k in node.keywords)])):
            found.append(node)
    return [f"{node.lineno}: {ast.get_source_segment(source, node)}" for node in found]
