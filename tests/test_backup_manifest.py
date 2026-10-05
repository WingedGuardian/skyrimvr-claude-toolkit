"""Plan 6 Task 13: backups land atomically under an exclusive name, are recorded in backups/MANIFEST.jsonl with a sha256,
and restore-from-backup.sh restores to the RECORDED path after checking that sha256.

The defects this pins (plan L7/L8): two backups of one file in the same second overwrote each other; a copy that failed
midway left a partial file under the final name; restore rebuilt the original path by turning every `_` into `/`, so
`Skyrim_VR - Main.bsa` was restored to the wrong place.
"""
import hashlib
import json
import os
import re
import subprocess
import sys

import pytest

from conftest import BASH, REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit as audit_mod, backup  # noqa: E402

TS = "20261005_120000"
RESTORE = REPO / "scripts" / "restore-from-backup.sh"


class FixedAudit(audit_mod.Audit):
    """Every call in the same second: the stamp is pinned."""

    def __init__(self, project, ts=TS):
        super().__init__(project)
        self._ts = ts

    def stamp(self):
        return self._ts


def bdir(project):
    return project / ".claude" / "backups"


def backup_files(project):
    """The backup copies themselves: every regular file except the audit log and the manifest."""
    d = bdir(project)
    if not d.is_dir():
        return {}
    return {p.name: p.read_bytes() for p in d.iterdir()
            if p.is_file() and p.name not in ("AUDIT_LOG.txt", "MANIFEST.jsonl")}


def manifest(project):
    p = bdir(project) / "MANIFEST.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.is_file() else []


def log_text(project):
    p = bdir(project) / "AUDIT_LOG.txt"
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def safe(path):
    return re.sub(r"[/\\:]", "_", path).lstrip("_")


def take(project, f, **kw):
    backup.run(str(project), "Edit", str(f), False, FixedAudit(str(project)), **kw)


# ---------------------------------------------------------------- Step 2: the write

def test_two_backups_in_the_same_second_both_survive(tmp_path):
    f = tmp_path / "a.ini"
    for body in (b"one", b"two", b"three"):
        f.write_bytes(body)
        take(tmp_path, f)
    s = safe(str(f))
    assert backup_files(tmp_path) == {TS + "__" + s: b"one", TS + "-1__" + s: b"two", TS + "-2__" + s: b"three"}
    assert [m["dest"] for m in manifest(tmp_path)] == [TS + "__" + s, TS + "-1__" + s, TS + "-2__" + s]


def test_same_second_backups_survive_where_hard_links_are_unavailable(tmp_path, monkeypatch):
    def no_link(*a, **k):
        raise OSError(1, "hard links not supported here")

    monkeypatch.setattr(backup.os, "link", no_link)
    f = tmp_path / "a.ini"
    for body in (b"one", b"two"):
        f.write_bytes(body)
        take(tmp_path, f)
    s = safe(str(f))
    assert backup_files(tmp_path) == {TS + "__" + s: b"one", TS + "-1__" + s: b"two"}


def test_a_copy_that_fails_midway_leaves_nothing_under_the_final_name(tmp_path, monkeypatch):
    f = tmp_path / "a.ini"
    f.write_bytes(b"x" * 100)
    seen = []
    real = backup._hasher

    class Breaks:
        def __init__(self):
            self.h, self.n = real(), 0

        def update(self, chunk):
            self.n += 1
            if self.n == 2:                       # one chunk is already on disk
                seen.append(sorted(backup_files(tmp_path)))
                raise OSError("disk went away")
            self.h.update(chunk)

        def hexdigest(self):
            return self.h.hexdigest()

    monkeypatch.setattr(backup, "CHUNK", 4)
    monkeypatch.setattr(backup, "_hasher", Breaks)
    take(tmp_path, f)
    final = TS + "__" + safe(str(f))
    # while the copy was in flight, the bytes were in the part file and NOT under the final name
    assert seen and final not in seen[0] and any(".part-" in n for n in seen[0]), seen
    assert backup_files(tmp_path) == {}, "a failed copy left a file behind"
    assert manifest(tmp_path) == []
    assert re.search(r"backup-before-edit FAILED: could not copy .*a\.ini -- NO BACKUP EXISTS", log_text(tmp_path))


def test_the_part_is_fsynced_before_it_gets_its_final_name(tmp_path, monkeypatch):
    f = tmp_path / "a.ini"
    f.write_bytes(b"body")
    calls = []
    real = os.fsync

    def spy(fd):
        calls.append(sorted(backup_files(tmp_path)))
        return real(fd)

    monkeypatch.setattr(backup.os, "fsync", spy)
    take(tmp_path, f)
    assert len(calls) == 1 and all(".part-" in n for n in calls[0]) and calls[0], calls
    assert list(backup_files(tmp_path).values()) == [b"body"]


def test_a_size_mismatch_is_failed_and_the_part_is_removed(tmp_path, monkeypatch):
    f = tmp_path / "a.ini"
    f.write_bytes(b"y" * 40)
    real = backup._hasher

    class Grows:
        def __init__(self):
            self.h, self.n = real(), 0

        def update(self, chunk):
            self.n += 1
            if self.n == 1:                       # the file grows while it is being copied
                with open(str(f), "ab") as fh:
                    fh.write(b"z" * 10)
            self.h.update(chunk)

        def hexdigest(self):
            return self.h.hexdigest()

    monkeypatch.setattr(backup, "CHUNK", 8)
    monkeypatch.setattr(backup, "_hasher", Grows)
    take(tmp_path, f)
    assert backup_files(tmp_path) == {}
    assert manifest(tmp_path) == []
    assert re.search(r"backup-before-edit FAILED: .*a\.ini.* size .* NO BACKUP EXISTS", log_text(tmp_path)), log_text(tmp_path)


def test_the_manifest_line_records_the_copy(tmp_path):
    d = tmp_path / "Data"
    d.mkdir()
    f = d / "Skyrim_VR - Main.bsa"
    body = os.urandom(3000)
    f.write_bytes(body)
    src = str(tmp_path) + "/Data\\Skyrim_VR - Main.bsa"           # mixed separators: recorded verbatim
    backup.run(str(tmp_path), "Write", src, False, FixedAudit(str(tmp_path)), agent="claude", session_id="s-1")
    (m,) = manifest(tmp_path)
    assert m == {"v": 1, "ts": TS, "src": src, "dest": TS + "__" + safe(src), "size": 3000,
                 "sha256": hashlib.sha256(body).hexdigest(), "tool": "Write", "agent": "claude", "session_id": "s-1"}
    assert (bdir(tmp_path) / m["dest"]).read_bytes() == body


def test_the_manifest_append_is_one_write_of_one_line(tmp_path, monkeypatch):
    f = tmp_path / "a.ini"
    f.write_text("x\ny\n")
    writes = []
    real = os.write

    def spy(fd, data):
        writes.append(bytes(data))
        return real(fd, data)

    monkeypatch.setattr(backup.os, "write", spy)
    take(tmp_path, f)
    lines = [w for w in writes if b'"sha256"' in w]
    assert len(lines) == 1 and lines[0].endswith(b"\n") and lines[0].count(b"\n") == 1, writes
    assert len(manifest(tmp_path)) == 1


def test_the_audit_line_names_the_backup_actually_written(tmp_path):
    f = tmp_path / "a.ini"
    f.write_text("1")
    take(tmp_path, f)
    take(tmp_path, f)
    s = safe(str(f))
    assert log_text(tmp_path).splitlines()[-1] == "[{t}] Edit -> {p} (backup: {t}-1__{s})".format(t=TS, p=f, s=s)


def test_the_claude_hook_records_its_agent_and_session(tmp_path):
    f = tmp_path / "a.ini"
    f.write_text("x")
    payload = json.dumps({"tool_name": "Edit", "session_id": "sess-42", "tool_input": {"file_path": str(f)}})
    r = subprocess.run([sys.executable, str(REPO / "tools" / "skyguard" / "run.py"), "claude", "backup-before-edit",
                        "--root", "C:/GOG Games/Fake", "--project", str(tmp_path)],
                       input=payload.encode("utf-8"), capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr
    (m,) = manifest(tmp_path)
    assert (m["agent"], m["session_id"], m["tool"], m["src"]) == ("claude", "sess-42", "Edit", str(f))


def test_the_codex_patch_backup_records_its_agent_and_session(tmp_path):
    from skyguard import engine, paths
    from skyguard.adapters import codex
    f = tmp_path / "notes" / "gone.txt"
    f.parent.mkdir()
    f.write_text("keep me")
    raw = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "cwd": "C:/G", "session_id": "cx-7",
                      "tool_input": {"command": "*** Begin Patch\n*** Delete File: {}\n*** End Patch".format(
                          f.as_posix())}})
    engine.pre_tool(codex.to_request(raw, "win32"), str(tmp_path), lambda: paths.roots(str(tmp_path), "", {}),
                    audit_mod.Audit(str(tmp_path)))
    (m,) = manifest(tmp_path)
    assert (m["agent"], m["session_id"]) == ("codex", "cx-7")


# ---------------------------------------------------------------- Step 3: restore

def restore(project, *args):
    env = dict(os.environ, GAME_DIR=str(project), SKYGUARD_PYTHON=sys.executable)
    return subprocess.run([BASH, str(RESTORE), *args], capture_output=True, text=True, env=env, timeout=120)


@pytest.fixture
def bsa(tmp_path):
    d = tmp_path / "Data"
    d.mkdir()
    f = d / "Skyrim_VR - Main.bsa"
    f.write_bytes(b"original")
    take(tmp_path, f)
    return f


def test_restore_lists_the_recorded_path_verbatim(tmp_path, bsa):
    r = restore(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert str(bsa) in r.stdout, r.stdout
    assert "(legacy: path guessed)" not in r.stdout


def test_restore_puts_the_file_back_at_the_recorded_path(tmp_path, bsa):
    bsa.write_bytes(b"broken by an edit")
    (m,) = manifest(tmp_path)
    r = restore(tmp_path, "--restore", m["dest"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert bsa.read_bytes() == b"original"
    # the version it replaced was itself backed up first
    assert b"broken by an edit" in backup_files(tmp_path).values()


def test_restore_refuses_a_backup_whose_sha256_does_not_match(tmp_path, bsa):
    bsa.write_bytes(b"current")
    (m,) = manifest(tmp_path)
    (bdir(tmp_path) / m["dest"]).write_bytes(b"tampered")
    before = backup_files(tmp_path)
    r = restore(tmp_path, "--restore", m["dest"])
    assert r.returncode == 1 and r.stdout.startswith("REFUSED: ") and "sha256" in r.stdout, r.stdout + r.stderr
    assert bsa.read_bytes() == b"current"
    assert backup_files(tmp_path) == before, "refused before anything was touched"


def test_a_backup_that_changes_after_its_check_is_not_restored(tmp_path, bsa, monkeypatch, capsys):
    """The copy is hashed again on the way out: a file swapped between the check and the copy is caught."""
    bsa.write_bytes(b"current")
    (m,) = manifest(tmp_path)
    (bdir(tmp_path) / m["dest"]).write_bytes(b"tampered")
    monkeypatch.setattr(backup, "_sha256", lambda p: m["sha256"])        # the check saw the recorded bytes
    assert backup.restore_main([str(bdir(tmp_path)), "--restore", m["dest"]]) == 1
    assert bsa.read_bytes() == b"current"
    assert "FAILED" in capsys.readouterr().out
    assert not [p for p in bsa.parent.iterdir() if ".restore-part-" in p.name]


def test_a_pre_manifest_backup_is_listed_as_a_guess(tmp_path):
    bdir(tmp_path).mkdir(parents=True)
    (bdir(tmp_path) / "20250101_000000__C__Games_My_Mod.esp").write_bytes(b"old")
    r = restore(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert re.search(r"C//Games/My/Mod\.esp .*\(legacy: path guessed\)", r.stdout), r.stdout


def test_a_legacy_restore_needs_an_explicit_target(tmp_path):
    bdir(tmp_path).mkdir(parents=True)
    (bdir(tmp_path) / "20250101_000000__x.txt").write_bytes(b"old")
    r = restore(tmp_path, "--restore", "20250101_000000__x.txt")
    assert r.returncode == 2 and "--to" in r.stdout, r.stdout + r.stderr
    out = tmp_path / "back.txt"
    r = restore(tmp_path, "--restore", "20250101_000000__x.txt", "--to", str(out))
    assert r.returncode == 0 and out.read_bytes() == b"old", r.stdout + r.stderr


def test_an_unreadable_manifest_line_is_skipped_and_counted(tmp_path, bsa):
    with open(str(bdir(tmp_path) / "MANIFEST.jsonl"), "ab") as fh:
        fh.write(b'{"v":1, "src": "torn\n')
    r = restore(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert str(bsa) in r.stdout and "1 unreadable manifest line(s) skipped" in r.stdout, r.stdout


def test_a_recorded_backup_whose_file_is_gone_is_marked_missing(tmp_path, bsa):
    (m,) = manifest(tmp_path)
    os.unlink(str(bdir(tmp_path) / m["dest"]))
    r = restore(tmp_path)
    assert re.search(re.escape(str(bsa)) + r".*\(missing\)", r.stdout), r.stdout
