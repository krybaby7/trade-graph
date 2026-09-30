"""Apply an allowlisted patch in a temporary git repository and attest from the trusted runner."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from trade_graph.adapters.engineering.provenance import scrubbed_env
from trade_graph.kernel.authority import path_is_protected

ALLOWLIST_PREFIXES = ("artifacts/", "prompts/", "strategies/templates/")


class EngineerRunner:
    def __init__(self, source_root: Path) -> None:
        self.source_root = source_root

    def stage(self, destination: Path) -> str:
        source_root = self.source_root.resolve()
        if (destination.is_symlink() or source_root == destination.resolve()
                or source_root.is_relative_to(destination.resolve())):
            raise PermissionError("stage cannot replace or contain the source")
        if destination.exists() and any(destination.iterdir()):
            raise PermissionError("stage already contains retained evidence")
        destination.mkdir(parents=True, exist_ok=True)
        for prefix in ALLOWLIST_PREFIXES:
            source = self.source_root / prefix
            if source.is_symlink():
                raise PermissionError("symlink source is not an artifact")
            if not source.exists():
                continue
            if any(item.is_symlink() for item in source.rglob("*")):
                raise PermissionError("symlink artifacts are forbidden")
            target = destination / prefix
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        subprocess.run(["git", "init"], cwd=destination, check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=destination, check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=engineer@example.invalid",
                "-c",
                "user.name=Engineer",
                "commit",
                "-m",
                "baseline",
                "--allow-empty",
            ],
            cwd=destination,
            check=True,
            capture_output=True,
        )
        return self.hash_tree(destination)

    def apply_files(self, root: Path, files: dict[str, str]) -> list[str]:
        if not files:
            raise ValueError("advice without a file is not an artifact")
        changed = []
        for relative, content in files.items():
            normalized = relative.replace("\\", "/")
            if Path(normalized).is_absolute() or ".." in Path(normalized).parts:
                raise PermissionError(normalized)
            if path_is_protected(normalized) or not normalized.startswith(ALLOWLIST_PREFIXES):
                raise PermissionError(normalized)
            target = (root / normalized).resolve()
            if not target.is_relative_to(root.resolve()):
                raise PermissionError(normalized)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            changed.append(normalized)
        return changed

    def attest(self, root: Path) -> dict:
        """Run the installed checks module. The worktree is an argument, not ``sys.path``."""
        tested_hash = self.hash_tree(root)
        neutral = Path(tempfile.mkdtemp(prefix="tg-checks-"))
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "trade_graph.adapters.engineering.checks", str(root)],
                cwd=neutral,
                env=scrubbed_env(),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        finally:
            shutil.rmtree(neutral, ignore_errors=True)
        current_hash = self.hash_tree(root)
        changed = tested_hash != current_hash
        return {
            "command": f"{sys.executable} -m trade_graph.adapters.engineering.checks",
            "exit_code": completed.returncode if not changed else 1,
            "stdout": completed.stdout,
            "stderr": completed.stderr + (" artifact changed during checks" if changed else ""),
            "content_hash": current_hash,
        }

    def hash_tree(self, root: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(item for item in root.rglob("*") if item.is_file() and ".git" not in item.parts):
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()
