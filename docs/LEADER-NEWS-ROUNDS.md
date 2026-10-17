# The Leader's news rounds (T38)

Status: built and tested in source on 2026-10-17 by cloud package W6-B (decisions D210–D214, state migration
0039). Not deployed and switched off by default. Paper only. The rules behind it: D121 (the Leader works like a
human manager and never places or approves trades), D122 and D124 (Perplexity, every search result stored as dated
evidence), D125 (testing without a money limit, every call receipted), D123 (each department names its next
check-in) and D116 (no caps on activity). It builds on W1-C's Perplexity connection
([PERPLEXITY-RESEARCH.md](PERPLEXITY-RESEARCH.md)) and W5-A's event engine ([EVENT-ENGINE.md](EVENT-ENGINE.md)).

In plain words: when the Leader is woken (at the day start, at the time it chose, or because another department or a
news alarm asked), it skims crypto market news and news about AI trading agents on Perplexity. Software runs every
search, saves every result before the AI reads it and writes a receipt for every call. From what it read, the Leader
may ask Research to dig deeper, wake the Trader with a short note, and pass a strategy idea to Research or
Optimisation, who must test it on the practice range before anyone uses it. It always says when it wants to look
again. It cannot place, approve or block a trade: nothing it returns can do that, and a note to the Trader is
information the Trader weighs for itself.

| Part | Path |
|---|---|
| The contract: the reply, the brief, the records | `src/trade_graph/contracts/leader_news.py` |
| The wake handler: one round per wake, routing, recovery | `src/trade_graph/application/leader_news/rounds.py` |
| The brief: what the Leader is handed | `src/trade_graph/application/leader_news/brief.py` |
| The records and reads | `src/trade_graph/application/leader_news/store.py`; state migration 0039: `src/trade_graph/adapters/persistence/leader_news_schema.py`, `migrations/versions/0039_leader_news_rounds.py` |
| Tests | `tests/unit/test_leader_news_contracts.py`, `tests/integration/test_migration_0039.py`, `test_leader_news_rounds.py`, `test_leader_news_service.py`; helpers in `tests/leader_news_support.py` |

The contract and the round's code are protected paths (`kernel/authority.py`): they hold the Leader's authority
boundary and its receipts, which agents cannot edit.

## One wake, one round

`LeaderNewsRounds(research, enabled=..., spend_policy=..., engine=..., fallback=..., settings=...)` is the Leader
department's handler.
For a task the event engine made (input `{"wake": WakeContext}`):

1. **Brief** (`context`, before the task runs): the wake (purpose, reasons, trading day), every note left for the
   Leader (wake requests, news alarms), the day's focus coins and the coins of the day's plans in force, each
   department's next check-in, a summary of today's wakes (the latest 12), which departments software wakes, and the
   Leader's previous round. Built from saved records only; the worker persists it with the task's snapshot and the
   round keeps it with its hash. Everything in it is data.
2. **Start**: the round is recorded `started` before any paid call. One round per wake task.
3. **Read**: one W1-C round (`PerplexityWebResearch.run`, role `leader`) with the Leader's guidance after the
   protected instructions. The question is fixed by software: what is new in crypto market news and news about AI
   trading agents since the last round. Each agent step and each search reserves first and gets a receipt; results
   are stored in `web_search_sources` before the model's next turn. The round's envelope (6 searches, 32,000 input
   and 3,000 output tokens, 120 seconds) sizes its reservations; it does not limit how often rounds run.
4. **Check**: the reply must be a `LeaderNewsReply`, every source it cites must be stored for this round, and the
   round must have stored at least one ("a news round reads the news"). Otherwise the round is `rejected` (or
   `failed`) whole: nothing is routed, the receipts and stored results stay, and the wake fails as one turn (no
   system pause; its event triggers are carried to the Leader's next wake).
5. **Answer**: a `WakeOutcome` under `wake_outcome`: the summary, the Leader's `check_in`, and one `wake_request`
   per routed request. The event engine applies it after the task's result is saved.

| Status | Meaning | The wake |
|---|---|---|
| `completed` | A valid reply was routed | completed; the Leader's named check-in is in force |
| `switched_off` | The protected switch keeps the Leader off Perplexity (or the switch could not be read); nothing was sent | completed; next look at the next day start |
| `disabled` | No owner spending policy, or paid calls are off; nothing was reserved or sent | completed; next day start |
| `paused_at_limit` | The owner's money limit, the deployment allowance or the task's allocation stopped the round; only news rounds pause | completed; next day start |
| `rejected`, `failed`, `uncertain` | The reply could not be used, or a bill is unknown; receipts stay | failed as a turn |
| `interrupted` | A restart stopped the round; no second paid round runs for that wake | failed as a turn |

## What the Leader returns

`LeaderNewsReply` (a strict provider reply that passes the OpenAI and Anthropic wire schemas):

- `summary`: what it read, in plain sentences;
- `headlines`: up to 12 lines, each citing one stored source;
- `deep_dives`: up to 8 questions for Research, each with why it matters and the stored sources;
- `trader_note`: at most one note for the Trader, with sources, only for news the Trader should see before its next
  check-in;
- `strategy_ideas`: up to 4 ideas for `research` or `optimisation`, with sources;
- `next_check_in`: required; a time, or `at_utc: null` for the next day start (D123). Nothing limits how soon.
- `findings`: always empty. Findings with counterevidence are Research's work; the Leader asks for a deep dive.

There is no field for orders, approvals, vetoes, plans, pauses, mandates, settings, prompts or code, and extra
fields are refused, so a reply that tries is rejected whole. The bounds are the size of one document, not a cap on
activity.

## How requests travel

Each request becomes a wake request in the outcome, numbered in order (Trader note first, then deep dives, then
ideas), so its engine trigger is `wake-request:<wake_id>:<position>`. The note (at most 500 characters) starts with
the request id and its source references, then the text; the full text, the sources and the symbols stay in
`leader_round_requests`.

- A **deep dive** wakes Research: `Leader deep dive <id> (sources ...): <question> Why: <why>`.
- A **Trader note** wakes the Trader: `Leader note <id> (sources ...): <note>`. The Trader reads it as data and decides
  for itself. Day plans come only from the Trader's own answer, and `DayPlans.install` consults no Leader record, so
  a note that says "do not buy" blocks nothing (tested).
- A **strategy idea** wakes Optimisation when software wakes Optimisation, otherwise Research (both recorded):
  `Leader strategy idea <id>, for the practice range before any use; nothing changes while running ...`. The idea
  changes no strategy, artifact, mandate or setting; Research or Optimisation turn it into a strategy version that
  goes through the practice range (T29) like any other.

## Records (state migration 0039)

- `leader_news_rounds`: one row per round: portfolio, task (unique), wake, trading day, status, the T36 web research
  run, the brief and its hash, the reply, the answer given to the engine, the reason, and the start and finish times.
  Written `started` before any paid call; it finishes once; identity and brief never change; never deleted.
- `leader_round_requests`: one row per routed request: kind, department, the department the Leader asked for,
  position, the engine trigger's `source_ref` and the request document. Written with its round before the round
  finishes, append-only, and each kind only where it may go.

Receipts stay in `usage_receipts` and stored results in `web_search_sources`; `LeaderNewsStore.rounds(portfolio_id,
since=)` and `round(round_id)` read a round with its billing (`resolved`, `unresolved` or `none`), receipt ids,
unresolved reservations, stored source refs and routed requests, live from T36's records. HQ (W6-C) can read these;
reading starts nothing.

## Switch, money and the paper service

- **The switch.** `enabled` is the protected owner switch that moves the Leader to Perplexity (W6-A owns it for both
  departments; it ships off and the owner turns it on at step L3 with `trade-graph perplexity-departments
  --confirm`). `spend_policy` reads the owner's spend rule that travels with it. Both are functions, read fresh for
  each task, and fail closed: an unreadable switch counts as off, an unreadable spend rule as none. Once W6-A is
  merged they are `enabled=lambda: perplexity_enabled_for(database, "leader")` and `spend_policy=lambda:
  perplexity_setting(database).spend_policy` (`trade_graph.application.perplexity_switch`). Switched off, the
  `fallback` Leader handler (the existing subscription Leader) keeps the Leader's wakes; with no fallback the round
  is recorded `switched_off`.
- **Other Leader tasks.** A Leader task the engine did not make (Secretary routing, an owner request) goes to the
  `fallback` handler; without one it fails as a turn.
- **Money.** The owner's `PerplexitySpendPolicy` decides (D125 testing mode, or a monthly USD limit, D138 item 4).
  Reaching the limit pauses only Research and Leader news rounds. A wake task's allocation also bounds a round: in
  the paper service it is the owner root limit. A subscription installation allocated EUR0 to every wake task
  (W5-A), which stops a round at `paused_at_limit` before any request (tested); W6-A gives departments switched to
  Perplexity the owner root limit instead.
- **The paper service.** Pass the handler as `handlers["leader"]` and set `rounds.engine = service.event_engine`
  after the service is made (the service builds its engine). Under an owner or system halt the service defers the
  Leader's wake, so nothing is sent; the AI allowance floor holds it too.

## Restart

A round is recorded before any paid call. If the process stops in the middle, the next claim of the task finds the
`started` round and records it `interrupted`: its calls keep their receipts, an unknown bill stays unresolved at its
reserved maximum, and no second paid round runs for that wake. A finished round's saved answer is returned before
any snapshot or paid call, so the engine applies it once.
