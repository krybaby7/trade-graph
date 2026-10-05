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

The [protected collector](KRAKEN-CONFORMANCE.md) is already implemented as a
Python interface. Owner-local credential/grant provisioning, an executable
onboarding command and its verified-result integration into Mission Control
remain implementation work. There is currently no working account-test button
or browser credential-entry form. Use the collector's existing signed, pinned,
time-limited authority rather than substituting a caller's success/permission flag.

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
