"""Lift state for Layer 2 and shadow mode (Plan 6 Task 7; rulings R6, R7, R9).

`.skyguard/state.json` records temporary lifts of the guards or the vanilla-file lock:

    {"v": 1,
     "lifts": [{"kind": "guards"|"lock", "files"?: [str], "reason": str, "session_id": str, "agent": str,
                "created": <epoch s>, "expires_at": <epoch s>}],
     "persistent": {"lock_disabled"?: {"since": <epoch s>, "by_session": str}},
     "sum": <sha256 hex of the canonical JSON of everything above, without "sum">}

What this module guarantees, and what it does not (R6): a lift is honoured only in the session that made it,
only after that session's LATEST start, and never past `expires_at` or 8 hours after creation. A state the
reader cannot verify reads as `State.SAFE` (guards on, lock on) with a reason, and the caller logs
`STATE-REJECTED <reason>`. The checksum catches naive and accidental edits ONLY: code running as the same user
can rewrite any file and compute any checksum, so forgery cannot be prevented; the session binding, the expiry,
the per-call SHADOW line and the session-start report are what bound what a forger gains.

Known open gap (ledgered, owned by Tasks 10/12): `persistent.lock_disabled` is as forgeable as any other field,
and unlike a lift it is not session-bound; the guard against it is a deny on writes to state.json that set it.

Times are epoch seconds. Writes go only through `write()`, which refuses what `read()` would reject.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import stat as _stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

VERSION = 1
MAX_BYTES = 64 * 1024  # the read is bounded to this + 1, so this one constant is the whole size clause
MAX_LIFT_SECONDS = 8 * 3600.0
CLOCK_SKEW_SECONDS = 60.0
KINDS = ("guards", "lock")
_REPARSE = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: a junction, a symlink, a mount point


@dataclass(frozen=True)
class State:
    """What the reader hands back. Never raises. `rejected` is True when a file existed and could not be
    trusted (the caller logs `STATE-REJECTED <reason>`); a state that is simply absent is not a rejection."""
    lifts: list = field(default_factory=list)
    lock_disabled: dict | None = None
    reason: str = ""
    rejected: bool = False


class _Fresh:
    """`State.SAFE` is a NEW empty State on every access, so a caller that mutates one cannot change what the
    next reader is handed."""

    def __get__(self, obj, owner):
        return owner()


State.SAFE = _Fresh()


def _rejected(reason: str) -> State:
    return State(reason=reason, rejected=True)


def canonical_sum(data: dict) -> str:
    """sha256 over the canonical JSON of `data` without its `sum` key."""
    body = {k: v for k, v in data.items() if k != "sum"}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def _num(v) -> bool:
    return type(v) in (int, float) and math.isfinite(v)


def _valid_lift(x) -> bool:
    if not isinstance(x, dict) or x.get("kind") not in KINDS:
        return False
    if not all(isinstance(x.get(k), str) for k in ("reason", "session_id", "agent")):
        return False
    if not (_num(x.get("created")) and _num(x.get("expires_at"))):
        return False
    files = x.get("files")
    return files is None or (isinstance(files, list) and all(isinstance(f, str) for f in files))


def _valid_disable(x) -> bool:
    return isinstance(x, dict) and _num(x.get("since")) and isinstance(x.get("by_session"), str)


def _version_ok(data) -> bool:
    return type(data.get("v")) is int and data["v"] == VERSION


def _shape_problem(data):
    """None when `lifts` and `persistent` have the shape the reader accepts, else the reason."""
    lifts, persistent = data.get("lifts"), data.get("persistent")
    if not isinstance(lifts, list) or not all(_valid_lift(x) for x in lifts) or not isinstance(persistent, dict):
        return "bad shape"
    dis = persistent.get("lock_disabled")
    if dis is not None and not _valid_disable(dis):
        return "bad shape"
    return None


# ---- the state lock (Plan 6 final fix; minimal version, reconciled with the docs lane's at merge) -------------------
# ONE lock file beside the state, `state.json.lock`, created exclusively. Every write takes it (waiting up to `wait`
# seconds); a read-modify-write wraps the read AND the write in `locked()` so a concurrent writer cannot be lost between
# them. Re-entrant within a process. A per-call hook path passes a SHORT wait and skips (logged) on StateBusy. A lock
# file older than LOCK_STALE seconds is a crashed holder's and is broken.
LOCK_SUFFIX = ".lock"
LOCK_STALE = 30.0
DEFAULT_WAIT = 5.0
_HELD = set()


class StateBusy(OSError):
    """Another writer holds the state lock past the wait."""


class locked:
    def __init__(self, path, wait=DEFAULT_WAIT):
        self.lp = str(path) + LOCK_SUFFIX
        self.key = os.path.normcase(os.path.abspath(self.lp))
        self.wait, self.mine = wait, False

    def __enter__(self):
        import time
        if self.key in _HELD:
            return self
        Path(self.lp).parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + max(0.0, float(self.wait))
        while True:
            try:
                fd = os.open(self.lp, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("ascii"))
                os.close(fd)
                break
            except FileExistsError:
                try:
                    if time.time() - os.stat(self.lp).st_mtime > LOCK_STALE:
                        os.unlink(self.lp)
                        continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise StateBusy("the state file is being written by another process")
                time.sleep(0.02)
        _HELD.add(self.key)
        self.mine = True
        return self

    def __exit__(self, *exc):
        if self.mine:
            _HELD.discard(self.key)
            try:
                os.unlink(self.lp)
            except OSError:
                pass
        return False


def write(path, data: dict, wait=DEFAULT_WAIT) -> None:
    """Atomically write `data` with its checksum, under the state lock (`locked`). Refuses (ValueError) anything
    `read()` would reject: an unknown version, a malformed lift or persistent block, or more than MAX_BYTES. Temp file
    in the same directory, then `os.replace`: a reader sees the old file or the new one, never a half-written one. On
    any failure the old file is left and no temp file remains. The only sanctioned writer of state.json."""
    with locked(path, wait):
        _write(path, data)


def _write(path, data: dict) -> None:
    path = Path(path)
    body = {k: v for k, v in data.items() if k != "sum"}
    if not _version_ok(body):
        raise ValueError("refusing to write: unknown version")
    problem = _shape_problem(body)
    if problem:
        raise ValueError("refusing to write: " + problem)
    body["sum"] = canonical_sum(body)
    payload = json.dumps(body, sort_keys=True, indent=1, ensure_ascii=True).encode("ascii") + b"\n"
    if len(payload) > MAX_BYTES:
        raise ValueError("refusing to write: larger than {} KiB".format(MAX_BYTES // 1024))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _is_reparse(st) -> bool:
    return _stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & _REPARSE)


def read(path) -> State:
    """Read and verify. Any failure, of any kind (including a RecursionError from a hostile nesting depth) -> a
    rejected State with no lifts, the lock not disabled, and a reason. Never raises."""
    try:
        return _read(Path(path))
    except Exception as e:  # noqa: BLE001 -- the reader's contract is "never raises"
        return _rejected("unreadable ({})".format(e.__class__.__name__))


def _read(path: Path) -> State:
    try:
        st = os.lstat(str(path))
    except FileNotFoundError:
        return State.SAFE
    if _is_reparse(os.lstat(str(path.parent))):
        return _rejected("reparse point on the state folder")
    if _is_reparse(st):
        return _rejected("reparse point on the state file")
    if not _stat.S_ISREG(st.st_mode):
        return _rejected("not a regular file")
    with open(str(path), "rb") as fh:
        fst = os.fstat(fh.fileno())
        # The handle must be the file lstat vetted: a swap between the check and the open changes the identity.
        if (fst.st_ino, fst.st_dev) != (st.st_ino, st.st_dev) or _is_reparse(fst):
            return _rejected("file changed while opening")
        raw = fh.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        return _rejected("larger than {} KiB".format(MAX_BYTES // 1024))
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return _rejected("unparseable")
    if not isinstance(data, dict):
        return _rejected("unparseable")
    if not _version_ok(data):
        return _rejected("unknown version")
    got = data.get("sum")
    try:
        want = canonical_sum(data)
    except (ValueError, TypeError):
        return _rejected("bad sum (not canonicalisable)")
    if not isinstance(got, str) or not hmac.compare_digest(got.encode("ascii", "replace"), want.encode("ascii")):
        return _rejected("bad sum")
    problem = _shape_problem(data)
    if problem:
        return _rejected(problem)
    dis = data["persistent"].get("lock_disabled")
    return State(lifts=list(data["lifts"]), lock_disabled=dict(dis) if dis is not None else None)


def honoured(state: State, session_id, now: float, session_start) -> list:
    """The lifts this caller may act on: its own session's, created after that session's latest start (and not
    dated in the future), and neither past `expires_at` nor older than 8 hours. An unknown or non-finite
    `now` or session start honours nothing: "created after the session began" cannot be shown."""
    if not _num(now) or not _num(session_start) or not isinstance(session_id, str):
        return []
    out = []
    for lift in state.lifts:
        if lift["session_id"] != session_id:
            continue
        if lift["created"] < session_start:
            continue
        if lift["created"] > now + CLOCK_SKEW_SECONDS:
            continue
        if not now < min(lift["expires_at"], lift["created"] + MAX_LIFT_SECONDS):
            continue
        out.append(lift)
    return out


def lift_expired(lift, now) -> bool:
    """Plan 6 final fix (A-I3): past its expires_at or older than MAX_LIFT_SECONDS -- the bound honoured() applies. The
    lock's mid-session relock (engine.shadow -> lock.relock_expired) and `guards status` use this one test."""
    return not now < min(lift["expires_at"], lift["created"] + MAX_LIFT_SECONDS)


def session_start(dirpath, session_id):
    """Epoch seconds of the LATEST `start` time among the `start` lines Plan 4's `health.session_record` wrote for this
    session (the MAX, not the last line: an appended early-dated forged line cannot move it back), or None. One is
    appended per resume, so a lift made before a resume is older than the start and not honoured after it.
    Any garbled or non-finite start line gives None. `dirpath` is the agent's
    heartbeat directory (the one `session_record` was given)."""
    from . import health
    if not isinstance(session_id, str) or not health._SESSION_ID.match(session_id):
        return None
    try:
        text = (Path(dirpath) / "sessions" / (session_id + ".log")).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    best = None
    for line in text.splitlines():
        f = line.split(None, 3)
        if len(f) < 3 or f[1] != "start":
            continue
        # Plan 6 final fix (A-M1, R9: compact changes nothing): a compaction is not a new start. Counted, it ended a
        # `guards off` that `guards status` still reported as on. A lift from before a startup/resume/clear start is still
        # not honoured, and a session with no start line but compact ones honours nothing (None).
        if f[2] == "compact":
            continue
        try:
            t = float(f[0])
        except ValueError:
            return None            # a garbled start line: the session start cannot be shown
        if not math.isfinite(t):
            return None
        if best is None or t > best:
            best = t
    return best
