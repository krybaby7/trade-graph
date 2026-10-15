import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "src" / "trade_graph"
FORBIDDEN = (
    "openai", "anthropic", "langgraph", "sqlalchemy", "fastapi", "httpx",
    # T27: indicator libraries and their array types stay in application/indicators; money stays Decimal.
    "talib", "talipp", "pandas", "pandas_ta_classic", "numpy",
)
GUARDED = ("domain", "contracts", "kernel")
_IMPORT_ALL = """
import importlib
import pkgutil
import sys

for name in sys.argv[1:]:
    package = importlib.import_module("trade_graph." + name)
    for info in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
        importlib.import_module(info.name)
watched = ("talib", "talipp", "pandas", "pandas_ta_classic", "numpy")
print(",".join(sorted(module for module in watched if module in sys.modules)))
"""


def test_domain_contracts_and_kernel_import_no_sdk() -> None:
    for name in GUARDED:
        for path in (ROOT / name).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for banned in FORBIDDEN:
                assert f"import {banned}" not in text
                assert f"from {banned}" not in text


def test_importing_the_guarded_packages_loads_no_indicator_library() -> None:
    """The transitive imports of the financial kernel never reach TA-Lib, numpy or pandas."""
    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_ALL, *GUARDED], capture_output=True, text=True, check=False,
        cwd=ROOT.parents[1], timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == ""
