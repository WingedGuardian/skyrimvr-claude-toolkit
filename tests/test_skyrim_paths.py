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
