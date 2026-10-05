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


# ---------------------------------------------------------------- restore (scripts/restore-from-backup.sh)

def read_manifest(backups):
    """({dest: entry}, unreadable line count). A later line for the same dest wins. A line that does not parse, or
    does not have the v1 shape, is skipped and COUNTED -- never silently dropped."""
    entries, bad = {}, 0
    try:
        data = (Path(backups) / MANIFEST).read_bytes()
    except OSError:
        return entries, 0
    for line in data.split(b"\n"):
        if not line.strip():
            continue
        try:
            e = json.loads(line.decode("utf-8"))
            if not (isinstance(e, dict) and e.get("v") == 1
                    and all(isinstance(e.get(k), str) for k in ("ts", "src", "dest", "sha256"))
                    and isinstance(e.get("size"), int) and not isinstance(e.get("size"), bool)):
                raise ValueError("not a v1 entry")
            if e["dest"] in ("", ".", "..") or "/" in e["dest"] or "\\" in e["dest"]:
                raise ValueError("dest is not a name inside the backups folder")
        except ValueError:
            bad += 1
            continue
        entries[e["dest"]] = e
    return entries, bad


def legacy_guess(name):
    """What the pre-manifest restore script printed: stamp stripped, then EVERY `_` turned into `/` (plan L8). Only
    ever shown for a backup the manifest does not know, labelled as a guess."""
    return re.sub(r"^[0-9]*_[0-9]*__", "", name).replace("_", "/")


def _native(path):
    """A Git Bash path (`/c/x`) as Windows spells it, when running on Windows."""
    m = re.match(r"^/([a-zA-Z])(/.*)?$", path)
    return "{}:{}".format(m.group(1).upper(), m.group(2) or "/") if (os.name == "nt" and m) else path


def _sha256(path):
    h = hashlib.sha256()
    with open(str(path), "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _rows(backups, entries):
    rows = []
    for dest, e in entries.items():
        f = backups / dest
        rows.append((e["ts"], dest, e["src"], f.stat().st_size if f.is_file() else None, ""))
    for f in backups.iterdir():
        if f.name in entries or not _NAMED.match(f.name) or ".part-" in f.name or not f.is_file():
            continue
        rows.append((f.name.split("__", 1)[0], f.name, legacy_guess(f.name), f.stat().st_size, "legacy"))
    return sorted(rows, reverse=True)


def _list(backups, search):
    entries, bad = read_manifest(backups)
    rows = _rows(backups, entries)
    if search:
        rows = [r for r in rows if search.lower() in r[2].lower() or search.lower() in r[1].lower()]
    print("=== Available Backups ===\n")
    if search:
        print("Filtering for: {}\n".format(search))
    for i, (ts, dest, src, size, kind) in enumerate(rows, 1):
        note = "(missing)" if size is None else "({} bytes)".format(size)
        if kind:
            note += "  (legacy: path guessed)"
        print("  [{}] {}  {}  {}  <- {}".format(i, ts, src, note, dest))
    if not rows:
        print("No backups found.")
    print("\nTotal: {} backup(s)".format(len(rows)))
    if bad:
        print("{} unreadable manifest line(s) skipped".format(bad))
    print("\nTo restore one (its sha256 is checked first; the current file is backed up before it is replaced):")
    print("  bash scripts/restore-from-backup.sh --restore <backup-file> [--to <path>]")
    print("\nTo view the audit log:\n  cat \"{}\"".format(backups / "AUDIT_LOG.txt"))
    return 0


def _restore(backups, name, to):
    from .audit import Audit
    f = backups / name
    if name in ("", ".", "..") or "/" in name or "\\" in name or not f.is_file():
        print("No backup named {!r} in {}".format(name, backups))
        return 2
    entries, _bad = read_manifest(backups)
    e = entries.get(name)
    if e is None and not to:
        print("{} was taken before backups were recorded: its original path is not known.".format(name))
        print("  guess: {}  (legacy: path guessed)".format(legacy_guess(name)))
        print("Re-run with --to <path> to say where it goes.")
        return 2
    if e is not None:
        got = _sha256(f)
        if got != e["sha256"]:
            print("REFUSED: {} does not match the sha256 recorded when it was taken (recorded {}, now {}). "
                  "Nothing was restored.".format(name, e["sha256"], got))
            return 1
    target = Path(_native(to or e["src"]))
    audit = Audit(str(backups.parent.parent))
    if target.is_file() and take(str(backups.parent.parent), "restore-from-backup", str(target), audit) is None:
        print("REFUSED: could not back up the current {} first. Nothing was restored.".format(target))
        return 1
    part = Path("{}.restore-part-{}".format(target, os.getpid()))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        _want, _copied, digest = _copy_hashed(str(f), part)
        if e is not None and digest != e["sha256"]:
            raise OSError("the backup changed while it was being restored")
        os.replace(str(part), str(target))
    except OSError as exc:
        _discard(part)
        print("FAILED: could not restore {} to {}: {}".format(name, target, exc))
        return 1
    audit.log("restore-from-backup", "RESTORED", "{} -> {}".format(name, target))
    print("Restored {} from {}{}".format(target, name, " (sha256 ok)" if e is not None else " (legacy: path given)"))
    return 0


def restore_main(argv):
    """argv: <backups dir> [search-term] | <backups dir> --restore <backup-file> [--to <path>]"""
    backups, rest = Path(argv[0]), list(argv[1:])
    if not backups.is_dir():
        print("No backup directory found at {}".format(backups))
        print("Set GAME_DIR to your Skyrim install path, or run from the game directory.")
        return 1
    if rest[:1] == ["--restore"]:
        if len(rest) not in (2, 4) or (len(rest) == 4 and rest[2] != "--to"):
            print("usage: restore-from-backup.sh --restore <backup-file> [--to <path>]")
            return 2
        return _restore(backups, rest[1], rest[3] if len(rest) == 4 else "")
    return _list(backups, rest[0] if rest else "")


def run(project, tool_name, file_path, payload_empty, audit, agent="", session_id=""):
    if payload_empty:
        audit.log(HOOK, "BLIND", "received an EMPTY payload -- no backup was taken for this call")
        return
    if not file_path or not os.path.isfile(file_path):
        return
    if any(grep(p, file_path) for p in SKIP):
        return
    take(project, tool_name, file_path, audit, agent, session_id)
