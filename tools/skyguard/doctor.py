#!/usr/bin/env python3
"""skydoctor -- what is LIVE on this install, layer by layer (Plan 6 Task 16).

    bash tools/skydoctor [--root R] [--json] [--no-toolchain] [--codex-config P]      (tools\\skydoctor.cmd on cmd)

One report. Every line is `<item> | live | not live | unknown | <reason>`, and the reason is never blank:

  Layer 0          the documented CLIs start (tools/toolchain-check.sh, run as it is).
  Layer 1          CLAUDE.md and AGENTS.md are present; AGENTS.md is within the 32,768 bytes Codex reads.
  Layer 2          per target: `locked` / `unlocked (lift until ...)` / `unlocked (outside skyguard)` /
                   `skipped: multi-link` / `missing`; the mechanism; `disabled since ...`. Then the honest gap list:
                   the rows Table A recorded NOT BLOCKED for the shipped mechanism. Those are printed with their
                   status but NOT COUNTED in the exit code: they are permanent, so counting them would make 0 unreachable.
  Layer 3 claude   per hook: wired in .claude/settings.json + Plan 4's health.hook_state; the disableAllHooks switch.
  Layer 3 codex    codex_trust; .codex/rules/skyrim.rules present; per-session coverage.
  Guards           on/off, who, why, until; the state file itself (`STATE-REJECTED <reason>` when it cannot be trusted).
  Integrity        the guard files against GUARD-FILES.sha256 (session.py's own check).

Exit: 1 when any counted line is `not live`; else 2 when any is `unknown`; else 0. A known failure outranks an
unproven line, as in health.report.

It WRITES NOTHING, with one exception (R9): a lift past its expiry (or the 8-hour cap) is removed from
.skyguard/state.json through state.write, a line is appended to .claude/backups/AUDIT_LOG.txt, and the report says so.
A state file that does not verify is never rewritten. Nothing is relocked here: the next session start, or
`tools/skyguard lock lock`, does that. (health.report is not reused whole for the same reason: it probes the telemetry
folder by writing a file into it.)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from skyguard import health, lock, session, state  # noqa: E402

LIVE, NOT_LIVE, UNKNOWN = "live", "not live", "unknown"
STATUSES = (LIVE, NOT_LIVE, UNKNOWN)
AGENTS_MD_MAX = 32768          # Codex silently truncates AGENTS.md past this (scripts/gen_agent_trees.py)
TOOLCHAIN_TIMEOUT_S = 180
HOOK_STATUS = {"HEALTHY": LIVE, "DEGRADED": NOT_LIVE, "NEVER INVOKED": NOT_LIVE,
               "STALE": UNKNOWN, "NOT EXERCISED": UNKNOWN, "UNMEASURABLE": UNKNOWN}
COVERAGE_STATUS = {"HEALTHY": LIVE, "DEGRADED": NOT_LIVE, "NOT EXERCISED": UNKNOWN, "UNMEASURABLE": UNKNOWN}

# Table A, M-D column (docs/superpowers/measurements/2026-10-04-plan6-ml2.md, Task 1 + C1-C4): the rows NOT BLOCKED.
GAPS = (
    ("P32 / C3 xEdit or xelib save of a locked master", NOT_LIVE,
     "the save renames the locked file into Data/zEdit Backups/<name>.<stamp>.bak and writes a new one; the "
     "original bytes survive as the .bak (MEASURED, Task 3)"),
    ("D7 rename-away (mv <master> <master>.bak)", NOT_LIVE,
     "renaming needs only DELETE, which no mechanism denies; the locked object lives on under the new name "
     "(MEASURED, Task 1)"),
    ("WSL tools", UNKNOWN, "never measured: no WSL distro on the measuring machine (L12)"),
)


@dataclass
class Row:
    section: str
    item: str
    status: str
    reason: str
    label: str = ""
    counted: bool = True

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError("status must be one of %s, not %r" % (STATUSES, self.status))
        if not str(self.reason).strip():
            raise ValueError("a report line needs a reason: %s %s" % (self.section, self.item))


def _txt(s, n=150):
    """A line of the toolkit's OWN text (a script's output, a reader's detail): control characters dropped, whitespace
    collapsed, length capped. Strings recorded in state.json go through session._clean instead, which keeps far less."""
    t = " ".join("".join(c if c.isprintable() else " " for c in str(s)).split())
    return t if len(t) <= n else t[: n - 3] + "..."


def _when(t, fmt="%Y-%m-%d %H:%M"):
    try:
        return time.strftime(fmt, time.localtime(t))
    except (OverflowError, OSError, ValueError):
        return "an unknown time"


def _expired(lift, now) -> bool:
    return not now < min(lift["expires_at"], lift["created"] + state.MAX_LIFT_SECONDS)


# ---- R9: the one write -------------------------------------------------------------------------------------------

def clear_expired(root, now):
    """Remove the expired lifts from a state file that verifies; log it. -> the removed lifts ([] when none, when
    there is no state file, or when it does not verify -- a rejected file is never rewritten)."""
    sf = Path(root) / ".skyguard" / "state.json"
    if not os.path.lexists(str(sf)):
        return []
    st = state.read(str(sf))
    if st.rejected:
        return []
    gone = [x for x in st.lifts if _expired(x, now)]
    if not gone:
        return []
    keep = [x for x in st.lifts if not _expired(x, now)]
    persistent = {"lock_disabled": dict(st.lock_disabled)} if st.lock_disabled else {}
    lock._assert_sandbox(sf)
    state.write(str(sf), {"v": 1, "lifts": keep, "persistent": persistent})
    from skyguard.audit import Audit
    what = ", ".join(session._clean("%s %s" % (x["kind"], x["session_id"]), 60) for x in gone[:5])
    Audit(str(root)).log("skydoctor", "INFO", "cleared %d expired lift(s) (R9): %s" % (len(gone), what))
    return gone


# ---- Layer 0 / Layer 1 -------------------------------------------------------------------------------------------

def _find_bash():
    """$SKYDOCTOR_BASH (the bash launcher exports its own), else Git for Windows' bash, else a `bash` on PATH that
    is not System32's WSL launcher."""
    cand = os.environ.get("SKYDOCTOR_BASH")
    if cand and os.path.isfile(cand):
        return cand
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432"), r"C:\Program Files"):
            if base and os.path.isfile(os.path.join(base, "Git", "bin", "bash.exe")):
                return os.path.join(base, "Git", "bin", "bash.exe")
    found = shutil.which("bash")
    if found and "system32" not in found.lower():
        return found
    return None


def run_toolchain(root):
    """-> (exit code or None, output). None = the check could not be run."""
    script = Path(root) / "tools" / "toolchain-check.sh"
    if not script.is_file():
        return None, "tools/toolchain-check.sh is not in this install"
    bash = _find_bash()
    if not bash:
        return None, "no bash found to run tools/toolchain-check.sh"
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    try:
        r = subprocess.run([bash, str(script)], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
                           timeout=TOOLCHAIN_TIMEOUT_S, errors="replace")
    except (OSError, subprocess.SubprocessError) as e:
        return None, "tools/toolchain-check.sh did not run (%s)" % e.__class__.__name__
    return r.returncode, r.stdout


def _layer0(root, toolchain):
    if toolchain is False:
        return [Row("Layer 0", "toolchain", UNKNOWN, "not checked (--no-toolchain)")]
    rc, out = (toolchain or run_toolchain)(root)
    result = [ln.strip() for ln in str(out).splitlines() if ln.strip().startswith("RESULT:")]
    said = _txt(result[-1]) if result else ""
    if rc == 0:
        return [Row("Layer 0", "toolchain", LIVE, said or "tools/toolchain-check.sh exit 0: every documented tool starts")]
    if rc == 1:
        return [Row("Layer 0", "toolchain", NOT_LIVE, said or "tools/toolchain-check.sh exit 1: a documented tool cannot start")]
    why = _txt(out) if rc is None else "tools/toolchain-check.sh exit %s: it could not check" % rc
    return [Row("Layer 0", "toolchain", UNKNOWN, why or "tools/toolchain-check.sh gave no answer")]


def _layer1(root):
    rows = []
    for name in ("CLAUDE.md", "AGENTS.md"):
        p = Path(root) / name
        try:
            size = os.stat(str(p)).st_size
        except FileNotFoundError:
            rows.append(Row("Layer 1", name, NOT_LIVE, "missing: the agent starts with no instructions"))
            continue
        except OSError as e:
            rows.append(Row("Layer 1", name, UNKNOWN, "cannot be read (%s)" % e.__class__.__name__))
            continue
        if name == "AGENTS.md" and size > AGENTS_MD_MAX:
            rows.append(Row("Layer 1", name, NOT_LIVE, "{:,} bytes > {:,}: Codex silently truncates the rest".format(
                size, AGENTS_MD_MAX)))
        else:
            rows.append(Row("Layer 1", name, LIVE, "present ({:,} bytes)".format(size)))
    return rows


# ---- Layer 2 -----------------------------------------------------------------------------------------------------

def _lift_for(lifts, name, now):
    """The newest unexpired lock lift that names this file (or every file), or None."""
    hits = [x for x in lifts if x["kind"] == "lock" and not _expired(x, now)
            and (x.get("files") is None or name.lower() in (f.lower() for f in x["files"]))]
    return max(hits, key=lambda x: x["expires_at"]) if hits else None


def _layer2(root, st, now, cleared):
    S = "Layer 2"
    rows = []
    if not lock.supported():
        return [Row(S, "lock", NOT_LIVE, "unsupported on this platform (Windows only in v4.0.0, R11)")]
    data_dir = Path(root) / "Data"
    if not data_dir.is_dir():
        return [Row(S, "lock", UNKNOWN, "no Data/ folder under the install root")]
    t = lock.targets(data_dir)
    if t.problem:
        return [Row(S, "lock", UNKNOWN, _txt(t.problem))]
    manifest, mstatus = lock._read_manifest(root)
    if st.rejected:
        rows.append(Row(S, "persistent disable", UNKNOWN, "the state file was rejected, so a disable cannot be read"))
    elif st.lock_disabled:
        rows.append(Row(S, "persistent disable", NOT_LIVE, "disabled since %s by session %s; `tools/skyguard lock "
                        "enable` turns it back on" % (_when(st.lock_disabled["since"]),
                                                      session._clean(st.lock_disabled["by_session"], 60))))
    else:
        rows.append(Row(S, "persistent disable", LIVE, "not disabled"))
    files, marked = [], False
    for p in t.files:
        try:
            files.append((p, lock.read_state(p)))
            marked = marked or files[-1][1].ours is not None
        except OSError as e:
            files.append((p, e))
    engaged = mstatus != "absent" or marked
    missing = [n for n, r in t.skips if r == "missing"]
    head = "mechanism %s (read-only bit + this user's deny (WD,AD,WA,WEA)), edition %s, default %s" % (
        lock.MECHANISM, t.edition, lock.DEFAULT)
    absent = "; %d edition name(s) absent from Data/, nothing to lock there" % len(
        [n for n in missing if n not in manifest]) if missing else ""
    if not engaged:
        rows.insert(0, Row(S, "lock", NOT_LIVE, "never applied here (off by default, R3); `tools/skyguard lock lock` "
                           "applies it. " + head + absent))
        return rows + _gaps()
    per = []
    cleared_names = {n.lower() for x in cleared if x["kind"] == "lock" for n in (x.get("files") or ["*"])}
    for p, fs in files:
        if isinstance(fs, OSError):
            per.append(Row(S, p.name, UNKNOWN, "its attributes or ACL cannot be read (%s)" % fs.__class__.__name__))
            continue
        if fs.ours is not None and fs.readonly:
            why = "read-only bit + the toolkit's deny mark, re-read from the file"
            if fs.ours == "pre":
                why += " (it was read-only before the lock, so it stays read-only after an unlock)"
            per.append(Row(S, p.name, LIVE, why, label="locked"))
            continue
        lift = None if st.rejected else _lift_for(st.lifts, p.name, now)
        if lift is not None:
            per.append(Row(S, p.name, NOT_LIVE, "lifted by session %s (agent %s): %s" % (
                session._clean(lift["session_id"], 60), session._clean(lift["agent"], 20),
                session._clean(lift["reason"], 60)), label="unlocked (lift until %s)" % _when(
                min(lift["expires_at"], lift["created"] + state.MAX_LIFT_SECONDS))))
            continue
        if fs.ours is not None:
            why = "the toolkit's deny mark is there but the read-only bit was cleared"
        elif fs.readonly:
            why = "read-only, but without the toolkit's mark"
        elif p.name.lower() in cleared_names or "*" in cleared_names:
            why = ("its lift expired and this run cleared it; nothing relocks until the next session start or "
                   "`tools/skyguard lock lock`")
        else:
            why = "no lift on record explains it; `tools/skyguard lock lock` relocks"
        per.append(Row(S, p.name, NOT_LIVE, why, label="unlocked (outside skyguard)"))
    for name, reason in t.skips:
        if reason == "missing":
            if name in manifest:
                per.append(Row(S, name, NOT_LIVE, "the toolkit locked it and it is gone from Data/ (renamed into a "
                               "backup, or deleted)", label="missing"))
        elif reason.startswith("multi-link"):
            per.append(Row(S, name, NOT_LIVE, "a hardlinked mod-manager copy: an ACL would land on the manager's "
                           "staging file too, so the lock never touches it", label="skipped: multi-link"))
        elif reason.startswith(("cannot be opened", "unreadable")):
            per.append(Row(S, name, UNKNOWN, _txt(reason, 120), label="skipped: " + _txt(reason, 40)))
        else:
            per.append(Row(S, name, NOT_LIVE, "the lock never touches a " + _txt(reason, 60),
                           label="skipped: " + _txt(reason, 40)))
    try:
        travelled = lock._travelled(data_dir)
    except OSError:
        travelled = []
    for p in travelled[:20]:
        per.append(Row(S, os.path.relpath(str(p), str(data_dir)), NOT_LIVE, "a locked file moved into a backup "
                       "(xEdit save or Vortex collision); its mark travelled with it", label="moved"))
    locked_n = sum(1 for r in per if r.label == "locked")
    whole = Row(S, "lock", LIVE if (locked_n == len(per) and not st.lock_disabled) else NOT_LIVE,
                "%d of %d targets locked; %s%s" % (locked_n, len(per), head, absent))
    return [whole] + rows + per + _gaps()


def _gaps():
    return [Row("Layer 2 gaps", item, status, why + " -- a measured limit, not counted in the exit code",
                counted=False) for item, status, why in GAPS]


# ---- Layer 3 -----------------------------------------------------------------------------------------------------

def _load_json(p):
    """-> (dict or None, problem or None). An absent file is (None, None)."""
    try:
        with open(str(p), "rb") as fh:
            raw = fh.read(4 * 1024 * 1024)
    except FileNotFoundError:
        return None, None
    except OSError as e:
        return None, "unreadable (%s)" % e.__class__.__name__
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        return None, "not valid JSON"
    return (data, None) if isinstance(data, dict) else (None, "not a JSON object")


def _commands(settings):
    out = []
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    for groups in (hooks.values() if isinstance(hooks, dict) else []):
        for g in groups if isinstance(groups, list) else []:
            for h in (g.get("hooks") or []) if isinstance(g, dict) else []:
                if isinstance(h, dict) and isinstance(h.get("command"), str):
                    out.append(h["command"].replace("\\", "/"))
    return out


def _layer3_claude(root, now, home):
    S = "Layer 3 claude"
    rows = []
    switches, unknown = [], []
    for p in (Path(home) / ".claude" / "settings.json", Path(root) / ".claude" / "settings.json",
              Path(root) / ".claude" / "settings.local.json"):
        data, problem = _load_json(p)
        if problem:
            unknown.append("%s is %s" % (p.name if p.parent.parent != Path(home) else "~/.claude/" + p.name, problem))
        elif data is not None and data.get("disableAllHooks") is True:
            switches.append(str(p))
    if switches:
        rows.append(Row(S, "hook switch", NOT_LIVE, "disableAllHooks is true in %s: Claude runs NO hook" %
                        _txt(", ".join(switches))))
    elif unknown:
        rows.append(Row(S, "hook switch", UNKNOWN, _txt("; ".join(unknown))))
    else:
        rows.append(Row(S, "hook switch", LIVE, "disableAllHooks is not set (user, project, project local)"))
    settings, problem = _load_json(Path(root) / ".claude" / "settings.json")
    cmds = _commands(settings) if settings else []
    hb = Path(root) / health.HB_REL / "claude"
    for h in health.CLAUDE_HOOKS:
        if not (Path(root) / ".claude" / "hooks" / (h + ".sh")).is_file():
            rows.append(Row(S, h, NOT_LIVE, ".claude/hooks/%s.sh is missing" % h))
            continue
        if problem or settings is None:
            rows.append(Row(S, h, UNKNOWN, ".claude/settings.json is %s, so the wiring cannot be read" %
                            (problem or "missing")))
            continue
        wire = re.compile(r"/\.claude/hooks/" + re.escape(h) + r"\.sh(?=[\"'\s]|\Z)")
        if not any(wire.search(c) for c in cmds):
            rows.append(Row(S, h, NOT_LIVE, "not wired in .claude/settings.json: Claude never runs it"))
            continue
        st, detail = health.hook_state(hb, h, now)
        rows.append(Row(S, h, HOOK_STATUS.get(st, UNKNOWN), "wired; %s: %s" % (st, _txt(detail, 140))))
    return rows


def _layer3_codex(root, codex_config):
    S = "Layer 3 codex"
    hooks_json = Path(root) / ".codex" / "hooks.json"
    if not hooks_json.is_file():
        return [Row(S, "hooks", NOT_LIVE, ".codex/hooks.json is missing: Codex runs no toolkit hook here")]
    from skyguard import codex_trust
    cfg = Path(codex_config) if codex_config else codex_trust.codex_home() / "config.toml"
    rows = []
    try:
        rep = codex_trust.trust_report(hooks_json, cfg, Path(root))
    except Exception as e:  # noqa: BLE001 -- a report line, never a crash
        rep = {"overall": "unknown", "error": "the trust reader failed (%s)" % e.__class__.__name__}
    overall = rep.get("overall")
    if overall == "trusted":
        rows.append(Row(S, "trust", LIVE, "every toolkit hook is approved in Codex's config.toml at its current hash"))
    elif overall == "unknown":
        rows.append(Row(S, "trust", UNKNOWN, "cannot tell: %s" % _txt(rep.get("error") or "see "
                        "tools/skyguard/codex_trust.py report", 140)))
    elif overall == "unrunnable":
        rows.append(Row(S, "trust", NOT_LIVE, "unrunnable: %s" % _txt(rep.get("root_problem") or "?", 140)))
    else:
        rows.append(Row(S, "trust", NOT_LIVE, "%s: Codex skips such a hook silently; approve them with /hooks in Codex"
                        % session._clean(overall, 20)))
    if (Path(root) / ".codex" / "rules" / "skyrim.rules").is_file():
        rows.append(Row(S, ".rules", LIVE, ".codex/rules/skyrim.rules is present (Codex's own forbidden list)"))
    else:
        rows.append(Row(S, ".rules", NOT_LIVE, ".codex/rules/skyrim.rules is missing"))
    cov = health._coverage_rows(Path(root), "codex")
    if not cov:
        rows.append(Row(S, "coverage", UNKNOWN, "no Codex session recorded yet: nothing proves the hooks fire"))
    for sid, surface, hooked, called, cstate, why in cov:
        rows.append(Row(S, "coverage " + session._clean(sid, 13), COVERAGE_STATUS.get(cstate, UNKNOWN),
                        "%s: hooked %d / called %d, %s%s" % (session._clean(surface, 20), hooked, called, cstate,
                                                            (" (%s)" % _txt(why, 100)) if why else "")))
    return rows


# ---- Guards / Integrity ------------------------------------------------------------------------------------------

def _guards(st, sf_exists, now):
    rows = []
    if st.rejected:
        rows.append(Row("Guards", "state file", NOT_LIVE, "STATE-REJECTED %s: it is ignored, so guards are on and every "
                        "lift in it is void" % session._clean(st.reason, 80)))
        rows.append(Row("Guards", "guards", LIVE, "the rejected state file is ignored, so the guards decide", label="ON"))
        return rows
    rows.append(Row("Guards", "state file", LIVE, "verified (checksum and shape)" if sf_exists else
                    "absent: no lift and no disable recorded"))
    off = [x for x in st.lifts if x["kind"] == "guards" and not _expired(x, now)]
    if not off:
        rows.append(Row("Guards", "guards", LIVE, "no guards-off lift on record", label="ON"))
        return rows
    x = max(off, key=lambda y: y["expires_at"])
    more = " (+%d more)" % (len(off) - 1) if len(off) > 1 else ""
    rows.append(Row("Guards", "guards", NOT_LIVE, "shadow mode by agent %s, session %s, why \"%s\", until %s%s; "
                    "honoured only inside that session" % (
                        session._clean(x["agent"], 20), session._clean(x["session_id"], 60),
                        session._clean(x["reason"], 60),
                        _when(min(x["expires_at"], x["created"] + state.MAX_LIFT_SECONDS)), more), label="OFF"))
    return rows


def _integrity(root):
    gd = session._guard_drift(root)
    if gd is None:
        return [Row("Integrity", "guard files", UNKNOWN, "no readable %s: nothing to compare against" %
                    session.GUARD_MANIFEST)]
    release, differ, missing, unchecked = gd
    if differ or missing:
        tail = (" (%s)" % session._names(differ, 4)) if differ else ""
        tail += ("; %d missing" % missing) if missing else ""
        return [Row("Integrity", "guard files", NOT_LIVE, "%d differ from release %s%s" % (
            len(differ) + missing, release, tail))]
    if unchecked:
        return [Row("Integrity", "guard files", UNKNOWN, "%d too large to check in the time limit (%s)" % (
            len(unchecked), session._names(unchecked)))]
    return [Row("Integrity", "guard files", LIVE, "every guard file matches release %s" % release)]


# ---- the report --------------------------------------------------------------------------------------------------

def collect(root, now=None, toolchain=None, codex_config=None, home=None):
    """-> (rows, the expired lifts this call cleared). `toolchain`: None runs tools/toolchain-check.sh, False skips
    it (reported as unknown), a callable(root) -> (rc, output) replaces it."""
    root = str(root)
    now = time.time() if now is None else now
    home = str(Path.home()) if home is None else str(home)
    cleared = clear_expired(root, now)
    sf = Path(root) / ".skyguard" / "state.json"
    st = state.read(str(sf))
    rows = _layer0(root, toolchain) + _layer1(root)
    if cleared:
        rows.append(Row("Layer 2", "expired lifts", LIVE, "cleared %d expired lift(s) from .skyguard/state.json (R9); "
                        "logged in .claude/backups/AUDIT_LOG.txt" % len(cleared)))
    rows += _layer2(root, st, now, cleared)
    rows += _layer3_claude(root, now, home)
    rows += _layer3_codex(root, codex_config)
    rows += _guards(st, os.path.lexists(str(sf)), now)
    rows += _integrity(root)
    return rows, cleared


def exit_code(rows) -> int:
    counted = [r.status for r in rows if r.counted]
    if NOT_LIVE in counted:
        return 1
    if UNKNOWN in counted:
        return 2
    return 0


def render(rows, out):
    print("=" * 62, file=out)
    print("SKYDOCTOR -- what is live on this install", file=out)
    print("=" * 62, file=out)
    section = None
    for r in rows:
        if r.section != section:
            section = r.section
            print(section + (" (measured limits; not counted in the exit code)" if not r.counted else ""), file=out)
        reason = (r.label + " -- " + r.reason) if r.label else r.reason
        print("  %s | %s | %s" % (r.item, r.status, reason), file=out)
    counted = [r for r in rows if r.counted]
    n = {s: sum(1 for r in counted if r.status == s) for s in STATUSES}
    print("-" * 62, file=out)
    code = exit_code(rows)
    word = {0: "ALL LIVE", 1: "NOT LIVE", 2: "UNPROVEN"}[code]
    print("RESULT: %s -- live %d, not live %d, unknown %d (exit %d)" % (word, n[LIVE], n[NOT_LIVE], n[UNKNOWN], code),
          file=out)


def main(argv=None, out=None) -> int:
    out = sys.stdout if out is None else out
    ap = argparse.ArgumentParser(prog="skydoctor", description="What is live on this install, layer by layer.")
    ap.add_argument("--root", default=None, help="the install root (default: this toolkit's)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-toolchain", action="store_true", help="skip tools/toolchain-check.sh (Layer 0 reads unknown)")
    ap.add_argument("--codex-config", default=None, help="default: $CODEX_HOME/config.toml or ~/.codex/config.toml")
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 2
    root = a.root or lock.default_root()
    if not os.path.isdir(root):
        print("skydoctor: no such install root: %s" % _txt(root), file=out)
        return 2
    try:
        rows, _cleared = collect(root, toolchain=False if a.no_toolchain else None, codex_config=a.codex_config)
    except lock.SandboxViolation as e:
        print("REFUSED (sandbox): " + session._clean(e, 150), file=out)
        return 2
    code = exit_code(rows)
    if a.json:
        print(json.dumps({"root": root, "rows": [asdict(r) for r in rows], "exit": code}, indent=1), file=out)
    else:
        render(rows, out)
    return code


if __name__ == "__main__":
    sys.exit(main())
