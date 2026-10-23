# Owner-local Research subscription preflight — 2026-10-06

The requested bounded diagnostic collected real public history and verified CLI
subscription metadata, but **no Research inference was started**. The installed
Codex subscription route cannot establish the requested zero-retry guarantee;
Claude Code is logged out. This checkpoint does not enable continuous graph
operation, certify T17 funded operation, change T17–T22 acceptance or authorize
private Kraken reads, separately billed API calls or live trading.

## Preserved checkout and runtime

The owner's WSL checkout `~/trade-graph` fast-forwarded from `9dedf97` to
`e920cb7` with `git pull --ff-only`. The two existing local commits are ancestors
of the updated head. The separate Windows checkout and its local status edit
were untouched. Documentation was prepared in isolated branch
`codex/research-diagnostic-2026-10-06`; runtime commands used the original WSL
checkout. No source, lockfile, owner policy or model routing was changed.

Before collection, SQLite's online backup API saved consistent private backups
of the financial database and progress-history sidecar. Private before/after
manifests compare every existing table, retained Kraken file contents/modes and
the owner-session file. Existing changes are limited to `schema_migrations`,
`fx_rates`, `valuation_marks`, `instruments` and `observations`, plus the new
`hourly_candles` table. Financial/order/decision/budget/expense/authority tables,
progress history, private Kraken evidence and owner-session bytes are unchanged.

No dashboard process or listener was running when checked; its saved PID was
stale. The same loopback dashboard was restored against the existing database
and existing session file, appending its log. WSL and Windows `/login` returned
HTTP 200. No scheduler or provider starts through that dashboard command. The
PID changed deliberately; the session and stored history did not.

## Actual public history and refresh

Executed with a clean environment, private umask and the committed lockfile:

```bash
uv run --frozen trade-graph collect-kraken-history --database runtime/trade_graph.sqlite --hours 168
uv run --frozen trade-graph run --mode paper --database runtime/trade_graph.sqlite --public-data --once
```

| Symbol | Completed candles | UTC open/close coverage | Missing hourly slots |
|---|---:|---|---:|
| BTC/USD | 168 inserted | 2026-09-29 14:00 through 2026-10-06 14:00 | 0 |
| ETH/USD | 168 inserted | 2026-09-29 14:00 through 2026-10-06 14:00 | 0 |

The history command made exactly two public OHLC GETs, excluded the unfinished
candle and recorded actual receipt times 2026-10-06 14:54:52.928150 UTC and
14:54:53.292156 UTC. No private requests, pagination or collector retries were
made. The single public-data tick completed with two observations, no scheduled
or completed model tasks, no new decisions and no failures; paid calls and live
mode remained disabled. It refreshed public quote/rules, paper valuation marks
and the attributed reference FX observation. Reference FX is daily data, not an
executable intraday rate. The actual persisted USD/EUR reference was 0.88739,
Frankfurter/ECB dated 2026-10-06, retrieved at 14:55:29.961922 UTC, with its
stale flag false.

## Verified official subscription route and exact blockers

Windows has the official `@openai/codex` CLI 0.125.0 and Claude Code 2.1.280.
No native Linux CLI installation was performed. The Codex shared config contains
values this CLI cannot parse: `model_reasoning_effort="ultra"` and
`service_tier="default"`. For read-only checks only, those two values were
translated/omitted temporarily, then the original config bytes were restored
in a guarded finally block. No global setting change remains.

`codex login status` confirmed ChatGPT login. The official app-server protocol
was initialized without starting any thread or turn; `account/read` with
`refreshToken:false` returned account type `chatgpt`, and
`account/rateLimits/read` returned quota and credit information. The process
received an allowlisted environment with no provider/exchange key variables.
No authentication file or token was extracted. Only authentication mode,
non-identifying quota fields and restoration status were retained. Account
identifiers, emails and tokens were excluded. Exact available quota and reset
metadata remain in the ignored private receipt; no quota reset or purchase was
used. A null shorter-window field remains unavailable, not zero.

Official documentation confirms subscription-backed Codex scripted work and
structured output. However, the installed-version OpenAI provider leaves its
retry fields unset and resolves them to defaults of 4 request and 5 stream
retry settings. Its configuration rejects overrides of reserved built-in
provider IDs, including `openai`. Current official documentation likewise says
not to override `[model_providers.openai]`. Therefore settings that appear to
set retries to zero cannot safely establish a zero-retry built-in subscription
run. A custom endpoint/token route was not attempted. There is no real model
identity, inference usage receipt or validated AI result for this diagnostic.

Claude's documented `claude auth status` returned `loggedIn:false`,
`authMethod:"none"`, `apiProvider:"firstParty"`, and no subscription type.
Its installed login therefore cannot establish existing subscription access.
The current official billing clarification still permits `claude -p` to draw
subscription limits: the previously announced June change was paused. No
unsupported API-billing assumption was used to reject this route. Official
Claude docs expose retry/structured-attempt controls, but authentication,
subscription capacity, extra-usage settings and the entire isolated invocation
still require verification before an inference request.

Official references reviewed on 2026-10-06:

- [Codex authentication](https://learn.chatgpt.com/docs/auth),
  [app-server account/usage checks](https://learn.chatgpt.com/docs/app-server),
  [plan usage](https://learn.chatgpt.com/docs/pricing), and
  [advanced configuration](https://learn.chatgpt.com/docs/config-file/config-advanced).
- [Installed-version provider defaults](https://github.com/openai/codex/blob/rust-v0.125.0/codex-rs/model-provider-info/src/lib.rs#L264)
  and [reserved-provider validation](https://github.com/openai/codex/blob/rust-v0.125.0/codex-rs/config/src/config_toml.rs#L736).
- [Claude subscription billing clarification](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan),
  [CLI reference](https://code.claude.com/docs/en/cli-reference), and
  [retry and structured-output environment controls](https://code.claude.com/docs/en/env-vars).

## Private diagnostic preparation, usage and costs

`runtime/research-diagnostic-20261006/` retains private backups/manifests,
collection/refresh summaries, a public-only input packet, prompt, locally
checked JSON result schema and `preflight-receipt.json`. The packet contains only
public hourly candles, coverage and precomputed Decimal features with source
references. Both assets' requested hourly features were ready at its frozen
receipt-time snapshot. It contains no portfolio state, private account files,
credentials, owner permissions or budget. It has not been sent to a model.

The structured schema is checked locally with JSON Schema's schema validator;
provider acceptance and a structured model result remain unverified. No
production subscription adapter was implemented because its required route is
blocked. No Research result was inserted into the graph or financial database.

Research inference attempts: **0**. Actual Research model/result: **none**.
Separately billed API calls, purchases and private Kraken requests: **0**.
A subscription fee allocation and controller/preparation usage/cost are unknown
and are not represented as free or converted into a fabricated API receipt.
Current account usage is shared with other work and cannot attribute this
conversation's cost to Research. No continuous AI operation has been configured.

## Verification and next step

On WSL2, locked Python 3.12: **162 relevant tests passed**, covering history CLI,
contexts, migration, Kraken/history normalization/store, public market and
repository hygiene. No production source changed in this checkpoint. Full-suite,
installed-wheel and protected-image runs were not repeated. Staged/repository
secret hygiene, planning validation, ten standalone planning tests and diff
checks were run before commit. An initial attempt to run the standalone
planning script through pytest failed import resolution (`check_plan`); its
documented direct invocation `python scripts/test_planning.py` passed all ten
tests. This was a command invocation error, with no source fix required.

The practical next route is to sign in to the installed Claude Code through its
official subscription login, verify subscription capacity and disabled extra
usage, then validate its documented zero-retry/no-fallback/no-tool structured
invocation in a sanitized working directory. Alternatively, Codex needs an
officially supported strict zero-retry subscription configuration. Under the
current instructions, do not run Codex with its internal retry policy, extract
login tokens, configure custom undocumented endpoints, or enable direct API
funding. Refresh the public snapshot before the eventual real analysis.
