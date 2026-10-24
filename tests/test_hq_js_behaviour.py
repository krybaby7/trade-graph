"""HQ page scripts: syntax check plus behaviour tests run with Node's own test runner (tests/js/hq_*.test.mjs).

The behaviour tests drive the real hq-*.js files on a small in-memory page (tests/js/hq_harness.mjs) with a fake
clock, timers, fetch and storage: polling and back-off, saved requests, wizard focus, calendar series and live copy.
They need Node.js 18 or newer and nothing else; without Node they are skipped (the WSL image has none), never failed.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "trade_graph" / "web" / "static"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed on this host")


@pytest.mark.parametrize("script", sorted(STATIC.glob("hq*.js")), ids=lambda path: path.name)
def test_script_parses(script: Path) -> None:
    result = subprocess.run([NODE, "--check", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


def test_behaviour_suite_passes() -> None:
    # node --test reads its arguments as glob patterns, so they are relative POSIX paths (Windows-safe).
    files = sorted(path.relative_to(ROOT).as_posix() for path in (ROOT / "tests" / "js").glob("hq_*.test.mjs"))
    assert files, "no behaviour tests found"
    result = subprocess.run([NODE, "--test", *files], capture_output=True, text=True, timeout=600, cwd=ROOT)
    assert result.returncode == 0, (result.stdout[-6000:] + result.stderr[-2000:])
