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
from pathlib import Path

import pytest

from conftest import REPO

HOOKS = REPO / ".claude" / "hooks"
BS = chr(92)


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
    jq = shutil.which("jq")
    if not jq:
        pytest.skip("jq not installed")
    root = tmp_path_factory.mktemp("install") / "Skyrim VR"
    (root / ".claude" / "hooks").mkdir(parents=True)
    (root / "Data" / "Scripts" / "Source").mkdir(parents=True)
    for f in HOOKS.glob("*.sh"):
        text = f.read_text(encoding="utf-8").replace("{{JQ_PATH}}", jq.replace(BS, "/"))
        (root / ".claude" / "hooks" / f.name).write_text(text, encoding="utf-8", newline="\n")
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
    ("C:/Games/Skyrim VR/Data/Textures/x.dds", "advise", "an ordinary asset is a note"),
    ("C:/Games/Skyrim VR/.claude/hooks/x.sh", "allow", "our own workspace is silent"),
    ("C:/work/notes.md", "allow", "nothing to do with the install"),
    ("C:/Users/x/Documents/My Games/Skyrim VR/SkyrimVR.ini", "advise", "config is consequential"),
    ("C:/Games/Skyrim VR/Data/Scripts/Source/A.psc", "advise", "a .pex loads at startup only"),
])
def test_protect_files_decides(project, path, expected, why):
    got, _ = fire(project, "protect-files.sh", fpath(path))
    assert got == expected, f"{why}: {path}"


# --------------------------------------------------------------------------
# protect-bash
# --------------------------------------------------------------------------

@pytest.mark.parametrize("command,expected,why", [
    ('rm -rf "C:/Games/Skyrim VR"', "deny", "deleting the install"),
    ('rm -rf "C:' + BS + 'Games' + BS + 'Skyrim VR"', "deny",
     "the backslash form -- cmd.exe and powershell are both callable, so this is "
     "the spelling that got through a forward-slash-only guard"),
    ('rm -rf "/c/Games/Skyrim VR"', "deny", "the MSYS form"),
    ('rm -rf "C:/Users/x/Documents/My Games/Skyrim VR"', "deny", "the config directory"),
    ('reg delete "HKLM' + BS + 'SOFTWARE' + BS + 'Bethesda"', "deny", "registry keys"),
    ('Champollion.exe "C:/Games/Skyrim/Data/Scripts/A.pex"', "deny",
     "Champollion writes to Data/Scripts/Source regardless of flags and can leave "
     "the output empty; this has destroyed a .psc twice"),
    ('tool --output "C:/tmp/A.psc"', "deny", "output aimed straight at a source file"),
    # Controls. A guard that fires on these is one that gets deleted.
    ("ls -la", "allow", "plain ls"),
    ("git status", "allow", "git status"),
    ("rm -rf /tmp/scratch", "allow", "an rm outside the install"),
    ('python -c "print(1)"', "allow", "ordinary work"),
    ('Champollion.exe "C:/tmp/A.pex" -p C:/tmp', "allow", "Champollion somewhere safe"),
    ("rm mydata/cache.bin", "allow", "the word boundary: mydata is not Data"),
    # Advisories.
    ('cat "C:/Games/Skyrim VR/Data/x.esp"', "advise", "reading a plugin is fine"),
    ("rm Data/Textures/x.dds", "advise", "a relative delete inside Data"),
    ("rm ./Data/x.dds", "advise", "dot-relative too"),
    ("cat loadorder.txt", "advise", "the mod manager owns load order"),
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


@pytest.mark.parametrize("hook", ["protect-files.sh", "protect-bash.sh"])
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
                          ("backup-before-edit.sh", fpath("C:/work/x.md")),
                          ("snapshot-before-tool.sh", cmd("ls -la"))):
        fire(project, hook, payload)
    hb = project / ".claude" / "backups" / ".hook-heartbeat"
    alive = {f.name for f in hb.iterdir() if not f.name.endswith(".blind")}
    assert alive == {"protect-files", "protect-bash",
                     "backup-before-edit", "snapshot-before-tool"}, sorted(alive)
    for f in hb.iterdir():
        if not f.name.endswith(".blind"):
            assert "payload_bytes=" in f.read_text(encoding="utf-8")
            assert "payload_bytes=0" not in f.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Deleting the install: every spelling a review found reaching it unrefused
#
# The old rule was `rm\s+(-[a-z]*f[a-z]*\s+)?<path>` -- exactly ONE flag token,
# which had to contain an `f`. The README meanwhile promised the game directory
# "cannot" be deleted. Each row below was MEASURED reaching the directory before
# the rule was re-anchored on (destroyer AND qualified path) instead of on one
# spelling of rm.
# --------------------------------------------------------------------------

GAME = "C:/GOG Games/The Elder Scrolls V Skyrim VR"
GAMEW = GAME.replace("/", BS)


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
    (f'cat "{GAME}/Data/x.esp"', "advise", "READING a game path is not deleting one"),
    (f'cp "{GAME}/Data/x.nif" /tmp/', "advise", "copying OUT is not deleting"),
    (f'ls "{GAME}/Data/Scripts"', "allow", "listing is not writing"),
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
# jq is how a hook SPEAKS
# --------------------------------------------------------------------------

def _hook_with_jq(project, tmp_path, name, jq_value):
    src = (HOOKS / name).read_text(encoding="utf-8").replace("{{JQ_PATH}}", jq_value)
    d = tmp_path / "hooks_jq" / ".claude" / "hooks"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(src, encoding="utf-8", newline="\n")
    return d.parent.parent


@pytest.mark.parametrize("name", ["protect-bash.sh", "protect-files.sh"])
@pytest.mark.parametrize("jq_value,why", [
    ("/c/nonexistent/jq.exe", "jq uninstalled after setup"),
    ("{{JQ_PATH}}", "the zip was extracted and setup.sh never run"),
])
def test_a_blocking_hook_refuses_when_jq_is_unusable(tmp_path, name, jq_value, why):
    """deny() emits its JSON THROUGH jq, so without jq the refusal itself vanishes
    and the hook produces nothing -- which the runtime reads as ALLOW. That is a
    second live route to the inert state, by another door, and it needs no jq to
    close: the one refusal that must not depend on jq is printed with printf."""
    root = _hook_with_jq(None, tmp_path, name, jq_value)
    payload = (cmd('rm -rf "C:/Games/Skyrim VR"') if name == "protect-bash.sh"
               else fpath("C:/Games/Skyrim VR/Data/M.esp"))
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    r = subprocess.run([_bash(), str(root / ".claude" / "hooks" / name)],
                       input=json.dumps(payload), capture_output=True, text=True,
                       env=env, timeout=60)
    assert r.stdout.strip(), f"{why}: hook emitted NOTHING, which reads as allow"
    j = json.loads(r.stdout)
    h = j["hookSpecificOutput"]
    assert h["permissionDecision"] == "deny", why
    assert "GUARD INERT" in h["permissionDecisionReason"]
