#!/usr/bin/env python3
"""Build and export a complete pinned image with an offline Docker build.

Only reviewed wheel/SQLite source acquisition uses the network. Installation uses
--require-hashes/--no-index and the actual build uses --network none. Output is a
private reviewable image/archive pin, not an owner approval or a publication.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import ssl
import subprocess
import tarfile
import urllib.request
import zipfile
from pathlib import Path

from trade_graph.kernel.deployment_image import (
    DeploymentImagePin,
    directory_sha256,
    file_sha256,
    verify_image_inspection,
)
from trade_graph.kernel.runtime_manifest import canonical_json, document_sha256

ROOT = Path(__file__).resolve().parents[1]

# The same probe records the runtime during sealing and independently checks the
# exact content-addressed image afterward. /proc maps proves Python actually
# loaded the pinned native library; importing a substitute Python module cannot
# satisfy this check. This script never opens the installation database.
SQLITE_RUNTIME_PROBE = '''from pathlib import Path
import hashlib
import json
import platform
import sqlite3
import sys

path = Path(sys.argv[1])
document = json.loads(path.read_bytes())
expected = document["sqlite_source"]
with sqlite3.connect(":memory:") as connection:
    source_id = connection.execute("select sqlite_source_id()").fetchone()[0]
    options = sorted(row[0] for row in connection.execute("pragma compile_options"))
    integrity = connection.execute("pragma integrity_check").fetchall()
libraries = {line.split()[-1] for line in Path("/proc/self/maps").read_text().splitlines()
             if "libsqlite3.so" in line}
library = Path("/usr/local/lib/libsqlite3.so.0")
if (sqlite3.sqlite_version != expected["version"] or source_id != expected["source_id"]
        or libraries != {str(library)} or library.is_symlink()
        or not library.is_file() or sqlite3.threadsafety != 3
        or "THREADSAFE=1" not in options or integrity != [("ok",)]
        or sys.version_info[:2] != (3, 12)):
    raise PermissionError("Python did not load the reviewed SQLite runtime")
record = {"version": sqlite3.sqlite_version, "source_id": source_id,
          "library_path": str(library),
          "library_sha256": hashlib.sha256(library.read_bytes()).hexdigest(),
          "compile_options": options, "python_version": platform.python_version(),
          "libc": list(platform.libc_ver())}
if sys.argv[2:] == ["--record"]:
    if "sqlite_runtime" in document:
        raise PermissionError("SQLite runtime identity is already recorded")
    document["sqlite_runtime"] = record
    path.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\\n")
elif sys.argv[2:] or document.get("sqlite_runtime") != record:
    raise PermissionError("sealed SQLite runtime differs from the loaded native library")
print(json.dumps(record, sort_keys=True, separators=(",", ":")))
'''


def stage_sqlite_source(context: Path, inputs: dict, *, archive: Path | None = None) -> dict:
    """Acquire an exact official amalgamation; stage only verified C/header bytes.

    The build itself remains offline. An explicitly supplied archive is supported
    for offline acquisition and is subject to the same independent checks.
    No archive extraction API is used and no shell/build scripts are admitted.
    """
    policy = inputs.get("sqlite")
    required = {"version", "source_id", "archive_url", "archive_sha256", "archive_sha3_256",
                "amalgamation_sha3_256", "compiler_image"}
    if (type(policy) is not dict or set(policy) != required
            or any(type(value) is not str for value in policy.values())
            or not re.fullmatch(r"3\.[0-9]{1,2}\.[0-9]{1,2}", policy["version"])
            or not re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} [0-9a-f]{64}",
                                policy["source_id"])
            or not re.fullmatch(r"python@sha256:[0-9a-f]{64}", policy["compiler_image"])
            or any(not re.fullmatch(r"[0-9a-f]{64}", policy[name])
                   for name in ("archive_sha256", "archive_sha3_256", "amalgamation_sha3_256"))):
        raise ValueError("exact reviewed SQLite source and compiler identities required")
    major, minor, patch = (int(part) for part in policy["version"].split("."))
    root = f"sqlite-amalgamation-{major}{minor:02d}{patch:02d}00"
    if policy["archive_url"] != f"https://sqlite.org/{policy['source_id'][:4]}/{root}.zip":
        raise ValueError("exact official version-specific SQLite archive URL required")
    maximum = 16 * 1024 * 1024
    if archive is None:
        with urllib.request.urlopen(policy["archive_url"], context=ssl.create_default_context(),
                                    timeout=60) as response:
            if response.geturl() != policy["archive_url"]:
                raise ValueError("SQLite acquisition redirected away from the reviewed URL")
            raw = response.read(maximum + 1)
    else:
        if (not archive.is_absolute() or archive.is_symlink() or not archive.is_file()
                or archive.resolve() != archive or not 0 < archive.stat().st_size <= maximum):
            raise ValueError("bounded absolute SQLite archive without links required")
        raw = archive.read_bytes()
    if (not 0 < len(raw) <= maximum or hashlib.sha256(raw).hexdigest() != policy["archive_sha256"]
            or hashlib.sha3_256(raw).hexdigest() != policy["archive_sha3_256"]):
        raise ValueError("SQLite archive differs from reviewed SHA256/SHA3-256")
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        members = bundle.infolist()
        allowed = {root + "/"} | {root + "/" + name for name in ("sqlite3.c", "sqlite3.h", "sqlite3ext.h", "shell.c")}
        if (len({item.filename for item in members}) != len(members)
                or any(item.filename not in allowed or item.file_size > maximum
                       or item.external_attr >> 16 & 0o170000 == 0o120000 for item in members)):
            raise ValueError("SQLite archive contains unexpected or linked members")
        sources = {name: bundle.read(root + "/" + name) for name in ("sqlite3.c", "sqlite3.h")}
    if hashlib.sha3_256(sources["sqlite3.c"]).hexdigest() != policy["amalgamation_sha3_256"]:
        raise ValueError("SQLite amalgamation differs from the official release hash")
    for source in sources.values():
        for name, value in (("SQLITE_VERSION", policy["version"]), ("SQLITE_SOURCE_ID", policy["source_id"])):
            found = re.findall(rb"^#define\s+" + name.encode() + rb'\s+"([^"\n]+)"', source, re.MULTILINE)
            if found != [value.encode()]:
                raise ValueError("SQLite source version/commitment differs from reviewed release")
    target = context / "sqlite-source"
    target.mkdir()
    for name, source in sources.items():
        (target / name).write_bytes(source)
    probe = context / "verify-sqlite-runtime.py"
    probe.write_text(SQLITE_RUNTIME_PROBE)
    return {"sqlite_source": policy, "sqlite_source_sha256": directory_sha256(target),
            "sqlite_runtime_probe_sha256": file_sha256(probe)}


def run(arguments: list[str], *, output: bool = False) -> str:
    result = subprocess.run(arguments, cwd=ROOT, check=True, timeout=600,
                            stdout=subprocess.PIPE if output else None, text=True)
    return result.stdout.strip() if output else ""


def _unique_fields(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("native input JSON cannot contain duplicate fields")
        result[name] = value
    return result


def _native_file(directory: Path, filename: str, maximum_bytes: int) -> Path:
    path = directory / filename
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= maximum_bytes:
        raise ValueError("native inputs require bounded regular files without links")
    return path


def stage_subscription_tools(directory: Path, context: Path, inputs: dict) -> dict:
    """Admit explicit reviewed native files, never a recursive credential directory.

    The private manifest pins base-matching Debian runtime packages. Acquisition
    and release-signature review happen before this offline assembler is invoked.
    Package metadata and every copied byte are checked again before Docker runs.
    """
    if not directory.is_absolute() or directory.is_symlink() or not directory.is_dir():
        raise ValueError("an absolute native input directory without links is required")
    if directory.resolve() != directory:
        raise ValueError("native input parent links are forbidden")
    policy_path = ROOT / "deploy/subscription-native-inputs.json"
    policy = json.loads(policy_path.read_bytes(), object_pairs_hook=_unique_fields)
    manifest_path = _native_file(directory, "manifest.json", 65536)
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes, object_pairs_hook=_unique_fields)
    required = {"schema_version", "platform", "base_image", "claude_sha256", "packages"}
    optional = {"codex_sha256"} if "codex" in policy else set()
    if (not isinstance(manifest, dict) or not required <= set(manifest) or set(manifest) - required - optional
            or manifest["schema_version"] != 1 or policy.get("schema_version") != 1
            or any(manifest.get(name) != inputs.get(name) or policy.get(name) != inputs.get(name)
                   for name in ("platform", "base_image"))
            or manifest["claude_sha256"] != policy["claude"]["sha256"]):
        raise ValueError("native manifest must match reviewed platform, base and Claude release")
    claude = _native_file(directory, "claude", 512 * 1024 * 1024)
    if file_sha256(claude) != manifest["claude_sha256"]:
        raise ValueError("native Claude SHA256 differs from the reviewed release")
    packages = manifest["packages"]
    if not isinstance(packages, list) or not 1 <= len(packages) <= 8:
        raise ValueError("a bounded reviewed bubblewrap package set is required")
    seen_packages, seen_files, sources = set(), {"claude", "manifest.json"}, [(claude, manifest["claude_sha256"])]
    if "codex_sha256" in manifest:
        codex = _native_file(directory, "codex", 512 * 1024 * 1024)
        if (manifest["codex_sha256"] != policy["codex"]["sha256"]
                or file_sha256(codex) != manifest["codex_sha256"]):
            raise ValueError("native Codex SHA256 differs from the reviewed release")
        sources.append((codex, manifest["codex_sha256"]))
        seen_files.add("codex")
    for package in packages:
        if (not isinstance(package, dict) or set(package) != {
                "filename", "package", "version", "architecture", "sha256"}
                or package["package"] not in policy["allowed_debian_packages"]
                or package["package"] in seen_packages or package["architecture"] != "amd64"
                or not isinstance(package["version"], str)
                or re.fullmatch(r"[0-9][A-Za-z0-9.+:~_-]{0,127}", package["version"]) is None
                or not isinstance(package["filename"], str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+%~_-]{0,199}\.deb", package["filename"]) is None
                or package["filename"] in seen_files
                or not isinstance(package["sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", package["sha256"]) is None):
            raise ValueError("native package scope, metadata or filename is not reviewed")
        path = _native_file(directory, package["filename"], 32 * 1024 * 1024)
        if file_sha256(path) != package["sha256"]:
            raise ValueError("native Debian package SHA256 differs from its reviewed manifest")
        fields = {}
        for line in run(["dpkg-deb", "--field", str(path), "Package", "Version", "Architecture"],
                        output=True).splitlines():
            name, separator, value = line.partition(": ")
            if separator:
                fields[name.lower()] = value
        if any(fields.get(name) != package[name] for name in ("package", "version", "architecture")):
            raise ValueError("native Debian package metadata differs from its reviewed manifest")
        seen_packages.add(package["package"])
        seen_files.add(package["filename"])
        sources.append((path, package["sha256"]))
    if "bubblewrap" not in seen_packages:
        raise ValueError("the reviewed package set must include bubblewrap")
    target = context / "subscription-tools"
    target.mkdir()
    try:
        for source, digest in sources:
            destination = target / source.name
            shutil.copyfile(source, destination)
            if file_sha256(destination) != digest:
                raise ValueError("native input changed while staging")
        (target / "manifest.json").write_bytes(manifest_bytes)
        return {"subscription_tools": {
            "claude": policy["claude"],
            **({"codex": policy["codex"]} if "codex_sha256" in manifest else {}),
            "packages": packages,
            "manifest_sha256": file_sha256(target / "manifest.json"),
            "policy_sha256": file_sha256(policy_path),
            "inputs_sha256": directory_sha256(target),
        }}
    except BaseException:
        shutil.rmtree(target)
        raise


def subscription_dockerfile(original: bytes, *, enabled: bool, codex: bool = False) -> bytes:
    """Preserve default bytes; add offline native tools only before sealing."""
    if not enabled:
        return original
    marker = b"COPY build-inputs.json /build-inputs.json\n"
    if original.count(marker) != 1:
        raise ValueError("reviewed Dockerfile seal insertion point changed")
    commands = [
        "dpkg -i /subscription-tools/*.deb",
        "mkdir -p /opt/trade-graph",
        "cp /subscription-tools/claude /opt/trade-graph/claude",
        "chmod 0755 /opt/trade-graph/claude /usr/bin/bwrap",
        "/usr/bin/bwrap --version",
        "/opt/trade-graph/claude --version",
        "rm -rf /subscription-tools",
    ]
    if codex:
        commands[-1:-1] = ["cp /subscription-tools/codex /opt/trade-graph/codex",
                           "chmod 0755 /opt/trade-graph/codex", "/opt/trade-graph/codex --version"]
    additions = "COPY subscription-tools /subscription-tools\nRUN " + " && \\\n    ".join(commands) + "\n"
    return original.replace(marker, additions.encode() + marker)


def build(output: Path, *, subscription_tools: Path | None = None,
          sqlite_source: Path | None = None) -> DeploymentImagePin:
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
    native_inputs = ({} if subscription_tools is None
                     else stage_subscription_tools(subscription_tools, context, inputs))
    sqlite_inputs = stage_sqlite_source(context, inputs, archive=sqlite_source)
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
    dockerfile = subscription_dockerfile((ROOT / "deploy/Dockerfile.protected").read_bytes(),
                                         enabled=subscription_tools is not None,
                                         codex="codex" in native_inputs.get("subscription_tools", {}))
    (context / "Dockerfile").write_bytes(dockerfile)
    retained_inputs = {"schema_version": 1, "base_image": inputs["base_image"],
                       "source_date_epoch": inputs["source_date_epoch"],
                       "lock_sha256": file_sha256(ROOT / "uv.lock"),
                       "requirements_sha256": file_sha256(requirements),
                       "build_requirements_sha256": file_sha256(context / "build-requirements.txt"),
                       "acquisition_ca_sha256": file_sha256(context / "acquisition-ca.pem"),
                       "wheelhouse_sha256": directory_sha256(wheelhouse),
                       "source_sha256": directory_sha256(source),
                       "dockerfile_sha256": file_sha256(context / "Dockerfile"), **native_inputs, **sqlite_inputs}
    (context / "build-inputs.json").write_text(canonical_json(retained_inputs) + "\n")
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
                        f"SQLITE_COMPILER_IMAGE={inputs['sqlite']['compiler_image']}", "--build-arg",
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
    # Re-execute the probe in the immutable final image and compare actual
    # library bytes, version, source ID and compile options against its seal.
    runtime = json.loads(run(["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
                              "--security-opt", "no-new-privileges", "--entrypoint", "python", image_id,
                              "-I", "-B", "-c", SQLITE_RUNTIME_PROBE, "/opt/trade-graph/image-seal.json"],
                             output=True))
    if seal.get("sqlite_runtime") != runtime:
        raise PermissionError("built image SQLite library differs from its seal")
    (output / "sqlite-runtime.json").write_text(canonical_json(runtime) + "\n")
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
    parser.add_argument("--subscription-tools", type=Path,
                        help="reviewed native Claude and base-matching Debian package input directory")
    parser.add_argument("--sqlite-source", type=Path,
                        help="optional already acquired official SQLite archive; all reviewed hashes remain mandatory")
    args = parser.parse_args()
    pin = build(args.output, subscription_tools=args.subscription_tools, sqlite_source=args.sqlite_source)
    print(canonical_json({"image_id": pin.image_id, "archive_sha256": pin.archive_sha256,
                          "build_directory": str(args.output), "owner_approval": False,
                          "intended_host_verified": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
