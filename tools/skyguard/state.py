"""Lift state for Layer 2 and shadow mode (Plan 6 Task 7; rulings R6, R7, R9).

`.skyguard/state.json` records temporary lifts of the guards or the vanilla-file lock:

    {"v": 1,
     "lifts": [{"kind": "guards"|"lock", "files"?: [str], "reason": str, "session_id": str, "agent": str,
                "created": <epoch s>, "expires_at": <epoch s>}],
     "persistent": {"lock_disabled"?: {"since": <epoch s>, "by_session": str}},
     "sum": <sha256 hex of the canonical JSON of everything above, without "sum">}

What this module guarantees, and what it does not (R6): a lift is honoured only in the session that made it,
only after that session started, and never past `expires_at` or 8 hours after creation. A state the reader
cannot verify reads as `State.SAFE` (guards on, lock on) with a reason, and the caller logs
`STATE-REJECTED <reason>`. The checksum catches naive and accidental edits ONLY: code running as the same user
can rewrite any file and compute any checksum, so forgery cannot be prevented; the session binding, the expiry,
the per-call SHADOW line and the session-start report are what bound what a forger gains.

Times are epoch seconds. Writes go only through `write()`.
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
    SAFE: "State" = field(default=None, init=False, repr=False, compare=False)  # replaced below


State.SAFE = State()


def _rejected(reason: str) -> State:
    return State(reason=reason, rejected=True)


def canonical_sum(data: dict) -> str:
    """sha256 over the canonical JSON of `data` without its `sum` key."""
    body = {k: v for k, v in data.items() if k != "sum"}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def write(path, data: dict) -> None:
    """Atomically write `data` with its checksum. Temp file in the same directory, then `os.replace`: a
    reader sees the old file or the new one, never a half-written one. Raises OSError on failure, leaving
    the old file and no temp file behind. The only sanctioned writer of state.json."""
    path = Path(path)
    body = {k: v for k, v in data.items() if k != "sum"}
    body["sum"] = canonical_sum(body)
    payload = json.dumps(body, sort_keys=True, indent=1, ensure_ascii=True).encode("ascii") + b"\n"
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


def read(path) -> State:
    """Read and verify. Any failure -> a rejected `State.SAFE`-equivalent (no lifts, lock not disabled) with a
    reason. Never raises."""
    path = Path(path)
    try:
        try:
            st = os.lstat(str(path))
        except FileNotFoundError:
            return State.SAFE
        if _is_reparse(os.lstat(str(path.parent))) or _is_reparse(st):
            return _rejected("reparse point on the state file or its folder")
        if not _stat.S_ISREG(st.st_mode):
            return _rejected("not a regular file")
        with open(str(path), "rb") as fh:
            raw = fh.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return _rejected("larger than {} KiB".format(MAX_BYTES // 1024))
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return _rejected("unparseable")
        if not isinstance(data, dict):
            return _rejected("unparseable")
        if type(data.get("v")) is not int or data["v"] != VERSION:
            return _rejected("unknown version")
        got = data.get("sum")
        try:
            want = canonical_sum(data)
        except (ValueError, TypeError):
            return _rejected("bad sum (not canonicalisable)")
        if not isinstance(got, str) or not hmac.compare_digest(got.encode("ascii", "replace"), want.encode("ascii")):
            return _rejected("bad sum")
        lifts, persistent = data.get("lifts"), data.get("persistent")
        if not isinstance(lifts, list) or not all(_valid_lift(x) for x in lifts) or not isinstance(persistent, dict):
            return _rejected("bad shape")
        dis = persistent.get("lock_disabled")
        if dis is not None and not _valid_disable(dis):
            return _rejected("bad shape")
        return State(lifts=list(lifts), lock_disabled=dict(dis) if dis is not None else None)
    except OSError as e:
        return _rejected("unreadable ({})".format(e.__class__.__name__))


def honoured(state: State, session_id, now: float, session_start) -> list:
    """The lifts this caller may act on: its own session's, created after that session started (and not
    dated in the future), and neither past `expires_at` nor older than 8 hours. An unknown session start
    honours nothing: without it "created after the session began" cannot be shown."""
    if session_start is None or not isinstance(session_id, str):
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


def session_start(dirpath, session_id):
    """Epoch seconds of the `start` line Plan 4's `health.session_record` wrote for this session, or None.
    `dirpath` is the agent's heartbeat directory (the one `session_record` was given)."""
    from . import health
    if not isinstance(session_id, str) or not health._SESSION_ID.match(session_id):
        return None
    try:
        text = (Path(dirpath) / "sessions" / (session_id + ".log")).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        f = line.split(None, 3)
        if len(f) >= 3 and f[1] == "start":
            try:
                t = float(f[0])
            except ValueError:
                return None
            return t if math.isfinite(t) else None
    return None
