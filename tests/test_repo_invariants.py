"""Repo-level invariants: things that must hold across files, not inside one.

Both checks here exist because the failure they catch is silent.
"""

from __future__ import annotations

import re
import subprocess

from conftest import BASH, REPO

PROMPT_START = "I just installed the Skyrim Claude Code Modding Toolkit"

# The canonical copy, and the two files that embed it.
PROMPT_SOURCES = [
    "SETUP_PROMPT.txt",
    "README.md",
    "docs/getting-started.md",
]


def _extract_prompt(relpath: str) -> str:
    text = (REPO / relpath).read_text(encoding="utf-8")
    if relpath.endswith(".txt"):
        return text.strip()
    for line in text.splitlines():
        if line.startswith(PROMPT_START):
            return line.strip()
    raise AssertionError(f"{relpath}: setup prompt not found at all")


def test_setup_prompt_is_identical_everywhere():
    """The setup prompt exists in three files and they must not drift.

    README.md's copy silently lost its DevBench paragraph for three releases.
    Anyone pasting the prompt from the README -- the most visible copy, and the
    one the mod page points at -- was never offered the live in-game test
    channel at all. Nothing detected it; it was found by hand.
    """
    canonical = _extract_prompt(PROMPT_SOURCES[0])
    assert canonical, "the canonical prompt is empty"
    for relpath in PROMPT_SOURCES[1:]:
        assert _extract_prompt(relpath) == canonical, (
            f"{relpath}'s copy of the setup prompt has drifted from "
            f"{PROMPT_SOURCES[0]}. They must be byte-identical."
        )


def test_npm_test_actually_runs_tests():
    """`npm test` must not report success having examined nothing.

    package.json runs `node --test`, which with zero test files prints nothing
    and exits 0 -- so the CI node job passed while testing literally nothing.
    A gate that cannot fail is worse than no gate, because it reads as coverage.
    """
    # The reporter is pinned to TAP. node --test picks its reporter based on
    # whether stdout is a TTY, so the format differs between a local run and
    # CI -- and the orphan check below is format-sensitive. It silently matched
    # nothing on the CI runner while passing locally, which made this guard
    # itself a false green on exactly the machine that matters.
    result = subprocess.run(
        ["node", "--test", "--test-reporter=tap"], cwd=str(REPO),
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    match = re.search(r"^#\s*pass\s+(\d+)", result.stdout, re.M)
    assert match, f"could not read a pass count from node --test:\n{result.stdout}"
    assert int(match.group(1)) > 0, (
        "node --test reported zero passing tests, so `npm test` is a gate that "
        "examines nothing. Add tests or remove the gate."
    )

    # A pass count alone is not enough. When a file registers no tests at all,
    # node --test still counts the FILE as one passing test and reports
    # "pass 1" -- so a suite asserting nothing looks identical to a healthy one.
    # In that case the reported test NAME is the filename, which is the signal
    # worth checking. Found by the mutation gate: neutering the test file left
    # the pass-count check above green, so it was guarding nothing.
    orphans = re.findall(r"^ok\s+\d+\s+-\s+(\S+\.test\.js)\s*$", result.stdout, re.M)
    assert not orphans, (
        f"these files ran but registered no tests, so they count as passes "
        f"while asserting nothing: {sorted(set(orphans))}"
    )


def test_help_banners_do_not_leak_source_lines():
    """`--help` here prints a fixed LINE RANGE of the script's own comment header.
    That silently rots: add four lines of documentation and the banner starts
    printing `set -uo pipefail` and variable assignments at the reader. Measured
    2026-09-06 -- it was doing exactly that, and nothing noticed, because a banner
    that prints too much still looks like a banner."""
    script = REPO / "tools" / "esp-verify-wrapper.sh"
    r = subprocess.run([BASH, str(script), "--help"],
                       capture_output=True, text=True)
    leaked = [l for l in r.stdout.splitlines()
              if l.startswith(("set -", "SPRIGGIT=", "SNAPDIR=", "GAME_RELEASE=",
                               "PKG=", "PKGVER=", "mkdir "))]
    assert not leaked, f"--help leaked source lines: {leaked}"
