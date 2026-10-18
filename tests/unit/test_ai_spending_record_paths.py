"""What the deployed Engineer may not edit (W10-A, D261 and D264): the files that record every paid AI call.

``application/budget.py`` holds the reservations, receipts and allocations, and ``application/gateway.py`` the gateway
that reserves before each paid call and settles it afterwards. ``kernel/protected_budget.py`` builds the protected
budget origins on top of them, so every module it imports must be protected too, including the clock, typed failures
and Decimal helpers of ``domain/``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from trade_graph.kernel.authority import path_is_protected

ROOT = Path(__file__).resolve().parents[2]
PROTECTED_BUDGET = ROOT / "src" / "trade_graph" / "kernel" / "protected_budget.py"


@pytest.mark.parametrize("path", [
    "src/trade_graph/application/budget.py",  # reservations, receipts, allocations and the money rule's sums
    "src/trade_graph/application/gateway.py",  # reserve before every paid call, receipt or unresolved hold after it
    "src/trade_graph/domain/clock.py",  # the timestamps of every reservation, receipt and origin
    "src/trade_graph/domain/errors.py",  # the typed refusals protected code raises and catches
    "src/trade_graph/domain/money.py",  # the Decimal amounts written into reservations and receipts
    "src/trade_graph/domain/__init__.py",  # runs on every import of the package
    "src/trade_graph/kernel/protected_budget.py",  # the protected origins and route budgets (already under kernel/)
    "src/trade_graph/kernel/pricing.py",  # worst-case and actual cost from a dated card (already under kernel/)
    "src/trade_graph/application/perplexity_installation.py",  # the Perplexity route's budget (W8-A, D244)
    "src/trade_graph/application/news_installation.py",  # the news route's budget (W9-A, D259)
])
def test_the_files_that_record_ai_spending_are_protected_paths(path) -> None:
    assert path_is_protected(path), path


def _imported_modules(source: Path) -> set[str]:
    """Every ``trade_graph`` module the file names in an import statement, at module level or inside a function."""
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
            names += [f"{node.module}.{alias.name}" for alias in node.names
                      if (ROOT / "src" / Path(*f"{node.module}.{alias.name}".split("."))).with_suffix(".py").exists()]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            continue
        modules.update(name for name in names if name == "trade_graph" or name.startswith("trade_graph."))
    return modules


def _source_path(module: str) -> str:
    base = ROOT / "src" / Path(*module.split("."))
    path = base.with_suffix(".py") if base.with_suffix(".py").exists() else base / "__init__.py"
    assert path.exists(), module
    return path.relative_to(ROOT).as_posix()


def test_the_protected_budget_imports_only_protected_code() -> None:
    """Direct imports of ``kernel/protected_budget.py``. Package initialisers above them (docstrings, and the paper-mode
    constants of ``trade_graph/__init__.py``) are not counted; the protected package hash pins all source anyway."""
    modules = _imported_modules(PROTECTED_BUDGET)
    assert "trade_graph.application.budget" in modules  # the base class of every protected budget
    unprotected = sorted(path for path in map(_source_path, modules) if not path_is_protected(path))
    assert unprotected == []


def test_reading_and_reporting_ai_spending_stays_ordinary_code() -> None:
    assert not path_is_protected("src/trade_graph/application/ai_spending.py")
