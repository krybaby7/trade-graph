"""0023 signed stream commitments survive the additive 0024 owner expense upgrade.

The legacy fixture reconstructs the immediately preceding scanner by removing
only the two owner-expense additions, and runs the real old-schema preparation,
fill and authenticated transition writers. No checkpoint or witness is edited.
"""

import ast
import inspect
import json
import textwrap
from dataclasses import asdict, replace
from datetime import timedelta

import pytest
from tests.integration.test_financial_transition import facts, transition_stack
from tests.integration.test_protected_financial_runtime import KEY

from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.adapters.persistence import migrate
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import StaleState
from trade_graph.kernel import financial_checkpoint
from trade_graph.kernel.financial_service import ProtectedFinancialService
from trade_graph.kernel.financial_transition import FinancialManifestTransition


def _scanner_0023():
    tree = ast.parse(textwrap.dedent(inspect.getsource(financial_checkpoint.FinancialHistoryCheckpoint._scan)))
    removed = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if node.targets[0].id == "queries":
            for index, key in enumerate(node.value.keys):
                if isinstance(key, ast.Constant) and key.value == "owner_expense_evidence":
                    del node.value.keys[index]
                    del node.value.values[index]
                    removed += 1
                    break
        elif node.targets[0].id == "immutable":
            for index, item in enumerate(node.value.elts):
                if isinstance(item, ast.Constant) and item.value == "owner_expense_evidence":
                    del node.value.elts[index]
                    removed += 1
                    break
    assert removed == 2, "the preceding 0023 scanner differed by exactly these two additions"
    namespace = dict(vars(financial_checkpoint))
    exec(compile(ast.fix_missing_locations(tree), "<synthetic-schema-0023-scanner>", "exec"), namespace)
    return namespace["_scan"]


def _legacy_installation(tmp_path, monkeypatch):
    with monkeypatch.context() as legacy:
        legacy.setattr(migrate, "STATEMENTS", [(version, statements) for version, statements in migrate.STATEMENTS
                                              if version <= "0023"])
        legacy.setattr(financial_checkpoint.FinancialHistoryCheckpoint, "_scan", _scanner_0023())
        db, clock, ledger, execution, portfolio, first, second = transition_stack(tmp_path)
        op = FinancialManifestTransition(second)
        approval = op.inspect(source_manifests=[asdict(first.financial.manifest)], operation_id="legacy-only-upgrade",
                              expires_at=utc_iso(clock.now() + timedelta(minutes=10)))
        assert op.apply(approval)["status"] == "APPLIED"
        assert db.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == "0023"
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='owner_expense_evidence'").fetchone()
        checkpoints = [dict(row) for row in db.execute("SELECT * FROM protected_financial_checkpoints ORDER BY rowid")]
        assert checkpoints
        assert all("owner_expense_evidence" not in json.loads(row["payload_json"])["tables"] for row in checkpoints)
        source, witness, prior_facts = second.manifest, second.history.path.read_bytes(), facts(db)
        identity, path = db.file_identity(), db.path
        db.close()
    return path, clock, portfolio, source, witness, prior_facts, checkpoints, identity


@pytest.mark.parametrize("attack", [False, True], ids=["preserve-originals", "reject-old-prefix-edit"])
def test_schema_0023_checkpoint_chain_upgrades_to_0024_without_resealing(tmp_path, monkeypatch, attack):
    path, clock, portfolio, source, original_witness, prior_facts, checkpoints, identity = _legacy_installation(
        tmp_path, monkeypatch)
    db = Database(path)
    try:
        assert db.file_identity() == identity
        assert db.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == "0024"
        assert db.execute("SELECT count(*) FROM owner_expense_evidence").fetchone()[0] == 0
        ledger = Ledger(db, clock)
        execution = Execution(db, ledger, clock, PaperBroker(db, clock))
        target = ProtectedFinancialService(db, clock, execution, manifest=replace(source, wall_seconds=4),
                                          capability_key=KEY)
        op = FinancialManifestTransition(target)
        assert target.history.path.read_bytes() == original_witness
        if attack:
            db.execute("UPDATE ledger_events SET payload_json='{}' WHERE sequence=1")
            with pytest.raises(StaleState):
                op.inspect(source_manifests=[asdict(source)], operation_id="schema-0024-upgrade",
                           expires_at=utc_iso(clock.now() + timedelta(minutes=10)))
            assert target.history.path.read_bytes() == original_witness
            assert db.execute("SELECT count(*) FROM protected_financial_transitions").fetchone()[0] == 1
            return
        approval = op.inspect(source_manifests=[asdict(source)], operation_id="schema-0024-upgrade",
                              expires_at=utc_iso(clock.now() + timedelta(minutes=10)))
        assert target.history.path.read_bytes() == original_witness
        assert op.apply(approval)["status"] == "APPLIED"
        assert op.apply(approval)["status"] == "APPLIED"
        assert facts(db) == prior_facts
        assert execution.profile(portfolio) == "MANAGE_ONLY"
        assert target.history.ready(portfolio)
        retained = [dict(row) for row in db.execute("SELECT * FROM protected_financial_checkpoints ORDER BY rowid")]
        assert retained[:len(checkpoints)] == checkpoints
        latest = json.loads(retained[-1]["payload_json"])
        assert latest["tables"]["owner_expense_evidence"]["rows"] == 0
        witness = json.loads(target.history.path.read_bytes())["payload"]
        old_witness = json.loads(original_witness)["payload"]
        assert all(witness["scopes"][scope] == value for scope, value in old_witness["scopes"].items())
        assert witness["transitions"][:len(old_witness["transitions"])] == old_witness["transitions"]
        assert len(witness["scopes"]) == 3
        assert len(witness["transitions"]) == 2
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        db.close()
