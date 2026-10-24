#!/usr/bin/env python3
"""Local Docker image/mount attacks, protected finance and default boot proof.

This creates only a fresh synthetic fixture under /opt. Docker tooling and image
distribution stay in this host controller, and are never mounted in the worker.
The result explicitly leaves intended-host and owner deployment gates open.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from trade_graph.kernel.deployment_image import (
    DeploymentImagePin,
    ProtectedDeploymentSpec,
    file_sha256,
    verify_container_inspection,
    verify_image_archive,
    verify_image_inspection,
)
from trade_graph.kernel.deployment_probe import probe_source_sha256
from trade_graph.kernel.runtime_manifest import ProtectedRuntimeManifest, canonical_json, document_sha256

GRAPH = '''
def propose(context):
    return {"action":"hold", "rationale":"Synthetic installed service", "invalidation":"New quote",
            "horizon_seconds":3600, "strategy_id":"slow-trend"}
def graph(context):
    if context["operation"] == "invoke_model":
        return {"node":"invoke_model", "guidance":"Use bounded source evidence."}
    return {"node":"apply_role_result", "payload":context["result"]}
'''

PREPARE = '''
import json, os, sys
from pathlib import Path
from trade_graph.cli import main
root = Path('/fixtures')
assert not list(root.iterdir()), 'fixture must be fresh'
doc = json.load(sys.stdin)
owner, state = root / 'owner', root / 'state'
owner.mkdir(mode=0o750)
state.mkdir(mode=0o700)
for name, payload in doc.items():
    assert name in ('runtime-manifest.json', 'graph.py', 'capability.key', 'paper-config.json')
    path = owner / name
    path.write_text(payload)
    os.chown(path, 0, 10001)
    path.chmod(0o440)
os.chown(owner, 0, 10001)
for name, mode, uid in [('unsafe-public.json',0o444,0), ('unsafe-writable.json',0o660,0),
                        ('unsafe-owned.json',0o440,10001)]:
    path = owner / name
    path.write_text('{}')
    path.chmod(mode)
    os.chown(path,uid,10001)
(owner / 'unsafe-link.json').symlink_to('runtime-manifest.json')
os.mkfifo(owner / 'unsafe-fifo.json',0o440)
os.chown(owner / 'unsafe-fifo.json',0,10001)
assert main(['init','--database',str(state / 'trade_graph.sqlite')]) == 0
for path in state.rglob('*'):
    os.chown(path, 10001, 10001)
os.chown(state, 10001, 10001)
'''
VERIFY_HOST = '''
import json, sys
from dataclasses import asdict
from pathlib import Path
from trade_graph.kernel.deployment_image import DeploymentImagePin, ProtectedDeploymentSpec
from trade_graph.kernel.runtime_manifest import ProtectedRuntimeManifest
doc = json.load(sys.stdin)
manifest = doc['manifest']
manifest['approved_source_sha256'] = tuple(manifest['approved_source_sha256'])
manifest['operations'] = tuple(manifest['operations'])
spec = ProtectedDeploymentSpec(DeploymentImagePin(**doc['image']), ProtectedRuntimeManifest(**manifest),
                               Path(doc['owner']), Path(doc['state']))
spec.verify_host_paths()
print(json.dumps({'root_owner_distribution_verified':True}))
'''
SERVICE_STATE = '''
import json, sqlite3
from pathlib import Path
path = Path('/fixtures/state/trade_graph.sqlite')
try:
    conn = sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True)
    result = {'protected_running':conn.execute(
        "SELECT count(*) FROM protected_runtime_instances WHERE status='RUNNING'").fetchone()[0],
        'service_leases':conn.execute(
            "SELECT count(*) FROM process_leases WHERE lease_name='paper-service'").fetchone()[0],
        'paid_receipts':conn.execute('SELECT count(*) FROM usage_receipts WHERE synthetic=0').fetchone()[0]}
except sqlite3.OperationalError:
    # Startup can recreate WAL/SHM before the read-only observer attaches.
    # The host's existing finite readiness loop must still establish success.
    result = {'protected_running':0, 'service_leases':0, 'startup_read_pending':True}
print(json.dumps(result))
'''


def run(arguments: list[str], *, payload: str | None = None, timeout: int = 120) -> str:
    try:
        return subprocess.run(arguments, input=payload, text=True, check=True, capture_output=True,
                              timeout=timeout).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("synthetic Docker verification failed: " + exc.stderr[:2000]) from None


def verify(build_directory: Path, output: Path) -> dict:
    if not build_directory.is_absolute() or not output.is_absolute() or output.exists():
        raise ValueError("absolute build directory and fresh private output required")
    output.mkdir(parents=True, mode=0o700)
    pin = DeploymentImagePin(**json.loads((build_directory / "image-pin.json").read_bytes()))
    verify_image_archive(pin, build_directory / "protected-image.tar")
    run(["docker", "image", "load", "--input", str(build_directory / "protected-image.tar")])
    image = json.loads(run(["docker", "image", "inspect", pin.image_id]))[0]
    verify_image_inspection(pin, image)
    seal = json.loads((build_directory / "image-seal.json").read_bytes())
    if document_sha256(seal) != pin.seal_sha256:
        raise PermissionError("distributed image seal differs from owner pin")
    security = json.loads(run(["docker", "info", "--format", "{{json .SecurityOptions}} "]))
    manifest = ProtectedRuntimeManifest(schema_version=1, protected_package_sha256=pin.protected_package_sha256,
        deployment_id="deployment", approved_source_sha256=(*probe_source_sha256(),
        hashlib.sha256(GRAPH.encode()).hexdigest()),
        operations=("submit_decision", "invoke_model", "apply_role_result"))
    root = Path("/opt") / ("trade-graph-local-proof-" + uuid.uuid4().hex)
    if root.exists() or root.is_symlink():
        raise ValueError("local proof requires a fresh isolated owner mount")
    # Docker creates just this fresh fixture root. The privileged helper receives
    # only synthetic stdin and that empty directory. Its only added capability
    # is CHOWN for establishing the distinct protected runtime identity.
    helper = ["docker", "run", "--rm", "-i", "--network", "none", "--read-only", "--cap-drop", "ALL",
              "--cap-add", "CHOWN", "--security-opt", "no-new-privileges", "--user", "0:0", "--workdir", "/",
              "--entrypoint",
              "python", "--volume", f"{root}:/fixtures", pin.image_id, "-I", "-B", "-c"]
    run([*helper, PREPARE], payload=canonical_json({
        "runtime-manifest.json": canonical_json(asdict(manifest)), "graph.py": GRAPH,
        "capability.key": "synthetic-image-proof-parent-key-32-bytes-41",
        "paper-config.json": canonical_json({"tick_interval_seconds": 0.05}),
    }))
    spec = ProtectedDeploymentSpec(pin, manifest, root / "owner", root / "state")
    # The root deployment operator verifies secure leaf permissions. In this
    # local environment a narrowly mounted, owner-image helper supplies that
    # read-only root operation; the UID1000 test caller cannot read private keys.
    run(["docker", "run", "--rm", "-i", "--network", "none", "--read-only", "--cap-drop", "ALL",
         "--security-opt", "no-new-privileges", "--user", "0:0", "--workdir", "/", "--entrypoint", "python", "--mount",
         f"type=bind,src={root},dst={root},readonly", pin.image_id, "-I", "-B", "-c", VERIFY_HOST],
        payload=canonical_json({"manifest": asdict(manifest), "image": asdict(pin),
                                "owner": str(spec.owner_directory), "state": str(spec.state_directory)}))
    reports, inspections = [], []

    def create(action: str) -> str:
        name = "trade-graph-proof-" + uuid.uuid4().hex
        identity = run(spec.create_arguments(name=name, action=action))
        container = json.loads(run(["docker", "inspect", identity]))[0]
        try:
            verify_container_inspection(spec, image, container, security, action=action)
        except BaseException:
            run(["docker", "rm", "--force", identity])
            raise
        inspections.append(container)
        return identity

    for action in ("check-boot", "probe", "probe"):
        identity = create(action)
        try:
            raw = run(["docker", "start", "--attach", identity], timeout=45)
            report = json.loads(raw)
            state = json.loads(run(["docker", "inspect", identity]))[0]["State"]
            if state["ExitCode"] != 0:
                raise AssertionError("installed protected image probe failed")
            reports.append(report)
        finally:
            run(["docker", "rm", "--force", identity])
    if (reports[0].get("manifest_sha256") != manifest.sha256
            or reports[1].get("phase") != "attacks_and_recovery_verified"
            or reports[2].get("phase") != "restart_verified" or reports[2].get("restart_dispatched") != 0):
        raise AssertionError("independent image preflight/recovery expectations failed")
    identity = create("boot")
    try:
        run(["docker", "start", identity])
        ready = None
        for _ in range(40):
            state = json.loads(run(["docker", "inspect", identity]))[0]["State"]
            if not state["Running"]:
                raise AssertionError("default protected image boot failed: " + run(["docker", "logs", identity]))
            # No Docker socket enters the boot container; this independent root
            # observer has only the synthetic fixture mounted read-only.
            ready = json.loads(run(["docker", "run", "--rm", "--network", "none", "--read-only",
                "--cap-drop", "ALL", "--cap-add", "DAC_OVERRIDE", "--security-opt", "no-new-privileges",
                "--user", "0:0", "--workdir", "/", "--entrypoint", "python", "--mount",
                f"type=bind,src={root},dst=/fixtures,readonly", pin.image_id, "-I", "-B", "-c", SERVICE_STATE]))
            if ready["protected_running"] == 1 and ready["service_leases"] == 1:
                break
            time.sleep(0.1)
        else:
            raise AssertionError("default protected service never became ready")
        run(["docker", "stop", "--time", "10", identity], timeout=20)
        default_boot = json.loads(run(["docker", "logs", identity]).splitlines()[-1])
        if default_boot["live_enabled"] is not False or default_boot["paid_calls_enabled"] is not False:
            raise AssertionError("default image boot enabled external authority")
        reports.append({"phase": "default_protected_service_boot_verified", "ready": ready,
                        "outcome": default_boot})
    finally:
        run(["docker", "rm", "--force", identity])
    result = {"schema_version": 1, "image_id": pin.image_id, "archive_sha256": pin.archive_sha256,
              "seal_sha256": pin.seal_sha256, "manifest_sha256": manifest.sha256,
              "inspected_containers": len(inspections), "proofs": reports,
              "local_mount_root": str(root), "root_owner_distribution_verified": True,
              "synthetic": True, "intended_host_verified": False, "owner_deployment_authorization": False,
              "live_authorization": False, "paid_authorization": False, "deployed_engineer_authorization": False}
    (output / "container-inspections.json").write_text(canonical_json(inspections) + "\n")
    (output / "proof.json").write_text(canonical_json(result) + "\n")
    (output / "image-archive-sha256.txt").write_text(file_sha256(build_directory / "protected-image.tar") + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.build_directory, args.output)
    print(canonical_json({"image_id": result["image_id"], "proof": str(args.output / "proof.json"),
                          "local_proof_verified": True, "intended_host_verified": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
