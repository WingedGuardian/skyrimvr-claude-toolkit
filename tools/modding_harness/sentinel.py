"""Post-call save sentinel: saves that changed since the last look without a committed backup.

A pre-call guard judges what a call names. Code it cannot read (a script that builds a save path at run time) changes
saves it never sees. This module notices afterwards: it compares a profile's save roots (plus any extra watched
folders) against the previous look and reports members MODIFIED or REMOVED with no committed pre-mutation backup
in the store since then. It only lists and stats; it never opens, copies or changes a save. New members are not
reported: creating a save changes none.

    state = load_state(path)
    report = check(profile, store, state, watch=(), now=None)   # -> Report; state is updated in place
    save_state(path, state)

The host decides how loudly to report (a game running may be the writer). Nothing here blocks: a post-call hook
cannot undo a call, and the store's committed transactions remain the only recovery authority.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from .profiles import comparison_key

WALK_LIMIT = 20000          # entries per watched folder; a larger one is reported as truncated
CLOCK_SLACK = 5.0           # seconds: a backup committed just before the previous look still counts


class Report(NamedTuple):
    first: bool             # no previous look: nothing compared, the baseline was recorded
    unbacked: tuple         # ((kind, path), ...) kind is "modified" or "removed"
    backed_up: tuple        # changes a committed backup covers
    truncated: tuple        # watched folders too large to list fully


def _member(name, extensions):
    low = name.lower()
    return any(low.endswith(ext.lower()) for ext in extensions)


def snapshot(roots, extensions, watch=(), limit=WALK_LIMIT):
    """{key: [size, mtime_ns, path]} for every save member: roots at top level (as games keep them), watched
    folders recursively. Returns (files, truncated)."""
    files, truncated = {}, []

    def add(path, st):
        files[comparison_key(os.path.abspath(path))] = [st.st_size, st.st_mtime_ns, str(path)]

    for root in roots:
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    if _member(entry.name, extensions) and entry.is_file():
                        add(entry.path, entry.stat())
        except OSError:
            continue
    for folder in watch:
        seen = 0
        for parent, _dirs, names in os.walk(folder):
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
    return files, tuple(truncated)


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
    now = time.time() if now is None else now
    files, truncated = snapshot(profile.save_roots, profile.extensions, watch)
    previous = state.get("files")
    since = state.get("time", now)
    first = not isinstance(previous, dict)
    unbacked, backed = [], []
    if not first:
        found = changes(previous, files)
        if found:
            keys = backed_up_since(store.root, since) | set(covered or ())
            for kind, path in found:
                (backed if comparison_key(os.path.abspath(path)) in keys else unbacked).append((kind, path))
    state.update({"v": 1, "time": now, "files": files})
    return Report(first, tuple(unbacked), tuple(backed), truncated)


def load_state(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and data.get("v") == 1 else {}


def save_state(path, state):
    """Replace the state atomically. Unlike durable.atomic_json (no-clobber, for immutable records) this is a
    cache that each look overwrites; losing it only makes the next look a baseline."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(".part-" + str(os.getpid()) + "-" + path.name)
    try:
        part.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        os.replace(part, path)
    finally:
        if part.exists():
            part.unlink()   # same-call scratch this function created


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
        lines.append("SAVE SENTINEL: watched folder(s) too large to list fully: " + ", ".join(report.truncated))
    return "\n".join(lines)
