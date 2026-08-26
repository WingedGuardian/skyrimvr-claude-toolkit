"""Behavioral tests for the paths setup.sh writes into CLAUDE.md.

Every test here is anchored to a bug this toolkit actually shipped. The
structural CI added in v3.6 cannot catch any of them: `setup.sh` is valid bash
in every case, and `setup-smoke` only checks that settings.json parses
afterwards -- it never looks at a single path setup.sh wrote.
"""

from __future__ import annotations

from conftest import claude_md, game_root_win, key_path, run_setup, write_mo2_ini


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
