"""Review round 3: crash and log tools read the wrong Documents when it is redirected. The guard already knew better."""
import importlib
import sys

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
skyrim_paths = importlib.import_module("skyrim_paths")


def test_the_recorded_documents_folder_wins(tmp_path, monkeypatch):
    game = tmp_path / "OneDrive" / "Dokumente" / "My Games" / "Skyrim VR"
    game.mkdir(parents=True)
    monkeypatch.setenv("SKYRIM_DOCUMENTS_DIR", game.as_posix())
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))
    assert skyrim_paths.my_games() == game.parent


def test_without_a_record_a_test_profile_stays_isolated(tmp_path, monkeypatch):
    """A fake USERPROFILE is never overridden by the real machine's known folder."""
    monkeypatch.delenv("SKYRIM_DOCUMENTS_DIR", raising=False)
    monkeypatch.setattr(skyrim_paths, "_recorded_documents", lambda: "")
    monkeypatch.setattr(skyrim_paths, "_windows_folder", lambda csidl: str(tmp_path / ("real" if csidl == 0x28 else "redirected")))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))
    assert skyrim_paths.my_games() == tmp_path / "profile" / "Documents" / "My Games"


def test_a_redirected_documents_folder_is_what_windows_reports(tmp_path, monkeypatch):
    monkeypatch.delenv("SKYRIM_DOCUMENTS_DIR", raising=False)
    monkeypatch.setattr(skyrim_paths, "_recorded_documents", lambda: "")
    monkeypatch.setattr(skyrim_paths, "_windows_folder", lambda csidl: str(tmp_path / ("real" if csidl == 0x28 else "OneDrive")))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "real"))
    assert skyrim_paths.my_games() == tmp_path / "OneDrive" / "My Games"
