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


def test_help_banner_range_stays_inside_the_comment_header():
    """`--help` prints a fixed LINE RANGE of the script's own comment header, and
    that range rots: add four lines of documentation and the banner starts printing
    `set -uo pipefail` and variable assignments at the reader.

    This reads the range out of the source and compares it to where the header
    actually ends, rather than running the script and looking for leaked lines. The
    first version of this test did the latter, and it was VACUOUS on any machine
    without spriggit installed -- the banner renders one line, there is nothing to
    find, and the assertion passes in both the fixed and the broken state. The
    mutation gate caught it; a green local run had not.
    """
    script = REPO / "tools" / "esp-verify-wrapper.sh"
    lines = script.read_text(encoding="utf-8").splitlines()

    m = re.search(r"sed -n '2,(\d+)p' \"\$0\"", "\n".join(lines))
    assert m, "no --help line-range found; this test is asserting nothing"
    claimed_end = int(m.group(1))

    # Where the leading comment block actually ends: the last line, before any code,
    # that is a comment or blank.
    header_end = 0
    for i, line in enumerate(lines[1:], start=2):     # skip the shebang
        if line.startswith("#") or not line.strip():
            header_end = i
        else:
            break

    assert claimed_end <= header_end, (
        f"--help prints lines 2-{claimed_end} but the comment header ends at "
        f"{header_end}; lines {header_end + 1}-{claimed_end} are source code: "
        f"{lines[header_end:claimed_end][:3]}"
    )


# --------------------------------------------------------------------------
# The hooks, as TEXT
#
# tests/test_hooks.py exercises the decision logic, but it structurally cannot
# catch the defect that actually shipped: a suite pipes stdin, so `/dev/stdin`
# resolves fine there and an inert hook passes every behavioural case. These two
# checks are properties of the file, so they hold without a runtime.
# --------------------------------------------------------------------------

HOOK_DIR = REPO / ".claude" / "hooks"


def test_no_shipped_hook_reads_stdin_through_dev_stdin():
    """`INPUT=$(cat /dev/stdin)` returns ZERO BYTES in the Claude Code hook
    environment, while a bare `cat` returns the payload (MEASURED: X4 toolkit
    2026-08-29, reproduced on a second machine 2026-09-06 -- seven probes, 0 bytes
    via /dev/stdin, 641-2840 via bare cat).

    Every hook in this toolkit used it, so every hook was inert: it fired, read
    nothing, fell through its first guard and exited 0 -- byte-identical to
    deciding "this is fine". The audit log carried an empty command field in 2,454
    of 2,454 entries and nothing looked wrong.

    The behavioural suite CANNOT see this, which is exactly why the check lives
    here instead. It matches the command substitution, not the string, so the
    comments explaining the defect do not trip it.
    """
    hooks = sorted(HOOK_DIR.glob("*.sh"))
    assert hooks, "no hooks found -- this test would pass vacuously"
    offenders = []
    for h in hooks:
        for n, line in enumerate(h.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if "$(cat /dev/stdin)" in line or "< /dev/stdin" in line:
                offenders.append(f"{h.name}:{n}: {line.strip()}")
    assert not offenders, (
        "these hooks read stdin through /dev/stdin and are INERT in the hook "
        "environment; use a bare `cat`:\n  " + "\n  ".join(offenders)
    )


def test_every_hook_writes_a_heartbeat_and_handles_an_empty_payload():
    """The two properties that make a hook's death detectable at all.

    Without the heartbeat, `tools/hook-canary.sh` has nothing to read and an inert
    hook is indistinguishable from a quiet one. Without the empty-payload branch,
    a hook that sees nothing exits 0 and that silence reads as approval.
    """
    hooks = sorted(HOOK_DIR.glob("*.sh"))
    assert hooks, "no hooks found -- this test would pass vacuously"
    missing = []
    for h in hooks:
        text = h.read_text(encoding="utf-8")
        if "hook-heartbeat" not in text:
            missing.append(f"{h.name}: writes no liveness heartbeat")
        if 'if [ -n "$INPUT" ]' not in text and 'if [ -z "$INPUT" ]' not in text:
            missing.append(f"{h.name}: does not branch on an empty payload")
    assert not missing, "\n  " + "\n  ".join(missing)
