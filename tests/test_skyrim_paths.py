"""Path discovery shared by the triage tools.

Both tools shipped with this machine's own Documents path baked into them, the
same defect skyrim-winner.js had. Worse, the folder they need has the same
multiple-candidate hazard as plugins.txt: a machine can carry a "Skyrim VR"
and a "Skyrim Special Edition" folder at once, and this install does. Picking
one silently means triaging the wrong game's logs and reporting a clean bill of
health for a log nobody was asking about.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
import skyrim_paths  # noqa: E402


def make_games(tmp_path: Path, *games: str) -> Path:
    base = tmp_path / "My Games"
    for g in games:
        (base / g / "Logs" / "Script").mkdir(parents=True)
    base.mkdir(parents=True, exist_ok=True)
    return base


def test_single_candidate_wins_with_no_rivals(tmp_path):
    base = make_games(tmp_path, "Skyrim VR")
    chosen, others = skyrim_paths.find_game_dir("Logs/Script", base=base)
    assert chosen == base / "Skyrim VR" / "Logs" / "Script"
    assert others == []


def test_every_rival_candidate_is_reported(tmp_path):
    base = make_games(tmp_path, "Skyrim VR", "Skyrim Special Edition")
    chosen, others = skyrim_paths.find_game_dir("Logs/Script", base=base)
    assert chosen == base / "Skyrim VR" / "Logs" / "Script"
    assert others == [base / "Skyrim Special Edition" / "Logs" / "Script"]


def test_vr_is_preferred_over_special_edition(tmp_path):
    # Order is load-bearing, not cosmetic: this toolkit is VR-first, and the
    # rival is usually the stale leftover.
    base = make_games(tmp_path, "Skyrim Special Edition", "Skyrim VR")
    chosen, _ = skyrim_paths.find_game_dir("Logs/Script", base=base)
    assert "Skyrim VR" in str(chosen)


def test_no_candidate_still_names_a_real_location(tmp_path):
    base = tmp_path / "My Games"
    base.mkdir()
    chosen, others = skyrim_paths.find_game_dir("Logs/Script", base=base)
    assert chosen == base / "Skyrim VR" / "Logs" / "Script"
    assert others == []


def test_env_override_beats_discovery(tmp_path, monkeypatch):
    base = make_games(tmp_path, "Skyrim VR")
    monkeypatch.setenv("PAPYRUS_LOG_DIR", "X:/explicit/logs")
    chosen, others = skyrim_paths.resolve_dir("PAPYRUS_LOG_DIR", "Logs/Script", base=base)
    assert chosen == Path("X:/explicit/logs")
    assert others == []


def test_ambiguity_warns_naming_winner_loser_and_the_override(tmp_path, capsys):
    base = make_games(tmp_path, "Skyrim VR", "Skyrim Special Edition")
    skyrim_paths.resolve_dir("PAPYRUS_LOG_DIR", "Logs/Script", base=base)
    err = capsys.readouterr().err
    assert "Skyrim VR" in err, "the warning must name the folder actually used"
    assert "Skyrim Special Edition" in err, "the warning must name the ignored folder"
    assert "PAPYRUS_LOG_DIR" in err, "the warning must say how to override the choice"


def test_an_unambiguous_choice_stays_silent(tmp_path, capsys):
    # A warning printed on every ordinary run is a warning nobody reads.
    base = make_games(tmp_path, "Skyrim VR")
    skyrim_paths.resolve_dir("PAPYRUS_LOG_DIR", "Logs/Script", base=base)
    assert capsys.readouterr().err == ""


# --------------------------------------------------------------------------
# game_root(): the guard's tool-lookup precedence, reusing its paths-file reader
# --------------------------------------------------------------------------

def toolkit_with_env(tmp_path, text=None, legacy=None):
    tk = tmp_path / "toolkit"
    (tk / ".claude").mkdir(parents=True)
    if text is not None:
        (tk / "skyrim-paths.env").write_text(text, encoding="utf-8")
    if legacy is not None:
        (tk / ".claude" / "skyrim-paths.env").write_text(legacy, encoding="utf-8")
    return tk


def test_game_root_defaults_to_the_toolkit_folder(tmp_path):
    tk = toolkit_with_env(tmp_path)
    assert skyrim_paths.game_root({}, toolkit=tk) == (tk, "the toolkit folder")


def test_game_root_reads_the_paths_file(tmp_path):
    tk = toolkit_with_env(tmp_path, "SKYRIM_GAME_ROOT='D:/Games/Skyrim Special Edition'\n")
    assert skyrim_paths.game_root({}, toolkit=tk) == (Path("D:/Games/Skyrim Special Edition"), "skyrim-paths.env")


def test_game_root_reads_the_legacy_paths_file_when_the_root_one_is_absent(tmp_path):
    tk = toolkit_with_env(tmp_path, legacy="SKYRIM_GAME_ROOT='E:/SkyrimVR'\n")
    assert skyrim_paths.game_root({}, toolkit=tk) == (Path("E:/SkyrimVR"), ".claude/skyrim-paths.env")


def test_game_root_environment_beats_the_paths_file(tmp_path):
    tk = toolkit_with_env(tmp_path, "SKYRIM_GAME_ROOT='D:/Games/SSE'\n")
    got = skyrim_paths.game_root({"SKYRIM_GAME_ROOT": "F:/Elsewhere"}, toolkit=tk)
    assert got == (Path("F:/Elsewhere"), "SKYRIM_GAME_ROOT")


def test_game_root_refuses_a_malformed_value_instead_of_falling_back(tmp_path):
    """A value the guard's reader refuses ($HOME expansion) must not quietly become 'the toolkit folder'."""
    tk = toolkit_with_env(tmp_path, "SKYRIM_GAME_ROOT=$HOME/Skyrim\n")
    with pytest.raises(ValueError, match="SKYRIM_GAME_ROOT"):
        skyrim_paths.game_root({}, toolkit=tk)


# --------------------------------------------------------------------------
# find_7zip(): SEVENZIP, then PATH (7z, 7zz, 7za), then Program Files
# --------------------------------------------------------------------------

def fake_exe(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / (name + (".exe" if sys.platform == "win32" else ""))
    p.write_text("", encoding="utf-8")
    p.chmod(0o755)
    return p


def test_sevenzip_override_wins(tmp_path):
    exe = fake_exe(tmp_path / "custom", "my7z")
    onpath = fake_exe(tmp_path / "bin", "7z")
    env = {"SEVENZIP": str(exe), "PATH": str(onpath.parent), "PATHEXT": ".EXE"}
    assert skyrim_paths.find_7zip(env) == (str(exe), "SEVENZIP")


def test_a_wrong_sevenzip_override_is_reported_not_replaced(tmp_path):
    """The user named one 7-Zip; quietly using another would test a binary nobody chose."""
    onpath = fake_exe(tmp_path / "bin", "7z")
    path, why = skyrim_paths.find_7zip({"SEVENZIP": str(tmp_path / "nope.exe"), "PATH": str(onpath.parent),
                                        "PATHEXT": ".EXE"})
    assert path is None and "SEVENZIP" in why and "nope.exe" in why


@pytest.mark.parametrize("names,expected", [(("7z", "7zz", "7za"), "7z"), (("7zz", "7za"), "7zz"), (("7za",), "7za")])
def test_path_lookup_takes_the_names_in_order(tmp_path, names, expected):
    for n in names:
        fake_exe(tmp_path / "bin", n)
    path, why = skyrim_paths.find_7zip({"PATH": str(tmp_path / "bin"), "PATHEXT": ".EXE",
                                        "ProgramFiles": str(tmp_path / "none")})
    assert path is not None and Path(path).stem.lower() == expected and why == "PATH ({})".format(expected)


def test_program_files_is_the_last_resort(tmp_path):
    pf = tmp_path / "Program Files"
    exe = fake_exe(pf / "7-Zip", "7z")
    if sys.platform != "win32":                       # the Program Files default is a Windows layout
        exe = exe.rename(exe.with_name("7z.exe"))
    env = {"PATH": str(tmp_path / "empty"), "PATHEXT": ".EXE", "ProgramFiles": str(pf)}
    assert skyrim_paths.find_7zip(env) == (str(exe), "Program Files")


def test_nothing_found_says_what_to_do(tmp_path):
    path, why = skyrim_paths.find_7zip({"PATH": str(tmp_path / "empty"), "PATHEXT": ".EXE",
                                        "ProgramFiles": str(tmp_path / "none")})
    assert path is None
    assert "SEVENZIP" in why and "installing-tools.md" in why and "26.00" in why
