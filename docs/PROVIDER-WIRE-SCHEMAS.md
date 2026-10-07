# Provider wire schemas

Reviewed on 2026-10-02 using the providers' official SDK source. The documentation
URLs in S03/S06 were blocked by the workspace proxy (HTTP 403). These exact source
versions were fetched and inspected without an API call:

- [OpenAI strict schema conversion, commit e5de2e5](https://github.com/openai/openai-python/blob/e5de2e5656fb3d4fa70f050195382e6a4d59f806/src/openai/lib/_pydantic.py).
- [Anthropic schema conversion, commit 18f2554](https://github.com/anthropics/anthropic-sdk-python/blob/18f25547f20cf5f01da69ac611e700e3bc9ebf21/src/anthropic/lib/_parse/_transform.py).

The adapters translate a detached copy of each domain JSON Schema. Every object
is closed to extra properties, tagged `oneOf` branches become nested `anyOf`, and
constant tags become single-value enums. Local definitions and references remain
local; unsupported roots, dynamic object maps and unsupported semantic constructs
receive a local `unsupported` result before a reservation or HTTP request.

OpenAI requires every named property, including defaulted fields. Their original
types remain intact: empty evidence lists use `[]`, defaulted booleans use boolean
values, and fields originally permitting null still permit null. No new nullable
alternative is introduced and no response null is converted into omitted/defaulted
evidence. This restricts the provider output to explicitly populated domain fields.
Existing software/scripted replies can still omit fields with domain defaults.

Anthropic retains the original required list. Following its SDK subset, unsupported
numeric bounds, string bounds/patterns and array maxima become descriptive guidance.
The supported string formats and `minItems` values 0/1 remain schema keywords.
The OpenAI projection also treats string length limits and defaults as guidance.
The protected gateway validates replies against the original bounded Draft 2020-12
schema, and each role still performs its original Pydantic and authority checks.
Provider grammar guidance cannot weaken those checks. Invalid dispatched replies
retain their supplied usage and receipts.

The gateway builds and checks the body for every new attempt, including repairs and
fallbacks, before budget effects. A previously journalled attempt recovers its saved
response or uncertainty before a new wire build; schema translation never causes a
replay. Registered strict function schemas receive the same copied translation.

`test_provider_wire_schemas.py` checks both wire dialects with the actual six role
contracts, populated nested Research/Learning/Optimisation/Leader/Engineer replies,
forbidden extras, null/default semantics, raw recording transport, cost boundaries,
fallback and durable recovery. This is fixture and static-source evidence.
Credentialed provider acceptance remains pending an authorized funded probe; no
such probe was performed for this change.
