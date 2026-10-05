"""SessionStart: Layer 2 expiry/drift/banners, then tools/kb-guard.sh and preferences. Never fails the session."""
from __future__ import annotations

import hashlib
import os
import subprocess
import time
from pathlib import Path

from . import prefs

HOOK = "session-kb-guard"


def _with_prefs(text, project, audit):
    extra = prefs.render(project, audit)
    return "\n".join(t for t in (text, extra) if t)


# ---- Layer 2 at session start (Plan 6 Task 10; R4, R6, R7, R9, R12) ---------------------------------------------
# Order (Review Focus 4): 1 the start is recorded (run.py, before this) -> 2 every lift is cleared and the derived
# target set relocked unless a TRUSTED persistent disable exists -> 3 the banners are computed from what was found
# BEFORE the clear -> 4 kb-guard and preferences (run_status below). A banner is one or two plain lines; nothing
# the state file carries (a reason, a file name) reaches one unsanitised.

_LINE = 190
TRUSTED_DISABLE = "user-tty"       # lock.USER_SESSION: the only disable source a start will honour
GUARD_MANIFEST = "GUARD-FILES.sha256"


def _clean(s, n=60):
    """One printable line, at most n characters: a state-file string is data, never layout."""
    t = "".join(c if c.isprintable() else " " for c in str(s))
    t = " ".join(t.split())
    return t if len(t) <= n else t[: n - 3] + "..."


def _names(names, limit=3):
    names = [_clean(n, 60) for n in names]
    return ", ".join(names[:limit]) + ("…" if len(names) > limit else "")


def _clip(s):
    return s if len(s) <= _LINE else s[: _LINE - 3] + "..."


def _covers(lifts, name):
    """True when a lock lift on record names this file (or every file)."""
    for x in lifts:
        if x["kind"] == "lock" and (x.get("files") is None or name.lower() in (f.lower() for f in x["files"])):
            return True
    return False


def _disable_trust(st):
    """-> (trusted, since). A persistent disable is the user's to make (R7): it is honoured only when it carries the
    terminal source `lock disable` stamps when a person types it. A claim written by an agent session, or with no
    session at all, buys nothing: it is dropped and reported. A forger who writes the terminal source still wins
    persistence (same-user code can write anything), which is why the banner is shown at EVERY session."""
    dis = st.lock_disabled
    if not dis:
        return False, None
    return dis.get("by_session") == TRUSTED_DISABLE, dis.get("since")


def _inside(root, target):
    try:
        return os.path.commonpath([root, target]) == root
    except ValueError:
        return False


def _guard_drift(project):
    """R12: (release, [basenames that differ]) against the shipped GUARD-FILES.sha256, or None when there is no
    manifest. Format: an optional `# release <ver>` line, then `<sha256>  <relative path>` per file."""
    try:
        text = (Path(project) / GUARD_MANIFEST).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    root = os.path.realpath(str(project))
    release, bad = "?", []
    for line in text.splitlines()[:5000]:
        line = line.strip()
        if line.startswith("# release "):
            release = _clean(line[len("# release "):], 24)
            continue
        parts = line.split(None, 1)
        if not line or line.startswith("#") or len(parts) != 2 or len(parts[0]) != 64:
            continue
        rel = parts[1].strip().lstrip("*")
        target = os.path.realpath(os.path.join(root, rel))
        if os.path.isabs(rel) or not _inside(root, target):
            continue                                    # a path that leaves the project is never read
        try:
            with open(target, "rb") as fh:
                got = hashlib.sha256(fh.read(32 * 1024 * 1024)).hexdigest()
        except OSError:
            got = ""
        if got != parts[0].lower():
            bad.append(os.path.basename(rel))
    return release, bad


def _engaged(root):
    """The lock was applied at some point (R3: never applied by default): `.skyguard/lock.json` exists. A lift
    in state.json does not count -- a forged one must not be able to switch the lock on."""
    from . import lock
    return lock.supported() and (Path(root) / ".skyguard" / "lock.json").is_file()


def _scan(root, lifts):
    """Before the relock: the target files that are open now -> (lifted, drift). `lifted` = a lock lift on record
    covers it; `drift` = it was in the manifest (we locked it) and nothing on record explains it being open. A
    target never locked and not lifted (e.g. a new file in the set) is relocked without comment."""
    from . import lock
    data_dir = Path(root) / "Data"
    if not data_dir.is_dir():
        return [], []
    t = lock.targets(data_dir)
    manifest, _status = lock._read_manifest(root)
    lifted, drift = [], []
    for p in t.files:
        try:
            fs = lock.read_state(p)
        except OSError:
            continue
        if fs.ours is not None and fs.readonly:
            continue
        if _covers(lifts, p.name):
            lifted.append(p.name)
        elif p.name in manifest:
            drift.append(p.name)
    return lifted, drift


def _relock(root):
    """Relock the derived target set (R4). -> a failure line, or "" when it worked. Files the lock never
    covers (multi-link, reparse) are expected skips, not failures."""
    from . import lock
    res = lock.lock_all(root)
    bad = [x for x in res.data.get("not_locked", []) if x[1].startswith(("did not verify", "error"))]
    if res.exit == 2 or bad:
        return _clip("Vanilla-file relock FAILED: " + _clean(res.lines[0] if res.lines else "see skyguard lock status", 150))
    return ""


def layer2_start(project, source="startup"):
    """The Layer 2 half of SessionStart -> banner strings (one or two lines each). Never raises: a failure is
    itself a banner. `compact` (R9) changes nothing and reports only the standing persistent-disable notice."""
    from . import lock, state
    banners = []
    try:
        root = str(project)
        sf = Path(root) / ".skyguard" / "state.json"
        st = state.read(str(sf))
        trusted, since = _disable_trust(st)
        disabled = None
        if trusted:
            try:
                when = time.strftime("%Y-%m-%d", time.localtime(since))
            except (OverflowError, OSError, ValueError):
                when = "an unknown date"
            disabled = "Vanilla-file lock DISABLED since %s. tools/skyguard lock enable turns it back on." % when
        if source == "compact":
            return [disabled] if disabled else []
        untrusted = bool(st.lock_disabled) and not trusted
        engaged = _engaged(root) and not trusted
        lifted, drift = _scan(root, st.lifts) if engaged else ([], [])
        # step 2: clear every lift and drop an untrusted disable claim; THEN relock; THEN report
        if not st.rejected and (st.lifts or untrusted):
            try:
                lock._assert_sandbox(sf)
                persistent = {"lock_disabled": dict(st.lock_disabled)} if trusted else {}
                state.write(str(sf), {"v": 1, "lifts": [], "persistent": persistent})
            except (ValueError, OSError):
                pass                                    # lifts stay, but each is session-bound and expires (R6)
        failure = _relock(root) if engaged else ""
        # step 3: the banners, in the order the brief lists them
        guards = [x for x in st.lifts if x["kind"] == "guards"]
        if guards:
            banners.append("Guards were left OFF by an earlier session (%s); they are back ON." % _clean(guards[0]["reason"]))
        if lifted:
            banners.append("%d vanilla files were left unlocked; relocked." % len(lifted))
        if drift:
            banners.append(_clip("%d vanilla files were unlocked outside skyguard (%s); relocked." % (len(drift), _names(drift))))
        if failure:
            banners.append(failure)
        if disabled:
            banners.append(disabled)
        elif untrusted:
            banners.append("A lock disable that was not made at a terminal was ignored; the lock is back ON.\n"
                           "Run `tools/skyguard lock disable` yourself at a terminal to keep it off.")
        gd = _guard_drift(root)
        if gd and gd[1]:
            banners.append(_clip("%d guard files differ from release %s (%s)." % (len(gd[1]), gd[0], _names(gd[1]))))
        if st.rejected:
            banners.append("STATE-REJECTED: the guard state file was unreadable or altered; treated as guards on.")
        return banners
    except lock.SandboxViolation as e:
        return banners + [_clip("Session start refused by the sandbox: " + _clean(e, 120))]
    except Exception as e:  # noqa: BLE001 -- the session must start; the failure is reported, never swallowed
        return banners + ["The Layer 2 session check failed (%s); guard and lock state could not be checked."
                          % e.__class__.__name__]


def export_env(session_id, agent, env_file=None):
    """Claude only: persist SKYGUARD_SESSION_ID / SKYGUARD_AGENT for the session's later Bash calls through
    CLAUDE_ENV_FILE (documented for SessionStart hooks: `export` lines appended to that file; bash syntax, so the
    PowerShell tool does not see them). The id is written only if it is a plain token, since it lands inside a
    shell file. Returns True when written."""
    from . import health
    path = os.environ.get("CLAUDE_ENV_FILE", "") if env_file is None else env_file
    if not path or not isinstance(session_id, str) or not health._SESSION_ID.match(session_id) \
            or agent not in ("claude", "codex"):
        return False
    try:
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write("export SKYGUARD_SESSION_ID='%s'\nexport SKYGUARD_AGENT='%s'\n" % (session_id, agent))
        return True
    except OSError:
        return False


def run(project, source, bash, audit):
    """The text SessionStart injects. `run_status` is the same call with the health verdict."""
    return run_status(project, source, bash, audit)[0]


def run_status(project, source, bash, audit):
    """-> (text, status, code). status is what the engine reports to the canary: `failed` when the guard did
    not run at all, `partial` when it ran but snapshotted nothing (kb-guard exit 2), else `evaluated`."""
    banners = "\n".join(layer2_start(project, source))
    if source == "compact":
        # no snapshot on compact (R4: prefs survive), no clearing (R9); only the standing disable notice
        return _with_prefs(banners, project, audit), "evaluated", "session"
    tool = "{}/tools/kb-guard.sh".format(project)
    if not Path(tool).is_file():
        audit.raw("[{}] {} NOT RUN: tools/kb-guard.sh missing\n".format(audit.stamp(), HOOK))
        missing = "KB GUARD: NOT RUN -- {} is missing, so your knowledgebase was NOT snapshotted this session.".format(tool)
        return _with_prefs("\n".join(t for t in (banners, missing) if t), project, audit), "failed", "session.NOT_RUN"
    r = subprocess.run([bash, tool], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    raw = r.stdout.rstrip(b"\n")
    output = raw.decode("utf-8", "replace")
    lines = ([banners] if banners else []) + ([output] if output else [])
    if r.returncode == 2:
        lines.append("KB GUARD: the above means NOTHING was snapshotted this session -- unchecked, not clean.")
    flat = raw.replace(b"\n", b" ")[:200]        # bash: tr '\n' ' ' | head -c 200
    audit.raw(b"[" + audit.stamp().encode() + b"] " + HOOK.encode() + b" rc=" + str(r.returncode).encode()
              + b" " + flat + b"\n")
    status, code = ("partial", "session.NOT_SNAPSHOTTED") if r.returncode == 2 else ("evaluated", "session")
    return _with_prefs("\n".join(lines), project, audit), status, code
