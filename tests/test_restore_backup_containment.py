"""Plan 6 final fix pass (lane B I1): `restore-from-backup.sh --restore` is contained like `snapshot restore --in-place`.

The reviewer's probe: a hashed backup of `.skyguard/state.json` exists (backup-before-edit runs beside the W-SELF-STATE
deny), and `bash scripts/restore-from-backup.sh --restore <that backup>` put back a file whose checksum verifies and
which carried an old `persistent.lock_disabled` -- `allow` at the guard, exit 0 at the restore. Two layers now:

  1. backup._restore refuses, writing nothing, a target in the guards' state or code (`.skyguard/`, `.claude/hooks`,
     `.claude/settings*.json`, `.claude/skyrim-paths.env`, `.codex/`, `tools/skyguard/`, `agent/guards/`, the
     telemetry), GUARD-FILES.sha256, a plugin or archive, or a stream -- read through links (realpath), as the
     manifest line is data a tampered store can hold. And backup-before-edit no longer copies `.skyguard/` at all.
  2. S-RESTORE-INPLACE (ask; hook_only on Codex, so a deny there) covers `restore-from-backup.sh --restore` and the
     python entry (`backup.restore_main` / `backup._restore`), as it covers `snapshot restore --in-place`.
"""
import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from conftest import REPO, scratch_dir, GAME_ROOT

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, backup, codes, paths, rules, shell_rules, state  # noqa: E402
from skyguard.adapters import codex  # noqa: E402

GAME = GAME_ROOT
NOHOME = scratch_dir("p6fixA-b1-")
ENV0 = {"USERPROFILE": NOHOME, "HOME": NOHOME}


def _restore(project, *args):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = backup.restore_main([str(Path(project) / ".claude" / "backups")] + list(args))
    return rc, out.getvalue()


def _take(project, rel, data=b"x"):
    p = Path(project) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    dest = backup.take(str(project), "Edit", str(p), audit.Audit(str(project)))
    assert dest is not None
    return p, dest.name


def test_the_reviewers_state_json_probe_is_refused_at_the_restore(tmp_path):
    project = tmp_path / "inst"
    sf = project / ".skyguard" / "state.json"
    state.write(str(sf), {"v": 1, "lifts": [], "persistent": {"lock_disabled": {"since": 1.0, "by_session": "s1"}}})
    dest = backup.take(str(project), "Edit", str(sf), audit.Audit(str(project)))
    assert dest is not None
    state.write(str(sf), {"v": 1, "lifts": [], "persistent": {}})          # the user ran `lock enable` since
    before = sf.read_bytes()
    rc, text = _restore(project, "--restore", dest.name)
    assert rc == 2 and "REFUSED" in text, text
    assert sf.read_bytes() == before and state.read(str(sf)).lock_disabled is None


GUARDED = [".claude/hooks/protect-bash.sh", ".claude/settings.json", ".claude/settings.local.json",
           ".claude/skyrim-paths.env", ".codex/config.toml", "tools/skyguard/engine.py", "agent/guards/x.py",
           "GUARD-FILES.sha256", "Data/Skyrim.esm", "Data/MyMod.esp", "Data/Foo - Main.bsa", ".skyguard/lock.json"]


@pytest.mark.parametrize("rel", GUARDED)
def test_a_backup_is_never_restored_into_the_guards_files_or_a_plugin(tmp_path, rel):
    project = tmp_path / "inst"
    p, name = _take(project, "Data/SKSE/Plugins/plain.ini", b"backup bytes")
    target = project / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"live")
    rc, text = _restore(project, "--restore", name, "--to", str(target))
    assert rc == 2 and "REFUSED" in text, (rel, text)
    assert target.read_bytes() == b"live"


@pytest.mark.skipif(sys.platform != "win32", reason="a directory junction is a Windows construct")
def test_a_restore_through_a_junction_into_the_state_is_refused(tmp_path):
    import _winapi
    project = tmp_path / "inst"
    p, name = _take(project, "Data/SKSE/Plugins/plain.ini", b"backup bytes")
    sf = project / ".skyguard" / "state.json"
    state.write(str(sf), {"v": 1, "lifts": [], "persistent": {}})
    before = sf.read_bytes()
    link = project / "innocent"
    _winapi.CreateJunction(str(project / ".skyguard"), str(link))
    try:
        rc, text = _restore(project, "--restore", name, "--to", str(link / "state.json"))
        assert rc == 2 and "REFUSED" in text, text
        assert sf.read_bytes() == before
    finally:
        os.rmdir(str(link))                                          # the junction only, never its target


def test_an_ordinary_file_is_still_restored(tmp_path):
    """The twin: the containment refuses the guard's files, not restores."""
    project = tmp_path / "inst"
    p, name = _take(project, "Data/SKSE/Plugins/plain.ini", b"backup bytes")
    p.write_bytes(b"edited")
    rc, text = _restore(project, "--restore", name)
    assert rc == 0, text
    assert p.read_bytes() == b"backup bytes"


def test_the_state_folder_is_not_backed_up_at_all(tmp_path):
    project = tmp_path / "inst"
    sf = project / ".skyguard" / "state.json"
    state.write(str(sf), {"v": 1, "lifts": [], "persistent": {}})
    a = audit.Audit(str(project))
    backup.run(str(project), "Edit", str(sf), False, a)
    other = project / "Data" / "x.ini"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"x")
    backup.run(str(project), "Edit", str(other), False, a)
    names = [p.name for p in (project / ".claude" / "backups").iterdir() if "__" in p.name]
    assert any(n.endswith("x.ini") for n in names), names                 # the twin: an ordinary file is kept
    assert not any("skyguard" in n for n in names), names


# ---- the guard -------------------------------------------------------------------------------------------------

def _decide(command, shell_name="bash"):
    a = audit.Audit("")
    a.raw = lambda line: None
    r = paths.roots("C:/p6fixA/Skyrim VR", "", {"SKYRIM_GAME_ROOT": GAME})
    v = codes.classify(shell_rules.decide(command, r, a, cwd=GAME, shell=shell_name, env=ENV0))
    return v, {rid for rid, _ in a.fired}


ASKED = [
    ("sh", "bash", "bash scripts/restore-from-backup.sh --restore 20261005_120000__C_x_.skyguard_state.json"),
    ("sh-to", "bash", "./scripts/restore-from-backup.sh --restore a.ini --to C:/x/a.ini"),
    ("sh-env", "bash", "GAME_DIR=/c/g bash scripts/restore-from-backup.sh --restore a.ini"),
    ("ps", "powershell", "bash scripts\\restore-from-backup.sh --restore a.ini"),
    ("python-entry", "bash", "python -B -c 'import sys; sys.path.insert(0, \"tools\"); from skyguard import backup; "
                             "sys.exit(backup.restore_main(sys.argv[1:]))' .claude/backups --restore a.ini"),
    ("python-private", "bash", "python -c 'from skyguard import backup; backup._restore(B, \"a.ini\", \"\")'"),
]


@pytest.mark.parametrize("cid,shell_name,command", ASKED, ids=[c[0] for c in ASKED])
def test_the_guard_asks_before_a_backup_restore(cid, shell_name, command):
    v, fired = _decide(command, shell_name)
    assert (v.decision, v.code) == ("ask", "shell.S-RESTORE-INPLACE"), (v.decision, v.code, v.reason)
    assert v.reason == rules.message("S-RESTORE-INPLACE")


LISTED = [
    ("list", "bash scripts/restore-from-backup.sh"),
    ("search", "bash scripts/restore-from-backup.sh plugins"),
    ("python-list", "python -c 'from skyguard import backup; backup.restore_main([\".claude/backups\"])'"),
]


@pytest.mark.parametrize("cid,command", LISTED, ids=[c[0] for c in LISTED])
def test_listing_the_backups_is_not_asked(cid, command):
    """The twin: only --restore writes; the listing and the search stay allowed."""
    _v, fired = _decide(command)
    assert "S-RESTORE-INPLACE" not in fired


def test_on_codex_the_backup_restore_ask_is_a_deny():
    v, _ = _decide(ASKED[0][2])
    body = json.loads(codex.render(v))["hookSpecificOutput"]
    assert body["permissionDecision"] == "deny"
