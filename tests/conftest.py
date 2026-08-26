"""Shared fixtures for the toolkit's behavioral tests.

Every test gets its own copy of the repo in a temp dir and runs the *real*
`setup.sh` against it, then asserts on what setup.sh **wrote** -- never on what
it printed. Log text is cosmetic; the written paths are the contract. That
distinction is exactly what the `setup-smoke` CI job got wrong: it ran setup.sh
and then only checked that settings.json parsed, so every path bug we have ever
shipped would have passed it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BIN = Path(__file__).resolve().parent / "bin"

# setup.sh shells out to `winget install` when it cannot find jq, which would be
# a slow, network-dependent step that can hang a CI run. Every invocation is
# capped so a stuck install fails the test instead of the job.
SETUP_TIMEOUT = 240

IGNORE = shutil.ignore_patterns(
    ".git", "tests", "__pycache__", "node_modules", ".pytest_cache", ".venv"
)


def copy_repo(dest: Path) -> Path:
    """A pristine copy of the toolkit, minus the test suite itself."""
    shutil.copytree(REPO, dest, ignore=IGNORE)
    return dest


@pytest.fixture
def game_dir(tmp_path: Path) -> Path:
    """A stock (non-MO2) install: the toolkit plus a stubbed VR executable.

    The stub matters -- without an .exe, setup.sh asks `read -p "Continue
    anyway?"` and would block on stdin.
    """
    d = copy_repo(tmp_path / "game")
    (d / "SkyrimVR.exe").write_bytes(b"")
    return d


def run_setup(cwd: Path, env_overrides: dict | None = None, shim_path: bool = False):
    """Run the real setup.sh against `cwd`. Returns a CompletedProcess.

    `shim_path` puts tests/bin first on PATH, which is how a test takes control
    of an external command setup.sh depends on (`which`, `powershell`) without
    adding a test-only seam to the shipped script.
    """
    env = dict(os.environ)
    env.setdefault("USERNAME", "testuser")
    if shim_path:
        env["PATH"] = f"{BIN}{os.pathsep}{env['PATH']}"
    if env_overrides:
        env.update({k: str(v) for k, v in env_overrides.items()})
    return subprocess.run(
        ["bash", "setup.sh"],
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=SETUP_TIMEOUT,
    )


def claude_md(game: Path) -> str:
    return (game / "CLAUDE.md").read_text(encoding="utf-8")


def hook_jq_lines(game: Path) -> dict[str, str]:
    """The `JQ="..."` line from every hook setup.sh configures."""
    found: dict[str, str] = {}
    for hook in sorted((game / ".claude" / "hooks").glob("*.sh")):
        for line in hook.read_text(encoding="utf-8").splitlines():
            if line.startswith("JQ="):
                found[hook.name] = line.strip()
                break
    return found
