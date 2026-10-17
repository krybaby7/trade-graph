"""The protected image carries the indicator engine's compiled libraries (W6-D, D224; D136 "before deploying").

The cloud cannot build the image (no Docker daemon), so these tests check the builder's inputs: the requirements it
exports from ``uv.lock`` pin NumPy and TA-Lib with hashes, the lock holds a CPython 3.12 Linux x86-64 wheel for each
(the image is ``linux/amd64`` Python 3.12 and downloads binary wheels only), the builder refuses an export without
them before any download, and the image build imports both before it is sealed. The real build is step L3.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def builder():
    spec = importlib.util.spec_from_file_location("native_wheels_builder", ROOT / "scripts/build_protected_image.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def exported() -> str:
    if shutil.which("uv") is None:  # CI and the development setup both run tests through uv
        pytest.fail("uv is required: the image builder exports its requirements with it")
    return subprocess.run(["uv", "export", "--locked", "--offline", "--no-dev", "--no-emit-project", "--format",
                           "requirements-txt"], cwd=ROOT, check=True, capture_output=True, text=True, timeout=120).stdout


def test_numpy_and_ta_lib_are_pinned_with_the_hash_of_a_linux_python_312_wheel(exported) -> None:
    pins = builder().native_wheel_pins(exported, (ROOT / "uv.lock").read_text(encoding="utf-8"))
    assert set(pins) == {"numpy", "ta-lib"}
    assert re.fullmatch(r"0\.8\.\d+", pins["ta-lib"]["version"])  # D136: TA-Lib capped below 0.9
    for pin in pins.values():
        assert "-cp312-cp312-" in pin["wheel"] and "manylinux" in pin["wheel"] and pin["wheel"].endswith(
            "x86_64.whl")
        assert re.fullmatch(r"[0-9a-f]{64}", pin["sha256"])
        assert f"--hash=sha256:{pin['sha256']}" in exported


@pytest.mark.parametrize("damage", ["drop_ta_lib", "unhashed_numpy", "other_version"])
def test_an_export_without_a_pinned_hashed_native_wheel_is_refused(exported, damage) -> None:
    module = builder()
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    text = exported
    if damage == "drop_ta_lib":
        text = re.sub(r"(?ms)^ta-lib==.*?(?=^\S)", "", text)
    elif damage == "unhashed_numpy":
        text = re.sub(r"(?m)^(numpy==\S+) \\\n(\s+--hash=sha256:[0-9a-f]{64}(?: \\)?\n)+", r"\1\n", text)
    else:
        text = re.sub(r"(?m)^numpy==\S+", "numpy==1.0.0", text)
    with pytest.raises(ValueError, match="NumPy and TA-Lib"):
        module.native_wheel_pins(text, lock)


def test_the_builder_checks_the_native_wheels_before_any_download_and_the_image_imports_them() -> None:
    source = (ROOT / "scripts" / "build_protected_image.py").read_text(encoding="utf-8")
    check, download = source.index("native_wheel_pins(requirements.read_text"), source.index('"pip", "download"')
    assert check < download
    dockerfile = (ROOT / "deploy" / "Dockerfile.protected").read_text(encoding="utf-8")
    probe, seal = dockerfile.index("import numpy, talib"), dockerfile.index("deployment_image seal")
    assert probe < seal
