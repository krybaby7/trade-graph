"""The archive reader, importer and command can never reach the network, even through first-party imports."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
ENTRY_MODULES = (
    "trade_graph.adapters.market.kraken_archive",
    "trade_graph.application.import_candle_archive",
    "trade_graph.market_data_cli.archive",
)
NETWORK_LIBRARIES = {
    "httpx", "urllib", "urllib3", "socket", "requests", "http", "ftplib", "smtplib", "ssl", "aiohttp", "websockets",
    "telnetlib", "xmlrpc", "socketserver",
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


def _closure(start: tuple[str, ...]) -> dict[str, set[str]]:
    """Every first-party module reachable from ``start`` (with parent packages) mapped to what it imports."""
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


def test_the_entry_modules_exist() -> None:
    assert all(_file_for(module) is not None for module in ENTRY_MODULES)


def test_no_entry_module_imports_a_network_library_directly() -> None:
    for module in ENTRY_MODULES:
        found = {name for name in _imported_names(_file_for(module)) if name.split(".")[0] in NETWORK_LIBRARIES}
        assert not found, f"{module} imports {sorted(found)}"


def test_nothing_reachable_through_first_party_imports_uses_a_network_library() -> None:
    reachable = _closure(ENTRY_MODULES)
    assert len(reachable) > 5  # the walk really followed imports
    offenders = {module: sorted(name for name in names if name.split(".")[0] in NETWORK_LIBRARIES)
                 for module, names in reachable.items()}
    offenders = {module: names for module, names in offenders.items() if names}
    assert not offenders, offenders


def test_the_archive_modules_never_extract_or_download() -> None:
    for module in ENTRY_MODULES:
        text = _file_for(module).read_text(encoding="utf-8")
        for forbidden in (".extract(", ".extractall(", "urlopen", "urlretrieve", "tempfile", "subprocess"):
            assert forbidden not in text, f"{module} contains {forbidden}"
