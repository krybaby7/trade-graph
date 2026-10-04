#!/usr/bin/env python3
"""Build and export a complete pinned image with an offline Docker build.

Only reviewed wheel acquisition uses the network. Both image stages install with
--require-hashes/--no-index and the actual build uses --network none. Output is a
private reviewable image/archive pin, not an owner approval or a publication.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import ssl
import subprocess
import tarfile
from pathlib import Path

from trade_graph.kernel.deployment_image import (
    DeploymentImagePin,
    directory_sha256,
    file_sha256,
    verify_image_inspection,
)
from trade_graph.kernel.runtime_manifest import canonical_json, document_sha256

ROOT = Path(__file__).resolve().parents[1]


def run(arguments: list[str], *, output: bool = False) -> str:
    result = subprocess.run(arguments, cwd=ROOT, check=True, timeout=600,
                            stdout=subprocess.PIPE if output else None, text=True)
    return result.stdout.strip() if output else ""


def build(output: Path) -> DeploymentImagePin:
    if not output.is_absolute() or output.exists():
        raise ValueError("choose a fresh absolute private build output directory")
    output.mkdir(parents=True, mode=0o700)
    context = output / "context"
    context.mkdir(mode=0o700)
    inputs = json.loads((ROOT / "deploy/protected-image-inputs.json").read_bytes())
    if (inputs.get("schema_version") != 1 or inputs.get("platform") != "linux/amd64"
            or not re.fullmatch(r"[a-z0-9][a-z0-9./_-]*@sha256:[0-9a-f]{64}", inputs["base_image"])
            or not re.fullmatch(r"setuptools==[0-9.]+ --hash=sha256:[0-9a-f]{64}",
                                inputs["build_backend_requirement"])
            or type(inputs.get("source_date_epoch")) is not int or inputs["source_date_epoch"] <= 0):
        raise ValueError("reviewed digest-only base and hash-pinned build backend required")
    requirements = context / "requirements.txt"
    requirements.write_text(run(["uv", "export", "--locked", "--offline", "--no-dev", "--no-emit-project",
                                "--format", "requirements-txt"], output=True) + "\n")
    (context / "build-requirements.txt").write_text(inputs["build_backend_requirement"] + "\n")
    wheelhouse = context / "wheelhouse"
    wheelhouse.mkdir()
    # Preserve the deployment builder's trusted CA set for its reviewed proxy;
    # it is public certificate material, not a private credential. Package hashes
    # remain mandatory. The acquisition CA is not installed in the runtime image.
    ca_file = ssl.get_default_verify_paths().cafile
    if ca_file is None:
        raise ValueError("trusted acquisition CA bundle required")
    shutil.copyfile(ca_file, context / "acquisition-ca.pem")
    # This trusted acquisition container receives only its requirements/output
    # staging directory, never runtime mounts, a socket, secrets or credentials.
    run(["docker", "run", "--rm", "--platform", inputs["platform"], "--cap-drop", "ALL",
         "--user", f"{os.getuid()}:{os.getgid()}",
         "--security-opt", "no-new-privileges", "--mount", f"type=bind,src={context},dst=/build",
         inputs["base_image"], "python", "-m", "pip", "download", "--disable-pip-version-check", "--no-cache-dir",
         "--index-url", "https://pypi.org/simple", "--cert", "/build/acquisition-ca.pem",
         "--require-hashes", "--only-binary=:all:",
         "--dest", "/build/wheelhouse", "-r", "/build/requirements.txt", "-r", "/build/build-requirements.txt"])
    source = context / "source"
    source.mkdir()
    for name in ("pyproject.toml", "README.md"):
        if (ROOT / name).is_symlink():
            raise ValueError("source file links forbidden")
        shutil.copyfile(ROOT / name, source / name)
    for path in sorted((ROOT / "src/trade_graph").rglob("*")):
        if path.is_symlink():
            raise ValueError("package links forbidden")
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        if path.suffix not in {".py", ".typed", ".json", ".html", ".css", ".js", ".svg"}:
            raise ValueError("unexpected package input; review before image inclusion")
        target = source / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    retained_inputs = {"schema_version": 1, "base_image": inputs["base_image"],
                       "source_date_epoch": inputs["source_date_epoch"],
                       "lock_sha256": file_sha256(ROOT / "uv.lock"),
                       "requirements_sha256": file_sha256(requirements),
                       "build_requirements_sha256": file_sha256(context / "build-requirements.txt"),
                       "acquisition_ca_sha256": file_sha256(context / "acquisition-ca.pem"),
                       "wheelhouse_sha256": directory_sha256(wheelhouse),
                       "source_sha256": directory_sha256(source),
                       "dockerfile_sha256": file_sha256(ROOT / "deploy/Dockerfile.protected")}
    (context / "build-inputs.json").write_text(canonical_json(retained_inputs) + "\n")
    shutil.copyfile(ROOT / "deploy/Dockerfile.protected", context / "Dockerfile")
    # Feed exact normalized tar bytes, avoiding BuildKit's local directory sync
    # heuristic reusing same-size files after normalized mtimes across builds.
    context_archive = output / "build-context.tar"
    with tarfile.open(context_archive, "w", format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(context.rglob("*")):
            info = archive.gettarinfo(str(path), arcname=path.relative_to(context).as_posix())
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = inputs["source_date_epoch"]
            info.mode = 0o755 if path.is_dir() else 0o644
            if path.is_file():
                with path.open("rb") as stream:
                    archive.addfile(info, stream)
            else:
                archive.addfile(info)
    image_file = output / "image-id.txt"
    with context_archive.open("rb") as stream:
        subprocess.run(["docker", "build", "--network", "none", "--platform", inputs["platform"],
                        "--build-arg", f"BASE_IMAGE={inputs['base_image']}", "--build-arg",
                        f"SOURCE_DATE_EPOCH={inputs['source_date_epoch']}", "--iidfile", str(image_file), "-"],
                       stdin=stream, cwd=ROOT, check=True, timeout=600)
    image_id = image_file.read_text().strip()
    # Read metadata from the exact content-addressed image, independent of tags.
    raw_seal = run(["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges", "--entrypoint", "python", image_id,
                    "-I", "-B", "-c",
                    "from pathlib import Path; print(Path('/opt/trade-graph/image-seal.json').read_text())"],
                   output=True)
    seal = json.loads(raw_seal)
    if any(seal.get(name) != value for name, value in retained_inputs.items()):
        raise PermissionError("built image did not retain the reviewed inputs")
    (output / "image-seal.json").write_text(canonical_json(seal) + "\n")
    archive = output / "protected-image.tar"
    run(["docker", "image", "save", "--output", str(archive), image_id])
    pin = DeploymentImagePin(image_id=image_id, base_image=inputs["base_image"],
        archive_sha256=file_sha256(archive), seal_sha256=document_sha256(seal),
        protected_package_sha256=seal["protected_package_sha256"],
        **{name: retained_inputs[name] for name in ("lock_sha256", "requirements_sha256", "wheelhouse_sha256",
                                                   "source_sha256")})
    from dataclasses import asdict

    inspection = json.loads(run(["docker", "image", "inspect", image_id], output=True))[0]
    verify_image_inspection(pin, inspection)
    (output / "image-pin.json").write_text(canonical_json(asdict(pin)) + "\n")
    (output / "image-inspection.json").write_text(canonical_json(inspection) + "\n")
    return pin


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    pin = build(args.output)
    print(canonical_json({"image_id": pin.image_id, "archive_sha256": pin.archive_sha256,
                          "build_directory": str(args.output), "owner_approval": False,
                          "intended_host_verified": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
