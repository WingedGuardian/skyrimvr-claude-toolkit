#!/usr/bin/env python3
"""mirror-launch-logs -- snapshot the game's logs now, into an append-only mirror the game cannot rotate away.

The game and its plugins overwrite their logs at every launch: Papyrus.0.log becomes Papyrus.1.log and the
oldest falls off, sksevr.log / skse64.log start empty, a plugin's log is truncated. The evidence for a problem
from two launches ago is usually gone by the time anyone looks. Run this once after a session (or before
the next launch) and the logs are kept.

One shot: it copies, reports, and exits. No watcher, no background process.

    python tools/mirror-launch-logs.py [--dest DIR]

Sources (each folder discovered by tools/skyrim_paths.py; override with the variable named):
    SKSE folder      *.log, crash-*.txt    $SKSE_LOG_DIR    (SKSE and plugin logs, CrashLogger dumps)
    crash folder     crash-*.log/.txt      $SKSE_CRASH_DIR  (the same folder unless overridden)
    Papyrus folder   Papyrus.*.log         $PAPYRUS_LOG_DIR
Destination: <toolkit>/.claude/backups/log-mirror/<SKSE|crash|Papyrus>/<file name> (never shipped, never
tracked), or --dest DIR.

Per file, against its mirror copy:
    NEW        no mirror copy yet: copied whole
    APPENDED   the log grew: only the new bytes are copied (the copy is never rewritten)
    UNCHANGED  nothing new: the copy is not touched
    REPLACED   the log was truncated or replaced (a new launch): the previous copy is KEPT, renamed
               <name>.replaced-<UTC time>, and a fresh copy started. Nothing is ever overwritten or deleted.
    SHORT      the log shrank WHILE it was being read (the game rotated it mid-copy): what was read is kept,
               and the next run sees the mismatch and keeps it as a REPLACED copy. Exit 1.

"Same log, grown" is decided by comparing the copy's first and last 4 KB with the same ranges of the source;
a replacement that matched both by coincidence would be taken for an append. Each pass copies at most the
size the source had when it was opened, so a log still being written cannot make a pass run forever.

Exit: 0 mirrored · 1 a file could not be read, or shrank while being read · 2 bad usage.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skyrim_paths  # noqa: E402

SAMPLE = 4096
CHUNK = 1024 * 1024
DEFAULT_DEST = skyrim_paths.TOOLKIT_ROOT / ".claude" / "backups" / "log-mirror"
STATUSES = ("NEW", "APPENDED", "UNCHANGED", "REPLACED", "SHORT", "FAILED")

# (label, env override, folder under My Games/<game>/, file patterns)
SOURCES = (
    ("SKSE", "SKSE_LOG_DIR", "SKSE", ("*.log", "crash-*.txt")),
    ("crash", "SKSE_CRASH_DIR", "SKSE", ("crash-*.log", "crash-*.txt")),
    ("Papyrus", "PAPYRUS_LOG_DIR", "Logs/Script", ("Papyrus.*.log",)),
)


def open_source(path: Path):
    """The source handle (a seam for the mid-read truncation test)."""
    return path.open("rb")


def _same_log(old, src, old_size: int, size: int) -> bool:
    if old_size > size:
        return False
    for pos in sorted({0, max(0, old_size - SAMPLE)}):
        length = min(SAMPLE, old_size - pos)
        old.seek(pos)
        src.seek(pos)
        if old.read(length) != src.read(length):
            return False
    return True


def _keep_previous(destination: Path) -> Path:
    """Rename the existing copy aside; never overwrite one already kept."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(destination.stat().st_mtime))
    kept = destination.with_name("{}.replaced-{}".format(destination.name, stamp))
    n = 2
    while kept.exists():
        kept = destination.with_name("{}.replaced-{}-{}".format(destination.name, stamp, n))
        n += 1
    destination.rename(kept)
    return kept


def mirror(source, destination):
    """Bring the mirror copy up to date. Returns (status, bytes written, the kept previous copy or None)."""
    source, destination = Path(source), Path(destination)
    kept = None
    with open_source(source) as src:
        size = os.fstat(src.fileno()).st_size
        destination.parent.mkdir(parents=True, exist_ok=True)
        offset, status = 0, "NEW"
        if destination.exists():
            with destination.open("rb") as old:
                old_size = os.fstat(old.fileno()).st_size
                same = _same_log(old, src, old_size, size)
            if same and old_size == size:
                return "UNCHANGED", 0, None
            if same:
                offset, status = old_size, "APPENDED"
            else:
                kept, status = _keep_previous(destination), "REPLACED"
        src.seek(offset)
        written = 0
        with destination.open("ab" if status == "APPENDED" else "xb") as dst:
            while written < size - offset:
                block = src.read(min(CHUNK, size - offset - written))
                if not block:
                    status = "SHORT"          # the source shrank mid-read; the next pass keeps this copy aside
                    break
                dst.write(block)
                written += len(block)
        return status, written, kept


def collect(sources=SOURCES):
    """[(label, source file)], each real file once (the crash folder is usually the SKSE folder), plus the folders
    looked in."""
    seen, out, folders = set(), [], []
    for label, env, sub, patterns in sources:
        folder, _ = skyrim_paths.resolve_dir(env, sub)
        folders.append((label, folder))
        if not folder.is_dir():
            continue
        for pattern in patterns:
            for p in sorted(folder.glob(pattern)):
                key = os.path.normcase(str(p.resolve()))
                if p.is_file() and key not in seen:
                    seen.add(key)
                    out.append((label, p))
    return out, folders


def main(argv) -> int:
    args, dest = list(argv), DEFAULT_DEST
    if "--dest" in args:
        i = args.index("--dest")
        if i + 1 >= len(args):
            print("usage: mirror-launch-logs.py [--dest DIR]   (--dest needs a folder)", file=sys.stderr)
            return 2
        dest = Path(args[i + 1])
        del args[i:i + 2]
    if args:
        print("usage: mirror-launch-logs.py [--dest DIR]   (got {})".format(args), file=sys.stderr)
        return 2

    files, folders = collect()
    print("mirror: {}".format(dest))
    for label, folder in folders:
        print("  {:<8} from {}{}".format(label, folder, "" if folder.is_dir() else "  (not found: nothing to copy)"))
    counts = {s: 0 for s in STATUSES}
    for label, src in files:
        try:
            status, written, kept = mirror(src, dest / label / src.name)
        except OSError as e:
            status, written, kept = "FAILED", 0, None
            print("  FAILED    {}/{}: {}".format(label, src.name, e))
        counts[status] += 1
        if status == "FAILED":
            continue
        note = ""
        if status in ("NEW", "APPENDED", "REPLACED"):
            note = "  +{:,} bytes".format(written)
        if kept is not None:
            note += "  (previous copy kept as {})".format(kept.name)
        if status == "SHORT":
            note = "  the log shrank while it was read ({:,} bytes kept); run again".format(written)
        print("  {:<9} {}/{}{}".format(status, label, src.name, note))
    print("  {} file(s): ".format(len(files)) + ", ".join("{} {}".format(counts[s], s) for s in STATUSES))
    return 1 if counts["FAILED"] or counts["SHORT"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
