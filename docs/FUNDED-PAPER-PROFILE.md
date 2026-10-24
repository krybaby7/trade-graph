# Separately prepared protected funded-paper profile

The ordinary immutable-image launch remains paper mode with `--network none`,
blank proxy environment, read-only root/owner mounts, private state, dropped
capabilities, seccomp, no-new-privileges and bounded resources. It does not inherit
shell provider credentials. This document describes an explicit additional
preparation profile; it is not intended-host admission or owner spending approval.

An externally pinned `PreparedFundedPaperProfile` binds the immutable application
image, complete owner runtime manifest, deployment ID, exact private paper config,
provider credential file hashes, dedicated internal Docker bridge ID, fixed
private proxy address/port and immutable separately reviewed proxy container/image
identities. The proxy review identity remains an owner-supplied external review
record. A Docker identity check cannot establish that a proxy actually implements
its routing policy. The owner must supply and review that proxy and its external
network separately; this repository does not create an internet-connected proxy.

Before a funded container may start, the host controller independently inspects
the dedicated bridge and proxy. An external bridge, host networking, extra peers,
IPv6, changed image/address, privileged or mutable proxy, published proxy ports
or any proxy mount refuse. The proxy configuration must be baked into its exact
reviewed immutable image; socket aliases and directory mounts cannot bypass this
rule. `ProtectedDeploymentSpec` requires the complete
separate profile and fixed `check-funded`/`boot-funded` actions, and refuses generic
network or ambiguous ordinary boot substitutions. The application container's
existing resource, user, mounts, security and entrypoint checks remain in force.
The proxy must also run as an explicit numeric non-root user, drop all capabilities,
use exact no-new-privileges and independently inspected builtin daemon seccomp,
retain private namespaces and standard proc protections, use `runc`, and have no
added devices, groups, host links, volume inheritance or sysctl changes. Proxy
resource limits and external routing remain part of the separately required owner
review; this local preparation does not establish that policy.

## Parent-only credentials and transport

The read-only root-distributed owner bundle adds `funded-paper-profile.json`,
`openai.key` and/or `anthropic.key`. Existing secure owner-file reads verify root
controlled ancestors, no-follow regular files, private modes and bounded sizes.
The parent requires exact key-file bytes matching the profile before constructing
the model transport. Keys never enter command arguments, environment, mutable
graph context or a Docker socket mount. Only the fixed official OpenAI Responses
and Anthropic Messages HTTPS endpoints for supplied providers are available.
The parent uses its explicit pinned proxy, normal TLS certificate verification,
`trust_env=False` and no redirects. Source, owner config, profile and key changes
make readiness false before later role work.

This profile does not install price cards, set a real allowance, replenish the
operating budget, set either paid permission, or authorize a private venue call.
The configured model registry and persisted owner permission must still both
allow paid calls; all existing reservation, receipt and uncertainty rules remain
mandatory. `check-funded` performs only local secure-file/config validation and
reports `paid_authorization=false`, `live_authorization=false` and
`intended_host_verified=false`. Mutable child network/filesystem syscall denial
continues independently of the credentialed parent's selected network.

Public market/FX transport is not covered by this first funded-provider profile.
A profile with `public_data_enabled=true` refuses. An authentic funded paper soak
therefore still requires a separately reviewed public-data transport/profile and
current provider routes/prices, owner budget, actual keys and intended host.
Default public connectivity failures must be resolved by that approved host
configuration, never by disabling its network controls.

## Verification scope

Synthetic tests exercise owner/file hashes, fixed endpoints, explicit proxy
selection, unchanged offline arguments, pre-start profile/network checks,
credential/config drift and preservation of disabled owner paid/live gates.
Scripted HTTP is not a successful paid call, and synthetic network inspection
documents are not a real intended-host deployment. The final immutable-image
rebuild and ordinary offline Docker proof verify the integrated protected source
separately. No production credentials, funded requests, internet proxy deployment
or live orders are part of this preparation.
