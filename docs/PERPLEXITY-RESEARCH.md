# Perplexity research connection

Status: built and tested in source on 2026-10-16 (T36, cloud package W1-C,
decisions D150–D154). Not deployed. No real Perplexity call has been made: every
test plays synthetic responses. Research and the Leader still use their current
models; switching them to Perplexity is a protected owner setting at step L3
(T37, T38): `trade-graph perplexity-departments --confirm`, built by W6-A with
Research's focus-coin workflow (see [FOCUS-RESEARCH.md](FOCUS-RESEARCH.md)).

## In one minute

- Research and the Leader can think with Claude Sonnet 5.5 through
  Perplexity's Agent API (D124).
- When the model wants to look something up, the software runs the search on
  Perplexity's Search API, saves every result (title, link, date, snippet) and
  only then shows the results to the model.
- A finding must point at one of those saved results from the same round.
  Anything else is rejected, and nothing from that reply is kept.
- Every call gets a receipt. A call whose cost is unknown stays "unresolved"
  and is counted at its maximum, never as free.
- During testing there is no spending limit (D125). When you set a monthly
  limit, reaching it pauses only Perplexity research and Leader news rounds.
  The Trader, protection and records keep running.
- The key is read only from your private file and is only ever sent in the
  request header.

## How a round works

1. A round starts with a question, the department (Research or the Leader)
   and size bounds that set the reservation. The bounds are a resource envelope
   for one call, not a limit on how often rounds run (D116).
2. The owner spend policy is checked. Without a policy nothing is reserved or
   sent. In testing mode (D125) there is no limit. With a monthly limit, a call
   is refused when this month's Perplexity spend plus the call's reserved
   maximum would pass it.
3. Each Agent API step reserves money first, records the dispatch intent, then
   sends. Its receipt is priced from the dated card; Perplexity's own reported
   total is saved next to it for comparison.
4. The model may call the `web_search` tool. That tool is software: it reserves
   one search, records the intent, calls the Search API, saves each result as a
   source with a `source_ref`, commits the receipt, and returns the saved
   results marked as untrusted data. Perplexity's own `web_search` and
   `fetch_url` tools are never turned on (D150).
5. The final reply must match its contract. Each finding names one
   `source_ref`. Software fills in the link, publisher, publication date and
   hash from the saved result, never from the model. A result whose text looks
   like an instruction is saved but labelled untrusted, so it is never offered
   as fresh research.

## Outcomes

| Status | Meaning |
|---|---|
| `completed` | The reply was accepted and its findings stored. `billing` says `resolved` or `unresolved`. |
| `rejected` | A finding cited something outside the round's saved sources. Nothing from the reply is kept; receipts and sources stay. |
| `paused_at_limit` | The owner limit (or the deployment allowance) would be passed. Nothing more is sent. |
| `failed` | Refusal, cut-off answer, invalid output, rate limit or credentials, with the failure kind. |
| `uncertain` | The answer never arrived (timeout). The hold stays at its maximum. |
| `disabled` | No owner policy, or paid calls are off. Nothing was reserved or sent. |

A reply whose usage was not reported is still used (it is valid evidence), but
the round says `billing: unresolved` and the reservation stays held at its
maximum until reconciled (D152).

## What is stored (migration 0028)

All tables are append-only. No key, header, request body or raw response is
stored.

- `web_research_runs`: one row per round, with the policy that applied.
- `web_research_steps`: one row per Agent API step: reservation, outcome,
  Perplexity request id, model and Perplexity's reported total.
- `web_search_calls`: one row per search, written with its reservation before
  the request leaves.
- `web_search_outcomes`: results, malformed, failed or uncertain. Only an
  answered request (results or malformed) carries a receipt.
- `web_search_sources`: each saved result, keyed by `source_ref`.
- `web_research_outcomes`: the round's outcome document.

Accepted findings go to the existing `findings` table, so the Trader's research
cache sees them like any other finding.

## Prices

Dated cards live in `config/perplexity-price-cards.json` and as planning
entries in `planning/model-prices.json`. Cards never change; a new price is a
new card ID.

| Card | Price | Basis |
|---|---|---|
| `perplexity-agent-claude-sonnet-5-5-2026-10-12` | USD2 / USD10 per million input / output tokens | Official Agent API models page, read 2026-10-12 (D124) |
| `perplexity-search-api-2026-10-16` | USD0.005 per search | Summaries of the official pricing page and third-party pages; not opened directly (D151) |

The cloud session could not open Perplexity's documentation. Step L2 re-reads
the official pricing page; if a price differs, add a new dated card rather than
editing one.

## Key file

`trade_graph.application.private_key_file.read_private_key` accepts one plain
key in a file that is outside any Git checkout, owned by you, mode 600 or 400,
with a single name, in a folder others cannot write. The value is masked in
every text form and never appears in an error message.

## Smoke test at step L2

The smoke test is opt-in. Use a separate smoke database, never the
installation's.

```bash
# Preview: checks the key file, prints the endpoints and the most it could spend. No call.
trade-graph perplexity-smoke --key-file /path/to/private/perplexity.key --database ~/trade-graph-smoke/smoke.sqlite

# One paid round (D125 test key): one search at most, receipts printed beside Perplexity's totals.
trade-graph perplexity-smoke --key-file /path/to/private/perplexity.key --database ~/trade-graph-smoke/smoke.sqlite \
  --confirm-paid-call --report ~/trade-graph-smoke/report.json
```

The report says `"label": "real"` only when the pinned network transport ran.
It lists each receipt (card, status, USD cost) and each step's reported total.
Its money limit defaults to USD0.25 for the smoke database's month, and the
largest single run is about USD0.06.

What L2 should confirm against the real responses: the endpoint path
(`/v1/agent`), the `response_format` shape, replaying function calls with
their outputs, the `usage` and `cost` field names, the Search API path and
result fields, and both prices.

## For later packages

- **T37 / T38 (W6-A, W6-B):** call
  `PerplexityWebResearch(gateway, route=..., policy=...).run(WebResearchAsk(...), reply_type=...)`.
  Since W6-A, `WebResearchAsk.findings_expire_at` makes every accepted finding
  last until a set moment (Research uses the trading day's end), and
  `run(..., check=...)` sees a valid reply with the round's stored sources before
  anything is kept; a `ValidationFailure` there rejects the whole reply. The
  owner's switch and spend rule come from `perplexity_setting(database)`;
  `perplexity_enabled_for(database, "leader")` is the Leader's read (W6-B).
  A reply type must extend `WebResearchReply`, whose findings cite
  `source_ref`. Treat `paused_at_limit` as "this round pauses", never as a
  system pause. The owner policy and route come from protected configuration.
  A durable department handler will also need: a protected route for the
  registered tool (the department gateway refuses `tools` in a department's
  context), the resolved model check in `RuntimeGateway` (Perplexity must
  report `anthropic/claude-sonnet-5-5`, or receipts stay unresolved), a
  pinned transport with both Perplexity endpoints, and a price card verified
  within 30 days for the model router.
- **T40 (W2-C, HQ spending):** Perplexity receipts are ordinary
  `usage_receipts` with provider `perplexity`; search receipts use the
  `perplexity-search-api-…` card. Unresolved reservations are held, not free.
  `PerplexitySpendGuard.spent_usd()` gives this month's USD total with
  unresolved calls at their maximum.
