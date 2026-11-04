"""`skyguard save-lock` (tools/skyguard/savelockcli.py): the user's switch for the optional OS save locks.

The Windows calls of modding_harness.save_lock are replaced with recorders: no real ACL is changed (a locked temp
file could not be cleaned up without an elevated prompt). Fake saves only, in tmp_path."""
import io
import json
import os
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import savelockcli, save_sentinel  # noqa: E402
from modding_harness import save_lock  # noqa: E402


@pytest.fixture
def kit(tmp_path, monkeypatch):
    project = tmp_path / "game"
    project.mkdir()
    calls = []
    state = {"elevated": False}
    monkeypatch.setattr(save_lock, "supported", lambda: True)
    monkeypatch.setattr(save_lock, "is_elevated", lambda: state["elevated"])
    monkeypatch.setattr(save_lock, "user_sid", lambda: "S-1-5-21-1")
    monkeypatch.setattr(save_lock, "status_map", lambda folder, exts: {})
    monkeypatch.setattr(save_lock, "is_locked", lambda p: False)
    monkeypatch.setattr(save_lock, "lock_file", lambda p, sid: calls.append(("lock", os.path.basename(p))))
    monkeypatch.setattr(save_lock, "lock_folder", lambda p, sid: calls.append(("folder", str(p))))
    monkeypatch.setattr(save_lock, "unlock", lambda p, sid: calls.append(("unlock", os.path.basename(p))))
    monkeypatch.setattr(savelockcli, "folders", lambda project, env=None: [str(tmp_path / "Saves")])
    (tmp_path / "Saves").mkdir()
    return project, calls, state


def run(project, *args):
    out = io.StringIO()
    rc = savelockcli.main(list(args) + ["--project", str(project)], out)
    return rc, out.getvalue()


def test_on_writes_and_locks_the_switch_and_locks_the_folders(kit):
    project, calls, _ = kit
    rc, text = run(project, "on")
    assert rc == 0 and "ON" in text
    assert json.loads(save_sentinel.lock_switch(project).read_text(encoding="utf-8")) == {"enabled": True}
    # The switch comes last: it needs an elevated prompt to undo, so a folder that fails must leave it unset.
    assert calls[0][0] == "folder" and calls[-1] == ("lock", "save-lock.json")


def test_on_is_refused_from_an_elevated_prompt(kit):
    project, calls, state = kit
    state["elevated"] = True
    rc, text = run(project, "on")
    assert rc == 2 and "elevated" in text and not save_sentinel.lock_switch(project).exists() and calls == []


def test_off_needs_an_elevated_prompt(kit):
    project, calls, state = kit
    run(project, "on")
    rc, text = run(project, "off")
    assert rc == 2 and "elevated" in text
    assert json.loads(save_sentinel.lock_switch(project).read_text(encoding="utf-8"))["enabled"] is True
    state["elevated"] = True
    rc, text = run(project, "off")
    assert rc == 0 and json.loads(save_sentinel.lock_switch(project).read_text(encoding="utf-8"))["enabled"] is False
    assert "lock(s) lifted" in text


def test_off_lifts_the_locks_in_every_watched_folder(kit, monkeypatch, tmp_path):
    """The locks outlast the toolkit, so `off` hands the core every watched folder to clear."""
    project, calls, state = kit
    state["elevated"] = True
    seen = []
    monkeypatch.setattr(save_lock, "turn_off", lambda switch, folders, exts: seen.append((list(folders), exts)) or 3)
    rc, text = run(project, "off")
    assert rc == 0 and seen == [([str(tmp_path / "Saves")], (".ess", ".skse"))] and "3 lock(s) lifted" in text


def test_unlocking_a_save_backs_up_the_pair_first_and_nothing_when_that_fails(kit, monkeypatch, tmp_path):
    project, calls, _ = kit
    ess = tmp_path / "Saves" / "Save1.ess"
    ess.write_bytes(b"E")
    ess.with_suffix(".skse").write_bytes(b"S")
    from skyguard import save_store
    monkeypatch.setattr(save_store, "backup_pairs", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    rc, text = run(project, "unlock", str(ess))
    assert rc == 2 and "disk full" in text and calls == []
    seen = []
    monkeypatch.setattr(save_store, "backup_pairs", lambda project, audit, pairs, command, agent="": (
        seen.extend(pairs) or ["save:abc"]))
    rc, text = run(project, "unlock", str(ess))
    assert rc == 0 and seen == [(str(ess), str(ess.with_suffix(".skse")))]
    assert sorted(c[1] for c in calls) == ["Save1.ess", "Save1.skse"]


@pytest.mark.parametrize("args", [["unlock"], ["on", "extra"], ["bogus"]])
def test_usage_errors_change_nothing(kit, args):
    project, calls, _ = kit
    rc, _ = run(project, *args)
    assert rc == 2 and calls == [] and not save_sentinel.lock_switch(project).exists()


def test_off_windows_it_says_unsupported(kit, monkeypatch):
    project, calls, _ = kit
    monkeypatch.setattr(save_lock, "supported", lambda: False)
    rc, text = run(project, "on")
    assert rc == 3 and "unsupported" in text and calls == []
