# Perplexity inside the installed service

Status: built 2026-10-17 by cloud package W8-A (C3, decisions D240–D244), tested only with a scripted Perplexity and a
made-up key. It changes nothing until you switch Research or the Leader on at L3 step 12. No real Perplexity call has
been made; the first one is the opt-in smoke at step L2 ([PERPLEXITY-RESEARCH.md](PERPLEXITY-RESEARCH.md)).

## In plain words

With the event engine on and your switch on (`trade-graph perplexity-departments --confirm`), the installed service
hands Research's wakes (the day's focus coins and its refreshes, [FOCUS-RESEARCH.md](FOCUS-RESEARCH.md)) and the
Leader's wakes (its news rounds, [LEADER-NEWS-ROUNDS.md](LEADER-NEWS-ROUNDS.md)) to Perplexity. Everything else stays as
it is: the Trader, Learning, Optimisation and the Engineer stay on your subscription, and so do Research and Leader
tasks that are not engine wakes (the Secretary's digests, for example). With the switch off, the service runs exactly
as before and reads no Perplexity file.

## What the service needs from you

All in your root-owned owner folder (`<owner>`, mounted read-only at `/run/trade-graph-owner`):

| File | What | Rules |
|---|---|---|
| `perplexity.key` | Your Perplexity API key, one line | `root:10001`, mode `0440` like `capability.key` ([PROTECTED-DEPLOYMENT.md](PROTECTED-DEPLOYMENT.md)): never writable or world-readable, one name, no link. Read by the protected owner-file reader, held masked, sent only as the `Authorization` header, never stored, logged or shown to a model (D154). |
| `perplexity-price-cards.json` | A copy of the repository's `config/perplexity-price-cards.json` | `root:10001`, mode `0440`. Dated cards with their source. Seeded into the database once; a card's id never changes its prices (an edited card is refused; a new price is a new dated card). |
| `subscription-network-profile.json` | Your existing profile plus the `paid_research` route | See [REVIEWED-NETWORK-ROUTES.md](REVIEWED-NETWORK-ROUTES.md). |

Your reviewed proxy also needs the route on its command line (`--route paid_research=8082:api.perplexity.ai`).

## How a wake runs

1. At start, if a department is switched on and the event engine runs, the protected parent process reads the three
   files above and builds the route: the transport (Perplexity's two endpoints only, through the route's proxy port,
   no environment proxy), a paid-enabled gateway that carries only Research's and the Leader's rounds and only the
   software `web_search` tool, and the newest effective price card for each endpoint. HQ's activity log records a
   `perplexity_route` event: `ready`, or `not_ready` with the reason. No secret is in it.
2. An engine wake of a switched department runs W6-A's `FocusResearch` or W6-B's `LeaderNewsRounds`: the round is
   recorded first; every search runs in software and every result is stored before the model reads it; every agent
   step and search reserves first and ends with a receipt, or stays unresolved at its reserved maximum.
3. Which handler answered a turn is fixed in its snapshot, so a restart never switches a half-done turn to the other
   handler; a round already recorded returns its answer or is marked interrupted, never paid twice.

If a file is missing or refused, the network profile lists no `paid_research` route, or a price card is stale, the
switched department's wakes fail with that reason and nothing is sent: the focus step is recorded as failed and the
Trader is still woken with the scanner's shortlist; the Leader's wake fails as one turn. They do not fall back to the
subscription silently.

## Prices

The 2026-10-12 Agent API card and the 2026-10-16 Search API card are the current dated cards (the cloud could not
open Perplexity's pricing page on 2026-10-17; step L2 re-checks it). A card goes stale 30 days after it was last
checked: the Agent API card on **2026-11-11**, the Search API card on 2026-11-15. A stale card refuses paid calls
(the wake fails with the reason); it never prices a call as free. After a re-check, a new dated card goes into the
repository file and your owner copy; the service uses the newest effective card at its next start.

## Money

- Every call has a reservation and a receipt; an unknown bill stays unresolved and is counted at its maximum.
- Your Perplexity spend rule is the money rule: no limit while testing (D125, the default of
  `perplexity-departments --confirm`), or the monthly USD limit you record later (`--monthly-limit-usd`, D138 item
  4). Reaching it pauses only these rounds; the Trader, protection and records continue.
- The deployment's EUR allowance (unused by a subscription installation) is not consulted, so it cannot block the
  testing period. Each wake task keeps the owner's paid root allocation per task (W6-A), which bounds one round.
- Reservations use their own deployment id (`<deployment>:perplexity`), apart from the subscription deployment's
  protected books; HQ's AI spending reads them by price card as before. Amounts are held in EUR at a 1:1 USD rate
  plus the 2% buffer (no FX quote is configured for this route), which overstates the EUR view slightly; receipts
  keep the USD cost.

## Records

The round records are W6-A's (`focus_research_rounds`, `focus_research_outcomes`), W6-B's (`leader_news_rounds`,
`leader_round_requests`) and T36's (`web_research_runs`, steps, search calls and outcomes, stored sources), with
`budget_reservations` and `usage_receipts`. No new table (no state migration).

## Tests

`tests/integration/test_perplexity_in_service.py` runs a scripted day through `run_subscription_operation` with the
real protected subscription binding: switched on, Research's focus step, the Leader's news round and the refresh its
deep dive asks for all run on a scripted Perplexity with receipts and stored sources; switched off, the same day runs
as before; switched on without a key, the wakes fail with the reason and nothing is sent.
`tests/integration/test_perplexity_installation.py` covers the owner files, the pinned transport, stale cards, the
money rule and which tasks may reserve; `tests/security/test_reviewed_routes.py` the network route.
