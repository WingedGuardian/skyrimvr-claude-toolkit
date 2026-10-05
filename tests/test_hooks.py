"""The safety hooks, exercised as processes with a real payload on stdin.

WHY THIS FILE EXISTS, AND WHAT IT STILL CANNOT SEE

Every hook in this toolkit was INERT for months. They fired on every call, read zero
bytes, fell through their first guard and exited 0 -- which is byte-identical to
deciding "this is fine". The audit log recorded an empty command field in 2,454 of
2,454 entries and nothing looked wrong.

The cause was `INPUT=$(cat /dev/stdin)`, which returns ZERO BYTES in the Claude Code
hook environment while a bare `cat` returns the payload (MEASURED: X4 toolkit
2026-08-29, reproduced on a second machine 2026-09-06 -- seven consecutive probes,
0 bytes via /dev/stdin, 641-2840 via bare cat).

**This file DOES catch that defect -- but only on Windows.** `/dev/stdin` is a
symlink to `/proc/self/fd/0`: it resolves when fd 0 is a real file or a pipe made
by an MSYS shell, and fails when fd 0 is a Win32 pipe from a non-MSYS parent.
Python's `subprocess` hands bash a Win32 pipe, exactly as Claude Code's Node
process does -- so reinstating `cat /dev/stdin` here gives **18 failed, 16 passed**
(measured). On Linux `/dev/stdin` resolves for any pipe and every case below
passes with the defect in place.

So this suite is a real detector on one platform and blind on the other, and the
honest reason the defect survived in the first place is neither: **nothing ran the
hooks as processes at all.** Two platform-independent checks carry what this
cannot:

  * `tests/test_repo_invariants.py` asserts no shipped hook USES `$(cat /dev/stdin)`.
    That is a property of the text, so it holds on every platform and in CI's Linux
    leg, where the behavioural check above is blind.
  * `tools/hook-canary.sh` reads heartbeats written from inside REAL invocations,
    which is the only evidence that survives the harness/production gap.

Every deny below has a control beside it that must NOT fire. A rule that fires on
ordinary work gets worked around, and then it protects nothing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import REPO, install_hooks, no_python_env

HOOKS = REPO / ".claude" / "hooks"
BS = chr(92)

#: The install these tests talk about. Defined HERE, above the fixture, because the
#: fixture now has to declare it in `.claude/skyrim-paths.env`: protect-bash.sh resolves
#: the real install root instead of matching the word "Skyrim" anywhere in a path, so a
#: row naming some third directory would describe a place the hook has no reason to
#: guard. One constant, used by the fixture and by every row, so the two cannot disagree.
GAME = "C:/GOG Games/The Elder Scrolls V Skyrim VR"
GAMEW = GAME.replace("/", BS)


def _bash() -> str:
    """Git Bash by path, never `bash` from PATH.

    On Windows `bash` resolves to the WSL launcher, which cannot see these scripts.
    It exits without running anything, and a harness that reads empty output as
    "allowed" then reports every case as passing -- a whole matrix of green that
    measured nothing. This has happened here.
    """
    for c in (r"C:\Program Files\Git\bin\bash.exe",
              r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if Path(c).is_file():
            return c
    found = shutil.which("bash")
    if found and "System32" not in found:
        return found
    pytest.skip("no non-WSL bash available")


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    """A throwaway install with the hooks configured, as setup.sh would leave them."""
    root = tmp_path_factory.mktemp("install") / "Skyrim VR"
    (root / "Data" / "Scripts" / "Source").mkdir(parents=True)
    install_hooks(root)
    # DECLARE THE INSTALL THE ASSERTIONS TALK ABOUT.
    #
    # protect-bash.sh no longer decides "is this the game" by looking for the word
    # Skyrim in a path -- that matched the session scratchpad, this repo's own
    # checkout, and C:/Temp/skyrim-notes.txt. It resolves the REAL install root from
    # the hook's own location, plus whatever setup.sh recorded here.
    #
    # In this fixture the hook's location is a pytest tmp dir, so without this file the
    # rows below -- which assert about `C:/GOG Games/...` -- would describe a directory
    # the hook has no reason to guard, and every deletion row would (correctly) stop
    # denying. Writing it also means the configured-paths arm is exercised rather than
    # assumed; INSTALL_ROOT alone would never test it.
    # Only the GAME root is declared. The config directory keeps a generic literal
    # rule in the hook (`Documents/My Games/Skyrim`), because it lives OUTSIDE any
    # install root and its INIs are not recoverable from a mod manager -- so it must
    # be guarded even on a machine setup.sh has never configured.
    (root / ".claude" / "skyrim-paths.env").write_text(
        f'SKYRIM_GAME_ROOT="{GAME}"\n', encoding="utf-8", newline="\n",
    )
    return root


def fire(project, hook, payload):
    """Run a hook the way Claude Code does and classify its decision.

    `payload=None` sends genuinely EMPTY stdin -- the condition the defect produced.
    That is NOT the same as a well-formed payload with a field missing, and the two
    are tested separately.
    """
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project))
    r = subprocess.run(
        [_bash(), str(project / ".claude" / "hooks" / hook)],
        input="" if payload is None else json.dumps(payload),
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert r.returncode == 0, f"{hook} exited {r.returncode}: {r.stderr[:300]}"
    out = r.stdout.strip()
    if not out:
        return "allow", ""
    j = json.loads(out)
    h = j.get("hookSpecificOutput", {})
    if "permissionDecision" in h:
        return h["permissionDecision"], h.get("permissionDecisionReason", "")
    if "additionalContext" in h:
        return "advise", h["additionalContext"]
    return "allow", out


def cmd(c):
    return {"tool_name": "Bash", "tool_input": {"command": c}}


def fpath(p):
    return {"tool_name": "Write", "tool_input": {"file_path": p}}


# --------------------------------------------------------------------------
# protect-files
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path,expected,why", [
    ("C:/Games/Skyrim VR/Data/MyMod.esp", "deny", "a text write into a binary plugin"),
    ("C:" + BS + "Games" + BS + "Skyrim VR" + BS + "Data" + BS + "M.esp", "deny", "same, backslashes"),
    ("/c/games/skyrim/Data/x.BSA", "deny", "archives too, and case-insensitively"),
    # Controls. Each must NOT deny.
    ("C:/Games/Skyrim VR/Data/Textures/x.dds", "allow", "not the declared install: the note is by resolved zone (Plan 5 Task 9)"),
    ("C:/Games/Skyrim VR/.claude/hooks/x.sh", "allow", "our own workspace is silent"),
    ("C:/work/notes.md", "allow", "nothing to do with the install"),
    ("C:/Users/x/Documents/My Games/Skyrim VR/SkyrimVR.ini", "advise", "config is consequential"),
    ("C:/Games/Skyrim VR/Data/Scripts/Source/A.psc", "allow", "not the declared install's Data/Scripts (Plan 5 Task 9)"),
])
def test_protect_files_decides(project, path, expected, why):
    got, _ = fire(project, "protect-files.sh", fpath(path))
    assert got == expected, f"{why}: {path}"


def test_protect_files_notes_inside_the_declared_install(project):
    """Plan 5 Task 9: W-INSTALL / W-PAPYRUS are decided on where the path resolves, so the hook must hand the engine
    its roots. A dict built here, not fpath(): the corpus recorder harvests fpath() literals (record.inputs)."""
    for path, want in ((GAME + "/Data/Textures/x.dds", "Note: editing x.dds inside the live Skyrim install."),
                       (GAME + "/Data/Scripts/Source/A.psc", "Note: a script change in A.psc reaches the game only "
                                                            "at its next start; snapshot it first.")):
        got, text = fire(project, "protect-files.sh", {"tool_name": "Write", "tool_input": {"file_path": path}})
        assert (got, text) == ("advise", want), path


# --------------------------------------------------------------------------
# protect-bash
# --------------------------------------------------------------------------

@pytest.mark.parametrize("command,expected,why", [
    (f'rm -rf "{GAME}"', "deny", "deleting the install"),
    (f'rm -rf "{GAMEW}"', "deny",
     "the backslash form -- cmd.exe and powershell are both callable, so this is "
     "the spelling that got through a forward-slash-only guard"),
    (f'rm -rf "/c/{GAME[3:]}"', "deny", "the MSYS form"),
    ('rm -rf "C:/Users/x/Documents/My Games/Skyrim VR"', "deny", "the config directory"),
    ('reg delete "HKLM' + BS + 'SOFTWARE' + BS + 'Bethesda"', "deny", "registry keys"),
    (f'Champollion.exe "{GAME}/Data/Scripts/A.pex"', "deny",
     "Champollion writes to Data/Scripts/Source regardless of flags and can leave "
     "the output empty; this has destroyed a .psc twice. Scoped to the LIVE install: "
     "a .pex copied to a temp dir -- the remedy this refusal prescribes -- must not "
     "itself be refused"),
    ('tool --output "C:/tmp/A.psc"', "deny", "output aimed straight at a source file"),
    # Controls. A guard that fires on these is one that gets deleted.
    ("ls -la", "allow", "plain ls"),
    ("git status", "allow", "git status"),
    ("rm -rf /tmp/scratch", "allow", "an rm outside the install"),
    ('python -c "print(1)"', "allow", "ordinary work"),
    ('Champollion.exe "C:/tmp/A.pex" -p C:/tmp', "allow", "Champollion somewhere safe"),
    ("rm mydata/cache.bin", "allow", "the word boundary: mydata is not Data"),
    # Advisories.
    ('cat "C:/Games/Skyrim VR/Data/x.esp"', "allow", "reading a plugin is silent (R11, Plan 5 Task 8)"),
    ("rm Data/Textures/x.dds", "advise", "a relative delete inside Data"),
    ("rm ./Data/x.dds", "advise", "dot-relative too"),
    ("cat loadorder.txt", "allow", "reading the load order is silent; a write notes (Plan 5 Task 8)"),
    # The single ask -- genuinely the user's decision.
    ("bash tools/resaver-cli.sh reset-havok save.ess --apply", "ask",
     "--apply MUTATES a save through ReSaver's write path"),
    ("bash tools/resaver-cli.sh reset-havok save.ess", "allow", "the dry-run does not"),
])
def test_protect_bash_decides(project, command, expected, why):
    got, _ = fire(project, "protect-bash.sh", cmd(command))
    assert got == expected, f"{why}: {command}"


# --------------------------------------------------------------------------
# The defect itself
# --------------------------------------------------------------------------

MCP_RESET = {"tool_name": "mcp__devbench-vr__console", "tool_input": {"command": "smp reset"}}     # protect-mcp (Task 11): a call the engine denies

@pytest.mark.parametrize("name", ["protect-bash.sh", "protect-files.sh", "protect-mcp.sh"])
def test_a_windows_python_path_still_produces_parseable_json(tmp_path, name):
    r"""A backslash path (`C:\Users\...\python.exe`) is what a hand-edit or a careless
    paste puts in the hook. It is echoed into a JSON string via printf, and `\U` is not a
    valid JSON escape -- so the refusal built to survive a broken interpreter would itself
    be unparseable, i.e. an allow. The wrapper normalises the separators it prints."""
    root = install_hooks(tmp_path / "install",
                         python="C:" + BS + "Users" + BS + "me" + BS + "AppData" + BS + "python.exe")
    payload = (cmd('rm -rf "C:/x"') if name == "protect-bash.sh"
               else MCP_RESET if name == "protect-mcp.sh" else fpath("C:/Games/Skyrim VR/Data/M.esp"))
    env = no_python_env(CLAUDE_PROJECT_DIR=str(root))
    r = subprocess.run([_bash(), str(root / ".claude" / "hooks" / name)],
                       input=json.dumps(payload), capture_output=True, text=True,
                       env=env, timeout=60)
    j = json.loads(r.stdout)          # the assertion IS that this does not raise
    assert j["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "C:/Users/me/AppData/python.exe" in j["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("hook,field", [
    ("protect-files.sh", "file_path"),
    ("protect-bash.sh", "command"),
])
def test_a_blocking_hook_refuses_when_it_cannot_see_its_input(project, hook, field):
    """Silence IS allow. A guard that evaluated nothing must say so, not exit 0 --
    exiting 0 is what made the inert hooks indistinguishable from working ones."""
    tool = "Write" if field == "file_path" else "Bash"
    got, reason = fire(project, hook, {"tool_name": tool, "tool_input": {}})
    assert got == "deny", f"{hook} allowed a call it could not evaluate"
    assert "GUARD INERT" in reason, reason


def test_protect_mcp_refuses_when_it_cannot_see_the_tool_name(project):
    """The same guarantee for the DevBench hook: a payload whose tool_name did not arrive is a call it cannot judge."""
    got, reason = fire(project, "protect-mcp.sh", {"tool_input": {"command": "smp reset"}})
    assert got == "deny", "protect-mcp allowed a call whose tool it could not read"
    assert "GUARD INERT" in reason, reason


@pytest.mark.parametrize("hook", ["protect-files.sh", "protect-bash.sh", "protect-mcp.sh"])
def test_a_blocking_hook_refuses_on_genuinely_empty_stdin(project, hook):
    """The exact shape `cat /dev/stdin` produced on every invocation."""
    got, reason = fire(project, hook, None)
    assert got == "deny", f"{hook} allowed a call after reading nothing"
    assert "GUARD INERT" in reason, reason


@pytest.mark.parametrize("hook", ["backup-before-edit.sh", "snapshot-before-tool.sh"])
def test_a_recording_hook_logs_rather_than_blocks_on_empty_stdin(project, hook):
    """These two must never block -- that is not their job -- but they must not pass
    in silence either, because the silence is what hid the outage."""
    got, _ = fire(project, hook, None)
    assert got == "allow"
    log = project / ".claude" / "backups" / "AUDIT_LOG.txt"
    assert log.is_file(), "nothing was written to the audit log"
    assert "BLIND" in log.read_text(encoding="utf-8", errors="replace")


def test_every_hook_writes_a_liveness_heartbeat(project):
    """`tools/hook-canary.sh` reads these back. They are the only signal that
    survives the gap between a piped harness and the real hook environment, so a
    hook that stops writing one has removed the sole detector for its own death."""
    for hook, payload in (("protect-files.sh", fpath("C:/work/x.md")),
                          ("protect-bash.sh", cmd("ls -la")),
                          ("protect-mcp.sh", {"tool_name": "mcp__devbench-vr__console", "tool_input": {"command": "help"}}),
                          ("backup-before-edit.sh", fpath("C:/work/x.md")),
                          ("snapshot-before-tool.sh", cmd("ls -la"))):
        fire(project, hook, payload)
    hb = project / ".claude" / "backups" / ".hook-heartbeat" / "claude"
    names = {f.name for f in hb.iterdir()}
    for hook in ("protect-files", "protect-bash", "protect-mcp", "backup-before-edit", "snapshot-before-tool"):
        # every stage, written from inside a real invocation: ran, received, evaluated, delivered
        for stage in ("invoked", "received", "evaluated", "emitted", "latency"):
            assert "{}.{}".format(hook, stage) in names, (hook, stage, sorted(names))
        recv = (hb / (hook + ".received")).read_text(encoding="utf-8").split()
        assert recv[2].startswith("bytes=") and int(recv[2][6:]) > 0, recv
        evaluated = (hb / (hook + ".evaluated")).read_text(encoding="utf-8").split()
        assert evaluated[2] == "evaluated" and evaluated[1] == recv[1], evaluated  # same invocation id
        assert (hb / (hook + ".emitted")).read_text(encoding="utf-8").split()[1] == recv[1]
    # the old 3.x layout is gone: nothing beside the agent directory
    assert not [f for f in hb.parent.iterdir() if f.is_file()], "3.x heartbeat files must not be written"


# --------------------------------------------------------------------------
# Deleting the install: every spelling a review found reaching it unrefused
#
# The old rule was `rm\s+(-[a-z]*f[a-z]*\s+)?<path>` -- exactly ONE flag token,
# which had to contain an `f`. The README meanwhile promised the game directory
# "cannot" be deleted. Each row below was MEASURED reaching the directory before
# the rule was re-anchored on (destroyer AND qualified path) instead of on one
# spelling of rm.
# --------------------------------------------------------------------------



@pytest.mark.parametrize("command,why", [
    (f'rm -rf "{GAME}"', "the spelling that was already caught"),
    (f'rm -f -r "{GAME}"', "two flag tokens"),
    (f'rm -r -f "{GAME}"', "two flag tokens, reversed"),
    (f'rm --recursive --force "{GAME}"', "long flags"),
    (f'rm -rf -- "{GAME}"', "the end-of-options marker"),
    (f'cd "{GAME}" && rm -rf .', "the path is in the cd, not the rm"),
    (f'G="{GAME}"; rm -rf "$G"', "the path is in a variable"),
    (f'rmdir /s /q "{GAMEW}"', "rmdir, not rm"),
    (f'del /f /s /q "{GAMEW}"', "del, not rm"),
    (f'powershell -c "Remove-Item -Recurse -Force \'{GAMEW}\'"',
     "Remove-Item -- and powershell is allow-listed"),
    (f'find "{GAME}" -delete', "find -delete"),
    (f'python -c "import shutil; shutil.rmtree(r\'{GAME}\')"', "shutil.rmtree"),
    ('rm -rf "C:/Users/Moona/Documents/My Games/Skyrim VR"', "the config directory"),
    ('reg.exe delete "HKLM' + BS + 'SOFTWARE' + BS + 'Bethesda Softworks" /f',
     "reg.exe -- the old pattern needed whitespace straight after `reg`"),
    # DOUBLED separators. `shutil.rmtree('C:\\Games\\Skyrim VR')` is not an exotic
    # spelling -- it is how you WRITE a Windows path in Python or PowerShell source,
    # and the hook is handed the command TEXT, not the string Python will build from
    # it. The root-resolution rework emitted exactly one separator per separator, so
    # every doubled form produced NO hook output at all, which the runtime reads as
    # ALLOW. The pre-arc rule matched with `[^"']*` and was separator-agnostic, so
    # this was a REGRESSION -- and the 7,641-command replay could not see it, because
    # no historical command spells the install that way. Replay prices over-blocking
    # well and under-blocking not at all.
    (f'python -c "import shutil; shutil.rmtree(\'{GAME.replace("/", BS * 2)}\')"',
     "doubled backslashes -- the canonical Python spelling"),
    (f'powershell -c "Remove-Item -Recurse -Force \'{GAME.replace("/", BS * 2)}\'"',
     "doubled backslashes via PowerShell"),
    (f'rm -rf "{GAME.replace("/", "//")}"', "doubled forward slashes"),
])
def test_the_install_cannot_be_deleted_however_it_is_spelled(project, command, why):
    got, _ = fire(project, "protect-bash.sh", cmd(command))
    assert got == "deny", f"{why}: {command}"


@pytest.mark.parametrize("command,expected,why", [
    # The controls that keep the rule above from being a blanket refusal.
    ("rm -rf node_modules", "allow", "an rm with no game path"),
    ("rm -rf /tmp/scratch", "allow", "an rm somewhere else entirely"),
    ('grep -rn "rm" README.md', "allow", "the WORD rm inside a quoted argument"),
    ("python -m pytest tests/ -q", "allow", "running the suite"),
    (f'cat "{GAME}/Data/x.esp"', "allow", "READING a game path is not deleting one (and is silent, R11)"),
    (f'cp "{GAME}/Data/x.nif" /tmp/', "allow", "copying OUT is not deleting, and the install is only the source"),
    (f'ls "{GAME}/Data/Scripts"', "allow", "listing is not writing"),

    # ---- paths that merely CONTAIN the word Skyrim, and are not the install ----
    #
    # The rule used to ask "does this command mention a path containing Skyrim", which
    # matched all four of these. The scratchpad one bit five times in a single session:
    # a Claude Code scratchpad path contains `C--GOG-Games-The-Elder-Scrolls-V-Skyrim-VR`,
    # so tidying a temp file read as deleting the game.
    #
    # Note what these rows are NOT: they are not a scoping problem, and a shell parser
    # would not fix them. It would bind the verb to its target correctly and still
    # refuse, because the TARGET matches. That is why the rule resolves a real install
    # root instead.
    ("rm -rf \"C:/Users/x/AppData/Local/Temp/claude/"
     "C--GOG-Games-The-Elder-Scrolls-V-Skyrim-VR/sess/scratchpad/tmp\"",
     "allow", "the session SCRATCHPAD is not the install"),
    ('rm -rf "C:/Users/x/Projects/skyrimvr-claude-toolkit/build"',
     "allow", "this toolkit's own checkout is not the install"),
    ('rm -rf "C:/Temp/skyrim-notes"',
     "allow", "a temp file merely NAMED skyrim"),
    ('git clone https://github.com/x/Open-Composite-Unleashed-for-Skyrim-VR.git /tmp/b',
     "allow", "a repository whose NAME contains Skyrim VR"),

    # Champollion is scoped to the live install, so the remedy its own refusal
    # prescribes -- copy the .pex to a temp directory and run it there -- must work.
    ('Champollion.exe "C:/Temp/work/Data/Scripts/A.pex"',
     "allow", "a .pex copied OUT of the install is the prescribed workaround"),

    # ...and the relative-Data advisory, which no resolved root can cover because a
    # hook is never told the caller's working directory.
    ("rm -rf Data/Meshes/x.nif", "advise", "a relative delete inside Data, with flags"),
    ("rm mydata/cache.bin", "allow", "the word boundary: mydata is not Data"),
])
def test_the_delete_rule_does_not_swallow_ordinary_work(project, command, expected, why):
    got, _ = fire(project, "protect-bash.sh", cmd(command))
    assert got == expected, f"{why}: {command}"


def test_a_redirect_elsewhere_is_not_called_a_redirect_into_the_game(project):
    """`[^"']*` spanned the whole command, so any redirect plus a later mention of
    Data or Skyrim produced "redirecting output into the game/config directory" --
    naming a redirect that goes to /tmp. A warning that misdescribes what it saw
    teaches the reader to stop reading warnings."""
    got, ctx = fire(project, "protect-bash.sh",
                    cmd("grep foo bar.txt > /tmp/o.txt && ls Data/Scripts"))
    assert "Redirecting output into the game" not in ctx, ctx


def test_editing_this_toolkits_own_checkout_is_not_editing_a_game_install(project):
    """The catch-all matched a bare `Skyrim`, and this repository's directory name
    contains it -- so every edit to CHANGELOG.md or tools/*.py was annotated
    "Editing inside the live game/config install", which is simply false."""
    for path in ("C:/Users/x/Projects/skyrimvr-claude-toolkit/CHANGELOG.md",
                 "C:/Users/x/Projects/skyrimvr-claude-toolkit/tools/crash-triage.py"):
        got, ctx = fire(project, "protect-files.sh", fpath(path))
        assert got == "allow", f"{path} -> {got}: {ctx}"


def test_a_plugin_path_with_a_trailing_space_is_still_denied(project):
    r"""The deny was anchored `\.(esp|...)$`, so a trailing space slipped past it --
    and Win32 strips the trailing space, so the write lands on the plugin anyway."""
    got, _ = fire(project, "protect-files.sh", fpath("C:/Games/Skyrim VR/Data/M.esp "))
    assert got == "deny"


# --------------------------------------------------------------------------
# the wrapper is how a hook SPEAKS
# --------------------------------------------------------------------------

def test_the_delete_guard_refuses_when_it_cannot_locate_the_install(tmp_path):
    """A guard that cannot work out WHICH directory the install is has evaluated
    nothing, and must refuse rather than shrug.

    The delete rules now key on a resolved install root instead of the word "Skyrim"
    appearing anywhere in a path. That trades one failure mode for another: if the root
    cannot be derived, the rules match nothing and every deletion sails through -- the
    inert-hook state again, by a fourth door.

    The branch is otherwise unreachable from the suite (the wrapper's ROOT is `cd ... && pwd`,
    which always yields an absolute path), so it is exercised the way the Python-unusable
    case is: on a DOCTORED COPY with the resolution knocked out. Written because the
    mutation gate reported the fail-closed branch could be deleted with nothing going
    red -- i.e. it was shipped, load-bearing, and untested.
    """
    root = install_hooks(tmp_path / "install", names={"protect-bash.sh"})
    hook = root / ".claude" / "hooks" / "protect-bash.sh"
    text = hook.read_text(encoding="utf-8")
    # BOTH roots must be handed to the engine blank. The wrapper passes the install twice -- the MSYS
    # spelling from `pwd` and the Windows spelling from `pwd -W` -- and the engine also reads the
    # declared env file from --root, so blanking the flags knocks out every route to a root.
    marker = '--root "$ROOT" --root-win "$ROOT_WIN"'
    assert text.count(marker) == 1, f"{marker} moved; this test is stale"
    text = text.replace(marker, '--root "" --root-win ""')
    hook.write_text(text, encoding="utf-8", newline="\n")

    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    r = subprocess.run([_bash(), str(hook)],
                       input=json.dumps(cmd(f'rm -rf "{GAME}"')),
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.stdout.strip(), "the hook emitted NOTHING, which the runtime reads as allow"
    h = json.loads(r.stdout)["hookSpecificOutput"]
    assert h["permissionDecision"] == "deny", "an unlocatable install must not allow"
    assert "GUARD INERT" in h["permissionDecisionReason"]


def _hook_at(tmp_path, root_name, paths_env=""):
    """A throwaway install whose ROOT NAME is chosen by the test, so the rules are
    built from that path rather than from a fixture constant."""
    root = install_hooks(tmp_path / root_name, names={"protect-bash.sh"})
    if paths_env:
        (root / ".claude" / "skyrim-paths.env").write_text(
            paths_env, encoding="utf-8", newline="\n")
    return root


def _decide(root, command):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    r = subprocess.run(
        [_bash(), str(root / ".claude" / "hooks" / "protect-bash.sh")],
        input=json.dumps(cmd(command)),
        capture_output=True, text=True, env=env, timeout=60)
    h = json.loads(r.stdout).get("hookSpecificOutput") if r.stdout.strip() else None
    if not h:
        return "allow"            # the engine says `{}` for a silent allow
    return h.get("permissionDecision", "advise")


def test_an_install_path_containing_regex_metacharacters_is_still_guarded(tmp_path):
    """`C:/Program Files (x86)/Steam/steamapps/common/Skyrim VR` is the most common
    real Steam layout there is, and `(x86)` unescaped is a valid ERE GROUP -- it
    matches `Program Files x86`, so the guard would simply never fire on the actual
    directory. `GAME_PATH` stays non-empty, so the fail-closed branch does not save
    it either: it is the inert-guard state by another door.

    MEASURED by the release review: deleting the escaping arm from `path_to_ere`
    left all 70 tests in this file green while this exact deletion flipped to allow.
    """
    root = _hook_at(tmp_path, "Program Files (x86)")
    target = str(root).replace(BS, "/")
    assert _decide(root, f'rm -rf "{target}"') == "deny"


def test_a_configured_config_directory_outside_documents_is_guarded(tmp_path):
    """MO2/Wabbajack/Nolvus put the INIs in the instance's profile folder, which is
    NOT under `Documents/My Games` -- so for exactly those users the hardcoded
    literal rule matches nothing and `SKYRIM_CONFIG_DIR` is the ONLY thing guarding
    their config. That arm had no test: replacing it with `:` left 88 tests green.
    """
    cfg = tmp_path / "MO2" / "profiles" / "Default"
    cfg.mkdir(parents=True)
    cfg_win = str(cfg).replace(BS, "/")
    root = _hook_at(tmp_path, "game", paths_env=f'SKYRIM_CONFIG_DIR="{cfg_win}"\n')
    assert _decide(root, f'rm -rf "{cfg_win}"') == "deny"
    # ...and the control: a same-shaped path that is NOT the configured one.
    other = str(tmp_path / "MO2" / "profiles" / "Someone Else").replace(BS, "/")
    assert _decide(root, f'rm -rf "{other}"') != "deny"


@pytest.mark.parametrize("name", ["protect-bash.sh", "protect-files.sh", "protect-mcp.sh"])
@pytest.mark.parametrize("python,why", [
    ("/c/nonexistent/python.exe", "Python uninstalled after setup"),
    ("{{PYTHON_PATH}}", "the zip was extracted and setup.sh never run"),
])
def test_a_blocking_hook_refuses_when_python_is_unusable(tmp_path, name, python, why):
    """With no interpreter the engine cannot run, and a hook that prints nothing reads as ALLOW.
    That is a live route to the inert state, so the wrapper itself -- needing nothing but bash and
    printf -- says so."""
    root = install_hooks(tmp_path / "install", names={name}, python=python)
    payload = (cmd('rm -rf "C:/Games/Skyrim VR"') if name == "protect-bash.sh"
               else MCP_RESET if name == "protect-mcp.sh" else fpath("C:/Games/Skyrim VR/Data/M.esp"))
    env = no_python_env(CLAUDE_PROJECT_DIR=str(root))
    r = subprocess.run([_bash(), str(root / ".claude" / "hooks" / name)],
                       input=json.dumps(payload), capture_output=True, text=True,
                       env=env, timeout=60)
    assert r.stdout.strip(), f"{why}: hook emitted NOTHING, which reads as allow"
    h = json.loads(r.stdout)["hookSpecificOutput"]
    assert h["permissionDecision"] == "deny", why
    assert "GUARD INERT" in h["permissionDecisionReason"]
    assert "cannot run Python" in h["permissionDecisionReason"]


# --------------------------------------------------------------------------
# the wrapper must not turn a broken engine into an allow
# --------------------------------------------------------------------------

def _with_engine(tmp_path, name, run_py_source):
    """An install whose engine entry point is replaced by `run_py_source`."""
    root = install_hooks(tmp_path / "install", names={name})
    (root / "tools" / "skyguard" / "run.py").write_text(run_py_source, encoding="utf-8", newline="\n")
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    return root, env


def _run_wrapper(root, env, name, payload):
    return subprocess.run([_bash(), str(root / ".claude" / "hooks" / name)],
                          input=json.dumps(payload), capture_output=True, text=True,
                          env=env, timeout=60)


BLOCKING = [
    ("protect-bash.sh", {"tool_name": "Bash", "tool_input": {"command": "ls"}}),
    ("protect-files.sh", {"tool_name": "Write", "tool_input": {"file_path": "C:/work/notes.md"}}),
    ("protect-mcp.sh", {"tool_name": "mcp__devbench-vr__console", "tool_input": {"command": "help"}}),
]
NONBLOCKING = [
    ("backup-before-edit.sh", "backup-before-edit"),
    ("snapshot-before-tool.sh", "snapshot-before-tool"),
]
EDIT = {"tool_name": "Write", "tool_input": {"file_path": "C:/work/a.txt"}}


@pytest.mark.parametrize("name,payload", BLOCKING)
def test_wrapper_denies_when_engine_crashes(tmp_path, name, payload):
    """An engine that dies prints nothing and exits non-zero -- exactly what an ALLOW looks like.
    The wrapper must refuse, naming the exit code."""
    root, env = _with_engine(tmp_path, name, "raise SystemExit(3)\n")
    r = _run_wrapper(root, env, name, payload)
    h = json.loads(r.stdout)["hookSpecificOutput"]
    assert h["permissionDecision"] == "deny"
    assert "GUARD INERT" in h["permissionDecisionReason"]
    assert "engine failed (exit 3)" in h["permissionDecisionReason"]


@pytest.mark.parametrize("name,payload", BLOCKING)
def test_wrapper_denies_on_non_json_output(tmp_path, name, payload):
    """An engine that exits 0 but prints something that is not a JSON verdict (a stray debug line)
    must not be passed through: the runtime would read it as no decision, i.e. allow."""
    root, env = _with_engine(tmp_path, name, 'print("hi")\n')
    r = _run_wrapper(root, env, name, payload)
    h = json.loads(r.stdout)["hookSpecificOutput"]
    assert h["permissionDecision"] == "deny"
    assert "engine failed (exit 0)" in h["permissionDecisionReason"]


@pytest.mark.parametrize("name,payload", BLOCKING)
def test_wrapper_denies_when_engine_prints_a_verdict_but_exits_nonzero(tmp_path, name, payload):
    """An engine that crashed AFTER printing a verdict (an allow, say) has not decided anything it can
    vouch for. Only a clean exit makes the output a verdict."""
    root, env = _with_engine(tmp_path, name, 'print("{}")\nraise SystemExit(3)\n')
    r = _run_wrapper(root, env, name, payload)
    h = json.loads(r.stdout)["hookSpecificOutput"]
    assert h["permissionDecision"] == "deny"
    assert "engine failed (exit 3)" in h["permissionDecisionReason"]


@pytest.mark.parametrize("name,what", NONBLOCKING)
def test_nonblocking_wrapper_logs_when_engine_crashes(tmp_path, name, what):
    """These hooks may not block, but a crash must be on the record: silence would leave an audit log
    that implies a backup exists."""
    root, env = _with_engine(tmp_path, name, "raise SystemExit(3)\n")
    r = _run_wrapper(root, env, name, EDIT)
    assert r.returncode == 0 and r.stdout.strip() == ""
    log = (root / ".claude" / "backups" / "AUDIT_LOG.txt").read_text(encoding="utf-8")
    assert f"{what} FAILED: engine error (exit 3) -- NO backup/snapshot was taken" in log


@pytest.mark.parametrize("name,what", NONBLOCKING)
def test_nonblocking_wrapper_logs_without_python(tmp_path, name, what):
    root = install_hooks(tmp_path / "install", names={name}, python="{{PYTHON_PATH}}")
    env = no_python_env(CLAUDE_PROJECT_DIR=str(root))
    r = _run_wrapper(root, env, name, EDIT)
    assert r.returncode == 0 and r.stdout.strip() == ""
    log = (root / ".claude" / "backups" / "AUDIT_LOG.txt").read_text(encoding="utf-8")
    assert f"{what} FAILED: Python not runnable" in log and "NO backup/snapshot was taken" in log


# --------------------------------------------------------------------------
# stage telemetry: failures leave a record, and a payload-less call cannot clear itself
# (Plan 4 Task 4; helpers are local -- cmd()/fpath() calls in this file are harvested into the corpus)
# --------------------------------------------------------------------------

def _hb_dir(root):
    return root / ".claude" / "backups" / ".hook-heartbeat" / "claude"


def _rec(root, name, stage):
    f = _hb_dir(root) / "{}.{}".format(name[:-3], stage)
    return f.read_text(encoding="utf-8").split() if f.is_file() else None


@pytest.mark.parametrize("name,payload", BLOCKING)
def test_a_crashing_engine_leaves_a_failed_engine_record_and_no_emission(tmp_path, name, payload):
    root, env = _with_engine(tmp_path, name, "raise SystemExit(3)\n")
    _run_wrapper(root, env, name, payload)
    failed = _rec(root, name, "failed")
    assert failed and failed[2] == "engine" and "exit" in failed, failed
    assert _rec(root, name, "emitted") is None, "a refused call must not read as a delivered verdict"
    assert _rec(root, name, "invoked") and _rec(root, name, "received")


@pytest.mark.parametrize("name,payload", BLOCKING)
def test_non_json_output_is_an_emission_failure(tmp_path, name, payload):
    root, env = _with_engine(tmp_path, name, 'print("hi")\n')
    _run_wrapper(root, env, name, payload)
    failed = _rec(root, name, "failed")
    assert failed and failed[2] == "emission", failed
    assert _rec(root, name, "emitted") is None


@pytest.mark.parametrize("name,payload", BLOCKING)
def test_missing_python_is_a_python_failure(tmp_path, name, payload):
    root = install_hooks(tmp_path / "install", names={name}, python="{{PYTHON_PATH}}")
    _run_wrapper(root, no_python_env(CLAUDE_PROJECT_DIR=str(root)), name, payload)
    failed = _rec(root, name, "failed")
    assert failed and failed[2] == "python", failed
    assert _rec(root, name, "invoked") and _rec(root, name, "emitted") is None


@pytest.mark.parametrize("name,what", NONBLOCKING)
def test_nonblocking_failures_leave_records_too(tmp_path, name, what):
    root, env = _with_engine(tmp_path, name, "raise SystemExit(3)\n")
    _run_wrapper(root, env, name, EDIT)
    failed = _rec(root, name, "failed")
    assert failed and failed[2] == "engine", failed
    assert _rec(root, name, "emitted") is None
    root2 = install_hooks(tmp_path / "install2", names={name}, python="{{PYTHON_PATH}}")
    _run_wrapper(root2, no_python_env(CLAUDE_PROJECT_DIR=str(root2)), name, EDIT)
    assert _rec(root2, name, "failed")[2] == "python"


@pytest.mark.parametrize("name,payload", BLOCKING)
def test_an_empty_payload_stays_failed_even_though_the_engine_then_evaluates(tmp_path, name, payload):
    """The engine still runs on an empty payload and a verdict is emitted. That completed evaluation must
    NOT clear the receipt failure of the very call that produced it (it would read HEALTHY at once)."""
    root = install_hooks(tmp_path / "install", names={name})
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    r = subprocess.run([_bash(), str(root / ".claude" / "hooks" / name)], input="", capture_output=True,
                       text=True, env=env, timeout=60)
    assert r.returncode == 0
    failed, ev = _rec(root, name, "failed"), _rec(root, name, "evaluated")
    assert failed and failed[2] == "receipt", failed
    assert ev and float(failed[0]) > float(ev[0]), "the failure must be NEWER than the evaluation it accompanies"
    sys.path.insert(0, str(REPO / "tools"))
    from skyguard import health
    assert health.hook_state(_hb_dir(root), name[:-3], time.time())[0] == "DEGRADED"


def _health():
    sys.path.insert(0, str(REPO / "tools"))
    from skyguard import health
    return health


def _good_call(root, name="protect-bash.sh"):
    payload = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
    r = _run_wrapper(root, dict(os.environ, CLAUDE_PROJECT_DIR=str(root)), name, payload)
    assert r.returncode == 0, r.stderr


def test_the_invocation_sequence_advances_on_every_call(tmp_path):
    root = install_hooks(tmp_path / "install", names={"protect-bash.sh"})
    seqs = []
    for _ in range(3):
        _good_call(root)
        seqs.append(int(_rec(root, "protect-bash.sh", "invoked")[1].split(".")[0]))
    assert seqs == [1, 2, 3], seqs


def test_a_killed_run_is_reported_and_stays_degraded_until_a_newer_completion(tmp_path):
    """The agent's timeout kills a hook after `invoked`; the next call overwrites `invoked`, which used to
    erase the only trace -- and then read HEALTHY."""
    h = _health()
    root = install_hooks(tmp_path / "install", names={"protect-bash.sh"})
    _good_call(root)
    inv = _hb_dir(root) / "protect-bash.invoked"
    inv.write_text("{:.6f} 9.1-1\n".format(time.time() - 200), encoding="utf-8")   # never emitted
    _good_call(root)                       # this call itself completes fine ...
    state, detail = h.hook_state(_hb_dir(root), "protect-bash", time.time())
    assert state == "DEGRADED" and "killed" in detail and "9.1-1" in detail, (state, detail)   # ... and is NOT newer
    _good_call(root)
    state, detail = h.hook_state(_hb_dir(root), "protect-bash", time.time())
    assert state == "HEALTHY" and "recovered" in detail and "killed" in detail, (state, detail)


def test_a_run_still_inside_the_grace_period_is_not_called_killed(tmp_path):
    """Parallel tool calls: another invocation that started a few seconds ago may simply still be running."""
    root = install_hooks(tmp_path / "install", names={"protect-bash.sh"})
    _good_call(root)
    (_hb_dir(root) / "protect-bash.invoked").write_text("{:.6f} 9.1-1\n".format(time.time() - 5), encoding="utf-8")
    _good_call(root)
    assert _rec(root, "protect-bash.sh", "failed") is None


def test_a_failed_run_is_not_also_called_killed(tmp_path):
    """An old invocation that recorded its own failure (and so never emitted) is not reported twice."""
    root, env = _with_engine(tmp_path, "protect-bash.sh", "raise SystemExit(3)\n")
    _run_wrapper(root, env, "protect-bash.sh", BLOCKING[0][1])
    inv = _hb_dir(root) / "protect-bash.invoked"
    old = inv.read_text(encoding="utf-8").split()
    inv.write_text("{:.6f} {}\n".format(time.time() - 500, old[1]), encoding="utf-8")
    (root / "tools" / "skyguard" / "run.py").write_bytes((REPO / "tools" / "skyguard" / "run.py").read_bytes())
    _run_wrapper(root, env, "protect-bash.sh", BLOCKING[0][1])     # a healthy call: it must not rewrite the failure
    assert "killed" not in " ".join(_rec(root, "protect-bash.sh", "failed")), _rec(root, "protect-bash.sh", "failed")


def test_a_session_start_that_could_not_run_kb_guard_is_degraded(tmp_path):
    """The wrapper emits the engine's text either way; the canary must still see that nothing was checked."""
    h = _health()
    root = install_hooks(tmp_path / "install", names={"session-kb-guard.sh"})
    r = _run_wrapper(root, dict(os.environ, CLAUDE_PROJECT_DIR=str(root)), "session-kb-guard.sh",
                     {"hook_event_name": "SessionStart", "source": "startup"})
    assert r.returncode == 0 and "NOT RUN" in r.stdout
    state, detail = h.hook_state(_hb_dir(root), "session-kb-guard", time.time())
    assert state == "DEGRADED" and "NOT_RUN" in detail, (state, detail)


def test_telemetry_never_blocks_a_hook(tmp_path):
    """An unwritable telemetry directory is recorded nowhere and changes no decision."""
    root = install_hooks(tmp_path / "install", names={"protect-files.sh"})
    hb_parent = root / ".claude" / "backups"
    hb_parent.mkdir(parents=True)
    (hb_parent / ".hook-heartbeat").write_text("not a directory", encoding="utf-8")   # mkdir -p fails
    r = _run_wrapper(root, dict(os.environ, CLAUDE_PROJECT_DIR=str(root)), "protect-files.sh",
                     {"tool_name": "Write", "tool_input": {"file_path": "C:/work/notes.md"}})
    assert r.returncode == 0 and r.stdout.strip() == "{}", (r.stdout, r.stderr)


# --------------------------------------------------------------------------
# invoked the way settings.json invokes them
# --------------------------------------------------------------------------
# settings.json runs `bash "$CLAUDE_PROJECT_DIR/.claude/hooks/X.sh"`. On Windows that is a forward-slash
# or MIXED path (`C:\Users\me\proj/.claude/hooks/X.sh`), never the all-backslash form a Python
# `str(Path)` gives. The wrapper derives its own location from $BASH_SOURCE, so each spelling has to
# resolve to the same install -- a normalisation slip there denied EVERY call in production while every
# test (which passed a backslash-only path) stayed green.

def _spellings(root):
    fwd = root.as_posix()
    mixed = str(root) + "/.claude/hooks/"
    return [
        ("forward-slash", lambda name: (f"{fwd}/.claude/hooks/{name}", None)),
        ("mixed", lambda name: (mixed + name, None)),
        ("relative", lambda name: (f".claude/hooks/{name}", root)),
    ]


def _invoke(root, name, payload, how):
    path, cwd = dict((k, v) for k, v in _spellings(root))[how](name)
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    return subprocess.run([_bash(), path], input=json.dumps(payload), capture_output=True, text=True,
                          env=env, cwd=str(cwd) if cwd else None, timeout=60)


def _decision(r):
    assert r.returncode == 0, r.stderr
    h = (json.loads(r.stdout) if r.stdout.strip() else {}).get("hookSpecificOutput") or {}
    return h.get("permissionDecision", "allow"), h.get("permissionDecisionReason", "")


@pytest.mark.parametrize("how", ["forward-slash", "mixed", "relative"])
def test_protect_bash_resolves_its_install_however_it_is_launched(project, how):
    ok = _decision(_invoke(project, "protect-bash.sh", {"tool_name": "Bash", "tool_input": {"command": "ls"}}, how))
    assert ok[0] == "allow", f"{how}: an `ls` was not allowed: {ok}"
    rm = {"tool_name": "Bash", "tool_input": {"command": f'rm -rf "{project.as_posix()}"'}}
    got = _decision(_invoke(project, "protect-bash.sh", rm, how))
    assert got[0] == "deny" and got[1].startswith("Blocked: this deletes"), f"{how}: deleting the install was not refused: {got}"


@pytest.mark.parametrize("how", ["forward-slash", "mixed", "relative"])
def test_protect_files_works_however_it_is_launched(project, how):
    esp = {"tool_name": "Write", "tool_input": {"file_path": "C:/Games/Skyrim VR/Data/M.esp"}}
    got = _decision(_invoke(project, "protect-files.sh", esp, how))
    assert got[0] == "deny" and got[1].startswith("Blocked"), f"{how}: {got}"
    ok = _decision(_invoke(project, "protect-files.sh",
                           {"tool_name": "Write", "tool_input": {"file_path": "C:/work/notes.md"}}, how))
    assert ok[0] == "allow", f"{how}: {ok}"


@pytest.mark.parametrize("how", ["forward-slash", "mixed", "relative"])
def test_nonblocking_hooks_find_their_engine_however_launched(project, how):
    """A launch that cannot find its engine logs FAILED and does NOTHING. So assert the work happened:
    a backup file lands for an existing file, and a snapshot is taken for a fresh .psc."""
    backups = project / ".claude" / "backups"
    log = backups / "AUDIT_LOG.txt"
    before = log.read_text(encoding="utf-8") if log.is_file() else ""

    target = project / "Data" / f"launch-{how}.txt"
    target.write_text("keep me", encoding="utf-8")
    r = _invoke(project, "backup-before-edit.sh",
                {"tool_name": "Write", "tool_input": {"file_path": target.as_posix()}}, how)
    assert r.returncode == 0 and r.stdout.strip() == ""
    made = [p for p in backups.iterdir() if p.is_file() and p.name.endswith(f"launch-{how}.txt")]
    assert made, f"{how}: no backup of {target.name} was taken; audit: {log.read_text(encoding='utf-8')[len(before):]}"
    assert made[0].read_text(encoding="utf-8") == "keep me"

    (project / "Data" / "Scripts" / "Source" / f"Fresh_{how.replace('-', '_')}.psc").write_text(
        "ScriptName Fresh\n", encoding="utf-8")
    r = _invoke(project, "snapshot-before-tool.sh",
                {"tool_name": "Bash", "tool_input": {"command": "some-tool run"}}, how)
    assert r.returncode == 0 and r.stdout.strip() == ""
    after = log.read_text(encoding="utf-8")[len(before):]
    assert "AUTO-SNAPSHOT" in after, f"{how}: no snapshot was taken: {after}"
    assert "FAILED" not in after, after


@pytest.mark.parametrize("how", ["forward-slash", "mixed", "relative"])
def test_session_kb_guard_finds_its_engine_however_it_is_launched(tmp_path, how):
    """session-kb-guard once broke on forward-slash launches while every test passed an all-backslash
    path. Assert it really RAN the guard (a KB GUARD report that is not NOT RUN) and logged no failure."""
    root = install_hooks(tmp_path / "install", names={"session-kb-guard.sh"})
    (root / "tools").mkdir(exist_ok=True)
    shutil.copy2(REPO / "tools" / "kb-guard.sh", root / "tools" / "kb-guard.sh")
    (root / "KNOWLEDGEBASE.md").write_text("notes " * 200, encoding="utf-8")
    path, cwd = dict(_spellings(root))[how]("session-kb-guard.sh")
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    r = subprocess.run([_bash(), path], input=json.dumps({"hook_event_name": "SessionStart", "source": "startup"}),
                       capture_output=True, text=True, env=env, cwd=str(cwd) if cwd else None, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "KB GUARD:" in r.stdout, f"{how}: the guard printed no report: {r.stdout!r}"
    assert "NOT RUN" not in r.stdout, f"{how}: {r.stdout}"
    log = root / ".claude" / "backups" / "AUDIT_LOG.txt"
    text = log.read_text(encoding="utf-8") if log.is_file() else ""
    assert "FAILED" not in text and "NOT RUN" not in text, text
    assert any((root / ".claude" / "backups" / "kb").rglob("KNOWLEDGEBASE.md")), "no snapshot was taken"


# --------------------------------------------------------------------------
# the fork-free location block (latency fix, 2026-10-02)
# --------------------------------------------------------------------------
# The wrappers used to find their install with `$(cd "$(dirname ...)" && pwd)` and `pwd -W` in
# subshells; each fork costs 10-70 ms on Git Bash. They now `cd` in their own shell and convert a
# drive path (`/c/x` -> `C:/x`) TEXTUALLY, asking `pwd -W` only for an MSYS mount such as /tmp.
#
# Git Bash ALSO converts a plain-path argument to a native python.exe on its own, which masks a wrong
# ROOT_WIN in every other test here. MSYS2_ARG_CONV_EXCL=* switches that conversion off, so these
# tests see the wrapper's own spelling and nothing else.

# Payloads here are built by _shell/_write, NOT cmd()/fpath(): scripts/guard-corpus/record.py harvests every
# cmd()/fpath() call in this file into the frozen equivalence corpus, and these rows test the wrapper, not rules.
def _shell(c):
    return {"tool_name": "Bash", "tool_input": {"command": c}}


def _write(p):
    return {"tool_name": "Write", "tool_input": {"file_path": p}}


def _fire_at(launch: str, root: Path, payload) -> tuple:
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root), MSYS2_ARG_CONV_EXCL="*")
    return _decision(subprocess.run([_bash(), launch], input=json.dumps(payload), capture_output=True,
                                    text=True, env=env, timeout=60))


def _check_root_spelling(root: Path, launch: str):
    ok = _fire_at(launch, root, _shell("ls"))
    assert ok[0] == "allow", f"an `ls` was not allowed -- the engine path was misspelled: {ok}"
    got = _fire_at(launch, root, _shell(f'rm -rf "{root.as_posix()}"'))
    assert got[0] == "deny" and got[1].startswith("Blocked: this deletes"), f"deleting the install was not refused: {got}"


def test_a_drive_root_is_converted_to_its_windows_spelling(tmp_path):
    """`/c/Users/...` must reach the engine as `C:/Users/...`, built without a `pwd -W` fork."""
    root = install_hooks(tmp_path / "Skyrim VR", names={"protect-bash.sh"})
    win = root.as_posix()
    if os.name != "nt" or len(win) < 3 or win[1:3] != ":/":
        pytest.skip(f"not a Windows drive path: {win!r}")
    # Launched by its drive spelling explicitly: pytest's tmp dir is usually also reachable as /tmp.
    _check_root_spelling(root, f"/{win[0].lower()}{win[2:]}/.claude/hooks/protect-bash.sh")


def test_a_root_on_an_msys_mount_still_gets_its_windows_spelling(tmp_path):
    """An install reached through an MSYS mount (`/tmp/...`) has no textual Windows spelling; the
    wrapper must fall back to `pwd -W` there rather than hand python.exe a path it cannot open."""
    tmp_win = subprocess.run([_bash(), "-c", "cd /tmp 2>/dev/null && pwd -W"],
                             capture_output=True, text=True, timeout=60).stdout.strip()
    root = install_hooks(tmp_path / "Skyrim VR", names={"protect-bash.sh"})
    here = root.as_posix()
    if not tmp_win or not here.lower().startswith(tmp_win.lower() + "/"):
        pytest.skip(f"{here} is not under the /tmp mount ({tmp_win!r})")
    _check_root_spelling(root, "/tmp" + here[len(tmp_win):] + "/.claude/hooks/protect-bash.sh")


@pytest.mark.parametrize("name", ["protect-bash.sh", "protect-files.sh", "protect-mcp.sh", "backup-before-edit.sh",
                                  "snapshot-before-tool.sh", "session-kb-guard.sh"])
def test_a_fresh_install_gets_its_heartbeat_directory(tmp_path, name):
    """mkdir now runs only when the directory is missing. On a fresh install it IS missing, and a
    hook that skips it writes no heartbeat -- hook-canary then reports a live hook as dead."""
    root = install_hooks(tmp_path / "install", names={name})
    payload = ({"hook_event_name": "SessionStart", "source": "compact"} if name == "session-kb-guard.sh"
               else _shell("ls") if name in ("protect-bash.sh", "snapshot-before-tool.sh")
               else {"tool_name": "mcp__devbench-vr__console", "tool_input": {"command": "help"}}
               if name == "protect-mcp.sh" else _write("C:/work/notes.md"))
    hb = root / ".claude" / "backups" / ".hook-heartbeat"
    assert not hb.exists()
    r = _run_wrapper(root, dict(os.environ, CLAUDE_PROJECT_DIR=str(root)), name, payload)
    assert r.returncode == 0, r.stderr
    hb = hb / "claude"
    for stage in ("invoked", "received", "evaluated", "emitted"):
        assert (hb / "{}.{}".format(name[:-3], stage)).is_file(), f"{name}: no {stage} record on a fresh install"
    assert "bytes=" in (hb / (name[:-3] + ".received")).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# the declared env file is read by the engine itself (fix pass, 2026-10-02)
# --------------------------------------------------------------------------
# run.py read `.claude/skyrim-paths.env` from `Path(--root)`, a POSIX spelling (/c/...). Native Windows
# python only opened it because Git Bash converts the argument; with conversion off the read failed
# silently and a DECLARED game root (outside the install) dropped out, so deleting it was allowed.
_NO_CONV = [{"MSYS2_ARG_CONV_EXCL": "*"}, {"MSYS_NO_PATHCONV": "1"}]


def _declared_fire(root, extra_env, command):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root), **extra_env)
    return _decision(subprocess.run([_bash(), str(root / ".claude" / "hooks" / "protect-bash.sh")],
                                    input=json.dumps(_shell(command)), capture_output=True, text=True,
                                    env=env, timeout=60))


@pytest.mark.parametrize("extra", _NO_CONV, ids=["MSYS2_ARG_CONV_EXCL", "MSYS_NO_PATHCONV"])
def test_a_declared_root_is_read_with_path_conversion_off(tmp_path, extra):
    root = install_hooks(tmp_path / "install", names={"protect-bash.sh"})
    other = tmp_path / "declared game"
    (root / ".claude" / "skyrim-paths.env").write_text(
        f'SKYRIM_GAME_ROOT="{other.as_posix()}"\n', encoding="utf-8", newline="\n")
    got = _declared_fire(root, extra, f'rm -rf "{other.as_posix()}/Data"')
    assert got[0] == "deny", f"a declared game root was dropped when path conversion was off: {got}"


def test_an_unreadable_declared_env_file_fails_closed(tmp_path):
    root = install_hooks(tmp_path / "install", names={"protect-bash.sh"})
    (root / ".claude" / "skyrim-paths.env").write_bytes(b'SKYRIM_GAME_ROOT="\xff\xfe\x80"\n')
    got = _declared_fire(root, {"MSYS2_ARG_CONV_EXCL": "*"}, "ls")
    assert got[0] == "deny" and "GUARD INERT" in got[1], got
