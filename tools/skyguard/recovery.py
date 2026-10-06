"""Recovery snapshots: the load order, the INIs and the one save a command is about to rewrite (Plan 6 Task 15, spec 5.3).

The script snapshot (snapshot.py) covers .psc/.pex. This covers the rest of what a bad command can break and the lock
does not protect:

* LOAD ORDER AND INIs, before every non-read-only shell command (snapshot.read_only: both stores agree on what is
  read-only): `plugins.txt` and `loadorder.txt` in each declared SKYRIM_LOADORDER_DIR, and every `Skyrim*.ini` (any case)
  directly in each declared SKYRIM_CONFIG_DIR. The CONFIGURED roots only (skyrim-paths.env and the environment, through
  paths.roots): nothing is probed, so an install that declares neither gets no such snapshot and the audit log says so
  (once per change of that state, not once per call).
* THE SAVE A COMMAND WRITES, whatever the command's first word (fix round 1, I1), before a command S-RESAVER-APPLY
  asks about (ReSaver run with --apply and a save-writing operation; shell_rules.resaver_writes, on the rule's own
  decision: the <out.ess> it writes, never the input it only reads -- fix round 1, I2) or one S-DEVBENCH-SAVE covers (DevBench told to save
  over an EXISTING save, by MCP, curl, Invoke-RestMethod or devbench-cli; mcp_rules.save_name / shell_save_names, then
  the rule's Saves folder and its case-insensitive listing): that `<name>.ess` and its `<name>.skse` co-save. Never the
  save folder. A save and its co-save are kept together or not at all.

STORE `.claude/backups/recovery_snapshots/`:
* `blobs/<sha256>`: content-addressed (snapshot._store_blob, backup.py's part file + running sha256 + size check), so
  identical bytes are stored once however many snapshots hold them.
* `MANIFEST.jsonl`: Task 13's line -- `v, ts, src` (verbatim) `, dest` (the blob's name) `, size, sha256, tool, agent,
  session_id` -- plus `t` (epoch), `kind` (config | save), `snap` (the snapshot id) and `command` (its first 120 bytes).
  One locked append (backup._append_line) per snapshot. A reader skips and COUNTS a line that does not parse.
* `index.json`: per path, the size / mtime_ns / sha256 / snapshot id last recorded, so a path is re-read only when needed.

DEDUP AND RATE LIMIT. A file whose listing (size, mtime) matches the index is not read again; a line is written only when
a path's content differs from what was last recorded for it, so a snapshot id holds what CHANGED and `resolve` gives the
full state at that id. Config files are re-read regardless once per CONFIG_RATE seconds (a same-size, same-mtime rewrite
is seen then). A save is copied at most once per SAVE_RATE seconds per path (the state before the FIRST of a burst of
ReSaver runs is the one kept; the burst's intermediate states are not -- logged RATE-LIMITED).

BOUNDS. A save whose .ess plus .skse exceed SAVE_MAX_BYTES is not copied (SKIPPED, with both sizes). The copy is
time-boxed: at most SAVE_BOX seconds and never past the caller's deadline (the hook's budget); a copy cut short leaves no
part file and no snapshot of that save and is logged PARTIAL -- never a silent skip.

ORDER. The hooks call this in PreToolUse (Claude: snapshot-before-tool and protect-mcp; Codex: pre-tool's side
effects), so the copy is finished before the command can start.

PRUNING (once a day, inside PRUNE_BOX): a snapshot is kept WHOLE while it is younger than KEEP_DAYS (a save:
SAVE_KEEP_DAYS) or holds some path's newest content, and each kept config snapshot keeps the earlier lines its full state
needs (fix round 1, I4); every other line goes, then every blob no remaining line names is deleted -- PERMANENTLY, as
this is the tool's own store. An unreadable line is kept as it is and stops blob deletion. Every line records its
snapshot's line count and full path set, so resolve() REFUSES a snapshot pruning has cut into instead of returning a
state that never existed. A save copy also keeps SAVE_RESERVE_BYTES free on its drive (review M2).

Restoring: `tools/skyguard.sh snapshot list | restore` (snapcli.py).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

from . import backup, mcp_rules, paths, shell_rules, shellparse, snapshot

HOOK = "recovery-snapshot"
STORE = "recovery_snapshots"
MANIFEST = "MANIFEST.jsonl"
INDEX = "index.json"
DAY = 86400
CONFIG_RATE = 60          # seconds: config files are re-read at most this often when their listing is unchanged
SAVE_RATE = 300           # seconds: one copy of a given save per this long
LOADORDER_FILES = ("plugins.txt", "loadorder.txt")
_INI = re.compile(r"^skyrim[^/\\]*\.ini$", re.IGNORECASE)
SAVE_MAX_BYTES = 192 * 1024 * 1024   # .ess + .skse together
# Review M2: a save copy never takes its drive below this much free space (SKIPPED, logged, instead).
SAVE_RESERVE_BYTES = 2 * 1024 * 1024 * 1024
SAVE_BOX = 10.0           # seconds, at most, for one call's save copies (also capped by the caller's deadline)
LOCK_WAIT = 0.2           # a call waits this long for the store lock
KEEP_DAYS = 14
SAVE_KEEP_DAYS = 3        # saves are tens of MB on a drive that may be nearly full; the newest per save is always kept
PRUNE_EVERY = DAY
PRUNE_BOX = 0.5
COSAVE = ".skse"
_BLOB = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^\d{8}_\d{6}-(config|save)(?:-\d+)?$")
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


def _cosave(ess):
    folder, base = os.path.split(ess)
    return _listed(folder, base[:-4] + COSAVE) if base.lower().endswith(".ess") else None


def _resaver_save(word, cwd, shell, roots, env):
    """(path, (level, message) or None). A save ReSaver WRITES (fix round 1, I2), as written: absolute, relative to the
    payload cwd (where ReSaver resolves it), else a bare name in the Saves folder. A target that does not exist yet is
    a NEW file: nothing to keep, and the log says so."""
    if not word:
        return None, ("SKIPPED", "ReSaver --apply names no .ess this guard can read, so it cannot tell which save "
                                 "to keep")
    if "*" in word or "?" in word:
        return None, ("SKIPPED", "ReSaver --apply names a pattern ({}), not one save, so no save was kept -- copy the "
                                 "saves yourself first".format(word))
    n = paths.norm_abs(word, msys=(shell != "powershell"))
    cands = [n] if n else []
    if not n:
        base = paths.norm_abs(cwd) if cwd else None
        if base:
            j = paths.norm_abs(base + "/" + word.replace("\\", "/"))
            if j:
                cands.append(j)
        folder = mcp_rules.saves_folder(roots, env)
        if folder:
            cands.append(os.path.join(folder, re.split(r"[/\\]", word)[-1]))
    for c in cands:
        hit = _listed(*os.path.split(c))
        if hit:
            return hit, None
    return None, ("NEW", "ReSaver writes {}, which does not exist yet -- nothing to keep".format(word))


def _devbench_save(name, roots, env):
    """(path, problem); (None, "") for a NEW save name -- nothing exists to keep, and the rule passes it too."""
    clean = mcp_rules._clean_name(name)
    if clean is None:
        return None, "cannot tell which save the DevBench call overwrites ({!r})".format(name)
    folder = mcp_rules.saves_folder(roots, env)
    if folder is None:
        return None, "no Saves folder is known, so the save {} cannot be kept".format(clean)
    return _listed(folder, clean + ".ess"), ""


def shell_saves(command, shell, cwd, roots, env):
    """([save path], [(level, message)]) for the saves a shell command WRITES under S-RESAVER-APPLY / S-DEVBENCH-SAVE,
    decided on the parsed command as the rules decide (fix round 1, I1: whatever its first word). The parse happens
    only when the text names ReSaver with --apply, or the DevBench endpoint / CLI (M6: a JSON-escaped "save" is found
    by the parse, not by a text filter)."""
    low = command.lower()
    resaver = "resaver" in low and shell_rules.RESAVER_APPLY in low
    dev = any(w in low for w in mcp_rules._SHELL_WORDS)
    if not (resaver or dev) or roots is None:
        return [], []
    parsed = shellparse.parse(command, shell or "")
    found, notes = [], []
    if resaver:
        w = shell_rules.resaver_writes(command, shell or "", parsed)
        for word in (w[1] or [""]) if w else []:
            p, note = _resaver_save(word, cwd, shell, roots, env)
            if p:
                found.append(p)
            if note:
                notes.append(note)
    if dev:
        for name in mcp_rules.shell_save_names(command, parsed):
            p, why = _devbench_save(name, roots, env)
            if p:
                found.append(p)
            elif why:
                notes.append(("SKIPPED", why))
    return list(dict.fromkeys(found)), notes


# ---------------------------------------------------------------- entry points (the hooks)


def run(project, command, payload_empty, audit, roots, cwd="", shell="", agent="", session_id="", deadline=None,
        now=None, env=None):
    """Shell route: the saves the command writes (if any), then the load order and INIs. Never raises into the hook.
    Fix round 1 (I1): the read-only first-word skip applies to the load order and INIs only -- `echo x && resaver ...`,
    `env X=y resaver ...` still write the save, and the rule still fires on them."""
    if payload_empty or not (command or "").strip():
        return
    env = os.environ if env is None else env
    saves, notes = shell_saves(command, shell, cwd, roots, env)
    take(project, audit, roots, command, saves, notes, not snapshot.read_only(command), now, deadline, agent,
         session_id)


def mcp(project, tool_name, args, audit, roots, agent="", session_id="", deadline=None, now=None, env=None):
    """MCP route (protect-mcp): a DevBench save over an existing save keeps that save first."""
    parts = (tool_name or "").split("__")
    if len(parts) < 3 or parts[0] != "mcp" or not parts[1].startswith("devbench"):
        return
    name = mcp_rules.save_name(parts[-1], args)
    if name is None or roots is None:
        return
    p, why = _devbench_save(name, roots, os.environ if env is None else env)
    if not (p or why):
        return
    take(project, audit, roots, "{} save {}".format(tool_name, name), [p] if p else [],
         [("SKIPPED", why)] if why else [], False, now, deadline, agent, session_id)


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
    last = idx["last"].get(kind, "")
    n = 1
    if last == base:
        n = 2
    elif last.startswith(base + "-") and last[len(base) + 1:].isdigit():
        n = int(last[len(base) + 1:]) + 1
    sid = base if n == 1 else "{}-{}".format(base, n)
    idx["last"][kind] = sid
    return sid


def take(project, audit, roots, command, saves, problems, do_config, now=None, deadline=None, agent="", session_id=""):
    now = time.time() if now is None else now
    for level, msg in problems:
        audit.log(HOOK, level, msg)
    if not saves and not do_config:
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
                  "snapshot{}".format(" (NOT of the save it is about to rewrite)" if saves else ""))
        return
    try:
        _take_locked(store, audit, roots, command, saves, do_config, now, deadline, agent, session_id)
    finally:
        lock.__exit__(None, None, None)


def _take_locked(store, audit, roots, command, saves, do_config, now, deadline, agent, session_id):
    idx = _read_index(store)
    before = json.dumps(idx, sort_keys=True)
    start = backup._clock()
    box_end = start + SAVE_BOX if deadline is None else min(deadline, start + SAVE_BOX)
    pending = []                              # (lines, index updates) appended only after the lines are on disk
    for ess in saves:
        got = _save(store, idx, ess, audit, command, now, box_end, agent, session_id)
        if got:
            pending.append(got)
    if do_config:
        got = _config(store, idx, roots, audit, command, now, agent, session_id)
        if got:
            pending.append(got)
    for lines, updates, logline in pending:
        data = b"".join((json.dumps(e, ensure_ascii=True) + "\n").encode("ascii") for e in lines)
        try:
            backup._append_line(store / MANIFEST, data)
        except OSError:
            audit.log(HOOK, "FAILED", "could not append to {} -- {} is NOT recorded; the next call retries".format(
                MANIFEST, lines[0]["snap"]))
            continue
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


def _save(store, idx, ess, audit, command, now, box_end, agent, session_id):
    co = _cosave(ess)
    members = [ess] + ([co] if co else [])
    name = os.path.basename(ess)
    try:
        stats = [os.stat(m) for m in members]
    except OSError:
        audit.log(HOOK, "FAILED", "could not read save {} -- NO snapshot of it was taken".format(name))
        return None
    total = sum(s.st_size for s in stats)
    if total > SAVE_MAX_BYTES:
        audit.log(HOOK, "SKIPPED", "save {} is {:.1f} MB with its co-save, over the {:.0f} MB bound -- NO snapshot of it "
                  "was taken; copy it yourself before this command".format(name, total / _MB, SAVE_MAX_BYTES / _MB))
        return None
    try:
        free = shutil.disk_usage(str(store)).free
    except OSError:
        free = None
    if free is not None and free - total < SAVE_RESERVE_BYTES:
        audit.log(HOOK, "SKIPPED", "save {} ({:.1f} MB) would leave under {:.0f} MB free on the snapshot drive -- NO "
                  "snapshot of it was taken; copy it yourself before this command".format(
                      name, total / _MB, SAVE_RESERVE_BYTES / _MB))
        return None
    recs = [idx["files"].get(m) for m in members]
    if all(isinstance(r, list) and r[:2] == [s.st_size, s.st_mtime_ns] for r, s in zip(recs, stats)):
        audit.log(HOOK, "UNCHANGED", "save {} is unchanged since recovery snapshot {} -- not copied again".format(
            name, recs[0][3]))
        return None
    last = recs[0][4] if isinstance(recs[0], list) and len(recs[0]) > 4 and recs[0][4] else None
    if last is not None and 0 <= now - last < SAVE_RATE:
        audit.log(HOOK, "RATE-LIMITED", "save {} was copied {} s ago ({}); this intermediate state is NOT kept -- the "
                  "state from before that run is".format(name, int(now - last), recs[0][3]))
        return None
    blobs = store / "blobs"
    got = []
    for m, s, r in zip(members, stats, recs):
        if isinstance(r, list) and r[:2] == [s.st_size, s.st_mtime_ns]:
            got.append((m, r[2], s))                  # this member is unchanged: its blob is already held
            continue
        try:
            sha = snapshot._store_blob(blobs, m, deadline=box_end)
        except backup.CopyTimeout:
            audit.log(HOOK, "PARTIAL", "the copy of save {} did not finish inside its time box ({:.1f} MB) -- NO "
                      "snapshot of it was kept; copy it yourself before this command".format(name, total / _MB))
            return None
        if sha is None:
            audit.log(HOOK, "FAILED", "could not copy {} (unreadable, or it changed while being copied) -- NO snapshot "
                      "of save {} was kept".format(os.path.basename(m), name))
            return None
        got.append((m, sha, s))
    ts = audit.stamp()
    sid = _new_id(idx, ts, "save")
    lines = [_line(ts, now, m, sha, os.stat(str(blobs / sha)).st_size, "save", sid, command, agent, session_id,
                   len(got), sorted(g[0] for g in got)) for m, sha, _s in got]
    updates = {m: [s.st_size, s.st_mtime_ns, sha, sid, int(now)] for m, sha, s in got}
    logline = "[{}] RECOVERY-SNAPSHOT (save: {}{}, {:.1f} MB) {} before: {}\n".format(
        ts, name, " + " + os.path.basename(co) if co else ", no co-save", total / _MB, sid, _short(command))
    return lines, updates, logline


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
    KEEP_DAYS (a save: SAVE_KEEP_DAYS) or holds the newest line of some path (the only copy of that content). For every
    kept config snapshot, each path's newest line at or before it is kept too, so its full state still resolves. A line
    kept only for that leaves its own (old) snapshot partial, which resolve() then refuses rather than misreports."""
    newest, last = {}, {}
    for i, e in enumerate(parsed):
        if e is not None:
            newest[e["src"]] = i
            last[e["snap"]] = i
    kept = {parsed[i]["snap"] for i in newest.values()}
    for sid, i in last.items():
        days = SAVE_KEEP_DAYS if parsed[i]["kind"] == "save" else KEEP_DAYS
        if now - parsed[i]["t"] < days * DAY:
            kept.add(sid)
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
