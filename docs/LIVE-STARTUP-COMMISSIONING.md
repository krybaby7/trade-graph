# Protected live startup and dashboard commissioning

Updated 2026-10-06. This source implementation is not live commissioning or
owner authorization. The installed WSL paper database, owner policy, private
Kraken evidence and keys are not modified by these implementation tests. No
private Kraken request, real order or withdrawal was made for this task.

## Supported entrypoints and exact scope

The protected image entrypoint supports `check-live`, `boot-live`, and
`boot-live-dashboard`. All three require a separate, root-distributed
`live-network-profile.json`, matching immutable image/runtime/protected-package
pins, a dedicated inspected internal network, and a separately reviewed fixed
Kraken/public-FX proxy. The protected parent remains nonroot, read-only, with no
capabilities, no new privileges, builtin seccomp, private process namespaces,
bounded resources, a read-only owner mount and one private writable state mount.
The host deployment controller checks the created container before starting it;
the Docker socket is never mounted inside the protected service.

The live financial database must already contain exactly the one live portfolio
in `LiveRuntimeConfig.scope`. Paper portfolios and additional live portfolios
are refused using a read-only query before migration, commission-key access or
transport creation. Initialization/funding of this distinct journal is owner
provisioning, not a conversion of USD10,000 virtual paper equity into real funds.
Runtime assembly does not make requests. An actual commissioned exclusive
`LiveService` first reconciles durable intents/attempts and account holdings;
only then can deterministic allocation/permission gates admit discretionary
execution. Unknown outcomes retain their holds and are not replaced.

The dashboard uses a broker that cannot perform private effects. Viewing it
cannot acquire Kraken keys or make private requests. Start Trading launches or
attaches to the one exclusive service inside the same verified container; that
child repeats full protected admission before constructing a credentialed
transport. Static dashboard readiness is labelled
`requires_final_service_admission`, not a commissioned-live result.

## Optional owner-approved dashboard publication

`PreparedLiveNetworkProfile.dashboard_host_port` defaults to null. Publication
requires the owner to distribute and independently review a replacement profile
with an explicit integer port in 1024–65535. `boot-live-dashboard` alone creates
`--publish 127.0.0.1:<approved-host-port>:8000/tcp`. Exact pre-start inspection
rejects wildcard bindings, other ports/protocols, extra host bindings and
publication on `boot-live`/`check-live`. The entrypoint passes the hidden
`--protected-network-bind` capability; the CLI verifies live mode, the real
protected process boundary and the approved publication before it binds the
container listener to all container interfaces. The general `--host` interface
continues to permit only loopback.

Docker's official [port publishing documentation](https://docs.docker.com/engine/network/port-publishing/)
describes explicit loopback binding. Intended-host commissioning must verify
actual host/container reachability and refusal of external access; neither a
scripted inspection nor a port string proves the deployed network boundary.
The existing loopback paper dashboard is not opened or converted by this path.

## Owner provisioning still required

An actual installation must independently provision and retain:

- An owner-pinned current protected image/archive, complete runtime manifest,
  signed readiness bundle and distinct root-controlled distribution/state paths.
- A `live-config.json` with real scoped allocation, maximum loss, operating
  allowance, account/deployment/portfolio/version identity, live enablement and
  an exact commission-file hash. `live_configuration_digest` hashes normalized
  substantive settings excluding only `commission_sha256`, avoiding circular
  hashes while the root-distributed config independently pins commission bytes.
- Independent venue, operations and economics reviews of complete pinned actual
  sources, signed with distinct protected issuer keys. Synthetic evidence,
  unsigned statements and model-generated approvals do not satisfy these gates.
- Actual authenticated read-only Kraken observations for balances, open/closed
  orders, trades and fees; account/key eligibility and read/trade-only permissions
  with withdrawals absent; current adapter uncertainty/cancel/fill/restart
  conformance. First admission requires the fresh original observation. After
  admission the immutable observation is checked at its original timestamp and
  the protected service supplies current whole-account reconciliation health.
- Intended-host OS isolation attacks, independent management/restart protection,
  backup/restore and delivered alert verification, plus actual complete economics
  and usage/cost evidence or an explicitly signed bounded diagnostic mandate.
- Exact protected Kraken credential hashes and explicit owner live authorization
  matching the independent real allocation and budget. Credentials are read only
  after production admission and never enter mutable/model context.

Expired admission blocks new increases. A previously admitted exact commission
can restart in management-only mode to reconcile/protect existing exposure;
expired authorization never creates permission for replacement/increased orders.
The live start hook recovers interrupted scoped owner-command receipts while
holding the actual financial database inode lock. Recovery retains uncertainty
and stronger owner/System/Leader position-management pauses; it never replays
requests. A concurrent dashboard command can be terminally fenced as uncertain
and require review. The read-only dashboard broker cannot perform native pause
management or native resume verification. The protected exclusive worker performs
those actions after actual fresh reconciliation.

## Bounded owner Resume control

In live mode, the authenticated, CSRF-protected POST /api/v1/owner/resume
queues a local request and returns HTTP 202. It accepts the existing owner
request_id/expected_revision pair. Queuing makes no broker or model call,
cannot create or reactivate an owner grant, and requires the one selected live
portfolio and its active exclusive service. One pending request is allowed;
repeat identity replays the current persisted result, while a different request
is refused until the first request has a terminal result.

The request expires after 120 seconds and binds the service run, owner revision,
current grant and exact owner pause. The worker first reconciles durable orders,
the entire native account, fees and order ownership. It preserves system pauses,
unresolved orders/billing, account discrepancies, incomplete protection and
expired, revoked or RECOVERY_REQUIRED grants. A newer Stop or owner change
cancels an older queued Resume.

Only a current ACTIVE grant with unchanged signed authorization and full
protected readiness may lift an eligible owner pause. The provisional RUNNING
write, readiness revalidation and terminal successful receipt share one SQLite
writer transaction. Failure rolls back the pause and records a bounded reason.
An interrupted predecessor request cannot be applied by its successor; it
terminates as cancelled and requires another explicit owner request. The service
status API exposes live_resume with its queued/succeeded/failed/cancelled state.
Paper mode retains its existing reconciliation-based Resume behavior.

Pause/stop controls must keep protection running until the financial state is
verified flat, or retain an explicit manage-only/flatten management requirement.
No process can protect positions while its host is offline; that limitation is
part of the independent operations review.

## Credential-free implementation verification

`tests/integration/test_live_startup_service.py` exercises exclusive ownership,
unknown-outcome holds, scripted account failures, expired authorization restart,
private-file denial by the actual confined graph RPC, exact scope refusal and
static prerequisite failures. `tests/security/test_live_deployment.py` exercises
fixed live entrypoints, immutable pins, separate network/proxy inspection,
loopback publication and adversarial mount/capability/port substitutions.
These are implementation/conformance tests with synthetic brokers and admission
stand-ins. They establish neither actual Kraken conformance nor intended-host
commissioning, economic viability, continuous operation or live authorization.

tests/integration/test_live_owner_resume.py additionally verifies local-only
queueing, owner/CSRF authorization, duplicate identities, concurrent request
refusal, successful scripted resumption, unresolved orders/billing, account
mismatch, expired/revoked/recovery grants, newer Stop fencing, retained system
FLATTEN, bounded expiry, atomic interruption rollback, current-readiness failure
and refusal of a synthetic transport. These tests do not commission live use.
