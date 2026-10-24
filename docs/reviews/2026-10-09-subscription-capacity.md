# Subscription capacity and operational diagnostics — 2026-10-09

Owner-authorized T17/T21 continuation, integration owner Codex, branch `codex/resource-capacity-20261009`. Base `a0be95d` matched every one of the 186 installed Python modules. Executable changes are `986c19d` and `97f1290`; verifier startup-read handling is `60cf18e`/`905e2c7`. This slice does not close broader acceptance gates.

## Measured cause and deployment

Host-side cgroup sampling distinguished processes from kernel tasks, including threads. Before repair, idle usage was three processes/24 tasks; routine native quota metadata reached six processes/61 tasks. The native client used 35 threads, then exited with both namespace wrappers. Repeated cycles returned to baseline, giving no evidence of an accumulating orphan leak. A terminated worker remained a zombie until its dashboard parent shut down; it held no leases or database ownership and was subsequently reaped.

The subscription container's 64-task and 512 MiB bounds were too close to ordinary native-helper activity. Memory-limit events increased during metadata collection; the cgroup total included substantial file cache, so the 512 MiB reading does not measure Python heap alone. No OOM kill or automatic restart occurred. The graph's heartbeat and normal collection remained healthy; observed inspection-launch failures are consistent with shared task-cap exhaustion, but discarded historical output prevents assigning every failure to a proved cause. WSL and Windows available physical memory comfortably supported a finite 1 GiB container.

The supported subscription create specification and independent verifier now require exactly 192 tasks, 1 GiB memory and equal memory-swap, with one CPU. Equal memory-swap grants no additional swap. Other deployment profiles retain their prior bounds. Read-only root, UID, private namespaces, deny-default seccomp, dropped capabilities, dedicated internal network/proxy, private owner/state mounts and finite tmpfs are unchanged. Negative cases reject old, arbitrary and unlimited subscription limits.

An authenticated owner MANAGE_ONLY pause and graceful service drain released both leases and original-inode ownership. The supported financial manifest transition retained the original key, database inode, complete prior witness scopes, accepted recovery-gap evidence and historical financial/AI commitments. A separately pinned replacement image then passed the exact created-container verifier, supported dashboard boot, Start Trading and reconciled Owner Resume. The old container and image are preserved stopped. No tasks were replayed, strategy/schedule configuration changed or inference forced.

The next naturally scheduled Trader completed and applied its decision successfully. Following that run, cgroup task highwater was 61 and memory highwater approximately 463 MiB; PID-limit and memory-limit event counts remained zero. This validates the observed ordinary run, not a universal worst-case inference maximum. Public collection and current heartbeat remained active.

## Bounded diagnostics

Controller-launched workers retain allowlisted lifecycle, warning/error classes, witness-publication failures, degraded/recovered state, stream byte counts and periodic own-process resource counters. Three private files of at most 256 KiB each rotate in the private state directory, with 0700 directory/0600 files, nofollow opens and single-link checks. No raw stream text, tracebacks, model conversations, request/reply bodies, credentials, filenames or arbitrary exception messages are retained. Monitoring adds no thread/process.

The deployed dashboard's worker path activates the logger before CLI startup. Python worker resource samples are instantaneous once/minute and exclude native descendants and total cgroup usage; host counters establish whole-container peaks. Hard kills before cleanup cannot write an exit event. Caught CLI failures may log SystemExit while the original sanitized class remains in service records. Direct foreground boot/management entrypoints do not activate this controller logging context. Diagnostic writes are observational and lack witness-style fsync durability; failing diagnostic storage cannot interrupt financial reconciliation. Earlier discarded output cannot be reconstructed.

## Verification and limits

196 focused deployment/network/controller/service/diagnostic tests and 92 financial continuity tests passed. Ruff and diff checks passed. Synthetic child-launch tests cover real process cleanup, privacy and bounded rotation. Actual immutable-image probes passed protected child/owner-loader attacks, finance/recovery/default boot, single-worker inode locking, auxiliary-descriptor lifetime, 1,000 concurrent WAL commits, full integrity and foreign keys. Nine actual subscription isolation checks passed without model inference or provider/exchange API requests.

The general image verifier initially encountered a transient read-only observer error during synthetic WAL/SHM startup. It now treats that state as pending within the existing finite readiness loop, which still requires actual healthy admission; the repeated complete image proof passed. This verifier-only change does not relax production startup or isolation.

Complete machine evidence, original record hashes, actual inspection timestamps, account state, quota, scheduling details and Research expiry explanation remain in private ignored storage. Sol-only routing, empty fallback, strict greater-than-40-percent quota admission, disabled live/billed/extra routes and the accepted historical recovery limits remain in force. No actual-model provider echo, profitability, full-history recovery or broader live/funded acceptance is claimed.

Research alignment is a separate proposal only: retain current frequency, phase daily Research shortly before one existing Trader within the same completed-hour source-validity window, and anchor expiry to the source's absolute freshness boundary. Never extend an hourly claim merely to reach Trader or reset its freshness at application time. No alignment was deployed in this repair.
