# First public-data paper cycle — operating runbook

**Preparation checkpoint, 2026-10-07. No model attempt or completed AI cycle is
claimed.** This route uses the owner's existing Claude subscription, Kraken
public data and Trade Graph's simulated broker. Paper capital remains
USD10,000 with EUR reporting; it cannot fund model usage or authorize live orders.

## What is verified and what remains

- Native Linux Claude Code **2.1.292** is installed as the root-controlled
  executable `/usr/local/lib/trade-graph/claude-2.1.292`. Its SHA256 is
  `a967e7b1d8b4e47ee421d5433027880347952b0c0857abf880e2c942a4ec93b3`.
  The signed release manifest was verified against fingerprint
  `31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE`, following
  [Anthropic's binary verification procedure](https://code.claude.com/docs/en/setup#verify-the-manifest-signature).
- The separate native login directory
  `/home/adami/.local/share/trade-graph/claude-login` is empty and signed out.
  The clean-environment `auth status` check exits **1**, as documented for a
  signed-out CLI. Windows Claude/Codex logins do not authenticate this directory.
- The retained public collection contains **168 completed hourly candles per
  BTC/USD and ETH/USD, with zero missing hourly slots at collection**. The
  retained Frankfurter/ECB USD/EUR reference is **0.88739**, dated
  **2026-10-06**. These are historical observations, not a claim of freshness
  now; see the [retained collection evidence](reviews/2026-10-06-research-subscription-preflight.md#actual-public-history-and-refresh).
- Model attempts are **0**; the paper portfolio remains **USD10,000**.
  Mandate revision **1** already supplies the paper authority for a
  Research/Trader cycle. It needs no Leader model call or per-trade committee.
- Final protected-image isolation/egress probes, login, subscription capacity
  and spending-setting checks remain pending. A package/version check does not
  establish the final process boundary. The earlier builtin-Docker-seccomp
  bubblewrap namespace probe refused; the intended-image result must be
  independently retained. [Docker's seccomp reference](https://docs.docker.com/engine/security/seccomp/)
  and the [pinned Moby default profile](https://github.com/moby/profiles/blob/2ceae35d351c156cb5a8efc0fdc4a08cf94569d8/seccomp/default.json)
  describe namespace restrictions; neither substitutes for that probe.

## Owner login and included-usage check

In PowerShell, run this exact command and complete the official browser
subscription login. The clean environment excludes inherited API keys and
provider overrides. Do not select Console/API billing.

```powershell
wsl -d Ubuntu -- env -i HOME=/home/adami PATH=/usr/bin:/bin CLAUDE_CONFIG_DIR=/home/adami/.local/share/trade-graph/claude-login DISABLE_AUTOUPDATER=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 /usr/local/lib/trade-graph/claude-2.1.292 auth login
```

Then use the same command with `auth status` in place of `auth login`.
Confirm the selected directory, a successful login and the subscription
authentication method; keep identifying auth output private.
[Official CLI commands](https://code.claude.com/docs/en/cli-reference) and
[authentication precedence](https://code.claude.com/docs/en/authentication)
explain these checks.

Open [Claude Settings → Usage](https://claude.ai/settings/usage) and verify
available included usage, **usage credits/extra usage disabled**, and
**auto-reload/automatic purchases disabled**. Do not add funds, enable credits
or buy capacity. Retain only the sanitized check time and outcome in private
operating evidence. Claude's current
[billing clarification](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
says `claude -p` still draws subscription limits; enabled
[usage credits](https://support.claude.com/en/articles/12429409-manage-usage-credits-for-paid-claude-plans)
can create separate charges. A subscription is not a zero-cost receipt.

## Bounded diagnostic and cycle

The selected model is exactly **`claude-sonnet-5-5`**. Account access still
needs the credentialed check; the [official model identifier](https://platform.claude.com/docs/en/models/sonnet-5-5/overview)
alone does not establish it.

The controller must enforce the following envelope before dispatch:

| Control | Limit |
|---|---|
| Native inference invocations | At most **3 total**: one public-only Research diagnostic, then one cycle Research and one cycle Trader invocation |
| Per invocation | **120 seconds**, **4096 output tokens**, **one agentic turn**, **256 KiB combined output** |
| Transport retries | `CLAUDE_CODE_MAX_RETRIES=0`; `CLAUDE_CODE_NONSTREAMING_TIMEOUT_RETRIES=0` |
| Structured attempts | `MAX_STRUCTURED_OUTPUT_RETRIES=1`: one initial attempt, **no validation retry** |
| Extra calls | No application retry, repair, provider/model fallback or direct API route |
| Model access | Tools/MCP disabled; fresh restricted environment; no model access to credentials, database or owner policy |

The structured variable counts attempts, despite its name. These semantics and
the retry controls are documented in the
[official environment reference](https://code.claude.com/docs/en/env-vars).
Persist each attempt before dispatch and retain success, failure, unknown usage
and requested/actual model. A hold/no-action is valid; do not force a paper trade.
Quota exhaustion, timeout, invalid output or an uncertain attempt stops new AI
work without replaying it. Deterministic reconciliation/protection must continue
for any existing paper orders or positions.

The prepared protected actions are `check-subscription` for metadata,
`research-subscription` for the diagnostic phase, then `boot-subscription`
for the bounded cycle. **A final operator inference command is not yet
verified and is intentionally not supplied here.** Admission and the independent
image probes must pass before either inference phase starts. Provider egress
must use the reviewed fixed proxy; the
[official network requirements](https://code.claude.com/docs/en/network-config)
identify inference and login/refresh hosts. Login from the owner's browser is
separate from the model process's restricted network.

## Preserve state and review the result

The original WSL database is preserved. Creating/migrating the private
protected-state copy remains pending and must wait until the authentication and
spending checks pass. Take a consistent SQLite backup, retain the original
database, and apply required migrations to the protected copy. Do not initialize
a new portfolio or reset capital, expenses, order history or earlier receipts.

Before inference, refresh public quotes/rules, completed history and the sourced
ECB FX reference in the admitted state. Preserve both event/close times and
actual receipt times. Kraken's [OHLC endpoint](https://docs.kraken.com/api-reference/market-data/get-ohlc-data)
returns at most 720 recent rows and always includes an uncommitted final candle;
exclude that candle. [Frankfurter](https://frankfurter.dev/) supplies daily
reference FX for reporting, not an executable intraday rate.

Kraken's [current FAQ](https://support.kraken.com/articles/advanced-api-faq)
offers Spot REST/WebSocket/FIX testing to qualified clients through API-team
onboarding. Its validation parameter checks an order without returning an order
ID. This experiment uses public Kraken prices with **Trade Graph simulated
fills**. Kraken's [credential-free CLI paper engine](https://www.kraken.com/kraken-cli)
is another local simulator; Futures demo is a separate product. No private
Kraken request, validation order, real order or withdrawal belongs to this run.

After the bounded run, retain the diagnostic result, Research/Trader outcomes,
invocation receipts, paper intents/fills/fees, USD balances, dated EUR valuation
and reconciliation result. Unknown subscription fee allocation stays unknown;
do not replace it with a zero API bill. Record any stop and the continuing
order/position-management path. A passing cycle establishes only its measured
operation; economic evidence, T17–T22 acceptance and live authority remain
separate.
