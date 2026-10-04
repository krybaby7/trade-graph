"""Actual confined application effects and synthetic expand/contract compatibility."""

import asyncio
import json
import os
import sqlite3

import pytest
from tests.integration.test_protected_financial_runtime import ENTER, activate, stack

from trade_graph.adapters.engineering.plugin_artifacts import PluginStageStore, canonical_bytes, sha256
from trade_graph.adapters.engineering.plugin_runtime import authenticated_report, runtime_environment_sha256
from trade_graph.application import application_projection as projection
from trade_graph.application.application_projection import (
    OfflineApplicationProjection,
    ProjectionPolicy,
    projection_controller_sha256,
    retain_projection_corpus,
    secretary_snapshot,
)
from trade_graph.application.scheduler import Scheduler
from trade_graph.application.secretary import Secretary
from trade_graph.domain.errors import AuthorityDenied, StaleState, ValidationFailure

KEY = b"synthetic-offline-application-receipt-key-41"
V1 = """
def graph(context):
    return {"node":"render_projection", "payload":{"title":"Reports",
        "report_ids":[report["report_id"] for report in context["snapshot"]["reports"]]}}
"""
V2 = """
def graph(context):
    labels = {"research":"Research", "learning":"Learning", "optimisation":"Optimisation",
        "trader":"Trader", "engineer":"Engineering", "system":"System"}
    groups = []
    for report in context["snapshot"]["reports"]:
        label = "Incidents" if report["incident"] else labels[report["role"]]
        group = next((group for group in groups if group["label"] == label), None)
        if group is None:
            group = {"label":label, "report_ids":[]}
            groups.append(group)
        group["report_ids"].append(report["report_id"])
    return {"node":"render_projection", "payload":{"title":"Grouped reports", "groups":groups}}
"""


@pytest.fixture(scope="module")
def environment_pin():
    return runtime_environment_sha256()


def setup(tmp_path, environment_pin, *, maximum_records=128):
    db, clock, ledger, execution, broker, pid, runtime = stack(tmp_path)
    secretary = Secretary(execution, Scheduler(db, clock))
    for role, kind, summary, material in (
        ("research", "finding", "Public synthetic observation.", False),
        ("learning", "lesson", "Synthetic counterevidence retained.\nOutcome:\tInsufficient sample.", False),
        ("system", "incident", "Synthetic incident remains visible.", True),
    ):
        secretary.report(
            pid,
            role=role,
            kind=kind,
            summary=summary,
            evidence_refs=[f"evidence-{role}"],
            source_key=f"projection-{role}",
            material=material,
        )
    secretary.process(pid, route=False)
    snapshot = secretary_snapshot(secretary, pid)
    ids = [report["report_id"] for report in snapshot["reports"]]
    expected_v1 = {"title": "Reports", "report_ids": ids}
    expected_v2 = {
        "title": "Grouped reports",
        "groups": [
            {"label": "Research", "report_ids": ids[:1]},
            {"label": "Learning", "report_ids": ids[1:2]},
            {"label": "Incidents", "report_ids": ids[2:3]},
        ],
    }
    store = PluginStageStore(tmp_path / "projection-private")
    corpus = retain_projection_corpus(
        store, [{"snapshot": snapshot, "expected_v1": expected_v1, "expected_v2": expected_v2}]
    )
    policy = ProjectionPolicy(
        baseline_source_sha256=sha256(V1.encode()), corpus_sha256=corpus, maximum_records=maximum_records
    )
    arguments = dict(
        policy=policy,
        expected_policy_sha256=policy.sha256,
        expected_controller_sha256=projection_controller_sha256(),
        expected_environment_sha256=environment_pin,
        receipt_key=KEY,
    )
    controller = OfflineApplicationProjection(store, **arguments)
    return controller, store, arguments, snapshot, secretary, (db, clock, ledger, execution, broker, pid, runtime)


def validated(controller, source, version):
    build = controller.stage(source, version=version)
    receipt = controller.validate(build)
    assert controller.verify_validation(receipt, build=build)["status"] == "finite_projection_passed"
    return build, receipt


def baseline(controller, snapshot):
    build, receipt = validated(controller, V1, 1)
    controller.install_baseline(receipt)
    rendered = controller.render(snapshot)
    assert rendered["status"] == "RENDERED"
    return build, receipt, rendered


def activate_v2(controller, snapshot, source=V2):
    old, _, result = baseline(controller, snapshot)
    controller.expand()
    build, receipt = validated(controller, source, 2)
    controller.activate(receipt, expected_build=old, expected_generation=1)
    return old, build, receipt, result


def test_actual_secretary_projection_expand_restart_rollback_and_contract_clone(tmp_path, environment_pin):
    controller, store, arguments, snapshot, secretary, financial = setup(tmp_path, environment_pin)
    pid = financial[5]
    old, baseline_receipt, first = baseline(controller, snapshot)
    assert first["projection"]["title"] == "Reports"
    before = controller.read(version=1)
    controller.expand()
    assert controller.read(version=1) == before
    assert controller.read(version=2)[0]["projection"]["groups"][0]["label"] == "Reports"
    build, receipt = validated(controller, V2, 2)
    controller.activate(receipt, expected_build=old, expected_generation=1)
    result = controller.render_secretary(secretary, pid)
    assert result["projection"]["title"] == "Grouped reports"
    assert [group["label"] for group in result["projection"]["groups"]] == ["Research", "Learning", "Incidents"]
    assert controller.verify_render(result["receipt_sha256"], record_id=result["record_id"])["source_sha256"] == sha256(
        V2.encode()
    )
    assert controller.read(version=1)[-1]["projection"]["report_ids"] == first["projection"]["report_ids"]
    controller.close()
    restarted = OfflineApplicationProjection(store, **arguments)
    assert restarted.status()["active_build"] == build
    assert restarted.read(version=2)[-1]["projection"] == result["projection"]
    assert restarted.rollback(expected_build=build, expected_generation=2) == "ROLLED_BACK"
    assert restarted.status()["active_build"] == old
    restarted.render_secretary(secretary, pid)
    assert restarted.read(version=2)[-1]["projection"]["groups"] == [
        {"label": "Reports", "report_ids": first["projection"]["report_ids"]}
    ]
    assert len(restarted.read(version=1)) == 3, "source rollback retains subsequent application records"
    restarted.activate(receipt, expected_build=old, expected_generation=3)
    restarted.render(snapshot)
    original_rows, original_state = restarted.read(version=2), restarted.status()
    proof = restarted.contract_clone()
    contract = authenticated_report(store, proof, KEY)
    assert contract["rows_retained"] == 4 and contract["legacy_reader_refused"]
    assert contract["retired_releases_refused"] == 1
    assert contract["v2_render_receipt_sha256"]
    assert restarted.read(version=2) == original_rows and restarted.status() == original_state
    clone_path = next(store.root.glob("contract-rehearsal-*.sqlite"))
    with sqlite3.connect(clone_path) as clone:
        assert "report_ids_json" not in {row[1] for row in clone.execute("PRAGMA table_info(projection_records)")}
        assert clone.execute("SELECT COUNT(*) FROM projection_records").fetchone()[0] == 5
    assert restarted.verify_validation(baseline_receipt, build=old)["status"] == "finite_projection_passed"
    restarted.close()


def test_real_multiline_secretary_reports_are_sanitized_and_live_sources_refused(tmp_path, environment_pin):
    controller, _, _, snapshot, secretary, financial = setup(tmp_path, environment_pin)
    assert snapshot["reports"][1]["summary"] == "Synthetic counterevidence retained. Outcome: Insufficient sample."
    assert "\nOutcome:" in secretary.digest(financial[5])["reports"][1]["summary"]
    financial[0].execute("UPDATE portfolios SET mode='live' WHERE portfolio_id=?", (financial[5],))
    with pytest.raises(AuthorityDenied, match="paper"):
        secretary_snapshot(secretary, financial[5])
    controller.close()


@pytest.mark.parametrize(
    "attack",
    [
        'payload["report_ids"] = payload["report_ids"][:-1]',
        'payload["report_ids"][1] = payload["report_ids"][0]',
        'payload["report_ids"][0] = "outside-snapshot"',
        'payload["passed"] = True',
        'payload["title"] = "<script>enabled</script>"',
        'payload["title"] = "bad\\x1btext"',
    ],
)
def test_independent_parent_rejects_lost_reports_identity_self_attestation_and_markup(
    tmp_path, environment_pin, attack
):
    controller, store, _, _, _, _ = setup(tmp_path, environment_pin)
    source = V1.replace('return {"node":"render_projection", "payload":', "payload = ").replace(
        'context["snapshot"]["reports"]]}}',
        'context["snapshot"]["reports"]]}\n    '
        + attack
        + '\n    return {"node":"render_projection", "payload":payload}',
    )
    build = controller.stage(source, version=1)
    receipt = controller.validate(build)
    report = controller.verify_validation(receipt, build=build)
    assert report["status"] == "rejected"
    assert len(report["attempts"]) == 2
    assert all(attempt["exit_code"] == 0 for attempt in report["attempts"]), (
        "independent parent refuses real child output"
    )
    assert controller.status()["active_build"] is None
    assert authenticated_report(store, receipt, KEY)["production_authorization"] is False
    controller.close()


def test_real_seccomp_denies_financial_files_network_mutation_process_and_inherited_handles(tmp_path, environment_pin):
    controller, _, _, snapshot, _, financial = setup(tmp_path, environment_pin)
    db = financial[0]
    sentinel = tmp_path / "world-readable-owner-secret"
    sentinel.write_text("synthetic-owner-credential")
    sentinel.chmod(0o644)
    attack = f"""
    import os, socket
    assert os.geteuid() != 0
    actions = [lambda: open({str(db.path)!r}).read(), lambda: open({str(sentinel)!r}).read(),
        lambda: open({str(sentinel)!r}, "w").write("changed"), lambda: socket.socket(),
        lambda: os.fork(), lambda: os.read(8, 1)]
    for action in actions:
        try:
            action()
        except OSError:
            pass
        else:
            raise RuntimeError("escaped OS boundary")
"""
    source = V1.replace("    return ", attack + "    return ", 1)
    build, receipt = validated(controller, source, 1)
    assert controller.verify_validation(receipt, build=build)["attempts"][0]["exit_code"] == 0
    assert controller._evaluate(build, snapshot)[0]["title"] == "Reports"
    assert sentinel.read_text() == "synthetic-owner-credential"
    controller.close()


@pytest.mark.parametrize(
    "source",
    [
        "def graph(context):\n    while True: pass\n",
        'def graph(context):\n    return {"x":"z" * 20000}\n',
        "def graph(context):\n    data = bytearray(256 * 1024 * 1024)\n",
        'def graph(context):\n    context["request_id"]="forged"\n    return {}\n',
        'def graph(context):\n    return {"node":"withdraw", "payload":{}}\n',
    ],
)
def test_actual_process_failure_resources_and_protected_operations_remain_retained(tmp_path, environment_pin, source):
    controller, store, _, _, _, _ = setup(tmp_path, environment_pin)
    build = controller.stage(source, version=2)
    receipt = controller.validate(build)
    report = authenticated_report(store, receipt, KEY)
    assert report["status"] == "rejected" and len(report["attempts"]) == 2
    assert all("exit_code" in attempt for attempt in report["attempts"])
    assert controller.read(version=1) == []
    controller.close()


def failing_late():
    return V2.replace(
        "    labels =",
        '    if context["request_id"] == "f" * 48:\n'
        '        raise RuntimeError("finite evidence does not prove all future contexts")\n    labels =',
    )


def force_failure_nonce(monkeypatch):
    original = projection.secrets.token_hex
    monkeypatch.setattr(projection.secrets, "token_hex", lambda size: "f" * 48 if size == 24 else original(size))


def test_confined_runtime_failure_rolls_back_code_preserving_real_fills_and_management(
    tmp_path, environment_pin, monkeypatch
):
    controller, store, _, snapshot, _, financial = setup(tmp_path, environment_pin)
    db, _, ledger, execution, _, pid, runtime = financial
    activate(runtime, ENTER)
    entered = asyncio.run(runtime.cycle(pid, "BTC/USD"))
    intent = entered["response"]["intent_id"]
    quote = execution.latest_observation("BTC/USD", execution.now())
    execution.on_observation(quote.model_copy(update={"observation_id": "projection-fill"}))
    assert execution.intent_state(intent) == "FILLED"
    before = {
        table: [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        for table in ("ledger_events", "fills", "order_intents", "usage_receipts", "position_reservations")
    }
    old, candidate, _, _ = activate_v2(controller, snapshot, failing_late())
    force_failure_nonce(monkeypatch)
    result = controller.render(snapshot)
    assert result["status"] == "REJECTED" and result["recovery"] == "ROLLED_BACK"
    assert controller.status()["active_build"] == old
    failure = authenticated_report(store, result["receipt_sha256"], KEY)
    assert failure["build_sha256"] == candidate and failure["process"]["exit_code"] == 1
    assert asyncio.run(runtime.recover(pid))["status"] == "RECONCILED"
    for table, rows in before.items():
        assert [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")] == rows
    assert ledger.books(pid).cash_amount("USD") < 10000
    assert execution.owned_quantity(pid, "BTC") > 0
    assert len(controller.read(version=1)) == 1
    controller.close()


def test_delayed_failure_cannot_revert_newer_activation(tmp_path, environment_pin, monkeypatch):
    controller, _, _, snapshot, _, _ = setup(tmp_path, environment_pin)
    _, candidate, _, _ = activate_v2(controller, snapshot, failing_late())
    newer, newer_receipt = validated(controller, V2 + "\n# distinct compatible build\n", 2)
    original = controller._evaluate
    force_failure_nonce(monkeypatch)

    def evaluate(*args):
        try:
            return original(*args)
        except ValidationFailure:
            controller.activate(newer_receipt, expected_build=candidate, expected_generation=2)
            raise

    monkeypatch.setattr(controller, "_evaluate", evaluate)
    result = controller.render(snapshot)
    assert result["recovery"] == "NEWER_RELEASE_PRESERVED"
    assert controller.status()["active_build"] == newer
    controller.close()


@pytest.mark.parametrize(
    "column,value",
    [
        ("title", "Forged title"),
        ("report_ids_json", '["invented"]'),
        ("groups_json", '[{"label":"Incidents","report_ids":["invented"]}]'),
    ],
)
def test_every_durable_projection_is_authenticated_including_later_rows(tmp_path, environment_pin, column, value):
    controller, _, _, snapshot, _, _ = setup(tmp_path, environment_pin)
    _, candidate, _, _ = activate_v2(controller, snapshot)
    result = controller.render(snapshot)
    controller.db.execute(f"UPDATE projection_records SET {column}=? WHERE record_id=?", (value, result["record_id"]))
    with pytest.raises(AuthorityDenied, match="authenticated|receipt"):
        controller.read(version=2)
    with pytest.raises(AuthorityDenied, match="authenticated|receipt"):
        controller._has_effect(controller.status()["previous_build"])
    assert controller.rollback(expected_build=candidate, expected_generation=2) == "MANAGE_ONLY"
    assert controller.status()["status"] == "MANAGE_ONLY"
    controller.close()


def test_damaged_active_source_triggers_independent_pointer_recovery(tmp_path, environment_pin):
    controller, _, _, snapshot, _, _ = setup(tmp_path, environment_pin)
    old, candidate, _, _ = activate_v2(controller, snapshot)
    source_path = controller.store.root / "stages" / candidate / "source.py"
    source_path.chmod(0o600)
    source_path.write_text("def graph(context): raise RuntimeError('tampered')")
    source_path.chmod(0o400)
    result = controller.render(snapshot)
    assert result["status"] == "REJECTED" and result["recovery"] == "ROLLED_BACK"
    assert controller.status()["active_build"] == old
    assert "tampered" in source_path.read_text(), "recovery retains damaged evidence"
    controller.close()


def test_receipt_input_key_and_class_substitution_are_refused(tmp_path, environment_pin):
    controller, store, arguments, snapshot, _, _ = setup(tmp_path, environment_pin)
    old, receipt, result = baseline(controller, snapshot)
    other = controller.stage(V1 + "\n# new source\n", version=1)
    with pytest.raises(AuthorityDenied, match="substitution"):
        controller.verify_validation(receipt, build=other)
    with pytest.raises(AuthorityDenied, match="effect"):
        controller.verify_render(receipt, record_id=result["record_id"])
    changed = json.loads(canonical_bytes(snapshot))
    changed["reports"][0]["summary"] = "New unpinned observation."
    with pytest.raises(AuthorityDenied, match="pinned"):
        controller.render(changed)
    controller.close()
    with pytest.raises(AuthorityDenied, match="key"):
        OfflineApplicationProjection(store, **{**arguments, "receipt_key": b"x" * 32})
    from trade_graph.adapters.engineering.plugin_artifacts import PluginStageManifest

    restarted = OfflineApplicationProjection(store, **arguments)
    manifest, _ = restarted.load(old)
    with pytest.raises(ValueError):
        PluginStageManifest.model_validate(manifest.model_dump())
    restarted.close()


def test_repeated_actual_validation_retains_distinct_authenticated_attempts(tmp_path, environment_pin):
    controller, store, _, _, _, _ = setup(tmp_path, environment_pin)
    build = controller.stage(V1, version=1)
    first, second = controller.validate(build), controller.validate(build)
    assert first != second
    one, two = authenticated_report(store, first, KEY), authenticated_report(store, second, KEY)
    assert one["attempt_id"] != two["attempt_id"]
    assert one["attempts"] == two["attempts"]
    assert controller.verify_validation(first, build=build)["status"] == "finite_projection_passed"
    assert controller.verify_validation(second, build=build)["status"] == "finite_projection_passed"
    controller.close()


def test_stale_activation_schema_record_quota_and_forged_corpus_are_refused(tmp_path, environment_pin):
    controller, _, _, snapshot, _, _ = setup(tmp_path, environment_pin, maximum_records=1)
    old, _, _ = baseline(controller, snapshot)
    build, receipt = validated(controller, V2, 2)
    with pytest.raises(StaleState, match="schema"):
        controller.activate(receipt, expected_build=old, expected_generation=1)
    controller.expand()
    with pytest.raises(StaleState, match="generation"):
        controller.activate(receipt, expected_build=old, expected_generation=9)
    controller.activate(receipt, expected_build=old, expected_generation=1)
    with pytest.raises(ValidationFailure, match="quota"):
        controller.render(snapshot)
    assert controller.status()["active_build"] == build
    controller.close()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "financial"])
def test_sidecar_cannot_substitute_files_or_financial_database(tmp_path, environment_pin, kind):
    controller, store, arguments, _, _, financial = setup(tmp_path, environment_pin)
    original = controller.path
    controller.close()
    moved = store.root / "saved.sqlite"
    original.rename(moved)
    if kind == "symlink":
        original.symlink_to(moved)
    elif kind == "hardlink":
        os.link(moved, original)
    else:
        original.write_bytes(financial[0].path.read_bytes())
        original.chmod(0o600)
    with pytest.raises((AuthorityDenied, OSError)):
        OfflineApplicationProjection(store, **arguments)
