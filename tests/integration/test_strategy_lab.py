"""The strategy lab's queue and worker (W11-B, C11; D280, D116, D165-D169, D290-D294).

The worker runs queued requests in order against the candle file (read only) and writes practice-range records into
the state database. The protected store keeps the test fair: tune on earlier days, confirm once on untouched later
days, every try listed. A refused request says why; an interrupted one is run again and never half recorded. The
account's own cost profile is always replayed. Synthetic candles (``synthetic_fixture``) and the synthetic day-plan
account; no network, model, key, paid call or order.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.day_plan_support import day_plan_account
from tests.strategy_lab_support import (
    CONFIRM,
    STRATEGY,
    STUDY_ID,
    TUNE,
    candle_file,
    confirm,
    register,
    statuses,
    study,
    submit,
    tune,
    worker_for,
)

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.practice_range.replay import replay
from trade_graph.application.practice_range.store import PracticeRangeStore
from trade_graph.application.strategy_lab.queue import LabQueue
from trade_graph.application.strategy_lab.starting import STARTING_FILES, starting_strategies
from trade_graph.application.strategy_lab.worker import StrategyLabWorker

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def lab(tmp_path):
    account = day_plan_account(tmp_path)
    market, candles = candle_file(tmp_path / "market_data.sqlite")
    yield account, candles
    market.close()


def _runs(account, phase: str | None = None) -> list:
    """The lab's runs (the day-plan account also holds one synthetic slow-trend-pullback report)."""
    return [run for run in PracticeRangeStore(account.database, account.clock).runs(phase=phase)
            if run.strategy_ref.startswith("cross-hundred@")]


def test_a_malformed_strategy_is_refused_at_once_and_the_rest_queue_in_order(lab) -> None:
    account, _candles = lab
    broken = register(sizing={"equity_fraction": "2"})
    applied = submit(account, [broken, register(), study(), tune(), confirm()])
    assert [(item.kind, item.status) for item in applied] == [
        ("register_strategy", "refused"), ("register_strategy", "queued"), ("create_study", "queued"),
        ("tune", "queued"), ("confirm", "queued")]
    assert "equity_fraction" in applied[0].reason and applied[0].request_id == "batch-1:0"
    queue = LabQueue(account.database, account.clock)
    assert queue.batch("batch-1").finished_at is None
    assert [record.request_id for record in queue.waiting(account.portfolio)] == [
        "batch-1:1", "batch-1:2", "batch-1:3", "batch-1:4"]
    assert submit(account, [register()]) == applied  # the same batch again changes nothing


def test_the_worker_runs_each_request_in_order_under_the_named_profiles_and_the_accounts_own(lab) -> None:
    account, candles = lab
    records: list = []
    submit(account, [register(), study(), tune(window=(TUNE[0], TUNE[0].replace(day=4))), tune(), confirm()])
    worker = worker_for(account, candles, records=records)
    assert [step["status"] for step in worker.run_until_idle()] == ["ran"] * 5 + ["idle"]
    assert statuses(account) == [("register_strategy", "done"), ("create_study", "done"), ("tune", "done"),
                                 ("tune", "done"), ("confirm", "done")]
    store = PracticeRangeStore(account.database, account.clock)
    assert store.strategy("cross-hundred@1") == STRATEGY
    assert store.study(STUDY_ID).confirm.start == CONFIRM[0]
    tuned, confirmed = _runs(account, "tune"), _runs(account, "confirm")
    # okx-eea-spot was asked for; the account's own (Kraken's starting tier, the paper broker's fees) is always added.
    assert sorted(run.cost_profile_ref for run in confirmed) == ["kraken-spot-starting-tier@1", "okx-eea-spot@1"]
    assert len(tuned) == 4 and {run.window.end.day for run in tuned} == {4, 8}
    assert all(run.report["evidence"]["labels"] == ["synthetic_fixture"] for run in tuned + confirmed)
    assert confirmed[0].report["selection"]["tune_runs"] == 4  # every try is listed in the confirmation
    assert confirmed[0].report["result"]["metrics"]["trades"] > 0  # the swinging series really trades
    queue = LabQueue(account.database, account.clock)
    (request,) = [record for record in queue.requests(batch_id="batch-1") if record.request.kind == "confirm"]
    assert sorted(item["run_id"] for item in request.result["runs"]) == sorted(run.run_id for run in confirmed)
    assert request.attempts == 1 and request.seconds is not None
    assert queue.batch("batch-1").finished_at is not None
    finished = [record for record in records if record["status"] in ("done", "refused", "failed")]
    assert [record["kind"] for record in finished] == ["register_strategy", "create_study", "tune", "tune", "confirm"]
    assert finished[-1]["queue"] == {"queued": 0, "running": 0} and "seconds" in finished[-1]
    assert account.database.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0  # never an order


def test_the_study_rules_refuse_with_reasons_and_record_nothing_for_a_refused_request(lab) -> None:
    account, candles = lab
    submit(account, [register(), register(version=2), study(), confirm(), tune()], batch_id="first")
    worker_for(account, candles).run_until_idle()
    before = len(_runs(account))
    submit(account, [
        confirm("cross-hundred@2"),  # the window is spent on version 1
        tune(window=CONFIRM),  # tuning on confirmation days
        study("cross-later", tune=(CONFIRM[0], CONFIRM[1]), confirm=(CONFIRM[1], CONFIRM[1].replace(day=20))),
        tune("cross-hundred@9"),  # never registered
        register(),  # the identical document again: done, nothing new
        register(title="A changed document under the same version"),
    ], batch_id="second")
    worker_for(account, candles).run_until_idle()
    queue = LabQueue(account.database, account.clock)
    ended = {record.position: record for record in queue.requests(batch_id="second")}
    assert [ended[n].status for n in range(6)] == ["refused", "refused", "refused", "refused", "done", "refused"]
    assert "spent on cross-hundred@1" in ended[0].reason
    assert "tuning stays inside the study's tuning window" in ended[1].reason
    assert "confirmation window" in ended[2].reason and "never reused for tuning" in ended[2].reason
    assert "no registered strategy version cross-hundred@9" in ended[3].reason
    assert ended[4].result["created"] is False
    assert "different content" in ended[5].reason
    assert len(_runs(account)) == before


def test_an_interrupted_request_is_run_again_after_a_restart_and_never_half_recorded(lab, tmp_path) -> None:
    account, candles = lab
    submit(account, [register(), study(), confirm(profiles=("all",))])

    class Crash(BaseException):
        """The process dies in the middle of a request (not an error the worker could catch)."""

    calls = []

    def crashing(store, request):
        calls.append(request.profile.ref)
        if len(calls) == 2:
            raise Crash
        return replay(store, request)

    worker = worker_for(account, candles, replay_fn=crashing)
    worker.step()
    worker.step()
    with pytest.raises(Crash):
        worker.step()
    queue = LabQueue(account.database, account.clock)
    (running,) = [record for record in queue.requests(batch_id="batch-1") if record.status == "running"]
    assert running.request.kind == "confirm" and running.attempts == 1
    assert _runs(account, "confirm") == []  # the first profile's report was never recorded alone
    account.database.close()

    reopened = Database(account.database.path)  # the restart
    restarted = StrategyLabWorker(reopened, account.clock, candles=candles)
    assert [step["status"] for step in restarted.run_until_idle()] == ["ran", "idle"]
    store = PracticeRangeStore(reopened, account.clock)
    assert len(store.runs(phase="confirm", strategy_ref="cross-hundred@1")) == 3  # each published profile, once
    (done,) = [record for record in LabQueue(reopened, account.clock).requests(batch_id="batch-1")
               if record.request.kind == "confirm"]
    assert done.status == "done" and done.attempts == 2
    reopened.close()


def test_a_clean_stop_puts_the_running_request_back_and_it_runs_after_a_resume(lab) -> None:
    account, candles = lab
    submit(account, [register(), study(), confirm(profiles=("all",))])
    holder: dict = {}

    def stopping(store, request):
        holder["worker"].request_stop()  # the service stops while the first profile replays
        return replay(store, request)

    worker = worker_for(account, candles, replay_fn=stopping)
    holder["worker"] = worker
    worker.step()
    worker.step()
    worker.resume()
    assert worker.step()["status"] == "stopped"
    assert statuses(account)[-1] == ("confirm", "queued") and _runs(account, "confirm") == []
    worker.replay_fn = replay
    worker.resume()
    worker.run_until_idle()
    assert statuses(account)[-1] == ("confirm", "done") and len(_runs(account, "confirm")) == 3


def test_without_a_candle_file_a_replay_is_refused_with_the_reason(lab) -> None:
    account, _candles = lab
    submit(account, [register(), study(), tune()])
    worker_for(account, None).run_until_idle()
    (refused,) = [record for record in LabQueue(account.database, account.clock).requests(batch_id="batch-1")
                  if record.status == "refused"]
    assert refused.request.kind == "tune" and "candle file" in refused.reason


def test_an_unexpected_error_fails_one_request_with_its_type_and_the_queue_goes_on(lab) -> None:
    account, candles = lab
    submit(account, [register(), study(), tune(), confirm()])

    def broken(store, request):
        if request.window.end == TUNE[1]:
            raise ZeroDivisionError("synthetic")
        return replay(store, request)

    worker_for(account, candles, replay_fn=broken).run_until_idle()
    assert statuses(account) == [("register_strategy", "done"), ("create_study", "done"), ("tune", "failed"),
                                 ("confirm", "done")]
    (failed,) = [record for record in LabQueue(account.database, account.clock).requests(batch_id="batch-1")
                 if record.status == "failed"]
    assert "ZeroDivisionError" in failed.reason


def test_first_start_registers_the_two_examples_only_when_no_strategy_is_registered(tmp_path) -> None:
    for name, path in STARTING_FILES.items():
        shipped = json.loads((ROOT / "strategies" / "practice" / name).read_text())
        assert json.loads(path.read_text()) == shipped, name  # the packaged copies are the repository's examples
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    account = day_plan_account(tmp_path / "one", report=False)
    records: list = []
    worker = worker_for(account, None, records=records)
    worker.step()
    store = PracticeRangeStore(account.database, account.clock)
    assert [item.ref for item in store.strategies()] == [strategy.ref for strategy in starting_strategies()]
    assert {item.ref for item in store.strategies()} == {"range-reversion@1", "slow-trend-pullback@1"}
    assert records[0]["status"] == "started" and records[0]["registered_starting"] == [
        strategy.ref for strategy in starting_strategies()]
    worker_for(account, None).step()  # a second start registers nothing new
    assert len(store.strategies()) == 2
    other = day_plan_account(tmp_path / "two")  # slow-trend-pullback@1 is already registered there
    worker_for(other, None).step()
    assert [item.ref for item in PracticeRangeStore(other.database, other.clock).strategies()] == [
        "slow-trend-pullback@1"]


def test_there_is_no_cap_on_the_queue(lab) -> None:
    account, candles = lab
    many = [register(version=number) for number in range(1, 61)]
    applied = submit(account, many)
    assert len(applied) == 60 and {item.status for item in applied} == {"queued"}
    worker_for(account, candles).run_until_idle()
    assert len(PracticeRangeStore(account.database, account.clock).strategies()) == 61
