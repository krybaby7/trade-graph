"""HQ projections come only from persisted records (spec §B.5, §E.1), on isolated synthetic databases."""

import json
import re
import time
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from tests.integration.test_dashboard import _client

from trade_graph.api import hq, hq_facts, resources
from trade_graph.api.auth import issue_session, role_for_token
from trade_graph.api.hq_contract import DEPARTMENT_ORDER, ISO_MS_PATTERN, iso_ms, validate_snapshot
from trade_graph.api.security import redact
from trade_graph.application import service_controller
from trade_graph.domain.clock import utc_iso
from trade_graph.paper_runtime import PaperRuntimeConfig

OWNER = hq.Viewer("owner", "cookie", True)
READER = hq.Viewer("reader", "bearer", False)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hq"
LIVE_PID = 4242


@pytest.fixture
def env(tmp_path, monkeypatch):
    client, database, portfolio, token, csrf, leader = _client(tmp_path)
    runtime = client.app.state.runtime
    runtime.clock.advance(2 * 86400 + 3600)
    # The recorded worker PID is "alive" exactly when its birth identity matches.
    monkeypatch.setattr(service_controller, "process_identity", lambda pid: "birth" if pid == LIVE_PID else None)
    monkeypatch.setattr(resources, "observation",
                        lambda runtime: {"observed_at": utc_iso(runtime.clock.now()), "warnings": []})
    yield SimpleNamespace(client=client, runtime=runtime, db=database, pid=portfolio, token=token, csrf=csrf,
                          leader=leader)
    database.close()


def at(env, seconds: float = 0) -> str:
    return utc_iso(env.runtime.clock.now() + timedelta(seconds=seconds))


def insert(env, table, **values):
    env.db.execute(f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
                   tuple(values.values()))


def run(env, status="RUNNING", heartbeat: float | None = -4, *, run_id="r-1", requested=-3600, pid=LIVE_PID,
        finished=None, error_type=None):
    insert(env, "graph_service_runs", run_id=run_id, portfolio_id=env.pid, mode="paper", status=status, pid=pid,
           pid_start_ticks="birth", requested_at=at(env, requested),
           heartbeat_at=None if heartbeat is None else at(env, heartbeat),
           finished_at=None if finished is None else at(env, finished), error_type=error_type)


def schedule(env, role, next_due: float, *, interval=14400, last_due: float | None = -3600, name=None,
             schedule_id=None):
    insert(env, "schedules", schedule_id=schedule_id or f"s-{role}", portfolio_id=env.pid,
           name=name or f"{role}-review", last_due_at=None if last_due is None else at(env, last_due),
           next_due_at=at(env, next_due), missed_run_policy="coalesce", interval_seconds=interval)


def task(env, task_id, role, status, *, created: float = -60, objective=None, dedup_key=None, output=None,
         lease: float | None = None, due: float | None = None, attempts=0, result=None, root=None):
    insert(env, "tasks", task_id=task_id, root_task_id=root or task_id, portfolio_id=env.pid, role=role,
           objective=objective or f"{role}-review", status=status, due_at=None if due is None else at(env, due),
           priority=0, dedup_key=dedup_key, max_steps=1, max_attempts=3, attempts_used=attempts,
           lease_expires_at=None if lease is None else at(env, lease), input_json="{}",
           output_json=None if output is None else json.dumps(output), created_at=at(env, created))
    if result is not None:
        insert(env, "role_results", task_id=task_id, portfolio_id=env.pid, role=role,
               status=result.get("_status", "SUCCEEDED"), document_json=json.dumps(result),
               created_at=at(env, created + 60))


def reading(env, remaining=72, *, observed: float = -163, threshold=40, paused_reason=None):
    quota = {"source": "codex-app-server", "observed_at": at(env, observed),
             "windows": {"limit_1_primary": {"remaining_percent": remaining, "used_percent": 100 - remaining,
                                             "window_duration_mins": 10080},
                         "limit_1_secondary": None}}
    insert(env, "subscription_provider_state", provider="codex_subscription", ai_paused=int(bool(paused_reason)),
           reason=paused_reason or "", quota_json=json.dumps(quota), updated_at=at(env, observed))
    # Static policy is the only thing HQ may read here; the cached readiness verdict is stale on purpose.
    env.runtime.subscription_readonly_status = lambda: {
        "ai_available": False, "admitted": False, "reasons": ["stale cached readiness"],
        "subscription": {"quota_reserve_policy": {"enabled": True, "admitted": False, "blockers": ["stale"],
                                                  "admission_threshold_remaining_percent": threshold},
                         "quota_policy_routes": [{"admitted": False, "blockers": ["stale"]}]}}


def pause(env, profile, originator, *, since: float = -600, achieved="managing", reason="synthetic"):
    insert(env, "pause_states", portfolio_id=env.pid, profile=profile, originator=originator, reason=reason,
           scope="portfolio", requested_at=at(env, since), achieved=achieved, details_json="{}")


def finding(env, finding_id, *, created: float, expires: float, available: float | None = None):
    document = {"claim": "Synthetic momentum note.", "relevance": "source-analysis", "task_id": "t-research"}
    insert(env, "findings", finding_id=finding_id, portfolio_id=env.pid, document_json=json.dumps(document),
           source_hash=finding_id, available_at=at(env, created if available is None else available),
           expires_at=at(env, expires), created_at=at(env, created))


def decision(env, decision_id, action="no_action", *, created: float = -180, task_id=None, rationale=None):
    payload = {"action": action, "rationale": rationale or "Neither asset establishes the required uptrend.",
               "actions": [{"symbol": "BTC/USD"}, {"symbol": "ETH/USD"}]}
    insert(env, "decisions", decision_id=decision_id, portfolio_id=env.pid, action=action,
           payload_json=json.dumps(payload), mandate_revision="1", policy_revision="1", snapshot_id="snap",
           system_version_id="v1", created_at=at(env, created), task_id=task_id)


def snap(env, viewer=OWNER, **kwargs) -> dict:
    env.runtime._hq_money = None  # each scenario recomputes the 60 s money cache
    data = hq.snapshot(env.runtime, viewer, **kwargs)
    validate_snapshot(data)
    for value in _strings(data):
        if re.match(r"^\d{4}-\d{2}-\d{2}T", value):
            assert re.fullmatch(ISO_MS_PATTERN, value), value
    # Every projected string survives redaction; only the /journal route waits for its allowlist entry (B.11).
    changed = [(old, new) for old, new in zip(_strings(data), _strings(redact(data)), strict=True) if old != new]
    assert {old for old, _ in changed} <= {"/journal"}
    for item in data["attention"]:  # "since" is when something began, never a future moment (U3)
        assert item["since"] is None or item["since"] <= data["as_of"], item["id"]
    return data


def _strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, str):
        yield value


def text(segments) -> str:
    return "".join(segment.get("v") or segment.get("display") or "" for segment in segments or [])


def card(data, decision_id) -> dict:
    return next(item for item in data["decisions"] if item["id"] == decision_id)


def room(data, dept) -> dict:
    return next(item for item in data["departments"] if item["id"] == dept)


def codes(data) -> list[str]:
    return [item["id"].split(":", 1)[0] for item in data["attention"]]


def quiet(env, *, mode="paper"):
    run(env)
    reading(env)
    schedule(env, "trader", 4 * 3600)
    schedule(env, "research", 18 * 3600, interval=86400)
    schedule(env, "learning", 20 * 3600, interval=302400)
    schedule(env, "leader", 3 * 86400, interval=604800)
    task(env, "t-31", "trader", "SUCCEEDED", created=-240, dedup_key=f"schedule:s-trader:{at(env, -240)}",
         output={"decision_id": "d-123"}, attempts=1, result={"decision_id": "d-123"})
    decision(env, "d-123", task_id="t-31", created=-200)
    if mode == "live":
        env.db.execute("UPDATE portfolios SET mode='live' WHERE portfolio_id=?", (env.pid,))


# --- Spec §E.1 scenarios -----------------------------------------------------------------------------------


def test_empty_database_invents_nothing(env):
    data = snap(env)
    assert data["now"]["code"] == "no_run"
    assert data["now"]["primary_decision"] == "start_trading"
    assert all(item["light"] == "off" for item in data["departments"] if item["kind"] == "ai")
    assert [item["kind"] for item in data["story"]["items"]] == ["account_opened"]
    assert not [item for item in data["calendar"]["items"] if item["kind"] == "dept_turn"]
    assert data["calendar"]["notes"] and "once the team has been started" in text(data["calendar"]["notes"][0])
    assert set(data["money"]["counts"].values()) == {0}
    assert data["money"]["opening"]["native"] == [{"amount": "100", "currency": "EUR", "display": "€100.00"}]
    assert data["money"]["pays_for_itself"]["status"] == "unknown"
    assert data["money"]["pays_for_itself"]["net"] is None
    assert data["team"]["ai"]["state"] == "unknown"
    assert data["team"]["allowance"]["status"] == "unknown"
    assert data["quests"] == []
    assert codes(data)[0] == "team_off"


def test_quiet_running_is_calm_and_never_uses_cached_readiness(env, monkeypatch):
    def poison(*args, **kwargs):
        pytest.fail("HQ must not use the service controller or its cached readiness")

    monkeypatch.setattr(service_controller.ServiceController, "status", poison)
    monkeypatch.setattr(service_controller.ServiceController, "_readiness", poison)
    quiet(env)
    data = snap(env)
    assert data["now"]["code"] == "calm"
    assert data["now"]["tone"] == "calm"
    assert data["team"]["ai"]["state"] == "ok_last_reading"
    allowance = data["team"]["allowance"]
    assert allowance["status"] == "ok"
    assert allowance["stop_line_percent"] == 40
    assert allowance["windows"][0]["remaining_percent"] == 72
    assert allowance["unreported_windows"] == ["limit_1_secondary"]
    assert "isn't reported" in text(allowance["plain"])
    assert "the Trader looks at prices" in text(data["now"]["detail"])
    assert "72% (new AI work stops at 40%; checked 2 min ago)" in text(data["now"]["detail"])
    trader = room(data, "trader")
    assert trader["light"] == "resting"
    assert trader["last"]["outcome"] == "decided"
    assert trader["last"]["link"] == "/decisions/d-123"
    assert trader["last"]["quote"]["label"] == "In their words"
    assert trader["next"]["exact"] is True and trader["next"]["every_seconds"] == 14400
    assert data["calendar"]["up_next"][0].startswith("turn:s-trader:")
    estimated = [item for item in data["calendar"]["items"] if item["status"] == "estimated"]
    assert estimated and all(not item["exact"] for item in estimated)
    assert [item["kind"] for item in data["story"]["items"]][:2] == ["trader_decision", "team_started"]


def test_stale_heartbeat_is_silent_and_start_not_recommended(env):
    run(env, heartbeat=-91)
    data = snap(env)
    assert data["now"]["code"] == "silent"
    assert data["team"]["service"]["stale"] is True
    start = card(data, "start_trading")
    assert start["shown"] is True and start["available"] is False and start["recommended"] is False
    assert "only reconnect" in text(start["unavailable_because"])
    assert "the team is on" not in text(start["preview"]["when"])
    assert "isn't responding" in text(start["preview"]["when"])
    assert codes(data)[0] == "team_silent"
    assert all(item["light"] == "off" for item in data["departments"] if item["kind"] == "ai")


def test_system_pause_stuck_card_and_controls(env):
    run(env)
    pause(env, "MANAGE_ONLY", "system", reason="role unavailable; manage positions")
    task(env, "t-60", "trader", "FAILED", created=-660, output={"reason": "market feed blocks new exposure"})
    data = snap(env)
    assert data["now"]["code"] == "safety_stop"
    assert data["now"]["light"] == "stuck"
    assert "protect-only" in text(data["now"]["headline"])
    assert codes(data)[0] == "safety_stop"
    assert "After the Trader's turn failed" in text(data["attention"][0]["why"])
    pause_card, resume = card(data, "pause_ai"), card(data, "resume")
    assert pause_card["available"] is False and "safety stop" in text(pause_card["unavailable_because"])
    assert resume["shown"] is True and resume["available"] is False
    assert {"code": "system pause requires controller recovery", "definite": True,
            "text": [{"t": "text", "v": "Automatic safety stop"}]} in resume["facts"]["predicted_blockers"]
    story = [item["kind"] for item in data["story"]["items"]]
    assert "safety_stop" in story and "turn_problem" in story
    env.db.execute("UPDATE pause_states SET profile='NO_NEW_EXPOSURE' WHERE portfolio_id=?", (env.pid,))
    softer = snap(env)
    assert "departments keep working" in text(softer["team"]["pause"]["plain"])
    assert softer["team"]["pause"]["departments_paused"] is False


def _mark(env, asset, seconds_ago):
    insert(env, "valuation_marks", mark_id=f"m-{asset}-{seconds_ago}", portfolio_id=env.pid, asset=asset,
           quote_currency="USD", mark="62000", convention="mid", observed_at=at(env, -seconds_ago), stale=0,
           source="synthetic")


def test_old_run_feed_problem_does_not_colour_the_new_run(env):
    run(env, "STOPPED", heartbeat=-610, run_id="r-old", requested=-7200, finished=-600)
    env.runtime.ledger._activity(env.pid, "public_feed_health",
                                 {"run_id": "r-old", "status": "degraded", "symbol": "BTC/USD",
                                  "first_failed_at_utc": at(env, -1800), "error_type": "TimeoutError"},
                                 created_at=at(env, -1700))
    env.runtime.ledger._activity(env.pid, "service_degraded", {"run_id": "r-old", "failures": ["reconciliation:X"]},
                                 created_at=at(env, -1650))
    run(env, requested=-300)
    _mark(env, "BTC", 5)
    data = snap(env)
    market = room(data, "market")
    assert market["light"] == "ok" and text(market["now"]) == "Prices are arriving."
    finished = iso_ms(at(env, -600))
    spans = [item for item in data["story"]["items"] if item["kind"] in ("prices_problem", "routine_problem")]
    assert sorted(item["kind"] for item in spans) == ["prices_problem", "routine_problem"]
    for item in spans:
        assert item["until"] == finished
        assert "No longer tracked" in text(item["consequence"])
        assert "Recovered" not in text(item["consequence"])
    # The current run's own feed problem still turns the light.
    env.runtime.ledger._activity(env.pid, "public_feed_health",
                                 {"run_id": "r-1", "status": "degraded", "symbol": "BTC/USD",
                                  "first_failed_at_utc": at(env, -20), "error_type": "TimeoutError"},
                                 created_at=at(env, -10))
    again = snap(env)
    assert room(again, "market")["light"] == "attention"
    current = [item for item in again["story"]["items"] if item["kind"] == "prices_problem"
               and item["until"] is None]
    assert len(current) == 1 and "No longer tracked" not in text(current[0]["consequence"])


def test_safety_stop_cause_needs_evidence_close_in_time(env):
    run(env)
    pause(env, "MANAGE_ONLY", "system", reason="role unavailable; manage positions")
    task(env, "t-old", "trader", "FAILED", created=-600 - 7200, output={"reason": "market feed blocks new exposure"})
    data = snap(env)
    why = text(data["attention"][0]["why"])
    assert "a problem it recorded" in why and "Trader's turn failed" not in why


def test_maintenance_mode_stop_is_not_blamed_on_an_old_failure(env):
    run(env)
    pause(env, "MANAGE_ONLY", "system", reason="Subscription management service: retain existing paper orders and "
                                               "positions; no new decisions.")
    task(env, "t-old", "trader", "FAILED", created=-6 * 86400, output={"reason": "market feed blocks new exposure"})
    data = snap(env)
    assert data["now"]["code"] == "safety_stop"
    why = text(data["attention"][0]["why"])
    assert "maintenance mode" in why and "Trader's turn failed" not in why
    assert "maintenance mode" in text(data["team"]["pause"]["reason"])


def test_stop_during_a_safety_stop_is_partly_reversible_and_not_recommended(env):
    run(env)
    pause(env, "MANAGE_ONLY", "system", reason="role unavailable; manage positions")
    stop = card(snap(env), "stop_safely")
    assert stop["shown"] is True and stop["recommended"] is False
    assert stop["facts"]["flat"] is True
    preview = stop["preview"]
    assert preview["reversible"] == "partly"
    assert "Not recommended" in text(preview["when"])
    assert "cause isn't fixed" in text(preview["undo"])
    assert any("replaces the automatic safety stop" in text(line) for line in preview["what_happens"])
    env.db.execute("DELETE FROM pause_states WHERE portfolio_id=?", (env.pid,))
    normal = card(snap(env), "stop_safely")["preview"]
    assert normal["reversible"] == "yes" and "Not recommended" not in text(normal["when"])


def test_owner_pause_resume_shown_with_predicted_blockers(env):
    run(env)
    pause(env, "MANAGE_ONLY", "owner")
    insert(env, "order_intents", intent_id="i1", portfolio_id=env.pid, client_order_id="c1", state="UNKNOWN",
           symbol="BTC/USD", payload_json='{"client_order_id":"c1","symbol":"BTC/USD"}',
           created_at=at(env, -30), updated_at=at(env, -30))
    data = snap(env)
    assert data["now"]["code"] == "owner_pause"
    assert data["now"]["primary_decision"] == "resume"
    resume = card(data, "resume")
    assert resume["shown"] is True and resume["available"] is True and resume["recommended"] is False
    blockers = resume["facts"]["predicted_blockers"]
    assert blockers == [{"code": "unresolved orders", "definite": False,
                         "text": [{"t": "text", "v": "A practice order's status is unclear"}]}]
    assert "probably be refused" in text(resume["preview"]["when"])
    assert card(data, "pause_ai")["shown"] is False
    assert {"orders_unclear", "owner_paused"} <= set(codes(data))
    assert data["money"]["holdings"]["unclear_orders"] == 1
    assert room(data, "accounts")["light"] == "attention"


def test_pause_never_downgrades_a_stronger_leader_pause(env):
    run(env)
    reading(env, remaining=38)
    pause(env, "FLATTEN", "leader", reason="Too volatile to hold anything")
    data = snap(env)
    pause_card = card(data, "pause_ai")
    assert pause_card["shown"] is True and pause_card["available"] is False
    assert pause_card["recommended"] is False
    assert "stronger" in text(pause_card["unavailable_because"])
    assert "'sell everything'" in text(pause_card["unavailable_because"])
    allowance = next(item for item in data["attention"] if item["id"] == "allowance_near")
    assert allowance["decision"] is None and allowance["can_fix_here"] is False
    env.db.execute("UPDATE pause_states SET profile='PAUSE_DECISIONS' WHERE portfolio_id=?", (env.pid,))
    softer = snap(env)
    pause_card = card(softer, "pause_ai")
    assert pause_card["available"] is True
    assert any("replaces the Leader's 'decisions paused' setting" in text(line)
               for line in pause_card["preview"]["what_happens"])
    assert next(item for item in softer["attention"] if item["id"] == "allowance_near")["decision"] == "pause_ai"


def test_review_is_unavailable_while_any_cycle_task_is_unresolved(env):
    quiet(env)
    assert card(snap(env), "start_optimisation")["available"] is True
    task(env, "t-cycle", "optimisation", "SUCCEEDED", created=-900, objective="owner-optimisation-cycle")
    task(env, "t-cycle-a", "optimisation", "FAILED", created=-800, root="t-cycle")
    task(env, "t-cycle-b", "leader", "RUNNING", created=-700, root="t-cycle", lease=60)
    review = card(snap(env), "start_optimisation")
    assert review["available"] is False and "already in progress" in text(review["unavailable_because"])


def test_resume_hidden_when_running(env):
    quiet(env)
    data = snap(env)
    resume = card(data, "resume")
    assert resume["shown"] is False and resume["facts"] == {}
    assert card(data, "pause_ai")["shown"] is True


@pytest.mark.parametrize(("reason", "state", "code", "clears"), [
    ("subscription quota exhausted", "paused_quota", "quota_paused", True),
    ("subscription model unavailable", "paused_model", "model_unavailable", False),
])
def test_quota_and_model_pauses(env, reason, state, code, clears):
    run(env)
    reading(env, remaining=3, paused_reason=reason)
    schedule(env, "trader", -3600)
    data = snap(env)
    assert data["team"]["ai"]["state"] == state
    assert data["team"]["ai"]["clears_by_itself"] is clears
    assert data["now"]["code"] == code
    trader = room(data, "trader")
    assert trader["light"] == "paused"
    assert trader["next"]["overdue"] is True and "AI is paused" in text(trader["next"]["waiting_because"])
    if state == "paused_model":
        assert "model_unavailable" in codes(data)


def test_quota_deferrals_collapse(env):
    run(env)
    start = env.runtime.clock.now() - timedelta(hours=3)
    for index in range(50):
        env.runtime.ledger._activity(env.pid, "subscription_quota_deferred",
                                     {"task_id": "t-77", "role": "trader", "inference_attempts": 0},
                                     created_at=utc_iso(start + timedelta(seconds=30 * index)))
    data = snap(env)
    waits = [item for item in data["story"]["items"] if item["kind"] == "allowance_wait"]
    assert len(waits) == 1
    assert waits[0]["count"] == 50
    assert waits[0]["until"] == iso_ms(start + timedelta(seconds=30 * 49))
    assert waits[0]["at"] < waits[0]["until"]
    assert "50 retries" in text(waits[0]["headline"])


def events(env, kind, rows):
    """Bulk activity events: ``rows`` are (seconds from now, payload) pairs, inserted in time order."""
    with env.db.transaction() as connection:
        connection.executemany(
            """INSERT INTO activity_events (event_id, portfolio_id, kind, payload_json, created_at, hash, prev_hash)
            VALUES (?, ?, ?, ?, ?, ?, NULL)""",
            [(f"e-{kind}-{index}", env.pid, kind, json.dumps(payload), at(env, seconds), f"h-{kind}-{index}")
             for index, (seconds, payload) in enumerate(sorted(rows, key=lambda row: row[0]))])


def _page_through(env, first: dict) -> tuple[list[dict], list[dict]]:
    """Every story page from ``first`` back to the first uncut page: (pages, items de-duplicated by id)."""
    pages, seen = [first], {}
    while pages[-1]["next_before"] != pages[-1]["window_start"] and len(pages) < 12:
        pages.append(hq.story_page(env.runtime, OWNER, before=pages[-1]["next_before"])["story"])
    for page in pages:
        for item in page["items"]:
            seen.setdefault(item["id"], item)
    return pages, list(seen.values())


def test_long_allowance_wait_is_counted_and_older_problems_stay_reachable(env):
    run(env)
    reading(env)
    window_start = env.runtime.clock.now() - timedelta(hours=72)
    # 72 hours of trader deferrals every 30 s, beginning a minute before the story window.
    first = -72 * 3600 - 60
    events(env, "subscription_quota_deferred",
           [(first + 30 * index, {"task_id": "t-77", "role": "trader", "inference_attempts": 0})
            for index in range(8640)])
    events(env, "subscription_ai_paused", [(-40 * 3600, {"task_id": "t-paused", "role": "trader"})])
    data = snap(env)
    assert data["team"]["ai"]["state"] == "waiting_for_allowance"
    story = data["story"]
    wait = next(item for item in story["items"] if item["kind"] == "allowance_wait")
    assert wait["count"] >= 8600
    assert abs((hq_facts.parse(wait["at"]) - window_start).total_seconds()) <= 60
    assert "at least" in text(wait["headline"])
    assert any(item["kind"] == "ai_paused" for item in story["items"])
    assert story["next_before"] == story["window_start"]  # nothing was cut

    # A capped reader: 130 decisions in the last day are more than the 120 the reader reads.
    for index in reversed(range(130)):  # oldest first, as rows are written
        decision(env, f"d-many-{index:03d}", created=-60 - 600 * index)
    data = snap(env)
    story = data["story"]
    assert story["window_start"] > iso_ms(window_start)
    assert story["next_before"] != story["window_start"]
    pages, items = _page_through(env, story)
    ids = {item["id"] for item in items}
    assert {f"decision:d-many-{index:03d}" for index in range(130)} <= ids
    assert any(item["kind"] == "ai_paused" for item in items)
    waits = [item for item in items if item["kind"] == "allowance_wait"]
    assert sum(item["count"] for item in waits) >= 8600  # split where a page starts, never dropped
    for page in pages:
        validate_snapshot({**data, "story": page})
        if page is not pages[-1]:
            assert page["next_before"] != page["window_start"]


def test_deferral_streaks_skip_malformed_rows_and_split_by_department(env):
    events(env, "subscription_quota_deferred",
           [(-300 + 30 * index, {"task_id": "t-1", "role": "trader"}) for index in range(5)]
           + [(-290 + 30 * index, {"task_id": "t-2", "role": "research"}) for index in range(3)])
    insert(env, "activity_events", event_id="e-bad", portfolio_id=env.pid, kind="subscription_quota_deferred",
           payload_json="{", created_at=at(env, -100), hash="h-bad", prev_hash=None)
    streaks = hq_facts.deferral_streaks(env.db, env.pid, since=env.runtime.clock.now() - timedelta(hours=1))
    assert sorted((streak["role"], streak["count"], streak["complete"]) for streak in streaks) == [
        ("research", 3, True), ("trader", 5, True)]


def test_story_paging_keeps_items_that_share_a_timestamp(env):
    # 59 newer decisions, then 3 that share one millisecond: the 60-item page ends among those 3.
    for index in range(59):
        decision(env, f"d-{index:02d}", created=-60 - 60 * index)
    shared = at(env, -60 - 60 * 59 - 120)
    for suffix in ("a", "b", "c"):
        insert(env, "decisions", decision_id=f"d-same-{suffix}", portfolio_id=env.pid, action="no_action",
               payload_json=json.dumps({"action": "no_action", "rationale": "Waiting."}), mandate_revision="1",
               policy_revision="1", snapshot_id="snap", system_version_id="v1", created_at=shared, task_id=None)
    expected = {f"decision:d-{index:02d}" for index in range(59)} | {f"decision:d-same-{s}" for s in "abc"}
    first = snap(env)["story"]
    assert len(first["items"]) == 60 and first["next_before"] != first["window_start"]
    second = hq.story_page(env.runtime, OWNER, before=first["next_before"], limit=60)["story"]
    ids = [item["id"] for item in first["items"] + second["items"]]
    found = [item_id for item_id in dict.fromkeys(ids) if item_id.startswith("decision:")]
    assert set(found) == expected and len(found) == 62


def test_probably_low_allowance_gate(env):
    run(env)
    reading(env, remaining=38)
    schedule(env, "trader", -300)
    data = snap(env)
    assert data["team"]["ai"]["state"] == "probably_low"
    assert data["team"]["allowance"]["status"] == "below_line"
    assert data["now"]["code"] == "probably_low_allowance"
    waiting = room(data, "trader")["next"]["waiting_because"]
    assert text(waiting).startswith("Probably waiting for AI allowance: the last reading was 38%")
    overdue = next(item for item in data["calendar"]["items"] if item["status"] == "overdue")
    assert "Probably" in text(overdue["waiting_because"])
    allowance = next(item for item in data["attention"] if item["id"] == "allowance_near")
    assert "below the line" in text(allowance["title"])
    assert card(data, "pause_ai")["recommended"] is True


def test_schedule_selection(env):
    env.runtime.config = PaperRuntimeConfig(optimisation_manual_only=True)
    run(env)
    schedule(env, "trader", 3600, last_due=-8 * 3600, schedule_id="s-old")
    schedule(env, "trader", 7200, last_due=-2 * 3600, name="artifact-trader-review", schedule_id="s-new")
    schedule(env, "optimisation", -86400, interval=604800)
    data = snap(env)
    trader = room(data, "trader")
    assert trader["next"]["at"] == iso_ms(at(env, 7200))
    assert trader["next"]["extra_rows"] == 1
    optimisation = room(data, "optimisation")
    assert optimisation["next"] is None
    assert optimisation["light"] == "on_call"
    assert data["team"]["automatic_optimisation"] is False
    assert not [item for item in data["calendar"]["items"] if item["dept"] == "optimisation"]


def test_reserve_rule_unknown_is_not_none(env):
    data = snap(env)  # no allowance reading and no readable reserve policy
    assert data["team"]["allowance"]["stop_line_state"] == "unknown"
    assert data["prompt_facts"]["allowance"] == "No allowance reading yet; the reserve rule couldn't be read"
    run(env)
    reading(env)
    on = snap(env)
    assert on["team"]["allowance"]["stop_line_state"] == "on" and on["team"]["allowance"]["stop_line_percent"] == 40
    env.runtime.subscription_readonly_status = None
    unknown = snap(env)
    assert unknown["team"]["allowance"]["stop_line_state"] == "unknown"
    assert unknown["team"]["allowance"]["stop_line_percent"] is None
    assert "the reserve rule couldn't be read" in unknown["prompt_facts"]["allowance"]
    assert "no reserve rule is set" not in unknown["prompt_facts"]["allowance"]
    assert "No reserve rule is set." not in text(unknown["team"]["allowance"]["plain"])
    env.runtime.subscription_readonly_status = lambda: {"subscription": {"quota_reserve_policy": {
        "enabled": False, "admission_threshold_remaining_percent": 40}}}
    off = snap(env)
    assert off["team"]["allowance"]["stop_line_state"] == "off"
    assert "No reserve rule is set." in text(off["team"]["allowance"]["plain"])
    assert "no reserve rule is set" in off["prompt_facts"]["allowance"]


def test_leftover_schedule_rows_are_ignored(env):
    env.runtime.config = PaperRuntimeConfig(schedule_intervals={"trader": 14400})
    run(env)
    reading(env)
    schedule(env, "trader", 3600)
    schedule(env, "research", -3 * 86400, interval=86400)
    data = snap(env)
    assert room(data, "research")["next"] is None
    assert not [item for item in data["calendar"]["items"] if item["dept"] == "research"]
    assert next(turn for turn in data["prompt_facts"]["next_turns"] if turn["dept"] == "research")["at"] is None
    assert "Overdue" not in json.dumps(data)
    assert room(data, "trader")["next"]["status"] == "planned"


def test_artifact_row_wins(env):
    run(env)
    schedule(env, "research", 7200, interval=86400, last_due=-600, schedule_id="s-plain")
    schedule(env, "research", 9000, interval=86400, last_due=-5000, name="artifact-research-review",
             schedule_id="s-artifact")
    data = snap(env)
    research = room(data, "research")["next"]
    assert research["at"] == iso_ms(at(env, 9000)) and research["extra_rows"] == 1
    assert [item["id"] for item in data["calendar"]["items"] if item["dept"] == "research"][0] == \
        f"turn:s-artifact:{iso_ms(at(env, 9000))}"


def test_overdue_projections_start_after_now(env):
    run(env)
    reading(env, remaining=38)
    schedule(env, "trader", -5 * 3600)
    data = snap(env)
    estimated = sorted(item["at"] for item in data["calendar"]["items"]
                       if item["dept"] == "trader" and item["status"] == "estimated")
    assert estimated and all(moment > data["as_of"] for moment in estimated)
    assert estimated[0] == iso_ms(at(env, 4 * 3600))


def _first_trader_turn(data) -> dict:
    return min((item for item in data["calendar"]["items"] if item["dept"] == "trader"
                and item["kind"] == "dept_turn"), key=lambda item: item["at"])


@pytest.mark.parametrize(("gate", "words"), [
    ("G1", "Only if the team is running."), ("G2", "Only if the AI is back on."),
    ("G3", "Only once AI allowance is available."), ("G5", "Only if Resume is possible."),
])
def test_first_turn_carries_its_condition(env, gate, words):
    if gate == "G1":
        run(env, heartbeat=-91)
    else:
        run(env)
    reading(env, remaining=3, paused_reason="subscription quota exhausted" if gate == "G2" else None)
    if gate == "G3":
        events(env, "subscription_quota_deferred", [(-60, {"task_id": "t-q", "role": "research"})])
    if gate == "G5":
        pause(env, "MANAGE_ONLY", "owner")
    schedule(env, "trader", 3600)
    first = _first_trader_turn(snap(env))
    assert first["status"] == "planned" and first["conditional"] is True
    assert text(first["detail"]) == words


def test_next_status(env):
    run(env)
    reading(env)
    schedule(env, "trader", 3600)
    assert room(snap(env), "trader")["next"]["status"] == "planned"
    for due, status in ((-60, "due_now"), (-3600, "overdue")):
        env.db.execute("UPDATE schedules SET next_due_at=? WHERE schedule_id='s-trader'", (at(env, due),))
        trader = room(snap(env), "trader")["next"]
        assert (trader["overdue"], trader["status"]) == (True, status)
    env.db.execute("DELETE FROM subscription_provider_state")
    reading(env, remaining=38)
    assert room(snap(env), "trader")["next"]["status"] == "waiting"


def test_management_only_is_a_gate(env):
    run(env, status="MANAGEMENT_ONLY")
    reading(env)
    schedule(env, "trader", -3600)
    schedule(env, "research", 7200, interval=86400)
    data = snap(env)
    assert data["now"]["code"] == "calm"
    assert "started while AI wasn't ready" in text(data["now"]["detail"])
    trader = room(data, "trader")["next"]
    assert trader["status"] == "waiting" and "started without AI" in text(trader["waiting_because"])
    research = min((item for item in data["calendar"]["items"] if item["dept"] == "research"),
                   key=lambda item: item["at"])
    assert text(research["detail"]) == "Only if AI becomes available." and research["conditional"] is True


def test_notes_expiring_before_trader(env):
    quiet(env)
    finding(env, "f-90", created=-3600, expires=1800)
    data = snap(env)
    assert "notes_expire_early" in codes(data)
    assert next(item for item in data["attention"] if item["id"] == "notes_expire_early")["since"] is None
    expiry = next(item for item in data["calendar"]["items"] if item["kind"] == "research_expiry")
    assert "Before the Trader's turn" in text(expiry["warning"])
    trader = room(data, "trader")
    assert "without research notes" in text(trader["if_nothing"])
    assert "Research notes expire at" in text(trader["heads_up"])
    assert data["money"]["counts"]["notes_in_use"] == 1


def test_close_research_expiries_are_one_calendar_item(env):
    quiet(env)  # the Trader's next turn is in 4 hours
    for index in range(3):  # expire before that turn: a group of their own, with the warning
        finding(env, f"f-early-{index}", created=-3600, expires=1800 + index * 60)
    for index in range(18):  # written together, expiring 8 h to 9 h 25 min from now
        finding(env, f"f-batch-{index}", created=-3600, expires=8 * 3600 + index * 300)
    finding(env, "f-later", created=-3600, expires=8 * 3600 + 7300)  # more than 2 h after the batch's first
    data = snap(env)
    expiries = [item for item in data["calendar"]["items"] if item["kind"] == "research_expiry"]
    assert [item.get("count") for item in expiries] == [3, 18, None]
    early, batch, later = expiries
    assert text(early["title"]) == "3 research notes stop being used"
    assert "Before the Trader's turn" in text(early["warning"]) and batch["warning"] is None
    assert text(batch["title"]) == "18 research notes stop being used"
    assert batch["id"] == "research-expiry:f-batch-0"
    assert batch["at"] == iso_ms(at(env, 8 * 3600)) and batch["until"] == iso_ms(at(env, 8 * 3600 + 17 * 300))
    assert "count" not in later and "until" not in later
    assert text(later["title"]) == "Research notes stop being used"
    assert early["id"] in data["calendar"]["up_next"]
    assert data["money"]["counts"]["notes_in_use"] == 22


def test_research_expiries_keep_the_ten_soonest_groups(env):
    quiet(env)
    for index in range(12):  # three hours apart: twelve separate items
        finding(env, f"f-{index:02d}", created=-3600, expires=5 * 3600 + index * 3 * 3600)
    expiries = [item for item in snap(env)["calendar"]["items"] if item["kind"] == "research_expiry"]
    assert [item["id"] for item in expiries] == [f"research-expiry:f-{index:02d}" for index in range(10)]
    assert not [item for item in expiries if "count" in item]


def test_unknown_money_is_null(env):
    env.runtime.ledger.deposit(env.pid, "USD", Decimal("10"), "synthetic-usd-top-up")
    data = snap(env)
    money = data["money"]
    assert money["change"]["total"] is None
    assert money["change"]["unknown_reason"]
    zero_money = []

    def walk(value, path):
        # Every Money object and money segment in the whole snapshot (review T13), not only the money block.
        if isinstance(value, dict):
            if "amount" in value and "currency" in value and Decimal(value["amount"]) == 0:
                zero_money.append(path)
            for key, item in value.items():
                walk(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    walk(data, "snapshot")
    assert zero_money == []
    rendered = [value for value in _strings(data) if re.search(r"[$€£]0\.00", value)]
    assert rendered == []
    assert money["practice_value"] is None
    assert "not known yet" in text(data["now"]["detail"])
    assert data["prompt_facts"]["practice_value"] is None


def test_fx_recorded_stale_is_calm(env):
    env.runtime.ledger.deposit(env.pid, "USD", Decimal("10"), "synthetic-usd-top-up")
    env.runtime.ledger.observe_fx(base="USD", quote="EUR", rate=Decimal("0.9"), source="synthetic reference",
                                  kind="reference", stale=True)
    data = snap(env)
    notes = data["money"]["estimate_notes"]
    assert [(note["code"], note["tone"]) for note in notes] == [("euro_rate_from_earlier_day", "calm")]
    assert "latest published euro rate" in text(notes[0]["text"])
    assert not {"prices_old", "routine_failing"} & set(codes(data))
    assert data["money"]["practice_value"] is not None


def test_history_gap_honest_note(env):
    env.runtime.recovery_history = lambda: {"status": "PARTIAL_HISTORY", "incidents": []}
    data = snap(env)
    pays = data["money"]["pays_for_itself"]
    assert "history_gap" in [item["code"] for item in pays["missing"]]
    assert next(item for item in pays["missing"] if item["code"] == "history_gap")["fixable_here"] is False
    assert "history gap" in text(pays["honest_note"])
    assert "history_gap" in [notice["code"] for notice in data["notices"]]
    costs = next(item for item in data["attention"] if item["id"] == "costs_unconfirmed")
    assert "history gap" in text(costs["consequence"])
    bill = card(data, "record_bill")
    assert bill["recommended"] is True and not text(bill["recommended_because"]).startswith("Optional")
    env.runtime.recovery_history = lambda: {"status": "UNAVAILABLE"}
    assert "history_unverified" in [notice["code"] for notice in snap(env)["notices"]]


def test_reader_and_leader_cannot_act(env):
    quiet(env)
    for viewer in (READER, hq.Viewer("leader", "cookie", True), hq.Viewer("owner", "bearer", False),
                   hq.Viewer("owner", "cookie", False)):
        data = snap(env, viewer)
        assert data["viewer"]["can_act"] is False
        assert data["decisions"] == []
        assert "View only" in text(data["viewer"]["act_block_reason"])
        assert "read_only" in [notice["code"] for notice in data["notices"]]
        if viewer.role != "owner":
            assert data["checkins"] is None
            assert data["links"]["owner"] is None
            assert data["viewer"]["owner_revision"] is None
    owner = snap(env)
    # Migration 0025 is registered, so the owner's (still empty) check-in store is available.
    assert owner["viewer"]["can_act"] is True and owner["checkins"]["available"] is True
    assert owner["checkins"]["items"] == []
    assert len(owner["decisions"]) == 8


def test_live_mode_disables_decisions_and_paper_copy(env):
    quiet(env, mode="live")
    data = snap(env)
    encoded = json.dumps(data)
    assert data["mode"] == "live"
    assert data["viewer"]["can_act"] is False and data["decisions"] == []
    assert "Practice money" not in encoded
    assert "Paper results are simulated." not in encoded
    assert "live_mode" in [notice["code"] for notice in data["notices"]]
    assert "live account" in text(data["money"]["at_risk"])
    assert "practice" not in encoded.lower().replace('"practice_value"', "")


def _orders_and_problems(env, *, mode):
    quiet(env, mode=mode)
    insert(env, "order_intents", intent_id="i-unclear", portfolio_id=env.pid, client_order_id="c-unclear",
           state="UNKNOWN", symbol="BTC/USD", payload_json='{"client_order_id":"c-unclear","symbol":"BTC/USD"}',
           created_at=at(env, -30), updated_at=at(env, -30))
    env.db.execute("UPDATE graph_service_runs SET error_type='reconciliation:X;dispatch:Y' WHERE run_id='r-1'")
    task(env, "t-px", "trader", "FAILED", created=-900, dedup_key=f"schedule:s-trader:{at(env, -900)}",
         output={"reason": "stale or missing quote for BTC/USD"})
    env.runtime.recovery_history = lambda: {"status": "PARTIAL_HISTORY", "incidents": []}


def test_live_mode_never_says_practice(env):
    _orders_and_problems(env, mode="live")
    data = snap(env)
    assert data["now"]["code"] == "routine_failing"
    assert {"orders_unclear", "routine_failing"} <= set(codes(data))
    encoded = json.dumps(data).lower().replace('"practice_value"', "")
    assert "practice" not in encoded
    assert "1 order has an unclear status" in [text(item["title"]) for item in data["attention"]]
    assert "Places orders" in text(room(data, "accounts")["job"])
    assert "An order's status is unclear" in text(room(data, "accounts")["now"])
    assert "the account's money" in text(room(data, "trader")["job"])
    assert "prices were too old to place an order" in encoded


def test_paper_mode_still_says_practice_order(env):
    _orders_and_problems(env, mode="paper")
    data = snap(env)
    encoded = json.dumps(data).lower()
    assert "practice order" in encoded
    assert "1 practice order has an unclear status" in [text(item["title"]) for item in data["attention"]]


def test_pause_preview_mentions_set_aside_when_working(env):
    run(env)
    reading(env)
    task(env, "t-44", "research", "RUNNING", created=-90, lease=60, attempts=1)
    insert(env, "subscription_invocations", invocation_id="inv-1", request_hash="h", task_id="t-44",
           root_task_id="t-44", role="research", run_id="r-1", system_version_id="v1", provider="codex_subscription",
           requested_model="m", state="DISPATCHED", quota_json="{}", created_at=at(env, -80),
           updated_at=at(env, -80))
    insert(env, "subscription_attempts", attempt_id="inv-1:1", invocation_id="inv-1", attempt_index=1,
           request_hash="h1", provider="codex_subscription", requested_model="m", state="DISPATCHED",
           created_at=at(env, -75), updated_at=at(env, -75))
    data = snap(env)
    assert data["now"]["code"] == "working"
    assert "Research is working right now." == text(data["now"]["headline"])
    assert "try 1 of 3" in text(data["now"]["detail"])
    assert room(data, "research")["light"] == "working"
    preview = card(data, "pause_ai")["preview"]
    assert any("set aside" in text(item) for item in preview["what_happens"])


def test_stuck_turns_and_repeat_problems_need_attention(env):
    run(env)
    reading(env)
    schedule(env, "learning", 6 * 3600, interval=302400)
    task(env, "t-41", "leader", "BLOCKED_BUDGET", created=-1200, dedup_key="secretary:digest-1",
         objective="Review departmental evidence", output={"reason": "room exhausted"}, attempts=1)
    for index, created in enumerate((-90000, -3600)):
        task(env, f"t-l{index}", "learning", "FAILED", created=created,
             dedup_key=f"schedule:s-learning:{at(env, created)}",
             output={"reason": "model timeout_uncertain: no answer"}, attempts=3)
    data = snap(env)
    stuck = data["attention"][0]
    assert stuck["id"] == "turn_stuck:t-41"
    assert "won't retry by itself" in text(stuck["why"]) and "cost limit" in text(stuck["why"])
    assert "out of allowance" not in text(stuck["why"])  # its own cost limit, not the AI allowance (U7)
    assert stuck["consequence"] == []
    assert room(data, "leader")["last"]["text"] == [{"t": "text", "v": "Its last turn got stuck."}]
    repeat = next(item for item in data["attention"] if item["id"] == "repeat_problems")
    assert text(repeat["title"]) == "Learning's turn (every 3½ days) keeps having problems (2 in a row)"
    assert "didn't answer in time" in text(repeat["why"])
    assert room(data, "leader")["light"] == "stuck"
    assert room(data, "learning")["had_problem"] is True
    quest = next(item for item in data["quests"] if item["id"] == "task:t-41")
    assert quest["origin"] == "secretary" and quest["needs_you"] is True
    assert quest["reason"][0]["code"] == "room exhausted"
    review = next(item for item in data["calendar"]["items"] if item["kind"] == "extra_review")
    assert review["status"] == "stuck"
    assert "Leader's turn is stuck" in data["prompt_facts"]["flagged"]


def test_old_problem_says_it_was_tried_again(env):
    run(env)
    schedule(env, "learning", 6 * 3600, interval=302400)
    task(env, "t-lf", "learning", "FAILED", created=-50 * 3600, output={"reason": "model temporary: retry"},
         dedup_key=f"schedule:s-learning:{at(env, -50 * 3600)}")
    for task_id, created in (("t-ls1", -26 * 3600), ("t-ls2", -2 * 3600)):
        task(env, task_id, "learning", "SUCCEEDED", created=created, result={"lesson_revision_ids": []},
             dedup_key=f"schedule:s-learning:{at(env, created)}")
    problem = next(item for item in snap(env)["story"]["items"] if item["id"] == "task:t-lf")
    assert text(problem["headline"]) == "Learning's turn (every 3½ days) didn't work (a temporary AI error)."
    assert "Tried again" in text(problem["consequence"]) and "Next try" not in text(problem["consequence"])
    assert problem["consequence"][1] == {"t": "time", "at": iso_ms(at(env, -26 * 3600)), "style": "day_clock"}


def test_ai_pause_story_uses_the_reason_recorded_then(env):
    run(env)
    reading(env, remaining=3, paused_reason="subscription quota exhausted")
    insert(env, "subscription_invocations", invocation_id="inv-p", request_hash="h", task_id="t-paused",
           root_task_id="t-paused", role="trader", run_id="r-1", system_version_id="v1",
           provider="codex_subscription", requested_model="m", state="FAILED",
           result_json=json.dumps({"ok": False, "failure": "unsupported"}), quota_json="{}",
           created_at=at(env, -3700), updated_at=at(env, -3600))
    events(env, "subscription_ai_paused", [(-3600, {"task_id": "t-paused", "role": "trader"}),
                                           (-1800, {"task_id": "t-unknown", "role": "trader"})])
    paused = sorted((item for item in snap(env)["story"]["items"] if item["kind"] == "ai_paused"),
                    key=lambda item: item["at"])
    assert [text(item["headline"]) for item in paused] == [
        "AI paused: the AI model isn't available.", "AI paused: a provider stop."]


def test_trade_decision_names_quantity_coin_and_limit(env):
    run(env)
    payload = {"action": "enter", "symbol": "ETH/USD", "quantity": {"amount": "0.5", "asset": "ETH"},
               "limit_price": {"amount": "2418.70", "currency": "USD"}, "rationale": "Breakout confirmed."}
    task(env, "t-buy", "trader", "SUCCEEDED", created=-400, output={"decision_id": "d-buy", "intent_id": "i-buy"},
         result={"decision_id": "d-buy"})
    insert(env, "decisions", decision_id="d-buy", portfolio_id=env.pid, action="enter",
           payload_json=json.dumps(payload), mandate_revision="1", policy_revision="1", snapshot_id="snap",
           system_version_id="v1", created_at=at(env, -300), task_id="t-buy")
    insert(env, "fills", fill_id="fill-buy", venue="paper", account_id="paper", trade_id="tr-buy",
           portfolio_id=env.pid, intent_id="i-buy", created_at=at(env, -200),
           document_json=json.dumps({"symbol": "ETH/USD", "side": "buy", "quantity": "0.5", "price": "2418.70",
                                     "fee_amount": "1.20", "fee_asset": "USD"}))
    data = snap(env)
    item = next(item for item in data["story"]["items"] if item["id"] == "decision:d-buy")
    assert text(item["headline"]) == "Trader decided to buy 0.5 ETH at a limit of $2,418.70."
    assert text(item["consequence"]) == "Bought 0.5 ETH at $2,418.70, fee $1.20."
    assert text(room(data, "trader")["last"]["text"]) == "Decided to buy ETH."
    env.db.execute("UPDATE decisions SET payload_json=? WHERE decision_id='d-buy'",
                   (json.dumps({"action": "enter", "symbol": "ETH/USD", "rationale": "Breakout."}),))
    plain = next(item for item in snap(env)["story"]["items"] if item["id"] == "decision:d-buy")
    assert text(plain["headline"]) == "Trader decided to buy ETH."


def test_story_pages_go_back_without_inventing(env):
    quiet(env)
    decision(env, "d-old", created=-4 * 86400)
    data = snap(env)
    assert "decision:d-old" not in [item["id"] for item in data["story"]["items"]]
    page = hq.story_page(env.runtime, OWNER, before=data["story"]["window_start"])
    assert [item["id"] for item in page["story"]["items"]] == ["decision:d-old"]
    with pytest.raises(ValueError):
        hq.story_page(env.runtime, OWNER, before="2026-01-01T00:00:00")


def test_routes_validate_parameters_and_redact(env):
    app = FastAPI()

    def identity(request: Request) -> str:
        parts = request.headers.get("authorization", "").split()
        token = parts[1] if len(parts) == 2 else request.cookies.get("tg_session")
        role = role_for_token(env.db, token)
        if role is None:
            raise HTTPException(401, "authentication required")
        request.state.token, request.state.role = token, role
        request.state.auth_mechanism = "bearer" if len(parts) == 2 else "cookie"
        return role

    hq.register(app, env.runtime, identity)
    client = TestClient(app)
    bearer = {"Authorization": f"Bearer {env.token}"}
    assert client.get("/api/v1/hq").status_code == 401
    response = client.get("/api/v1/hq", headers=bearer)
    assert response.status_code == 200
    assert response.json()["schema"] == "trade-graph.hq.v1"
    assert response.json()["viewer"] == {"role": "owner", "auth": "bearer", "can_act": False,
                                         "act_block_reason": response.json()["viewer"]["act_block_reason"],
                                         "owner_revision": 0}
    assert client.get("/api/v1/hq", params={"calendar_days": 14}, headers=bearer).status_code == 200
    for days in (0, 15, "x"):
        assert client.get("/api/v1/hq", params={"calendar_days": days}, headers=bearer).status_code == 422
    assert client.get("/api/v1/hq/story", params={"before": "bad"}, headers=bearer).status_code == 422
    assert client.get("/api/v1/hq/story", headers=bearer).status_code == 422
    story = client.get("/api/v1/hq/story", params={"before": at(env), "limit": 5}, headers=bearer)
    assert story.status_code == 200 and set(story.json()) == {"story"}
    cookie_token, _ = issue_session(env.db, env.runtime.clock, "owner")
    client.cookies.set("tg_session", cookie_token)
    assert client.get("/api/v1/hq").json()["viewer"]["can_act"] is True


# --- Scenario shapes mirror the committed fixtures ------------------------------------------------------------


def _scenario(env, name: str) -> hq.Viewer:
    viewer = OWNER
    if name == "snapshot_empty":
        pass
    elif name in ("snapshot_quiet", "snapshot_reader", "snapshot_live"):
        quiet(env, mode="live" if name == "snapshot_live" else "paper")
        viewer = READER if name == "snapshot_reader" else OWNER
    elif name == "snapshot_busy":
        run(env)
        reading(env, remaining=46)
        task(env, "t-44", "research", "RUNNING", created=-90, lease=60, attempts=1)
    elif name == "snapshot_allowance_paused":
        run(env)
        reading(env, remaining=3, paused_reason="subscription quota exhausted")
    elif name == "snapshot_silent":
        run(env, heartbeat=-245)
    elif name == "snapshot_safety_stop":
        run(env)
        pause(env, "MANAGE_ONLY", "system")
    elif name == "snapshot_offline":
        run(env, pid=99, heartbeat=-5800)
    return viewer


@pytest.mark.parametrize("name", sorted(path.stem for path in FIXTURES.glob("snapshot_*.json")))
def test_snapshot_validates_against_contract(env, name):
    fixture = json.loads((FIXTURES / f"{name}.json").read_text())
    data = snap(env, _scenario(env, name))
    assert data["now"]["code"] == fixture["now"]["code"]
    assert (data["mode"], data["viewer"]["role"]) == (fixture["mode"], fixture["viewer"]["role"])
    assert [(item["id"], item["kind"]) for item in data["departments"]] == list(DEPARTMENT_ORDER)
    assert set(data) == set(fixture)
    assert set(data["team"]) == set(fixture["team"]) and set(data["money"]) == set(fixture["money"])
    assert (data["checkins"] is None) == (fixture["checkins"] is None)
    assert len(json.dumps(data)) < 80 * 1024


def _checkin_store(monkeypatch, *, last_done, items=()):
    import sys

    import trade_graph.api

    store = SimpleNamespace(available=lambda runtime: True, window=lambda runtime, start, end: list(items),
                            last_done_at=lambda runtime: last_done)
    monkeypatch.setitem(sys.modules, "trade_graph.api.owner_checkins", store)
    monkeypatch.setattr(trade_graph.api, "owner_checkins", store, raising=False)
    return store


def test_prompt_since_counts_are_exact_beyond_reader_caps(env, monkeypatch):
    run(env)
    for index in range(45):
        insert(env, "fills", fill_id=f"fill-{index}", venue="paper", account_id="paper", trade_id=f"tr-{index}",
               portfolio_id=env.pid, intent_id=None, document_json="{}", created_at=at(env, -600 - 60 * index))
        finding(env, f"f-{index}", created=-700 - 60 * index, expires=3600)
    for index in reversed(range(130)):
        decision(env, f"d-{index:03d}", "hold" if index % 5 == 0 else "enter", created=-60 - 300 * index)
    began = time.perf_counter()
    since = snap(env)["prompt_facts"]["since"]
    print(f"snapshot with 130 decisions, 45 fills and 45 notes: {(time.perf_counter() - began) * 1000:.0f} ms")
    assert since["label"] == "the last 24 hours"
    assert (since["completed_trades"], since["research_notes"], since["decisions"]) == (45, 45, 130)
    assert since["waits"] == 26
    # The owner's last check-in was 9 days ago; a failure 8.5 days ago is older than the task reader's window.
    _checkin_store(monkeypatch, last_done=iso_ms(at(env, -9 * 86400)))
    task(env, "t-old-failure", "learning", "FAILED", created=-8.5 * 86400, output={"reason": "ValueError"})
    since = snap(env)["prompt_facts"]["since"]
    assert since["label"] == "my last check-in"
    assert since["problems"] >= 1 and since["decisions"] == 130


def test_owner_checkins_come_from_the_lazy_store(env, monkeypatch):
    import sys

    import trade_graph.api

    planned, done = iso_ms(at(env, 300)), iso_ms(at(env, -7200))
    items = [
        {"checkin_id": "c-done", "title": "Morning review", "prompt_kind": "baseline",
         "planned_at": iso_ms(at(env, -7300)), "state": "done", "done_at": done, "note": None,
         "history": [{"action": "plan", "at": iso_ms(at(env, -9000)), "planned_at": iso_ms(at(env, -7300))},
                     {"action": "done", "at": done, "planned_at": None}]},
        {"checkin_id": "c-due", "title": "Afternoon review", "prompt_kind": "interval", "planned_at": planned,
         "state": "due", "done_at": None, "note": None,
         "history": [{"action": "plan", "at": iso_ms(at(env, -3600)), "planned_at": planned}]},
    ]
    calls = []
    store = SimpleNamespace(available=lambda runtime: True,
                            window=lambda runtime, start, end: calls.append((start, end)) or items,
                            last_done_at=lambda runtime: done)
    monkeypatch.setitem(sys.modules, "trade_graph.api.owner_checkins", store)
    monkeypatch.setattr(trade_graph.api, "owner_checkins", store, raising=False)
    quiet(env)
    data = snap(env)
    checkins = data["checkins"]
    assert checkins["available"] is True and checkins["next_id"] == "c-due"
    assert checkins["last_done_at"] == done
    assert data["attention"][0]["id"] == "checkin_due"
    assert data["attention"][0]["checkin_id"] == "c-due"
    assert data["calendar"]["up_next"][0] == "checkin:c-due"
    assert data["prompt_facts"]["since"]["from"] == done
    assert data["prompt_facts"]["since"]["label"] == "my last check-in"
    assert "You marked 'Morning review' done." in [text(item["headline"]) for item in data["story"]["items"]]
    assert card(data, "plan_checkin")["shown"] is True and card(data, "plan_checkin")["recommended"] is False
    assert calls and calls[0][0] == iso_ms(at(env, -48 * 3600))
    assert snap(env, READER)["checkins"] is None
    store.available = lambda runtime: False
    unavailable = snap(env)
    assert unavailable["checkins"]["available"] is False and unavailable["checkins"]["items"] == []
    assert "checkin_due" not in codes(unavailable)


def test_all_times_iso_ms(env):
    _scenario(env, "snapshot_busy")
    task(env, "t-9", "leader", "FAILED", created=-7200, output={"reason": "model validation: bad"},
         result={"_status": "FAILED", "reason": "model validation: bad"})
    data = snap(env, calendar_days=14)
    page = hq.story_page(env.runtime, OWNER, before="2026-01-02T00:00:00+02:00")
    moments = [value for value in _strings({"snapshot": data, "page": page})
               if re.match(r"^\d{4}-\d{2}-\d{2}T", value)]
    assert moments and all(re.fullmatch(ISO_MS_PATTERN, value) for value in moments)


def test_unreadable_records_are_skipped_not_guessed(env):
    run(env)
    schedule(env, "trader", 3600)
    task(env, "t-bad", "trader", "FAILED", dedup_key="schedule:s-trader:not-a-time",
         output={"reason": "ValueError"})
    env.db.execute("UPDATE tasks SET created_at='not-a-time' WHERE task_id='t-bad'")
    insert(env, "decisions", decision_id="d-bad", portfolio_id=env.pid, action="no_action", payload_json="{",
           mandate_revision="1", policy_revision="1", snapshot_id="s", system_version_id="v",
           created_at="yesterday", task_id=None)
    data = snap(env)
    assert "decision:d-bad" not in [item["id"] for item in data["story"]["items"]]
    assert not [item for item in data["calendar"]["items"] if "not-a-time" in item["id"]]
    assert next(item for item in data["quests"] if item["id"] == "task:t-bad")["state"] == "FAILED"
