"""Snapshot every .psc/.pex before a non-read-only shell command, so a broken experiment can be rolled back.

Plan 6 Task 14 (Plan 5 R16). The bash-era hook copied only TOP-LEVEL `Data/Scripts/Source/*.psc` and `Data/Scripts/*.pex`
younger than 7 days, never looked at the SSE layout `Data/Source/Scripts/`, and copied the same bytes again on every
snapshot (MEASURED on the real install, Plan 5 Task 16: 3,428 of 11,898 script files never seen; 2.82 GB of snapshots
holding 89 distinct contents). Now:

* WHAT: every `.psc` / `.pex` (any case) under `Data/Scripts/**` (which holds `Data/Scripts/Source/**`) and
  `Data/Source/Scripts/**`, whatever its age. One `os.scandir` walk; on Windows a DirEntry's size and mtime come with
  the listing, so the walk makes no per-file stat call. A linked FOLDER (junction, symlink, any reparse point) is never
  entered -- it is counted and logged -- and the walk has its own time box (WALK_BOX); a walk cut short is PARTIAL and
  never reads as deletions.
* STORE: `auto_snapshots/blobs/<sha256>`, content-addressed, so unchanged bytes are never stored twice. A blob is
  written by backup.py's helpers (part file, sha256 in the same pass, size check) and only then linked to its final
  name. Blobs are NOT fsynced (controller ruling, fix round 1): a blob already present at its name is size-checked and
  replaced when wrong, and `materialise` re-hashes every blob before writing. The index and manifests are fsynced.
* INDEX: `auto_snapshots/index.json` keeps `(size, mtime_ns, sha256, nlink)` per path; only a path whose entry changed
  is read and hashed again. A path with more than one hard link is re-stat'd on every comparison: NTFS lists a hard
  link with its own directory entry's size/mtime, which an edit through the OTHER link does not update.
* ORDER: a baseline reads the newest files first, so the scripts being worked on are captured inside the first box.
* SNAPSHOT: one manifest per snapshot, `auto_snapshots/manifests/<stamp>__snapshot.json`, taken only when something
  changed. A manifest is FULL (`"base": null`, `"set"` = every path -> sha256) or a DELTA against one full manifest
  (`"set"` = paths whose content differs from that base, `"gone"` = paths the base has and this snapshot does not);
  `resolve()` turns either into the full path -> sha256 map. Deltas are never chained (a delta's base is always full),
  so any snapshot is readable from two files. Why deltas: a full map of the measured install is ~1.4 MB, and one per
  snapshot for 14 days is gigabytes on a drive that is already 87% full.
* BASELINE: the first walk hashes everything (36.7 MB on the measured install). It is time-boxed: 20 s at
  SessionStart (less when the engine's own time budget has less left), CALL_BOX per tool call. Unfinished, it resumes
  on the next call (the rate limit does not apply until it completes), and the snapshot it wrote is `PARTIAL` in the
  audit log and `"partial": true` in its manifest.
* RATE LIMIT (Task 14's pre-committed latency gate; the measured branch is stated at RATE): the walk always runs; a
  full index comparison runs at most once per RATE seconds, unless a walked file is newer than the last full walk.
  Stated gaps: inside the window, a file replaced with an OLDER mtime (an mtime-preserving copy or extract), and an edit
  made through another hard link (its listing is stale until the comparison re-stats it), are not seen until the window
  ends; a rewrite that keeps both the size and the mtime is not seen by the index at all.
* PRUNING keeps every blob referenced by the newest snapshot, by any snapshot younger than 14 days, or by the index
  (the live files' content); and it keeps, whatever its age, the newest snapshot that holds each path's latest content,
  so the only copy of a deleted script's last version is never pruned. Pruning never deletes: what it removes is
  RETIRED into <project>/.claude/retired/ (retire.py), and housekeep's session sweep sends it to the Recycle Bin (the
  user's rule has no carve-out for the toolkit's own store; review L2 LOW-7).
"""
from __future__ import annotations

import bisect
import json
import os
import re
import stat
import time
from pathlib import Path

from . import backup, retire
from .grep import grep

SKIP = (r"^\s*(ls|cat|head|tail|grep|find|wc|file|stat|pwd|echo|date|whoami|which|type|env|printenv)\s",
        r"^\s*git\s+(status|log|diff|show|branch|remote|config\s+--get)")
# The PowerShell tool is wired to this hook too (E12). Its read-only verbs take no snapshot, like `ls`. PowerShell
# verbs are case-insensitive, so these match any case; the bash list above stays case-sensitive (`LS x` snapshots).
SKIP_POWERSHELL = r"^\s*(get-[a-z]+|select-string|test-path|resolve-path|measure-object)(\s|$)"
HOOK = "snapshot-before-tool"
DAY = 86400
TREES = ("Data/Scripts", "Data/Source/Scripts")   # Data/Scripts/** holds the LE layout Data/Scripts/Source/** too
EXTS = (".psc", ".pex")
# Latency gate (controller ruling: the added PER-CALL p95 over the real call stream): 131-137 ms MEASURED by replaying
# 15,138 real walking hook calls against measured shape costs -> the <= 150 ms branch, the 60 s rate limit. The worst
# single-call shape is 160-168 ms p95. Source and numbers: docs/evidence/2026-10-04-plan6-pricing.md.
RATE = 60
KEEP_DAYS = 14
BASELINE_BOX = 20.0       # seconds at SessionStart
# Seconds per tool call while a baseline is unfinished: inside the engine's measured ~0.25-0.35 s of p95 headroom under
# the 1 s per-wrapper gate (Plan 4 Risk 2), so finishing a baseline never makes a call miss that gate.
CALL_BOX = 0.25
# The walk has its own box: no tree (a huge folder, a slow disk) can hold a hook past it. MEASURED walk of the Task 6
# tree: 22 ms p95, so 1 s is ~45x headroom; a truncated walk compares only what it saw and is reported.
WALK_BOX = 1.0
CALL_LOCK_WAIT = 0.2      # a tool call waits this long for the store lock, then takes no snapshot (inside the 1 s gate)
PRUNE_CALL_BOX = 1.0      # the once-a-day pruning inside a tool call stops deleting after this long
PRUNE_EVERY = DAY         # per-call pruning at most this often; SessionStart always prunes
RESTAT_BOX = 0.075        # cap on re-stat'ing hard-linked scripts per comparison (real install MEASURED: 58 ms in all)
RESTAT_CURSOR = ".restat_cursor"
# Re-review N3: a path indexed with ONE link is re-stat'd in turn, at most SWEEP_MAX paths and SWEEP_BOX seconds per
# comparison (at least one path), from a position cursor, so a hard link made AFTER indexing is found. The listing
# cannot tell: on Windows a DirEntry's st_nlink is always 0 (MEASURED, Task 15). The box uses perf_counter, not
# _walk_clock: time.monotonic ticks every ~15.6 ms on Windows (Python 3.10), and a 10 ms monotonic box MEASURED 15.5 ms.
# MEASURED on an 8,850-path synthetic index: ~24 us per os.stat, so ~250 paths (~7 ms in all) per comparison and a full
# round in ~35 comparisons (>= 35 min at RATE 60).
SWEEP_MAX = 256
SWEEP_BOX = 0.006
SWEEP_CURSOR = ".sweep_cursor"
_sweep_clock = time.perf_counter
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_walk_clock = time.monotonic      # bound at import: the walk's box is real time even where a test fakes snapshot.time
REBASE_MIN = 256          # a delta larger than max(REBASE_MIN, base/REBASE_SHARE) paths is written as a new full map
REBASE_SHARE = 20
INDEX = "index.json"
PARTIAL = ".partial"      # present while the baseline is unfinished
_MANIFEST = re.compile(r"^(\d{8}_\d{6})(?:-(\d+))?__snapshot\.json$")
_LEGACY_DIR = re.compile(r"^\d{8}_\d{6}$")        # the bash-era per-snapshot copy folders
_BLOB = re.compile(r"^[0-9a-f]{64}$")


def store_dir(project):
    return Path(project) / ".claude" / "backups" / "auto_snapshots"


# ---------------------------------------------------------------- walk


def walk(project):
    """{rel: (size, mtime_ns)} for every .psc/.pex under TREES (see scan)."""
    return scan(project)[0]


def _is_link(e):
    """A symlink, a junction, or any other reparse point. On Windows `is_dir(follow_symlinks=False)` is True for a
    JUNCTION and `is_symlink()` False (review I1, MEASURED: a junction to `Data` recursed to MAX_PATH), so the reparse
    attribute is read too -- it comes with the directory listing, no extra call."""
    if e.is_symlink():
        return True
    return bool(getattr(e.stat(follow_symlinks=False), "st_file_attributes", 0) & _REPARSE)


def scan(project, deadline=None):
    """(files, linked, truncated). files = {rel: (size, mtime_ns)} for every .psc/.pex under TREES, recursively; rel
    uses `/`, from the project root. A linked FOLDER (junction, symlink, other reparse point) is never entered: it is
    listed in `linked`, so it can be reported. A linked FILE is read through its link (one file, no loop). `truncated`
    is True when the walk reached `deadline` (a `_walk_clock()` time) before it finished."""
    out, linked = {}, []
    for tree in TREES:
        stack = [(os.path.join(str(project), *tree.split("/")), tree + "/")]
        while stack:
            if deadline is not None and _walk_clock() >= deadline:
                return out, linked, True
            d, rel = stack.pop()
            try:
                it = os.scandir(d)
            except OSError:
                continue
            with it:
                for k, e in enumerate(it):
                    if deadline is not None and k % 256 == 255 and _walk_clock() >= deadline:
                        return out, linked, True
                    try:
                        if e.is_dir():                # follows links: a real folder, a junction or a folder symlink
                            if _is_link(e):           # only folders are checked: a FILE link is read through
                                linked.append(rel + e.name)
                            else:
                                stack.append((e.path, rel + e.name + "/"))
                        elif e.name[-4:].lower() in EXTS and e.is_file():
                            st = e.stat()
                            out[rel + e.name] = (st.st_size, st.st_mtime_ns)
                    except OSError:
                        continue
    return out, linked, False


# ---------------------------------------------------------------- store primitives


def _read_json(path):
    try:
        return json.loads(Path(path).read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return None


def _write_replace(path, data):
    """Mutable state (the index): part file, fsync, then replace."""
    part = Path("{}.part-{}".format(path, os.getpid()))
    try:
        with open(str(part), "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(part), str(path))
    except OSError:
        backup._discard(part)
        raise


def _store_blob(blobs, src):
    """Copy `src` into the store under its sha256 (backup.py's hashed part-file copy). -> sha256, or None when the file
    could not be read or changed size while it was copied (the index keeps its old entry, so the next walk retries).
    (A save is stored by recovery._store_verified instead: fsynced, read back, and inside the hook's deadline.)"""
    part = blobs / ".part-{}".format(os.getpid())
    try:
        want, copied, digest = backup._copy_hashed(src, part, fsync=False)   # see backup._copy_hashed
        on_disk = os.stat(str(part)).st_size
    except OSError:
        backup._discard(part)
        return None
    if not (on_disk == copied == want):
        backup._discard(part)
        return None
    dest = blobs / digest
    try:
        os.link(str(part), str(dest))
    except FileExistsError:
        # Already stored -- unless a power loss tore it (blobs are not fsynced): a blob of the wrong size is replaced.
        if not _blob_size_is(dest, copied) and not _replace(part, dest):
            return None
    except OSError:
        if not _blob_size_is(dest, copied) and not _replace(part, dest):   # no hard links on this volume
            return None
    backup._discard(part)
    return digest


def _blob_size_is(dest, size):
    try:
        return os.stat(str(dest)).st_size == size
    except OSError:
        return False


def _replace(part, dest):
    try:
        os.replace(str(part), str(dest))
        return True
    except OSError:
        backup._discard(part)
        return False


def manifest_names(store):
    """Every manifest's file name, oldest first (stamp, then the collision suffix as a number)."""
    try:
        names = [n for n in os.listdir(str(Path(store) / "manifests")) if _MANIFEST.match(n)]
    except OSError:
        return []
    return sorted(names, key=_order)


def _order(name):
    m = _MANIFEST.match(name)
    return (m.group(1), int(m.group(2) or 0))


def _load_manifest(store, name):
    m = _read_json(Path(store) / "manifests" / name)
    if not (isinstance(m, dict) and m.get("v") == 1 and isinstance(m.get("set"), dict)
            and isinstance(m.get("gone", []), list) and (m.get("base") is None or isinstance(m.get("base"), str))):
        return None
    return m


def resolve(store, name, _cache=None):
    """The full {rel: sha256} map of one snapshot. Raises ValueError when it (or its base) cannot be read."""
    m = _load_manifest(store, name)
    if m is None:
        raise ValueError("snapshot {} is unreadable".format(name))
    if m["base"] is None:
        return dict(m["set"])
    base = _load_manifest(store, m["base"])
    if base is None or base["base"] is not None:
        raise ValueError("snapshot {} names an unreadable base {}".format(name, m["base"]))
    out = dict(base["set"])
    for rel in m["gone"]:
        out.pop(rel, None)
    out.update(m["set"])
    return out


class Damaged(ValueError):
    """Selected blobs are missing, or their bytes no longer hash to their names. `bad` = [(path, problem)]."""

    def __init__(self, name, bad):
        super().__init__("snapshot {}: {} -- nothing was written; the other files can be restored one by one".format(
            name, "; ".join("the blob for {} {}".format(rel, why) for rel, why in bad)))
        self.bad = bad


def check_blobs(store, name, files):
    """Before anything is written: every entry's path must stay inside its destination and its hash be a sha256 (a
    tampered manifest raises ValueError -- never TypeError, re-review N2), and every blob is re-hashed (blobs are not
    fsynced). -> [(path, problem)] for each blob that is missing or damaged (Task 15: the caller can then restore the
    others file by file, re-review N4)."""
    bad = []
    for rel, sha in files.items():
        parts = rel.split("/")
        if not isinstance(sha, str) or not _BLOB.match(sha) or "\\" in rel or ":" in rel or any(p in ("", ".", "..") for p in parts):
            raise ValueError("snapshot {}: refusing entry {!r} -> {!r}".format(name, rel, sha))
        blob = Path(store) / "blobs" / sha
        if not blob.is_file():
            bad.append((rel, "is missing ({})".format(sha)))
            continue
        if backup._sha256(blob) != sha:
            bad.append((rel, "does not hash to {} -- it is damaged".format(sha)))
    return bad


def materialise(project, name, dest, only=None):
    """Write snapshot `name`'s files under `dest`, at their recorded relative paths. -> number of files written.
    `only`: just these relative paths (per-file restore). Raises ValueError, before writing anything, for an unreadable
    snapshot or a bad entry (check_blobs), and Damaged (a ValueError) naming every selected blob that is missing or
    damaged."""
    store = store_dir(project)
    files = resolve(store, name)
    if only is not None:
        files = {r: s for r, s in files.items() if r in only}
    bad = check_blobs(store, name, files)
    if bad:
        raise Damaged(name, bad)
    return write_blobs(store, [(Path(dest).joinpath(*rel.split("/")), sha) for rel, sha in sorted(files.items())])


def restore_target_state(path):
    """Identity and content a restore may replace; an absent path remains explicitly absent."""
    from modding_harness import durable
    path = Path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(info.st_mode) or path.is_symlink() or
            getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
        raise OSError("restore target is not an ordinary unlinked file: " + str(path))
    before = durable.identity(path)
    digest = backup._sha256(path)
    if durable.identity(path) != before:
        raise OSError("restore target changed while inspecting: " + str(path))
    return {"identity": list(before), "sha256": digest}


def write_blobs(store, pairs, replace=False, expected=None):
    """Copy each (target, sha256) blob of `store` to its target. Each file is written to a part file and swapped in, so
    a hard-linked target (Vortex) is replaced, never written through. The part file is hashed BEFORE it replaces the
    target (the save policy: a restore is hash-verified at the file it writes, not only at the blob it reads). -> files
    written; OSError reports how many files were published. Quiesce other writers for in-place restore:
    identity/hash rechecks are not a filesystem compare-and-swap. Initially absent paths never replace a creator."""
    from modding_harness import durable
    import uuid
    pairs = list(pairs)
    if replace:
        expected = ({str(t): restore_target_state(t) for t, _ in pairs} if expected is None else expected)
        if set(expected) != {str(t) for t, _ in pairs}:
            raise OSError("restore observations do not cover the complete target set")
    else:
        expected = {str(t): None for t, _ in pairs}
    store, staged = Path(store), []
    operation = uuid.uuid4().hex
    journal = store / "restore-journals" / operation
    journal.mkdir(parents=True, exist_ok=False)
    durable.atomic_json(journal / "plan.json", {"targets": [str(t) for t, _ in pairs], "replace": replace,
                                             "observed": expected})
    n = 0
    try:
        for target, sha in pairs:
            target = Path(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            part = target.with_name(target.name + ".restore-part-" + uuid.uuid4().hex)
            staged.append((part, target))
            durable.copy_verified(store / "blobs" / sha, part)
            if backup._sha256(part) != sha:
                raise OSError("the copy of {} does not hash to {}".format(target.name, sha))
        if replace:
            for _, target in staged:
                if restore_target_state(target) != expected[str(target)]:
                    raise OSError("restore target changed after its prebackup: " + str(target))
        for part, target in staged:
            if replace and expected[str(target)] is not None:
                if restore_target_state(target) != expected[str(target)]:
                    raise OSError("restore target changed before publication: " + str(target))
                os.replace(str(part), str(target))
                durable.sync_dir(target.parent)
            else:
                durable.publish_new(part, target)
            n += 1
            durable.atomic_json(journal / ("{}-published.json".format(n)), {"path": str(target)})
        durable.atomic_json(journal / "complete.json", {"written": n})
    except OSError as exc:
        raise OSError("{}; {} file(s) published; restore journal {}".format(exc, n, journal)) from exc
    finally:
        for part, _ in staged:
            backup._discard(part)
    return n


def _write_manifest(store, ts, now, command, files, partial, head_base):
    """Write one snapshot's manifest under an exclusive name. -> (name, base name of the full map it uses)."""
    base_name, delta = head_base, None
    if base_name is not None:
        base = _load_manifest(store, base_name)
        if base is not None and base["base"] is None:
            bset = base["set"]
            changed = {r: s for r, s in files.items() if bset.get(r) != s}
            gone = sorted(r for r in bset if r not in files)
            if len(changed) + len(gone) <= max(REBASE_MIN, len(bset) // REBASE_SHARE):
                delta = (changed, gone)
    body = {"v": 1, "ts": ts, "t": int(now), "command": command, "partial": partial}
    if delta is None:
        body.update({"base": None, "set": files, "gone": []})
    else:
        body.update({"base": base_name, "set": delta[0], "gone": delta[1]})
    mdir = store / "manifests"
    final = mdir / "{}__snapshot.json".format(ts)
    part = Path("{}.part-{}".format(final, os.getpid()))
    try:
        with open(str(part), "wb") as fh:
            fh.write(json.dumps(body, ensure_ascii=True, sort_keys=True).encode("ascii"))
            fh.flush()
            os.fsync(fh.fileno())
        dest = backup._finalise(part, final)
    except OSError:
        backup._discard(part)
        raise
    return dest.name, (dest.name if delta is None else base_name)


# ---------------------------------------------------------------- the snapshot


def read_only(command):
    """Skip only when the complete parsed command consists of known reads.

    This classifies ordinary builtins, not shell functions, aliases or programs'
    runtime configuration. Unknown or incomplete syntax takes a snapshot.
    """
    from . import shellparse
    bash_prefix = any(grep(p, command, icase=False) for p in SKIP)
    if not (bash_prefix or grep(SKIP_POWERSHELL, command, icase=True)):
        return False
    parsed = shellparse.parse(command, "bash" if bash_prefix else "powershell")
    if not parsed.ok or not parsed.execs:
        return False
    reads = {"ls", "cat", "head", "tail", "grep", "wc", "file", "stat", "pwd", "echo", "date", "whoami",
             "which", "type", "printenv", "get-childitem", "get-content", "get-item", "get-itemproperty",
             "get-date", "get-process", "get-command", "get-location", "get-filehash", "get-acl", "get-culture",
             "get-variable", "get-module", "get-service", "select-string", "test-path", "resolve-path", "measure-object"}
    for call in parsed.execs:
        if any(">" in op for op, _ in call.redirects):
            return False
        program, args = call.program, call.argv
        if program in reads:
            continue
        if program == "env" and not args:
            continue
        if program == "find" and not any(a in ("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls")
                                          for a in args):
            continue
        if program == "git" and args and not any(a == "--output" or a.startswith("--output=") for a in args):
            sub, rest = args[0], args[1:]
            if sub in ("status", "log", "diff", "show"):
                continue
            if sub == "remote" and all(a in ("-v", "--verbose") for a in rest):
                continue
            if sub == "branch" and all(a in ("-a", "-r", "-v", "-vv", "--all", "--remotes", "--verbose", "--list")
                                        or ("--list" in rest and not a.startswith("-")) for a in rest):
                continue
            if sub == "config" and rest and rest[0] in ("--get", "--get-all", "--get-regexp", "--get-urlmatch"):
                continue
        return False
    return True


def run(project, command, payload_empty, audit, now=None):
    if payload_empty:
        audit.raw("[{}] snapshot-before-tool BLIND: EMPTY payload -- no snapshot taken\n".format(audit.stamp()))
        return
    if read_only(command):
        return
    take(project, audit, command, now=now)


def session_start(project, source, audit, box=BASELINE_BOX, now=None):
    """SessionStart: the first-ever baseline (time-boxed), or an ordinary snapshot ignoring the rate limit; then
    prune. Never on `compact` (nothing new happened, and the hook keeps that event cheap)."""
    if source == "compact":
        return None
    if box <= 0:
        audit.log(HOOK, "SKIPPED", "no time left in the session-start budget -- the snapshot runs on the next call")
        return None
    return take(project, audit, "session start ({})".format(source or "unknown"), now=now, box=box, rate=0,
                prune_due=True, lock_wait=None)


def take(project, audit, command, now=None, box=CALL_BOX, rate=RATE, prune_due=False, lock_wait=CALL_LOCK_WAIT):
    """Walk, store what changed, write a manifest when anything did. -> the manifest name written, or None."""
    t0 = time.monotonic()
    w0 = _walk_clock()
    now = time.time() if now is None else now
    root = Path(project)
    if not any((root / t).is_dir() for t in TREES):
        return None
    store = store_dir(project)
    try:
        (store / "blobs").mkdir(parents=True, exist_ok=True)
        (store / "manifests").mkdir(exist_ok=True)
    except OSError:
        audit.log(HOOK, "FAILED", "could not create {} -- no snapshot was taken".format(store))
        return None
    lock = backup._exclusive(store / ".lock", wait=lock_wait)
    try:
        lock.__enter__()
    except OSError:
        audit.log(HOOK, "SKIPPED", "another snapshot holds the store lock -- this call took none")
        return None
    try:
        return _take_locked(root, store, audit, command, now, t0 + box, rate, prune_due,
                            w0 + max(box, WALK_BOX), w0 + box)
    finally:
        lock.__exit__(None, None, None)


def _restat_multilink(root, store, files, seen, audit):
    """Review I2: on NTFS the listing's size/mtime for a hard link is its OWN directory entry, which an edit made
    through ANOTHER link does not update (MEASURED: 9 bytes listed, 16 on disk). For every path the index knows to have
    more than one link, ask the file itself (os.stat opens it). Real install, MEASURED: 3,049 of 11,899 scripts.

    Capped at RESTAT_BOX (ruling on the gate, condition 3): a fully hard-linked install (11,898 os.stat MEASURED at
    186 ms warm / 442 ms cold) must not stretch one call. The pass resumes where it stopped at the next comparison, in
    path order, from a cursor in a small sidecar file (never the 2 MB index). A path not reached this call keeps its
    listing values, so an edit made through THIS link is still seen at once. -> (re-stat'd, multi-link total)."""
    # Serial on purpose: MEASURED 58 ms for 2,975 paths serially, 82-86 ms with 2-8 threads.
    multi = sorted(rel for rel, v in files.items() if len(v) > 3 and v[3] > 1 and rel in seen)
    if not multi:
        return 0, 0
    cursor_file = store / RESTAT_CURSOR
    try:
        after = cursor_file.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        after = ""
    start = bisect.bisect_right(multi, after) if after else 0
    order = multi[start:] + multi[:start]
    base = str(root)
    stop = _walk_clock() + RESTAT_BOX
    done = 0
    for done, rel in enumerate(order, 1):
        try:
            st = os.stat(os.path.join(base, *rel.split("/")))
        except OSError:
            st = None
        if st is not None:
            seen[rel] = (st.st_size, st.st_mtime_ns)
        if done < len(order) and _walk_clock() >= stop:
            break
    try:
        if done < len(order):
            cursor_file.write_bytes(order[done - 1].encode("utf-8"))
            audit.log(HOOK, "RESTAT-PARTIAL", "{} of {} hard-linked script(s) re-checked inside the {:g} s cap -- "
                      "the rest at the next comparison".format(done, len(order), RESTAT_BOX))
        elif cursor_file.exists():
            cursor_file.unlink()
    except OSError:
        pass
    return done, len(order)


def _sweep_links(root, store, files, seen):
    """Re-review N3: re-stat single-link indexed paths in turn (SWEEP_BOX per comparison). One found to have gained a
    link gets its link count recorded -- from then on _restat_multilink re-stats it on every comparison -- and its
    REAL size/mtime in `seen`, so an edit already made through the new link is compared now. -> paths whose recorded
    link count changed (the index must be rewritten for them even when no content changed)."""
    keys = [rel for rel, v in files.items() if rel in seen and not (len(v) > 3 and v[3] > 1)]
    if not keys:
        return 0
    cursor_file = store / SWEEP_CURSOR
    try:
        start = int(cursor_file.read_bytes().decode("ascii").strip()) % len(keys)
    except (OSError, ValueError):
        start = 0
    base = str(root)
    stop = _sweep_clock() + SWEEP_BOX
    relinked, k = 0, 0
    while k < len(keys):
        rel = keys[(start + k) % len(keys)]
        k += 1
        try:
            st = os.stat(os.path.join(base, *rel.split("/")))
        except OSError:
            st = None
        if st is not None and st.st_nlink > 1:
            v = files[rel]
            if len(v) > 3:
                v[3] = st.st_nlink
            else:
                v.append(st.st_nlink)
            seen[rel] = (st.st_size, st.st_mtime_ns)
            relinked += 1
        if k >= SWEEP_MAX or _sweep_clock() >= stop:
            break
    try:
        cursor_file.write_bytes(str((start + k) % len(keys)).encode("ascii"))
    except OSError:
        pass
    return relinked


def _take_locked(root, store, audit, command, now, deadline, rate, prune_due, walk_deadline, box_end):
    walk_ns = time.time_ns()
    seen, linked, truncated = scan(root, walk_deadline)
    rl = store / ".last_snapshot"
    partial_marker = store / PARTIAL
    if rate and rl.is_file() and not partial_marker.exists():
        try:
            last = int(rl.read_bytes().decode("utf-8", "replace").strip() or "0")
            rl_ns = rl.stat().st_mtime_ns
        except (OSError, ValueError) as e:
            # fix-limits 7: an unreadable stamp reads as "never compared", so this call compares in full despite the
            # window and the write below repairs it -- said once, because the next call reads the repaired stamp.
            last, rl_ns = 0, 0
            audit.log(HOOK, "RATE-STAMP", ".last_snapshot is {} -- this call compared every script despite the {} s "
                      "rate limit and rewrites it".format(
                          "not a whole number of seconds" if isinstance(e, ValueError) else "unreadable", rate))
        if int(now) - last < rate and not any(m > rl_ns for _s, m in seen.values()):
            return None
    idx = _read_json(store / INDEX)
    if not (isinstance(idx, dict) and idx.get("v") == 1 and isinstance(idx.get("files"), dict)):
        idx = {"v": 1, "files": {}, "head_base": None, "dirty": False, "pruned": 0}
    files = idx["files"]
    _restat_multilink(root, store, files, seen, audit)
    relinked = _sweep_links(root, store, files, seen)
    todo = [r for r, st in seen.items() if (files.get(r) or [None, None])[:2] != list(st)]
    # Review I3: newest first -- the scripts being experimented on are the recent ones, so a time-boxed baseline
    # captures them before the long tail of untouched mod scripts.
    todo.sort(key=lambda r: (-seen[r][1], r))
    gone = [] if truncated else [r for r in files if r not in seen]   # an unwalked folder is not "deleted"
    blobs = store / "blobs"
    changed, unread, failed, altered = [], [], 0, 0
    for i, rel in enumerate(todo):
        if i and time.monotonic() >= deadline:        # at least one file per call: a slow walk cannot stall a baseline
            unread = todo[i:]
            break
        path = str(root.joinpath(*rel.split("/")))
        try:
            st = os.stat(path)                        # BEFORE the copy: an edit during it shows at the next walk
        except OSError:
            failed += 1
            continue
        sha = _store_blob(blobs, path)
        if sha is None:
            failed += 1                               # unreadable or changing: its old entry stays, retried next walk
            continue
        before = files.get(rel)
        if before is None or before[2] != sha:
            changed.append(rel)
            altered += before is not None
        # The link count makes the next comparisons re-stat this path (an indexed multi-link path's `seen` entry is
        # already the re-stat; a new one's listing may be stale, which costs at most one extra hash).
        files[rel] = [seen[rel][0], seen[rel][1], sha, st.st_nlink]
    for rel in gone:
        del files[rel]
    complete = not unread and not truncated
    was_partial = partial_marker.exists()
    linked = sorted(linked)
    links_changed = linked != idx.get("linked", [])
    if links_changed:
        idx["linked"] = linked
        if linked:
            audit.log(HOOK, "LINKS", "{} linked folder(s) under the script trees are NOT followed (junction or symlink),"
                      " so nothing inside them is snapshotted: {}".format(len(linked), ", ".join(linked[:5])
                                                                            + (", ..." if len(linked) > 5 else "")))
    if truncated:
        audit.log(HOOK, "PARTIAL", "the walk stopped at its time box -- folders it did not reach were not compared "
                  "this call, and nothing in them counts as deleted")
    name = None
    # While a baseline is unfinished, a resume that only adds not-yet-read files writes no manifest (one per call would
    # be hundreds of near-identical maps); the first time-boxed run (it always reads at least one file, so `changed`),
    # the completing run and any real change do.
    if (changed and not was_partial) or altered or gone or idx.get("dirty") or (complete and was_partial):
        snap_files = {r: v[2] for r, v in files.items() if truncated or r in seen}
        ts = audit.stamp()
        try:
            name, idx["head_base"] = _write_manifest(store, ts, now, command, snap_files, not complete,
                                                     idx.get("head_base"))
            idx["dirty"] = False
        except OSError:
            idx["dirty"] = True
            audit.log(HOOK, "FAILED", "could not write the snapshot manifest -- the files are stored, the next call "
                      "retries the manifest")
        if name is not None:
            _log_snapshot(audit, ts, name, changed + gone, len(snap_files), len(unread), failed, command, len(linked),
                          truncated)
    if failed and name is None:
        audit.log(HOOK, "FAILED", "{} script file(s) could not be read -- their current content is in no snapshot "
                  "until a later walk reads them".format(failed))
    if todo or gone or name is not None or idx.get("dirty") or links_changed or relinked:   # unchanged: no rewrite
        try:
            _write_replace(store / INDEX, json.dumps(idx, ensure_ascii=True, sort_keys=True).encode("ascii"))
        except OSError:
            audit.log(HOOK, "FAILED", "could not write {} -- the next call hashes those files again".format(INDEX))
    try:
        if complete:
            if partial_marker.exists():
                partial_marker.unlink()
        else:
            partial_marker.write_bytes(b"")
        rl.write_bytes("{}\n".format(int(now)).encode("ascii"))   # digits + LF, as the bash hook wrote
        os.utime(str(rl), ns=(walk_ns, walk_ns))      # "newer than the last walk" means newer than when it STARTED
    except OSError:
        pass
    if complete and (prune_due or now - idx.get("pruned", 0) >= PRUNE_EVERY):
        # Bounded (review M1): SessionStart prunes inside what its box leaves (at least 0.5 s), a tool call for
        # PRUNE_CALL_BOX. Pruning is restartable, so stopping early only defers it.
        until = max(box_end, _walk_clock() + 0.5) if prune_due else _walk_clock() + PRUNE_CALL_BOX
        prune(store, now, audit, deadline=until)
    return name


def _log_snapshot(audit, ts, name, changed, total, unread, failed, command, linked=0, truncated=False):
    psc = sum(1 for r in changed if r.lower().endswith(".psc"))
    pex = len(changed) - psc
    head = "AUTO-SNAPSHOT PARTIAL" if (unread or truncated) else "AUTO-SNAPSHOT"
    tail = ", {} not yet read -- resumes on the next call".format(unread) if unread else ""
    tail += ", walk cut short by its time box" if truncated else ""
    tail += ", {} could not be read -- retried on the next walk".format(failed) if failed else ""
    tail += ", {} linked folder(s) not followed".format(linked) if linked else ""
    short = (command + "\n").encode("utf-8")[:120].rstrip(b"\n")
    audit.raw(b"[" + ts.encode() + b"] " + head.encode() + b" (psc:" + str(psc).encode() + b" pex:"
              + str(pex).encode() + b" changed, " + str(total).encode() + b" files" + tail.encode() + b") "
              + name.encode() + b" before: " + short + b"\n")


# ---------------------------------------------------------------- pruning


def _stale_part(entry, now):
    """A `*.part-<pid>` this tool left when it was killed mid-write, older than a day (never a live writer's)."""
    try:
        return ".part-" in entry.name and now - entry.stat().st_mtime > DAY
    except OSError:
        return False


def prune(store, now, audit, deadline=None):
    """Retire (retire.py: renamed out of the store, then recycled by housekeep; never deleted) the snapshots and blobs
    nothing still needs. See the module docstring for what is kept. Any unreadable manifest, or an unreadable index,
    stops blob pruning: what it references is unknown. With a `deadline` (a `_walk_clock()` time) it stops between
    removals -- it is restartable, so stopping only defers the rest (review M1). -> blobs and part files removed."""
    store = Path(store)
    if (store / "transactions").exists() or (store / "MANIFEST.jsonl").exists():
        audit.log(HOOK, "PRUNE-SKIPPED", "another store format; no script snapshot or blob was pruned")
        return 0
    batch = retire.Batch(store, store.name)

    def late():
        return deadline is not None and _walk_clock() >= deadline
    names = manifest_names(store)
    loaded = {}
    for n in names:                                   # re-review N1: loading is inside the box too (~5 ms each, cold)
        if late():
            return _pruned(store, now, audit, 0, stopped=True)
        loaded[n] = _load_manifest(store, n)
    unreadable = [n for n, m in loaded.items() if m is None]
    # Newest by the recorded epoch, not the local-time stamp in the name (review M2: a DST fall-back hour reorders
    # names); the name's collision suffix breaks ties within one second.
    good = sorted((n for n in names if loaded[n] is not None), key=lambda n: (loaded[n].get("t", 0), _order(n)))
    keep = set()
    if good:
        keep.add(good[-1])
    keep.update(n for n in good if now - loaded[n].get("t", 0) < KEEP_DAYS * DAY)
    # The newest snapshot holding each path: kept, so a path's latest content always survives with its path.
    claimed, unseen = set(), {}
    for n in reversed(good):
        m = loaded[n]
        if m["base"] is None:
            paths = m["set"].keys()
        else:
            b = loaded.get(m["base"])
            if b is None:
                keep.add(n)                           # its base is gone or unreadable: keep it, never guess
                continue
            u = unseen.setdefault(m["base"], set(b["set"]))
            gone = set(m["gone"])
            paths = set(m["set"]) | {p for p in u if p not in gone}
        new = [p for p in paths if p not in claimed]
        if new:
            keep.add(n)
            claimed.update(new)
        if m["base"] is not None:                     # shrink once claimed: the next delta on this base is cheap
            unseen[m["base"]] = {p for p in unseen[m["base"]] if p not in claimed}
    for n in list(keep):
        m = loaded.get(n)
        if m is not None and m["base"] is not None:
            keep.add(m["base"])
    for n in good:
        if n not in keep:
            if late():
                return _pruned(store, now, audit, 0, stopped=True)
            batch.retire(store / "manifests" / n)
    removed = 0
    idx = _read_json(store / INDEX)
    if unreadable:
        audit.log(HOOK, "PRUNE-SKIPPED", "{} unreadable snapshot manifest(s) -- no blob was pruned".format(
            len(unreadable)))
    elif not (isinstance(idx, dict) and isinstance(idx.get("files"), dict)):
        audit.log(HOOK, "PRUNE-SKIPPED", "the index is unreadable -- no blob was pruned")
    else:
        live = set()
        for n in keep:
            m = loaded.get(n)
            if m is not None:
                live.update(m["set"].values())
        live.update(v[2] for v in idx["files"].values())
        try:
            entries = list(os.scandir(str(store / "blobs")))
        except OSError:
            entries = []
        for e in entries:
            if (_BLOB.match(e.name) and e.name not in live) or _stale_part(e, now):
                if late():
                    return _pruned(store, now, audit, removed, stopped=True)
                removed += batch.retire(e.path)
    # Part files a killed writer left beside the manifests and the index (review M3).
    for d in (store / "manifests", store):
        try:
            entries = list(os.scandir(str(d)))
        except OSError:
            continue
        for e in entries:
            if e.is_file() and _stale_part(e, now):
                removed += batch.retire(e.path)
    # The bash-era copy folders (`<stamp>/source`, `<stamp>/deploy`) age out as they always did: older than 14 days.
    try:
        for d in store.iterdir():
            if d.is_dir() and _LEGACY_DIR.match(d.name) and int((now - d.stat().st_mtime) // DAY) > KEEP_DAYS:
                if late():
                    return _pruned(store, now, audit, removed, stopped=True)
                batch.retire(d)
    except OSError:
        pass
    return _pruned(store, now, audit, removed)


def _pruned(store, now, audit, removed, stopped=False):
    """Record the prune (once per PRUNE_EVERY, finished or not: a tool call must not retry it every call; the next
    SessionStart always prunes again)."""
    if stopped:
        audit.log(HOOK, "PRUNE-PARTIAL", "pruning stopped at its time box -- the rest is pruned next time")
    idx = _read_json(store / INDEX)
    if isinstance(idx, dict) and isinstance(idx.get("files"), dict):
        idx["pruned"] = int(now)
        try:
            _write_replace(store / INDEX, json.dumps(idx, ensure_ascii=True, sort_keys=True).encode("ascii"))
        except OSError:
            pass
    return removed
