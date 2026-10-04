# T21 immutable offline image checkpoint

The complete offline protected paper image builds from a digest-pinned Linux amd64
Python/Debian base, hash-verified frozen runtime wheels and a separately hash-pinned
build backend. The actual build has no network or production mounts. Exact archive,
image, source, wheelhouse, lock and installed seal identities are retained privately.
The fixed default boot now uses the protected service composition added by the
integration owner. Deployment operator tooling remains outside the container.

Actual local results on Docker Engine 28.4.0:

- Ruff passed. The 115-test protected financial/department/service/image suite
  passed before six additional container guard cases; the final 48-test image
  security suite passed after that guard hardening.
- Image `sha256:35debc27381f116fba803f0d4ff4f6d48f5487f04519bbbefaec3af857f1eab2`
  built and exported. Archive SHA256:
  `b89c2344a43e7ad52f74ce29381bc8eb61cc1ea2adc5a08efe01396a12747c8c`.
- Rebuilding the exact normalized tar context without network on the same daemon
  produced the identical image ID. This does not claim an independent clean-host
  reproducibility result.
- Four actual containers passed independent pre-start image/resource/mount/env
  inspection. The exact installed package and read-only root/owner mounts passed
  boot preflight. Five malformed owner files, including a FIFO, were refused.
- Thirty child OS attacks were blocked; the 128 MiB child memory bound held.
  Actual synthetic paper execution lost its acknowledgement, reconciled its fill
  with one submission attempt, and preserved financial history through a runaway
  child's failure. Trusted management ran eight times during failure and restored
  the prior successful release. A second container restarted the same private state
  without redispatch or financial-history changes.
- The image's fixed default protected paper service started with its durable
  protected instance and exclusive service lease, then stopped cleanly. Paid and
  live remained disabled. No real provider, venue, order or purchase occurred.

Commands and private artifacts:

```bash
PYTHONPATH=src /workspace/trade-graph/.venv/bin/python scripts/build_protected_image.py \
  --output /tmp/trade-graph-t21-image-20261004-7
PYTHONPATH=src /workspace/trade-graph/.venv/bin/python scripts/verify_protected_image.py \
  --build-directory /tmp/trade-graph-t21-image-20261004-7 \
  --output /tmp/trade-graph-t21-image-proof-20261004-7
```

The retained proof is `/tmp/trade-graph-t21-image-proof-20261004-7/proof.json`;
four full independent inspections are in `container-inspections.json`. Root-owned
synthetic mounts remain under the private `/opt` fixture identified by that proof.
The proof leaves `intended_host_verified`, `owner_deployment_authorization`, paid,
live and broader Engineer authorization false. The final integrated application
requires a fresh build and proof because its complete protected package changes.
Production T21 completion still needs the intended host, owner-pinned distribution
there and repeated actual-host adversarial/recovery verification. Funded networking
requires its separately reviewed profile. See [the deployment contract](../PROTECTED-DEPLOYMENT.md).

Two real integration failures were repaired before the passing proof: normalized
directory mtimes allowed BuildKit's local sync to reuse changed same-size files, so
the builder now transmits exact normalized tar bytes; Docker client proxy defaults
injected environment values, so the fixed launch explicitly empties every standard
proxy variable. Expected-input seal and environment checks rejected both failures.
The corporate proxy CA was admitted only to public hash-verified wheel acquisition;
TLS verification was never disabled and that CA is absent from the runtime image.
