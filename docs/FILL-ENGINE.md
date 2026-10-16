# Fill engine and cost profiles (T28)

Status: implemented and tested in source on 2026-10-16 by cloud package W1-B (branch
`claude/nice-thompson-j88ec4`, for `claude/day-trader-engine-20261012`). Not deployed. Decisions D145–D149 are
proposed in `planning/handoffs/W1-B.md`; the integrator copies them into the decision log. They build on D14,
D119 and D126.

In plain words: when the paper account or the practice range pretends to trade, this one piece of software
decides whether an order would really have filled, at what price and for what fee. It is deliberately
cautious. A buy order waiting at $100 fills only if the price actually trades below $100, not when it just
touches $100. A safety exit (stop-loss) that the price jumps over fills at the worse price after the jump,
minus a little extra slippage. If one candle reached both the stop-loss and the take-profit, the software
assumes the loss. The fees come from three dated fee records (Kraken now, Kraken after USD10,000 of monthly
volume, OKX Europe), each with its source and the date it was checked, and a fee record is never edited
after publication: a change gets a new record, so old results keep the fees they were computed with.

| Part | Path |
|---|---|
| Contracts: cost profile, fill policy, order, protective exit, fill | `src/trade_graph/contracts/fills.py` |
| The engine (pure functions over exact decimals) | `src/trade_graph/kernel/fills.py` |
| Fee records | `src/trade_graph/kernel/cost_profiles.json` |
| Fee-record book and content-hash pins | `src/trade_graph/kernel/cost_profiles.py` |
| Paper broker, now filling through the engine | `src/trade_graph/adapters/brokers/paper.py` |
| Tests | `tests/unit/test_fill_engine.py`, `tests/unit/test_cost_profiles.py`, `tests/property/test_fill_engine_properties.py`, `tests/integration/test_paper_fill_engine.py` |

The engine and the fee records sit in the protected kernel: they decide paper results and fees, which agents
must not edit (AGENTS.md). The old private matcher `adapters/brokers/matching.py` is removed.

## Cost profiles

Rates are per side and charged in the quote asset (USD for USD pairs). Round trip = one buy and one sell.

| Profile id | Maker / taker per side | Round trip limit / market | Figure date | Source |
|---|---|---|---|---|
| `kraken-spot-starting-tier@1` | 0.40% / 0.80% | 0.80% / 1.60% | 2026-09-29 | [Kraken fee schedule](https://www.kraken.com/features/fee-schedule) (S08) |
| `kraken-spot-after-10k-volume@1` | 0.22% / 0.38% | 0.44% / 0.76% | 2026-10-12 | Kraken fee schedule, as corrected in the D126 venue session |
| `okx-eea-spot@1` | 0.10% / 0.20% | 0.20% / 0.40% | 2026-09-25 | OKX's official notice for OKX Europe (D126); [OKX fees](https://www.okx.com/fees) |

**Not re-read on 2026-10-16.** The cloud build session could not reach kraken.com or okx.com (the network
policy refused the connections), so each record keeps the build-plan figure and says so in its
`check_result` (`unreachable_from_build_environment`). Re-check the three pages at step L2. If a figure
differs, add a revision; do not edit the existing one.

Rules of the book (`CostProfileBook.from_document`):

- Each record has a `profile_id` and a `revision` from 1 upward without gaps, a venue, a tier description,
  maker and taker rates (0 to 5%, no rebates), the date it takes effect, the date it was recorded, at least
  one HTTPS source with the figure's date, the date the page was checked and the result of that check, and
  the decisions it rests on.
- Every published revision is pinned by the SHA-256 of its canonical content in `PUBLISHED_PINS`. Loading
  refuses a record that differs from its pin, a record without a pin and a pin without a record.
- A later revision must take effect later than the one before. `get(id)` gives the newest revision,
  `get(id, revision=n)` and `resolve("id@n")` a fixed one, `effective(id, day)` the one in force on a day.
- Every fill carries `cost_profile_ref` and `cost_profile_sha256`, so a stored result can always be
  reproduced under the exact fees it used.

To add a revision: append the record to `cost_profiles.json` with the next revision number and a later
`effective_from`, compute its hash (`CostProfile.model_validate(record).content_sha256`), add the pin, and
add a test. Never change an existing record or pin.

Rates passed directly in configuration (`PaperBroker(taker_rate=...)`, or a test changing a broker's fee) are
`CostProfile.explicit(...)` records: basis `explicit_configuration`, no source, and an id that spells out the
rates. They are never mistaken for a published schedule.

## Fill policies

A fill policy holds the simulation assumptions that are not venue facts. Each has an id, a revision and a
content hash that every fill records.

| Policy | Volume share | Entry slippage | Exit slippage | Used by |
|---|---|---|---|---|
| `paper-quote@1` | 100% of the displayed size or quote volume; 50% of the order when no volume is known | 0 | 0.10% | Paper broker (real quotes) |
| `bar-replay@1` | 10% of the bar's volume | 0.10% | 0.10% | Practice range (candles) |

The paper broker refuses a policy with entry slippage: its buy reservations are sized at the quote, and the
quote already includes the spread. Slipped prices are rounded to the instrument's price increment against
the trader (buys up, sells down) when the increment is known.

## Entry rules

The caller keeps `remaining` between events and passes each event once.

- **No hindsight.** An event that began before the order's `eligible_after_utc` never fills it. On candles,
  the bar must open at or after that time, so the bar whose close a decision used can never fill the order.
  `excluded_event_ref` excludes one named event as well.
- **Resting limit on candles.** Fills only when trades print beyond the limit (`low < limit` for a buy,
  `high > limit` for a sell). A touch is not a fill. It fills at the limit, never at a better gap price, as a
  maker, for at most the policy's share of the bar's volume. A bar with no volume fills nothing.
- **Marketable limit on candles.** A buy limit at or above the placement price (a sell at or below) would
  cross the book on arrival, so it is a taker: at the next open plus slippage when the open is inside the
  limit, never beyond the limit; otherwise at the limit if trades later print beyond it. Limits on candles
  therefore need `placement_price`.
- **Market order on candles.** The next open plus slippage, as a taker, for at most the volume share.
- **Quotes (paper broker).** A resting limit fills when the opposite best price reaches it, because a real
  resting order there would have been matched; this is a crossed book, not a touch (T05). A market order
  takes the displayed size at the touch.
- Candle fills inside the bar are stamped at the bar's close; fills at the open at its open. Quote fills are
  stamped when the quote became available.

## Protective exits

`ProtectiveExit` is a stop-loss and an optional take-profit above it on one long spot position (D127). The
first leg to fill closes the whole exit, which cancels the other. Both legs fill as triggered taker sells of
the whole quantity, with the exit slippage.

| Situation (candle) | Result |
|---|---|
| Low reaches the stop | Stop fills from the stop level, less slippage (`stop`) |
| Opens below the stop (a gap) | Stop fills from the worse open, less slippage (`stop_gap`) |
| High only touches the take-profit | Nothing |
| High trades beyond the take-profit | Fills from the take-profit level, less slippage, never a better gap open (`take_profit`) |
| One bar reaches both | The stop is assumed (`stop_both_hit`) |
| The bar the position was entered in | Only the stop can trigger (`stop_entry_bar`): the order of the entry and the bar's high and low is unknown, so a later low is assumed and a later high is not. Pass `armed_in_this_candle=True` |

On quotes, the stop triggers when the bid is at or below it and fills from the bid less slippage, so a gap
gives the worse price; the take-profit needs the bid above it and fills from its level less slippage. Without
a bid there is no fill and no invented price.

Minimums: `check_order_rules` and `check_exit_rules` refuse an order or exit a venue would refuse (quantity
and price increments, minimum quantity, minimum value). Nothing is resized.

## In the paper broker

`PaperBroker` builds a `SimOrder` (market, limit) or a stop-only `ProtectiveExit` (its existing sell stop)
from each stored order and asks the engine. The default fees are `kraken-spot-starting-tier@1`, the same
rates as the earlier fixed tier (D14). `cost_profile=` selects another record. Each fill's terms (reason,
reference price, profile and policy references and hashes) are added to the broker order's stored document
under `fill_terms`; the `FillRecord` itself is unchanged, and orders stored before this change stay readable.
A changed profile or fee applies to later fills only. Stop fills now include the 0.10% exit slippage, so a
stop gapped to a bid of 90 on a 0.1 tick fills at 89.9.

Not yet in the paper broker: take-profit legs and one-cancels-the-other exits as submitted orders. The engine
supports them; attaching them to entries is T34 (day plans).

## For the practice range (T29) and day plans (T34)

- Replays call `fill_order_on_candle` and `trigger_exit_on_candle` with a profile from the book and
  `BAR_REPLAY_POLICY` (or a stated stricter policy to vary pessimism), one completed candle at a time from
  the candle store.
- When an entry fills inside a candle, arm its exit with `eligible_after_utc` set to the entry fill time and
  evaluate that same candle with `armed_in_this_candle=True`; later candles use `False`.
- Store `cost_profile_ref`, `cost_profile_sha256`, `fill_policy_ref` and `fill_policy_sha256` with each
  report so reruns reproduce it.
