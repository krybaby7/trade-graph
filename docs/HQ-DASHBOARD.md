# Trade Graph HQ: owner guide

HQ is the home page you see after signing in at `/`. It answers five questions, in this order:

1. Is everything OK right now?
2. Does anything need me?
3. What is each department doing, and what is next?
4. What is coming up?
5. What happened, and what did it lead to?

This is a practice run. The goal right now is to check that the team works safely, not to make money.
Nothing on HQ moves real money: the account uses practice money, and real costs are only the bills you enter.

The older, detailed pages are still there, unchanged. Open **Detailed views** in the top bar:

| Page | Address | Use it for |
|---|---|---|
| Account journal (detailed numbers) | `/journal` | The old home page: exact balances, costs, review snapshot download |
| Mission Control (build progress & tests) | `/progress` | Build progress, tests, service status |
| Trading records | `/trading` | Every practice order and position |
| Organisation records | `/organization` | Every task, research note and lesson |
| Costs | `/costs` | Every recorded cost |
| Improvements | `/changes` | Every improvement the Engineer proposed |
| Owner controls | `/owner` | All owner settings (owner only) |

## Using HQ on a phone or iPad

On a phone, HQ shows one area at a time. Use the tab bar at the bottom: **HQ**, **Calendar**, **Story**,
**Money** and **Decide**. On a wider screen (768 px and up) everything shows at once.

- Tap any **time** to see the full date in your time zone and in UTC.
- Tap any **underlined word** for a plain explanation (the glossary).
- Tap the **?** next to a heading for "What am I looking at?".
- Tap a **room** on the HQ map for what that department does, its last turn and its next turn.
- The pill at the top says how fresh the page is. HQ updates itself every 30 seconds while it is on screen.
  If it can't reach the server it says so, keeps showing what it last saw, and disables every Confirm button.
- **Show technical names** (in Help on the Decide tab) adds the raw codes next to the plain words.
  It is stored on this device only.

## The five areas

**HQ tab**
- **Right now**: one sentence about this minute, what happens if you do nothing, and what changed since you last looked.
  Quiet means nobody is working; it doesn't mean broken.
- **Needs you**: only things that need a person. An empty list means you can relax.
- **Up next**: the next moments something can change. Between them the team is idle.
- **HQ map**: nine rooms (Trader, Research, Learning, Leader, Optimisation, Engineer, Secretary, Price feed,
  Accounts desk). A room's light changes only when a saved record says so, and every light has a shape and words,
  never colour alone.

**Calendar**: each department's next turns, your own check-ins, and deadlines. Items marked "≈" are estimates,
because each turn sets the next one when it actually runs. Estimates more than 48 hours ahead are grouped under
"Later (estimates)", one line per department. A turn that can only happen under a condition (the team running, the
AI back on, allowance available, Resume) says so. "Show 2 weeks" looks further ahead.

**Story**: what happened, grouped by day, newest first. Short quotes labelled "In their words" are summaries the
departments wrote themselves; HQ never rewrites them. "Load earlier" goes further back; on a very busy day a page
may stop short of 3 days and the next page continues from there, so nothing is skipped.

**Money**: the scoreboard (practice money, what changed it, what is held), whether the run "pays for itself",
"Is my money at risk?", the AI allowance gauge, **AI spending** (below), the market weather (price and
20-hour / 50-hour averages) and **Price charts** (below). Practice money and real costs are always shown apart and
never added together. Anything not known yet says "not known yet"; it is never shown as zero.

**Decide**: the buttons. See below.

## AI spending (the Money tab)

The **AI spending** panel shows what paid AI has really cost, in US dollars, from the receipts the system keeps
for every call (T40, decisions D175–D179):

- **Perplexity web research** (Research and the Leader) and the **Jev news alarm**, each per department. Every
  call counts once: **from receipts** (the provider reported its use), **estimated** (its use was never reported,
  so it was charged at the most it could cost) or **not known yet** (shown as "up to" the most it could cost,
  never as free). Test calls with made-up answers are counted apart and never added.
- The **testing total**, next to the **prepaid credit** you last recorded and what has been spent since. The
  credit is information only: HQ never stops anything at that amount; the provider itself stops calls when its
  credit runs out.
- The **testing alert**: at USD20 for Perplexity (D131) and USD5 for Jev (D138). Once testing spending passes it,
  "Needs you" shows a "Keep an eye on" card. It pauses nothing. If calls not known yet could take the total past
  the alert, the card says it "may have passed". Once you set a Perplexity monthly limit, the panel shows this
  month's spending against the limit instead (reaching it pauses Research and Leader news rounds only).
- **AI allowance by department**: each department's turns on your ChatGPT subscription in the last 7 days and its
  share of the team's reported use. The allowance itself is shared with your own use, so HQ never splits the
  gauge by department.

Practice money never appears here and never pays for AI.

Two owner commands go with it (run them on your PC, or ask the local session to):

- `trade-graph ai-spending-report [--format text]` prints the same figures. It only reads the database.
- `trade-graph ai-spending-record --service perplexity --prepaid-credit 25 --checked-at 2026-10-17T09:00:00Z`
  records the balance you read on your Perplexity account page; `--testing-alert 30` records another alert
  amount. Both preview first and write only with `--confirm`. The newest record counts; older ones stay as history.

## Price charts (the Money tab)

The **Price charts** panel draws one coin at a time from the system's own saved records (T41, decisions D195–D199):

- **Coin**: the coins of today's day plans, coins the account holds, coins with an alarm set, the scanner's newest
  shortlist (if it is less than a day old) and BTC/USD and ETH/USD for reference. The list says why each coin is there.
- **Candle size**: 1 min, 5 min, 15 min or 1 hour. A chart shows the last 300 candles (5 hours at 1 minute, about
  12 days at 1 hour). Drag sideways to look further back; pinch, or drag the time axis, to zoom. Scrolling the
  page over the chart scrolls the page.
- **Candles**: hollow when the price rose, filled when it fell. A candle that was never saved stays an empty gap;
  nothing is filled in. Tap or hover a candle for its exact open, high, low and close under the chart.
- **Indicators**: the ones the Trader chose in the coin's day plan for this candle size, and the ones its alarms
  watch. Averages and bands sit on the prices; RSI, MACD and the like get a panel of their own below. An indicator
  that can't be worked out yet (too little history, or a missing candle it needs) is not drawn, and the list under
  the chart says why. Indicators chosen for another candle size are named, with the size to pick.
- **Lines**: alarms (dashed, amber), resting buys (solid, blue), safety exits: stop (dashed, red) and target (dashed,
  green), and any other open order (dotted). The price scale stretches to show buys and exits within a tenth of the
  price; an alarm line appears when the price comes near it. Every line is also written out under the chart, so you
  never need the colours. An order whose status is being checked says so.
- **Marks**: an arrow for each buy or sell that filled, and a dot where an alarm went off, listed under "Marked on the
  chart".
- The chart refreshes every minute while it is on screen. Looking at it never starts anything or changes anything.

Charts are drawn with TradingView Lightweight Charts™ (Apache License 2.0), a copy kept inside Trade Graph: the page
never loads anything from TradingView or anywhere else. The line under every chart names TradingView, links to its
website and to the licence and notice, as the licence asks.

If the panel says price history isn't connected, the dashboard was started without the candle file
(`runtime/market_data.sqlite`, or the file given with `trade-graph dashboard --market-database <file>`). HQ only reads
that file; it never creates or changes it. Alarm and order levels are still listed.

## Decisions (the Decide tab)

Only the owner, signed in on this device in the browser, in paper mode, sees working buttons. Everyone else sees
the same page as "View only". In a live account HQ shows no buttons at all; use Owner controls.

Every button first shows a preview: what happens, what it costs, whether you can undo it, what HQ recommends and
why, and the current state. Nothing happens until you press Confirm. Bills and a Stop on an empty account also ask
you to tick "I've double-checked this".

| Card | What it does |
|---|---|
| Start the team | Starts the practice team, or reconnects to it if it is already running. If the team has gone silent, Start would only reconnect, so HQ offers a check-up prompt instead. |
| Pause the AI, keep protection | No new AI work and no new buying. Prices, record-keeping and protection continue. A turn in progress is set aside; allowance it already used stays used. Not offered while the Leader has a stronger setting on (cancel all orders, sell everything, or stopped), because your pause would replace and weaken it. |
| Resume | Double-checks every order and balance first. If anything is unclear, Resume is refused and nothing changes. |
| Ask for an optimisation review | Asks Optimisation for one review of the team's work. Nothing changes automatically. |
| Stop the team safely | Stops the team. If something is held, protection keeps running instead. On an account that holds nothing, this also replaces an automatic safety stop with your own Stop (existing behaviour); during a safety stop HQ marks it "not recommended" and only partly reversible. |
| Record a bill | Adds a real bill (AI subscription, hosting, electricity, internet or another service), with Trade Graph's share and either no split or an equal split across the five scheduled departments. |
| I've entered every bill for a month | Says every bill for a finished month is entered, so the month's costs become known. |
| Plan a review check-in | Puts a review time in your HQ calendar. |

If the network drops while you confirm, HQ says "We don't know if it went through." Pressing again sends the
**same** request, which is safe: the server recognises it and never does the action twice.

Other pause types, flattening, budgets, policy and anything live stay on the Owner controls page.

## Review check-ins

A check-in is a time you plan to review the run with your separate AI chat. It is only a note for you: the team
never reads it, and it never changes what the team does.

- **Plan**: suggest a time (just after the Trader's next turn), today's usual set (12:30, 16:30, 20:30 and a 22:30
  bedtime check), tomorrow at this time, or a custom time.
- **Copy review prompt**: copies ready-made text to paste into your review chat. It asks the right questions and
  tells the chat to inspect only. Your project folder, notes folder and model are set under
  "Prompt settings (this device only)" and are never sent to Trade Graph.
- **Done** (with an optional short note), **Skip**, **Move** or **Cancel**.
- **Add to my calendar (.ics)** downloads your planned check-ins with a 10-minute reminder. Download it again after
  changes; your calendar won't update by itself.

A check-in is "due" from 15 minutes before its time until an hour after, "overdue" until 6 hours after, then "missed".

## Data rules (for reviewers)

These rules are enforced in code and tests. See `docs/DECISIONS.md` D105–D110.

- **Only saved records.** Every state on HQ is projected on the server (`api/hq*.py`) from persisted records in one
  read snapshot. The browser never infers state. Quiet means idle.
- **AI state comes from evidence, never from cached readiness** (D106). The provider pause state, quota-deferral
  events and the latest saved allowance reading against the stop line, always with its age. HQ never calls
  `ServiceController.status()` or the dashboard's readiness cache.
- **Unknown is never zero.** Unknown values are `null` with a reason.
- **Practice money and real costs are never summed.**
- **Reads are cheap.** One `GET /api/v1/hq` reads bounded records and a 60-second money cache (refreshed early when
  bills, fills or ledger events change). It never runs the activity, journal, health, service-status, configuration
  or review-export projections, and never probes database ownership, so it never takes the database lock (D110).
  The payload stays at most 80 KB on a busy synthetic week (`tests/integration/test_hq_payload.py`, about 66 KB):
  estimated turns more than 48 hours ahead are summarised in `calendar.estimates`. The review prompt's "since"
  counts are exact `COUNT(*)` queries, not the capped lists.
- **Story paging** (D110). A page's `window_start` is the oldest moment it covers completely; it may be later than
  72 hours ago when a capped reader was full. `next_before` continues from there; pages may overlap by 1 ms and the
  page de-duplicates by id.
- **No new authority** (D107). Each decision card maps to one existing owner endpoint with a hard-coded body; the
  page never posts to a URL taken from data. Pause and Resume read the owner revision just before sending; Pause
  retries once with a fresh request id if the revision changed.
- **Content Security Policy stays `'self'`.** No inline scripts or styles; the snapshot travels in a non-executing
  JSON island and all text is inserted as text.
- **Model text** appears only as a short labelled quote (at most 280 characters) of what was saved; never a
  paraphrase and never a chain of thought.
- **AI spending** (T40, D175–D179) comes from `budget_reservations` and `usage_receipts` (Perplexity: price card
  provider `perplexity`; Jev: role `news_alarm`, purpose `news_alarm:jev`), the owner policy each Perplexity round
  recorded (`web_research_runs.policy_json`), `subscription_invocations` for Codex turns and the owner's records
  (migration `0032`, append-only). It is read through `application/ai_spending.py`, cached for 60 seconds behind a
  fingerprint of those tables (`api/hq_spending.py`) and sent as the additive `ai_spending` key. A part with no call
  is `null`, never `0`; it reads no practice-money table. The block is left out, not invented, if the records
  can't be read.
- **Price charts** (T41, D195–D199) come from `GET /api/v1/hq/chart` (`trade-graph.hq.chart.v1`,
  `api/hq_chart_contract.py`), never from the snapshot, so the snapshot's size is unchanged; the page asks for a chart
  only while the panel is on screen. Candles are read point in time as of now from the candle file
  (`kraken_public_ohlc`), opened read-only (`adapters/persistence/market_data_readonly.py`: never created or
  upgraded, private folder and file, every write refused). Indicators are the T27 engine's numbers over the same
  candles (D118; a gap withholds an indicator, as for the Trader); `hq_chart.PLACEMENT` only says where each menu entry
  is drawn. Lines come from `price_alarms`, `order_intents`, `day_plan_entries` and `day_plan_exits`; marks from
  `fills` and `price_alarm_events`. Prices and levels are exact decimal text; indicator values are rounded to eight
  significant digits for drawing. A busy synthetic chart (8 indicators, 59 lines) is about 69 KB, within the
  endpoint's own 80 KB budget. The vendored library (`web/static/vendor/lightweight-charts/`, pinned by
  `VENDOR.json` and `scripts/vendor_lightweight_charts.py --check`) is fetched from HQ's own origin with a pinned
  integrity the first time a chart is shown; its built-in logo is off because it would inject a `<style>` element
  (refused by the policy) and send HQ's address to TradingView. The plain attribution link beside every chart is the
  page's only outside address.
- **Owner check-ins** (migration `0025`, D108) are owner-only and append-only, are not in the review export, the
  financial checkpoint, the activity feed or any agent context, and never bump the owner revision. A process sharing
  the SQLite file is not stopped by a module boundary; that residual risk is accepted for non-financial notes.
- **Safety-stop recovery** (D109): HQ discloses that Stop on an empty account replaces an automatic safety stop. It
  offers no guided recovery until the owner decides (open question Q1).

## Routes

| Route | Who | What |
|---|---|---|
| `GET /` | any signed-in role | HQ page (HTML with the snapshot island) |
| `GET /journal` | any signed-in role | The previous home page, unchanged; accepts `?end_at=` |
| `GET /api/v1/hq` | any signed-in role | HQ snapshot JSON (`trade-graph.hq.v1`); `calendar_days` 1–14, default 7 |
| `GET /api/v1/hq/story` | any signed-in role | Earlier story; `before` (timezone-aware ISO, required), `limit` 1–60 |
| `GET /api/v1/hq/chart` | any signed-in role | One price chart (`trade-graph.hq.chart.v1`); `symbol` (for example `SOL/USD`) and `interval` 1, 5, 15 or 60, both optional |
| `GET /api/v1/owner/checkins` | owner | Check-ins between `from` and `to` (default 7 days back to 30 days ahead) |
| `POST /api/v1/owner/checkins` | owner (cookie sessions also need the CSRF header) | Plan, reschedule, done, skip or cancel a check-in |

Signed-out browser visits to `/` or `/journal` go to `/login`; signed-out API calls get a JSON 401.
