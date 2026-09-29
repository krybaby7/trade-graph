from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "src" / "trade_graph"
FORBIDDEN = ("openai", "anthropic", "langgraph", "sqlalchemy", "fastapi", "httpx")
GUARDED = ("domain", "contracts", "kernel")


def test_domain_contracts_and_kernel_import_no_sdk() -> None:
    for name in GUARDED:
        for path in (ROOT / name).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for banned in FORBIDDEN:
                assert f"import {banned}" not in text
                assert f"from {banned}" not in text
