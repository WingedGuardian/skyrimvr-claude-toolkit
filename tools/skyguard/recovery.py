"""Recovery snapshots: the load order and the INIs before a command, and every save a command changes (spec 5.3; the
save policy, user ruling 2026-10-13).

The script snapshot (snapshot.py) covers .psc/.pex. This covers the rest of what a bad command can break and the lock
does not protect:

* LOAD ORDER AND INIs, before every non-read-only shell command (snapshot.read_only: both stores agree on what is
  read-only): `plugins.txt` and `loadorder.txt` in each declared SKYRIM_LOADORDER_DIR, and every `Skyrim*.ini` (any case)
  directly in each declared SKYRIM_CONFIG_DIR. The CONFIGURED roots only (skyrim-paths.env and the environment, through
  paths.roots): nothing is probed, so an install that declares neither gets no such snapshot and the audit log says so
  (once per change of that state, not once per call). Taken by run() (snapshot-before-tool; Codex pre-tool's side
  effects); never blocks.
* EVERY SAVE A COMMAND CHANGES (backup_saves), called by the DECIDING hook (saves.enforce) before it allows the
  command, because the save policy allows a save change only once its backup is verified: both files of each pair
  (the save and its SKSE co-save, whichever one the command names), each copy fsynced and read back to its SHA-256,
  one snapshot per pair; a missing co-save is recorded in the line's `missing`. Any failure raises SaveBackupFailed
  and the hook DENIES. No rate limit, no size bound (the hook's time is the bound), and never pruned.

STORE `.claude/backups/recovery_snapshots/`:
* `blobs/<sha256>`: content-addressed (snapshot._store_blob, backup.py's part file + running sha256 + size check), so
  identical bytes are stored once however many snapshots hold them.
* `MANIFEST.jsonl`: Task 13's line -- `v, ts, src` (verbatim) `, dest` (the blob's name) `, size, sha256, tool, agent,
  session_id` -- plus `t` (epoch), `kind` (config | save), `snap` (the snapshot id), `command` (its first 120 bytes)
  and, on a save line, `missing` (the pair's files that did not exist). One locked append (backup._append_line) per
  snapshot (per call, for saves). A reader skips and COUNTS a line that does not parse.
* `index.json`: per config path, the size / mtime_ns / sha256 / snapshot id last recorded, so a path is re-read only when
  needed; and the last snapshot id of each kind.

DEDUP AND RATE LIMIT (config only). A file whose listing (size, mtime) matches the index is not read again; a line is
written only when a path's content differs from what was last recorded for it, so a snapshot id holds what CHANGED and
`resolve` gives the full state at that id. Config files are re-read regardless once per CONFIG_RATE seconds (a
same-size, same-mtime rewrite is seen then).

ORDER. The hooks call this in PreToolUse (Claude: snapshot-before-tool, and protect-bash / protect-files /
protect-mcp for saves; Codex: pre-tool), so the copy is finished before the command can start.

PRUNING (once a day, inside PRUNE_BOX): a config snapshot is kept WHOLE while it is younger than KEEP_DAYS or holds
some path's newest content, and each kept config snapshot keeps the earlier lines its full state needs (fix round 1,
I4); a SAVE snapshot is always kept (the save policy: backups are kept forever; disk space is the user's call). Every
other line goes, then every blob no remaining line names is deleted -- PERMANENTLY, as this is the tool's own store.
An unreadable line is kept as it is and stops blob deletion. Every line records its snapshot's line count and full
path set, so resolve() REFUSES a snapshot pruning has cut into instead of returning a state that never existed. A save
copy also keeps SAVE_RESERVE_BYTES free on its drive (review M2): short of that it fails, and the hook denies.

Restoring: `tools/skyguard.sh snapshot list | restore` (snapcli.py); a save snapshot restores both files of its pair.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

from modding_harness import durable

from . import backup, snapshot

HOOK = "recovery-snapshot"
STORE = "recovery_snapshots"
MANIFEST = "MANIFEST.jsonl"
INDEX = "index.json"
DAY = 86400
CONFIG_RATE = 60          # seconds: config files are re-read at most this often when their listing is unchanged
LOADORDER_FILES = ("plugins.txt", "loadorder.txt")
_INI = re.compile(r"^skyrim[^/\\]*\.ini$", re.IGNORECASE)
# Review M2: a save copy never takes its drive below this much free space (SKIPPED, logged, instead).
SAVE_RESERVE_BYTES = 2 * 1024 * 1024 * 1024
LOCK_WAIT = 0.2           # a config snapshot waits this long for the store lock
SAVE_LOCK_WAIT = 5.0      # a save backup waits up to this long (never past the hook's deadline): it decides the call
KEEP_DAYS = 14
PRUNE_EVERY = DAY
PRUNE_BOX = 0.5
_BLOB = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^\d{8}_\d{6}-(config|save)(?:-(?:\d+|[0-9a-f]{32}))?\Z")
_MB = 1024 * 1024


def store_dir(project):
    return Path(project) / ".claude" / "backups" / STORE


def _short(command):
    return (command or "").encode("utf-8", "replace")[:120].decode("utf-8", "ignore").split("\n", 1)[0]


# ---------------------------------------------------------------- what is snapshotted


def config_files(roots):
    """The load-order files and game INIs under the configured roots that exist now, in a stable order."""
    out = []
    for d in roots.loadorder_dirs:
        for n in LOADORDER_FILES:
            p = d.rstrip("/") + "/" + n
            if os.path.isfile(p) and p not in out:
                out.append(p)
    for d in roots.config_dirs:
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for n in names:
            p = d.rstrip("/") + "/" + n
            if _INI.match(n) and os.path.isfile(p) and p not in out:
                out.append(p)
    return out


def _listed(folder, name):
    """`folder/<entry>` whose entry equals `name` case-insensitively (the rule's own comparison), else None."""
    try:
        for n in os.listdir(folder):
            if n.casefold() == name.casefold():
                p = os.path.join(folder, n)
                return p if os.path.isfile(p) else None
    except OSError:
        pass
    return None


# ---------------------------------------------------------------- entry points (the hooks)


def run(project, command, payload_empty, audit, roots, cwd="", shell="", agent="", session_id="", deadline=None,
        now=None, env=None):
    """Shell route (snapshot-before-tool, Codex pre-tool side effects): the load order and INIs before a command that
    is not read-only. Never raises into the hook. A SAVE a command changes is backed up by the deciding hook instead
    (saves.enforce -> backup_saves), before it allows the command: the save policy needs the backup's result."""
    if payload_empty or not (command or "").strip():
        return
    take(project, audit, roots, command, not snapshot.read_only(command), now, deadline, agent, session_id)


# ---------------------------------------------------------------- taking a snapshot


def _read_index(store):
    idx = snapshot._read_json(store / INDEX)
    if not (isinstance(idx, dict) and idx.get("v") == 1 and isinstance(idx.get("files"), dict)):
        idx = {"v": 1, "files": {}}
    idx.setdefault("last", {})
    return idx


def _new_id(idx, ts, kind):
    """`<stamp>-<kind>`, with `-2`, `-3` ... when the previous snapshot of that kind took the same second."""
    base = "{}-{}".format(ts, kind)
    if kind == "save":
        sid = base + "-" + uuid.uuid4().hex
        idx["last"][kind] = sid
        return sid
    last = idx["last"].get(kind, "")
    n = 1
    if last == base:
        n = 2
    elif last.startswith(base + "-") and last[len(base) + 1:].isdigit():
        n = int(last[len(base) + 1:]) + 1
    sid = base if n == 1 else "{}-{}".format(base, n)
    idx["last"][kind] = sid
    return sid


def take(project, audit, roots, command, do_config, now=None, deadline=None, agent="", session_id=""):
    """The load order and INIs (config snapshot). Never raises; a lost lock or an unwritable store is logged."""
    now = time.time() if now is None else now
    if not do_config:
        return
    store = store_dir(project)
    try:
        (store / "blobs").mkdir(parents=True, exist_ok=True)
    except OSError:
        audit.log(HOOK, "FAILED", "could not create {} -- NO recovery snapshot was taken".format(store))
        return
    lock = backup._exclusive(store / ".lock", wait=LOCK_WAIT)
    try:
        lock.__enter__()
    except OSError:
        audit.log(HOOK, "SKIPPED", "another session holds the recovery store lock -- this call took no recovery "
                  "snapshot")
        return
    try:
        _take_locked(store, audit, roots, command, now, agent, session_id)
    finally:
        lock.__exit__(None, None, None)


def _take_locked(store, audit, roots, command, now, agent, session_id):
    idx = _read_index(store)
    before = json.dumps(idx, sort_keys=True)
    got = _config(store, idx, roots, audit, command, now, agent, session_id)
    if got:
        lines, updates, logline = got
        data = b"".join((json.dumps(e, ensure_ascii=True) + "\n").encode("ascii") for e in lines)
        try:
            backup._append_line(store / MANIFEST, data)
        except OSError:
            audit.log(HOOK, "FAILED", "could not append to {} -- {} is NOT recorded; the next call retries".format(
                MANIFEST, lines[0]["snap"]))
        else:
            idx["files"].update(updates)          # recorded only once on disk, so a failed append is retried
            audit.raw(logline)
    if now - idx.get("pruned", 0) >= PRUNE_EVERY:
        prune(store, now, audit, deadline=time.monotonic() + PRUNE_BOX)
        idx["pruned"] = int(now)
    if json.dumps(idx, sort_keys=True) != before:
        try:
            snapshot._write_replace(store / INDEX, json.dumps(idx, ensure_ascii=True, sort_keys=True).encode("ascii"))
        except OSError:
            audit.log(HOOK, "FAILED", "could not write {} -- the next call reads those files again".format(INDEX))


def _line(ts, now, src, sha, size, kind, sid, command, agent, session_id, lines, state):
    """Fix round 1 (I4): every line also records how many lines its snapshot has and the full set of paths its state
    holds, so resolve() can tell a whole snapshot from one pruning has cut into, and refuse the latter."""
    return {"v": 1, "ts": ts, "t": int(now), "src": src, "dest": sha, "size": size, "sha256": sha, "tool": HOOK,
            "agent": agent, "session_id": session_id, "kind": kind, "snap": sid, "command": _short(command),
            "lines": lines, "state": state}


def _note(idx, audit, level, msg):
    """Log a standing condition once, when it starts (not on every call)."""
    if idx.get("note") != msg:
        idx["note"] = msg
        if msg:
            audit.log(HOOK, level, msg)


def _config(store, idx, roots, audit, command, now, agent, session_id):
    if roots is None:
        _note(idx, audit, "FAILED", "the paths file could not be read, so the load order and INIs are NOT being "
              "snapshotted until it can")
        return None
    if not roots.loadorder_dirs and not roots.config_dirs:
        _note(idx, audit, "NOT CONFIGURED", "no SKYRIM_LOADORDER_DIR or SKYRIM_CONFIG_DIR is declared (setup.sh writes "
              "them), so the load order and INIs are NOT snapshotted")
        return None
    _note(idx, audit, "", "")
    full = not (0 <= now - idx.get("checked", 0) < CONFIG_RATE)
    files = config_files(roots)
    blobs = store / "blobs"
    changed, updates, failed = [], {}, []
    for src in files:
        try:
            st = os.stat(src)
        except OSError:
            continue
        rec = idx["files"].get(src)
        if not full and isinstance(rec, list) and rec[:2] == [st.st_size, st.st_mtime_ns]:
            continue
        sha = snapshot._store_blob(blobs, src)
        if sha is None:
            failed.append(src)
            continue
        if isinstance(rec, list) and len(rec) > 3 and rec[2] == sha:
            idx["files"][src] = [st.st_size, st.st_mtime_ns, sha, rec[3], rec[4] if len(rec) > 4 else 0]
            continue
        changed.append((src, sha, os.stat(str(blobs / sha)).st_size))
        updates[src] = [st.st_size, st.st_mtime_ns, sha, None, int(now)]
    if full:
        idx["checked"] = int(now)
    if failed:
        audit.log(HOOK, "FAILED", "{} could not be read -- its current content is in no recovery snapshot".format(
            ", ".join(os.path.basename(f) for f in failed)))
    if not changed:
        return None
    ts = audit.stamp()
    sid = _new_id(idx, ts, "config")
    for u in updates.values():
        u[3] = sid
    state = sorted({e["src"] for e in read_manifest(store)[0] if e["kind"] == "config"} | {s for s, _h, _z in changed})
    lines = [_line(ts, now, src, sha, size, "config", sid, command, agent, session_id, len(changed), state)
             for src, sha, size in changed]
    names = ", ".join(os.path.basename(s) for s, _h, _z in changed)
    logline = "[{}] RECOVERY-SNAPSHOT (config: {} of {} file(s) changed: {}) {} before: {}\n".format(
        ts, len(changed), len(files), names, sid, _short(command))
    return lines, updates, logline


# ---------------------------------------------------------------- the save policy's backup (saves.enforce)


class SaveBackupFailed(Exception):
    """A save pair could not be backed up and verified; the message says why (the deny names it)."""


def pair_members(ess, skse):
    """([existing file], [missing path]) of one save pair, each spelled as the folder lists it (case-insensitive)."""
    have, missing = [], []
    for p in (ess, skse):
        if not p:
            continue
        hit = p if os.path.isfile(p) else _listed(*os.path.split(p))
        (have if hit else missing).append(hit or p)
    return have, missing


def _store_verified(blobs, src, deadline):
    """Copy `src` into the store under its sha256, fsynced, and READ IT BACK: the stored bytes must hash to what was
    read from the source. An existing blob of that hash is re-hashed too (blobs of the other snapshots are not
    fsynced, and one torn by a power loss is replaced). -> sha256. Raises SaveBackupFailed with the reason."""
    part = blobs / ".part-save-{}".format(os.getpid())
    name = os.path.basename(src)
    try:
        want, copied, digest = backup._copy_hashed(src, part, fsync=True, deadline=deadline)
        on_disk = os.stat(str(part)).st_size
    except backup.CopyTimeout:
        backup._discard(part)
        raise SaveBackupFailed("the copy of {} did not finish inside the hook's time".format(name))
    except OSError as e:
        backup._discard(part)
        raise SaveBackupFailed("{} could not be read or copied ({})".format(name, e.__class__.__name__))
    if not (on_disk == copied == want):
        backup._discard(part)
        raise SaveBackupFailed("{} changed size while it was copied".format(name))
    try:
        if backup._sha256(part) != digest:
            raise SaveBackupFailed("the copy of {} does not hash to what was read".format(name))
        dest = blobs / digest
        if dest.is_file() and backup._sha256(dest) == digest:
            return digest
        os.replace(str(part), str(dest))
        if backup._sha256(dest) != digest:
            raise SaveBackupFailed("the stored copy of {} does not verify".format(name))
        return digest
    except OSError as e:
        raise SaveBackupFailed("the copy of {} could not be stored ({})".format(name, e.__class__.__name__))
    finally:
        backup._discard(part)


def backup_saves(project, audit, pairs, command, agent="", session_id="", deadline=None, now=None):
    """Back up each save pair BEFORE a command changes it (the save policy, user ruling 2026-10-13): both files of
    each pair, `.ess` and `.skse`, copied into the recovery store and each copy verified by SHA-256; then one
    manifest snapshot per pair (`<stamp>-save`), holding the files that exist and naming a missing co-save in
    `missing`. Kept FOREVER: pruning never removes a save snapshot. `pairs`: [(ess path, skse path)], either may be
    absent on disk (a pair with neither file is skipped: nothing exists to keep). -> [snapshot id], in pair order.
    Raises SaveBackupFailed (and records nothing) when any file cannot be copied and verified, the store cannot be
    written, its lock is held past the deadline, or the copy would leave the drive under SAVE_RESERVE_BYTES."""
    now = time.time() if now is None else now
    plan = []
    for ess, skse in pairs:
        have, missing = pair_members(ess, skse)
        if have:
            plan.append((have, missing))
    if not plan:
        return []
    store = store_dir(project)
    try:
        (store / "blobs").mkdir(parents=True, exist_ok=True)
        total = sum(os.path.getsize(f) for have, _m in plan for f in have)
        free = shutil.disk_usage(str(store)).free
    except OSError as e:
        raise SaveBackupFailed("the backup store {} is not usable ({})".format(store, e.__class__.__name__))
    if free - total < SAVE_RESERVE_BYTES:
        raise SaveBackupFailed("{:.1f} MB of saves would leave under {:.0f} MB free on the backup drive".format(
            total / _MB, SAVE_RESERVE_BYTES / _MB))
    left = SAVE_LOCK_WAIT if deadline is None else max(0.0, min(SAVE_LOCK_WAIT, deadline - backup._clock()))
    try:
        with backup._exclusive(store / ".lock", wait=left):
            return _backup_locked(store, audit, plan, command, agent, session_id, deadline, now)
    except SaveBackupFailed:
        raise
    except OSError:
        raise SaveBackupFailed("another session held the backup store's lock")


def _backup_locked(store, audit, plan, command, agent, session_id, deadline, now):
    blobs = store / "blobs"
    # Capture identities before copying any member, then revalidate the whole set
    # after every copy. Size/mtime alone cannot detect metadata-preserving writes.
    before = {f: durable.identity(f) for have, _missing in plan for f in have}
    stored = []
    for have, missing in plan:
        stored.append(([(f, _store_verified(blobs, f, deadline)) for f in have], missing))
    try:
        for got, missing in stored:
            for f, sha in got:
                if (durable.identity(f) != before[f] or durable.digest(f, deadline) != sha
                        or durable.identity(f) != before[f]):
                    raise SaveBackupFailed("{} changed before the complete save was preserved".format(f))
            for f in missing:
                if os.path.lexists(f) or _listed(*os.path.split(f)):
                    raise SaveBackupFailed("the missing save counterpart {} appeared during preservation".format(f))
    except OSError as exc:
        raise SaveBackupFailed("the complete save could not be revalidated ({})".format(exc)) from exc
    idx = _read_index(store)
    ts = audit.stamp()
    lines, ids = [], []
    if shutil.disk_usage(str(store)).free < SAVE_RESERVE_BYTES:
        raise SaveBackupFailed("the backup drive lost its free-space reserve during preservation")
    for got, missing in stored:
        sid = _new_id(idx, ts, "save")
        ids.append(sid)
        state = sorted(f for f, _s in got)
        for f, sha in got:
            e = _line(ts, now, f, sha, os.stat(str(blobs / sha)).st_size, "save", sid, command, agent, session_id,
                      len(got), state)
            e["missing"] = sorted(missing)
            lines.append(e)
    data = b"".join((json.dumps(e, ensure_ascii=True) + "\n").encode("ascii") for e in lines)
    try:
        backup._append_line(store / MANIFEST, data)
        # Windows _commit (os.fsync) requires a write-capable descriptor.
        with open(store / MANIFEST, "r+b") as manifest:
            os.fsync(manifest.fileno())
        durable.sync_dir(store)
        for sid in ids:
            if not resolve(store, sid):
                raise ValueError("empty save snapshot")
    except (OSError, ValueError):
        raise SaveBackupFailed("the backup manifest could not be written")
    try:
        snapshot._write_replace(store / INDEX, json.dumps(idx, ensure_ascii=True, sort_keys=True).encode("ascii"))
    except OSError:
        pass                                  # the index only spaces snapshot ids; the manifest holds the backup
    for (got, missing), sid in zip(stored, ids):
        audit.raw("[{}] SAVE-BACKUP {} ({}{}) before: {}\n".format(
            ts, sid, " + ".join(os.path.basename(f) for f, _s in got),
            ", co-save missing" if missing else "", _short(command)))
    return ids


# ---------------------------------------------------------------- reading


def _valid(e):
    return (isinstance(e, dict) and e.get("v") == 1
            and all(isinstance(e.get(k), str) for k in ("ts", "src", "dest", "sha256", "kind", "snap"))
            and e.get("kind") in ("config", "save") and _ID.match(e["snap"]) is not None
            and isinstance(e.get("size"), int) and not isinstance(e.get("size"), bool)
            and e["dest"] == e["sha256"] and _BLOB.match(e["sha256"]) is not None
            and isinstance(e.get("lines"), int) and not isinstance(e.get("lines"), bool) and e["lines"] > 0
            and isinstance(e.get("t"), int) and not isinstance(e.get("t"), bool)
            and isinstance(e.get("state"), list) and all(isinstance(s, str) for s in e["state"]))


def _raw_lines(store):
    try:
        data = (Path(store) / MANIFEST).read_bytes()
    except OSError:
        return []
    return [ln for ln in data.split(b"\n") if ln.strip()]


def _parse(raw):
    try:
        e = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None
    return e if _valid(e) else None


def read_manifest(store):
    """([entry], unreadable line count), in file order. A line that does not parse or has not the v1 shape is skipped
    and COUNTED, never silently dropped."""
    out, bad = [], 0
    for raw in _raw_lines(store):
        e = _parse(raw)
        if e is None:
            bad += 1
        else:
            out.append(e)
    return out, bad


def snapshots(store):
    """[{id, kind, ts, t, command, files: [entry]}], oldest first."""
    groups = {}
    for e in read_manifest(store)[0]:
        g = groups.setdefault(e["snap"], {"id": e["snap"], "kind": e["kind"], "ts": e["ts"], "t": e.get("t", 0),
                                          "command": e.get("command", ""), "files": [], "lines": e["lines"]})
        g["files"].append(e)
    for g in groups.values():
        g["complete"] = len(g["files"]) == g["lines"]
    return list(groups.values())


def resolve(store, sid):
    """{src: entry} -- the full state a snapshot stands for. A config snapshot holds only what changed, so its state is
    every config path's newest line up to and including that snapshot's; a save snapshot is its own files. Raises
    ValueError for an unknown id -- and (fix round 1, I4) for a snapshot that is no longer whole: one of its own lines
    is gone, or a path its state held has no line left at that point. A partial state is refused, never returned."""
    entries = read_manifest(store)[0]
    mine = [i for i, e in enumerate(entries) if e["snap"] == sid]
    if not mine:
        raise ValueError("no recovery snapshot {}".format(sid))
    first = entries[mine[0]]
    if len(mine) != first["lines"]:
        raise ValueError("recovery snapshot {} is incomplete: {} of its {} file(s) remain after pruning, so it cannot "
                         "be restored as a whole".format(sid, len(mine), first["lines"]))
    if first["kind"] == "save":
        state = {entries[i]["src"]: entries[i] for i in mine}
    else:
        state = {}
        for e in entries[:mine[-1] + 1]:
            if e["kind"] == "config":
                state[e["src"]] = e
    missing = sorted(set(first["state"]) - set(state))
    if missing:
        raise ValueError("recovery snapshot {} is incomplete: no stored copy of {} is left for that point, so the state "
                         "it recorded cannot be rebuilt".format(sid, ", ".join(missing)))
    return {s: state[s] for s in first["state"]} if first["kind"] == "config" else state


# ---------------------------------------------------------------- pruning


def _needed(parsed, now):
    """The indices of the lines pruning keeps (fix round 1, I4). A snapshot is KEPT -- whole -- when it is younger than
    KEEP_DAYS (a save: always) or holds the newest line of some path (the only copy of that content). For every
    kept config snapshot, each path's newest line at or before it is kept too, so its full state still resolves. A line
    kept only for that leaves its own (old) snapshot partial, which resolve() then refuses rather than misreports."""
    newest, last = {}, {}
    for i, e in enumerate(parsed):
        if e is not None:
            newest[e["src"]] = i
            last[e["snap"]] = i
    kept = {parsed[i]["snap"] for i in newest.values()}
    for sid, i in last.items():
        if parsed[i]["kind"] == "save" or now - parsed[i]["t"] < KEEP_DAYS * DAY:
            kept.add(sid)                     # a save snapshot is kept forever (the save policy)
    need, latest = set(), {}
    for i, e in enumerate(parsed):
        if e is None:
            continue
        if e["snap"] in kept:
            need.add(i)
        if e["kind"] == "config":
            latest[e["src"]] = i
            if e["snap"] in kept and last[e["snap"]] == i:
                need.update(latest.values())
    return need


def prune(store, now, audit, deadline=None):
    """See the module docstring. Rewrites MANIFEST.jsonl under its append lock (part file, fsync, replace). -> blobs
    removed."""
    store = Path(store)
    mpath = store / MANIFEST

    def late():
        return deadline is not None and time.monotonic() >= deadline
    try:
        with backup._exclusive(Path(str(mpath) + ".lock"), wait=LOCK_WAIT):
            raws = _raw_lines(store)
            parsed = [_parse(r) for r in raws]
            need = _needed(parsed, now)
            keep = [(r, e) for i, (r, e) in enumerate(zip(raws, parsed)) if e is None or i in need]
            if len(keep) != len(raws):
                if late():
                    audit.log(HOOK, "PRUNE-PARTIAL", "pruning stopped at its time box -- the rest is pruned next time")
                    return 0
                snapshot._write_replace(mpath, b"".join(r + b"\n" for r, _e in keep))
    except OSError:
        audit.log(HOOK, "PRUNE-SKIPPED", "could not rewrite {} (busy or unwritable) -- nothing was pruned".format(MANIFEST))
        return 0
    if any(e is None for _r, e in keep):
        audit.log(HOOK, "PRUNE-SKIPPED", "{} unreadable line(s) in {} -- no blob was pruned".format(
            sum(1 for _r, e in keep if e is None), MANIFEST))
        return 0
    live = {e["sha256"] for _r, e in keep}
    removed = 0
    try:
        entries = list(os.scandir(str(store / "blobs")))
    except OSError:
        entries = []
    for ent in entries:
        if (_BLOB.match(ent.name) and ent.name not in live) or snapshot._stale_part(ent, now):
            if late():
                audit.log(HOOK, "PRUNE-PARTIAL", "pruning stopped at its time box -- the rest is pruned next time")
                return removed
            try:
                os.unlink(ent.path)
                removed += 1
            except OSError:
                pass
    return removed
