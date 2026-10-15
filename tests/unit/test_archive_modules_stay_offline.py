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


# --- an allowlist, because a list of bad names always misses the next one (asyncio, ctypes, os.system, ...) -------

# Everything the three modules and their first-party imports use today. A new import fails the test until a person
# looks at it and adds it here; none of these opens a socket or starts a process.
ALLOWED_LIBRARIES = {
    "__future__", "argparse", "collections", "contextlib", "dataclasses", "datetime", "decimal", "functools",
    "hashlib", "json", "os", "pathlib", "pydantic", "re", "shlex", "sqlite3", "stat", "threading", "typing", "uuid",
    "zipfile", "zlib",
}
EXTRA_LIBRARIES = {
    "trade_graph.market_data_cli": {"importlib", "pkgutil", "types"},  # finds the command modules next to it
    "trade_graph.adapters.persistence.inode_locks": {"fcntl"},  # file locks on the candle database
}
# The os functions that start another program (a download tool would be one), plus ways to run text as code.
PROCESS_AND_CODE_NAMES = {
    "system", "popen", "startfile", "fork", "forkpty", "posix_spawn", "posix_spawnp",
    *(f"exec{suffix}" for suffix in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe")),
    *(f"spawn{suffix}" for suffix in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe")),
}
DYNAMIC_IMPORT_ATTRIBUTES = {"import_module"}
CODE_AS_TEXT_BUILTINS = {"__import__", "eval", "exec", "compile"}  # re.compile is an attribute, not this builtin
DISCOVERY_MODULES = {"trade_graph.market_data_cli"}  # the one place that imports modules by name


def _reachable() -> dict[str, set[str]]:
    reachable = _closure(ENTRY_MODULES)
    assert len(reachable) > 5  # the walk really followed imports
    return reachable


def test_only_allowed_libraries_are_imported_anywhere_in_the_archive_closure() -> None:
    offenders = {}
    for module, names in _reachable().items():
        used = {name.split(".")[0] for name in names if not name.startswith("trade_graph")}
        extra = used - ALLOWED_LIBRARIES - EXTRA_LIBRARIES.get(module, set())
        if extra:
            offenders[module] = sorted(extra)
    assert not offenders, offenders


def test_every_import_in_the_archive_closure_is_absolute_so_the_walk_sees_it() -> None:
    relative = {}
    for module in _reachable():
        tree = ast.parse(_file_for(module).read_text(encoding="utf-8"))
        if any(isinstance(node, ast.ImportFrom) and node.level for node in ast.walk(tree)):
            relative[module] = "relative import"
    assert not relative, relative


def _process_or_code_uses(path: Path, *, discovery: bool) -> list[str]:
    dynamic = set() if discovery else DYNAMIC_IMPORT_ATTRIBUTES
    builtins = set() if discovery else CODE_AS_TEXT_BUILTINS
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Attribute) and node.attr in PROCESS_AND_CODE_NAMES | dynamic:
            found.append(f"line {node.lineno}: .{node.attr}")
        elif isinstance(node, ast.Name) and node.id in PROCESS_AND_CODE_NAMES | builtins:
            found.append(f"line {node.lineno}: {node.id}")
        elif isinstance(node, ast.ImportFrom):
            found.extend(f"line {node.lineno}: from {node.module} import {alias.name}"
                         for alias in node.names if alias.name in PROCESS_AND_CODE_NAMES | dynamic)
    return found


def test_nothing_in_the_archive_closure_starts_a_program_or_runs_text_as_code() -> None:
    offenders = {}
    for module in _reachable():
        uses = _process_or_code_uses(_file_for(module), discovery=module in DISCOVERY_MODULES)
        if uses:
            offenders[module] = uses
    assert not offenders, offenders


def test_the_process_and_code_check_sees_the_forms_it_exists_to_catch(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    for text, expected in (
        ("import os\nos.system('curl x')\n", ".system"),
        ("from os import popen\n", "from os import popen"),
        ("import os as o\no.execvp('curl', [])\n", ".execvp"),
        ("__import__('socket')\n", "__import__"),
        ("import importlib\nimportlib.import_module('socket')\n", ".import_module"),
        ("eval('1')\n", "eval"),
    ):
        source.write_text(text, encoding="utf-8")
        assert any(expected in use for use in _process_or_code_uses(source, discovery=False)), text
    source.write_text("import re\nre.compile('x')\ncursor.execute('select 1')\n", encoding="utf-8")
    assert _process_or_code_uses(source, discovery=False) == []
