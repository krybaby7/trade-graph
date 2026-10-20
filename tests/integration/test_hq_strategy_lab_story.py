"""HQ names the strategy lab in plain words (W13-A, C13; D303; W11-B's open items).

- A failed lab step of the service (``strategy_lab:<error>`` from ``paper_service._run_lab``) is named "the strategy
  lab's last step failed" in "Right now" and "Needs you", not "a routine step is failing".
- The Story lists the lab's finished requests (its ``strategy_lab`` activities): requests that end within 15 minutes
  of each other are one item with their count and outcomes, under the Trading desk; a lab that couldn't start says so
  for attention. A start record alone is not a story.

Synthetic records on the HQ projection test's database (``test_hq_projection.env``); nothing is started or sent.
"""

from __future__ import annotations

from tests.integration.test_hq_projection import OWNER, env, run, snap  # noqa: F401 - env is the fixture

from trade_graph.api import hq_copy
from trade_graph.api import hq_contract as contract
from trade_graph.api.hq_state import PROBLEM_PREFIXES


def _plain(text) -> str:
    return hq_copy.plain(text)


def test_the_strategy_lab_prefix_has_its_own_plain_words() -> None:
    assert "strategy_lab:" in contract.PROBLEM_PREFIXES and contract.PROBLEM_PREFIXES[-1] == "other"
    assert "strategy_lab:" in PROBLEM_PREFIXES
    what, consequence = hq_copy.problem_prefix("strategy_lab:", live=False)
    assert "strategy lab" in what and "trading, protection and your records are not affected" in consequence.lower()
    assert "practice" not in (what + consequence).lower()  # a live account can use the same words


def test_a_failed_lab_step_is_named_in_right_now_and_needs_you(env):  # noqa: F811
    run(env, error_type="strategy_lab:OperationalError")
    data = snap(env)
    assert data["now"]["code"] == "routine_failing"
    assert "strategy lab" in _plain(data["now"]["headline"])
    card = next(item for item in data["attention"] if item["id"].startswith("routine_failing"))
    assert "strategy lab" in _plain(card["title"]).lower()
    assert "routine step" not in _plain(card["title"])


def _lab(env, status: str, *, minutes: float, request: str, kind: str = "tune", reason: str | None = None) -> None:
    env.runtime.clock.advance(minutes * 60)
    payload = {"status": status, "request_id": request, "kind": kind, "family": "dip-rebound",
               "brief": {"kind": kind, "strategy_ref": "dip-rebound@1", "symbol": "SOL/USD"}, "attempt": 1,
               "reason": reason, "runs": 3 if status == "done" and kind in ("tune", "confirm") else 0,
               "seconds": "1.5", "queue": {"queued": 0, "running": 0}, "note": f"Synthetic lab {status}."}
    env.runtime.ledger._activity(env.pid, "strategy_lab", payload)


def test_the_story_lists_the_labs_finished_requests_under_the_trading_desk(env):  # noqa: F811
    env.runtime.ledger._activity(env.pid, "strategy_lab", {"status": "started", "registered_starting": [],
                                                            "interrupted": 0, "queue": {}, "note": "started"})
    _lab(env, "done", minutes=1, request="wake:w1:0", kind="register_strategy")
    _lab(env, "done", minutes=2, request="wake:w1:1", kind="create_study")
    _lab(env, "done", minutes=5, request="wake:w1:2")
    _lab(env, "refused", minutes=3, request="wake:w1:3", kind="confirm",
         reason="the confirmation window was already spent")
    _lab(env, "done", minutes=120, request="wake:w2:0", kind="confirm")  # later: a story item of its own
    env.runtime.clock.advance(60)
    items = [item for item in snap(env)["story"]["items"] if item["kind"] == "lab_result"]
    assert [(item["count"], item["dept"]) for item in items] == [(None, "trader"), (4, "trader")]
    newest, first = items
    assert _plain(newest["headline"]) == "The strategy lab finished a confirmation of dip-rebound@1 on SOL/USD."
    assert _plain(first["headline"]) == "The strategy lab finished 4 requests: 3 done, 1 refused."
    assert first["until"] is not None and first["until"] > first["at"]
    assert "Trading desk" in _plain(first["consequence"])
    assert first["tone"] == "neutral" and newest["tone"] == "good"


def test_a_lab_that_could_not_start_or_failed_needs_a_look(env):  # noqa: F811
    env.runtime.ledger._activity(env.pid, "strategy_lab", {"status": "not_ready", "reason": "no candle file"})
    _lab(env, "failed", minutes=30, request="wake:w3:0", reason="the request failed unexpectedly (KeyError)")
    env.runtime.clock.advance(60)
    items = [item for item in snap(env)["story"]["items"] if item["kind"] == "lab_result"]
    assert [item["tone"] for item in items] == ["attention", "attention"]
    assert [item["problem"] for item in items] == [True, True]
    assert "couldn't start" in _plain(items[1]["headline"])
    assert "1 failed" in _plain(items[0]["headline"]) or "failed" in _plain(items[0]["headline"])
