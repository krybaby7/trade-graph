# Testing on a Windows PC

Owner direction, 2026-10-05: run on the existing Windows PC during testing and
revisit cloud hosting after the useful operating path is demonstrated. No paid
server is needed for this stage. Claude and Codex subscriptions can support
development work; the current graph's autonomous model adapters use separately
billed APIs. A subscription-backed runtime adapter is not implemented.

## What runs where

The graph and its private records run in **WSL2 Ubuntu**, the Linux environment
inside Windows. Use the Windows browser for Mission Control initially. The iPad
can become a viewer after private network access is configured; it does not run
the graph. The PC must be awake and connected during continuous tests.

Native Windows is not supported by the current runtime: it relies on Linux/POSIX
file permissions, process locks and private storage. Keep the checkout and runtime
under Ubuntu's home directory, not a Windows-mounted `/mnt/c` folder. Initial
dashboard and paper tests need no GPU or paid server. The independent Engineer
checker and full protected runtime require Linux x86-64/seccomp; the selected WSL2
host must prove those checks rather than inheriting this cloud host's results.
Check `uname -m` in Ubuntu: the existing checker requires `x86_64`; an ARM
installation cannot run that checker unchanged.

## 1. Install the local tools

In an administrator PowerShell window:

```powershell
wsl --install -d Ubuntu
```

Follow any restart prompt, then open **Ubuntu** and finish its local user setup.
For an existing WSL installation, check it with `wsl --list --verbose` and use
WSL version 2. See [Microsoft's installation guide](https://learn.microsoft.com/en-us/windows/wsl/install).

In Ubuntu:

```bash
sudo apt update
sudo apt install -y git curl ca-certificates
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Open a fresh Ubuntu terminal so the installed `uv` command is on the path.
The installer reference is [uv's official guide](https://docs.astral.sh/uv/getting-started/installation/).

## 2. Open Mission Control

For a fresh local checkout:

```bash
git clone --branch codex/orchestrator-takeover-2026-10-04 \
  https://github.com/krybaby7/trade-graph.git ~/trade-graph
cd ~/trade-graph
uv sync --frozen --group dev --python 3.12
umask 077
install -d -m 0700 runtime
uv run trade-graph init --database runtime/trade_graph.sqlite
uv run trade-graph dashboard --database runtime/trade_graph.sqlite
```

Clone and initialize once. If this PC already has a checkout/account, inspect it
and preserve its state; start the dashboard against that existing database.

Open `http://localhost:8000/login` in the Windows browser. WSL2 normally forwards
Windows localhost to the Linux server; this still needs checking on the PC.
Use `session_token` from the local private `runtime/owner-session.json` file on
the login page. Keep that token and file out of chat and Git.

Opening Mission Control starts the dashboard, not the trading service. Keep its
Ubuntu terminal open. See [the dashboard guide](MISSION-CONTROL.md) for the graph,
milestones, test history and refresh behavior.

## 3. Run the first checks

In Mission Control, start **Local rehearsal**. It uses scripted market/AI responses
and virtual orders; its passing result verifies the local software path, not real
AI reasoning or Kraken execution. Then start **Public Kraken API** to check actual
public connectivity and market metadata/prices. Neither check needs an AI-provider
key, Kraken account key or deposit.

To inspect the initialized account and run a bounded maintenance tick, use a
second Ubuntu terminal:

```bash
cd ~/trade-graph
uv run trade-graph doctor --database runtime/trade_graph.sqlite
uv run trade-graph run --mode paper --database runtime/trade_graph.sqlite --once
```

For a continuous maintenance service, omit `--once` and keep that terminal open.
The default service does not dispatch paid AI work. After the public check works,
a bounded market-data exercise is:

```bash
uv run trade-graph run --mode paper --database runtime/trade_graph.sqlite \
  --public-data --max-ticks 3
```

Stop any other paper service before this exercise. It contacts Kraken public
data and Frankfurter FX; fills remain local simulations. Actual connectivity must
be checked here even if an offline rehearsal passes.

## 4. Connect the account and AI separately

Follow [Kraken onboarding](KRAKEN-ONBOARDING.md) for the first read-only account
observation. The existing protected collector still needs its owner-local command
and dashboard-result integration. Account verification is already owner-reported;
authenticated graph connectivity is pending.

Current real AI paper evaluation requires an OpenAI or Anthropic API key, reviewed
model configuration and an explicit real spending allowance. Having Claude/Codex
subscription access does not supply credentials or API credit to these adapters.
Official Codex and Claude Code tools have noninteractive interfaces worth
evaluating for a subscription-backed alternative. Our
[source review](reviews/2026-10-05-local-subscription-options.md) records those
interfaces and the remaining support/accounting questions. Prefer that
investigation before committing to separate API spending; it still requires a
new adapter and verified provider support. Do not copy a login token into an
API-key field. Start with the no-paid checks above while that choice is resolved.

## Evidence and later migration

The software commands have Linux verification; this guide is not a completed
Windows/WSL2 or iPad Safari acceptance run. Retain actual PC test outcomes, including
failures. Preserve the existing T17–T22 work and private runtime records; local
success does not enable live trading or mark an unfinished task complete.

Once the selected local path works, revisit an always-on cloud installation with
the same version, verified backups and preserved history. No cloud purchase or
live-capital decision is made by this local testing plan.
