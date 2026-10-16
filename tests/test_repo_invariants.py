"""Repo-level invariants: things that must hold across files, not inside one.

Both checks here exist because the failure they catch is silent.
"""

from __future__ import annotations

import json
import re
import sys
import subprocess

import pytest

from conftest import BASH, REPO

PROMPT_START = "I just installed the Skyrim modding toolkit"

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
# tests/test_hooks.py exercises the decision logic and DOES catch the inert-hook
# defect -- but only on Windows, where Python's subprocess hands bash a Win32 pipe
# that /dev/stdin cannot resolve. On Linux /dev/stdin resolves for any pipe and the
# whole behavioural suite passes with the defect in place, which is precisely the
# leg CI would be relying on. These two checks are properties of the FILE, so they
# hold on every platform and need no runtime at all.
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

    The behavioural suite sees this on Windows and not on Linux (see the note
    above), which is exactly why the check also lives here, where platform does
    not enter into it. It matches the command substitution, not the bare string,
    so the comments explaining the defect do not trip it.
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
        # Outside a comment: the release gates require that too, and a comment naming the file
        # satisfied the bare substring test without any code writing a heartbeat.
        if not re.search(r"^[^#\n]*hook-heartbeat", text, re.M):
            missing.append(f"{h.name}: writes no liveness heartbeat")
        if 'if [ -n "$INPUT" ]' not in text and 'if [ -z "$INPUT" ]' not in text:
            missing.append(f"{h.name}: does not branch on an empty payload")
    assert not missing, "\n  " + "\n  ".join(missing)


def test_every_hook_locates_its_install_with_the_same_block():
    """The fork-free location block (`_SELF=` .. `PROJECT_DIR=`) is behaviour-tested through
    protect-bash only: the drive-spelling and MSYS-mount tests in test_hooks.py. That covers the other
    four hooks only if they carry the SAME code (comments aside), so a drift in one of them goes red here."""
    hooks = sorted(HOOK_DIR.glob("*.sh"))
    assert len(hooks) == 6, "expected six hooks -- this test is stale"
    blocks = {}
    for h in hooks:
        text = h.read_text(encoding="utf-8")
        start, end = text.find('_SELF="'), text.find("PROJECT_DIR=")
        assert 0 <= start < end, f"{h.name}: location block not found"
        blocks[h.name] = [ln for ln in text[start:end].splitlines() if not ln.lstrip().startswith("#")]
    reference = blocks["protect-bash.sh"]
    drifted = [name for name, b in blocks.items() if b != reference]
    assert not drifted, f"location block differs from protect-bash.sh in: {drifted}"


def _root_win_block(text):
    start, end = text.find('ROOT_WIN=""'), text.find("esac", text.find('ROOT_WIN=""'))
    assert 0 <= start < end, "Windows-spelling block not found"
    return [ln for ln in text[start:end].splitlines() if not ln.lstrip().startswith("#")]


def test_the_codex_entry_locates_its_install_like_the_claude_hooks():
    """The Codex entry's own copy of the Windows-spelling block (ROOT_WIN) is behaviour-tested through protect-bash
    only, so it must be the same code. Git for Windows 2.55 changed $OSTYPE to `cygwin`: a copy that kept keying
    on msys* alone would make every Codex call GUARD INERT there while the Claude hooks still worked."""
    claude = _root_win_block((HOOK_DIR / "protect-bash.sh").read_text(encoding="utf-8"))
    codex = _root_win_block((REPO / ".codex" / "hooks" / "skyguard-entry.sh").read_text(encoding="utf-8"))
    assert codex == claude, "the Codex entry's ROOT_WIN block differs from protect-bash.sh's"


def test_every_hook_is_configured_by_setup_and_carries_one_placeholder():
    """setup.sh substitutes {{PYTHON_PATH}} into a HARDCODED LIST of hook filenames, with
    `sed ... /g`. Two things rot silently here:

    1. A hook added later is never named in that list, so it ships with the literal
       placeholder. Since a blocking hook now REFUSES when Python is unrunnable, an
       unsubstituted hook denies every call -- loud, but only after install.
    2. A second `{{PYTHON_PATH}}` anywhere in a hook (a comment mentioning it, say) is
       rewritten too, so "the unsubstituted placeholder" turns into a sentence
       claiming a real path is a placeholder. Harmless at runtime, but it destroys
       the property that makes case 1 detectable: exactly one occurrence per hook.

    Both were live: two hooks carried the literal in an explanatory comment.
    """
    # Plan 4: the Codex entries take the same interpreter, from the same loop.
    hooks = sorted(HOOK_DIR.glob("*.sh")) + sorted((REPO / ".codex" / "hooks").glob("skyguard-entry.*"))
    assert len(hooks) >= 7, "hooks not found -- this test would pass vacuously"
    setup = (REPO / "setup.sh").read_text(encoding="utf-8").replace(chr(92) + "\n", " ")   # join continuations
    loop = next((l for l in setup.splitlines() if l.startswith("for hook in ")), "")
    assert loop, "setup.sh has no `for hook in ...` substitution loop; this test is stale"

    problems = []
    for h in hooks:
        text = h.read_text(encoding="utf-8")
        n = text.count("{{PYTHON_PATH}}")
        if n != 1:
            problems.append(f"{h.name}: {n} occurrences of the placeholder, expected exactly 1")
        # Assert membership of the SUBSTITUTION LOOP, not of the file. Every hook is
        # also named in setup.sh's closing summary `echo`, so a whole-file substring
        # test was satisfied by a print statement -- and MEASURED: deleting a hook from
        # the `for hook in ...` list left this test green, which is exactly the defect
        # its own docstring names. Fourth occurrence of "a check its own documentation
        # satisfies"; this time the documentation was a line the same arc added.
        if h.name not in loop:
            problems.append(f"{h.name}: not in setup.sh's substitution loop, so it ships unconfigured")
    assert not problems, "\n  " + "\n  ".join(problems)


def test_every_hook_is_registered_in_settings_json():
    """A hook file that no event invokes is decoration, and it is the quietest
    possible failure: it passes every other check in this file -- it reads stdin
    correctly, writes a heartbeat, carries one placeholder, is named in setup.sh --
    and never runs, because nothing in settings.json points at it.

    `hook-canary.sh` cannot tell you either: an unregistered hook writes no
    heartbeat, which the canary reports as NOT EXERCISED, the same thing it says
    about an Edit hook in a Bash-only session. Registration is a property of the
    two files together, so it is asserted here rather than hoped for.
    """
    settings = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))
    commands = [
        entry.get("command", "")
        for matchers in settings.get("hooks", {}).values()
        for matcher in matchers
        for entry in matcher.get("hooks", [])
    ]
    assert commands, "settings.json registers no hooks at all"

    hooks = sorted(HOOK_DIR.glob("*.sh"))
    assert hooks, "no hooks found -- this test would pass vacuously"
    unwired = [h.name for h in hooks if not any(h.name in c for c in commands)]
    assert not unwired, (
        "these hooks ship but no event invokes them, so they never run: "
        + ", ".join(unwired))


def test_shell_matcher_covers_powershell():
    """Claude Code's PowerShell tool is a second way to run any command. A matcher of plain `Bash` leaves
    it unguarded and unsnapshotted, and nothing errors: the hooks just never fire for it (E12).

    Both shell hooks (the command guard and the .psc snapshot) must sit under a matcher that, compiled as a
    regex the way Claude Code does (the whole tool name), matches `Bash` and `PowerShell` and not the file
    tools. The Edit|Write hooks must NOT widen to the shells: they read `file_path`, which a shell tool lacks."""
    settings = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))
    by_hook = {}
    for matcher in settings["hooks"]["PreToolUse"]:
        for entry in matcher.get("hooks", []):
            by_hook[entry["command"].rsplit("/", 1)[-1].rstrip('"')] = matcher.get("matcher", "")
    for hook in ("protect-bash.sh", "snapshot-before-tool.sh"):
        assert hook in by_hook, f"{hook} is not registered under PreToolUse"
        rx = re.compile(by_hook[hook])
        for tool in ("Bash", "PowerShell", "Monitor"):     # Monitor: Plan 5 Task 13b (MEASURED outside both, 2.1.289)
            assert rx.fullmatch(tool), f"{hook}: matcher {by_hook[hook]!r} does not match {tool}"
        for tool in ("Write", "Edit", "Read", "NotebookEdit"):
            assert not rx.fullmatch(tool), f"{hook}: matcher {by_hook[hook]!r} wrongly matches {tool}"
    for hook in ("protect-files.sh", "backup-before-edit.sh"):
        rx = re.compile(by_hook[hook])
        assert not rx.fullmatch("PowerShell") and not rx.fullmatch("Bash") and not rx.fullmatch("Monitor"), \
            f"{hook} must stay on the file tools"
        assert rx.fullmatch("NotebookEdit"), f"{hook}: matcher {by_hook[hook]!r} does not match NotebookEdit"


def test_every_gate_that_counts_hooks_agrees_with_the_shipped_hooks():
    """Task 11 added a sixth hook. The canary's list, the release workflow's floor and verify-release.sh's fallback
    floor each name the hook set; one left behind makes a hook invisible to the canary or lets a release ship
    without it."""
    shipped = {h.stem for h in HOOK_DIR.glob("*.sh")}
    # Plan 4 rewrote hook-canary.sh as a thin front for tools/skyguard/health.py: the canary's hook list is
    # health.CLAUDE_HOOKS (read as data, never by grepping the canary, which no longer names any hook).
    canary = (REPO / "tools" / "hook-canary.sh").read_text(encoding="utf-8")
    assert "tools/skyguard/health.py" in canary, "hook-canary.sh no longer delegates to health.py; this test is stale"
    sys.path.insert(0, str(REPO / "tools"))
    from skyguard import health
    assert set(health.CLAUDE_HOOKS) == shipped, (health.CLAUDE_HOOKS, sorted(shipped))
    # Plan 8 T10: both release gates run tools/check-release-payload.py, which owns the floor.
    import importlib.util
    spec = importlib.util.spec_from_file_location("crp", REPO / "tools" / "check-release-payload.py")
    crp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(crp)
    assert crp.CLAUDE_HOOK_FLOOR == len(shipped), (crp.CLAUDE_HOOK_FLOOR, len(shipped))


def test_the_devbench_mcp_hook_is_registered_under_the_devbench_matcher():
    """Task 11: DevBench's MCP tools are named `mcp__<server>__<tool>` (the server is `devbench-vr` here; the matcher
    `mcp__devbench.*` was observed firing in Task 0). The hook must match them, and ONLY them: an unrelated MCP server
    or a shell tool under this matcher would be judged by a guard that reads another tool's arguments."""
    settings = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))
    by_hook = {}
    for matcher in settings["hooks"]["PreToolUse"]:
        for entry in matcher.get("hooks", []):
            by_hook[entry["command"].rsplit("/", 1)[-1].rstrip('"')] = matcher.get("matcher", "")
    assert "protect-mcp.sh" in by_hook, "protect-mcp.sh is not registered under PreToolUse"
    rx = re.compile(by_hook["protect-mcp.sh"])
    # Plan 8 T6: any server name containing devbench, in any case (`DevBench-vr`, `skyrim-devbench`, a plugin's
    # `plugin_devbench_x`), and `dev-bench`: the spellings the judge (mcp_rules.is_devbench_server) recognises.
    for tool in ("mcp__devbench-vr__console", "mcp__devbench-vr__game", "mcp__devbench__inspect",
                 "mcp__skyrim-devbench__console", "mcp__plugin_devbench_x__game", "mcp__DevBench-vr__console",
                 "mcp__dev-bench__console"):
        assert rx.fullmatch(tool), f"matcher {by_hook['protect-mcp.sh']!r} does not match {tool}"
    for tool in ("Bash", "PowerShell", "Write", "mcp__other__console", "mcp__bench__console"):
        assert not rx.fullmatch(tool), f"matcher {by_hook['protect-mcp.sh']!r} wrongly matches {tool}"


def test_every_registered_hook_has_a_timeout_that_is_not_the_old_five_seconds():
    """A PreToolUse hook whose answer arrives after its `timeout` has that refusal
    DISCARDED and the command runs -- measured, and undocumented. `protect-bash.sh`
    was measured at 4,620 ms under load against the old 5,000 ms budget, so the delete
    guard could silently disarm on a busy machine, and `hook-canary.sh` is structurally
    blind to it (the hook is alive; it just answers late).

    That fix was a number in a JSON file with nothing asserting it. MEASURED by the
    release review: `sed 's/"timeout": 30/"timeout": 5/'` across all five registrations
    left 78 tests green. A number the release notes call "a safety setting, not a
    comfort one" was guarded by nothing at all.

    SessionStart is allowed more: its documented default is 600s (Claude Code lowers
    the default to 30 only for UserPromptSubmit/PreModelSwitch/PostModelSwitch), and
    its hook copies files rather than answering a permission question.
    """
    settings = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))
    floors = {"PreToolUse": 30, "SessionStart": 30}
    problems = []
    examined = 0
    for event, matchers in settings.get("hooks", {}).items():
        for matcher in matchers:
            for entry in matcher.get("hooks", []):
                name = entry.get("command", "?").rsplit("/", 1)[-1]
                examined += 1
                t = entry.get("timeout")
                if t is None:
                    problems.append(f"{event}/{name}: no explicit timeout")
                elif t < floors.get(event, 30):
                    problems.append(
                        f"{event}/{name}: timeout {t}s is below the {floors.get(event, 30)}s "
                        f"floor; a late refusal is DISCARDED")
    assert examined >= 5, (
        f"only {examined} hook registration(s) examined; this test would be vacuous")
    assert not problems, "\n  " + "\n  ".join(problems)


def _assert_knowledgebase_shipping_membership(root):
    local = "KNOWLEDGEBASE.local.md"
    result = subprocess.run(["git", "ls-files", "-z", "--", "KNOWLEDGEBASE.md", local],
                            cwd=str(root), capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    tracked = set(result.stdout.split("\0"))
    assert "KNOWLEDGEBASE.md" in tracked, "the toolkit's knowledgebase is not tracked for release"
    assert local not in tracked, local + " is tracked and would ship over the user's own notes"


def test_the_users_knowledgebase_is_never_shipped_and_is_where_writes_go():
    """A toolkit must not ship a file it also asks the user to edit.

    The historical update path was "extract the new zip over your install", so every
    shipped file could be REPLACED on update. `KNOWLEDGEBASE.md` was both shipped by
    us and appended to by the user on our own standing instruction, so an update
    silently destroyed their accumulated notes -- while the README promised the
    opposite ("Your knowledgebase additions are preserved"). A reassurance that fires
    exactly when the thing it names has been destroyed is worse than saying nothing.

    Accumulation now goes to `KNOWLEDGEBASE.local.md`, which is untracked and gated out
    of the release payload. Three pieces have to agree or the split protects nobody, so
    all three are asserted here rather than trusted:

      * CLAUDE.md must direct writes to the local file
      * .gitignore must keep it untracked
      * the release workflow must REFUSE a payload containing it
    """
    local = "KNOWLEDGEBASE.local.md"

    claude_md = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    assert local in claude_md, (
        "CLAUDE.md does not mention " + local + " -- the standing instruction still "
        "sends new findings into the file the next update overwrites")

    gitignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert any(l.strip() == local for l in gitignore.splitlines()), (
        local + " is not in .gitignore, so a contributor's own notes can be committed "
        "-- and anything tracked is shipped, which is the defect this prevents")

    # Match the CONSTRUCT, not the name, and skip comments. The first version of this
    # assertion looked for the filename anywhere in release.yml -- which the comment
    # explaining the gate also contains, so deleting the gate itself left the test
    # green. A check that its own documentation satisfies is worse than no check: it
    # goes red only when someone removes the explanation.
    # Plan 8 T10: the gate is an entry of the one payload checker (proven red on its own line in
    # test_release_payload.py), and release.yml runs that checker on the zip it publishes.
    import importlib.util
    spec = importlib.util.spec_from_file_location("crp", REPO / "tools" / "check-release-payload.py")
    crp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(crp)
    assert local in crp.ABSENT_PATHS, (
        "tools/check-release-payload.py does not assert " + local + " is absent from the "
        "payload. Without that gate the split is a convention, and a convention cannot go red")
    workflow = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert [l for l in workflow.splitlines()
            if not l.strip().startswith("#") and "tools/verify-release.sh" in l and "--local" in l], (
        "release.yml does not run the payload checker (through verify-release.sh --local) on the zip it publishes")

    # ...and the toolkit's own knowledgebase must still SHIP. Dropping it would make
    # the assertion above pass for the wrong reason.
    assert (REPO / "KNOWLEDGEBASE.md").is_file(), "the toolkit's KNOWLEDGEBASE.md is gone"
    # A user's ignored local file may exist in a working checkout. Releases use
    # committed objects; disk existence is not evidence that the file ships.
    # Exported/mutation source fixtures have no index; the guidance/ignore and
    # actual payload-checker requirements above still apply to those copies.
    if (REPO / ".git").exists():
        _assert_knowledgebase_shipping_membership(REPO)


@pytest.mark.parametrize("tracked", [False, True], ids=["ignored-local", "tracked-local"])
def test_knowledgebase_membership_allows_local_notes_but_refuses_shipping_them(tmp_path, tracked):
    from test_release_build import _build, _git, _names, _repo, _zip
    root = _repo(tmp_path)
    (root / "KNOWLEDGEBASE.md").write_bytes(b"toolkit knowledge\n")
    with (root / ".gitignore").open("a", encoding="utf-8") as stream:
        stream.write("KNOWLEDGEBASE.local.md\n")
    _git(root, "add", "--", "KNOWLEDGEBASE.md", ".gitignore")
    _git(root, "commit", "-q", "-m", "knowledgebase fixture")
    local = root / "KNOWLEDGEBASE.local.md"
    local.write_bytes(b"owned private findings\n")
    if tracked:
        _git(root, "add", "-f", "--", local.name)
        with pytest.raises(AssertionError, match="tracked and would ship"):
            _assert_knowledgebase_shipping_membership(root)
    else:
        _assert_knowledgebase_shipping_membership(root)
        result, output = _build(root, "HEAD", "--version", "notes-fixture")
        assert result.returncode == 0, result.stdout + result.stderr
        names = _names(_zip(output))
        assert "skyrim-ai-assistant-toolkit-notes-fixture/KNOWLEDGEBASE.md" in names
        assert not any(name.endswith("/KNOWLEDGEBASE.local.md") for name in names)
    assert local.read_bytes() == b"owned private findings\n"


# ---------------------------------------------------------------- the Codex payload (Plan 4 Task 11)

import shutil  # noqa: E402
import sys  # noqa: E402

from conftest import copy_repo  # noqa: E402

CHECKER = REPO / "tools" / "check-release-payload.py"   # Plan 8 T10: the Codex section of the one checker


def _check(root):
    return subprocess.run([sys.executable, str(CHECKER), str(root), "--section", "codex"],
                          capture_output=True, text=True, timeout=60)


def _line(out, name):
    return next((l for l in out.splitlines() if name in l), "")


def _drop_one_codex_skill(root):
    shutil.rmtree(next(p for p in sorted((root / ".agents" / "skills").iterdir()) if p.is_dir()))


def _pad_agents_md(root):
    p = root / "AGENTS.md"
    p.write_bytes(p.read_bytes() + b"x" * (32769 - p.stat().st_size))


def _render_hooks_json(root):
    p = root / ".codex" / "hooks.json"
    p.write_text(p.read_text(encoding="utf-8").replace("{{CODEX_ROOT_POSIX}}", "/c/Me/Skyrim")
                 .replace("{{CODEX_ROOT}}", "C:/Me/Skyrim"), encoding="utf-8")


BREAKS = {
    "definitions-missing": (lambda r: (r / ".codex" / "hooks.json").unlink(), ".codex/hooks.json present"),
    "definitions-unparseable": (lambda r: (r / ".codex" / "hooks.json").write_text("{", encoding="utf-8"),
                                ".codex/hooks.json present"),
    "definitions-rendered": (_render_hooks_json, "still unrendered"),
    "entry-ps1-missing": (lambda r: (r / ".codex" / "hooks" / "skyguard-entry.ps1").unlink(), "script is in the payload"),
    "entry-sh-missing": (lambda r: (r / ".codex" / "hooks" / "skyguard-entry.sh").unlink(), "script is in the payload"),
    "rules-missing": (lambda r: (r / ".codex" / "rules" / "skyrim.rules").unlink(), "skyrim.rules present"),
    "codex-skill-missing": (_drop_one_codex_skill, ".agents/skills matches"),
    "agents-md-missing": (lambda r: (r / "AGENTS.md").unlink(), "AGENTS.md present"),
    "agents-md-over-budget": (_pad_agents_md, "AGENTS.md present"),
}


@pytest.fixture(scope="module")
def pristine_payload(tmp_path_factory):
    return copy_repo(tmp_path_factory.mktemp("payload") / "toolkit")


def test_the_codex_payload_check_passes_on_the_real_payload(pristine_payload):
    r = _check(pristine_payload)
    assert r.returncode == 0 and "RELEASE PAYLOAD: OK" in r.stdout, r.stdout + r.stderr


@pytest.mark.parametrize("brk", sorted(BREAKS))
def test_every_codex_payload_check_can_fail(tmp_path, pristine_payload, brk):
    """Each check is proven red on a payload missing its piece -- and red on ITS OWN line, not by luck."""
    root = tmp_path / "toolkit"
    shutil.copytree(pristine_payload, root)
    damage, name = BREAKS[brk]
    damage(root)
    r = _check(root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "!!" in _line(r.stdout, name), f"{brk}: the '{name}' check did not go red:\n{r.stdout}"


def test_both_release_gates_run_the_release_payload_check():
    """The construct, not the name in a comment: release.yml runs verify-release.sh on the zip it publishes, and
    verify-release.sh runs the one checker (whose Codex section is tested above)."""
    verify = (REPO / "tools" / "verify-release.sh").read_text(encoding="utf-8")
    runs = [l for l in verify.splitlines()
            if not l.strip().startswith("#") and ("python" in l.lower() or l.lstrip().startswith('"$PY"'))
            and "check-release-payload.py" in l]
    assert runs, "tools/verify-release.sh never runs tools/check-release-payload.py"
    workflow = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    runs = [l for l in workflow.splitlines()
            if not l.strip().startswith("#") and "tools/verify-release.sh" in l and "--local" in l]
    assert runs, "release.yml never runs tools/verify-release.sh --local on the zip it publishes"


def test_the_codex_files_are_not_gitignored():
    """A tracked-but-ignored pattern would drop them from the next contributor's commit, and from the zip."""
    r = subprocess.run(["git", "check-ignore", ".codex/hooks.json", ".codex/hooks/skyguard-entry.ps1",
                        ".codex/rules/skyrim.rules", ".agents/skills/x/SKILL.md", "AGENTS.md"],
                       cwd=str(REPO), capture_output=True, text=True, timeout=60)
    assert r.stdout.strip() == "", f"ignored: {r.stdout}"
    assert r.returncode == 1, r.stderr   # 1 = none ignored; 128 = not a repo (the check proved nothing)
