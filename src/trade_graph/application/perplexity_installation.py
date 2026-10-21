"""Research and the Leader on Perplexity inside the installed subscription service (W8-A; D240-D244).

The installed service (``run_subscription_operation``) builds every department from the owner's subscription profile
and refuses separately billed API configuration. This module adds, in the protected parent process and only when the
owner's switch (``perplexity_enabled_for``, W6-A) has a department on and the event engine runs, the two Perplexity
departments: W6-A's ``FocusResearch`` for Research's engine wakes and W6-B's ``LeaderNewsRounds`` for the Leader's.
Everything a department does outside its engine wakes, and everything while the switch is off, stays on the
subscription exactly as before; the mutable graph child never sees a key, a route or a price.

The paid route has four protected parts, all from the owner's root-owned folder or this package:

1. **Network.** The owner's ``subscription-network-profile.json`` lists the reviewed route ``paid_research``
   (``kernel/subscription_proxy.py``: ``api.perplexity.ai`` only) with its own proxy port. The transport sends only to
   Perplexity's two fixed endpoints, through that port, with no environment proxy; the proxy admits nothing else.
2. **Key.** ``perplexity.key`` in the owner folder, read through the protected owner-file reader (root-owned, not
   readable by others, no link, bounded; D154), held masked and sent only as the ``Authorization`` header.
3. **Prices.** ``perplexity-price-cards.json`` in the owner folder (a copy of the repository's dated
   ``config/perplexity-price-cards.json``), seeded into the database as immutable cards. The newest effective card per
   endpoint is used; a card last verified 30 or more days ago is stale and refuses paid calls, never prices them free.
4. **Money.** Every agent step and search reserves first and ends with a receipt or stays unresolved (T36). The owner's
   Perplexity spend rule (D125 testing mode, or D138 item 4's monthly limit) is the money rule; the deployment's EUR
   allowance, which a subscription installation does not use, is not consulted. Each wake task keeps the owner's paid
   root allocation (W6-A). Reservations carry their own route deployment id, so they stay apart from the protected
   subscription deployment's books, and each one is bound to an authenticated protected budget origin in its own
   transaction (W10-A, D265, like the news route's Jev checks): dispatch, settlement, commit and allocation validate
   it, a receipt seals it with a full allocation to the portfolio, and the route's history is audited, bounded,
   before each round. A missing or forged origin refuses the round with the reason; its hold stays unresolved.

If anything is missing, a switched-on department's engine wake fails visibly with the reason and nothing is sent; the
Trader, protection and records continue.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from pydantic import SecretStr, ValidationError

from trade_graph.adapters.models.perplexity import AGENT_ENDPOINT, SEARCH_TOOL_NAME, PerplexityAgentAdapter
from trade_graph.adapters.models.transport import HttpxProviderHttp
from trade_graph.adapters.persistence.db import atomic
from trade_graph.adapters.research.perplexity_search import SEARCH_ENDPOINT
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.gateway import ModelGateway
from trade_graph.application.perplexity_switch import perplexity_enabled_for, perplexity_setting
from trade_graph.application.web_research import PerplexityRoute
from trade_graph.contracts.models import ModelRequest, ModelResult, PriceCard
from trade_graph.contracts.perplexity_switch import PERPLEXITY_DEPARTMENTS
from trade_graph.domain.errors import AuthorityDenied, PaidCallsDisabled, StaleState, TradeGraphError
from trade_graph.kernel.protected_budget import ProtectedBudgetOrigins, ProtectedRouteBudget

KEY_FILE: Final = "perplexity.key"
PRICE_CARD_FILE: Final = "perplexity-price-cards.json"
ROUTE: Final = "paid_research"
ROUTE_KIND: Final = "perplexity_route"
ENDPOINTS: Final = (AGENT_ENDPOINT, SEARCH_ENDPOINT)
STALE_AFTER_DAYS: Final = 30
TRANSPORT_TIMEOUT_SECONDS: Final = 120
_ACTIVE_TASKS: Final = ("LEASED", "RUNNING")


def route_deployment(deployment_id: str) -> str:
    """The deployment id of the installed Perplexity route's reservations, apart from the subscription deployment."""
    return f"{deployment_id}:perplexity"


# --- the owner's files ----------------------------------------------------------------------------------------------

def read_owner_key(directory: Path) -> SecretStr:
    """The Perplexity key from the owner's protected folder (D154). The error never includes the file's content."""
    from trade_graph.kernel import deployment_image

    raw = deployment_image.read_owner_file(directory, KEY_FILE, 4096)
    try:
        info = os.stat(Path(directory) / KEY_FILE, follow_symlinks=False)
    except OSError:
        info = None  # the protected reader already opened it without following links
    if info is not None and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        raise PermissionError("the Perplexity key must be one regular file with a single name")
    text = raw[:-2] if raw.endswith(b"\r\n") else raw[:-1] if raw.endswith(b"\n") else raw
    if not 16 <= len(text) <= 4096 or any(byte < 33 or byte > 126 for byte in text):
        raise ValueError("the Perplexity key file must hold one key of 16 or more visible ASCII characters")
    return SecretStr(text.decode("ascii"))


def read_owner_price_cards(directory: Path) -> list[PriceCard]:
    """The dated Perplexity price cards the owner installed (a copy of ``config/perplexity-price-cards.json``)."""
    from trade_graph.kernel import deployment_image

    document = json.loads(deployment_image.read_owner_file(directory, PRICE_CARD_FILE, 65536))
    if (type(document) is not dict or document.get("schema_version") != 1
            or type(document.get("price_cards")) is not list):
        raise ValueError("the price-card file needs schema_version 1 and a price_cards list")
    return [PriceCard.model_validate(item) for item in document["price_cards"]]


def card_problem(card: PriceCard, today: date) -> str | None:
    """Why a dated card cannot price a paid call today, or None. Stale is refused, never priced as free."""
    try:
        verified, effective = date.fromisoformat(card.verified_at), date.fromisoformat(card.effective_at)
    except ValueError:
        return f"the price card {card.price_card_id} has unreadable dates"
    if not card.source_id or card.tier != "standard":
        return f"the price card {card.price_card_id} has no source or is not a standard-tier card"
    if effective > today or verified > today:
        return f"the price card {card.price_card_id} is not effective yet"
    stale_on = verified + timedelta(days=STALE_AFTER_DAYS)
    if today >= stale_on:
        return (f"the price card {card.price_card_id} was last checked against Perplexity's pricing on "
                f"{verified.isoformat()} and went stale on {stale_on.isoformat()}; paid calls are refused until a "
                "re-checked dated card is installed")
    return None


def select_cards(cards: list[PriceCard], today: date) -> tuple[PriceCard, PriceCard]:
    """The newest effective Agent API card for an approved model and the newest effective Search API card."""
    approved = PerplexityAgentAdapter()

    def newest(endpoint: str, fits: Callable[[PriceCard], bool]) -> PriceCard:
        usable = [card for card in cards if card.provider == "perplexity" and card.endpoint == endpoint
                  and card.currency == "USD" and fits(card) and _effective(card, today)]
        if not usable:
            raise ValueError(f"no effective Perplexity price card for {endpoint}")
        return max(usable, key=lambda card: (card.verified_at, card.effective_at, card.price_card_id))

    agent = newest(AGENT_ENDPOINT, lambda card: approved.capabilities(card.model) is not None)
    search = newest(SEARCH_ENDPOINT, lambda card: card.search_per_call is not None)
    return agent, search


def _effective(card: PriceCard, today: date) -> bool:
    try:
        return date.fromisoformat(card.effective_at) <= today
    except ValueError:
        return False


def pinned_transport(profile) -> HttpxProviderHttp:
    """The real transport: Perplexity's two fixed endpoints only, through the reviewed route's proxy port, with no
    environment proxy. Built only for a switched-on department with every prerequisite in place."""
    return HttpxProviderHttp(timeout=TRANSPORT_TIMEOUT_SECONDS, proxy=profile.route_url(ROUTE), trust_env=False,
                             allowed_urls=ENDPOINTS)


# --- the route's money and requests ---------------------------------------------------------------------------------

class PerplexityRouteBudget(ProtectedRouteBudget):
    """Reservations of the installed route. Each one is admitted only for a leased engine task of a department the
    owner switched on, on the route's fresh cards and deployment, never as a synthetic (unbilled) call, while the
    protected controller runs; and each one is bound to its authenticated protected budget origin in the same
    transaction (W10-A, D265; ``kernel.protected_budget.ProtectedRouteBudget``, shared with the news route)."""

    label = "Perplexity route"
    waiting = "Perplexity rounds"

    def __init__(self, database, clock, *, deployment_id: str, origins: ProtectedBudgetOrigins, controller) -> None:
        super().__init__(database, clock, deployment_id=deployment_id, origins=origins, controller=controller)
        self.card_ids: frozenset[str] = frozenset()

    @atomic
    def reserve(self, **kwargs: Any) -> str:
        portfolio_id = self.admit(**kwargs)
        reservation_id = super().reserve(**kwargs)
        # The original authority is retained in the same transaction as the reservation and its journal rows.
        self._bind_origin(reservation_id, portfolio_id)
        return reservation_id

    def admit(self, *, deployment_id: str, role: str, task_id: str | None, price_card_id: str, synthetic: bool,
              **_: Any) -> str:
        """Refuse before anything is reserved or sent; returns the leased task's portfolio."""
        if deployment_id != self.deployment_id or price_card_id not in self.card_ids:
            raise PaidCallsDisabled("only the installed Perplexity route's deployment and price cards may reserve")
        if synthetic:
            raise PaidCallsDisabled("the installed Perplexity route never records a call as synthetic")
        if role not in PERPLEXITY_DEPARTMENTS or not perplexity_enabled_for(self.database, role):
            raise PaidCallsDisabled(f"{role} is not switched to Perplexity by the owner")
        task = self.database.execute("SELECT role, status, portfolio_id FROM tasks WHERE task_id = ?",
                                     (task_id,)).fetchone()
        if task is None or task["role"] != role or task["status"] not in _ACTIVE_TASKS:
            raise PaidCallsDisabled("a Perplexity call belongs to a leased task of its own department")
        problem = card_problem(self.card(price_card_id), self.clock.now().date()) or self.controller_problem()
        if problem is not None:
            raise PaidCallsDisabled(problem)
        return task["portfolio_id"]

    @atomic
    def commit(self, reservation_id, usage, **kwargs):
        """The receipt seals the origin with a full allocation to the origin's portfolio, recorded here in the same
        transaction: T36's rounds settle searches and agent steps without allocating them themselves."""
        receipt = super().commit(reservation_id, usage, **kwargs)
        origin = self.origins._load(reservation_id, "ORIGIN")
        self.allocate(receipt, {origin["portfolio_id"]: Decimal("1")})
        return receipt

    def _assert_room(self, conn, **_: Any) -> None:
        """The owner's Perplexity spend rule (D125 testing mode, D138 item 4's monthly limit, enforced per call by
        ``PerplexitySpendGuard``) is the money rule of this route, not the deployment's EUR allowance. The wake task's
        allocation (``_assert_task_room``) still applies."""
        return None


class PerplexityRouteGateway(ModelGateway):
    """The paid-enabled gateway of the installed route: Perplexity only, the software-run ``search_the_web`` only."""

    def __init__(self, budget: PerplexityRouteBudget, *, transport, key: SecretStr) -> None:
        super().__init__(budget, paid_calls_enabled=True, transport=transport,
                         api_keys={"perplexity": key.get_secret_value()}, enabled_providers={"perplexity"})

    def invoke(self, request: ModelRequest, **kwargs: Any) -> ModelResult:
        if (request.provider != "perplexity" or request.synthetic or request.role not in PERPLEXITY_DEPARTMENTS
                or "http_fixture" in request.context or "scripted_result" in request.context):
            raise AuthorityDenied("the installed Perplexity route carries only Research's and the Leader's rounds")
        tools = request.context.get("tools") or []
        names = {(item.get("function", item) if isinstance(item, dict) else {}).get("name") for item in tools}
        handlers = set(kwargs.get("tool_handlers") or {})
        if not names <= {SEARCH_TOOL_NAME} or not handlers <= {SEARCH_TOOL_NAME}:
            raise AuthorityDenied("the installed Perplexity route runs only the software search_the_web tool")
        return super().invoke(request, **kwargs)

    def _usage_is_priced(self, request: ModelRequest, result: ModelResult) -> bool:
        # A bill for another model than the card's is unresolved, never priced on the wrong card.
        return result.provider_model is None or result.provider_model == request.model


# --- assembly -------------------------------------------------------------------------------------------------------

@dataclass
class PerplexityInstallation:
    """The installed route, or the plain reason it is not ready (then nothing is built and nothing is sent)."""

    problem: str | None
    route: PerplexityRoute | None = None
    gateway: PerplexityRouteGateway | None = None
    cards: tuple[PriceCard, ...] = field(default=())

    def problem_now(self, today: date) -> str | None:
        if self.problem is not None:
            return self.problem
        for card in self.cards:
            problem = card_problem(card, today)
            if problem is not None:
                return problem
        return None

    def round_problem(self, now: datetime) -> str | None:
        """Why the next round cannot run now: the reason from the start, a stale card, the protected controller, or
        the route's bounded audit of its protected budget origins, run before each round (W10-A, D265)."""
        problem = self.problem_now(now.date())
        if problem is not None or self.gateway is None:
            return problem or "the installed Perplexity route was not built"
        budget = self.gateway.budget
        problem = budget.controller_problem()
        if problem is None:
            try:
                budget.audit_at(now)
            except (StaleState, sqlite3.DatabaseError) as error:
                problem = f"the Perplexity route's protected budget origins were refused ({error})"
        return None if problem is None else problem[:500]

    def status(self, today: date) -> dict[str, Any]:
        problem = self.problem_now(today)
        return {"status": "ready" if problem is None else "not_ready", "reason": problem,
                "route": ROUTE, "deployment_id": None if self.route is None else self.route.deployment_id,
                "price_card_ids": [card.price_card_id for card in self.cards]}


def assemble_perplexity_route(database, clock, owner_directory: Path, *, deployment_id: str, protected=None,
                              transport_factory: Callable[[Any], Any] | None = None) -> PerplexityInstallation:
    """Build the route from the owner's folder. Each missing prerequisite is a plain reason; no secret is in it.

    ``protected`` is the protected runtime the installed service prepared at start (its financial service and
    controller): the route's reservations are bound to its protected budget origins, so without it nothing is built."""
    from trade_graph.kernel import subscription_network

    try:
        profile = subscription_network.load_subscription_network_profile(owner_directory)
    except (OSError, ValueError, ValidationError) as error:
        return PerplexityInstallation(f"the owner's network profile can't be read ({type(error).__name__})")
    if ROUTE not in profile.reviewed_routes:
        return PerplexityInstallation("the owner's network profile lists no reviewed paid_research route for "
                                      "api.perplexity.ai")
    if not all(profile.route_admits(ROUTE, url) for url in ENDPOINTS):
        return PerplexityInstallation("the reviewed paid_research route does not admit both Perplexity endpoints")
    try:
        key = read_owner_key(owner_directory)
    except FileNotFoundError:
        return PerplexityInstallation(f"no Perplexity key: the owner folder has no {KEY_FILE}")
    except (OSError, ValueError) as error:
        return PerplexityInstallation(f"the Perplexity key file is refused ({type(error).__name__})")
    try:
        cards = read_owner_price_cards(owner_directory)
    except FileNotFoundError:
        return PerplexityInstallation(f"no Perplexity price cards: the owner folder has no {PRICE_CARD_FILE}")
    except (OSError, ValueError, ValidationError) as error:
        return PerplexityInstallation(f"the Perplexity price-card file is refused ({type(error).__name__})")
    if protected is None:
        return PerplexityInstallation("the protected runtime is not prepared; Perplexity rounds need its protected "
                                      "budget origins")
    scope = route_deployment(deployment_id)
    try:
        budget = PerplexityRouteBudget(database, clock, deployment_id=scope, controller=protected.controller,
                                       origins=ProtectedBudgetOrigins(protected.financial, deployment_id=scope))
    except (ValueError, AttributeError) as error:
        return PerplexityInstallation(f"the Perplexity route's protected budget origins are refused ({error})"[:500])
    try:
        with database.immediate():
            for card in cards:
                budget.seed_card(card)
        agent, search = select_cards(cards, clock.now().date())
    except (ValueError, TradeGraphError) as error:
        return PerplexityInstallation(f"the Perplexity price cards are refused: {error}"[:500])
    budget.card_ids = frozenset({agent.price_card_id, search.price_card_id})
    try:
        budget.audit_at(clock.now())
    except (StaleState, sqlite3.DatabaseError) as error:
        return PerplexityInstallation(f"the Perplexity route's protected budget origins were refused ({error})"[:500])
    transport = (transport_factory or pinned_transport)(profile)
    gateway = PerplexityRouteGateway(budget, transport=transport, key=key)
    route = PerplexityRoute(deployment_id=budget.deployment_id, agent_price_card_id=agent.price_card_id,
                            search_price_card_id=search.price_card_id)
    return PerplexityInstallation(None, route=route, gateway=gateway, cards=(agent, search))


# --- the departments ------------------------------------------------------------------------------------------------

def _is_wake(task: Mapping[str, Any]) -> bool:
    payload = task.get("input")
    return isinstance(payload, dict) and isinstance(payload.get("wake"), dict)


class SwitchedDepartment:
    """One department the owner switched to Perplexity: its engine wakes run on Perplexity; its other tasks, and every
    task while the switch reads off, stay on the subscription handler. A turn's route is fixed in its snapshot."""

    manages_attempts = False

    def __init__(self, role: str, *, perplexity, subscription, installation: PerplexityInstallation,
                 enabled: Callable[[], bool], clock, record: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.role, self.perplexity, self.subscription = role, perplexity, subscription
        self.installation, self.enabled, self.clock, self.record = installation, enabled, clock, record

    def _paid(self, task: Mapping[str, Any]) -> bool:
        if not _is_wake(task):
            return False
        try:
            return self.enabled() is True
        except Exception:  # noqa: BLE001 - an unreadable switch never turns a paid route on
            return False

    def _problem(self) -> str | None:
        return self.installation.problem_now(self.clock.now().date())

    def _refused(self, task: Mapping[str, Any], problem: str) -> None:
        """The activity log names a round refused at its start (the reason from the start is already there)."""
        if self.record is None:
            return
        try:
            self.record({"status": "refused", "reason": problem, "route": ROUTE, "department": self.role,
                         "task_id": task.get("task_id")})
        except Exception:  # noqa: BLE001 - recording the reason is best effort; the wake still fails with it
            pass

    def recover(self, task: dict) -> dict | None:
        if _is_wake(task):
            recovered = self.perplexity.recover(task)  # a round already recorded: its answer, or "interrupted"
            if recovered is not None:
                return recovered
        hook = getattr(self.subscription, "recover", None)
        return hook(task) if hook is not None else None

    def context(self, task: dict) -> dict:
        if not self._paid(task):
            hook = getattr(self.subscription, "context", None)
            return hook(task) if hook is not None else {"input": task["input"]}
        problem = self._problem()
        if problem is not None:
            return {"department_route": "perplexity", "input": task["input"], "perplexity_not_ready": problem}
        return {**self.perplexity.context(task), "department_route": "perplexity"}

    def __call__(self, task: dict) -> dict:
        snapshot = task.get("snapshot") or {}
        if snapshot.get("department_route") != "perplexity":
            return self.subscription(task)
        problem = snapshot.get("perplexity_not_ready")
        if problem is None:
            problem = self.installation.round_problem(self.clock.now())
            if problem is not None:
                self._refused(task, problem)
        if problem is not None:
            name = "Research" if self.role == "research" else "The Leader"
            return {"_status": "FAILED", "failure_scope": "turn",
                    "reason": (f"{name} is switched to Perplexity, but its installed route is not ready: {problem}. "
                               "Nothing was sent; the Trader, protection and records continue.")[:900]}
        return self.perplexity(task)


def perplexity_departments(runtime, protected_owner: Path, handlers: Mapping[str, Any], *, engine,
                           transport_factory: Callable[[Any], Any] | None = None) -> dict[str, Any]:
    """The installed service's handlers with Research and the Leader on Perplexity where the owner switched them on.

    With the event engine off, no subscription handlers, or the switch off for both departments, the handlers come
    back unchanged and nothing is read, seeded or built. The switch cannot change while the service runs (W6-A's
    command refuses), and each task still reads it afresh."""
    from trade_graph.application.focus_research import FocusResearch
    from trade_graph.application.leader_news import LeaderNewsRounds
    from trade_graph.application.web_research import PerplexityWebResearch

    result = dict(handlers)
    if engine is None or not handlers:
        return result
    database = runtime.database
    switched = [role for role in PERPLEXITY_DEPARTMENTS if role in handlers and perplexity_enabled_for(database, role)]
    if not switched:
        return result
    protected = getattr(getattr(runtime, "protected_deployment", None), "protected", None)
    installation = assemble_perplexity_route(database, runtime.clock, protected_owner,
                                             deployment_id=runtime.deployment_id, protected=protected,
                                             transport_factory=transport_factory)

    def record(payload: dict[str, Any]) -> None:
        runtime.ledger._activity(runtime.portfolio_id, ROUTE_KIND, payload)

    # Not ready: the handlers still recover rounds recorded earlier (an unfinished one becomes "interrupted"), but
    # their gateway has paid calls off and a wake fails with the reason before anything is built or sent.
    gateway = installation.gateway or ModelGateway(BudgetGateway(database, runtime.clock), paid_calls_enabled=False)
    route = installation.route or PerplexityRoute(deployment_id=route_deployment(runtime.deployment_id))
    for role in switched:
        if role == "research":
            perplexity = FocusResearch(runtime.execution, gateway=gateway, route=route)
        else:
            perplexity = LeaderNewsRounds(
                PerplexityWebResearch(gateway, route=route, policy=None),
                enabled=lambda: perplexity_enabled_for(database, "leader"),
                spend_policy=lambda: perplexity_setting(database).spend_policy, engine=engine)
        result[role] = SwitchedDepartment(role, perplexity=perplexity, subscription=handlers[role],
                                          installation=installation, clock=runtime.clock, record=record,
                                          enabled=lambda role=role: perplexity_enabled_for(database, role))
    record({**installation.status(runtime.clock.now().date()), "departments": switched})
    return result


__all__ = ["ENDPOINTS", "KEY_FILE", "PRICE_CARD_FILE", "ROUTE", "ROUTE_KIND", "STALE_AFTER_DAYS",
           "PerplexityInstallation", "PerplexityRouteBudget", "PerplexityRouteGateway", "SwitchedDepartment",
           "assemble_perplexity_route", "card_problem", "perplexity_departments", "pinned_transport", "read_owner_key",
           "read_owner_price_cards", "route_deployment", "select_cards"]
