"""An installation's database from before W12-A opens, migrates to 0052 and keeps every old record readable.

The application writes a database at migration 0051 (the head before this package): a Leader news round with its
routed requests, the Trader's wakes and a day plan it installed. The same file then opens with 0052: the old wakes,
day plans and news rounds read as before (the day plan simply has no desk record: its market type and playbook
revision were not recorded then), and the desk's records start from there. Synthetic records only; no network, model,
key, paid call or real order.
"""

from __future__ import annotations

from tests.day_plan_support import plan
from tests.leader_news_support import LeaderSetup, full_round
from tests.trading_desk_support import playbook, stance
from tests.wake_support import run_all, run_one, scripted

from trade_graph.adapters.persistence import migrate
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.events.store import WakeStore
from trade_graph.application.leader_news.store import LeaderNewsStore
from trade_graph.application.trading_desk.store import DeskStore, read_day_plan_desk_records


def test_a_0051_database_migrates_and_its_old_wakes_day_plans_and_news_rounds_still_read(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(migrate, "STATEMENTS", [entry for entry in migrate.STATEMENTS if entry[0] != "0052"])
    setup = LeaderSetup(tmp_path, full_round(), departments=("leader", "research", "trader"))
    database, clock, portfolio = setup.database, setup.clock, setup.portfolio
    assert database.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == "0051"
    setup.engine.step([portfolio])
    run_one(setup.scheduler, setup.engine, {"leader": setup.rounds})
    answer = {"summary": "An old answer: one SOL plan.", "day_plans": [plan().model_dump(mode="json")],
              "stances": [stance("SOL/USD", "buy_dips", "none").model_dump(mode="json")]}
    run_all(setup.scheduler, setup.engine, {"research": scripted({"summary": "Synthetic."}),
                                            "trader": scripted(answer)}, [portfolio])
    before_wakes = [wake.model_dump(mode="json") for wake in WakeStore(database, clock).wakes(portfolio)]
    before_rounds = [item.model_dump(mode="json") for item in LeaderNewsStore(database, clock).rounds(portfolio)]
    plans = [dict(row) for row in database.execute("SELECT * FROM day_plans ORDER BY sequence").fetchall()]
    assert plans and before_rounds and any(wake["department"] == "trader" for wake in before_wakes)
    trader = [wake for wake in before_wakes if wake["department"] == "trader" and wake["outcome"]
              and wake["outcome"]["plans"]]
    # Before 0052 the engine said why it kept no stance, and installed the plan all the same (D280 item 5).
    assert trader[0]["outcome"]["plans"][0]["status"] == "installed"
    assert "state migration 0052" in trader[0]["outcome"]["refused"]
    path = database.path
    database.close()

    monkeypatch.undo()
    upgraded = Database(path)
    try:
        assert upgraded.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == "0052"
        assert [wake.model_dump(mode="json") for wake in WakeStore(upgraded, clock).wakes(portfolio)] == before_wakes
        after_rounds = [item.model_dump(mode="json") for item in LeaderNewsStore(upgraded, clock).rounds(portfolio)]
        assert after_rounds == before_rounds
        assert [dict(row) for row in upgraded.execute("SELECT * FROM day_plans ORDER BY sequence").fetchall()] == plans
        with upgraded.connect() as connection:
            assert read_day_plan_desk_records(connection, portfolio_id=portfolio) == {}  # not recorded then
        store = DeskStore(upgraded, clock)
        assert store.playbook(portfolio) is None and store.stances(portfolio) == []
        first = store.record_playbook(portfolio, playbook(), wake_id="w-after", task_id=None)
        assert first.revision == 1
        assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        upgraded.close()
