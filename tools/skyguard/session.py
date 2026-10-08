"""SessionStart: Layer 2 expiry/drift/banners, then tools/kb-guard.sh and preferences. Never fails the session."""
from __future__ import annotations

import hashlib
import os
import subprocess
import time
from pathlib import Path

from . import guard_hash, paths, prefs

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
GUARD_MANIFEST = "GUARD-FILES.sha256"
# R12 hashing budget: a guard-file check must never stall a session start. Module constants so a test can shrink them.
FILE_CAP = 32 * 1024 * 1024        # a file over this is "too large to check", never "differs"
TOTAL_CAP = 200 * 1024 * 1024      # bytes hashed in total
TIME_CAP = 5.0                     # wall seconds in total
_SAFE = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 ._-")


def _clean(s, n=80):
    """EVERY string that flows from state.json, lock.json or GUARD-FILES.sha256 into a banner passes through here: only
    [A-Za-z0-9 ._-] survive (anything else, colons and quotes included, becomes '?'), whitespace collapses, length is
    capped. A recorded string is data, never an instruction the model should follow."""
    t = "".join(c if c in _SAFE else "?" for c in str(s))
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


def _inside(root, target):
    try:
        return os.path.commonpath([root, target]) == root
    except ValueError:
        return False


def _hash_capped(path, size, budget):
    """sha256 of a file read in chunks, charging `budget["bytes"]`. -> hex, or None when the file is over FILE_CAP,
    or the total byte or wall-time budget ran out (the caller reports it as unchecked)."""
    if size > FILE_CAP or budget["bytes"] + size > TOTAL_CAP:
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            if time.monotonic() > budget["deadline"]:
                return None
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            budget["bytes"] += len(chunk)
            h.update(chunk)
    return h.hexdigest()


def _guard_drift(project):
    """R12: None when there is no manifest, else (release, differ, missing, unchecked). `differ` names only files
    that EXIST inside the project; `missing` is a count (a manifest line is untrusted text and names nothing that is
    not on disk); `unchecked` names files too large for the budget, which are never reported as "differs".
    Format: an optional `# release <ver>` line, then `<sha256>  <relative path>` per file."""
    try:
        text = (Path(project) / GUARD_MANIFEST).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    root = os.path.realpath(str(project))
    budget = {"bytes": 0, "deadline": time.monotonic() + TIME_CAP}
    release, differ, unchecked, missing, configured = "?", [], [], 0, []
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
            st = os.stat(target)
            if not os.path.isfile(target):
                raise OSError("not a file")
        except OSError:
            missing += 1
            continue
        key = rel.replace(chr(92), "/")
        try:
            if key in guard_hash.CONFIGURED:
                # Fix round 1 (C1): a file setup.sh configures is hashed in its repo form, and the interpreter it was
                # given is compared across those files (guard_hash). Small files; the same caps apply.
                got = None
                fits = st.st_size <= FILE_CAP and budget["bytes"] + st.st_size <= TOTAL_CAP
                if fits and time.monotonic() <= budget["deadline"]:
                    with open(target, "rb") as fh:
                        data = fh.read()
                    budget["bytes"] += len(data)
                    got, values = guard_hash.digest(key, data)
                    configured.append((os.path.basename(rel), values))
            else:
                got = _hash_capped(target, st.st_size, budget)
        except OSError:
            missing += 1
            continue
        if got is None:
            unchecked.append(os.path.basename(rel))
        elif got != parts[0].lower():
            differ.append(os.path.basename(rel))
    for name in guard_hash.inconsistent(configured):
        if name not in differ:
            differ.append(name)
    return release, differ, missing, unchecked


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
        return _clip("Vanilla-file relock FAILED: " + _clean(res.lines[0] if res.lines else "see `bash tools/skyguard.sh lock status`", 150))
    return ""


def layer2_start(project, source="startup"):
    """The Layer 2 half of SessionStart -> banner strings (one or two lines each). Never raises: a failure is
    itself a banner. `compact` (R9) changes nothing and reports only the standing persistent-disable notice.
    A persistent disable is honoured whatever its source (spec R7: the visibility, not a gate, is the control):
    it is shown at EVERY session start for as long as it lasts."""
    from . import lock, state
    banners = []
    try:
        root = str(project)
        sf = Path(root) / ".skyguard" / "state.json"
        st = state.read(str(sf))
        # Clear old lifts before scanning. Re-read under the writer lock so a
        # newer CLI enable/disable decision cannot be overwritten by this start.
        if source != "compact" and not st.rejected and st.lifts:
            try:
                lock._assert_sandbox(sf)
                with state.locked(str(sf)):
                    st = state.read(str(sf))
                    if not st.rejected and st.lifts:
                        persistent = {"lock_disabled": dict(st.lock_disabled)} if st.lock_disabled else {}
                        state.write(str(sf), {"v": 1, "lifts": [], "persistent": persistent})
            except (ValueError, OSError):
                pass                                    # session-bound lifts still expire
        disabled = None
        if st.lock_disabled:
            try:
                when = time.strftime("%Y-%m-%d", time.localtime(st.lock_disabled["since"]))
            except (OverflowError, OSError, ValueError):
                when = "an unknown date"
            disabled = ("Vanilla-file lock DISABLED since %s. `bash tools/skyguard.sh lock enable` "
                        "(tools\\skyguard.cmd in cmd) turns it back on." % when)
        if source == "compact":
            return [disabled] if disabled else []
        engaged = _engaged(root) and not disabled
        lifted, drift = _scan(root, st.lifts) if engaged else ([], [])
        # Relock and report using the state captured before the clear.
        failure = _relock(root) if engaged else ""
        # step 3: the banners, in the order the brief lists them
        guards = [x for x in st.lifts if x["kind"] == "guards"]
        if guards:
            banners.append('Guards were left OFF by an earlier session (recorded reason, untrusted: "%s"); they are back ON.'
                           % _clean(guards[0]["reason"]))
        if lifted:
            banners.append("%d vanilla files were left unlocked; relocked." % len(lifted))
        if drift:
            banners.append(_clip("%d vanilla files were unlocked outside skyguard (%s); relocked." % (len(drift), _names(drift))))
        mine = [x for x in st.lifts if x["kind"] == "lock" and x["session_id"] == lock.USER_SESSION]
        if mine:
            every = any(x.get("files") is None for x in mine)
            what = "all vanilla files" if every else _names([f for x in mine for f in x["files"]])
            banners.append(_clip("This start also revoked your own terminal unlock of %s; unlock again if you still need it." % what))
        if failure:
            banners.append(failure)
        if disabled:
            banners.append(disabled)
        gd = _guard_drift(root)
        if gd:
            release, differ, missing, unchecked = gd
            if differ or missing:
                tail = (" (%s)" % _names(differ)) if differ else ""
                tail += ("; %d missing" % missing) if missing else ""
                banners.append(_clip("%d guard files differ from release %s%s." % (len(differ) + missing, release, tail)))
            if unchecked:
                banners.append(_clip("%d guard files were too large to check within the time limit (%s)."
                                     % (len(unchecked), _names(unchecked))))
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
    from . import lock
    if not path or not isinstance(session_id, str) or not health._SESSION_ID.match(session_id) \
            or lock.reserved_session(session_id) or agent not in ("claude", "codex"):
        return False
    lines = ["export SKYGUARD_SESSION_ID='%s'" % session_id, "export SKYGUARD_AGENT='%s'" % agent]
    try:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                have = fh.read().splitlines()
        except FileNotFoundError:
            have = []
        todo = [x for x in lines if x not in have]      # a resume or a second start must not stack duplicates
        if todo:
            with open(path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write("".join(x + "\n" for x in todo))
        return True
    except OSError:
        return False


def run(project, source, bash, audit):
    """The text SessionStart injects. `run_status` is the same call with the health verdict."""
    return run_status(project, source, bash, audit)[0]


def run_status(project, source, bash, audit, root=None):
    """-> (text, status, code). status is what the engine reports to the canary: `failed` when the guard did
    not run at all, `partial` when it ran but snapshotted nothing (kb-guard exit 2), else `evaluated`.
    `root`: the install the guard reads its paths file from (run.py's --root); the legacy-location notice is keyed on
    it, not on the session's project folder (T8 review M2). None: the project (the two normally coincide)."""
    banners = "\n".join(layer2_start(project, source))
    if source == "compact":
        # no snapshot on compact (R4: prefs survive), no clearing (R9); only the standing disable notice
        return _with_prefs(banners, project, audit), "evaluated", "session"
    # Plan 8 T8: the legacy paths-file location is announced here, once per session start -- never per tool call.
    banners = "\n".join(t for t in (banners, paths.legacy_notice(project if root is None else root)) if t)
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
