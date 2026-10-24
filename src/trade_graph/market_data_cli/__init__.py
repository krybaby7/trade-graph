"""Operator commands for the candle store.

Every sibling module (not starting with an underscore) defines ``register(sub)``, which adds its
subcommands to the parser, and ``run(args, parser) -> int | None``, which returns an exit code for
its own commands and ``None`` otherwise. Modules are discovered, so parallel work adds a module
instead of editing a shared list.
"""

from __future__ import annotations

import argparse
import importlib
import pkgutil
from types import ModuleType


def _modules() -> list[ModuleType]:
    return [importlib.import_module(f"{__name__}.{found.name}")
            for found in sorted(pkgutil.iter_modules(__path__), key=lambda item: item.name)
            if not found.name.startswith("_")]


def register_all(sub: argparse._SubParsersAction) -> None:
    for module in _modules():
        module.register(sub)


def dispatch(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int | None:
    for module in _modules():
        result = module.run(args, parser)
        if result is not None:
            return result
    return None
