"""Post-call save sentinel: saves that changed since the last look without a committed backup.

A pre-call guard judges what a call names. Code it cannot read (a script that builds a save path at run time) changes
saves it never sees. This module notices afterwards: it compares a profile's save roots (plus any extra watched
folders) against the previous look and reports members MODIFIED or REMOVED with no committed pre-mutation backup
in the store since then. It only lists and stats; it never opens, copies or changes a save. New members are not
reported: creating a save changes none.

    state = load_state(path)
    report = check(profile, store, state, watch=(), now=None)   # -> Report; state is updated in place
    save_state(path, state)

The host decides how loudly to report: the game may be the writer, and dated_within() tells it whether a change's own
timestamp falls inside a window in which the game ran. Nothing here blocks: a post-call hook cannot undo a call, and
the store's committed transactions remain the only recovery authority.

The record is shared by every session and hooks run in parallel, so a read or a replace can meet another look's replace,
or a scanner. Both are retried, under a second in all and only after a failure. A record still held after that is not
lost: load_state says "busy", check() compares nothing, save_state keeps the baseline, and the next look reports what
changed. Only a record held for STUCK_AFTER seconds, with no look recorded in between, counts as lost.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from .profiles import comparison_key
from .save_lock import REPLACE_WAITS

WALK_LIMIT = 20000          # entries per watched folder; a larger one is reported as truncated
CLOCK_SLACK = 5.0           # seconds: a backup committed just before the previous look still counts
STUCK_AFTER = 300.0         # seconds a record may stay held, across looks, before it reads as lost


class Report(NamedTuple):
    first: bool             # no previous look: nothing compared, the baseline was recorded
    unbacked: tuple         # ((kind, path), ...) kind is "modified" or "removed"
    backed_up: tuple        # changes a committed backup covers
    truncated: tuple        # watched folders too large to list fully
    state_lost: bool = False    # the previous look's record existed and could not be read
    unread: tuple = ()      # folders (or a drive) that could not be listed: their previous rows are kept, not "removed"
    record_held: bool = False   # the record is locked just now: nothing compared, the baseline kept for the next look
    lost_why: str = ""      # with state_lost: what happened to the record


def _member(name, extensions):
    low = name.lower()
    return any(low.endswith(ext.lower()) for ext in extensions)


def _scan(roots, extensions, watch, limit):
    """(files, truncated, unread): `unread` names the folders a listing failed for, so "not listed" is never "gone"."""
    files, truncated, unread = {}, [], []

    def add(path, st):
        files[comparison_key(os.path.abspath(path))] = [st.st_size, st.st_mtime_ns, str(path)]

    for root in roots:
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    if _member(entry.name, extensions) and entry.is_file():
                        try:
                            add(entry.path, entry.stat())
                        except FileNotFoundError:
                            continue    # this one went between the listing and the stat; the rest are still here
        except FileNotFoundError:
            continue                # the folder itself is gone: its saves are gone too, as far as anyone can tell
        except OSError:
            unread.append(str(root))
    for folder in watch:
        seen = 0
        failures = []
        for parent, _dirs, names in os.walk(folder, onerror=failures.append):
            for name in names:
                seen += 1
                if _member(name, extensions):
                    path = os.path.join(parent, name)
                    try:
                        add(path, os.stat(path))
                    except OSError:
                        continue
            if seen > limit:
                truncated.append(str(folder))
                break
        if any(not isinstance(e, FileNotFoundError) for e in failures):
            unread.append(str(folder))
    return files, tuple(truncated), tuple(unread)


def snapshot(roots, extensions, watch=(), limit=None):
    """{key: [size, mtime_ns, path]} for every save member: roots at top level (as games keep them), watched
    folders recursively. Returns (files, truncated)."""
    files, truncated, _unread = _scan(roots, extensions, watch, WALK_LIMIT if limit is None else limit)
    return files, truncated


def _carry_unlisted(previous, files, folders):
    """A member of a folder this look could not list fully keeps its previous row: unknown, not removed."""
    if folders:
        tops = [os.path.join(os.path.abspath(f), "") for f in folders]
        for key, row in previous.items():
            if key not in files and any(os.path.abspath(row[2]).startswith(top) for top in tops):
                files[key] = row


def _drive_absent(path):
    """The whole drive is gone (unplugged, unmounted): nothing below it can be judged."""
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    return bool(drive) and not os.path.exists(drive + os.sep)


def dated_within(kind, path, start, end=None, slack=CLOCK_SLACK):
    """Does this change's own timestamp fall inside [start, end] (epoch seconds; end None = still open)? A modified
    file is dated by its mtime and a removed one by its folder's. Anything that cannot be dated is outside, and so
    is a stamp older than `start`: a program saving now stamps now, so that is a restored or copied file."""
    try:
        stamp = os.stat(path if kind == "modified" else os.path.dirname(path)).st_mtime
    except OSError:
        return False
    return stamp >= start - slack and (end is None or stamp <= end + slack)


def changes(previous, current):
    """(kind, path) for members modified or removed between two snapshots."""
    found = []
    for key, (size, mtime, path) in sorted(previous.items()):
        now = current.get(key)
        if now is None:
            found.append(("removed", path))
        elif now[0] != size or now[1] != mtime:
            found.append(("modified", path))
    return found


def backed_up_since(store_root, since):
    """Comparison keys of every member preserved by a committed transaction at or after `since` (epoch seconds).
    Transaction files are immutable once committed, so their mtime bounds what must be read."""
    covered = set()
    folder = Path(store_root) / "transactions"
    try:
        candidates = [p for p in folder.glob("*.json") if p.stat().st_mtime >= since - CLOCK_SLACK]
    except OSError:
        return covered
    for path in candidates:
        try:
            manifest = json.loads(path.read_text(encoding="ascii"))
            when = datetime.fromisoformat(manifest["utc"]).timestamp()
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if manifest.get("complete") is not True or when < since - CLOCK_SLACK:
            continue
        for group in manifest.get("groups") or ():
            for member in group.get("members") or ():
                for field in ("path", "canonical"):
                    if isinstance(member.get(field), str):
                        covered.add(comparison_key(os.path.abspath(member[field])))
    return covered


def check(profile, store, state, watch=(), now=None, covered=None):
    """Compare the profile's save roots and `watch` folders with the previous look recorded in `state` (a dict,
    updated in place). `covered` may add keys the caller's own (legacy) backup records cover."""
    if state.get("busy"):
        return Report(False, (), (), (), record_held=True)
    now = time.time() if now is None else now
    files, truncated, unread = _scan(profile.save_roots, profile.extensions, watch, WALK_LIMIT)
    previous = state.get("files")
    since = state.get("time", now)
    first = not isinstance(previous, dict)
    lost = bool(state.pop("unreadable", False))
    why = state.pop("why", "")
    unbacked, backed = [], []
    if not first:
        _carry_unlisted(previous, files, truncated + unread)
        found = []
        for kind, path in changes(previous, files):
            if kind == "removed" and _drive_absent(path):
                files[comparison_key(os.path.abspath(path))] = previous[comparison_key(os.path.abspath(path))]
                drive = os.path.splitdrive(os.path.abspath(path))[0]
                if drive not in unread:
                    unread += (drive,)
            else:
                found.append((kind, path))
        if found:
            keys = backed_up_since(store.root, since) | set(covered or ())
            for kind, path in found:
                (backed if comparison_key(os.path.abspath(path)) in keys else unbacked).append((kind, path))
    state.update({"v": 1, "time": now, "files": files})
    return Report(first, tuple(unbacked), tuple(backed), truncated, lost, unread, lost_why=why)


def _seen_path(path):
    """A mark beside the record saying a look has been recorded here, so a record later deleted is not a first look."""
    path = Path(path)
    return path.with_name(path.name + ".seen")


def _retrying(call):
    """call(), tried again when another look or a scanner holds the file. Costs nothing unless a try fails."""
    for wait in REPLACE_WAITS:
        try:
            return call()
        except PermissionError:
            if wait is None:
                raise
            time.sleep(wait)


def _lost(why):
    return {"unreadable": True, "why": why}


def _still_held(path, now):
    """The record stayed locked through the retries. Busy, unless the mark shows it has been locked since a look at
    least STUCK_AFTER ago with none recorded since (save_state empties the mark); then it is lost, and the clock starts
    again so that a record that stays held alarms once per window. An unwritable mark only keeps this look busy."""
    now = time.time() if now is None else now
    seen = _seen_path(path)
    try:
        since = float(seen.read_text(encoding="ascii"))
    except (OSError, ValueError):
        since = None
    stuck = since is not None and abs(now - since) >= STUCK_AFTER
    if since is None or stuck:
        try:
            seen.write_text(str(now), encoding="ascii")
        except OSError:
            pass                    # silent-ok: this look's note does not depend on the mark
    return _lost("it stayed locked for {:.0f} seconds or more".format(STUCK_AFTER)) if stuck else {"busy": True}


def load_state(path, now=None):
    """The previous look's record. Nothing there, ever, is {}, a first look. A record gone though one was written here,
    or corrupt, or failing to read for any other reason, is {"unreadable": True, "why": ...}: still a new baseline, but
    check() reports it, because changes since the last look are lost. Deleting the mark along with the record reads as
    a first look. A record locked through the retries is {"busy": True}: see the module docstring."""
    path = Path(path)
    try:
        data = json.loads(_retrying(lambda: path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return _lost("it was deleted") if _seen_path(path).exists() else {}
    except PermissionError:
        return _still_held(path, now)
    except OSError as exc:
        return _lost("reading it failed: {}".format(type(exc).__name__))
    except ValueError:
        return _lost("its contents are corrupt")
    return data if isinstance(data, dict) and data.get("v") == 1 else _lost("its contents are corrupt")


def save_state(path, state):
    """Replace the state atomically, then mark that a record exists. Unlike durable.atomic_json (no-clobber, for
    immutable records) this is a cache that each look overwrites; losing it makes the next look a disclosed baseline.
    A look that read nothing writes nothing: the record it could not read is still the baseline."""
    if state.get("busy"):
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(".part-" + str(os.getpid()) + "-" + path.name)
    try:
        part.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        _retrying(lambda: os.replace(part, path))
    finally:
        if part.exists():
            part.unlink()   # same-call scratch this function created
    seen = _seen_path(path)
    try:
        clean = seen.stat().st_size == 0
    except OSError:
        clean = False
    if not clean:           # absent, or stamped by a look that found the record held
        _retrying(lambda: seen.write_bytes(b""))


def describe(report, limit=15):
    """Plain text for a host to show; '' when there is nothing to say."""
    lines = []
    if report.unbacked:
        lines.append("SAVE SENTINEL: {} save file(s) changed since the last look with no backup taken first:"
                     .format(len(report.unbacked)))
        lines += ["  {}: {}".format(kind, path) for kind, path in report.unbacked[:limit]]
        if len(report.unbacked) > limit:
            lines.append("  ... and {} more".format(len(report.unbacked) - limit))
    if report.truncated:
        lines.append("SAVE SENTINEL: watched folder(s) too large to list fully, so removals there are not reported: "
                     + ", ".join(report.truncated))
    if report.unread:
        lines.append("SAVE SENTINEL: folder(s) that could not be listed, so changes there are not known: "
                     + ", ".join(report.unread))
    if report.record_held:
        lines.append("SAVE SENTINEL: the record of the previous look is held by another process just now, so this look "
                     "compared nothing. The record is kept, and the next look reports anything changed since it.")
    if report.state_lost:
        lines.append("SAVE SENTINEL: the record of the previous look is gone or could not be read{}, so this look is a "
                     "new baseline and any change since the previous look is not reported."
                     .format(" ({})".format(report.lost_why) if report.lost_why else ""))
    return "\n".join(lines)
