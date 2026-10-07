# Owner-local Kraken read-only onboarding implementation plan

> For agentic workers: use the subagent-driven-development and test-driven-development skills. User-authorized scope is the CLI-first signed collector workflow and a redacted historical Mission Control display.

**Goal:** Add a BTC/USD owner-local command that uses hidden credential prompts, a bounded signed and pinned read grant, the existing collector and independent verifier, and a minimal progress-sidecar result.

**Architecture:** Credentials live only in the short-lived owner CLI, never in graph configuration or the web process. Owner signing material, intent, exact pins and raw captures remain in an owner-private directory outside the checkout/runtime; the financial database is opened read-only for exact current portfolio/policy/artifact/package scope. Import independently verifies retained captures and exact expected scope before recording only fixed public labels, stage/transport evidence and observation/verification/freshness times in the existing progress sidecar. No browser execution, trading permission, financial journal or task acceptance write is added.

**Tech stack:** Python 3.12, existing Pydantic/HMAC/pinned collector contracts, SQLite sidecar, FastAPI/Jinja/vanilla JavaScript, pytest scripted HTTPS fixtures.

## Shared contracts

- Command: `trade-graph kraken-read-only --database runtime/trade_graph.sqlite --owner-directory ~/.local/share/trade-graph-owner/kraken --symbol BTC/USD`. No credential argument, environment variable, browser form or stored API secret.
- `api.account_checks.import_observation(runtime, capture, expected_scope, *, maximum_age_seconds=60, now=None)` verifies `PinnedVenueObservation` itself and imports only sanitized historical metadata. Runtime supplies only database.path, portfolio_id and deployment_id. Returns the redacted record. It must reject wrong scope, stale/tampered sources and fabricated caller success flags.
- Private grants use the existing `ReadOnlyObservationGrant`, exact SHA256 pin, separate owner/collector keys, explicit `observe_read_only_venue_account`, one BTC/USD symbol, <=60 seconds /128 requests /5 history pages by default, and a declared bounded history start. Explicit typed terminal authorization precedes every external effect; private intent is persisted first.
- The typed native scope's `mode=live` is observation identity only: no live portfolio, execution service, mandate or paid-call grant is created.
- Sidecar projection excludes account/key/native IDs, credential bindings, native summaries, balances, fee values, order/trade records, paths and raw exception text. Only fixed allowlisted stage and pending reason labels appear. Injected transports remain explicitly synthetic and have zero authenticated private reads.
- An empty lookup list is not observed order-lookup evidence. Partial stage prefixes and permanent pending checks remain visible. Fresh verification has a <=300-second maximum; persistent results are historical and compute current expiry from original observation time, never refresh it on import.
- Mission label is exactly "Kraken read-only account check". Browser Run remains disabled; owner-local command instructions are shown. Results do not mean full reconciliation or live readiness.

## Tasks

- [ ] CLI implementer: failing synthetic tests for hidden prompt/non-TTY refusal, explicit refusal/no requests, private paths/permissions, signed exact scope, distinct keys, unchanged financial DB, partial failures, no credential disclosure and bounded execution. Implement `application/kraken_onboarding.py`, add lazy CLI dispatch/parser, and record private intent/pins without API credentials. Do not initialize/reset/migrate the runtime DB.
- [ ] Mission implementer: failing tests for actual-versus-injected classification, freshness expiry, incomplete stages, sanitized unknown pending codes, scope mismatch/stale/tampered rejection, idempotent scoped import, existing-sidecar retention, financial immutability, disabled HTTP account Run and safe UI rendering. Implement `api/account_checks.py`, additive sidecar projection, exact label and CLI instructions, and historical evidence rendering.
- [ ] Integration owner: combine reviewed commits, test the full synthetic CLI-to-sidecar-to-HTTP path, preserve prior root status changes/runtime, update onboarding/conformance/Mission Control/status docs and package progress snapshot without marking T17-T22 complete.
- [ ] Independent spec review followed by code-quality review; resolve all material findings. Run focused collector/onboarding/progress/CLI tests, Ruff, Node syntax, planning validation and tracked/staged repository hygiene. Record actual failures and repair evidence.
- [ ] Save source/sanitized-doc Git checkpoint; no credentials or actual private API call. Integrate into the existing implementation branch without resetting either checkout or runtime. Preserve the existing running dashboard; document restart needed to load the new route/display.

## Acceptance and limits

Only synthetic HTTPS fixtures are used for account collection in this implementation. Existing actual public-data evidence is retained separately. Current host has a running dashboard; implementation tests use fresh temporary databases and isolated worktrees. Keys, captures, private result pins and runtime databases are never staged. Later owner operation requires four read permissions: Query Funds, Query Open Orders & Trades, Query Closed Orders & Trades, Query Ledger Entries. Complete key inventory, withdrawal absence, identity, reconciliation, orders/protection and host admission remain pending.
