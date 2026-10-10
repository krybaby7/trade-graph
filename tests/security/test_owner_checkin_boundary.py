"""Owner check-ins stay out of agents, the review export and financial state (spec §B.10, D108).

The store is non-financial owner data. Only the check-in module and its schema hold SQL
against it; only the dashboard app and the HQ read model may import the module.
"""

import ast
import inspect
import re
import textwrap
from pathlib import Path

from tests.integration.test_dashboard import _client

from trade_graph import runtime_evidence
from trade_graph.adapters.persistence.owner_checkin_schema import OWNER_CHECKIN_STATEMENTS
from trade_graph.api import controls, owner_checkins, review_export
from trade_graph.kernel import financial_checkpoint

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "src" / "trade_graph"
MODULE = "trade_graph.api.owner_checkins"
TABLE = "owner_checkins"
SQL = re.compile(r"\b(?:FROM|INTO|UPDATE|JOIN)\s+owner_checkins\b")
SQL_OWNERS = {PACKAGE / "api" / "owner_checkins.py", PACKAGE / "adapters" / "persistence" / "owner_checkin_schema.py"}


def _sources():
    for root in (PACKAGE, REPO / "migrations", REPO / "scripts"):
        yield from sorted(root.rglob("*.py"))


def _imported(path: Path, tree: ast.AST) -> set[str]:
    """Absolute module names a file imports, including relative and ``import_module`` forms."""
    names = set()
    package = list(path.relative_to(PACKAGE.parent).with_suffix("").parts[:-1])
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[:len(package) - node.level + 1] if node.level else []
            module = ".".join([*base, *([node.module] if node.module else [])])
            names.add(module)
            names.update(f"{module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            function = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if function in {"import_module", "__import__"} and isinstance(node.args[0].value, str):
                names.add(node.args[0].value)
    return names


def _imports_store(names: set[str]) -> bool:
    return any(name == MODULE or name.startswith(MODULE + ".") for name in names)


def _importers() -> set[Path]:
    return {path for path in PACKAGE.rglob("*.py")
            if _imports_store(_imported(path, ast.parse(path.read_text(encoding="utf-8"))))}


def test_sql_against_check_ins_lives_only_in_the_store_and_its_schema():
    users = {path for path in _sources() if SQL.search(path.read_text(encoding="utf-8"))}
    assert users == SQL_OWNERS


def test_only_the_dashboard_app_and_hq_read_model_import_the_store():
    importers = {path.relative_to(PACKAGE).as_posix() for path in _importers()}
    for path in importers:
        assert path == "api/app.py" or re.fullmatch(r"api/hq[a-z_]*\.py", path), path
    for forbidden in ("application/", "kernel/", "adapters/models/", "roles/", "orchestration/", "contracts/",
                      "domain/"):
        assert not any(path.startswith(forbidden) for path in importers)
    # The detector sees every import spelling.
    probe = PACKAGE / "application" / "probe.py"
    for source in ("import trade_graph.api.owner_checkins", "from trade_graph.api import owner_checkins",
                   "from trade_graph.api.owner_checkins import record", "from ..api import owner_checkins",
                   "from .. import api", "importlib.import_module('trade_graph.api.owner_checkins')"):
        assert _imports_store(_imported(probe, ast.parse(source))) is (source != "from .. import api"), source


def _checkpoint_tables() -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(financial_checkpoint.FinancialHistoryCheckpoint._scan)))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id == "queries":
                names.update(key.value for key in node.value.keys)
                names.update(value.elts[0].value for value in node.value.values)
            elif node.targets[0].id == "immutable":
                names.update(item.value for item in node.value.elts)
    assert {"ledger", "ledger_events", "owner_expense_evidence"} <= names  # the map was found
    return names


def test_not_exported_checkpointed_or_an_engineer_change_class():
    assert TABLE not in review_export.DEPLOYMENT_TABLES
    assert TABLE not in review_export.PORTFOLIO_TABLES
    assert TABLE not in review_export.SCOPES
    assert TABLE not in inspect.getsource(review_export)
    assert TABLE not in _checkpoint_tables()
    for path in (PACKAGE / "kernel").rglob("*.py"):
        assert "owner_checkin" not in path.read_text(encoding="utf-8"), path
    assert TABLE not in runtime_evidence.TABLES
    assert not any("checkin" in change_class for change_class in controls._CHANGE_CLASSES)


def test_role_reads_and_review_export_never_carry_check_ins(tmp_path):
    client, database, _, owner, _, leader = _client(tmp_path)
    runtime = client.app.state.runtime
    with database.immediate() as connection:
        if not owner_checkins.available(runtime):
            for statement in OWNER_CHECKIN_STATEMENTS:
                connection.execute(statement)
    owner_checkins.record(runtime, {"request_id": "plan", "checkin_id": "sentinel-checkin", "action": "plan",
                                    "planned_at": "2026-01-01T12:00:00Z", "prompt_kind": "custom",
                                    "title": "SENTINEL title"})
    owner_checkins.record(runtime, {"request_id": "done", "checkin_id": "sentinel-checkin", "action": "done",
                                    "note": "SENTINEL note"})
    for token in (owner, leader):
        for path in ("/api/v1/review-export", "/api/v1/overview", "/api/v1/events", "/api/v1/tasks"):
            response = client.get(path, headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 200, path
            for marker in ("SENTINEL", "sentinel-checkin", TABLE):
                assert marker not in response.text, (path, marker)
