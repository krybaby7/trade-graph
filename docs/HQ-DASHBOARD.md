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
"Is my money at risk?", the AI allowance gauge and the market weather (price and 20-hour / 50-hour averages).
Practice money and real costs are always shown apart and never added together. Anything not known yet says
"not known yet"; it is never shown as zero.

**Decide**: the buttons. See below.

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
| `GET /api/v1/owner/checkins` | owner | Check-ins between `from` and `to` (default 7 days back to 30 days ahead) |
| `POST /api/v1/owner/checkins` | owner (cookie sessions also need the CSRF header) | Plan, reschedule, done, skip or cancel a check-in |

Signed-out browser visits to `/` or `/journal` go to `/login`; signed-out API calls get a JSON 401.
