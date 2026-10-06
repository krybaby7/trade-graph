# Normal local paper operation

The 2026-10-07 owner instruction replaces the diagnostic first-cycle policy with
normal Windows/WSL paper operation using existing subscription allowance. Preserve
the existing database, owner settings, mandate, runtime records and private evidence.
The account remains USD10,000 virtual opening capital with EUR reporting. Do not
initialize or reset it as part of an ordinary start.

This runbook describes implemented behavior and configuration. Actual provider
attempts, validated results, accounting and dashboard observations belong in
[IMPLEMENTATION-STATUS.md](../IMPLEMENTATION-STATUS.md) and dated operating evidence;
configuration and synthetic tests do not establish an actual AI run.

## Market data and order hosting

Kraken's current [Advanced API FAQ](https://support.kraken.com/articles/advanced-api-faq)
describes an onboarded Spot REST/WebSocket/FIX test environment for qualified
clients. It does not establish an accessible self-service virtual-money Spot
account for this installation. Its `validate` order parameter checks an order
without returning an order identifier; this is not a hosted paper portfolio or
fill simulation. The separately described Futures demo is a derivatives service,
not a Spot paper account.

This path reads public Kraken Spot quotes, metadata and hourly candles. Every
paper order and fill is hosted by **Trade Graph's local simulator**, with local
order-state reconciliation and accounting. It sends no private Kraken request
and does not submit exchange-hosted orders. Existing owner-authorized read-only
exchange integrations retain their permissions independently; they are not a
prerequisite for the public-data paper path or a grant of trading permission.

Hourly refresh defaults to 168 completed candles per configured symbol, initially
BTC/USD and ETH/USD. Record actual timestamps, returned coverage and missing slots;
requested coverage is not proof of acquired coverage. The [Kraken market-data
API](https://docs.kraken.com/api-reference/market-data/get-ohlc-data) retains a
bounded recent OHLC window, including its uncommitted current interval. This
collector accepts up to 719 completed hours, not arbitrary historical backfill.
[Frankfurter](https://frankfurter.dev/) supplies dated reference FX observations;
its daily rates must not be presented as live exchange quotes.

## Subscription connection

Use an explicitly configured, native Linux CLI in WSL or the protected Linux
image. Verified routes use Codex with ChatGPT subscription authentication or
Claude Code with its supported subscription authentication. The installed native
binaries and model catalog are owner-pinned; Windows login state is not proof
that the deployed WSL credential directory is authenticated. Consult the official
[Codex CLI reference](https://developers.openai.com/codex/cli/reference),
[Windows/WSL guidance](https://developers.openai.com/codex/windows) and
[Claude Code authentication documentation](https://code.claude.com/docs/en/authentication).

If login is required, the owner runs the supplied native interactive command in
the designated private login directory. Never paste credentials into chat, extract
authentication tokens, or copy an unrelated user's credential directory. Codex's
supported credential refresh may write only its private authentication directory;
it does not give the CLI access to the financial database, owner policy or host.

Compatible configured existing-subscription routes may provide fallback. Each
route must pass its actual CLI/configuration/authentication/network checks and
support the original structured business contract. Authentication failure,
exhausted allowance and unsupported configuration are technical outcomes to
report. Never switch silently to API billing, paid extras or an unverified route.

## Current owner hold

The latest owner instruction is to finish preparation and **not start the graph**
until explicit green light tomorrow morning. The dashboard-only service may run.
The active MANAGE_ONLY owner hold must be resumed through the existing owner
control when that authorization arrives; do not auto-start or schedule a future
start. No open paper order/position currently needs a management worker.

## Normal launch and dashboard

Use the installed owner launcher with its pinned image, private state mount,
protected owner bundle and provider/public-data proxies. These are the available
commands **inside that protected image**; they are not instructions to bypass the
launcher or manufacture an unprotected Docker command:

```sh
python -I -B -m trade_graph.kernel.deployment_image check-subscription
python -I -B -m trade_graph.kernel.deployment_image boot-subscription-dashboard
```

Open the owner's loopback dashboard and use its existing private owner session.
**Start Trading** starts one normal paper service in the same container, preserving
process ownership and stop/recovery semantics. **Start Optimisation** is available
on demand; configured recurring Optimisation also runs normally. State and journal
records determine the dashboard status. A running web server is not evidence that
a provider has generated or a Trader has decided.

For operation without a dashboard, the launcher may select:

```sh
python -I -B -m trade_graph.kernel.deployment_image boot-subscription
```

It runs continuously by default. `--maximum-ticks <positive integer>` is an optional
owner operating control, not a required diagnostic limit. `manage-subscription`
provides continuous model-free reconciliation and paper-order management during
provider/login/allowance outages. The older `research-subscription` command remains
an optional diagnostic; its success is not required before normal startup.

## Configurable work and limits

The protected `paper-config.json` supports these ordinary settings; change only
intended fields and preserve unrelated owner settings:

```json
{
  "public_data_enabled": true,
  "automatic_schedule": true,
  "optimisation_manual_only": false,
  "public_history_enabled": true,
  "public_history_hours": 168,
  "public_history_interval_seconds": 3600,
  "optimisation_deadline_seconds": null
}
```

Default cadence is Trader every four hours, Research daily, Learning twice weekly,
Leader weekly and Optimisation weekly. `schedule_intervals` and activated permitted
artifacts may configure supported role intervals. New schedules are immediately
due; persisted schedules retain their due times. Active work is coalesced, not
multiplied each tick. Existing mandate authority is reused; a forced Leader
initialization or per-trade committee is unnecessary. A valid hold/no-trade is a
normal outcome, and a model/provider failure is never relabeled as a hold.

The subscription profile defaults to 600 seconds, a requested 16,384 output
tokens, eight turns and 2 MiB captured output per dispatch. Application dispatch
attempts default to three. Native Codex 0.160.1 enforces the process wall-clock
and captured-byte boundaries through the executor; its CLI has no supported
output-token or turn cap. Turn validation occurs after completion. Its documented
built-in defaults allow four request retries and five stream retries; the generic
Claude retry settings do not override those Codex defaults. The Claude adapter
has supported controls and defaults to two transport retries and three
structured-output attempts. Report these distinctions rather than claiming every
profile field limits native Codex usage. Configuration does not impose a universal
one-generation or Research-only policy. Department tools use the supported,
owner-approved interface; provider-side research does not grant host shell,
credential-file, private network or protected database access. The current installed
subscription profile uses `department_tools: {}`: hosted search has no supported
end-to-end result contract in this path yet. Research can use the supplied market
and history evidence. This is a missing tool capability to report, not an owner
policy prohibition on supported departmental tools.

Only known eligible failures may retry or use compatible configured fallback.
A dispatched attempt with an uncertain outcome is retained for reconciliation
and is not blindly replayed. Record application dispatches in the durable
subscription attempt journal. CLI-internal provider attempts are a separate count;
report them only when exposed by actual provider evidence, otherwise mark unknown.

## Accounting, pauses and genuine blockers

Subscription capacity is separate from an API monetary allowance. A subscription
task's zero API allocation does not prove free service, zero subscription expense,
or known token usage. Preserve known per-attempt usage, unknown fields and unknown
costs; do not invent values or seed a budget to unlock work. The virtual portfolio
cannot replenish operating allowance. Shared expenses are allocated once.

Structured contracts, exact task/lease provenance, current owner authority,
Decimal/native-unit financial controls, persistent order intents/attempts and
reconciliation remain required. Protected process/filesystem/network controls
keep model workers away from credentials and financial authority. These existing
project controls remain distinct from obsolete diagnostic acceptance gates.

The service may resume this task's exact historical first-cycle manage-only pause
when a subscription route is ready, under the latest owner authorization. Stronger
or unrelated owner halts remain in force. There is no mandatory pause after the
first cycle. When AI is unavailable, disclose the blocker and retain deterministic
paper-order/position management rather than abandoning existing exposure.

For each remaining blocker, identify its source: obsolete earlier prompt policy,
existing project design, provider requirement or missing functionality. Remove
obsolete testing policy; repair actual login, configuration, networking and
implementation failures. Run checks appropriate to changed code. An unrelated
full test suite and live-account commissioning are not prerequisites for each
paper run. Real-money trading, separately billed APIs, paid extras and purchases
remain disabled throughout this operating path.
