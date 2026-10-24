"""Owner command tests use temporary paper state and synthetic HTTPS only."""

import asyncio
import base64
import importlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from tests.integration.test_kraken_live_adapter import ScriptedRest

from trade_graph.cli import main


def test_owner_onboarding_feature_is_available():
    assert importlib.util.find_spec("trade_graph.application.kraken_onboarding") is not None


@pytest.fixture
def onboarding():
    return importlib.import_module("trade_graph.application.kraken_onboarding")


@pytest.fixture
def state(tmp_path, capsys):
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    database = runtime / "paper.sqlite"
    assert main(["init", "--database", str(database)]) == 0
    capsys.readouterr()
    database.chmod(0o600)
    return database, tmp_path / "owner"


def _synthetic_factory(*, inspect=None, mutate=None, failed=False):
    from trade_graph.application.venue_conformance import KrakenReadOnlyConformance

    rest = ScriptedRest()
    rest.results["TradeVolume"] = {
        "fees": {"XXBTZUSD": {"fee": "0.2"}}, "fees_maker": {"XXBTZUSD": {"fee": "0.1"}},
    }

    def factory(authority, **kwargs):
        if inspect:
            inspect(authority, kwargs)
        if failed:
            raise RuntimeError("synthetic secret must remain private")

        def respond(request):
            if mutate:
                mutate()
            method = request.url.path.rsplit("/", 1)[-1]
            if request.method == "POST":
                values = parse_qs(request.content.decode())
                values.pop("nonce")
                parameters = {key: value[0] for key, value in values.items()}
            else:
                parameters = dict(request.url.params)
            return httpx.Response(200, json=rest(method, parameters))

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        collector = KrakenReadOnlyConformance(authority, **kwargs, client=client)

        class OwnedFixture:
            async def collect(self, directory):
                try:
                    return await collector.collect(directory)
                finally:
                    await client.aclose()

        return OwnedFixture()

    return factory


def _importer(runtime, capture, expected_scope, **kwargs):
    proof = capture.verify(now=kwargs["now"], maximum_age_seconds=kwargs["maximum_age_seconds"])
    assert proof.observation.scope == expected_scope
    assert runtime.portfolio_id == expected_scope.portfolio_id
    return {"run_id": "fixture-run", "status": "pending", "symbol": "BTC/USD",
            "stages": list(proof.observation.completed_stages), "pending": list(proof.pending),
            "transport_basis": proof.observation.transport_basis, "freshness": "fresh"}


def _collect(onboarding, state, **kwargs):
    database, owner = state
    return asyncio.run(onboarding._collect_owner_read_only(
        database, owner, api_key="synthetic-api-key",
        api_secret=base64.b64encode(b"synthetic credential only").decode(),
        authorization="AUTHORIZE BTC/USD READ ONLY", collector_factory=_synthetic_factory(),
        importer=_importer, **kwargs,
    ))


def test_signed_bounded_capture_retains_private_intent_without_financial_write(onboarding, state):
    database, owner = state
    before = database.read_bytes()
    inspected = []

    def inspect(authority, kwargs):
        grant, _ = authority.load(datetime.now(UTC))
        inspected.append(grant)
        assert grant.maximum_requests == 128
        assert grant.maximum_duration_seconds == 60
        assert grant.maximum_history_pages == 5
        assert (grant.not_before - grant.history_start_utc).total_seconds() == 7 * 86400
        assert (grant.expires_at - grant.not_before).total_seconds() <= 600
        assert authority.owner_key != kwargs["collector_key"]
        assert len(authority.owner_key) >= 32 and len(kwargs["collector_key"]) >= 32
        run = authority.path.parent
        assert (run / "intent.json").is_file()
        assert (run / "pin.json").is_file()
        assert oct((run / "intent.json").stat().st_mode & 0o777) == "0o600"
        assert oct((run / "pin.json").stat().st_mode & 0o777) == "0o600"

    result = asyncio.run(onboarding._collect_owner_read_only(
        database, owner, api_key="synthetic-api-key",
        api_secret=base64.b64encode(b"synthetic credential only").decode(),
        authorization="AUTHORIZE BTC/USD READ ONLY", collector_factory=_synthetic_factory(inspect=inspect),
        importer=_importer,
    ))
    assert result["transport_basis"] == "injected_transport"
    assert "protected_account_ledger_reconciliation_unverified" in result["pending"]
    assert inspected[0].scope.account_id == "owner-local-kraken"
    assert inspected[0].scope.deployment_id == "deployment"
    assert database.read_bytes() == before
    assert {item.name for item in owner.glob("*.key")} == {"owner-signing.key", "collector-verification.key"}
    for path in owner.rglob("*"):
        if path.is_file():
            assert path.stat().st_mode & 0o077 == 0
            assert b"synthetic-api-key" not in path.read_bytes()
            assert b"synthetic credential only" not in path.read_bytes()
    assert "owner-local-kraken" not in json.dumps(result)
    assert inspected[0].scope.policy_sha256 not in json.dumps(result)


@pytest.mark.parametrize("kind", ["public_db", "public_parent", "linked_db", "hardlink_db", "linked_parent"])
def test_unsafe_database_refused_before_credentials_or_storage(onboarding, state, kind, tmp_path, monkeypatch):
    database, owner = state
    if kind == "public_db":
        database.chmod(0o644)
    elif kind == "public_parent":
        database.parent.chmod(0o755)
    elif kind == "hardlink_db":
        os.link(database, tmp_path / "other.sqlite")
    elif kind == "linked_db":
        link = database.parent / "link.sqlite"
        link.symlink_to(database)
        database = link
    else:
        link = tmp_path / "linked"
        link.symlink_to(database.parent, target_is_directory=True)
        database = link / database.name
    monkeypatch.setattr(onboarding, "_prompt_credentials", lambda: pytest.fail("credentials prompted"))
    with pytest.raises((PermissionError, ValueError, OSError)):
        onboarding.run_kraken_read_only(database, owner)
    assert not owner.exists()


@pytest.mark.parametrize(
    "kind", ["inside_runtime", "inside_checkout", "public_owner", "linked_owner", "hardlinked_key"],
)
def test_unsafe_owner_storage_refused(onboarding, state, kind, tmp_path):
    database, owner = state
    if kind == "inside_runtime":
        owner = database.parent / "owner"
    elif kind == "inside_checkout":
        owner = Path.cwd() / "runtime" / "synthetic-owner-refused"
    elif kind == "public_owner":
        owner.mkdir(mode=0o755)
        owner.chmod(0o755)
    elif kind == "linked_owner":
        real = tmp_path / "real-owner"
        real.mkdir(mode=0o700)
        owner.symlink_to(real, target_is_directory=True)
    else:
        owner.mkdir(mode=0o700)
        key = owner / "owner-signing.key"
        key.write_bytes(b"x" * 32)
        key.chmod(0o600)
        os.link(key, tmp_path / "linked.key")
    with pytest.raises((PermissionError, ValueError, OSError)):
        _collect(onboarding, (database, owner))


def test_authorization_is_exact_and_precedes_external_effect(onboarding, state):
    database, owner = state
    with pytest.raises(PermissionError):
        asyncio.run(onboarding._collect_owner_read_only(
            database, owner, api_key="synthetic-key", api_secret="synthetic-secret",
            authorization="authorize btc/usd read only", collector_factory=lambda *a, **k: pytest.fail("dispatch"),
            importer=_importer,
        ))
    assert not owner.exists()


def test_failure_retains_signed_intent_with_fixed_private_error_category(onboarding, state):
    database, owner = state
    with pytest.raises(onboarding.OnboardingFailure):
        asyncio.run(onboarding._collect_owner_read_only(
            database, owner, api_key="synthetic-key", api_secret="synthetic-secret",
            authorization="AUTHORIZE BTC/USD READ ONLY", collector_factory=_synthetic_factory(failed=True),
            importer=_importer,
        ))
    intent = json.loads(next(owner.rglob("intent.json")).read_bytes())
    failure = json.loads(next(owner.rglob("failure.json")).read_bytes())
    assert intent["payload"]["action"] == "observe_read_only_venue_account"
    assert failure["payload"]["category"] == "collection_failed"
    assert "synthetic secret" not in json.dumps(failure)
    assert next(owner.rglob("pin.json")).exists()


def test_scope_change_refuses_import_and_retains_capture(onboarding, state):
    import sqlite3

    database, owner = state
    changed = False

    def mutate():
        nonlocal changed
        if not changed:
            changed = True
            with sqlite3.connect(database) as conn:
                conn.execute("UPDATE active_versions SET generation = generation + 1")

    with pytest.raises(onboarding.OnboardingFailure):
        asyncio.run(onboarding._collect_owner_read_only(
            database, owner, api_key="synthetic-key",
            api_secret=base64.b64encode(b"synthetic secret").decode(),
            authorization="AUTHORIZE BTC/USD READ ONLY", collector_factory=_synthetic_factory(mutate=mutate),
            importer=lambda *a, **k: pytest.fail("changed scope imported"),
        ))
    assert next(owner.rglob("observation.json")).exists()
    failure = json.loads(next(owner.rglob("failure.json")).read_bytes())
    assert failure["payload"]["category"] == "binding_changed"


def test_tty_required_and_warning_fails_closed(onboarding, monkeypatch):
    import getpass
    import warnings

    monkeypatch.setattr(onboarding.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(onboarding.getpass, "getpass", lambda *a, **k: pytest.fail("fallback attempted"))
    with pytest.raises(PermissionError):
        onboarding._prompt_credentials()
    monkeypatch.setattr(onboarding.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(onboarding.sys.stderr, "isatty", lambda: True)

    def fallback(*args, **kwargs):
        warnings.warn("fallback would echo synthetic secret", getpass.GetPassWarning)
        return "synthetic-key"

    monkeypatch.setattr(onboarding.getpass, "getpass", fallback)
    with pytest.raises(PermissionError):
        onboarding._prompt_credentials()


def test_owner_terminal_hidden_prompts_leave_no_credentials_in_output_or_storage(
    onboarding, state, monkeypatch, capsys,
):
    database, owner = state
    key = "synthetic-distinct-owner-api-key"
    secret = base64.b64encode(b"synthetic-distinct-owner-api-secret").decode()
    prompts = []
    values = iter([key, secret])
    monkeypatch.setattr(onboarding.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(onboarding.sys.stderr, "isatty", lambda: True)

    def hidden(prompt):
        prompts.append(prompt)
        return next(values)

    monkeypatch.setattr(onboarding.getpass, "getpass", hidden)
    monkeypatch.setattr("builtins.input", lambda prompt: "AUTHORIZE BTC/USD READ ONLY")
    monkeypatch.setattr(onboarding, "KrakenReadOnlyConformance", _synthetic_factory())
    importer_module = type("Bridge", (), {"import_observation": staticmethod(_importer)})
    monkeypatch.setitem(onboarding.sys.modules, "trade_graph.api.account_checks", importer_module)
    result = onboarding.run_kraken_read_only(database, owner)
    assert len(prompts) == 2 and all("hidden" in prompt for prompt in prompts)
    assert result["transport_basis"] == "injected_transport"
    output = capsys.readouterr()
    assert key not in output.out + output.err and secret not in output.out + output.err
    for file in owner.rglob("*"):
        if file.is_file():
            assert key.encode() not in file.read_bytes() and secret.encode() not in file.read_bytes()


def test_retained_original_capture_pin_precedes_failed_verification(onboarding, state):
    from trade_graph.application.venue_conformance import PinnedVenueObservation

    database, owner = state
    factory = _synthetic_factory()

    def tampered(authority, **kwargs):
        collector = factory(authority, **kwargs)

        class Tamper:
            async def collect(self, path):
                capture = await collector.collect(path)
                return PinnedVenueObservation(capture.path, "a" * 64, capture.collector_key)

        return Tamper()

    with pytest.raises(onboarding.OnboardingFailure):
        asyncio.run(onboarding._collect_owner_read_only(
            database, owner, api_key="synthetic-key", api_secret=base64.b64encode(b"synthetic secret").decode(),
            authorization="AUTHORIZE BTC/USD READ ONLY", collector_factory=tampered, importer=_importer,
        ))
    pin = json.loads(next(owner.rglob("capture-pin.json")).read_bytes())
    assert pin["payload"]["observation_sha256"] == "a" * 64


def test_existing_key_change_during_collection_refuses_import(onboarding, state):
    database, owner = state
    changed = False

    def mutate():
        nonlocal changed
        if not changed:
            changed = True
            (owner / "collector-verification.key").write_bytes(b"synthetic-changed-key".ljust(32, b"x"))

    with pytest.raises(onboarding.OnboardingFailure):
        asyncio.run(onboarding._collect_owner_read_only(
            database, owner, api_key="synthetic-key", api_secret=base64.b64encode(b"synthetic secret").decode(),
            authorization="AUTHORIZE BTC/USD READ ONLY", collector_factory=_synthetic_factory(mutate=mutate),
            importer=lambda *a, **k: pytest.fail("changed key imported"),
        ))
    assert next(owner.rglob("observation.json")).exists()
    failure = json.loads(next(owner.rglob("failure.json")).read_bytes())
    assert failure["payload"]["category"] == "binding_changed"


def test_read_only_onboarding_does_not_construct_database_or_migrate(onboarding, state, monkeypatch):
    monkeypatch.setattr("trade_graph.adapters.persistence.db.Database.__init__",
                        lambda *a, **k: pytest.fail("database writer constructed"))
    monkeypatch.setattr("trade_graph.adapters.persistence.migrate.apply_migrations",
                        lambda *a, **k: pytest.fail("migration attempted"))
    assert _collect(onboarding, state)["status"] == "pending"


@pytest.mark.parametrize("corrupt", ["artifact_bytes", "custom_deployment"])
def test_invalid_installed_runtime_identity_refused_before_prompt(onboarding, state, monkeypatch, corrupt):
    import sqlite3

    database, owner = state
    with sqlite3.connect(database) as connection:
        if corrupt == "artifact_bytes":
            connection.execute("UPDATE artifact_bundles SET files_json = '{}' ")
        else:
            connection.execute(
                "INSERT INTO deployment_budget VALUES ('custom','USD','0','0','0','0','0')"
            )
    monkeypatch.setattr(onboarding, "_prompt_credentials", lambda: pytest.fail("unsafe identity prompted"))
    with pytest.raises(Exception):
        onboarding.run_kraken_read_only(database, owner)
    assert not owner.exists()
