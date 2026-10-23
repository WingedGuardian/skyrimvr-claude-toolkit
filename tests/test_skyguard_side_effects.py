"""skyguard.backup / snapshot / session: the non-blocking hooks, plus bash-vs-python equivalence."""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import time

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit as audit_mod, backup, session, snapshot  # noqa: E402

_spec = importlib.util.spec_from_file_location("guard_record", REPO / "scripts" / "guard-corpus" / "record.py")
record = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(record)

DAY = 86400
HOOKS = REPO / "tests" / "guard_corpus" / "reference"
EACUTE = "\u00e9"


def log_text(project):
    p = project / ".claude" / "backups" / "AUDIT_LOG.txt"
    return p.read_bytes().decode("utf-8") if p.is_file() else ""


def age(path, days):
    t = time.time() - days * DAY - 60
    os.utime(str(path), (t, t))


def backups_listing(tmp_path):
    d = tmp_path / ".claude" / "backups"
    return set(os.listdir(str(d))) if d.is_dir() else set()


# ---------------------------------------------------------------- backup

def test_backup_copies_with_flattened_name_and_audit_line(tmp_path):
    f = tmp_path / "y.txt"
    f.write_text("hello")
    a = audit_mod.Audit(str(tmp_path))
    backup.run(str(tmp_path), "Edit", str(f), False, a)
    backups = [p for p in (tmp_path / ".claude" / "backups").iterdir() if "__" in p.name]
    assert len(backups) == 1
    safe = re.sub(r"[/\\:]", "_", str(f)).lstrip("_")
    assert re.fullmatch(r"\d{8}_\d{6}__" + re.escape(safe), backups[0].name)
    assert backups[0].read_text() == "hello"
    ts = backups[0].name.split("__")[0]
    assert log_text(tmp_path) == "[{t}] Edit -> {p} (backup: {t}__{s})\n".format(t=ts, p=f, s=safe)


def test_backup_name_flattening_matches_sed_for_windows_paths():
    # `C:\x\y.txt`: colon and backslash each become "_"; leading underscores are stripped.
    assert re.sub(r"[/\\:]", "_", "C:\\x\\y.txt").lstrip("_") == "C__x_y.txt"
    assert re.sub(r"[/\\:]", "_", "/c/x/y").lstrip("_") == "c_x_y"


@pytest.mark.parametrize("sub", [".claude/backups", ".claude/hooks", ".claude/plans", "node_modules",
                                 "A/node_modules", ".CLAUDE/Backups", "NODE_MODULES"])
def test_backup_skips_own_workspace_and_node_modules(tmp_path, sub):
    d = tmp_path / sub
    d.mkdir(parents=True)
    f = d / "a.txt"
    f.write_text("x")
    before = backups_listing(tmp_path)
    backup.run(str(tmp_path), "Edit", str(f), False, audit_mod.Audit(str(tmp_path)))
    assert backups_listing(tmp_path) == before and log_text(tmp_path) == ""


def test_backup_missing_or_empty_path_is_silent(tmp_path):
    a = audit_mod.Audit(str(tmp_path))
    backup.run(str(tmp_path), "Edit", str(tmp_path / "nope.txt"), False, a)
    backup.run(str(tmp_path), "Edit", "", False, a)
    assert log_text(tmp_path) == ""


def test_backup_empty_payload_writes_blind(tmp_path):
    backup.run(str(tmp_path), "Edit", "", True, audit_mod.Audit(str(tmp_path)))
    assert re.fullmatch(r"\[\d{8}_\d{6}\] backup-before-edit BLIND: received an EMPTY payload -- "
                        r"no backup was taken for this call\n", log_text(tmp_path))


def test_backup_copy_failure_writes_failed(tmp_path, monkeypatch):
    # An unreadable-but-existing source cannot be made portably (a directory fails os.path.isfile), so the
    # copy itself is made to raise OSError.
    f = tmp_path / "a.txt"
    f.write_text("x")

    def boom(*a, **k):
        raise OSError("denied")

    monkeypatch.setattr(backup, "_copy_hashed", boom)
    backup.run(str(tmp_path), "Write", str(f), False, audit_mod.Audit(str(tmp_path)))
    assert re.fullmatch(r"\[\d{8}_\d{6}\] backup-before-edit FAILED: could not copy .*a\.txt -- "
                        r"NO BACKUP EXISTS for this edit\n", log_text(tmp_path))


# ---------------------------------------------------------------- snapshot

def install(tmp_path):
    src = tmp_path / "Data" / "Scripts" / "Source"
    src.mkdir(parents=True)
    return src


def snaps(tmp_path):
    """The snapshot manifests, oldest first (Task 14: one manifest per snapshot)."""
    return snapshot.manifest_names(tmp_path / ".claude" / "backups" / "auto_snapshots")


def snap(tmp_path, command, now=None):
    snapshot.run(str(tmp_path), command, False, audit_mod.Audit(str(tmp_path)), now)


@pytest.mark.parametrize("cmd", ["ls x", "git status", "ls x\nls y", "  cat f", "git config --get a"])
def test_snapshot_skips_read_only(tmp_path, cmd):
    (install(tmp_path) / "a.psc").write_text("x")
    snap(tmp_path, cmd)
    assert snaps(tmp_path) == [] and log_text(tmp_path) == ""


@pytest.mark.parametrize("cmd", [
    "Get-ChildItem Data", "Get-Content a.txt", "Select-String -Path a.txt -Pattern x", "Test-Path Data",
    "Resolve-Path .", "Get-ChildItem Data | Measure-Object", "Measure-Object -Line a.txt",
    "Get-Date", "Get-Process", "  Get-Item x", "get-childitem Data", "GET-CONTENT a", "Get-Date\nGet-Date"])
def test_snapshot_skips_powershell_reads(tmp_path, cmd):
    """The PowerShell tool is wired to this hook (E12): its read-only verbs take no snapshot, like `ls`."""
    (install(tmp_path) / "a.psc").write_text("x")
    snap(tmp_path, cmd)
    assert snaps(tmp_path) == [] and log_text(tmp_path) == ""


@pytest.mark.parametrize("cmd", ["Remove-Item a.psc", "Set-Content a.txt x", "Get-ChildItem; Remove-Item a",
                                 "Getaway-Thing", "Copy-Item a b", "Select-Stringy x", "Test-PathX", "Get-DateX"])
def test_snapshot_does_not_skip_powershell_writes(tmp_path, cmd):
    """A mutating verb, or a lookalike that merely starts with a read verb, still snapshots."""
    (install(tmp_path) / "a.psc").write_text("x")
    snap(tmp_path, cmd)
    assert len(snaps(tmp_path)) == 1, cmd


def test_snapshot_uppercase_ls_is_not_skipped(tmp_path):
    (install(tmp_path) / "a.psc").write_text("x")
    snap(tmp_path, "LS x")
    assert len(snaps(tmp_path)) == 1


def test_snapshot_stores_every_script_whatever_its_age_or_case(tmp_path):
    """Task 14: no 7-day exclusion, and `.PSC` is a script too (Windows names are case-insensitive)."""
    src = install(tmp_path)
    (src / "new.psc").write_text("n")
    (src / "old.psc").write_text("o")
    age(src / "old.psc", 30)
    (src / "UP.PSC").write_text("u")
    (tmp_path / "Data" / "Scripts" / "x.pex").write_text("p")
    snap(tmp_path, "make it")
    (s,) = snaps(tmp_path)
    m = snapshot.resolve(tmp_path / ".claude" / "backups" / "auto_snapshots", s)
    assert sorted(m) == ["Data/Scripts/Source/UP.PSC", "Data/Scripts/Source/new.psc", "Data/Scripts/Source/old.psc",
                         "Data/Scripts/x.pex"]
    assert re.fullmatch(r"\[\d{8}_\d{6}\] AUTO-SNAPSHOT \(psc:3 pex:1 changed, 4 files\) " + re.escape(s)
                        + r" before: make it\n", log_text(tmp_path))
    rl = tmp_path / ".claude" / "backups" / "auto_snapshots" / ".last_snapshot"
    raw = rl.read_bytes()
    assert re.fullmatch(rb"\d+\n", raw) and abs(int(raw) - time.time()) < 30


def test_snapshot_rate_limit_and_newer_psc(tmp_path):
    src = install(tmp_path)
    f = src / "a.psc"
    f.write_text("x")
    snap(tmp_path, "make 1")
    rl = tmp_path / ".claude" / "backups" / "auto_snapshots" / ".last_snapshot"
    t = time.time() - 10
    os.utime(str(rl), (t, t))
    f.write_text("y")
    os.utime(str(f), (t - 5, t - 5))
    snap(tmp_path, "make 2")                       # inside the window, nothing newer than the last walk -> skipped
    assert len(snaps(tmp_path)) == 1
    os.utime(str(f), (t + 5, t + 5))               # strictly newer than the last walk
    snap(tmp_path, "make 3")
    assert len(snaps(tmp_path)) == 2 and len(log_text(tmp_path).splitlines()) == 2


def test_snapshot_equal_mtime_is_not_newer(tmp_path):
    src = install(tmp_path)
    f = src / "a.psc"
    f.write_text("x")
    snap(tmp_path, "make 1")
    rl = tmp_path / ".claude" / "backups" / "auto_snapshots" / ".last_snapshot"
    t = time.time() - 10
    os.utime(str(rl), (t, t))
    f.write_text("y")
    os.utime(str(f), (t, t))
    snap(tmp_path, "make 2")
    assert len(log_text(tmp_path).splitlines()) == 1


@pytest.mark.parametrize("garbage", [b"garbled", b"12x\n", b"\xff\xfe"], ids=["word", "digits-then-junk", "bytes"])
def test_a_garbled_rate_stamp_compares_now_rewrites_it_and_says_so_once(tmp_path, garbage):
    """fix-limits 7: an unreadable `.last_snapshot` reads as "no comparison yet": this call compares in full despite
    the 60-second window and rewrites the stamp, and the audit log says so -- once, because the rewrite repairs it."""
    src = install(tmp_path)
    f = src / "a.psc"
    f.write_text("x")
    snap(tmp_path, "make 1")
    rl = tmp_path / ".claude" / "backups" / "auto_snapshots" / ".last_snapshot"
    t = time.time() - 10
    rl.write_bytes(garbage)
    os.utime(str(rl), (t, t))
    f.write_text("y")
    os.utime(str(f), (t - 5, t - 5))               # inside the window and not newer: a valid stamp would skip this
    snap(tmp_path, "make 2")
    assert len(snaps(tmp_path)) == 2 and re.fullmatch(rb"\d+\n", rl.read_bytes())
    notes = [x for x in log_text(tmp_path).splitlines() if "RATE-STAMP" in x]
    assert len(notes) == 1 and ".last_snapshot" in notes[0], log_text(tmp_path)
    snap(tmp_path, "make 3")                       # repaired: the window applies again, and nothing more is said
    assert len([x for x in log_text(tmp_path).splitlines() if "RATE-STAMP" in x]) == 1


def test_snapshot_nothing_changed_writes_no_snapshot(tmp_path):
    """A walk that finds every file as the index has it takes no snapshot and logs nothing (it still records the
    walk, so the rate limit counts from it)."""
    (install(tmp_path) / "a.psc").write_text("x")
    snap(tmp_path, "make 1")
    snap(tmp_path, "make 2", now=time.time() + 120)
    assert len(snaps(tmp_path)) == 1 and len(log_text(tmp_path).splitlines()) == 1
    assert (tmp_path / ".claude" / "backups" / "auto_snapshots" / ".last_snapshot").is_file()


def test_snapshot_without_script_dirs_does_nothing(tmp_path):
    snap(tmp_path, "make")
    assert snaps(tmp_path) == [] and log_text(tmp_path) == ""


def test_snapshot_prunes_old_legacy_dirs_and_keeps_new(tmp_path):
    """The bash-era `<stamp>/` copy folders still age out after 14 days (floor), and only those."""
    src = install(tmp_path)
    (src / "a.psc").write_text("x")
    base = tmp_path / ".claude" / "backups" / "auto_snapshots"
    old, young = base / "20200101_000000", base / "20200102_000000"
    old.mkdir(parents=True)
    young.mkdir()
    edge = base / "20200103_000000"
    edge.mkdir()
    other = base / "not-a-stamp"
    other.mkdir()
    age(old, 15)
    age(young, 13)
    age(edge, 14)                                   # 14.0007 days: floor 14, not > 14 -> kept
    age(other, 30)
    snap(tmp_path, "make")
    names = sorted(p.name for p in base.iterdir() if p.is_dir())
    assert names == ["20200102_000000", "20200103_000000", "blobs", "manifests", "not-a-stamp"]


def test_snapshot_audit_line_carries_first_120_bytes(tmp_path):
    (install(tmp_path) / "a.psc").write_text("x")
    cmd = "make " + EACUTE * 100
    snap(tmp_path, cmd)
    raw = (tmp_path / ".claude" / "backups" / "AUDIT_LOG.txt").read_bytes()
    assert raw.endswith(b"before: " + cmd.encode("utf-8")[:120] + b"\n")


def test_snapshot_trailing_newlines_stripped_in_audit_line(tmp_path):
    (install(tmp_path) / "a.psc").write_text("x")
    snap(tmp_path, "make\n\n")
    assert log_text(tmp_path).endswith("before: make\n") and log_text(tmp_path).count("\n") == 1


def test_snapshot_empty_payload_writes_blind(tmp_path):
    snapshot.run(str(tmp_path), "", True, audit_mod.Audit(str(tmp_path)))
    assert re.fullmatch(r"\[\d{8}_\d{6}\] snapshot-before-tool BLIND: EMPTY payload -- no snapshot taken\n",
                        log_text(tmp_path))


# ---------------------------------------------------------------- session

def stub(tmp_path, body):
    (tmp_path / "tools").mkdir(exist_ok=True)
    (tmp_path / "tools" / "kb-guard.sh").write_text(body, encoding="utf-8", newline="\n")


def test_session_compact_is_silent(tmp_path):
    assert session.run(str(tmp_path), "compact", record.bash(), audit_mod.Audit(str(tmp_path))) == ""
    assert log_text(tmp_path) == ""


def test_session_missing_tool(tmp_path):
    out = session.run(str(tmp_path), "startup", record.bash(), audit_mod.Audit(str(tmp_path)))
    assert out == ("KB GUARD: NOT RUN -- {}/tools/kb-guard.sh is missing, so your knowledgebase was NOT "
                   "snapshotted this session.".format(tmp_path))
    assert re.fullmatch(r"\[\d{8}_\d{6}\] session-kb-guard NOT RUN: tools/kb-guard.sh missing\n", log_text(tmp_path))


def test_session_rc2_gets_extra_line_and_flat_audit(tmp_path):
    stub(tmp_path, "echo one\necho two >&2\nexit 2\n")
    out = session.run(str(tmp_path), "resume", record.bash(), audit_mod.Audit(str(tmp_path)))
    assert out == ("one\ntwo\nKB GUARD: snapshot coverage is incomplete this session -- unchecked, not clean.")
    assert re.fullmatch(r"\[\d{8}_\d{6}\] session-kb-guard rc=2 one two\n", log_text(tmp_path))


def test_session_clean_run_and_empty_output(tmp_path):
    stub(tmp_path, "exit 0\n")
    assert session.run(str(tmp_path), "startup", record.bash(), audit_mod.Audit(str(tmp_path))) == ""
    assert re.fullmatch(r"\[\d{8}_\d{6}\] session-kb-guard rc=0 \n", log_text(tmp_path))


@pytest.mark.parametrize("text,status", [
    ("KB GUARD: FAILED to store -- file. UNPROTECTED", "partial"),
    ("KB GUARD -- ALARM: an irreplaceable file lost content", "evaluated"),
    ("KB GUARD -- ALARM: an irreplaceable file lost content\nKB GUARD: FAILED to store -- file. UNPROTECTED", "partial"),
])
def test_session_distinguishes_completed_alarm_from_failed_preservation(tmp_path, text, status):
    import shlex
    stub(tmp_path, "printf '%s\\n' " + shlex.quote(text) + "\nexit 1\n")
    out, actual, code = session.run_status(str(tmp_path), "startup", record.bash(), audit_mod.Audit(str(tmp_path)))
    assert actual == status
    assert code == ("session.NOT_SNAPSHOTTED" if status == "partial" else "session")
    assert text in out


def test_session_audit_output_truncated_to_200_bytes(tmp_path):
    stub(tmp_path, "printf 'a%.0s' $(seq 1 300)\nexit 0\n")
    session.run(str(tmp_path), "startup", record.bash(), audit_mod.Audit(str(tmp_path)))
    assert log_text(tmp_path).endswith("rc=0 " + "a" * 200 + "\n")


# ---------------------------------------------------------------- equivalence with the real bash hooks

needs_jq = pytest.mark.skipif(record.needs_missing_jq(HOOKS), reason="the reference bash hooks need jq")


def strip_ts(text):
    return re.sub(r"\d{8}_\d{6}", "TS", text)       # every stamp: the two runs may straddle a second


@needs_jq
@pytest.mark.parametrize("name", ["y.txt", "sp ace.txt", EACUTE + ".txt"])
def test_backup_equivalent_to_real_bash(tmp_path, name):
    shared = tmp_path / "shared" / "dir"
    shared.mkdir(parents=True)
    f = shared / name
    f.write_text("body")
    path = f.as_posix()
    b = record.make_install(tmp_path / "bash", HOOKS, "declared")
    record.fire(b, "backup-before-edit.sh", {"tool_name": "Edit", "tool_input": {"file_path": path}})
    py = tmp_path / "py"
    py.mkdir()
    backup.run(str(py), "Edit", path, False, audit_mod.Audit(str(py)))

    def result(root):
        d = root / ".claude" / "backups"
        files = sorted((p.name.split("__", 1)[1], p.read_bytes()) for p in d.iterdir()
                       if p.is_file() and "__" in p.name)
        return files, strip_ts(log_text(root))

    rb, rp = result(b), result(py)
    assert rb[0] and rb == rp


@needs_jq
@pytest.mark.parametrize("command", ["ls x", "git status"])
def test_snapshot_skip_equivalent_to_real_bash(tmp_path, command):
    inst_b = record.make_install(tmp_path / "bash", HOOKS, "declared")
    py = tmp_path / "py"
    (py / "Data" / "Scripts" / "Source").mkdir(parents=True)
    for root in (inst_b, py):
        (root / "Data" / "Scripts" / "Source" / "a.psc").write_text("x")
    record.fire(inst_b, "snapshot-before-tool.sh", {"tool_name": "Bash", "tool_input": {"command": command}})
    snapshot.run(str(py), command, False, audit_mod.Audit(str(py)))
    assert strip_ts(log_text(inst_b)) == strip_ts(log_text(py))
    assert len(snaps(inst_b)) == len(snaps(py))


@needs_jq
def test_session_equivalent_to_real_bash(tmp_path):
    inst_b = record.make_install(tmp_path / "bash", HOOKS, "declared")
    py = tmp_path / "py"
    py.mkdir()
    for root in (inst_b, py):
        stub(root, "echo first\necho second\nexit 2\n")
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(inst_b))
    r = subprocess.run([record.bash(), str(inst_b / ".claude" / "hooks" / "session-kb-guard.sh")],
                       input=b'{"source":"startup"}', capture_output=True, env=env, timeout=120)
    assert r.returncode == 0
    want = r.stdout.decode("utf-8").replace("\r\n", "\n")
    got = session.run(str(py), "startup", record.bash(), audit_mod.Audit(str(py))) + "\n"
    # The historical hook called every rc=2 "NOTHING" preserved. The current
    # guard uses rc=2 for partial preservation too, so report that accurately.
    want = want.replace("the above means NOTHING was snapshotted", "snapshot coverage is incomplete")
    assert got == want and "snapshot coverage is incomplete" in got
    assert strip_ts(log_text(inst_b)) == strip_ts(log_text(py)) != ""


def test_snapshot_one_failed_copy_does_not_abort(tmp_path, monkeypatch):
    src = install(tmp_path)
    (src / "bad.psc").write_text("b")
    (src / "good.psc").write_text("g")
    real = snapshot.backup._copy_hashed

    def flaky(a, b, *k, **kw):
        if str(a).endswith("bad.psc"):
            raise OSError("locked")
        return real(a, b, *k, **kw)

    monkeypatch.setattr(snapshot.backup, "_copy_hashed", flaky)
    snap(tmp_path, "make")
    (s,) = snaps(tmp_path)
    assert sorted(snapshot.resolve(tmp_path / ".claude" / "backups" / "auto_snapshots", s)) == [
        "Data/Scripts/Source/good.psc"]
    assert re.fullmatch(r"\[\d{8}_\d{6}\] AUTO-SNAPSHOT \(psc:1 pex:0 changed, 1 files, 1 could not be read -- "
                        r"retried on the next walk\) \S+ before: make\n", log_text(tmp_path))
    assert (tmp_path / ".claude" / "backups" / "auto_snapshots" / ".last_snapshot").is_file()
    monkeypatch.setattr(snapshot.backup, "_copy_hashed", real)
    snap(tmp_path, "make", now=time.time() + 120)          # the next full walk picks it up
    assert "Data/Scripts/Source/bad.psc" in snapshot.resolve(
        tmp_path / ".claude" / "backups" / "auto_snapshots", snaps(tmp_path)[-1])


def test_snapshot_rate_limit_window_is_60_seconds(tmp_path):
    """Inside the window only a NEWER file is looked at; from 60 s on, the full index comparison runs again. Shown
    with the stated gap's own shape: content replaced under an OLDER mtime."""
    src = install(tmp_path)
    f = src / "a.psc"
    f.write_text("x")
    now = time.time()
    snap(tmp_path, "make", now)
    t = now - 3600

    def go(at, body):                  # each body a new size: same size + same mtime is the index's own blind spot
        f.write_text(body)
        os.utime(str(f), (t, t))                   # an mtime-preserving replace: older than the last walk
        snap(tmp_path, "make", at)
        return len(snaps(tmp_path))

    assert go(now + 59, "yy") == 1     # inside the window: not seen (the stated gap)
    assert go(now + 60, "zzz") == 2     # boundary: 60 is not < 60 -> the full comparison runs
    assert go(now + 60 + 59, "wwww") == 2
    assert go(now + 60 + 60, "vvvvv") == 3


def test_backup_mkdir_failure_logs_failed(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "backups").write_text("i am a file")
    f = tmp_path / "x.txt"
    f.write_text("x")
    calls = []

    class A:
        @staticmethod
        def stamp():
            return "20260101_000000"

        def log(self, *a):
            calls.append(a)

        def raw(self, *a):
            calls.append(("raw",) + a)

    backup.run(str(tmp_path), "Edit", str(f), False, A())
    assert len(calls) == 1 and calls[0][1] == "FAILED" and "NO BACKUP EXISTS" in calls[0][2]


def test_session_invalid_utf8_is_written_raw_and_stdin_is_closed(tmp_path):
    stub(tmp_path, "printf '\\377\\376 ok\\n'\ncat >/dev/null\nexit 0\n")
    out = session.run(str(tmp_path), "startup", record.bash(), audit_mod.Audit(str(tmp_path)))
    assert out == "�� ok"
    raw = (tmp_path / ".claude" / "backups" / "AUDIT_LOG.txt").read_bytes()
    assert raw.endswith(b" rc=0 \xff\xfe ok\n")
