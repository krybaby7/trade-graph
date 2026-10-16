# W1-C T36: Perplexity web research connection with search evidence and receipts

For the owner: Research and the Leader now have a tested connection to Claude
Sonnet 5.5 through Perplexity: the software runs each web search itself, saves
every result before the AI reads it, throws out any finding that points
somewhere else, and writes a receipt for every call. Nothing real has been
called yet, Research and the Leader are not switched over yet, and Perplexity's
own pages were blocked from the cloud, so the price of a search still needs
checking. On your PC (step L2), one command makes a single real test call with
your test key and shows its receipt; while testing there is no spending limit,
and a monthly limit you set later pauses only this research, never the Trader.

Branch: `claude/blissful-cray-21g9y3` (this session's branch; the plan names
`claude/t36-perplexity`), head `HEAD_COMMIT`, based on
`claude/day-trader-engine-20261012` at `435db71`. Pull request: PULL_REQUEST.

## What was built

- `src/trade_graph/adapters/models/perplexity.py`: `PerplexityAgentAdapter`
  behind the provider-neutral `InferenceAdapter` protocol (no SDK). Builds
  Agent API requests (`POST https://api.perplexity.ai/v1/agent`: `instructions`,
  `input` items, `response_format` JSON schema in the Claude dialect, function
  tools) and replays each software tool call with its stored output. Parses
  typed failures (refusal, truncation, rate limit, timeout, credentials,
  validation), tool calls and usage; missing usage stays `None`, partial usage
  is marked unreported, and any reported Perplexity-run tool use is unresolved.
  Perplexity's own `web_search`/`fetch_url` tools are refused. Perplexity's own
  reported total is kept beside the receipt for comparison.
- `src/trade_graph/adapters/research/perplexity_search.py`: Search API wire
  format (`POST https://api.perplexity.ai/search`) with strict parsing; a
  malformed answer is refused whole. Unreadable dates are unknown, never
  guessed. Instruction-like text is kept as data and flagged.
- `src/trade_graph/contracts/web_research.py`: `SearchQuery`, `SearchHit`,
  `StoredSource`, `WebFinding`, `WebResearchReply`, `WebResearchOutcome` and the
  owner `PerplexitySpendPolicy` (no default; D125 testing mode or a monthly USD
  limit).
- `src/trade_graph/application/web_research.py`: `PerplexityWebResearch.run`
  runs one round. Each Agent API step reserves first (through the existing
  gateway and transport journal); the registered `web_search` tool reserves
  one search, records the intent, calls the Search API, stores every result in
  `web_search_sources` with a `source_ref`, commits the receipt and only then
  returns the results to the model. Findings must cite a source stored for the
  same round, otherwise the whole reply is rejected; accepted findings go to
  the existing `findings` table with provenance from the stored result.
  `PerplexitySpendGuard` applies the owner limit per deployment and UTC month,
  counting unresolved calls at their reserved maximum.
- `src/trade_graph/adapters/persistence/web_research_schema.py` and
  `migrations/versions/0028_web_research_evidence.py`: migration 0028, six
  append-only tables (runs, agent steps, search calls, search outcomes, stored
  sources, round outcomes). No key, header, body or raw response is stored.
- `src/trade_graph/application/private_key_file.py`: reads the key only from
  an owner-only (600/400), single-link regular file outside any Git checkout,
  in a folder others cannot write; returns a masked `SecretStr`.
- `src/trade_graph/perplexity_smoke.py` and `trade-graph perplexity-smoke`
  (registered in `cli.py`): previews by default; with `--confirm-paid-call`
  runs one round on a separate smoke database (refuses a database that holds
  a portfolio), with a USD0.25 monthly limit and an EUR5 allowance, and prints
  every receipt beside Perplexity's reported totals. The report says `real`
  only when the pinned network transport ran.
- `src/trade_graph/application/gateway.py`: Perplexity routing; per-call
  `tool_handlers` and `observe_attempt` on `invoke`; tool transcripts keep each
  call's arguments; raw provider payloads may contain finite decimals (see
  "Found outside the package").
- `src/trade_graph/adapters/models/providers.py`: the shared approved
  capability registry lists `perplexity:anthropic/claude-sonnet-5-5`.
- `src/trade_graph/adapters/models/transport.py`: the pinned transport's fixed
  official endpoint list includes the two Perplexity endpoints.
- `src/trade_graph/contracts/models.py`: `perplexity` joins the provider
  literals of `ModelCapabilities` and `ModelRequest`.
- `config/perplexity-price-cards.json`: the two dated price cards.
  `planning/model-prices.json`: two new dated planning entries (existing
  entries untouched). `docs/90-SOURCES.md`: source S41.
  `docs/PERPLEXITY-RESEARCH.md`: how it works, outcomes, tables, prices, key
  file, the L2 smoke and notes for later packages.
- Tests: `tests/unit/test_perplexity_adapter.py`,
  `tests/unit/test_perplexity_search_client.py`,
  `tests/unit/test_private_key_file.py`, `tests/unit/test_perplexity_smoke_cli.py`,
  `tests/integration/test_perplexity_research.py`,
  `tests/integration/test_migration_0028.py`; synthetic fixtures in
  `tests/fixtures/perplexity/` (README says they are synthetic).

## Decisions (reserved block D150–D154)

- **D150 (2026-10-16, implementation default for T36):** Search runs in
  software as the registered `web_search` function tool, on Perplexity's
  Search API. Perplexity's own `web_search` and `fetch_url` tools are never
  enabled (the adapter refuses them, and any reported use leaves the usage
  unresolved), because the model could otherwise read pages the software never
  stored (D124). Each result is stored before the model reads it; a finding
  must cite a `source_ref` stored for its own round, or the whole reply is
  rejected and nothing from it is kept. Endpoints follow Perplexity's published
  documentation as summarised on 2026-10-16: Agent API
  `https://api.perplexity.ai/v1/agent`, Search API
  `https://api.perplexity.ai/search`; L2 confirms them.
- **D151 (2026-10-16, implementation default for T36; deviation from the
  plan's "USD0.0025 per search"):** Two dated cards. The Agent API card keeps
  D124's published 2026-10-12 prices (USD2 / USD10 per million tokens; its
  USD0.0025 search rate applies only to Perplexity's own `web_search` tool,
  which stays off; `fetch_url` USD0.0005 is recorded in the planning entry).
  Software-run searches use the Search API, a different product, priced at
  USD0.005 per request: the highest figure found (search-engine summaries of
  Perplexity's pricing page and third-party pages, USD5 per 1,000; a Fast
  Search tier at USD1 per 1,000 was also reported). The cloud session could
  not open docs.perplexity.ai, so this is unconfirmed; pricing high means a
  receipt can overstate but not understate. L2 re-reads the official page; a
  different price becomes a new card ID. The cost of choosing evidence capture
  over Perplexity's built-in search is about USD0.0025 more per search if
  D124's tool price is current.
- **D152 (2026-10-16, implementation default for T36):** Unknown usage is
  unresolved, never free. A reply whose usage is missing is still used (it is
  valid evidence) but the round reports `billing: unresolved` and the
  reservation stays held at its maximum. An answered search (results or a
  malformed body) is one billed request and gets a receipt; a timed-out or
  refused search stays unresolved. Unresolved calls count at their reserved
  maximum against the owner limit. There is no automatic retry in T36: a
  retry is a new round with its own reservations.
- **D153 (2026-10-16, implementation default for T36):** The owner spend
  policy has no default; without it nothing is reserved or sent.
  `unlimited_testing` is accepted only as D125. `monthly_limit` is in USD per
  deployment and UTC month. Reaching it (or the deployment allowance) gives
  the round the status `paused_at_limit`, which is not a system pause: the
  Trader, protection and records continue. Size bounds on a round (searches,
  tokens) are a reservation envelope for one call, not a cap on activity
  (D116).
- **D154 (2026-10-16, implementation default for T36):** The key is read only
  from the owner's private file under the rules above, held as a masked
  `SecretStr`, sent only as the `Authorization` header, and never stored,
  logged or placed in model context. The credentialed smoke is opt-in
  (`--confirm-paid-call`), uses a separate smoke database, and labels its
  report `real` only when the pinned network transport ran; a scripted run is
  `synthetic` and never counts as a paid-provider test.

## Evidence

- Tests written first: `tests/unit/test_perplexity_adapter.py` and
  `tests/unit/test_perplexity_search_client.py` failed at collection
  (`ModuleNotFoundError`) and pass now (40 at that point);
  `tests/integration/test_perplexity_research.py` failed at collection
  (`ModuleNotFoundError: trade_graph.application.web_research`) and passes;
  `tests/integration/test_migration_0028.py` fails without the 0028
  registration (2 failures) and passes; the decimal-payload test fails without
  the gateway change and passes; `tests/unit/test_private_key_file.py` and
  `tests/unit/test_perplexity_smoke_cli.py` failed at collection and pass.
- New tests: 96 passed (`uv run pytest -p no:cacheprovider` on the six new
  test files). Updated migration tests: `test_migration_0025.py` and
  `test_owner_checkins.py`, 51 passed (they no longer assume 0025 is the newest
  migration; they still prove 0025 is registered once, in order, with its
  exact schema, and upgrades without rewriting rows).
- Related existing tests: provider transport, cost acceptance, expense
  controls, usage FX provenance, funded profile, contracts, protocols, import
  boundary, owner expenses and upgrade: 319 passed.
- `uv run ruff check src tests`: RUFF_RESULT
- `python3 scripts/check_plan.py`: "Plan valid: 46 tasks". `python3
  scripts/test_planning.py`: 11 tests OK. `python3
  scripts/check_repository_hygiene.py`: passed.
- Full suite: FULL_SUITE_RESULT
- Real or synthetic: everything Perplexity-shaped is synthetic (hand-written
  fixtures in the documented shapes, made-up keys, a scripted transport). No
  Perplexity, model-provider, exchange or other paid call was made. The only
  network use was reading web-search results about Perplexity's documentation;
  docs.perplexity.ai itself was refused by the session's network.

## Schema and dependencies

- State migration: `0028` (reserved). Its Alembic revision has
  `down_revision = "0025"` on this branch; the integrator re-chains it after
  0026/0027 when W1-A and W1-B merge (the SQL runner needs no change: it
  applies versions in list order and skips applied ones). Candle database:
  none.
- Dependencies added: none (`pydantic.SecretStr` and `httpx` were already
  dependencies).

## Found outside the package

- **Gateway refused every real response with a fractional number (fixed,
  needed by this package).** The protected transport decodes fractional JSON
  numbers as `Decimal`, but `_bounded_json` accepted only
  `str/int/float/bool/None`, so `_parse_provider_result` rejected such
  responses as "invalid or oversized provider output". Perplexity always sends
  a `cost` block with fractions. OpenAI Responses objects also carry
  `temperature: 1.0` and `top_p: 1.0`, so the direct OpenAI adapter would have
  failed the same way on a real response (it has only run against fixtures).
  The fix admits finite decimals for raw provider payloads only; every other
  caller (including the kernel's department-context check) keeps the strict
  set. A test covers both adapters.
- **Duplicate provider request IDs crash settlement (not fixed).**
  `usage_receipts` has a unique `(provider, provider_request_id)`; a repeated
  ID raises `sqlite3.IntegrityError` out of `BudgetGateway.commit`, which the
  gateway does not catch. Real providers issue unique IDs, but a replayed or
  buggy response would stop the round with the reservation left `RESERVED`.
  For whoever owns the gateway.

## Not done, open or for someone else

- One real Perplexity call with the D125 test key, recorded real fixtures,
  confirmation of the endpoints, `response_format` shape, function-call
  replay, `usage`/`cost` field names and both prices: **local step L2**
  (`trade-graph perplexity-smoke ... --confirm-paid-call`, see
  docs/PERPLEXITY-RESEARCH.md).
- Switching Research and the Leader to Perplexity, a durable department
  handler for multi-step rounds, and the protected route for the registered
  tool (the kernel department gateway refuses `tools` in a department's
  context): **W6-A (T37), W6-B (T38), step L3**. Their handlers need
  `RuntimeGateway`'s resolved-model check to see `anthropic/claude-sonnet-5-5`
  from Perplexity, a pinned transport with both Perplexity endpoints, and a
  price card verified within 30 days (the 2026-10-12 card goes stale on
  2026-11-11).
- HQ spending per department, the USD20 testing alert and the owner's monthly
  limit view: **W2-C (T40)** can read `usage_receipts` (provider `perplexity`)
  and `PerplexitySpendGuard.spent_usd()`.
- The owner money limit as a recorded policy revision: **W3-A (T32)** or an
  owner command; this package supplies the `PerplexitySpendPolicy` contract and
  enforcement only.
- Source number S41 and the cards' `source_id` may collide with another wave-1
  package's new source; **integrator** may renumber (no card has been seeded
  in any installation yet).
- `planning/model-prices.json` is one line, so any other package adding a
  price entry will conflict textually; **integrator** keeps every entry.
- Push and pull request: this session's GitHub access refused writes (403 on
  `git push` and on branch creation through the API). See the pull request
  line above for what happened.
