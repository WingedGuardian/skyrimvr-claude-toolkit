"""Plan 6 Task 16: `skydoctor` -- one report of what is LIVE on an install, layer by layer.

Every line is `live | not live | unknown` with a reason, never blank. Exit 0 = every counted line live, 1 = any line
not live, 2 = otherwise any line unknown (a known failure outranks an unproven one, as in health.report). The report
writes nothing, except that it clears expired lifts (R9) and logs that it did.

EVERY lock here is applied to a scratch bed under pytest's base temp, with SKYGUARD_LOCK_SANDBOX set to it (as in
test_skyguard_lock.py); each bed is unlocked with raw icacls/attrib and removed afterwards. Codex's config is a file
in the bed and the "home" folder is the bed's own, so the user's ~/.codex and ~/.claude are never read.

Twins (tests/test_mutations.py, `l2-doctor-*`): one per state and clause, each killed through the test named there.
"""
from __future__ import annotations

import hashlib
import io
import itertools
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from conftest import REPO, BASH

sys.path.insert(0, str(REPO / "tools"))
from skyguard import codex_trust as ct  # noqa: E402
from skyguard import guard_hash, health, state  # noqa: E402

WINDOWS = os.name == "nt"
SYS32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
needs_windows = pytest.mark.skipif(not WINDOWS, reason="the lock is a Windows mechanism (R11)")
VR_FILES = ["Skyrim.esm", "Update.esm", "Dawnguard.esm", "Skyrim - Textures0.bsa", "Skyrim - Interface.bsa"]
CODEX_FX = REPO / "tests" / "fixtures" / "codex" / "0.160.0"
EXEC = CODEX_FX / "rollout-exec.jsonl"                  # 2 calls: one shell, one apply_patch
SID = "019a0000-aaaa-bbbb-cccc-000000000016"
SESSION = "sess-doctor-1"
OK_TOOLCHAIN = (0, "RESULT: OK -- every documented tool starts\n")
SCRATCH = None
_ids = itertools.count()


def _doctor():
    from skyguard import doctor
    return doctor


def _sid():
    out = subprocess.run([str(SYS32 / "whoami.exe"), "/user", "/fo", "csv", "/nh"],
                         capture_output=True, text=True).stdout
    return out.strip().split(",")[1].strip('"')


def _scrub(root: Path):
    if not root.exists():
        return
    if WINDOWS:
        subprocess.run([str(SYS32 / "icacls.exe"), str(root), "/remove:d", "*" + _sid(), "/T", "/C"], capture_output=True)
        subprocess.run([str(SYS32 / "attrib.exe"), "-R", os.path.join(str(root), "*"), "/S", "/D"], capture_output=True)
    shutil.rmtree(str(root), ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def _scratch_root(tmp_path_factory):
    global SCRATCH
    SCRATCH = tmp_path_factory.mktemp("doctorscratch")
    yield SCRATCH


def _complete(hb: Path, hook: str, t: float, inv="1.1-1"):
    hb.mkdir(parents=True, exist_ok=True)
    (hb / (hook + ".invoked")).write_text("{} {}\n".format(t - 0.2, inv), encoding="utf-8")
    (hb / (hook + ".received")).write_text("{} {} bytes=40\n".format(t - 0.1, inv), encoding="utf-8")
    (hb / (hook + ".evaluated")).write_text("{} {} evaluated allow allow\n".format(t, inv), encoding="utf-8")
    (hb / (hook + ".emitted")).write_text("{} {} ok\n".format(t + 0.01, inv), encoding="utf-8")


class Bed:
    """A scratch install on which every layer reads live."""

    def __init__(self, root: Path):
        self.root = root
        self.data = root / "Data"
        self.home = root / "home"
        self.cfg = root / "codex-home" / "config.toml"
        self.now = time.time()
        self.toolchain = OK_TOOLCHAIN

    def path(self, name):
        return self.data / name

    def hb(self, agent):
        return self.root / ".claude" / "backups" / ".hook-heartbeat" / agent

    def collect(self, now=None):
        d = _doctor()
        tc = self.toolchain
        return d.collect(str(self.root), now=self.now if now is None else now,
                         toolchain=(lambda root: tc), codex_config=str(self.cfg), home=str(self.home))

    def rows(self, now=None):
        return self.collect(now)[0]

    def code(self, now=None):
        return _doctor().exit_code(self.rows(now))

    def row(self, item, now=None):
        found = [r for r in self.rows(now) if r.item == item]
        assert len(found) == 1, (item, [r.item for r in self.rows(now)])
        return found[0]

    def state_write(self, lifts=(), persistent=None):
        state.write(str(self.root / ".skyguard" / "state.json"),
                    {"v": 1, "lifts": list(lifts), "persistent": persistent or {}})

    def manifest(self):
        lines = ["# release 4.0.0"]
        for f in sorted((self.root / ".claude" / "hooks").glob("*.sh")):
            rel = ".claude/hooks/" + f.name
            lines.append("{}  {}".format(guard_hash.digest(rel, f.read_bytes())[0], rel))
        (self.root / "GUARD-FILES.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build(root: Path, lock_it=True) -> Bed:
    b = Bed(root)
    b.data.mkdir(parents=True)
    (root / "SkyrimVR.exe").write_bytes(b"MZ")
    for n in VR_FILES:
        b.path(n).write_bytes(os.urandom(512))
    (root / "CLAUDE.md").write_text("# claude\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("# agents\n", encoding="utf-8")
    hooks = root / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    for h in health.CLAUDE_HOOKS:
        (hooks / (h + ".sh")).write_text("#!/bin/bash\necho %s\n" % h, encoding="utf-8", newline="\n")
        _complete(b.hb("claude"), h, b.now - 10)
    shutil.copyfile(str(REPO / "agent" / "targets" / "claude" / "settings.json"), str(root / ".claude" / "settings.json"))
    (root / ".codex" / "rules").mkdir(parents=True)
    win = str(root.resolve()).replace(chr(92), "/")
    tpl = (REPO / "agent" / "targets" / "codex" / "hooks.json").read_text(encoding="utf-8")
    (root / ".codex" / "hooks.json").write_text(
        tpl.replace("{{CODEX_ROOT_POSIX}}", "/x" + win).replace("{{CODEX_ROOT}}", win), encoding="utf-8", newline="\n")
    (root / ".codex" / "rules" / "skyrim.rules").write_text("# rules\n", encoding="utf-8")
    b.cfg.parent.mkdir(parents=True)
    lines = ['model = "x"', "", "[hooks.state]", ""]
    for k, h in ct.expected_entries(root / ".codex" / "hooks.json").items():
        lines += ["[hooks.state.'{}']".format(k), 'trusted_hash = "{}"'.format(h), ""]
    b.cfg.write_text("\n".join(lines), encoding="utf-8")
    sessions = b.hb("codex") / "sessions"
    sessions.mkdir(parents=True)
    (sessions / (SID + ".log")).write_text("1800000000.0 start startup {}\n1800000001.0 pre-tool Bash\n"
                                           "1800000002.0 pre-tool apply_patch\n".format(EXEC), encoding="utf-8")
    b.home.mkdir()
    b.manifest()
    if lock_it and WINDOWS:
        from skyguard import lock
        res = lock.lock_all(str(root))
        assert res.exit == 0, res.lines
    return b


@pytest.fixture
def bed(monkeypatch):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = SCRATCH / ("d16-%s-%d" % (uuid.uuid4().hex[:6], next(_ids)))
    monkeypatch.setenv("SKYGUARD_LOCK_SANDBOX", str(SCRATCH))
    monkeypatch.delenv("SKYGUARD_SESSION_ID", raising=False)
    try:
        yield _build(root)
    finally:
        _scrub(root)


def _lift(kind, created, expires, files=None, session=SESSION, reason="testing", agent="claude"):
    x = {"kind": kind, "reason": reason, "session_id": session, "agent": agent, "created": created,
         "expires_at": expires}
    if files is not None:
        x["files"] = files
    return x


def _render(rows):
    out = io.StringIO()
    _doctor().render(rows, out)
    return out.getvalue()


# ---- the whole report --------------------------------------------------------------------------------------------

@needs_windows
def test_a_fully_live_install_exits_0_and_every_line_is_live(bed):
    rows = bed.rows()
    bad = [(r.section, r.item, r.status, r.reason) for r in rows if r.counted and r.status != "live"]
    assert bad == []
    assert _doctor().exit_code(rows) == 0
    sections = {r.section for r in rows}
    assert {"Layer 0", "Layer 1", "Layer 2", "Layer 3 claude", "Layer 3 codex", "Guards", "Integrity"} <= sections
    assert sorted(r.item for r in rows if r.section == "Layer 2" and r.label == "locked") == sorted(VR_FILES)


@needs_windows
def test_every_line_has_a_status_and_a_reason_never_blank(bed):
    bed.toolchain = (2, "")                                # unknown, so a reason has to be invented for it
    bed.state_write([_lift("guards", bed.now - 5, bed.now + 600)])
    text = _render(bed.rows())
    body = [ln for ln in text.splitlines() if ln.startswith("  ")]
    assert len(body) >= 20
    for ln in body:
        parts = ln.strip().split(" | ")
        assert len(parts) == 3, ln
        assert parts[1] in ("live", "not live", "unknown"), ln
        assert parts[0].strip() and parts[2].strip(), ln


def test_the_row_constructor_refuses_a_blank_reason():
    d = _doctor()
    with pytest.raises(ValueError):
        d.Row("Layer 1", "x", "live", "  ")
    with pytest.raises(ValueError):
        d.Row("Layer 1", "x", "maybe", "why")


def test_exit_codes_not_live_outranks_unknown():
    d = _doctor()
    live = d.Row("A", "a", "live", "ok")
    unk = d.Row("A", "b", "unknown", "cannot tell")
    bad = d.Row("A", "c", "not live", "broken")
    gap = d.Row("Layer 2", "gap", "not live", "measured", counted=False)
    assert d.exit_code([live, gap]) == 0
    assert d.exit_code([live, unk]) == 2
    assert d.exit_code([live, bad]) == 1
    assert d.exit_code([unk, bad, live]) == 1


# ---- Layer 0 / Layer 1 -------------------------------------------------------------------------------------------

@needs_windows
def test_layer0_a_degraded_toolchain_is_not_live(bed):
    bed.toolchain = (1, "  java   NOT INSTALLED\nRESULT: DEGRADED -- java\n")
    r = bed.row("toolchain")
    assert r.status == "not live" and "java" in r.reason
    assert bed.code() == 1


@needs_windows
def test_layer0_a_toolchain_check_that_cannot_run_is_unknown(bed):
    bed.toolchain = (None, "no bash found")
    r = bed.row("toolchain")
    assert r.status == "unknown" and "no bash" in r.reason
    assert bed.code() == 2


@needs_windows
def test_layer1_agents_md_over_the_budget_is_not_live_and_the_cap_itself_is_live(bed):
    (bed.root / "AGENTS.md").write_bytes(b"x" * 32768)
    assert bed.row("AGENTS.md").status == "live"
    (bed.root / "AGENTS.md").write_bytes(b"x" * 32769)
    r = bed.row("AGENTS.md")
    assert r.status == "not live" and "32,769" in r.reason and "32,768" in r.reason


@needs_windows
def test_layer1_an_empty_instruction_file_is_not_live(bed):
    """Task 16 re-review: a 0-byte CLAUDE.md read `live | present (0 bytes)` -- live on an empty set."""
    (bed.root / "CLAUDE.md").write_bytes(b"")
    r = bed.row("CLAUDE.md")
    assert r.status == "not live" and "empty" in r.reason, r
    assert bed.code() == 1


@needs_windows
def test_layer1_a_missing_instruction_file_is_not_live(bed):
    (bed.root / "CLAUDE.md").unlink()
    assert bed.row("CLAUDE.md").status == "not live"
    assert bed.code() == 1


# ---- Layer 2 -----------------------------------------------------------------------------------------------------

@needs_windows
def test_layer2_a_lifted_file_reads_unlocked_with_its_lift_time(bed, monkeypatch):
    from skyguard import lock
    monkeypatch.setenv("SKYGUARD_AGENT", "claude")
    res = lock.unlock(str(bed.root), ["Update.esm"], SESSION, now=bed.now)
    assert res.exit == 0, res.lines
    r = bed.row("Update.esm")
    want = time.strftime("%Y-%m-%d %H:%M", time.localtime(bed.now + lock.LIFT_SECONDS))
    assert r.label == "unlocked (lift until %s)" % want, r.label
    assert r.status == "not live" and SESSION in r.reason
    assert bed.row("Skyrim.esm").label == "locked"
    assert bed.code() == 1


@needs_windows
def test_layer2_a_file_opened_outside_skyguard_says_so(bed):
    from skyguard import lock
    lock._remove_file(os.path.normpath(str(bed.path("Dawnguard.esm"))), False)      # no lift recorded
    r = bed.row("Dawnguard.esm")
    assert r.label == "unlocked (outside skyguard)" and r.status == "not live"


@needs_windows
def test_layer2_a_lift_for_another_file_does_not_explain_this_one(bed):
    from skyguard import lock
    bed.state_write([_lift("lock", bed.now - 5, bed.now + 600, files=["Update.esm"])])
    lock._remove_file(os.path.normpath(str(bed.path("Dawnguard.esm"))), False)
    assert bed.row("Dawnguard.esm").label == "unlocked (outside skyguard)"


@needs_windows
def test_layer2_a_multilink_target_is_skipped(bed):
    _scrub_one(bed, "Skyrim - Interface.bsa")
    os.link(str(bed.path("Skyrim - Interface.bsa")), str(bed.root / "staging-copy.bsa"))
    r = bed.row("Skyrim - Interface.bsa")
    assert r.label == "skipped: multi-link" and r.status == "not live"


def _scrub_one(bed, name):
    p = str(bed.path(name))
    subprocess.run([str(SYS32 / "icacls.exe"), p, "/remove:d", "*" + _sid()], capture_output=True)
    subprocess.run([str(SYS32 / "attrib.exe"), "-R", p], capture_output=True)


@needs_windows
def test_layer2_a_locked_file_that_vanished_is_missing(bed):
    _scrub_one(bed, "Dawnguard.esm")
    bed.path("Dawnguard.esm").unlink()
    r = bed.row("Dawnguard.esm")
    assert r.label == "missing" and r.status == "not live"
    # an edition name that was never there is not a row of its own
    assert not [x for x in bed.rows() if x.item == "HearthFires.esm"]


@needs_windows
def test_layer2_a_persistent_disable_is_named_with_its_date(bed):
    since = bed.now - 86400
    bed.state_write(persistent={"lock_disabled": {"since": since, "by_session": "sess-x"}})
    r = bed.row("persistent disable")
    assert r.status == "not live"
    assert r.reason.startswith("disabled since " + time.strftime("%Y-%m-%d", time.localtime(since)))
    assert "sess-x" in r.reason


@needs_windows
def test_layer2_mechanism_and_a_never_applied_lock(bed):
    r = bed.row("lock")
    assert r.status == "live" and "M-D" in r.reason
    (bed.root / ".skyguard" / "lock.json").rename(bed.root / ".skyguard" / "lock.json.moved")
    for n in VR_FILES:
        _scrub_one(bed, n)
    r = bed.row("lock")
    assert r.status == "not live" and "never applied" in r.reason
    assert not [x for x in bed.rows() if x.item in VR_FILES]


@needs_windows
def test_layer2_the_honest_gap_list_is_printed_and_not_counted(bed):
    gaps = [r for r in bed.rows() if r.section == "Layer 2 gaps"]
    assert {g.item.split()[0] for g in gaps} >= {"P32", "D7"}
    assert all(not g.counted for g in gaps)
    assert all(g.status == "not live" for g in gaps if g.item.split()[0] in ("P32", "D7"))
    assert bed.code() == 0


# ---- R9: the one write -------------------------------------------------------------------------------------------

def _snapshot(root: Path):
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


@needs_windows
def test_the_report_writes_nothing_when_nothing_expired(bed):
    bed.state_write([_lift("guards", bed.now - 5, bed.now + 600)])
    before = _snapshot(bed.root)
    rows, cleared = bed.collect()
    _render(rows)
    assert cleared == []
    assert _snapshot(bed.root) == before


@needs_windows
def test_an_expired_lift_is_cleared_and_logged(bed):
    live = _lift("guards", bed.now - 5, bed.now + 600)
    old = _lift("lock", bed.now - 7200, bed.now - 3600, files=["Update.esm"])
    capped = _lift("lock", bed.now - 9 * 3600, bed.now + 600, files=["Skyrim.esm"])      # past the 8 h cap
    bed.state_write([live, old, capped])
    rows, cleared = bed.collect()
    assert len(cleared) == 2
    assert state.read(str(bed.root / ".skyguard" / "state.json")).lifts == [live]
    log = (bed.root / ".claude" / "backups" / "AUDIT_LOG.txt").read_text(encoding="utf-8")
    assert "skydoctor" in log and "cleared 2 expired lift" in log
    assert any(r.item == "expired lifts" and "cleared 2" in r.reason for r in rows)


@needs_windows
def test_a_rejected_state_is_never_rewritten(bed):
    sf = bed.root / ".skyguard" / "state.json"
    bed.state_write([_lift("lock", bed.now - 7200, bed.now - 3600)])
    data = json.loads(sf.read_text(encoding="utf-8"))
    data["sum"] = "0" * 64
    sf.write_text(json.dumps(data), encoding="utf-8")
    before = sf.read_bytes()
    rows, cleared = bed.collect()
    assert cleared == [] and sf.read_bytes() == before


# ---- Guards / Integrity ------------------------------------------------------------------------------------------

@needs_windows
def test_guards_off_names_who_why_and_until(bed):
    bed.state_write([_lift("guards", bed.now - 5, bed.now + 600, reason="guards off", agent="codex")])
    r = bed.row("guards")
    assert r.status == "not live" and r.label.startswith("OFF")
    assert "codex" in r.reason and SESSION in r.reason and "guards off" in r.reason
    assert time.strftime("%H:%M", time.localtime(bed.now + 600)) in r.reason


@needs_windows
def test_a_rejected_state_reads_state_rejected(bed):
    sf = bed.root / ".skyguard" / "state.json"
    sf.write_text("{not json", encoding="utf-8")
    r = bed.row("state file")
    assert r.status == "not live" and r.reason.startswith("STATE-REJECTED")
    assert bed.row("guards").status == "live"
    assert bed.code() == 1


@needs_windows
def test_integrity_an_edited_guard_file_is_named(bed):
    with open(str(bed.root / ".claude" / "hooks" / "protect-bash.sh"), "a", encoding="utf-8") as fh:
        fh.write("exit 0\n")
    r = bed.row("guard files")
    assert r.status == "not live" and "protect-bash.sh" in r.reason


@needs_windows
def test_integrity_without_a_manifest_is_unknown(bed):
    (bed.root / "GUARD-FILES.sha256").unlink()
    assert bed.row("guard files").status == "unknown"


# ---- Layer 3 -----------------------------------------------------------------------------------------------------

@needs_windows
def test_claude_an_unwired_hook_is_not_live(bed):
    sp = bed.root / ".claude" / "settings.json"
    sp.write_text(sp.read_text(encoding="utf-8").replace("protect-mcp.sh", "protect-mcp.sh.off"), encoding="utf-8")
    r = bed.row("protect-mcp")
    assert r.status == "not live" and "not wired" in r.reason


@needs_windows
def test_claude_a_degraded_hook_is_not_live_and_an_unexercised_one_unknown(bed):
    hb = bed.hb("claude")
    (hb / "protect-files.failed").write_text("{} 9.9-9 python crash\n".format(bed.now - 1), encoding="utf-8")
    assert bed.row("protect-files").status == "not live"
    for stage in health.STAGES:
        p = hb / ("protect-bash." + stage)
        if p.exists():
            p.unlink()
    r = bed.row("protect-bash")
    assert r.status == "unknown" and "NOT EXERCISED" in r.reason


@needs_windows
def test_claude_disable_all_hooks_is_not_live(bed):
    (bed.home / ".claude").mkdir()
    (bed.home / ".claude" / "settings.json").write_text('{"disableAllHooks": true}', encoding="utf-8")
    r = bed.row("hook switch")
    assert r.status == "not live" and "disableAllHooks" in r.reason


@needs_windows
def test_codex_untrusted_hooks_are_not_live(bed):
    bed.cfg.write_text('model = "x"\n', encoding="utf-8")
    r = bed.row("trust")
    assert r.status == "not live" and "untrusted" in r.reason


@needs_windows
def test_codex_trust_it_cannot_read_is_unknown(bed):
    bed.cfg.write_bytes(b"\xff\xfe[[[ not toml")
    assert bed.row("trust").status == "unknown"


@needs_windows
def test_codex_a_missing_rules_file_is_not_live(bed):
    (bed.root / ".codex" / "rules" / "skyrim.rules").unlink()
    assert bed.row(".rules").status == "not live"


@needs_windows
def test_codex_coverage_short_is_not_live_and_none_recorded_is_unknown(bed):
    log = bed.hb("codex") / "sessions" / (SID + ".log")
    log.write_text("1800000000.0 start startup {}\n1800000001.0 pre-tool Bash\n".format(EXEC), encoding="utf-8")
    r = [x for x in bed.rows() if x.section == "Layer 3 codex" and x.item.startswith("coverage")]
    assert len(r) == 1 and r[0].status == "not live" and "hooked 1 / called 2" in r[0].reason
    log.unlink()
    r = [x for x in bed.rows() if x.section == "Layer 3 codex" and x.item.startswith("coverage")]
    assert len(r) == 1 and r[0].status == "unknown"


# ---- the launchers, from the outside -----------------------------------------------------------------------------

def _env(bed, **extra):
    env = dict(os.environ)
    env.update({"SKYGUARD_LOCK_SANDBOX": str(SCRATCH), "CODEX_HOME": str(bed.cfg.parent),
                "USERPROFILE": str(bed.home), "HOME": str(bed.home), "SKYGUARD_PYTHON": sys.executable})
    env.update(extra)
    return env


@needs_windows
def test_the_bash_launcher_reports_json_and_exits_by_the_worst_line(bed):
    r = subprocess.run([BASH, str(REPO / "tools" / "skydoctor"), "--root", str(bed.root), "--json", "--no-toolchain"],
                       capture_output=True, text=True, env=_env(bed), stdin=subprocess.DEVNULL, timeout=300)
    data = json.loads(r.stdout)
    assert r.returncode == 2 == data["exit"], (r.stdout[-2000:], r.stderr[-2000:])
    t = [x for x in data["rows"] if x["item"] == "toolchain"][0]
    assert t["status"] == "unknown" and "--no-toolchain" in t["reason"]
    assert [x for x in data["rows"] if x["item"] == "Update.esm"][0]["label"] == "locked"


@needs_windows
def test_the_cmd_twin_runs(bed):
    env = _env(bed)
    r = subprocess.run("%s /d /c %s --root %s --no-toolchain" % (SYS32 / "cmd.exe", REPO / "tools" / "skydoctor.cmd",
                                                                bed.root),
                       capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=300)
    assert r.returncode == 2, (r.stdout[-2000:], r.stderr[-2000:])
    assert "Update.esm | live | " in r.stdout and "RESULT:" in r.stdout


# ---- fix round 1 (task-16-review.md) -----------------------------------------------------------------------------

@needs_windows
@pytest.mark.parametrize("text", ["", "# release 9\ngarbage line\nnot-a-hash  .claude/hooks/protect-bash.sh\n",
                                  "# release 9\n" + "a" * 64 + "  ../outside.sh\n"], ids=["empty", "garbage", "outside"])
def test_i1_integrity_is_never_live_on_an_empty_set(bed, text):
    (bed.root / "GUARD-FILES.sha256").write_text(text, encoding="utf-8")
    r = bed.row("guard files")
    assert r.status == "unknown" and "no guard file was compared" in r.reason, r


@needs_windows
def test_i2_a_lock_with_nothing_present_is_not_live(bed):
    for n in VR_FILES:
        _scrub_one(bed, n)
        bed.path(n).unlink()
    (bed.root / ".skyguard" / "lock.json").write_text('{"v": 1, "files": {}}', encoding="utf-8")
    r = bed.row("lock")
    assert r.status == "not live" and "no target file is present" in r.reason, r


@needs_windows
def test_m1_the_lock_is_not_live_while_a_disable_cannot_be_read(bed):
    (bed.root / ".skyguard" / "state.json").write_text("junk", encoding="utf-8")
    assert bed.row("persistent disable").status == "unknown"
    r = bed.row("lock")
    assert r.status == "unknown" and "cannot be read" in r.reason, r


@needs_windows
def test_i3_a_state_file_that_cannot_be_rewritten_reads_unknown_not_a_crash(bed):
    sf = bed.root / ".skyguard" / "state.json"
    bed.state_write([_lift("lock", bed.now - 7200, bed.now - 3600, files=["Update.esm"])])
    subprocess.run([str(SYS32 / "attrib.exe"), "+R", str(sf)], capture_output=True, check=True)
    try:
        rows, cleared = bed.collect()
    finally:
        subprocess.run([str(SYS32 / "attrib.exe"), "-R", str(sf)], capture_output=True)
    assert cleared == []
    r = [x for x in rows if x.item == "expired lifts"]
    assert len(r) == 1 and r[0].status == "unknown" and "PermissionError" in r[0].reason, r
    assert _doctor().exit_code(rows) == 2 or any(x.status == "not live" and x.counted for x in rows)
    assert not list(sf.parent.glob(".state-*.tmp"))


@needs_windows
def test_i3_one_failing_layer_reads_unknown_and_hides_no_other(bed, monkeypatch):
    from skyguard import doctor

    def boom(*a, **k):
        raise OSError("disk went away")
    monkeypatch.setattr(doctor.health, "_coverage_rows", boom)
    rows = bed.rows()
    codex = [x for x in rows if x.section == "Layer 3 codex"]
    assert [x.status for x in codex if x.item == "check"] == ["unknown"]
    assert any("OSError" in x.reason for x in codex)
    assert bed.row("Update.esm").status == "live" and bed.row("guard files").status == "live"
    assert _doctor().exit_code(rows) == 2


@needs_windows
def test_i3_main_turns_an_unexpected_exception_into_exit_2(bed, monkeypatch):
    from skyguard import doctor

    def boom(*a, **k):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(doctor, "collect", boom)
    out = io.StringIO()
    assert doctor.main(["--root", str(bed.root), "--no-toolchain"], out=out) == 2
    assert "RuntimeError" in out.getvalue()


@needs_windows
@pytest.mark.parametrize("cmd", ["bash .claude/hooks/{h}.sh", 'bash ".claude/hooks/{h}.sh"'], ids=["bare", "quoted"])
def test_m2_a_relative_hook_command_is_wired(bed, cmd):
    sp = bed.root / ".claude" / "settings.json"
    data = json.loads(sp.read_text(encoding="utf-8"))
    for groups in data["hooks"].values():
        for g in groups:
            for h in g["hooks"]:
                if "protect-mcp.sh" in h["command"]:
                    h["command"] = cmd.format(h="protect-mcp")
    sp.write_text(json.dumps(data), encoding="utf-8")
    assert "wired" in bed.row("protect-mcp").reason and bed.row("protect-mcp").status == "live"


def _alive(pid):
    out = subprocess.run([str(SYS32 / "tasklist.exe"), "/FI", "PID eq %d" % pid, "/NH"], capture_output=True,
                         text=True).stdout
    return str(pid) in out


@needs_windows
def test_m3_a_toolchain_timeout_kills_the_whole_tree_by_pid(tmp_path, monkeypatch):
    from skyguard import doctor
    root = tmp_path / "inst"
    (root / "tools").mkdir(parents=True)
    pidf = tmp_path / "child.pid"
    py = sys.executable.replace(chr(92), "/")
    (root / "tools" / "toolchain-check.sh").write_text(
        '"%s" -c "import os,time; open(r\'%s\',\'w\').write(str(os.getpid())); time.sleep(120)" &\nwait\n'
        % (py, str(pidf)), encoding="utf-8", newline="\n")
    monkeypatch.setattr(doctor, "TOOLCHAIN_TIMEOUT_S", 4)
    monkeypatch.setenv("SKYDOCTOR_BASH", BASH)
    t0 = time.time()
    rc, out = doctor.run_toolchain(str(root))
    pid = int(pidf.read_text())
    try:
        assert rc is None and "timed out" in out
        assert time.time() - t0 < 60
        time.sleep(0.5)
        assert not _alive(pid), "the grandchild outlived the timeout"
    finally:
        subprocess.run([str(SYS32 / "taskkill.exe"), "/F", "/PID", str(pid)], capture_output=True)
