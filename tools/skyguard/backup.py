"""Back up a file before it is edited (port of backup-before-edit.sh). Never blocks.

Plan 6 Task 13: the copy goes to `<dest>.part-<pid>` with a running sha256, is fsynced, size-checked, and only then
given its final name -- created EXCLUSIVELY, with a `-1`, `-2` ... suffix on the timestamp when a backup of the same
file already holds that second. One JSON line per backup is then appended to `backups/MANIFEST.jsonl`, so a restore
reads the original path instead of guessing it from the flattened name (every `_` used to become `/`).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from .grep import grep

HOOK = "backup-before-edit"
SKIP = (r"[/\\]\.claude[/\\](backups|hooks|plans)[/\\]", r"[/\\]node_modules[/\\]")
MANIFEST = "MANIFEST.jsonl"
CHUNK = 1024 * 1024
MAX_SUFFIX = 999
_hasher = hashlib.sha256
_LEGACY = re.compile(r"^\d{8}_\d{6}__")
_NAMED = re.compile(r"^\d{8}_\d{6}(-\d+)?__")


def _discard(path):
    """Remove this call's own part file. Its absence is fine; failing to remove it is not worth raising over."""
    try:
        os.unlink(str(path))
    except OSError:
        pass


def _copy_hashed(src, part):
    """Copy src to part in one read pass, hashing as it goes. Returns (size at open, bytes copied, sha256)."""
    h = _hasher()
    copied = 0
    with open(src, "rb") as fin:
        want = os.fstat(fin.fileno()).st_size
        with open(str(part), "wb") as fout:
            while True:
                chunk = fin.read(CHUNK)
                if not chunk:
                    break
                h.update(chunk)
                fout.write(chunk)
                copied += len(chunk)
            fout.flush()
            os.fsync(fout.fileno())
    return want, copied, h.hexdigest()


def _named(base, n):
    """`<ts>__<safe>`, then `<ts>-1__<safe>`, ... -- the suffix goes on the stamp so the file keeps its extension."""
    if n == 0:
        return base
    ts, rest = base.name.split("__", 1)
    return base.with_name("{}-{}__{}".format(ts, n, rest))


def _finalise(part, base):
    """Give the part its final name without ever replacing an existing backup. Returns the name taken."""
    for n in range(MAX_SUFFIX + 1):
        cand = _named(base, n)
        try:
            os.link(str(part), str(cand))
        except FileExistsError:
            continue
        except OSError:
            # No hard links on this volume: claim the name exclusively, then replace onto the name we now own.
            try:
                fd = os.open(str(cand), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                continue
            os.close(fd)
            os.replace(str(part), str(cand))
            return cand
        _discard(part)
        return cand
    raise OSError("no free backup name after {} tries".format(MAX_SUFFIX + 1))


def _append_line(path, line):
    """ONE os.write of one line. Small O_APPEND writes are atomic on NTFS (INFERRED), so a reader still skips and
    counts any line that does not parse."""
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def take(project, tool_name, file_path, audit, agent="", session_id=""):
    """Back up one existing file. Returns the backup's Path, or None when no backup exists (FAILED is logged)."""
    backups = Path(project) / ".claude" / "backups"
    ts = audit.stamp()
    # bash: sed 's|[/\\:]|_|g' | sed 's|^_*||'
    safe = re.sub(r"[/\\:]", "_", file_path).lstrip("_")
    base = backups / "{}__{}".format(ts, safe)
    part = Path("{}.part-{}".format(base, os.getpid()))
    try:
        backups.mkdir(parents=True, exist_ok=True)      # bash: mkdir -p 2>/dev/null, then cp fails -> FAILED
        want, copied, digest = _copy_hashed(file_path, part)
    except OSError:
        _discard(part)
        audit.log(HOOK, "FAILED", "could not copy {} -- NO BACKUP EXISTS for this edit".format(file_path))
        return None
    try:
        on_disk = os.stat(str(part)).st_size
    except OSError:
        on_disk = -1
    if not (on_disk == copied == want):
        _discard(part)
        audit.log(HOOK, "FAILED", "copy of {} has the wrong size (source {}, copied {}, on disk {}) -- NO BACKUP "
                  "EXISTS for this edit".format(file_path, want, copied, on_disk))
        return None
    try:
        dest = _finalise(part, base)
    except OSError:
        _discard(part)
        audit.log(HOOK, "FAILED", "could not name the copy of {} -- NO BACKUP EXISTS for this edit".format(file_path))
        return None
    entry = {"v": 1, "ts": ts, "src": file_path, "dest": dest.name, "size": copied, "sha256": digest,
             "tool": tool_name, "agent": agent, "session_id": session_id}
    try:
        _append_line(backups / MANIFEST, (json.dumps(entry, ensure_ascii=True) + "\n").encode("ascii"))
    except OSError:
        audit.log(HOOK, "FAILED", "backup {} of {} is not in {} -- a restore will have to guess its path".format(
            dest.name, file_path, MANIFEST))
    audit.raw("[{t}] {n} -> {p} (backup: {d})\n".format(t=ts, n=tool_name, p=file_path, d=dest.name))
    return dest


def run(project, tool_name, file_path, payload_empty, audit, agent="", session_id=""):
    if payload_empty:
        audit.log(HOOK, "BLIND", "received an EMPTY payload -- no backup was taken for this call")
        return
    if not file_path or not os.path.isfile(file_path):
        return
    if any(grep(p, file_path) for p in SKIP):
        return
    take(project, tool_name, file_path, audit, agent, session_id)
