# Exact owner mapping of paper evidence to a live scope

`PinnedPaperLiveMapping` verifies an immutable private file and owner HMAC against
protected service pins. It declares exactly which four paper arms and future
trial correspond to one `LivePilotScope`; it grants no execution authority and
does not authenticate external economics or paper/live friction differences.

The mapping binds the entire live deployment, account, portfolio, venue,
instrument, owner policy and selected system version. It also binds the exact
paper deployment, runtime database device/inode, trial/protocol, retained producer
binding digest, producer controller, market stream, data/friction/regime policy
digests, selected symbols and all four arm portfolio/version/artifact identities.
The agent arm's selected artifact must equal the live selected artifact. Four
distinct paper portfolios must be separate from the live portfolio.

The source loader checks an absolute bounded owner-private regular file, refuses
symlinks/hardlinks/FIFOs and changed source bytes, checks the independent owner
signature and requires a positive verification window of at most 31 days. The
producer check accepts only the exact `PaperProducerBinding` implementation and
compares its complete retained identity to those declarations. Similar market
aliases, a favorable trial or a matching graph name cannot infer any live account
or owner policy.

This mapping removes only a missing explicit identity declaration when a
protected consumer verifies it against independently retained producer facts.
Authenticated market transport, actual provider/invoice provenance, untouched
forward outcomes, economic sufficiency and separate live authorization remain
their own gates. The mapping supplies no writer or HTTP signing route.

The 2026-10-04 implementation ran 26 private-source and exact mapping cases,
including nine independent live-scope drift dimensions, HMAC/source/key/time
failures, four-arm separation and nonblocking private-file checks. Integration
with the actual paper producer is a separate producer-workstream check.
