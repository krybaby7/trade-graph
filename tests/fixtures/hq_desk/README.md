# Trading desk room fixture (synthetic)

`desk_week.json` is HQ's Trading desk room (`GET /api/v1/hq/desk`, `trade-graph.hq.desk.v1`, W13-A) built by the real
projection (`src/trade_graph/api/hq_desk.py`) from the scripted week of `tests/hq_desk_support.py`: W12-A's scripted
Trading desk week with SOL's dip buys filled. Every candle, label, headline, Jev answer, Leader round, desk answer,
fill and report in it is synthetic (`synthetic_fixture`); no exchange, provider or network was used, and it is not a
real paper run. `tests/js/hq_desk.test.mjs` lays it out; `tests/integration/test_hq_desk.py` validates it against the
contract and checks it has the same sections and states as a fresh build. If the room's document changes on purpose,
rebuild it from `desk_week` and check the difference by eye.
