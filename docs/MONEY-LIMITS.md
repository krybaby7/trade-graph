# Money limits and the paper coin list (T32)

Status: built and tested in source on 2026-10-17 by cloud package W3-A (decisions D180–D184, state migration
0033). Not deployed, and the owner's values are not recorded anywhere yet: the owner records them with
`trade-graph money-limits --confirm` at step L3. Paper only. The rules behind it are D138 items 1, 2, 3 and 6
(the owner's settings), D117 (resting entries, protective exits, software keeps the exposure envelope), D127 (no
riba), D131 (no stablecoins or yield tokens) and D116 (money limits, never activity caps).

| Part | Path |
|---|---|
| Owner-policy fields (loss limit, day start, coin rule) | `src/trade_graph/contracts/money_limits.py`, `OwnerPolicy` and `Mandate` in `contracts/models.py` |
| Pure Decimal rules: trading day, loss limit, entries as if filled | `src/trade_graph/kernel/money_limits.py` |
| D127 product check and interest-like postings | `src/trade_graph/kernel/riba.py` |
| Run-time guard and the owner revision | `src/trade_graph/application/money_limits.py` |
| Where entries are authorized and the management step | `Execution.authorize`, `Execution.enforce_money_limits` in `application/execution.py`; `PaperService._management` |
| Coin scope in authority | `AuthorityRecord.require_coin_rule`, `entry_scope` in `application/authority.py`; `owner_scope_covers`, `mandate_within_owner` in `kernel/authority.py` |
| State migration 0033 | `src/trade_graph/adapters/persistence/money_limit_schema.py`, `migrations/versions/0033_money_limits.py` |
| Owner command `money-limits` | `src/trade_graph/market_data_cli/money_limits.py` |
| Tests | `tests/unit/test_money_limit_rules.py`, `tests/property/test_money_limit_properties.py`, `tests/property/test_money_limit_execution_properties.py`, `tests/integration/test_money_limits.py`, `test_money_limits_cli.py`, `test_migration_0033.py`; helpers in `tests/money_limit_support.py` |

## In one minute

- **Your limits become software rules.** The paper account may lose at most a set share of the day's starting
  value (3%), hold at most a set share in one coin (20%) and in coins altogether (80%). Orders still waiting
  to be filled count as if they had filled.
- **A bad day stops new buying, nothing else.** When the account has lost the day's share, new buying stops and
  waiting buy orders are cancelled until the next day starts (00:00 UTC). Safety exits, sales and the checking
  of orders and balances go on, and nothing is sold off.
- **The coin list is a rule.** Any Kraken coin traded in US dollars that passed the coin scanner's checks in its
  latest hourly scan may be bought. If there is no recent scan, nothing new is bought until there is one.
- **No riba.** Futures, perpetuals, short selling, borrowing, lending and earn products are refused. A payment
  that looks like interest or a staking reward is flagged for you.
- **Only you change these.** No department can edit or raise them. Plans from the Trader (T34) stay switched off
  until you record your values.

## The owner-policy fields

`OwnerPolicy.money_limits` (`OwnerMoneyLimits`): `daily_loss_limit_fraction` (above 0, below 1, Decimal),
`trading_day_start_utc` (`HH:MM`, default `00:00`) and `account_currency` (the account's native currency, USD
for the paper account). The amount per coin and the total remain the policy's
`maximum_single_asset_exposure_fraction` and `maximum_gross_exposure_fraction`, enforced as before but counted
as below. A revision holding `money_limits` is the one in which the owner recorded all four values.

`OwnerPolicy.coin_scope` (`CoinScope`): the rule `scanner_checks`, the quote asset (USD), the scan age
(`max_scan_age_seconds`, 7200, W2-B's default) and `when_unknown: refuse_new_entries`. Without it the coin list
stays `allowed_symbols`. A mandate follows the rule with `coin_scope: owner_rule`, which needs a policy that has
one; a listed mandate (the default) still narrows the rule to its own list, which may name any pair the rule
covers. `coin_scope` is left out of the JSON schema, as it is left out of a listed mandate's document: a model
reply (the Leader's mandate change, which OpenAI's strict wire requires in full) matches what the contract
serialises, and only software, the owner command, sets `owner_rule`.

Both blocks are absent from older revisions, and an absent block is left out of the stored document, so every
existing policy and mandate reads and hashes exactly as before. A mandate cannot carry a loss limit or a day
start at all (unknown fields are refused).

## Entries counted as if filled (D138 item 2)

`kernel.money_limits.check_entry` values every entry, and every resting entry already authorized, at the
highest of its own price, the current buy quote and the coin's mark. A resting buy at 60 while the coin trades
at 100 counts at 100, because that is what it is worth the moment it fills. A held coin counts at its mark (the
coin being bought at the highest of its mark, quote and order price). The caps are compared with the equity
left after every counted entry has paid the reserve fee rate and any loss it could take against the mark when
it fills (`equity_floor`), for the coin bought and for every other coin, because a new entry's fees lower the
floor for all of them. With marks unchanged, no sequence of fills of those entries, whole or partial, at or
inside their limits, can then put more than the per-coin or total share of the account into coins: two property
tests show it, one on the rule and one through `Execution` and the paper broker. A coin that grows past its share
because its price rose is not a breach, and no sell-off follows (D138).

This tightens the earlier check for everyone, with or without recorded limits: it used to value a resting buy
and the coin already held at the order's own price. The balance check now runs before the exposure check, so an
order the account cannot pay for says so.

## The daily loss limit (D138 items 1 and 6)

- **Trading day.** From the day start to the next one, in UTC (`kernel.money_limits.trading_day`).
- **Day-start value.** The account's native equity read point-in-time at the day start itself: the books as they
  stood then, with the newest marks observed at or before it, each at most 15 minutes old (`MARK_MAX_AGE`). If
  that is impossible (the portfolio opened later that day, or a held coin's mark was missing or older because the
  service was down), the first valuation the guard can make later that day is used and labelled
  `first_valuation_after_start`. One value per portfolio and day (`trading_day_values`).
- **The limit.** Reached when the day-start value less the current value is at least the fraction of the
  day-start value (at the limit is the limit). Values are equity, so fees paid and open positions at their marks
  count. The current value needs every held coin's mark to be at most 15 minutes old.
- **At the limit.** `Execution.authorize` refuses every new entry (`AuthorityDenied`, "daily loss limit
  reached") as soon as the value is at the limit; it only reads. The management step,
  `Execution.enforce_money_limits`, run by `PaperService._management` on every tick, records the stop for the
  day (`daily_loss_stops`, one per portfolio and day, an `activity_events` row `daily_loss_limit_reached`) and
  cancels every resting or unsent buy of the portfolio. Unsent buys are also re-checked at dispatch. Exits,
  protective exits and reconciliation are not touched.
- **Resume.** The stop belongs to its trading day. At the next day start a new day-start value is taken and
  buying resumes; cancelled entries are not restored (the Trader plans again).
- **Unknown.** Without a day-start value or a current value with fresh marks the limit can't be checked, and new
  entries are refused as stale (`StaleState`), to be retried; never allowed on a guess.

## The coin rule (D138 item 3)

Under `coin_scope` an entry needs `CoinScanStore.coin_rule(symbol, as_of=now, max_age=2 hours)` (W2-B) to answer
`passed_checks`: any Kraken USD spot pair that passed every liquidity and exclusion check of the newest live scan,
not only the shortlist. `excluded` (with the scanner's reasons) and `not_in_catalog` refuse the entry; an unsent
entry is rejected at dispatch. **Unknown** (`no_recent_scan`, `scan_failed`) refuses a new entry as stale and holds
an unsent one until a fresh scan answers. Exits, protective exits and reconciliation never consult the scan, and
an entry already resting at the venue is not cancelled by a later scan. Pairs outside the rule's quote currency
are refused. `owner_scope_covers` lets the paper feed configuration watch any pair the rule can include.

## D127

`kernel.riba.refuse_riba_symbol` refuses every entry on a symbol that is not a plain spot pair `BASE/QUOTE` of
upper-case letters and digits: Kraken Futures names (`PF_`, `PI_`, `FI_`), `PERP`, `SWAP`, dated contracts,
`BASE/QUOTE:SETTLE`, and Kraken's staking or earn variants (`ETH.S`, `DOT.F`, `USD.M`). Margin and leverage are
refused with the policy (`install_policy` refuses `leverage_allowed`), short sales by the spot balance check (an
exit sells only what is held; nothing is borrowed), and there is no lending, staking or earn action at all.
`MoneyLimitGuard.flag_interest_postings` flags, once each, deposits, withdrawals and transfers whose reference
or asset looks like interest, a staking or earn reward, a margin rollover or a funding payment
(`riba_posting_flags`, `activity_events` row `riba_interest_posting`); flagging changes no balance.

## The owner command

```
trade-graph money-limits [--database runtime/trade_graph.sqlite] [--portfolio-id P]
    [--daily-loss-percent 3] [--per-coin-percent 20] [--total-percent 80] [--day-start 00:00]
    [--coins rule|list] [--expect-revision R] [--confirm]
```

Like `mandate-cleanup`: it previews by default and prints JSON (current revision, values, every changed field,
the mandate revision, whether day-plan orders may be installed). `--confirm` records, in one transaction, a new
owner-policy revision `owner-<hex>` (copied from the active one, with the four values and the coin list), a
matching mandate revision for the paper portfolio (the Leader may narrow it again, never raise it) and an
`owner_policy_audit` row naming the revision it replaced, both document hashes and the changes. It is refused
while the paper service holds its lease and, with `--expect-revision`, when another revision became active
since the preview. Recording the same values again writes nothing. Decisions made under the old revision are
refused afterwards, as before. The defaults are the owner's D138 settings; tests record synthetic ones.

## For the next packages

- **W4-A (T34, day plans).** Install every day-plan entry with `Execution.authorize(portfolio_id, decision,
  day_plan=True)`: it refuses until the owner has recorded money limits (`MoneyLimitGuard.require_day_plan_orders`)
  and applies every rule above. Place protective exits with `place_protection` as today; they never wait for the
  gate or the loss limit. A pause and the loss stop both cancel resting buys and keep exits.
- **W5-A (T35, event engine).** Call `await execution.enforce_money_limits(portfolio_id)` at the day start and
  after price updates (the paper service already does on every tick). Its result says whether the day is stopped
  (`money_limits.stopped`) and which intents it cancelled; a stop is a natural wake reason for the Trader.
- **HQ.** `MoneyLimitGuard.status(portfolio_id, policy).document()` gives the day-start value, the loss so far,
  the limit, whether it is reached and why it can't be checked. Refusal reasons already read in plain words. HQ's
  Money tab shows it as "Today's loss limit" (W8-C, D250).
