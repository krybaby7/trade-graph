"""The practice range and its commands can never reach the network or a model, even through first-party imports.

Only ``candles-okx-fetch`` (``market_data_cli/okx.py``) may use the network, through the public market transport;
replays, reports, studies and verification read stored candles only.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
ENTRY_MODULES = (
    "trade_graph.application.practice_range.replay",
    "trade_graph.application.practice_range.service",
    "trade_graph.application.practice_range.store",
    "trade_graph.application.practice_range.summary",
    "trade_graph.application.indicators.rolling",
    "trade_graph.kernel.trade_metrics",
    "trade_graph.contracts.practice_range",
    "trade_graph.market_data_cli.practice_range",
)
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


def test_nothing_reachable_from_the_practice_range_uses_a_network_or_model_library() -> None:
    seen: dict[str, set[str]] = {}
    queue = list(ENTRY_MODULES)
    while queue:
        module = queue.pop()
        if module in seen:
            continue
        path = _file_for(module)
        if path is None:
            continue
        seen[module] = _imported_names(path)
        queue.extend(name for name in seen[module] if name.startswith("trade_graph"))
        parts = module.split(".")
        queue.extend(".".join(parts[:index]) for index in range(1, len(parts)))
    assert all(module in seen for module in ENTRY_MODULES)
    assert len(seen) > 20  # the walk really followed imports
    offenders = {module: sorted(name for name in names if name.split(".")[0] in NETWORK_AND_MODEL_LIBRARIES)
                 for module, names in seen.items()}
    assert not {module: names for module, names in offenders.items() if names}


def test_the_metrics_stay_in_the_protected_kernel_without_array_libraries() -> None:
    names = _imported_names(_file_for("trade_graph.kernel.trade_metrics"))
    assert not {name.split(".")[0] for name in names} & {"numpy", "talib", "pandas", "float"}
    text = _file_for("trade_graph.kernel.trade_metrics").read_text(encoding="utf-8")
    assert "float(" not in text  # money and ratios stay Decimal
