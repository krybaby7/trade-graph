# Local testing and subscription interface review

Owner direction: test on the existing Windows PC, preferably using existing
Claude/Codex subscriptions, and defer cloud hosting. This review changes no
runtime adapter, spending permission or task acceptance.

## Implemented path

Trade Graph currently invokes OpenAI Responses and Anthropic Messages through
its protected model/budget gateway. No `codex exec` or `claude -p` adapter exists.
Subscription sign-in is not an API credential or credit for those adapters.
Local rehearsals, the dashboard and public Kraken checks need no paid inference.

Windows needs WSL2 Ubuntu because the source uses POSIX ownership, permissions
and `fcntl` locks; the independent checker additionally requires Linux x86-64
seccomp. Actual acceptance on the owner's PC remains pending. See
[the local guide](../WINDOWS-LOCAL-TESTING.md).

## Official interfaces worth evaluating

Read-only source review, 2026-10-05, using official GitHub repositories:

- [Codex subscription sign-in](https://github.com/openai/codex/blob/823ea830c0fd418b09ff02d36cad9a1fff66465b/README.md#using-codex-with-your-chatgpt-plan)
  is documented for supported ChatGPT plans. The pinned
  [exec CLI](https://github.com/openai/codex/blob/823ea830c0fd418b09ff02d36cad9a1fff66465b/codex-rs/exec/src/cli.rs)
  supports noninteractive execution, JSON events, output-schema selection and
  final-message output. Its events expose token usage and failed turns.
- [Claude Code's official changelog](https://github.com/anthropics/claude-code/blob/2bfb629dfaff0c8318047a4beb93cf1dc5b58b18/CHANGELOG.md)
  records Pro subscription support, headless `-p`, structured output, turn/budget
  bounds and subscription usage limits.

These are potential integration surfaces, not proof that a custom unattended
trading workflow is covered by an owner subscription. Direct official pricing,
support and terms pages remained inaccessible through this workspace's proxy
(HTTPS CONNECT 403). Current plan eligibility and permitted use need verification
before choosing or implementing a subscription-backed runtime.

## What a later integration must preserve

Use the official tool's supported authentication; do not extract subscription
login tokens or pass them to direct API adapters. Keep exchange/financial authority
outside the tool and expose only the intended model task. Preserve bounded time,
turns, retries, schema validation, model identity, failures and usage evidence.
Subscription limits and allowances must have explicit treatment; reported tokens
are not an API invoice and cannot establish unlimited free operation.

The preferred next investigation is a supported official CLI route before
committing to separate model API spending. Meanwhile, execute the documented
no-paid PC checks. No model call, credential access, paid request, Windows host
installation or exchange-account observation was performed in this review.
