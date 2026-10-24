"""Only the backfill command can reach the network; the reconciliation path never can."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
NETWORK_LIBRARIES = {
    "httpx", "urllib", "urllib3", "socket", "requests", "http", "ftplib", "smtplib", "ssl", "aiohttp", "websockets",
    "telnetlib", "xmlrpc", "socketserver",
}
RECONCILE_ENTRY = ("trade_graph.application.reconcile_candles", "trade_graph.market_data_cli.reconcile")
BACKFILL_ENTRY = (
    "trade_graph.adapters.market.kraken_trades", "trade_graph.adapters.market.trades_rate_limit",
    "trade_graph.application.trade_aggregation", "trade_graph.application.backfill_trades",
    "trade_graph.application.backfill_model", "trade_graph.application.backfill_report",
)
PUBLIC_TRANSPORT = "trade_graph.adapters.market.public"  # imports httpx lazily, inside the one function that sends


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


def _closure(start: tuple[str, ...]) -> dict[str, set[str]]:
    seen: dict[str, set[str]] = {}
    queue = list(start)
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
    return seen


def _network_users(start: tuple[str, ...]) -> set[str]:
    return {module for module, names in _closure(start).items()
            if any(name.split(".")[0] in NETWORK_LIBRARIES for name in names)}


def test_the_entry_modules_exist() -> None:
    assert all(_file_for(module) is not None for module in RECONCILE_ENTRY + BACKFILL_ENTRY)


def test_nothing_reachable_from_reconciliation_uses_a_network_library() -> None:
    assert len(_closure(RECONCILE_ENTRY)) > 5
    assert _network_users(RECONCILE_ENTRY) == set()


def test_no_backfill_module_imports_a_network_library_directly() -> None:
    for module in BACKFILL_ENTRY:
        found = {name for name in _imported_names(_file_for(module)) if name.split(".")[0] in NETWORK_LIBRARIES}
        assert not found, f"{module} imports {sorted(found)}"


def test_the_only_network_import_behind_the_backfill_is_the_public_transport_class() -> None:
    assert _network_users(BACKFILL_ENTRY) <= {PUBLIC_TRANSPORT}
    public = (SRC / "trade_graph" / "adapters" / "market" / "public.py").read_text(encoding="utf-8")
    assert "        import httpx\n" in public and "\nimport httpx" not in public  # imported inside get_text only
