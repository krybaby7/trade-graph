# Kraken connection and iPad access

Continuation, 2026-10-05: the owner reports that Kraken account verification is
complete and Kraken Pro is available. This is account setup progress, not a
successful Trade Graph API test. No account email, login details or credentials
belong in this repository.

## Place to run it during testing

Owner update, 2026-10-05: use the existing **Windows PC with WSL2 Ubuntu** during
testing. This supersedes the earlier cloud-server recommendation. Follow the
[Windows local guide](WINDOWS-LOCAL-TESTING.md) to launch Mission Control, run
the no-paid checks and keep records in private Linux storage. The PC runs the
graph, stores records and contacts Kraken; the browser displays the dashboard.
It must stay awake and connected during continuous tests.

Revisit an always-on Linux cloud server after the local operating path works.
The current cloud coding session is a temporary development environment, not
an always-on deployment. No hosting purchase or real operating allowance is
selected by this plan. Claude/Codex subscriptions and the current runtime's
separately billed model API calls remain distinct.

The dashboard is mobile-friendly, but the running server currently listens only
on its own computer. An authenticated private HTTPS access path must be configured
before there is a live address to open on the iPad. The existing local address
`127.0.0.1` cannot reach the cloud server from the iPad. See
[Mission Control](MISSION-CONTROL.md) for current viewing and test capabilities.

## First connection: read the actual Kraken account

1. Prepare the Windows/WSL2 installation and confirm that the public Kraken check succeeds.
   This workspace's latest public check failed through its network proxy, so
   account verification alone does not resolve that connectivity issue.
2. Create a dedicated read-only API key in Kraken Pro's API settings. Permit
   querying funds, open orders and trades, closed orders and trades, and ledger
   entries. Current permission names may vary slightly. Enable only those read
   permissions; leave all other permissions disabled, including trading, order
   modification/cancellation and withdrawals. Keep the key and secret
   in private files on the host, outside Git, chat and the browser dashboard.
   The graph uses API credentials, not the Kraken login email or password.
3. Provision the existing protected account collector for one bounded read-only
   observation. Check actual instrument rules, fee tier, balances, open orders
   and bounded trade/ledger history. A new empty account can establish
   connectivity; depositing or trading is unnecessary for this first check.
4. Verify the retained private result and show a redacted outcome in Mission
   Control. The dashboard should distinguish a successful account read from
   remaining order-execution and live-trading checks.

## Owner-local read-only command

The CLI connects the existing [protected collector and verifier](KRAKEN-CONFORMANCE.md)
to Mission Control's **Kraken read-only account check**. Run it yourself in an
interactive Ubuntu terminal after reviewing a dedicated key's read permissions:

```bash
cd ~/trade-graph
source "$HOME/.local/bin/env"
uv run trade-graph kraken-read-only \
  --database runtime/trade_graph.sqlite \
  --owner-directory "$HOME/.local/share/trade-graph-owner/kraken" \
  --symbol BTC/USD
```

The command prompts for the key and secret without terminal echo and requires
explicit bounded read-only authorization before any network request. It refuses
redirected/noninteractive credential entry. API credentials are used only in that
short-lived owner process and are not saved to files, environment variables,
command arguments, the graph or browser. Never paste credentials into chat or
Mission Control. The owner directory must remain outside the checkout and runtime.
Signed grants, separate verification keys, private run intent, exact pins and raw
captures belong only in that owner-private directory. Keep that directory out of
Git, graph context, diagnostic output and shared evidence.

The initial command supports one symbol, BTC/USD. Its default observation is
bounded to 60 seconds, 128 requests and five history pages, with a declared
seven-day history start. Typed confirmation authorizes only the existing
`observe_read_only_venue_account` action. The native observation contract uses
`mode="live"` for account identity; it creates no live portfolio, policy, mandate,
trading service or paid-work grant. It submits, modifies, cancels and withdraws
nothing. A deposit is unnecessary.

Mission Control imports only verifier-derived historical metadata: fixed stage
labels, observation and verification times, actual versus synthetic transport,
authenticated-private-read count, freshness expiry and pending checks. It receives
no account identifiers, holdings, fee values, orders/trades, raw responses,
credentials or owner verification keys. The browser Run button stays disabled;
run the owner-local command in Ubuntu. A partial prefix stays partial, and old
observations remain historical rather than becoming fresh on dashboard refresh.
This check does not establish full reconciliation.

Implementation verification uses synthetic HTTPS fixtures only. Actual
authenticated Kraken operation remains unverified until the owner runs the
command privately. Successful local public checks are separate evidence.

### Required read-only permissions

Select only **Query Funds**, **Query Open Orders & Trades**, **Query Closed
Orders & Trades**, and **Query Ledger Entries** in Kraken Pro. Leave all other
permissions disabled, including order modification/cancellation and withdrawals.
See [Kraken's key configuration guide](https://support.kraken.com/articles/360000919966-how-to-create-an-api-key).
The adapter reads BalanceEx, TradeVolume, OpenOrders, ClosedOrders, QueryOrders,
TradesHistory and QueryLedgers; these match the four query grants.

Successful requests establish only the reads observed. The existing
`key_permission_inventory_unverified` and `withdrawals_absent_unverified` checks
stay pending: this command does not audit every permission or prove native
account ownership. API-key two-factor authentication requires client OTP support,
which this command does not implement. Follow
[Kraken's API key security guidance](https://support.kraken.com/articles/api-key-security)
for private handling and suitable restrictions.

## After that connection works

Continue the [crypto next steps](CRYPTO-NEXT-STEPS.md): supply usable history to
Research and Trader, connect actual market observations, establish one budgeted
AI-provider path and collect forward paper evidence. Paper orders remain local
simulations even when their prices come from Kraken.

Check the exact Kraken spot product's current practice or validation-only route
before preparing connected order tests; a validation-only path is not yet
implemented here. A small real-order trial comes later with a separate capital
decision and completed prerequisites. Read-only API success does not enable
trading, prove profitability or close T19/T20 acceptance.
