"""Snapshot every .psc/.pex before a non-read-only shell command, so a broken experiment can be rolled back.

Plan 6 Task 14 (Plan 5 R16). The bash-era hook copied only TOP-LEVEL `Data/Scripts/Source/*.psc` and `Data/Scripts/*.pex`
younger than 7 days, never looked at the SSE layout `Data/Source/Scripts/`, and copied the same bytes again on every
snapshot (MEASURED on the real install, Plan 5 Task 16: 3,428 of 11,898 script files never seen; 2.82 GB of snapshots
holding 89 distinct contents). Now:

* WHAT: every `.psc` / `.pex` (any case) under `Data/Scripts/**` (which holds `Data/Scripts/Source/**`) and
  `Data/Source/Scripts/**`, whatever its age. One `os.scandir` walk; on Windows a DirEntry's size and mtime come with
  the listing, so the walk makes no per-file stat call.
* STORE: `auto_snapshots/blobs/<sha256>`, content-addressed, so unchanged bytes are never stored twice. A blob is
  written by backup.py's helpers (part file, sha256 in the same pass, fsync, size check) and only then linked to its
  final name.
* INDEX: `auto_snapshots/index.json` keeps `(size, mtime_ns) -> sha256` per path; only a path whose entry changed is
  read and hashed again.
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
  Stated gap: inside the window, a file replaced with an OLDER mtime (an mtime-preserving copy or extract) is not seen
  until the window ends.
* PRUNING keeps every blob referenced by the newest snapshot, by any snapshot younger than 14 days, or by the index
  (the live files' content); and it keeps, whatever its age, the newest snapshot that holds each path's latest content,
  so the only copy of a deleted script's last version is never pruned. Pruning DELETES PERMANENTLY: it is this tool
  removing its own managed store, not user files, so it does not go to the Recycle Bin.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

from . import backup
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
RATE = 60                 # latency gate branch <= 150 ms (measured, see the Task 14 report): the 60 s rate limit
KEEP_DAYS = 14
BASELINE_BOX = 20.0       # seconds at SessionStart
# Seconds per tool call while a baseline is unfinished: inside the engine's measured ~0.25-0.35 s of p95 headroom under
# the 1 s per-wrapper gate (Plan 4 Risk 2), so finishing a baseline never makes a call miss that gate.
CALL_BOX = 0.25
PRUNE_EVERY = DAY         # per-call pruning at most this often; SessionStart always prunes
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
    """{rel: (size, mtime_ns)} for every .psc/.pex under TREES, recursively. rel uses `/`, from the project root."""
    out = {}
    for tree in TREES:
        stack = [(os.path.join(str(project), *tree.split("/")), tree + "/")]
        while stack:
            d, rel = stack.pop()
            try:
                it = os.scandir(d)
            except OSError:
                continue
            with it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append((e.path, rel + e.name + "/"))
                        elif e.name[-4:].lower() in EXTS and e.is_file():
                            st = e.stat()
                            out[rel + e.name] = (st.st_size, st.st_mtime_ns)
                    except OSError:
                        continue
    return out


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
    could not be read or changed size while it was copied (the index keeps its old entry, so the next walk retries)."""
    part = blobs / ".part-{}".format(os.getpid())
    try:
        want, copied, digest = backup._copy_hashed(src, part)
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
        pass                                          # these bytes are already stored
    except OSError:
        if not dest.is_file():                        # no hard links on this volume
            try:
                os.replace(str(part), str(dest))
            except OSError:
                backup._discard(part)
                return None
    backup._discard(part)
    return digest


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


def materialise(project, name, dest):
    """Write snapshot `name`'s files under `dest`, at their recorded relative paths. -> number of files written.
    Raises ValueError for an unreadable snapshot or a missing blob (nothing partial is left unreported)."""
    store = store_dir(project)
    files = resolve(store, name)
    n = 0
    for rel, sha in sorted(files.items()):
        blob = store / "blobs" / sha
        if not blob.is_file():
            raise ValueError("snapshot {}: the blob for {} ({}) is missing".format(name, rel, sha))
        target = Path(dest).joinpath(*rel.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(blob), str(target))
        n += 1
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


def run(project, command, payload_empty, audit, now=None):
    if payload_empty:
        audit.raw("[{}] snapshot-before-tool BLIND: EMPTY payload -- no snapshot taken\n".format(audit.stamp()))
        return
    if any(grep(p, command, icase=False) for p in SKIP) or grep(SKIP_POWERSHELL, command, icase=True):
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
                prune_due=True)


def take(project, audit, command, now=None, box=CALL_BOX, rate=RATE, prune_due=False):
    """Walk, store what changed, write a manifest when anything did. -> the manifest name written, or None."""
    t0 = time.monotonic()
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
    lock = backup._exclusive(store / ".lock")
    try:
        lock.__enter__()
    except OSError:
        audit.log(HOOK, "SKIPPED", "another snapshot holds the store lock -- this call took none")
        return None
    try:
        return _take_locked(root, store, audit, command, now, t0 + box, rate, prune_due)
    finally:
        lock.__exit__(None, None, None)


def _take_locked(root, store, audit, command, now, deadline, rate, prune_due):
    walk_ns = time.time_ns()
    seen = walk(root)
    rl = store / ".last_snapshot"
    partial_marker = store / PARTIAL
    if rate and rl.is_file() and not partial_marker.exists():
        try:
            last = int(rl.read_bytes().decode("utf-8", "replace").strip() or "0")
            rl_ns = rl.stat().st_mtime_ns
        except (OSError, ValueError):
            last, rl_ns = 0, 0
        if int(now) - last < rate and not any(m > rl_ns for _s, m in seen.values()):
            return None
    idx = _read_json(store / INDEX)
    if not (isinstance(idx, dict) and idx.get("v") == 1 and isinstance(idx.get("files"), dict)):
        idx = {"v": 1, "files": {}, "head_base": None, "dirty": False, "pruned": 0}
    files = idx["files"]
    todo = sorted(r for r, st in seen.items() if (files.get(r) or [None, None])[:2] != list(st))
    gone = [r for r in files if r not in seen]
    blobs = store / "blobs"
    changed, unread, failed, altered = [], [], 0, 0
    for i, rel in enumerate(todo):
        if i and time.monotonic() >= deadline:        # at least one file per call: a slow walk cannot stall a baseline
            unread = todo[i:]
            break
        sha = _store_blob(blobs, str(root.joinpath(*rel.split("/"))))
        if sha is None:
            failed += 1                               # unreadable or changing: its old entry stays, retried next walk
            continue
        before = files.get(rel)
        if before is None or before[2] != sha:
            changed.append(rel)
            altered += before is not None
        files[rel] = [seen[rel][0], seen[rel][1], sha]
    for rel in gone:
        del files[rel]
    complete = not unread
    was_partial = partial_marker.exists()
    name = None
    # While a baseline is unfinished, a resume that only adds not-yet-read files writes no manifest (one per call would
    # be hundreds of near-identical maps); the first time-boxed run (it always reads at least one file, so `changed`),
    # the completing run and any real change do.
    if (changed and not was_partial) or altered or gone or idx.get("dirty") or (complete and was_partial):
        snap_files = {r: v[2] for r, v in files.items() if r in seen}
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
            _log_snapshot(audit, ts, name, changed + gone, len(snap_files), len(unread), failed, command)
    if failed and name is None:
        audit.log(HOOK, "FAILED", "{} script file(s) could not be read -- their current content is in no snapshot "
                  "until a later walk reads them".format(failed))
    if todo or gone or name is not None or idx.get("dirty"):   # an unchanged walk rewrites nothing (~1.7 MB measured)
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
        prune(store, now, audit)
    return name


def _log_snapshot(audit, ts, name, changed, total, unread, failed, command):
    psc = sum(1 for r in changed if r.lower().endswith(".psc"))
    pex = len(changed) - psc
    head = "AUTO-SNAPSHOT PARTIAL" if unread else "AUTO-SNAPSHOT"
    tail = ", {} not yet read -- resumes on the next call".format(unread) if unread else ""
    tail += ", {} could not be read -- retried on the next walk".format(failed) if failed else ""
    short = (command + "\n").encode("utf-8")[:120].rstrip(b"\n")
    audit.raw(b"[" + ts.encode() + b"] " + head.encode() + b" (psc:" + str(psc).encode() + b" pex:"
              + str(pex).encode() + b" changed, " + str(total).encode() + b" files" + tail.encode() + b") "
              + name.encode() + b" before: " + short + b"\n")


# ---------------------------------------------------------------- pruning


def prune(store, now, audit):
    """Delete (permanently: this is the tool's own store) the snapshots and blobs nothing still needs. See the module
    docstring for what is kept. Any unreadable manifest stops blob pruning: what it references is unknown."""
    store = Path(store)
    names = manifest_names(store)
    loaded = {n: _load_manifest(store, n) for n in names}
    unreadable = [n for n, m in loaded.items() if m is None]
    good = [n for n in names if loaded[n] is not None]
    keep = set(unreadable)
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
            try:
                os.unlink(str(store / "manifests" / n))
            except OSError:
                pass
    removed = 0
    if not unreadable:
        live = set()
        for n in keep:
            m = loaded.get(n)
            if m is not None:
                live.update(m["set"].values())
        idx = _read_json(store / INDEX)
        if not (isinstance(idx, dict) and isinstance(idx.get("files"), dict)):
            return 0                                  # the live files' content is unknown: delete no blob
        live.update(v[2] for v in idx["files"].values())
        try:
            entries = list(os.scandir(str(store / "blobs")))
        except OSError:
            entries = []
        for e in entries:
            stale_part = e.name.startswith(".part-") and now - e.stat().st_mtime > DAY
            if (_BLOB.match(e.name) and e.name not in live) or stale_part:
                try:
                    os.unlink(e.path)
                    removed += 1
                except OSError:
                    pass
    else:
        audit.log(HOOK, "PRUNE-SKIPPED", "{} unreadable snapshot manifest(s) -- no blob was pruned".format(
            len(unreadable)))
    idx = _read_json(store / INDEX)
    if isinstance(idx, dict) and isinstance(idx.get("files"), dict):
        idx["pruned"] = int(now)                      # once per PRUNE_EVERY, whatever it found
        try:
            _write_replace(store / INDEX, json.dumps(idx, ensure_ascii=True, sort_keys=True).encode("ascii"))
        except OSError:
            pass
    # The bash-era copy folders (`<stamp>/source`, `<stamp>/deploy`) age out as they always did: older than 14 days.
    try:
        for d in store.iterdir():
            if d.is_dir() and _LEGACY_DIR.match(d.name) and int((now - d.stat().st_mtime) // DAY) > KEEP_DAYS:
                shutil.rmtree(str(d), ignore_errors=True)
    except OSError:
        pass
    return removed
