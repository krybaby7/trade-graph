# Research's focus coins on Perplexity (T37)

Status: built and tested in source on 2026-10-17 by cloud package W6-A (decisions D205–D209, state migration
0038). Not deployed. Paper only. Every test plays synthetic Perplexity answers through a scripted transport; no
real Perplexity call has been made. The switch that moves Research and the Leader to Perplexity ships **off**;
you turn it on at step L3 with one command.

## In one minute

- Every day at the day start, software runs the coin scanner, then wakes Research with the shortlist of about 10
  coins (the event engine, T35). Research looks for news on them through Perplexity and names the day's focus
  coins: the coins worth a Trader day plan today, each with a short reason and the evidence behind it. Then the
  Trader is woken to write its plans.
- Focus coins come only from that shortlist, and only from coins inside your coin list. Research may name none.
- Every web search is run by software, every result is saved before the AI reads it, and every call gets a
  receipt (T36). Research's notes now last until the trading day ends, as long as the day plan, instead of the
  47 minutes they lasted in the first paper run.
- During the day Research looks again when the Leader asks, or at the time it chose for itself. A new choice
  wakes the Trader.
- If research fails (no shortlist, Perplexity switched off, a refused or broken answer, a money limit, no answer),
  that is saved with the reason in plain words and the Trader is still woken and told why. A failure is never
  saved as "Research chose nothing today".
- Web pages are data, never orders: a page that tells the AI what to do changes no setting, places or blocks no
  trade and wakes nobody.

## Where it lives

| Part | Path |
|---|---|
| Research's wake handler (the workflow) | `src/trade_graph/application/focus_research.py` (`FocusResearch`) |
| The reply Research's Perplexity round must return | `src/trade_graph/contracts/focus_research.py` (`FocusResearchReply`) |
| Which coins your coin list covers | `src/trade_graph/application/focus_scope.py` (`focus_scope_refusals`) |
| The owner's switch: contract, read, record | `src/trade_graph/contracts/perplexity_switch.py`, `src/trade_graph/application/perplexity_switch.py` |
| The owner command | `src/trade_graph/market_data_cli/perplexity_departments.py` (`trade-graph perplexity-departments`) |
| The records | `src/trade_graph/adapters/persistence/focus_research_schema.py`, `migrations/versions/0038_focus_research.py` |
| Tests | `tests/unit/test_focus_research_contracts.py`, `tests/unit/test_focus_scope.py`, `tests/integration/test_focus_research.py`, `test_focus_research_service.py`, `test_perplexity_switch.py`, `test_migration_0038.py`; helpers in `tests/focus_research_support.py`; the injection fixture `tests/fixtures/perplexity/search_injection.json` |

All of these are protected paths (`kernel/authority.py`): agents cannot edit the switch, the spend rule, receipt
collection or the checks on Research's answers. So are `contracts/web_research.py` and
`application/web_research.py` (T36), which hold the spend rule and receipt collection.

## How a Research turn works

`FocusResearch` is Research's handler for the event engine's wakes. A wake whose purpose is `focus` is the day's
focus step; every other Research wake (`wake_request` from the Leader, its own `check_in`, a news alarm) is a
**refresh**.

1. **Record first.** The round is saved (`focus_research_rounds`) before anything is sent: its wake, task,
   purpose, trading day, scan and the switch revision in force. One round per task: a turn interrupted mid-round
   is never run again, so nothing is paid twice; it is recorded `interrupted`, its calls stay unresolved and held.
2. **The switch.** `perplexity_enabled_for(database, "research")`. Off: nothing is sent and the turn fails with
   `switched_off`.
3. **The shortlist.** The scan the day start recorded (the newest live scan in `CoinScanStore`, or the one the day
   start ran), never a replay. With no scan, or a failed one, there is no shortlist and so no focus coins: the turn
   fails with `no_shortlist`. Candidates are the shortlisted coins inside your coin list (`focus_scope`: a listed
   mandate names its coins; your coin rule covers spot pairs in its quote currency; riba products never count).
   The scanner's numbers (volume, swing for its cost, spread, trend) are shown with each candidate.
4. **No paid call when there is nothing to choose.** An empty shortlist (`no_candidates`) or no shortlisted coin
   inside your coin list (`outside_owner_scope`) is answered with an empty selection and a summary saying why.
5. **One Perplexity round** (T36). The model reads the candidates, the scan, today's current focus, the wake's
   reasons and notes (data) and the reply rules, and may call the software-run `web_search` tool. Before anything
   from the reply is kept, software checks that every pick is a candidate, every reference is the day's scan id or
   a result saved in this round, no pick rests only on results flagged as instruction-like, and every finding
   cites one saved result. Any breach rejects the whole reply (`rejected`); its saved results and receipts stay.
6. **Findings last for the day plan.** Accepted findings go to the research cache with an expiry at the end of
   the trading day (`WebResearchAsk.findings_expire_at`), set by software. The model chooses no lifetime.
7. **The answer.** Under `wake_outcome`: the focus (each coin's evidence is its cited references plus the ids of
   the findings saved for it), or `keep_current_focus` at a refresh, Research's next check-in and a summary. The
   engine records the focus as a new revision; a revision after the day start wakes the Trader (`focus_updated`).
8. **Or a visible failure.** The turn fails with its reason; the engine records the focus step as `failed` with
   that reason and wakes the Trader for its day plans anyway. The Trader's wake carries `focus_step` (`done`,
   `failed`, `skipped` or `started`, with the reason), so it never mistakes a failure for "nothing today".

### Round outcomes (`focus_research_outcomes.status`)

| Status | The turn | Meaning |
|---|---|---|
| `completed` | answers | The focus (possibly no coins) or "keep", a check-in and a summary. |
| `no_candidates` | answers | The shortlist is empty today; empty selection, no paid call. |
| `outside_owner_scope` | answers | No shortlisted coin is inside your coin list; empty selection, no paid call. |
| `no_shortlist` | fails | No scan, a failed scan or a replay: no shortlist, so no focus coins. Nothing sent. |
| `switched_off` | fails | Research is not switched to Perplexity. Nothing sent. |
| `disabled` | fails | Paid calls are off in this installation. Nothing sent. |
| `paused_at_limit` | fails | Your monthly Perplexity limit (or the deployment allowance) would be passed. Only research pauses. |
| `rejected` | fails | The answer broke a rule above; nothing from it was kept. |
| `failed` | fails | The model refused, was cut off or the call failed. |
| `uncertain` | fails | No answer arrived; the cost stays unresolved and held at its maximum, never free. |
| `interrupted` | fails | The process stopped mid-round; not repeated. |
| `refused` | fails | Not a Research wake. |

## The owner switch

```bash
# Preview: what would change. Writes nothing.
trade-graph perplexity-departments

# Step L3: Research and the Leader on Perplexity, no money limit while testing (D125, D138 item 4).
trade-graph perplexity-departments --confirm

# Later: a monthly limit (about 1.5 times the observed monthly cost, D138 item 4); or switch one back off.
trade-graph perplexity-departments --monthly-limit-usd 30 --confirm
trade-graph perplexity-departments --leader off --confirm
```

The command previews by default, records only with `--confirm`, refuses while the paper service runs, takes
`--expect-revision <the revision the preview showed>` and never rewrites an earlier revision. Each revision is
kept in `perplexity_department_settings` with the revision it replaced. Switching a department on carries the
spend rule: testing mode (no limit; every call still has a reservation and a receipt, and HQ shows the USD20
alert) or a monthly USD limit that pauses only Perplexity-backed research rounds, never the Trader, protection
or records. Recording the switch makes no call by itself; the Trader stays on its subscription.

Reads for the departments:

- `perplexity_enabled_for(database, department) -> bool` (`department` is `research` or `leader`): False until you
  record a revision, in a database from before migration 0038, and when the newest record no longer matches its
  hash (it fails closed). Takes a `Database` or any `sqlite3.Connection`, including a read-only one.
- `perplexity_setting(database) -> PerplexityDepartments`: the revision in force with its `spend_policy`
  (`PerplexitySpendPolicy`, T36) for `PerplexityWebResearch(..., policy=...)`.

A department switched to Perplexity is billed per call, so the paper service gives its wake tasks your paid root
allocation (`root_paid_limit`) even when the service runs on a subscription.

## Records (migration 0038)

All append-only; none is financial state or agent context. Receipts stay in `usage_receipts` (T36), search
evidence in `web_search_sources`, findings in `findings`.

- `perplexity_department_settings`: the switch revisions (only the owner; a department on always has a spend rule).
- `focus_research_rounds`: one row per Research turn on the workflow, written before anything is sent.
- `focus_research_outcomes`: one outcome per round: status, whether the turn answered, the Perplexity run,
  billing, the coins named, the reason and the handler output.

## For the next packages and step L3

- **W6-B (T38, the Leader).** Call `perplexity_enabled_for(database, "leader")` and take the spend rule from
  `perplexity_setting(database).spend_policy`. The Leader's wake tasks already get the paid allocation when the
  Leader is switched on. The Leader asks Research with `EventEngine.request_wake(..., requested_by="leader")` or a
  `wake_requests` answer; Research's refresh reads the note as data.
- **W6-C (T39, HQ wake log).** The focus step's state is in `day_cycle_steps` and in each Trader wake's
  `focus_step`; Research's rounds are in `focus_research_rounds` and `focus_research_outcomes`; the switch in
  `perplexity_department_settings` (`perplexity_setting` works on a read-only connection).
- **Installing it: built by W8-A (C3).** With the event engine on and the switch on, the installed subscription
  service runs `FocusResearch` for Research's engine wakes in the protected process, on the reviewed `paid_research`
  route with the key and dated price cards from your owner folder; see
  [PERPLEXITY-IN-SERVICE.md](PERPLEXITY-IN-SERVICE.md). A card goes stale 30 days after it was checked (the
  2026-10-12 agent card on 2026-11-11) and then refuses paid calls.
- **Step L3.** After installing: `trade-graph perplexity-departments --confirm`.

## Known limits

- Refreshes use the day start's shortlist, the one the engine checks focus coins against (D192). If the hourly
  scans of W6-D should widen it during the day, the engine's check and this workflow change together.
- Without a Research handler that answers wakes, the engine records the focus step as failed ("Research named no
  focus coins"); the old subscription Research handler does not answer wakes.
