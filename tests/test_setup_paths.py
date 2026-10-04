"""Behavioral tests for the paths setup.sh writes into CLAUDE.md.

Every test here is anchored to a bug this toolkit actually shipped. The
structural CI added in v3.6 cannot catch any of them: `setup.sh` is valid bash
in every case, and `setup-smoke` only checks that settings.json parses
afterwards -- it never looks at a single path setup.sh wrote.
"""

from __future__ import annotations

import pytest

from conftest import claude_md, game_root_win, hook_python_lines, key_path, run_setup, write_mo2_ini


# ---------------------------------------------------------------- stock/Vortex

def test_stock_install_is_not_reported_as_mo2(game_dir):
    """The null case must stay null -- widening MO2 detection must not make a
    plain Vortex/stock install start claiming an instance."""
    r = run_setup(game_dir)
    assert r.returncode == 0, r.stdout + r.stderr
    # Assert on the emitted "Mod manager" line, not the whole document: CLAUDE.md
    # documents MO2 in prose regardless of what was detected, so a naive
    # substring search over the file matches the docs and proves nothing.
    line = key_path(game_dir, "Mod manager")
    assert "stock layout" in line, f"expected the stock branch, got: {line!r}"
    assert "Mod Organizer 2" not in line


def test_game_root_is_a_windows_path_not_an_msys_path(game_dir):
    """Regression, v3.2.1: CLAUDE.md got `/c/Games/Skyrim` from bare `pwd`, a
    form Claude's file tools and PowerShell cannot open. It must be the
    `C:/Games/Skyrim` form.

    On Linux `pwd -W` does not exist and a POSIX path is correct, so this
    asserts only that the written root matches what setup.sh should have
    resolved -- which on Windows is the drive-letter form.
    """
    r = run_setup(game_dir)
    assert r.returncode == 0, r.stdout + r.stderr
    expected = game_root_win(game_dir)
    line = key_path(game_dir, "Game root")
    assert expected in line, f"expected {expected!r} in: {line!r}"
    assert "{{GAME_ROOT}}" not in line


def test_no_placeholders_survive(game_dir):
    """Any `{{...}}` left in CLAUDE.md is a substitution that silently did
    nothing."""
    run_setup(game_dir)
    text = claude_md(game_dir)
    leftovers = [ln for ln in text.splitlines() if "{{" in ln and "}}" in ln]
    assert not leftovers, f"unsubstituted placeholders: {leftovers}"


def test_paths_are_not_backslash_corrupted(game_dir):
    """Regression, v3.2.1: `$LOCALAPPDATA` went into sed's replacement text
    unescaped, so `C:\\Users\\You\\AppData\\Local` came out `C:SERSYOUAPPDATAocal`.
    The corruption signature is a run of capitals with the separators eaten.
    """
    r = run_setup(game_dir, env_overrides={
        "LOCALAPPDATA": r"C:\Users\testuser\AppData\Local",
    })
    assert r.returncode == 0, r.stdout + r.stderr
    line = key_path(game_dir, "Load order")
    assert "SERS" not in line and "APPDATA" not in line, f"sed corruption: {line}"
    assert "Users/testuser/AppData/Local" in line, f"got: {line}"


# ---------------------------------------------------------------- portable MO2

def test_portable_mo2_instance_is_detected(portable_mo2):
    """Regression, v3.5.4: a Nolvus/Wabbajack portable instance was never
    found, so setup fell through to the stock branch and wrote Documents /
    %LOCALAPPDATA% paths that exist but that no MO2 profile uses."""
    game, inst, mods = portable_mo2
    r = run_setup(game)
    assert r.returncode == 0, r.stdout + r.stderr
    line = key_path(game, "Mod manager")
    assert "Mod Organizer 2" in line, f"portable instance not detected: {line!r}"
    text = claude_md(game)
    assert "Nolvus Awakening" in text, "profile not resolved"
    assert "MODS/mods" in text.replace("\\", "/"), "mods path not written"


def test_portable_mo2_instance_path_is_normalized(portable_mo2):
    """Regression, v3.5.4: the sibling probe resolves through `..`, which wrote
    a correct-but-unreadable `STOCK GAME/../MO2` into CLAUDE.md."""
    game, inst, mods = portable_mo2
    run_setup(game)
    text = claude_md(game).replace("\\", "/")
    assert "/../" not in text, "unnormalized `..` left in an emitted path"


def test_profile_local_inis_win_over_documents(portable_mo2):
    """When the MO2 profile carries its own INIs and load order, CLAUDE.md must
    point at the profile -- not at Documents/My Games."""
    game, inst, mods = portable_mo2
    run_setup(game)
    cfg = key_path(game, "User INI configs").replace("\\", "/")
    assert "profiles/Nolvus Awakening" in cfg, f"got: {cfg}"
    assert "My Games" not in cfg, f"still pointing at Documents: {cfg}"


def test_byte_array_wrapping_is_unwrapped(portable_mo2):
    """Regression, v3.5.4: MO2 writes gamePath and selected_profile as
    `@ByteArray(...)`. Not unwrapping it meant the comparison could never match
    -- which also silently broke the documented MO2_INSTANCE_INI escape hatch."""
    game, inst, mods = portable_mo2
    run_setup(game)
    line = key_path(game, "Mod manager")
    # Assert the MO2 branch was actually taken AND that the profile -- which is
    # only readable by unwrapping @ByteArray(...) -- landed on that line.
    #
    # An earlier version of this test checked `"@ByteArray" not in claude_md()`
    # and `"Nolvus Awakening" in claude_md()`. The mutation gate proved both
    # pass vacuously when the unwrap is removed: detection then fails, so no MO2
    # block is written at all (hence no @ByteArray anywhere), and the profile
    # name still appears because it is part of the game directory path.
    assert "Mod Organizer 2" in line, f"@ByteArray broke detection: {line!r}"
    assert "Nolvus Awakening" in line, f"profile not unwrapped: {line!r}"
    assert "@ByteArray" not in claude_md(game), "QSettings wrapper leaked through"


def test_mo2_instance_ini_escape_hatch_works(portable_mo2, tmp_path):
    """The documented override must actually work -- it was broken by the same
    @ByteArray bug for as long as it was documented."""
    game, inst, mods = portable_mo2
    ini = inst / "MO2" / "ModOrganizer.ini"
    moved = inst / "Elsewhere" / "ModOrganizer.ini"
    moved.parent.mkdir(parents=True, exist_ok=True)
    moved.write_text(ini.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    ini.unlink()  # only reachable via the env override now
    r = run_setup(game, env_overrides={"MO2_INSTANCE_INI": str(moved).replace("\\", "/")})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Mod Organizer 2" in key_path(game, "Mod manager"), "escape hatch did not resolve"


# ---------------------------------------------------------------- global MO2

def test_global_mo2_with_plain_values_still_detected(global_mo2):
    """Guards the ini_get change: unwrapping @ByteArray must not regress the
    older plain-value format."""
    game, localapp, mods = global_mo2
    r = run_setup(game, env_overrides={"LOCALAPPDATA": str(localapp).replace("\\", "/")})
    assert r.returncode == 0, r.stdout + r.stderr
    line = key_path(game, "Mod manager")
    assert "Mod Organizer 2" in line, f"global instance with plain values missed: {line!r}"
    text = claude_md(game)
    assert "Default" in text


# ---------------------------------------------------------------- decoy

def test_sibling_instance_for_a_different_game_is_ignored(tmp_path):
    """Guards the widened sibling glob: an MO2 instance sitting beside the game
    but pointing at a *different* gamePath must not be adopted."""
    from conftest import make_game, make_mo2_tree

    game = make_game(tmp_path, name="SomeGame", exe="SkyrimSE.exe")
    make_mo2_tree(tmp_path / "MODS", "Decoy")
    write_mo2_ini(tmp_path / "MO2" / "ModOrganizer.ini",
                  "D:/Totally/Other/Game", "Decoy",
                  str(tmp_path / "MODS").replace("\\", "/"), byte_array=True)
    r = run_setup(game)
    assert r.returncode == 0, r.stdout + r.stderr
    line = key_path(game, "Mod manager")
    assert "stock layout" in line, f"adopted a decoy instance: {line!r}"


# ---------------------------------------------------- the hook paths file

def test_the_paths_file_survives_a_dollar_sign_in_the_install_path(tmp_path):
    """`$` and backtick are legal NTFS filename characters, and protect-bash.sh
    SOURCES this file on every Bash tool call. Written double-quoted, MEASURED: a real
    folder `C:/Games/My$Stuff/Skyrim VR` reached the guard as `C:/Games/My/Skyrim VR`
    -- matching nothing, so the install was silently UNGUARDED -- and a backtick in the
    path EXECUTED on every call. setup.sh's own validation could not catch either,
    because sourcing cleanly proves the file PARSES, not that it still says what was
    written.
    """
    from conftest import make_game
    game = make_game(tmp_path, name="My$Game")
    r = run_setup(game)
    assert r.returncode == 0, r.stdout + r.stderr
    env = (game / ".claude" / "skyrim-paths.env").read_text(encoding="utf-8")
    root_line = next(l for l in env.splitlines() if l.startswith("SKYRIM_GAME_ROOT="))
    assert "My$Game" in root_line, (
        f"the dollar sign was lost on the way to disk: {root_line!r}")

    # ...and it must still be there after the hook SOURCES it, which is the step that
    # actually mangled it.
    import subprocess as _sp
    from conftest import BASH as _BASH
    # SINGLE quotes around the path being sourced: this test's own install directory
    # contains a `$`, so a double-quoted path would have `$Game` expanded away by the
    # test's shell before `.` ever ran -- the test reproducing, on itself, the exact
    # defect it exists to catch.
    env_path = (game / ".claude" / "skyrim-paths.env").as_posix()
    got = _sp.run(
        [_BASH, "-c", ". '" + env_path + "'; printf '%s' \"$SKYRIM_GAME_ROOT\""],
        capture_output=True, text=True, timeout=60).stdout
    assert "My$Game" in got, f"sourcing mangled the path: {got!r}"


def test_setup_writes_a_usable_paths_file_for_the_hooks(game_dir):
    """`protect-bash.sh` guards a RESOLVED install root, not any path containing
    the word Skyrim -- which used to match the session scratchpad, this repo's own
    checkout and a downloaded zip. It derives one root from its own location and
    reads the rest from `.claude/skyrim-paths.env`.

    Asserting the FILE, not the log line: setup.sh printing "Wrote" proves nothing,
    and this suite exists because a CI job once ran setup.sh and then only checked
    that settings.json parsed.
    """
    import subprocess
    from conftest import BASH

    r = run_setup(game_dir)
    assert r.returncode == 0, r.stdout + r.stderr

    env = game_dir / ".claude" / "skyrim-paths.env"
    assert env.exists(), "setup.sh did not record the resolved paths for the hooks"
    body = env.read_text(encoding="utf-8")

    # It must SOURCE. A config bash cannot read leaves the hooks with only their
    # self-derived root -- the config directory would silently stop being guarded.
    sourced = subprocess.run([BASH, "-c", f'set -a; . "{env.as_posix()}"'],
                             capture_output=True, text=True, timeout=60)
    assert sourced.returncode == 0, f"paths file does not source: {sourced.stderr[:300]}"

    # And it must name THIS install, not a placeholder left unsubstituted.
    root = game_root_win(game_dir)
    # Single-quoted since v3.9: the file is SOURCED, and `$`/backtick are legal in a
    # Windows folder name. Assert the VALUE reaches the file, not one spelling of it.
    assert f"SKYRIM_GAME_ROOT='{root}'" in body, f"wrong or missing game root in:\n{body}"
    for key in ("SKYRIM_CONFIG_DIR", "SKYRIM_LOADORDER_DIR"):
        assert key in body, f"{key} missing from the paths file"
    assert "{{" not in body, f"an unsubstituted placeholder reached the paths file:\n{body}"


# ---------------------------------------------------------------- which Python the hooks get

# The Store alias starts ~135 ms slower than a regular install (MEASURED 2026-10-02), on every hook call.
STORE = "C:" + "\\".join(["", "Users", "testuser", "AppData", "Local", "Microsoft", "WindowsApps",
                         "PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0", "python.exe"])
REGULAR = "C:" + "\\".join(["", "Users", "testuser", "AppData", "Local", "Programs", "Python", "Python310",
                           "python.exe"])
OTHER = "C:/Python313/python.exe"


def _fwd(p):
    return p.replace(chr(92), "/")


@pytest.mark.parametrize("py,python,python3,expected,why", [
    (STORE, REGULAR, STORE, REGULAR, "`py -3` resolves to the Store alias; a regular `python` exists"),
    ("NONE", STORE, REGULAR, REGULAR, "no launcher; the regular install is only `python3`"),
    (REGULAR, OTHER, STORE, REGULAR, "first regular candidate wins when there are several"),
    (STORE, STORE, "NONE", STORE, "only the Store alias exists: it is still used, not refused"),
])
def test_hooks_prefer_a_regular_python_over_the_store_alias(game_dir, py, python, python3, expected, why):
    r = run_setup(game_dir, shim_path=True, env_overrides={
        "FAKE_PYTHON_EXE_py": py, "FAKE_PYTHON_EXE_python": python, "FAKE_PYTHON_EXE_python3": python3})
    assert r.returncode == 0, r.stdout + r.stderr
    lines = hook_python_lines(game_dir)
    assert len(lines) == 5, sorted(lines)
    for hook, line in lines.items():
        assert line == f"PY='{_fwd(expected)}'", f"{why}: {hook}: {line}"


# ---------------------------------------------------------------- Codex (Plan 4 Task 11)

import json
import shutil
import subprocess
import sys

from conftest import BASH, REPO, make_game

WIN = sys.platform == "win32"
windows_only = pytest.mark.skipif(not WIN, reason="the toolkit's Codex hooks are rendered on Windows only")
BS = chr(92)
ENTRY_PS1 = (".codex", "hooks", "skyguard-entry.ps1")
ENTRY_SH = (".codex", "hooks", "skyguard-entry.sh")
RENDERER = REPO / "tools" / "skyguard" / "codex_definitions.py"
TEMPLATE = REPO / "agent" / "targets" / "codex" / "hooks.json"
# A quote in the interpreter's folder: PowerShell must get it as '' and bash as '\''.
QUOTED_PY = "C:" + BS.join(["", "Users", "o'neil", "Python312", "python.exe"])


def _posix_root(game):
    return subprocess.run([BASH, "-c", "pwd"], cwd=str(game), capture_output=True, text=True,
                          check=True).stdout.strip()


def _ps_exe():
    return shutil.which("pwsh") or shutil.which("powershell")


def _ps_string_value(path, var):
    """The value PowerShell's own parser gives `$<var> = '...'` in the file (parsed, never executed)."""
    ps = _ps_exe()
    if not ps:
        pytest.skip("no PowerShell to parse the entry with")
    script = ("$t=$null;$e=$null;$a=[System.Management.Automation.Language.Parser]::ParseFile('"
              + str(path).replace("'", "''") + "',[ref]$t,[ref]$e); if ($e.Count) { exit 3 }; "
              "$n=$a.FindAll({param($x) $x -is [System.Management.Automation.Language.AssignmentStatementAst]"
              " -and $x.Left.VariablePath.UserPath -eq '" + var + "'}, $true)[0]; "
              "[Console]::Out.Write($n.Right.Expression.Value)")
    r = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"PowerShell cannot parse {path}: {r.stdout}{r.stderr}"
    return r.stdout


def _sh_value(path, var):
    """What bash makes of the file's `<var>='...'` line."""
    line = next(l for l in path.read_text(encoding="utf-8").splitlines() if l.startswith(var + "="))
    return subprocess.run([BASH, "-c", line + '\nprintf "%s" "$' + var + '"'],
                          capture_output=True, text=True, timeout=60).stdout


def test_setup_configures_the_codex_entry_scripts(game_dir):
    r = run_setup(game_dir, shim_path=True, env_overrides={"FAKE_PYTHON_EXE": QUOTED_PY})
    assert r.returncode == 0, r.stdout + r.stderr
    want = _fwd(QUOTED_PY)
    ps1, sh = game_dir.joinpath(*ENTRY_PS1), game_dir.joinpath(*ENTRY_SH)
    for f in (ps1, sh):
        text = f.read_text(encoding="utf-8")
        assert "{{PYTHON_PATH}}" not in text and "{{BASH_PATH}}" not in text, f"placeholder left in {f.name}"
    assert _sh_value(sh, "PY") == want
    bash_sh = _sh_value(sh, "BASH_EXE")
    assert bash_sh and not bash_sh.startswith("{{")
    if WIN:
        assert ":/" in bash_sh, f"the PowerShell side cannot use an MSYS bash path: {bash_sh!r}"
    assert _ps_string_value(ps1, "PY") == want, "PowerShell reads a different interpreter path"
    assert _ps_string_value(ps1, "BASHEXE") == bash_sh


def test_rerunning_setup_repairs_the_codex_entries_interpreter(game_dir):
    """Python moved after setup: a re-run must rewrite the entries' $PY / PY lines, as it does the Claude
    hooks', or the Codex hooks deny everything while setup says "Already configured"."""
    assert run_setup(game_dir, shim_path=True, env_overrides={"FAKE_PYTHON_EXE": OTHER}).returncode == 0
    r = run_setup(game_dir, shim_path=True, env_overrides={"FAKE_PYTHON_EXE": QUOTED_PY})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Updated interpreter: .codex/hooks/skyguard-entry.ps1" in r.stdout, r.stdout
    assert _sh_value(game_dir.joinpath(*ENTRY_SH), "PY") == _fwd(QUOTED_PY)
    assert _ps_string_value(game_dir.joinpath(*ENTRY_PS1), "PY") == _fwd(QUOTED_PY)


@windows_only
def test_setup_fills_the_codex_definitions_with_the_install_root(game_dir):
    r = run_setup(game_dir)
    assert r.returncode == 0, r.stdout + r.stderr
    raw = game_dir.joinpath(".codex", "hooks.json").read_text(encoding="utf-8")
    assert "{{" not in raw
    root, posix = game_root_win(game_dir), _posix_root(game_dir)
    handlers = [h for groups in json.loads(raw)["hooks"].values() for g in groups for h in g["hooks"]]
    assert len(handlers) == 2
    for h in handlers:
        assert f'"{root}/.codex/hooks/skyguard-entry.ps1"' in h["commandWindows"], h["commandWindows"]
        assert f'"{posix}/.codex/hooks/skyguard-entry.sh"' in h["command"], h["command"]
    # Byte for byte what the frozen renderer gives for this root: a re-implementation that differs by a
    # slash or a space is a different hash in Codex, and every approval silently stops applying.
    out = game_dir.parent / "direct.json"
    d = subprocess.run([sys.executable, str(RENDERER), "render", "--template", str(TEMPLATE),
                        "--root-win", root, "--root-posix", posix, "--out", str(out)],
                       capture_output=True, text=True, timeout=60)
    assert d.returncode == 0, d.stderr
    assert game_dir.joinpath(".codex", "hooks.json").read_bytes() == out.read_bytes()


@windows_only
def test_setup_renders_definitions_deterministically(tmp_path):
    game = make_game(tmp_path, name="Skyrim VR")
    assert run_setup(game).returncode == 0
    first = game.joinpath(".codex", "hooks.json").read_bytes()
    again = run_setup(game)
    assert "Already configured: .codex/hooks.json" in again.stdout, again.stdout
    assert game.joinpath(".codex", "hooks.json").read_bytes() == first
    # A second install at the SAME path from a different shell environment (the PATH shims first).
    shutil.rmtree(game)
    game = make_game(tmp_path, name="Skyrim VR")
    assert run_setup(game, shim_path=True).returncode == 0
    assert game.joinpath(".codex", "hooks.json").read_bytes() == first


def test_setup_warns_when_codex_definitions_point_elsewhere(game_dir):
    defs = game_dir / ".codex" / "hooks.json"
    elsewhere = TEMPLATE.read_text(encoding="utf-8").replace(
        "{{CODEX_ROOT_POSIX}}", "/d/Elsewhere/Skyrim VR").replace("{{CODEX_ROOT}}", "D:/Elsewhere/Skyrim VR")
    defs.write_bytes(elsewhere.encode("utf-8"))
    r = run_setup(game_dir)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "WARNING: .codex/hooks.json points at D:/Elsewhere/Skyrim VR" in r.stdout, r.stdout
    assert "Re-extract the toolkit zip" in r.stdout and "/hooks" in r.stdout
    assert defs.read_bytes() == elsewhere.encode("utf-8"), "setup rewrote definitions it was told not to"


@pytest.mark.parametrize("ch", [
    "$", "%", "!", "`",
    pytest.param('"', marks=pytest.mark.skipif(WIN, reason="not a legal Windows file name character")),
    pytest.param(BS, marks=pytest.mark.skipif(WIN, reason="a separator on Windows")),
], ids=["dollar", "percent", "bang", "backtick", "quote", "backslash"])
def test_setup_refuses_a_root_it_cannot_put_in_json(tmp_path, ch):
    """cmd.exe expands %X% / !X! and bash $X / `X` inside the hook's quoted path; a hook that cannot start
    lets the command RUN. The Codex side stays unconfigured, loudly; Claude's side still configures."""
    game = make_game(tmp_path, name="Sky" + ch + "rim")
    r = run_setup(game)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "{{CODEX_ROOT}}" in game.joinpath(".codex", "hooks.json").read_text(encoding="utf-8")
    assert not game.joinpath(".codex", "hooks.json.new").exists()
    assert "NOT CONFIGURED" in r.stdout, r.stdout
    if WIN:
        assert "CODEX HOOKS NOT CONFIGURED" in r.stdout and "the install root contains" in r.stdout, r.stdout
    assert "Codex users: Codex runs these hooks only after you approve them" not in r.stdout
    assert "{{GAME_ROOT}}" not in claude_md(game), "the Claude side must still configure"


def _key_lines(text):
    return [l for l in text.splitlines() if l.startswith(
        ("- **Game root", "- **User INI configs", "- **Load order", "- **Mod manager", "- **MO2"))]


@pytest.mark.parametrize("layout", ["stock", "mo2"])
def test_agents_md_gets_the_same_paths_as_claude_md(request, layout):
    game = (request.getfixturevalue("game_dir") if layout == "stock"
            else request.getfixturevalue("portable_mo2")[0])
    r = run_setup(game)
    assert r.returncode == 0, r.stdout + r.stderr
    agents = (game / "AGENTS.md").read_text(encoding="utf-8")
    assert "{{" not in agents, [l for l in agents.splitlines() if "{{" in l]
    want = _key_lines(claude_md(game))
    assert len(want) >= 4, want
    assert _key_lines(agents) == want


@windows_only
def test_setup_prints_the_codex_approval_step(game_dir):
    r = run_setup(game_dir)
    assert r.returncode == 0, r.stdout + r.stderr
    for part in ("Codex users: Codex runs these hooks only after you approve them.",
                 "/hooks, and approve the 2 Skyrim toolkit hooks. Until then NOTHING is checked",
                 "Check any time with: bash tools/hook-canary.sh"):
        assert part in r.stdout, part


SUBFOLDER_LINE = ("Start Codex in the game folder itself, never a subfolder: from a subfolder no hook runs, "
                  "and Codex does not tell you.")


def test_setup_tells_the_user_to_start_codex_in_the_game_folder(game_dir):
    r = run_setup(game_dir)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Start Codex in the game folder itself, never a subfolder" in r.stdout, r.stdout
    assert "from a subfolder no hook runs, and Codex does not tell you." in r.stdout


@pytest.mark.parametrize("rel", ["README.md", "CHANGELOG.md", "agent/instructions/codex.md"])
def test_the_codex_subfolder_warning_is_in_the_docs(rel):
    text = " ".join((REPO / rel).read_text(encoding="utf-8").split())
    assert SUBFOLDER_LINE in text, rel


@windows_only
def test_a_rerun_from_a_differently_cased_folder_is_not_a_moved_install(tmp_path):
    """Windows paths are case-insensitive: re-running setup from a cwd typed in another case must not warn
    that the install moved. The comparison is case-folded; the rendered path keeps its case."""
    game = make_game(tmp_path, name="Skyrim VR")
    assert run_setup(game).returncode == 0
    first = game.joinpath(".codex", "hooks.json").read_bytes()
    r = run_setup(type(game)(str(game).swapcase()))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "points at" not in r.stdout, r.stdout
    assert "Already configured: .codex/hooks.json" in r.stdout, r.stdout
    assert game.joinpath(".codex", "hooks.json").read_bytes() == first
