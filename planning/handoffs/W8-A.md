# W8-A C3: Perplexity inside the installed service

For the owner: When you switch it on at L3 step 12, the installed service can now send Research's morning coin pick
and its later looks, and the Leader's news rounds, to Perplexity through a separate network door that opens only to
Perplexity, with a receipt for every call and every web page saved before the AI reads it; with the switch off, as it
ships, nothing changes. It was tested only with a pretend Perplexity and a made-up key, so no real Perplexity call has
been made yet. At L3 you put your key and a copy of the price file into your protected owner folder and add the
Perplexity door to your network profile and proxy (the checklist gives each line), and at L2 the prices must be
re-checked because today's price card goes stale on 2026-11-11; nothing needs your decision now.

Branch: `claude/c3-perplexity-in-service`, head: the commit that adds this handoff's pull request link (code, tests
and docs at `641ec6f`). Pull request: PR_LINK_PLACEHOLDER.

## What was built

- `src/trade_graph/application/perplexity_installation.py` (new, protected path): Research and the Leader on
  Perplexity inside the installed subscription service.
  - `perplexity_departments(runtime, protected_owner, handlers, engine=...)`: called from `run_subscription_operation`'s
    `prepare` (one import, one line). With the event engine off, no subscription handlers, or the owner's switch off
    for both departments, it returns the handlers unchanged and reads, seeds and builds nothing. Otherwise it builds
    the route once and wraps each switched department in `SwitchedDepartment`: engine wakes go to W6-A's
    `FocusResearch` or W6-B's `LeaderNewsRounds` (with `enabled`/`spend_policy` reading W6-A's switch and
    `engine=service.event_engine`); every other task, and any task while the switch reads off, goes to the existing
    subscription handler. The route a turn took is fixed in its snapshot (`department_route`), and a round recorded
    earlier recovers through its own handler (answer, or `interrupted`). A `perplexity_route` activity event records
    `ready` or `not_ready` with the reason (no secret).
  - `assemble_perplexity_route`: reads the owner's `subscription-network-profile.json` (needs the reviewed
    `paid_research` route admitting both endpoints), `perplexity.key` (`read_owner_key`, through the protected
    owner-file reader, one name, 16+ visible ASCII characters, `SecretStr`) and `perplexity-price-cards.json`
    (`read_owner_price_cards`); seeds the cards (immutable ids), picks the newest effective card per endpoint
    (`select_cards`), and builds `PerplexityRouteGateway` on `PerplexityRouteBudget` with `pinned_transport(profile)`
    (`HttpxProviderHttp`: the two Perplexity endpoints only, proxy = the route's port, `trust_env=False`). Any missing
    or refused piece is a plain reason and nothing is built.
  - `PerplexityRouteBudget(BudgetGateway)`: reserves only for the route's deployment (`<deployment>:perplexity`) and
    cards, never synthetic, only for a leased task (`LEASED`/`RUNNING`) of a department the switch has on, and only on
    a fresh card (`card_problem`: stale 30 days after `verified_at`); refusals are `PaidCallsDisabled`, which T36's
    round records as `disabled`. `_assert_room` does not consult the deployment's EUR allowance (D243); the wake
    task's allocation still applies.
  - `PerplexityRouteGateway(ModelGateway)`: paid calls on, provider `perplexity` only, roles research/leader only, no
    fixtures, only the software `web_search` tool and handler; a bill whose resolved model differs from the card's
    model stays unresolved (`_usage_is_priced`, like `RuntimeGateway`).
- `src/trade_graph/kernel/subscription_proxy.py`: the reviewed catalogue `REVIEWED_ROUTE_HOSTS` (`paid_research`:
  `api.perplexity.ai`), `ConnectProxy(<route>, hosts=...)` (a subset of the catalogue, same admission checks as the
  other listeners), `reviewed_hosts`, `parse_route` and `--route NAME=PORT:HOST[,HOST...]` (distinct unprivileged
  ports, one listener per route, refused before any socket opens). `MARKET_HOSTS` and `PROVIDER_HOSTS` unchanged.
- `src/trade_graph/kernel/subscription_network.py`: `ReviewedRoute` and the optional
  `PreparedSubscriptionNetworkProfile.reviewed_routes` (catalogue names, catalogue hosts, own ports), omitted from the
  dump when empty so older profiles hash as before; `route_url`, `route_hosts`, `route_admits` (plain `https://host/`
  only).
- `src/trade_graph/application/budget.py`: `DuplicateProviderRequest(ValueError)`; `BudgetGateway.commit` refuses a
  request id that already has a receipt for another reservation before writing anything.
  `src/trade_graph/application/web_research.py`: a search answer that repeats an id is settled unresolved
  (`uncertain`, `duplicate_request_id`) and its results are neither stored nor shown. The model gateway already
  settles the `ValueError` as unresolved (`UNCERTAIN`).
- `src/trade_graph/kernel/authority.py`: `application/perplexity_installation.py`, `application/price_collector.py`
  and `application/service_market_data.py` appended to `PROTECTED_PATH_PREFIXES`.
- `src/trade_graph/application/subscription_operation.py`: `prepare` returns `perplexity_departments(...)`.
- Docs: `docs/PERPLEXITY-IN-SERVICE.md` (new), `docs/REVIEWED-NETWORK-ROUTES.md` (new: the route design and how W9-A
  adds a second list), `docs/L3-INSTALL-CHECKLIST.md` (step 3's owner files and route, step 12 no longer conditional,
  step 13's `perplexity_route` check, the prerequisites section rewritten as done or owner actions, the price
  re-check for L2), short updates to `docs/FOCUS-RESEARCH.md`, `docs/LEADER-NEWS-ROUNDS.md`,
  `docs/PERPLEXITY-RESEARCH.md`.
- Tests (new): `tests/integration/test_receipt_duplicate_request_id.py`, `tests/security/test_reviewed_routes.py`,
  `tests/integration/test_perplexity_installation.py`, `tests/integration/test_perplexity_in_service.py` (the
  acceptance day), `tests/unit/test_perplexity_installation_paths.py`; helper `tests/perplexity_service_support.py`.
  Changed: `tests/integration/test_owned_subscription_service.py` (`subscription_service(..., source=GRAPH)`, an
  optional owner graph; default unchanged).

## The reviewed route, for W9-A

A reviewed route is a named, owner-listed host list on its own proxy port: code holds the reviewed catalogue
(`REVIEWED_ROUTE_HOSTS` in `kernel/subscription_proxy.py`), the owner's network profile lists the route's port and
hosts (a subset), the proxy opens one listener per `--route`, and the caller's transport uses only
`profile.route_url(name)` and refuses any URL `profile.route_admits(name, url)` rejects. W9-A adds
`"news_feeds": frozenset({<five feed hosts>, "api.typesafe.ai"})` to the catalogue and builds its transport like
`perplexity_installation.pinned_transport`; nothing else in the proxy or profile changes
(`docs/REVIEWED-NETWORK-ROUTES.md`).

## Decisions (reserved block D240–D244)

- **D240 (2026-10-17, implementation default for C3): Perplexity departments in the installed service.** With the
  event engine on and the owner's switch on, `run_subscription_operation` hands a switched department's engine wakes
  to W6-A's `FocusResearch` (Research) or W6-B's `LeaderNewsRounds` (the Leader), built in the protected parent
  process; the mutable graph child never sees a key, route or price. Tasks the engine did not make (the Secretary's
  digests, owner requests) stay on the subscription handler (as D210 for the Leader), and so does every task while the
  switch reads off (fail closed). With the switch off for both, or the engine off, the handlers are returned unchanged
  and nothing is read, seeded or built, so the service runs exactly as before; `paper-config.json` still refuses
  `models` and `price_cards`. A turn's route is fixed in its snapshot. If a prerequisite is missing, a switched
  department's wake fails with the reason and nothing is sent; it does not fall back to the subscription silently (the
  owner asked for Perplexity, and a silent fallback would hide a broken install); the focus step is then recorded
  failed and the Trader is still woken (D208, D223). Reason: the W8-A package bullets, D124, D205, D210, rule 7.
- **D241 (2026-10-17, implementation default for C3): separately reviewed network routes.** The subscription proxy
  gains one listener per reviewed route, each admitting only owner-listed hosts from a reviewed catalogue in code
  (`REVIEWED_ROUTE_HOSTS`; `paid_research` = `api.perplexity.ai`), with the same CONNECT, DNS, TLS-name and size
  checks as the provider and market listeners, apart from `MARKET_HOSTS` (Kraken, Frankfurter), which is unchanged.
  The owner's protected network profile lists each route's port and hosts (`reviewed_routes`, optional, omitted when
  empty so older profiles load and hash as before); the transport sends only to fixed URLs the route admits, through
  that port only, without environment proxies. A second list (W9-A: news feeds and TypeSafe) is one catalogue entry
  plus the owner's profile and proxy lines. Reason: the package's "separately reviewed route ... nothing reaches a
  host the owner has not listed", D150, D154, FUNDED-PAPER-PROFILE.md's owner-reviewed proxy.
- **D242 (2026-10-17, implementation default for C3): the key and the price cards come from the owner's folder.** The
  key is `<owner>/perplexity.key` (`root:10001`, mode `0440`, like `capability.key`), read only by the protected
  owner-file reader, one name, held as `SecretStr`, sent only as the `Authorization` header (D154 inside the image:
  `read_private_key`'s "owned by the reading user" rule cannot hold for a root-owned read-only mount, so the protected
  reader's root-owned, not-other-readable, no-link rule replaces it). The cards are `<owner>/perplexity-price-cards.json`,
  a copy of `config/perplexity-price-cards.json`, seeded as immutable records (an edited card is refused); the newest
  effective card per endpoint is used, so a re-checked dated card needs no code change. A card is stale 30 days after
  its `verified_at` (the 2026-10-12 Agent card on 2026-11-11, as the plan and W6-B state; one day stricter than the
  API model router's "more than 30 days") and then refuses paid calls; it never prices one as free. The cloud could
  not open docs.perplexity.ai on 2026-10-17 (proxy 403, DNS failure), so no new card was added and the re-check is
  listed for step L2. Reason: AGENTS.md (prices are dated configured records), D151, D154, the package bullets.
- **D243 (2026-10-17, implementation default for C3): money on the installed route.** Every agent step and search
  reserves first and ends with a receipt or stays unresolved (T36). The owner's Perplexity spend rule is the money
  rule (D125 testing mode by default; D138 item 4's monthly limit pauses only these rounds); the deployment's EUR
  allowance, unused by a subscription installation, is not consulted, so it cannot block the testing period; the wake
  task's allocation (the owner's root paid limit, W6-A) still bounds one round. Reservations carry their own
  deployment id, `<deployment>:perplexity`: the protected subscription deployment's financial witness requires a
  protected budget origin for each of its reservations and re-verifies all of them, and re-scanning the portfolio's
  books on every Perplexity dispatch and settlement would not scale; HQ's AI spending reads Perplexity by price card,
  so it shows them as before. Reservations are admitted only for a leased task of a switched department, on the route's
  fresh cards, never synthetic. USD amounts are held in EUR at 1:1 plus the 2% buffer (no FX quote is configured for
  this route; conservative), receipts keep the USD cost. A provider request id that repeats an earlier receipt is a
  typed refusal (`DuplicateProviderRequest`), settled unresolved; its answer is not used. Reason: AGENTS.md (every
  paid call reserved and receipted; unknown usage unresolved, not free), D125, D138 item 4, D152, D153, W1-C's finding.
- **D244 (2026-10-17, implementation default for C3, the plan's recommended default): protected paths.**
  `application/price_collector.py` and `application/service_market_data.py` (they feed the coin rule's hourly scan
  and the price alarms) and the new `application/perplexity_installation.py` (key, prices, the route's money rule,
  which wakes go where) are added to `PROTECTED_PATH_PREFIXES`; the proxy catalogue and the network profile are already
  under `kernel/`. Reason: the package bullet (W6-D left it open), AGENTS.md (protected owner policy, accounting and
  receipt collection are not agent-editable).

## Evidence

All runs on CPython 3.12.13 (`uv sync --frozen --group dev --python 3.12`) in this cloud container, in
`/home/user/c3-perplexity-in-service`, shared with two other builders (4 CPUs). No network, key, paid, model or
exchange call in any test; every Perplexity answer is synthetic, played by a scripted transport with a made-up key.

- Tests written first:
  - `tests/integration/test_receipt_duplicate_request_id.py`: **3 failed** before the fix, each with
    `sqlite3.IntegrityError: UNIQUE constraint failed: usage_receipts.provider, usage_receipts.provider_request_id`
    (direct commit, an agent answer, a search answer); 3 passed after.
  - `tests/security/test_reviewed_routes.py`: failed at collection (`ImportError: cannot import name 'PAID_RESEARCH'`);
    46 passed after.
  - `tests/integration/test_perplexity_installation.py`: failed at collection (`ImportError: cannot import name
    'perplexity_installation'`); passes now. Mutation checks, each restored: consulting the EUR allowance, stale one
    day later, not checking the switch, not checking the lease each fail at least one test.
  - `tests/integration/test_perplexity_in_service.py`: with the module but without the one-line wiring in
    `run_subscription_operation`, the switch-on and no-key days **failed** (`assert [] == ['http://172.29.17.2:8082']`)
    while the switch-off day passed; 4 passed after.
  - `tests/unit/test_perplexity_installation_paths.py`: 3 failed (the three new paths not protected); 5 passed after.
- `uv run ruff check src tests`: all checks passed (also before each commit).
- New tests: `uv run pytest -p no:cacheprovider -rfs tests/integration/test_receipt_duplicate_request_id.py
  tests/security/test_reviewed_routes.py tests/integration/test_perplexity_installation.py
  tests/integration/test_perplexity_in_service.py tests/unit/test_perplexity_installation_paths.py` at `641ec6f`:
  **87 passed, 0 failed, 0 skipped** (50 s; 3 + 46 + 29 + 4 + 5).
- Related tests: every test module that mentions the subscription service, process boundary, network profile or
  proxy, the deployment image, protected paths, the budget gateway and receipts, Perplexity, web research, focus
  research, Leader news rounds, the paper service and scripted days, migrations, wire schemas, the model gateway,
  the event engine, the price collector or the Engineer (`grep -lE` over `tests`: 210 of 368 modules, including
  `tests/integration/test_provider_wire_schemas.py`, `test_subscription_process_boundary.py`,
  `test_protected_process_boundary.py`, every `test_migration_00xx.py`, `test_scripted_day.py`, `test_paper_service.py`
  and all new modules), in two shards of whole modules run side by side without parallel plugins
  (`uv run pytest -p no:cacheprovider -rfs --junitxml=... <modules>`) at `641ec6f`: **3,904 tests: 3,889 passed,
  0 failed, 15 skipped** (shard 1: 1,702 passed in 24 min; shard 2: 2,187 passed, 15 skipped in 32 min). Skips: 14 in
  `tests/security/test_subscription_process_boundary.py` (bubblewrap is not installed in the cloud container, as on
  GitHub) and 1 archive-permission case (this user can read any file). One warning: an existing archive test's
  duplicate zip name. Also `uv run pytest -p no:cacheprovider -rfs tests/unit`: **2,275 passed, 0 failed,
  7 skipped** (33 s; the indicator cross-checks the second library cannot make and the archive-permission cases).
  After `641ec6f` only this handoff and one checklist sentence changed.
- `python3 scripts/check_plan.py`: plan valid, 46 tasks, acyclic; `python3 scripts/test_planning.py`: 11 tests OK;
  `python3 scripts/check_repository_hygiene.py` (and `--staged` before every commit): passed.
- Full suite: not run (night shift: the wave integration runs it).
- Real or synthetic: everything is synthetic and labelled: the RuntimeFlow and day-plan accounts, a live coin scan
  written straight into migration 0031, the owner's folder served from a dictionary (a synthetic network profile, a
  made-up key, the repository's price cards, re-dated under `synthetic-` ids for the 2026-01-01 test clock), Agent
  and Search API answers in the documented shapes, a synthetic subscription CLI. No Perplexity, model, exchange or
  other paid call and no order; a scripted Perplexity is never a paid-provider test. The only network attempt was a
  plain fetch of Perplexity's public pricing page (refused: proxy 403, DNS failure).

## Schema and dependencies

- State migration: **none** (0043 unused): the route uses T36's, W6-A's and W6-B's tables. `adapters/persistence/
  migrate.py` is not touched. Candle database: no change.
- Dependencies added: none (`pyproject.toml` and `uv.lock` unchanged).

## Not done, open or for someone else

- **Step L2 (owner, local session):** the opt-in real smoke (`trade-graph perplexity-smoke --confirm-paid-call`) and
  the price re-check on Perplexity's official pricing page. If a price differs, or to renew the 2026-10-12 card
  before 2026-11-11, add a new dated card to `config/perplexity-price-cards.json` (never edit one).
- **Step L3 (owner):** the owner files and route of step 3 (`perplexity.key`, `perplexity-price-cards.json`,
  `reviewed_routes.paid_research` in the network profile, `--route paid_research=8082:api.perplexity.ai` on the
  reviewed proxy and its new review), then step 12's `--confirm`; HQ's activity log then shows `perplexity_route`
  `ready`. The real proxy container and the profile were not run here (no Docker).
- **Shared files for the integrator (keep both sides; this package merges last):** `kernel/authority.py` (three
  lines appended to `PROTECTED_PATH_PREFIXES`), `application/budget.py` (a class and a check in `commit`),
  `application/web_research.py` (an import and a `try` around `_settle`), `application/subscription_operation.py` (an
  import and `prepare`'s return), `kernel/subscription_proxy.py`, `kernel/subscription_network.py`,
  `tests/integration/test_owned_subscription_service.py` (an optional `source` argument), `docs/L3-INSTALL-CHECKLIST.md`,
  `docs/FOCUS-RESEARCH.md`, `docs/LEADER-NEWS-ROUNDS.md`, `docs/PERPLEXITY-RESEARCH.md`. Copy D240–D244 into
  `docs/DECISIONS.md`. `migrate.py` is untouched and 0043 is unused.
- **Found, not changed (integrator or owner):** `application/budget.py` and `application/gateway.py` (reservations
  and receipts) are not protected paths, although AGENTS.md says receipt collection is not agent-editable. The
  installed route's reservations are outside the protected financial witness by design (D243); binding them to
  protected budget origins would need a verifier that does not re-scan every reservation on each call (relevant to
  W9-A's "bind Jev checks to protected budget origins"). `run_subscription_operation`'s result keeps
  `paid_authorization: False` and `billing_kind: "subscription"` (they describe API model routing); the Perplexity
  route's state is in the `perplexity_route` activity event. A transport that raised an unexpected exception type
  (not a timeout, HTTP or value error) would leave its reservation `RESERVED` (held at its maximum, counted unresolved)
  and fail the task as a software error; the real transport raises only the typed ones. Perplexity departments still
  wait under the AI allowance floor (W6-B's note, "Later, not packaged").
- **W9-A:** add the news feeds and TypeSafe as a second reviewed route as described above.
