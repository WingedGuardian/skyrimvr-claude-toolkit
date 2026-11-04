"""The save sentinel, save locks and session charter after the Opus review of v4.0.0, each defect reproduced before its fix. Fake
saves only; no real ACL is changed."""
import io
import json
import os
import sys
import time
from pathlib import Path

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from modding_harness import save_lock  # noqa: E402
from skyguard import audit, paths, save_sentinel, savelockcli, session, shell_rules, write_rules  # noqa: E402


@pytest.fixture
def look(tmp_path, monkeypatch):
    """A project and a Saves folder the sentinel watches, with one look already recorded."""
    project, game = tmp_path / "project", tmp_path / "Skyrim VR"
    saves = game / "Saves"
    project.mkdir()
    saves.mkdir(parents=True)
    a = saves / "Save1.ess"
    a.write_bytes(b"one")
    os.utime(a, (time.time() - 500, time.time() - 500))
    monkeypatch.setattr(save_sentinel, "save_folders", lambda roots, env: [saves])
    monkeypatch.setattr(save_sentinel, "marked_folders", lambda project: [])
    monkeypatch.setattr(save_sentinel, "game_running", lambda: False)

    def run(now):
        return save_sentinel.report_text(str(project), None, {}, now=now)
    start = time.time() - 300
    run(start)
    return project, game, saves, a, run, start


def _change(path, when):
    path.write_bytes(b"changed " + str(when).encode())
    os.utime(path, (when, when))


# ---- 6.3: the record is written after everything that can fail
def test_a_failure_before_the_report_does_not_lose_the_finding(look, monkeypatch):
    project, _game, _saves, a, run, start = look
    _change(a, start + 100)

    def boom(*_a):
        raise RuntimeError("no")
    monkeypatch.setattr(save_sentinel, "games_doing", boom)
    text, loud = run(start + 200)
    assert "this look failed (RuntimeError)" in text and loud
    monkeypatch.undo()
    monkeypatch.setattr(save_sentinel, "save_folders", lambda roots, env: [a.parent])
    monkeypatch.setattr(save_sentinel, "marked_folders", lambda project: [])
    monkeypatch.setattr(save_sentinel, "game_running", lambda: False)
    text, loud = run(start + 250)
    assert "Save1.ess" in text and loud


# ---- 6.6: the game's own saves after a play session are not an alarm
def test_a_save_changed_during_a_session_that_ended_is_not_raised(look):
    _project, game, _saves, a, run, start = look
    log = game / "SKSE" / "skse.log"
    log.parent.mkdir()
    log.write_text("x", encoding="utf-8")
    _change(a, start + 100)
    os.utime(log, (start + 150, start + 150))
    text, loud = run(start + 200)
    assert "may be the game itself saving" in text and "Save1.ess" in text and not loud


def test_a_change_with_no_session_since_the_last_look_is_raised(look):
    _project, _game, _saves, a, run, start = look
    _change(a, start + 100)
    text, loud = run(start + 200)
    assert "did not back these up" in text and loud


def test_a_change_after_the_session_ended_is_raised(look):
    _project, game, _saves, a, run, start = look
    log = game / "Logs" / "Script" / "Papyrus.0.log"
    log.parent.mkdir(parents=True)
    log.write_text("x", encoding="utf-8")
    os.utime(log, (start + 50, start + 50))
    _change(a, start + 100)
    text, loud = run(start + 200)
    assert "did not back these up" in text and loud


def test_a_bulk_change_is_never_a_play_session(look):
    _project, game, saves, _a, run, start = look
    files = [saves / "S{}.ess".format(i) for i in range(45)]
    for f in files:
        _change(f, start - 100)
    run(start + 1)
    log = game / "SKSE" / "skse.log"
    log.parent.mkdir()
    log.write_text("x", encoding="utf-8")
    for f in files:
        _change(f, start + 100)
    os.utime(log, (start + 150, start + 150))
    assert run(start + 200)[1]


# ---- 6.7: a look that fails says so, once an hour
def test_a_failing_look_is_reported_once_an_hour(look, monkeypatch):
    _project, _game, _saves, _a, run, start = look
    now = time.time()

    def boom(*_a):
        raise RuntimeError("no")
    monkeypatch.setattr(save_sentinel, "save_folders", boom)
    text, loud = run(now)
    assert "NOT being reported" in text and loud
    assert run(now + 5) == ("", False)


# ---- 6.18: a standing note is said once
def test_an_unread_folder_is_noted_once(look, monkeypatch):
    project, _game, saves, _a, run, start = look
    real = save_sentinel.sentinel.check

    def unread(profile, store, state, watch=(), now=None, covered=None):
        return real(profile, store, state, watch, now, covered)._replace(unread=(str(saves),))
    monkeypatch.setattr(save_sentinel.sentinel, "check", unread)
    assert str(saves) in run(start + 10)[0]
    assert str(saves) not in run(start + 20)[0]


# ---- 6.8: what the user just unlocked stays unlocked for a repair
def test_a_save_the_user_unlocked_is_not_locked_again_during_the_hour(tmp_path, monkeypatch):
    locked = []
    monkeypatch.setattr(save_lock, "supported", lambda: True)
    monkeypatch.setattr(save_lock, "user_sid", lambda: "S-1")
    monkeypatch.setattr(save_lock, "status_map", lambda folder, exts: {})
    monkeypatch.setattr(save_lock, "is_locked", lambda p: True)
    monkeypatch.setattr(save_lock, "lock_file", lambda p, sid: locked.append(os.path.basename(p)))
    save = tmp_path / "Saves" / "a.ess"
    other = tmp_path / "Saves" / "b.ess"
    save.parent.mkdir()
    save.write_bytes(b"x")
    other.write_bytes(b"x")
    old = time.time() - 9999
    files = {"a": [1, 1, str(save)], "b": [1, 1, str(other)]}
    save_lock.auto_lock({}, {}, files, [str(save.parent)], now=old + 10 ** 6, exempt=(str(save),), extensions=(".ess",))
    assert locked == ["b.ess"]


def test_an_unlock_lasts_an_hour_then_lapses(tmp_path):
    save_sentinel.record_unlocked(tmp_path, ["C:/x/a.ess"], now=1000.0)
    path = save_sentinel.exempt_path(tmp_path)
    assert list(save_sentinel._exempt_rows(path, 1000.0 + save_sentinel.UNLOCK_LASTS - 1)) == [os.path.abspath("C:/x/a.ess")]
    assert not save_sentinel._exempt_rows(path, 1000.0 + save_sentinel.UNLOCK_LASTS + 1)


# ---- 6.1: the option the refusal names exists
def test_off_forget_missing_reaches_the_core(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(save_lock, "supported", lambda: True)
    monkeypatch.setattr(save_lock, "turn_off", lambda switch, folders, exts, **kw: seen.update(kw) or 0)
    monkeypatch.setattr(savelockcli, "folders", lambda project, env=None: [])
    out = io.StringIO()
    assert savelockcli.main(["off", "--forget-missing", "--project", str(tmp_path)], out) == 0
    assert seen == {"forget_missing": True}


def test_a_malformed_paths_file_is_a_message_not_a_traceback(tmp_path, monkeypatch):
    monkeypatch.setattr(save_lock, "supported", lambda: True)
    monkeypatch.setattr(save_lock, "is_elevated", lambda: False)

    def bad(project, env=None):
        raise ValueError("skyrim-paths.env: not UTF-8")
    monkeypatch.setattr(savelockcli, "folders", bad)
    out = io.StringIO()
    assert savelockcli.main(["status", "--project", str(tmp_path)], out) == 2 and "not UTF-8" in out.getvalue()


# ---- 6.5, 6.9, 6.10, 6.15, 7.9: the guard
def _judge(tmp_path, command, shell="bash"):
    roots = paths.roots(str(tmp_path), "", {"SKYRIM_GAME_ROOT": str(tmp_path)})
    a = audit.Audit("")
    a.raw = lambda line: None
    v = shell_rules.decide(command, roots, a, cwd=str(tmp_path), shell=shell, env=os.environ)
    return v.decision, {rid for rid, _ in a.fired}


@pytest.mark.parametrize("command", ["bash tools/skyguard.sh save-lock off", "bash tools/skyguard.sh save-lock on",
                                     "bash tools/skyguard.sh save-lock unlock x.ess", "python -m skyguard save-lock off",
                                     "python tools/skyguard save-lock off --forget-missing"])
def test_an_agent_cannot_change_save_locking_through_the_cli(tmp_path, command):
    assert _judge(tmp_path, command) == ("deny", {"S-SAVE-LOCK"})


@pytest.mark.parametrize("command", ["bash tools/skyguard.sh save-lock status", "bash tools/skyguard.sh save-lock off --help",
                                     "bash tools/skyguard.sh snapshot list"])
def test_reading_save_locking_stays_allowed(tmp_path, command):
    assert "S-SAVE-LOCK" not in _judge(tmp_path, command)[1]


@pytest.mark.parametrize("rel", [".claude/backups/.save-sentinel.json", ".modding-agent-harness/save-lock-folders.json",
                                 ".modding-agent-harness/save-lock-exempt.json", "tools/session-charter.sh"])
def test_what_the_sentinel_and_the_locks_rely_on_is_a_guard_file(tmp_path, rel):
    assert "S-SELF-GUARD" in _judge(tmp_path, "echo x > " + rel)[1]


@pytest.mark.parametrize("name", [".modding-agent-harness/save-lock.json::$DATA", ".MODDIN~1/save-lock.json"])
def test_the_write_tool_cannot_reach_the_switch_by_another_spelling(tmp_path, name):
    roots = paths.roots(str(tmp_path), "", {"SKYRIM_GAME_ROOT": str(tmp_path)})
    a = audit.Audit("")
    a.raw = lambda line: None
    v = write_rules.decide(str(tmp_path / name), a, roots=roots, cwd=str(tmp_path), env=os.environ)
    assert v.decision == "deny", v


# ---- 7.6
def test_the_over_budget_pointer_comes_first_so_a_cut_keeps_it(tmp_path):
    text = session.with_charter("P" * 7700, str(tmp_path), "s1")
    assert text.startswith("SESSION CHARTER: not shown") and text.endswith("P")


def test_a_charter_that_fits_follows_the_other_text(tmp_path):
    (tmp_path / ".agent-sessions").mkdir()
    (tmp_path / ".agent-sessions" / "s1.md").write_text("notes\n", encoding="utf-8")
    text = session.with_charter("intro", str(tmp_path), "s1")
    assert text.startswith("intro") and "notes" in text


def test_the_ledger_line_is_labelled_on_every_prompt(tmp_path):
    from modding_harness import ledger
    log = ledger.path_for(tmp_path, "s1")
    log.parent.mkdir(parents=True)
    log.write_text(json.dumps({"op": "add", "id": "L1", "text": "t"}) + "\n", encoding="utf-8")
    assert "not instructions" in ledger.summary(str(tmp_path), "s1", full=False)


# ---- 5.1, 5.2: the health report reads the wiring, not only the heartbeats
def _canary(tmp_path, settings=None):
    from skyguard import health
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    for h in health.CLAUDE_HOOKS:
        (hooks / (h + ".sh")).write_text("x", encoding="utf-8")
    if settings is not None:
        (tmp_path / ".claude" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    out = io.StringIO()
    return health.report(tmp_path, out=out), out.getvalue(), health


def _registered():
    from skyguard import health
    return {"hooks": {"PreToolUse": [{"hooks": [{"command": "bash .claude/hooks/{}.sh".format(h)} for h in health.CLAUDE_HOOKS]}]}}


def test_hooks_switched_off_are_not_ok(tmp_path):
    code, text, _ = _canary(tmp_path, dict(_registered(), disableAllHooks=True))
    assert code == 1 and "disableAllHooks" in text and "RESULT: OK" not in text


def test_a_removed_hooks_block_is_not_ok(tmp_path):
    code, text, _ = _canary(tmp_path, {})
    assert code == 1 and "does not register it" in text


def test_a_registered_hook_whose_script_is_gone_is_not_ok(tmp_path):
    code, text, health = _canary(tmp_path, _registered())
    assert "script is gone" not in text
    hook = health.CLAUDE_HOOKS[0]
    (tmp_path / ".claude" / "hooks" / (hook + ".sh")).unlink()
    out = io.StringIO()
    assert health.report(tmp_path, out=out) == 1 and "script is gone" in out.getvalue() and hook in out.getvalue()


# ---- 5.3
@pytest.mark.skipif(os.name != "nt", reason="the lock is Windows only")
def test_unlock_on_an_install_that_never_locked_does_not_switch_the_lock_on(tmp_path):
    from skyguard import lock
    (tmp_path / "Data").mkdir()
    (tmp_path / "SkyrimVR.exe").write_bytes(b"")
    (tmp_path / "Data" / "Skyrim.esm").write_bytes(b"x")
    lock.unlock(str(tmp_path), None, "abc")
    assert not (tmp_path / ".skyguard" / "lock.json").exists()


# ---- 5.4
def test_a_failed_restore_says_why_and_exits_non_zero(tmp_path, monkeypatch, capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("agent_harness_opus", REPO / "tools" / "agent-harness.py")
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)

    def boom(*_a, **_k):
        raise OSError("restore failed; published=[Save1.ess]; journal=j.json")
    monkeypatch.setattr(harness.save_store, "restore_result", boom)
    (tmp_path / "skyrim-paths.env").write_text("SKYRIM_GAME_ROOT='{}'\n".format(tmp_path.as_posix()), encoding="utf-8")
    code = harness.main(["save-restore", "abc", "--in-place"])
    assert code == 1 and "published=[Save1.ess]" in capsys.readouterr().out


# ---- 5.5
def test_a_very_long_source_path_still_gets_a_backup(tmp_path):
    from skyguard import backup
    a = audit.Audit("")
    a.raw = lambda line: None
    deep = "/".join(["x" * 40] * 5)
    assert len(backup.flat_name(str(tmp_path) + "/" + deep)) > backup.MAX_FLAT_NAME
    src = tmp_path / ("x" * 40)
    src.mkdir()
    f = src / "file.ini"
    f.write_text("x", encoding="utf-8")
    original = backup.flat_name
    backup.flat_name = lambda p: "C_" + "long_" * 60 + "file.ini"
    try:
        kept = backup.take(str(tmp_path), "Edit", str(f), a)
    finally:
        backup.flat_name = original
    assert kept is not None and len(Path(kept).name) < 200 and Path(kept).name.endswith("file.ini")


def test_a_failed_edit_backup_tells_the_agent(tmp_path, monkeypatch):
    from skyguard import backup
    a = audit.Audit("")
    a.raw = lambda line: None
    monkeypatch.setattr(backup, "take", lambda *_a, **_k: None)
    f = tmp_path / "x.ini"
    f.write_text("x", encoding="utf-8")
    assert backup.run(str(tmp_path), "Edit", str(f), False, a) is backup.FAILED


# ---- 5.7, 5.11
def _store(tmp_path, rows):
    from skyguard import recovery
    store = tmp_path / "store"
    store.mkdir()
    (store / recovery.MANIFEST).write_text("\n".join(rows) + "\n", encoding="utf-8")
    return recovery, store


def test_a_corrupt_manifest_line_refuses_a_config_restore(tmp_path):
    def line(snap, src, state):
        from skyguard import recovery as rec
        return json.dumps(rec._line("t", 1, src, "a" * 64, 1, "config", snap, "", "", "", 1, state))
    recovery, store = _store(tmp_path, [line("20260101_000000-config", "p", ["p"]), "{ broken", line("20260103_000000-config", "i", ["i", "p"])])
    with pytest.raises(ValueError, match="unreadable line"):
        recovery.resolve(store, "20260103_000000-config")


# ---- 5.13
def test_a_folder_that_fails_to_list_is_not_deleted_scripts(tmp_path, monkeypatch):
    from skyguard import snapshot
    (tmp_path / "Data" / "Scripts").mkdir(parents=True)

    def denied(path):
        raise PermissionError(path)
    monkeypatch.setattr(snapshot.os, "scandir", denied)
    assert snapshot.scan(str(tmp_path))[2] is True


# ---- 4.2
def test_a_doc_line_naming_the_output_root_protects_nothing_under_it(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("housekeep_opus2", REPO / "tools" / "housekeep.py")
    hk = importlib.util.module_from_spec(spec)
    sys.modules["housekeep_opus2"] = hk
    spec.loader.exec_module(hk)
    out = tmp_path / "out" / "task"
    out.mkdir(parents=True)
    (out / "payload.bin").write_bytes(b"x")
    (out / "linked.txt").write_bytes(b"x")
    (tmp_path / "CLAUDE.md").write_text("see `out/*.log` and out/task/linked.txt\n", encoding="utf-8")
    guard = hk.Guard(dict(hk.DEFAULTS, root=str(tmp_path), agent_output=["out"]))
    assert guard.why_protected(out / "payload.bin") == ""
    assert guard.why_protected(out / "linked.txt") == "linked evidence"


# ---- 4.1: the CHANGELOG cannot claim a pin the lock does not hold
def test_the_changelog_names_the_core_the_lock_pins():
    import re
    lock = json.loads((REPO / "tools" / "modding-harness.lock.json").read_text(encoding="utf-8"))
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    first = text[:text.index("\n## ", text.index("## ") + 3)]
    named = re.findall(r"Shared preservation core pinned to ([0-9a-f]{7,40})", first)
    assert named and all(lock["revision"].startswith(n) for n in named), (named, lock["revision"])


# ---- 4.4, Java: base 10 unless 0x, the same as the native backend
def test_the_java_backend_does_not_read_a_zero_padded_int_as_octal():
    """Source text only: no CI job compiles this file. Base 10 unless the value is spelled 0x."""
    text = (REPO / "tools" / "resaver-cli" / "ResaverCLI.java").read_text(encoding="utf-8")
    assert 'val.matches("[+-]?0[xX].*") ? Integer.decode(val) : Integer.parseInt(val)' in text


# ---- the review of the fixes
def test_an_unlock_is_recorded_in_the_spelling_the_sentinel_compares(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_sentinel.record_unlocked(tmp_path, ["Save1.ess"], now=1000.0)
    assert list(save_sentinel._exempt_rows(save_sentinel.exempt_path(tmp_path), 1000.0)) == [os.path.abspath("Save1.ess")]


def test_a_corrupt_line_after_a_snapshot_does_not_refuse_it(tmp_path):
    from skyguard import recovery as rec
    sid = "20260101_000000-config"
    row = json.dumps(rec._line("t", 1, "p", "a" * 64, 1, "config", sid, "", "", "", 1, ["p"]))
    recovery, store = _store(tmp_path, [row, "{ broken"])
    assert list(recovery.resolve(store, sid)) == ["p"]


@pytest.mark.skipif(os.name == "nt", reason="the POSIX branch")
def test_a_game_name_in_another_programs_arguments_is_not_the_game(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(save_sentinel.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="COMMAND\nvim\nbash\n"))
    assert not save_sentinel.game_running()
    monkeypatch.setattr(save_sentinel.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="COMMAND\nSkyrimSE.exe\n"))
    assert save_sentinel.game_running()


def test_a_windows_process_list_matches_the_whole_image_name(monkeypatch):
    from types import SimpleNamespace
    if os.name != "nt":
        pytest.skip("the Windows branch")
    monkeypatch.setattr(save_sentinel.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout='"notskyrim.exe","1"'))
    assert not save_sentinel.game_running()


def test_the_ledger_folder_ignores_itself_in_git_too(tmp_path):
    from modding_harness import ledger
    path = ledger.path_for(tmp_path, "s1")
    path.parent.mkdir(parents=True)
    ledger._append(path, str(tmp_path), {"op": "add", "id": "L1", "text": "t"})
    assert (path.parent / ".gitignore").read_bytes() == b"*\n"
