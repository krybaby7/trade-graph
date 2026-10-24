"""T44 boundary: headlines and Jev answers are data. The news alarm cannot reach orders, budgets or policy."""

from __future__ import annotations

import ast
import re
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "src"
NEWS_MODULES = (
    "trade_graph.contracts.decisions",
    "trade_graph.contracts.news",
    "trade_graph.adapters.decisions.jev",
    "trade_graph.adapters.news.feeds",
    "trade_graph.application.news_questions",
    "trade_graph.application.news_store",
    "trade_graph.application.jev_checks",
    "trade_graph.application.news_shadow",
    "trade_graph.application.news_report",
)
# Execution, orders, brokers, the ledger, schedulers, departments and owner-policy writers.
FORBIDDEN = (
    "trade_graph.application.execution",
    "trade_graph.application.ledger",
    "trade_graph.application.scheduler",
    "trade_graph.application.worker",
    "trade_graph.application.trader",
    "trade_graph.application.trader_workflow",
    "trade_graph.application.leader",
    "trade_graph.application.leadership",
    "trade_graph.application.runtime_departments",
    "trade_graph.application.authority",
    "trade_graph.application.owner_commands",
    "trade_graph.application.activation",
    "trade_graph.application.paper_service",
    "trade_graph.application.live_service",
    "trade_graph.adapters.brokers",
    "trade_graph.kernel.financial_service",
    "trade_graph.kernel.authority",
    "trade_graph.kernel.books",
    "trade_graph.paper_runtime",
    "trade_graph.live_runtime",
)


def _path(module: str) -> Path:
    base = SOURCE / Path(*module.split("."))
    return base.with_suffix(".py") if base.with_suffix(".py").is_file() else base / "__init__.py"


def _imports(module: str) -> set[str]:
    tree = ast.parse(_path(module).read_text())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return {name for name in found if name.startswith("trade_graph")}


def _closure(start: tuple[str, ...]) -> set[str]:
    seen, pending = set(), list(start)
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        for imported in _imports(module):
            if _path(imported).is_file():
                pending.append(imported)
    return seen


def test_the_news_alarm_import_closure_never_reaches_execution_policy_or_departments():
    closure = _closure(NEWS_MODULES)
    reached = sorted(module for module in closure if module.startswith(FORBIDDEN))
    assert reached == []
    assert "trade_graph.application.budget" in closure  # receipts use the existing budget path


def test_news_modules_write_only_news_tables_and_leave_budget_writes_to_the_budget_gateway():
    pattern = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|REPLACE\s+INTO)\s+([a-z_]+)", re.IGNORECASE)
    for module in NEWS_MODULES:
        for statement, table in pattern.findall(_path(module).read_text()):
            assert table.startswith("news_"), (module, statement, table)


def test_the_decision_contracts_have_no_provider_sdk_or_network_import():
    for module in ("trade_graph.contracts.decisions", "trade_graph.contracts.news"):
        text = _path(module).read_text()
        assert not re.search(r"^\s*(import|from)\s+(httpx|openai|anthropic|requests|socket|http\.client|"
                             r"urllib\.request)\b", text, re.MULTILINE)


def test_jev_answers_are_never_turned_into_tool_calls_or_instructions():
    text = "\n".join(_path(module).read_text() for module in NEWS_MODULES)
    for forbidden in ("ToolRequest", "tool_requests", "AuthorizedOrderIntent", "install_policy", "install_mandate",
                      "submit_order", "configure("):
        assert forbidden not in text, forbidden
