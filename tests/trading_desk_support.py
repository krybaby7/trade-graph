"""Helpers for the Trading desk tests (W12-A, C12): synthetic market-type labels, playbooks and stances.

Labels are written as the hourly scan writes them (one hash-checked row each) but with a short synthetic document, so a
test can say "SOL/USD was sideways at 09:05" without 863 hourly candles; the scripted week
(``test_trading_desk_week.py``) labels real synthetic candles with the protected labeller instead. Everything is
synthetic and labelled ``synthetic_fixture``. No network, model, key, paid call or order.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from trade_graph.application.activation import VersionController
from trade_graph.application.artifact_runtime import ArtifactRuntime
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.events import TaskSettings
from trade_graph.application.leader import LeaderOffice, Secretary
from trade_graph.application.model_invocations import MAX_REQUEST_BYTES
from trade_graph.application.runtime_models import RuntimeModelConfig, assemble_handlers
from trade_graph.application.worker import RoleWorker
from trade_graph.contracts.models import ModelRequest, ModelResult, ModelUsage, PriceCard
from trade_graph.contracts.trading_desk import CoinStance, PlaybookRevision
from trade_graph.domain.clock import utc_iso
from trade_graph.paper_runtime import installed_artifacts

SIDEWAYS_DIPS = {
    "why": "Fees decide most results: wait in falling markets, buy dips in sideways ones.",
    "choices": [
        {"market_type": "falling", "stance": "wait", "strategy_refs": [], "reason": "Losing less than holding is "
         "still losing; cash costs no fees.", "report_ids": []},
        {"market_type": "sideways", "stance": "buy_dips", "strategy_refs": ["slow-trend-pullback@1"],
         "reason": "Rest maker bids under the range.", "report_ids": []},
    ],
}


def playbook(**changes: Any) -> PlaybookRevision:
    return PlaybookRevision.model_validate({**SIDEWAYS_DIPS, **changes})


def stance(symbol: str = "BTC/USD", stance_word: str = "wait", seen: str = "falling", **changes: Any) -> CoinStance:
    document = {"symbol": symbol, "stance": stance_word, "market_type_seen": seen, "strategy_ref": None,
                "news_refs": [], "reason": f"Synthetic: {symbol} is {seen}."}
    document.update(changes)
    return CoinStance.model_validate(document)


def insert_label(database, symbol: str, market_type: str, as_of: datetime, *, reason: str | None = None) -> str:
    """One stored label at ``as_of`` (``unclear`` needs a reason), as ``market_type_labels`` keeps it."""
    label_id = "mt-" + uuid.uuid4().hex
    document = {"symbol": symbol, "market_type": market_type, "reason": reason, "rules": {"ref": "synthetic"},
                "candle_close_utc": utc_iso(as_of), "evidence": "synthetic_fixture"}
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    with database.immediate() as connection:
        connection.execute(
            """INSERT INTO market_type_labels (label_id, symbol, as_of, market_type, reason, candle_close, lag_bars,
               interval_minutes, source, rules_ref, rules_sha256, scan_id, evidence, document_json, document_sha256,
               recorded_at) VALUES (?, ?, ?, ?, ?, ?, 0, 60, 'synthetic', 'market-type-rules@1', ?, NULL,
               'synthetic_fixture', ?, ?, ?)""",
            (label_id, symbol, utc_iso(as_of), market_type, reason, utc_iso(as_of), "0" * 64, text,
             hashlib.sha256(text.encode("ascii")).hexdigest(), utc_iso(as_of)))
    return label_id


def insert_verdicts(database, titles_by_code: list[tuple[str, str]], at: datetime) -> None:
    """Jev verdicts as T44 stores them (a feed poll, the headline, an answered attempt, the would-wake verdict), with
    no reservation: for a protected installation's database, where scripted Jev checks are refused (D258)."""
    stamp = utc_iso(at)
    with database.immediate() as connection:
        poll = "poll-" + uuid.uuid4().hex
        connection.execute(
            """INSERT INTO news_feed_polls (poll_id, feed_id, url, publisher, started_at, finished_at, outcome,
               evidence, items_seen, items_new, items_repeat, items_rejected) VALUES (?, 'kraken-blog',
               'https://blog.kraken.com/feed', 'Kraken blog', ?, ?, 'fetched', 'synthetic_fixture', ?, ?, 0, 0)""",
            (poll, stamp, stamp, len(titles_by_code), len(titles_by_code)))
        for number, (code, title) in enumerate(titles_by_code):
            headline, attempt = f"nh-{uuid.uuid4().hex}", f"na-{uuid.uuid4().hex}"
            connection.execute(
                """INSERT INTO news_headlines (headline_id, link, feed_id, publisher, title, published_at, received_at,
                   item_sha256, evidence, poll_id) VALUES (?, ?, 'kraken-blog', 'Kraken blog', ?, ?, ?, ?,
                   'synthetic_fixture', ?)""",
                (headline, f"https://blog.kraken.com/synthetic/{headline}", title, stamp, stamp, "0" * 64, poll))
            connection.execute(
                """INSERT INTO news_jev_attempts (attempt_id, headline_id, attempt_number, reservation_id,
                   requested_model, question_set_version, request_sha256, request_json, watch_json, synthetic,
                   started_at) VALUES (?, ?, NULL, NULL, 'jev-1.13.0', 'news-alarm-questions-v1', ?, '{}', '{}', 1,
                   ?)""",
                (attempt, headline, "0" * 64, stamp))
            connection.execute("INSERT INTO news_jev_outcomes (attempt_id, status, usage_reported, finished_at) "
                               "VALUES (?, 'answered', 0, ?)", (attempt, stamp))
            connection.execute(
                """INSERT INTO news_alarm_verdicts (verdict_id, attempt_id, headline_id, rule_version,
                   question_set_version, urgent_threshold, would_wake, wake_roles_json, subjects_json,
                   held_subjects_json, event_type, holder_yes, created_at) VALUES (?, ?, ?, 'news-alarm-rule-v1',
                   'news-alarm-questions-v1', '0.5', 1, '["leader"]', ?, '[]', 'other', '0.9', ?)""",
                (f"nv-{uuid.uuid4().hex}", attempt, headline, json.dumps([code]), stamp))


SCRIPTED_CARD = PriceCard(
    price_card_id="scripted-desk", provider="scripted", model="scripted", endpoint="scripted", currency="EUR",
    input_per_million="0.1", output_per_million="0.1", effective_at="2026-01-01", verified_at="2026-01-01",
    source_id="fixture", tier="standard", context_band="short")


def request_bytes(request: ModelRequest, portfolio_id: str) -> int:
    """A request's size as the invocation journal bounds it (``InvocationJournal.binding``, 128 KiB)."""
    return len(json.dumps({"request": request.model_dump(mode="json"), "portfolio_id": portfolio_id,
                           "billing": {}}, sort_keys=True, default=str).encode())


class DeskRuntime:
    """The real Trader handler (the Trading desk) with a scripted model: its context, instruction and reply schema
    are the real ones, every request passes the real 128 KiB bound, and the desk's answer is computed by ``reply``
    from the request's context (what a model would read). No network, model, key or paid call; synthetic receipts."""

    def __init__(self, account, scheduler, *, reply: Callable[[dict[str, Any]], dict[str, Any]],
                 workspace: Path, owner: str = "desk-worker") -> None:
        database, clock = account.database, account.clock
        budget = BudgetGateway(database, clock)
        if database.execute("SELECT 1 FROM deployment_budget WHERE deployment_id = 'deployment'").fetchone() is None:
            budget.configure(deployment_id="deployment", currency="EUR", total=Decimal("5"), period=Decimal("5"),
                             priority_reserve=Decimal("1"), daily=Decimal("1.50"), root=Decimal("1.50"), roles={})
        budget.seed_card(SCRIPTED_CARD)
        self.portfolio = account.portfolio
        self.office = LeaderOffice(account.execution, scheduler, budget)
        self.secretary = Secretary(account.execution, scheduler)
        versions = VersionController(database, clock)
        versions.register_baseline(account.portfolio, "installed-desk-test", installed_artifacts())
        self.baseline = versions.current_hash(account.portfolio)
        self.runtime = ArtifactRuntime(versions, scheduler)
        config = RuntimeModelConfig(approved_price_card_ids=["scripted-desk"],
                                    role_routes={"trader": "scripted-desk"})
        self.assembly = assemble_handlers(self.office, self.secretary, SimpleNamespace(), self.runtime, config,
                                          workspace_root=workspace, api_keys={})
        self.handler = self.assembly.handlers["trader"]
        self.worker = RoleWorker(scheduler, owner=owner, system_version_id=self.baseline,
                                 reconcile=lambda: None, artifact_runtime=self.runtime)
        self.requests: list[ModelRequest] = []
        self.sizes: list[int] = []

        def complete(request: ModelRequest) -> ModelResult:
            self.requests.append(request)
            self.sizes.append(request_bytes(request, account.portfolio))
            return ModelResult(ok=True, payload=reply(request.context), provider_model="scripted",
                               usage=ModelUsage(uncached_input_tokens=0, billed_output_tokens=0,
                                                provider_request_id=f"scripted-desk-{len(self.requests)}"))

        self.assembly.gateway.scripted.complete = complete

    def task_settings(self, _portfolio_id: str, department: str) -> TaskSettings:
        """The desk's wake tasks run on the desk's artifact version; other departments as the engine's default."""
        if department == "trader":
            return TaskSettings(allocated_spend=Decimal("1"), max_attempts=1, expected_version=self.baseline)
        return TaskSettings()

    def run(self, lease) -> None:
        self.worker._run_lease(lease, {"trader": self.handler})

    @property
    def largest(self) -> int:
        return max(self.sizes, default=0)


__all__ = ["MAX_REQUEST_BYTES", "SCRIPTED_CARD", "SIDEWAYS_DIPS", "DeskRuntime", "insert_label", "insert_verdicts",
           "playbook", "request_bytes", "stance"]
