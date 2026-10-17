"""The scorecard (T42) only reads records: it reaches no network or model library and is a protected path.

The scorecard decides what the paper result is said to be, so, like the kernel sums it uses, an agent must not be
able to edit it (AGENTS.md: acceptance gates and accounting are not agent-editable; D204).

The import walk stops at W2-C's spending read model (``application.ai_spending``), which takes the Jev spending
line's role and purpose names from the news alarm module and so loads, without calling, the Jev and feed adapters;
``tests/integration/test_scorecard.py`` computes the scorecard with every socket blocked to show nothing is called.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from trade_graph.kernel.authority import path_is_protected

SRC = Path(__file__).resolve().parents[2] / "src"
ENTRY_MODULES = (
    "trade_graph.application.scorecard",
    "trade_graph.market_data_cli.scorecard",
)
SPENDING_READ_MODEL = "trade_graph.application.ai_spending"
NETWORK_AND_MODEL_LIBRARIES = {
    "httpx", "urllib", "urllib3", "socket", "requests", "http", "ftplib", "smtplib", "ssl", "aiohttp", "websockets",
    "telnetlib", "xmlrpc", "socketserver", "openai", "anthropic", "langgraph", "subprocess",
}


def _file_for(module: str) -> Path | None:
    base = SRC.joinpath(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _imported_names(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_nothing_reachable_from_the_scorecard_uses_a_network_or_model_library() -> None:
    seen: dict[str, set[str]] = {}
    queue = list(ENTRY_MODULES)
    while queue:
        module = queue.pop()
        if module in seen or module.startswith(SPENDING_READ_MODEL):
            continue
        path = _file_for(module)
        if path is None:
            continue
        seen[module] = _imported_names(path)
        queue.extend(name for name in seen[module] if name.startswith("trade_graph"))
        parts = module.split(".")
        queue.extend(".".join(parts[:index]) for index in range(1, len(parts)))
    assert all(module in seen for module in ENTRY_MODULES)
    assert "trade_graph.kernel.trade_metrics" in seen and "trade_graph.application.ledger" in seen
    assert len(seen) > 15  # the walk really followed imports
    offenders = {module: sorted(name for name in names if name.split(".")[0] in NETWORK_AND_MODEL_LIBRARIES)
                 for module, names in seen.items()}
    assert not {module: names for module, names in offenders.items() if names}


def test_the_scorecard_keeps_money_in_decimals() -> None:
    text = _file_for("trade_graph.application.scorecard").read_text(encoding="utf-8")
    assert "float(" not in text


@pytest.mark.parametrize("path", [
    "src/trade_graph/application/scorecard.py", "src/trade_graph/kernel/trade_metrics.py",
])
def test_the_scorecard_and_its_sums_are_protected_paths(path) -> None:
    assert path_is_protected(path)


def test_the_spending_read_model_itself_imports_no_network_or_model_library() -> None:
    names = _imported_names(_file_for(SPENDING_READ_MODEL))
    assert not {name.split(".")[0] for name in names} & NETWORK_AND_MODEL_LIBRARIES
