# Immutable protected paper image

The repository can build and run a complete offline Linux amd64 protected paper
image. The image includes the digest-pinned Python/Debian base, all frozen runtime
dependency wheels, installed application wheel, and a sealed inventory. Its fixed
default entrypoint verifies the read-only image and owner distribution, then starts
the existing paper service with `--protected-owner`. The service owns its financial
database before admitting the graph and uses the protected six-role handlers when
model operation is separately permitted. A restart preserves the actual current
release and management-only recovery state. It does not reactivate failed source.

This reference image permits no external networking or credential environment. It
does not provide funded provider evidence or select/certify an intended deployment
host. Broader Engineer classes and live/paid authority remain separate gates.

## Build and distribution

Review `deploy/protected-image-inputs.json`, the lockfile, application source and
`deploy/Dockerfile.protected` in the trusted owner checkout. The recorded base was
resolved on 2026-10-04 to
`python@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016`.
It is a proposed build input, not owner release approval. The build backend has its
own exact wheel hash. No repository dependency versions are changed by this workflow.

```bash
uv run python scripts/build_protected_image.py --output /tmp/trade-graph-image-new
uv run python scripts/verify_protected_image.py \
  --build-directory /tmp/trade-graph-image-new --output /tmp/trade-graph-image-proof-new
```

The acquisition container receives only requirements and a fresh wheel output
directory. It fetches binary wheels from the reviewed public package index with
`--require-hashes`. The builder's public trusted CA bundle accommodates a configured
build proxy without disabling TLS validation. That CA bundle does not enter the
runtime image. No provider credential, private database, owner policy or Docker
socket enters acquisition or either build stage. The actual Docker build uses
`--network none` and installs dependencies with `--no-index --require-hashes`.
The project wheel is built without network or dependency resolution using the
hash-pinned build backend.

The builder passes a sorted, fixed-time tar stream as its complete build context.
This avoids a directory-sync heuristic reusing changed same-size inputs with
normalized timestamps. The resulting installed seal must match every expected
input identity before distribution proceeds. Retained output includes the exact
image ID, image archive SHA256, base digest, lock/requirements/wheelhouse/source
hashes, Dockerfile/build-input identities, installed distribution inventory and
protected package/interpreter hash. An image ID binds complete image configuration
and root filesystem layers, including OS and dependencies. A source-only hash does
not establish that closure.

The owner pins the exact image/archive identities outside candidate mutation.
Transport the exact archive through an owner-controlled distribution path, verify
its SHA256, load it, and inspect the resulting exact `sha256:...` image ID. Do not
replace the pin with a tag or recalculate approval from an Engineer checkout.
The scripts create local artifacts; they do not publish images or grant permission.

## Owner mounts and boot

The protected parent runs as UID:GID `10001:10001`. The host owner controls every
ancestor of the mount distribution paths, with no symlinks or group/other writes.
The owner directory is root-owned, generally mode `0750`, group `10001`, containing:

| File | Contents and permissions |
|---|---|
| `runtime-manifest.json` | Complete `dataclasses.asdict(ProtectedRuntimeManifest)` JSON, including array-form operations/source hashes; root:10001, mode `0440`. |
| `graph.py` | Exact owner-approved source providing the scoped graph; root:10001, mode `0440`. |
| `capability.key` | At least 32 private opaque bytes; root:10001, mode `0440`. |
| `paper-config.json` | Optional bounded `PaperRuntimeConfig` JSON; root:10001, mode `0440`. Defaults keep paid calls/public networking disabled. |

The manifest pins the protected package/interpreter hash retained by the exact
approved image. The source hash must already be approved in that manifest, and
the operation tuple for service composition is
`["submit_decision", "invoke_model", "apply_role_result"]`. No source or key is
read from a candidate-controlled directory. The reader uses a verified directory
descriptor, nofollow/nonblocking file open, regular-file/ownership/mode checks and
bounded reads; malformed FIFO, symlink, writable or publicly accessible files fail.

Mount that owner directory read-only at `/run/trade-graph-owner`. Mount only the
private financial state directory at `/var/lib/trade-graph`, owned by `10001:10001`
with mode `0700`. Initialize the USD10,000 virtual paper account using the installed
CLI before starting the service. Retain financial state across image switches;
never roll it back with an executable release.

`ProtectedDeploymentSpec.create_arguments` generates the fixed Docker launch:
read-only root filesystem, no network, all capabilities dropped,
`no-new-privileges`, private process/IPC/cgroup namespaces, 64 processes, 512 MiB
memory with no additional swap, one CPU, zero core dump allowance and a 64 MiB
`noexec,nosuid,nodev` `/tmp` tmpfs. Only the two exact owner/state bind mounts are
admitted with private propagation. The independent host controller must verify
the created container with `verify_container_inspection` before starting it.
The verifier refuses extra mounts, socket/device exposure, environment/entrypoint
overrides, networking or resource/namespace expansion and requires the daemon's
builtin seccomp support. No Docker capability enters the protected parent or child.
Eight standard proxy environment variables are explicitly empty, preventing Docker
client defaults from injecting the operator's proxy configuration into the parent.

The reference image delegates the fixed command:

```text
trade-graph run --mode paper --database /var/lib/trade-graph/trade_graph.sqlite \
  --protected-owner /run/trade-graph-owner
```

## Actual local proof and remaining gates

The verifier creates a fresh narrowly mounted synthetic fixture under `/opt`.
When the caller is not root, an exact-image root helper establishes only this empty
fixture's root-owned distribution and distinct private state identity. It receives
no production key or host socket. An independent read-only helper verifies private
owner file metadata; the UID1000 caller does not read the key. Retained Docker
inspections independently bind mounts, resource configuration and image identity.

Inside the actual installed container, the probe executes 30 malicious child
file/descriptor/network/process/syscall attempts and a memory-allocation attack.
It additionally rejects five actual malformed owner distribution files, including
a FIFO. It places a real synthetic paper order, loses its acknowledgement,
reconciles a fill without redispatch, verifies management during a runaway child,
rolls back only the executable release, then restarts the installed container and
checks that financial history and one submission attempt remain unchanged. Finally
it boots and cleanly stops the image's fixed protected paper service, observing its
durable protected instance and exclusive service lease. There are zero paid or live
transport calls. These are local host/container proofs with synthetic state.

Before T21 production completion, the owner must select the actual host, approve
the image and distribution/mounts there, and repeat capability, egress, resource,
host-mount, recovery and rollback attacks on that platform. A separately reviewed
credential/network profile is required for funded paper connectivity. This offline
launch profile does not silently accept an expanded one. T18 economic results,
T20 live approval and T22 broader Engineer commissioning remain separate.
