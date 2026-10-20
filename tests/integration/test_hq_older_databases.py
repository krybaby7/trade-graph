"""HQ on a database from before the Trading desk's migrations (W13-A, C13): before 0052 (no playbook, stances or
day-plan desk records), before 0051 as well (no strategy lab) and before 0050 as well (no market-type labels), the
snapshot, the Story and the Trading desk room still render and validate, and reading any of them writes nothing.

Synthetic records on the HQ projection test's database (``test_hq_projection.env``); nothing is started or sent.
"""

from __future__ import annotations

import pytest
from tests.integration.test_hq_projection import env, run, snap  # noqa: F401 - env is the fixture
from tests.wake_log_support import dump_sha256

from trade_graph.api import hq, hq_desk
from trade_graph.api.hq_desk_contract import validate_desk

DESK = ("day_plan_desk_records", "desk_stances", "desk_playbook_revisions")
LAB = ("strategy_lab_requests", "strategy_lab_batches")
LABELS = ("market_type_labels",)


def _drop(env, tables) -> None:  # noqa: F811
    with env.db.immediate() as connection:
        for table in tables:
            connection.execute(f"DROP TABLE {table}")


@pytest.mark.parametrize("dropped", [DESK, DESK + LAB, DESK + LAB + LABELS], ids=["before-0052", "before-0051",
                                                                                   "before-0050"])
def test_an_older_database_still_renders_hq_the_story_and_the_room(env, dropped):  # noqa: F811
    run(env, error_type="strategy_lab:OperationalError")
    env.runtime.ledger._activity(env.pid, "strategy_lab", {"status": "done", "request_id": "wake:w:0",
                                                            "kind": "register_strategy", "brief": {}})
    _drop(env, dropped)
    data = snap(env)  # validates against the snapshot contract
    assert any(item["kind"] == "lab_result" for item in data["story"]["items"])
    page = hq.story_page(env.runtime, hq.Viewer("owner", "cookie", True), before=data["as_of"])
    assert "story" in page
    room = hq_desk.desk(env.runtime)
    validate_desk(room)
    assert room["playbook"]["state"] == "not_available" and room["stances"]["state"] == "not_available"
    if "strategy_lab_requests" in dropped:
        assert room["lab"]["state"] == "not_available"


def test_reading_hq_and_the_room_writes_nothing(env):  # noqa: F811
    run(env, error_type="strategy_lab:OperationalError")
    env.runtime.ledger._activity(env.pid, "strategy_lab", {"status": "failed", "request_id": "wake:w:1",
                                                            "kind": "tune", "brief": {}, "reason": "synthetic"})
    before = dump_sha256(env.db)
    for _ in range(2):
        data = snap(env)
        hq.story_page(env.runtime, hq.Viewer("owner", "cookie", True), before=data["as_of"])
        validate_desk(hq_desk.desk(env.runtime))
    assert dump_sha256(env.db) == before
