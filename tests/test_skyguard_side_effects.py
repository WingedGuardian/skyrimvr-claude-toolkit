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
HOOKS = REPO / ".claude" / "hooks"
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

    monkeypatch.setattr(backup.shutil, "copyfile", boom)
    backup.run(str(tmp_path), "Write", str(f), False, audit_mod.Audit(str(tmp_path)))
    assert re.fullmatch(r"\[\d{8}_\d{6}\] backup-before-edit FAILED: could not copy .*a\.txt -- "
                        r"NO BACKUP EXISTS for this edit\n", log_text(tmp_path))


# ---------------------------------------------------------------- snapshot

def install(tmp_path):
    src = tmp_path / "Data" / "Scripts" / "Source"
    src.mkdir(parents=True)
    return src


def snaps(tmp_path):
    base = tmp_path / ".claude" / "backups" / "auto_snapshots"
    return sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []


def snap(tmp_path, command, now=None):
    snapshot.run(str(tmp_path), command, False, audit_mod.Audit(str(tmp_path)), now)


@pytest.mark.parametrize("cmd", ["ls x", "git status", "ls x\nls y", "make\nls y", "  cat f", "git config --get a"])
def test_snapshot_skips_read_only(tmp_path, cmd):
    (install(tmp_path) / "a.psc").write_text("x")
    snap(tmp_path, cmd)
    assert snaps(tmp_path) == [] and log_text(tmp_path) == ""


def test_snapshot_uppercase_ls_is_not_skipped(tmp_path):
    (install(tmp_path) / "a.psc").write_text("x")
    snap(tmp_path, "LS x")
    assert len(snaps(tmp_path)) == 1


def test_snapshot_copies_only_recent_and_ignores_uppercase_ext(tmp_path):
    src = install(tmp_path)
    (src / "new.psc").write_text("n")
    (src / "old.psc").write_text("o")
    age(src / "old.psc", 8)
    (src / "UP.PSC").write_text("u")
    (tmp_path / "Data" / "Scripts" / "x.pex").write_text("p")
    snap(tmp_path, "make it")
    (s,) = snaps(tmp_path)
    assert sorted(os.listdir(str(s / "source"))) == ["new.psc"]
    assert sorted(os.listdir(str(s / "deploy"))) == ["x.pex"]
    assert re.fullmatch(r"\[\d{8}_\d{6}\] AUTO-SNAPSHOT \(psc:1 pex:1\) before: make it\n", log_text(tmp_path))
    rl = tmp_path / ".claude" / "backups" / "auto_snapshots" / ".last_snapshot"
    raw = rl.read_bytes()
    assert re.fullmatch(rb"\d+\n", raw) and abs(int(raw) - time.time()) < 30


def test_snapshot_age_boundary_is_truncated_days(tmp_path):
    src = install(tmp_path)
    now = time.time()
    f = src / "edge.psc"
    f.write_text("x")
    t = now - 7 * DAY + 5          # 6.99994 days: find -mtime -7 includes it
    os.utime(str(f), (t, t))
    g = src / "edge2.psc"
    g.write_text("x")
    t = now - 7 * DAY - 5          # 7.00006 days truncates to 7: excluded
    os.utime(str(g), (t, t))
    snap(tmp_path, "make", now)
    (s,) = snaps(tmp_path)
    assert os.listdir(str(s / "source")) == ["edge.psc"]


def test_snapshot_rate_limit_and_newer_psc(tmp_path):
    src = install(tmp_path)
    f = src / "a.psc"
    f.write_text("x")
    snap(tmp_path, "make 1")
    rl = tmp_path / ".claude" / "backups" / "auto_snapshots" / ".last_snapshot"
    t = time.time() - 10
    os.utime(str(rl), (t, t))
    os.utime(str(f), (t - 5, t - 5))
    snap(tmp_path, "make 2")                       # nothing newer -> skipped
    assert len(log_text(tmp_path).splitlines()) == 1
    os.utime(str(f), (t + 5, t + 5))               # strictly newer than the rate file
    (s,) = snaps(tmp_path)                         # the snapshot dir name is a 1-second stamp: avoid a clash
    s.rename(s.with_name("19990101_000000"))
    snap(tmp_path, "make 3")
    assert len(log_text(tmp_path).splitlines()) == 2


def test_snapshot_equal_mtime_is_not_newer(tmp_path):
    src = install(tmp_path)
    f = src / "a.psc"
    f.write_text("x")
    snap(tmp_path, "make 1")
    rl = tmp_path / ".claude" / "backups" / "auto_snapshots" / ".last_snapshot"
    t = time.time() - 10
    os.utime(str(rl), (t, t))
    os.utime(str(f), (t, t))
    snap(tmp_path, "make 2")
    assert len(log_text(tmp_path).splitlines()) == 1


def test_snapshot_empty_is_removed_and_no_rate_file(tmp_path):
    src = install(tmp_path)
    (src / "old.psc").write_text("x")
    age(src / "old.psc", 30)
    snap(tmp_path, "make")
    assert snaps(tmp_path) == [] and log_text(tmp_path) == ""
    assert not (tmp_path / ".claude" / "backups" / "auto_snapshots" / ".last_snapshot").exists()


def test_snapshot_without_script_dirs_does_nothing(tmp_path):
    snap(tmp_path, "make")
    assert snaps(tmp_path) == [] and log_text(tmp_path) == ""


def test_snapshot_prunes_old_dirs_and_keeps_new(tmp_path):
    src = install(tmp_path)
    (src / "a.psc").write_text("x")
    base = tmp_path / ".claude" / "backups" / "auto_snapshots"
    old, young = base / "20200101_000000", base / "20200102_000000"
    old.mkdir(parents=True)
    young.mkdir()
    edge = base / "20200103_000000"
    edge.mkdir()
    age(old, 15)
    age(young, 13)
    age(edge, 14)                                   # 14.0007 days: floor 14, not > 14 -> kept
    snap(tmp_path, "make")
    names = [p.name for p in snaps(tmp_path)]
    assert "20200101_000000" not in names and "20200102_000000" in names and "20200103_000000" in names
    assert len(names) == 3


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
    assert out == ("one\ntwo\nKB GUARD: the above means NOTHING was snapshotted this session -- "
                   "unchecked, not clean.")
    assert re.fullmatch(r"\[\d{8}_\d{6}\] session-kb-guard rc=2 one two\n", log_text(tmp_path))


def test_session_clean_run_and_empty_output(tmp_path):
    stub(tmp_path, "exit 0\n")
    assert session.run(str(tmp_path), "startup", record.bash(), audit_mod.Audit(str(tmp_path))) == ""
    assert re.fullmatch(r"\[\d{8}_\d{6}\] session-kb-guard rc=0 \n", log_text(tmp_path))


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
@pytest.mark.parametrize("command", ["make it", "LS x", "python build.py --out Data/x"])
def test_snapshot_equivalent_to_real_bash(tmp_path, command):
    inst_b = record.make_install(tmp_path / "bash", HOOKS, "declared")
    py = tmp_path / "py"
    (py / "Data" / "Scripts" / "Source").mkdir(parents=True)
    for root in (inst_b, py):
        (root / "Data" / "Scripts" / "Source" / "a.psc").write_text("src")
        (root / "Data" / "Scripts" / "a.pex").write_text("pex")
    record.fire(inst_b, "snapshot-before-tool.sh", {"tool_name": "Bash", "tool_input": {"command": command}})
    snapshot.run(str(py), command, False, audit_mod.Audit(str(py)))

    def result(root):
        base = root / ".claude" / "backups" / "auto_snapshots"
        (d,) = [p for p in base.iterdir() if p.is_dir()]
        files = sorted((p.relative_to(d).as_posix(), p.read_bytes()) for p in d.rglob("*") if p.is_file())
        return files, strip_ts(log_text(root)), re.fullmatch(rb"\d+\n", (base / ".last_snapshot").read_bytes()) is not None

    rb, rp = result(inst_b), result(py)
    assert rb[0] and rb == rp




@needs_jq
@pytest.mark.parametrize("command", ["ls x", "make\nls y", "git status", "LS x"])
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
    assert got == want and "KB GUARD: the above" in want
    assert strip_ts(log_text(inst_b)) == strip_ts(log_text(py)) != ""


def test_snapshot_one_failed_copy_does_not_abort(tmp_path, monkeypatch):
    src = install(tmp_path)
    (src / "bad.psc").write_text("b")
    (src / "good.psc").write_text("g")
    real = snapshot.shutil.copyfile

    def flaky(a, b, *k, **kw):
        if str(a).endswith("bad.psc"):
            raise OSError("locked")
        return real(a, b, *k, **kw)

    monkeypatch.setattr(snapshot.shutil, "copyfile", flaky)
    snap(tmp_path, "make")
    (s,) = snaps(tmp_path)
    assert os.listdir(str(s / "source")) == ["good.psc"]
    assert re.fullmatch(r"\[\d{8}_\d{6}\] AUTO-SNAPSHOT \(psc:1 pex:0\) before: make\n", log_text(tmp_path))
    assert (tmp_path / ".claude" / "backups" / "auto_snapshots" / ".last_snapshot").is_file()


def test_snapshot_rate_limit_window_is_60_seconds(tmp_path):
    (install(tmp_path) / "a.psc").write_text("x")
    now = time.time()
    base = tmp_path / ".claude" / "backups" / "auto_snapshots"

    def go(at):
        for d in snaps(tmp_path):                  # same-second dir names would merge; not what is tested
            shutil.rmtree(str(d))
        snap(tmp_path, "make", at)
        return len(log_text(tmp_path).splitlines())

    assert go(now) == 1
    assert go(now + 59) == 1          # inside the window, nothing newer -> skipped
    assert go(now + 60) == 2          # boundary: 60 is not < 60 -> taken
    assert go(now + 60 + 61) == 3     # 61 s after the previous snapshot -> taken
    assert (base / ".last_snapshot").is_file()


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
