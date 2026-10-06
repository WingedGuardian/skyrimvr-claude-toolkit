"""`skyguard snapshot list | restore`: the restore verb for both snapshot stores (Plan 6 Task 15).

    tools/skyguard.sh snapshot list [--kind scripts|config|save] [--project P]
    tools/skyguard.sh snapshot restore <id> [--path FILE ...] [--to DIR | --in-place] [--project P]

Ids: a script snapshot is its manifest stamp (`20261005_120000`, `-1` ... on a collision, snapshot.py); a recovery
snapshot is `<stamp>-config` or `<stamp>-save` (`-2` ... on a collision, recovery.py). `snapshot list` prints them.

WHERE A RESTORE WRITES. By default a NEW folder, `.claude/backups/restored/<id>__<now>/`, never the live files: script
paths keep their install-relative layout (`Data/Scripts/...`), recovery files their full original path with the drive
as a folder (`C/Users/.../plugins.txt`). `--to DIR` writes there instead and refuses, writing nothing, when any file it
would write already exists. `--in-place` writes over the live files -- each one that exists is first backed up the
Task 13 way (backup.take: hashed copy + MANIFEST.jsonl line, listed by scripts/restore-from-backup.sh); when any backup
fails, nothing is restored. A live file whose bytes already match the snapshot is left alone and reported `unchanged`
(final review B-M1: rewriting it would cut a mod manager's hard link and take a backup for nothing).

WHAT. A script snapshot restores its full map; a config snapshot the full load-order / INI state at that id
(recovery.resolve); a save snapshot its .ess and .skse. `--path FILE` (repeatable) restores only the files whose path
or file name matches. Every selected blob is re-hashed first (snapshot.check_blobs): a missing or damaged one refuses
the restore, writing nothing, and is named, with the command that restores each of the others file by file.

Exit codes (the X4 contract): 0 restored / listed, 1 failed part-way, 2 refused, unknown or usage (nothing written).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

from . import backup, mcp_rules, paths, recovery, snapshot

USAGE = ("usage: skyguard snapshot list [--kind scripts|config|save] [--project P]\n"
         "       skyguard snapshot restore <id> [--path FILE ...] [--to DIR | --in-place] [--project P]")
SHOW = 50
HOOK = "skyguard-snapshot"
_KINDS = ("scripts", "config", "save")
# Fix round 1 (I3): --in-place writes only where the snapshot's kind may write, resolved through links, inside the
# CONFIGURED roots; never a plugin or archive, never the guard's own files. A manifest line is data a tampered store can
# hold, so its path is checked here, at the write.
_NEVER_EXT = (".esp", ".esm", ".esl", ".bsa", ".ba2")
_NEVER_DIRS = (".claude", ".codex", ".skyguard")
_NEVER_RUNS = (("tools", "skyguard"), ("agent", "guards"))
_LOADORDER = ("plugins.txt", "loadorder.txt")


def _real(p):
    return os.path.normcase(os.path.realpath(str(p)))


def _never(target):
    """Clause (c): a plugin or archive, or anything under the guard's own folders. -> the reason, or ""."""
    parts = [x.lower() for x in re.split(r"[/\\]+", _real(target)) if x]
    if parts and parts[-1].endswith(_NEVER_EXT):
        return "a plugin or archive"
    if any(x in _NEVER_DIRS for x in parts) or any(
            parts[i:i + 2] == list(run) for run in _NEVER_RUNS for i in range(len(parts) - 1)):
        return "inside the guard's own files"
    if any(":" in x for x in parts[1:]):
        return "a stream or device name"
    return ""


def _allowed(project, sid, key, target):
    """"" when snapshot `sid` may write `target` in place, else why not (clauses a and b, then c)."""
    why = _never(target)
    if why:
        return why
    real, parent = _real(target), _real(Path(target).parent)
    name = Path(target).name.lower()
    if not recovery._ID.match(sid):                       # a script snapshot: .psc/.pex under the script trees
        inside = any(real.startswith(_real(Path(project).joinpath(*t.split("/"))) + os.sep) for t in snapshot.TREES)
        ok = name.endswith(snapshot.EXTS) and inside and _real(Path(project).joinpath(*key.split("/"))) == real
        return "" if ok else "not a .psc/.pex under the script folders of this install"
    try:
        cfg = paths.read_env(Path(project) / ".claude" / "skyrim-paths.env", os.environ)
    except (OSError, ValueError):
        return "the configured roots cannot be read"
    roots = paths.roots(str(project), str(project), cfg)
    if sid.endswith("-save") or "-save-" in sid:
        folder = mcp_rules.saves_folder(roots, os.environ)
        ok = name.endswith((".ess", ".skse")) and folder is not None and parent == _real(folder)
        return "" if ok else "not a save directly in the configured Saves folder"
    if name in _LOADORDER and parent in {_real(d) for d in roots.loadorder_dirs}:
        return ""
    if recovery._INI.match(name) and parent in {_real(d) for d in roots.config_dirs}:
        return ""
    return "not a load-order file or game INI directly in a configured folder"


def _unchanged(target, sha):
    """True when the live `target` is a regular file whose bytes already hash to the snapshot's `sha` (B-M1). Any
    doubt (missing, a link or folder, unreadable) is False: the file is then restored, backed up first, as before."""
    try:
        return os.path.isfile(str(target)) and not os.path.islink(str(target)) and backup._sha256(target) == sha
    except OSError:
        return False


def _project(p):
    from . import lock
    return p or os.environ.get("CLAUDE_PROJECT_DIR") or lock.default_root()


def _mb(n):
    return "{:.1f} MB".format(n / (1024 * 1024)) if n >= 1024 * 1024 else "{} bytes".format(n)


# ---------------------------------------------------------------- list


def _script_rows(project):
    store = snapshot.store_dir(project)
    rows = []
    for name in snapshot.manifest_names(store):
        m = snapshot._load_manifest(store, name)
        sid = name[:-len("__snapshot.json")]
        if m is None:
            rows.append((sid, "(unreadable manifest)", ""))
            continue
        what = ("full map, {} file(s)".format(len(m["set"])) if m["base"] is None
                else "{} changed, {} removed".format(len(m["set"]), len(m["gone"])))
        rows.append((sid, what + (" (partial)" if m.get("partial") else ""), m.get("command", "")))
    return rows


def _recovery_rows(project, kind):
    rows = []
    for g in recovery.snapshots(recovery.store_dir(project)):
        if g["kind"] != kind:
            continue
        names = [os.path.basename(e["src"]) for e in g["files"]]
        if kind == "save":
            what = " + ".join(names) + ", " + _mb(sum(e["size"] for e in g["files"]))
        else:
            what = "{} changed: {}".format(len(names), ", ".join(names))
        if not g["complete"]:
            what += "  (incomplete after pruning: cannot be restored)"
        rows.append((g["id"], what, g["command"]))
    return rows


def cmd_list(project, kind, out):
    print("Snapshots under {} (newest first):".format(Path(project) / ".claude" / "backups"), file=out)
    sections = (("scripts", "Scripts (.psc / .pex)", lambda: _script_rows(project)),
                ("config", "Load order and INIs", lambda: _recovery_rows(project, "config")),
                ("save", "Saves (taken before ReSaver --apply or a DevBench save)", lambda: _recovery_rows(project, "save")))
    for key, title, rows_of in sections:
        if kind and kind != key:
            continue
        rows = rows_of()
        print("\n{} -- {} snapshot(s)".format(title, len(rows)), file=out)
        for sid, what, cmd in reversed(rows):
            print("  {:<26} {}{}".format(sid, what, ("   before: " + cmd[:80]) if cmd else ""), file=out)
        if not rows:
            print("  (none)", file=out)
    bad = recovery.read_manifest(recovery.store_dir(project))[1]
    if bad and kind in (None, "config", "save"):
        print("\n{} unreadable line(s) in the recovery manifest were skipped".format(bad), file=out)
    print("\nRestore one (to a NEW folder; live files are written only with --in-place, each backed up first):", file=out)
    print("  bash tools/skyguard.sh snapshot restore <id> [--path <file>] [--to <folder> | --in-place]", file=out)
    return 0


# ---------------------------------------------------------------- restore


def _mirror(src):
    """A recovery file's place under a restore folder: its full path, the drive as a folder (`C/Users/...`). The key
    is relative, so it passes the same containment check as a script path (snapshot.check_blobs)."""
    n = paths.norm_abs(src) or src.replace("\\", "/")
    if re.match(r"^[a-z]:/", n):
        parts = [n[0].upper()] + n[3:].split("/")
    elif n.startswith("//"):
        parts = ["UNC"] + n[2:].split("/")
    else:
        parts = n.split("/")
    return "/".join(p for p in parts if p) or src


def _plan(project, sid):
    """(store, {key: sha256}, {key: what the user knows it by}, {key: live path}). Raises ValueError for an unknown id
    or an unreadable snapshot."""
    if recovery._ID.match(sid):
        store = recovery.store_dir(project)
        state = recovery.resolve(store, sid)
        files, shown, live = {}, {}, {}
        for src, e in state.items():
            k = _mirror(src)
            files[k], shown[k], live[k] = e["sha256"], src, Path(src)
        return store, files, shown, live
    store = snapshot.store_dir(project)
    name = sid + "__snapshot.json"
    if name not in snapshot.manifest_names(store):
        raise ValueError("no snapshot {} (see `snapshot list`)".format(sid))
    files = snapshot.resolve(store, name)
    return store, files, {k: k for k in files}, {k: Path(project).joinpath(*k.split("/")) for k in files}


def _matches(shown, wanted):
    s = shown.replace("\\", "/").lower()
    w = wanted.replace("\\", "/").lower().rstrip("/")
    ns, nw = paths.norm_abs(shown), paths.norm_abs(wanted)
    return s == w or s.rsplit("/", 1)[-1] == w or (ns is not None and ns == nw)


def _select(shown, wanted):
    """-> (selected keys, [--path values that matched nothing])"""
    keys = sorted(shown)
    if not wanted:
        return keys, []
    sel, missing = [], []
    for w in wanted:
        hit = [k for k in keys if _matches(shown[k], w)]
        if not hit:
            missing.append(w)
        sel += [k for k in hit if k not in sel]
    return sel, missing


def _new_folder(project, sid):
    root = Path(project) / ".claude" / "backups" / "restored"
    stamp = time.strftime("%Y%m%d_%H%M%S")
    base, n = root / "{}__{}".format(sid, stamp), 1
    while base.exists():
        n += 1
        base = root / "{}__{}-{}".format(sid, stamp, n)
    return base


def cmd_restore(project, sid, wanted, to, in_place, out):
    from .audit import Audit
    try:
        store, files, shown, live = _plan(project, sid)
    except ValueError as e:
        print("REFUSED: {}".format(e), file=out)
        return 2
    keys, missing = _select(shown, wanted)
    if missing:
        print("REFUSED: --path {} matches no file in snapshot {}. Nothing was restored.".format(
            ", ".join(missing), sid), file=out)
        return 2
    chosen = {k: files[k] for k in keys}
    try:
        bad = snapshot.check_blobs(store, sid, chosen)
    except OSError as e:
        print("REFUSED: a stored copy in snapshot {} cannot be read ({}). Nothing was restored.".format(sid, e),
              file=out)
        return 2
    except ValueError as e:
        print("REFUSED: {}. Nothing was restored.".format(e), file=out)
        return 2
    if bad:
        print("REFUSED: {} of the {} file(s) selected from snapshot {} cannot be restored. Nothing was "
              "restored.".format(len(bad), len(chosen), sid), file=out)
        for k, why in bad:
            print("  {}: the stored copy {}".format(shown[k], why), file=out)
        good = [k for k in keys if k not in dict(bad)]
        if good:
            print("Restore the other {} file by file, e.g.:".format(len(good)), file=out)
            for k in good[:5]:
                print('  bash tools/skyguard.sh snapshot restore {} --path "{}"'.format(sid, shown[k]), file=out)
        return 2
    audit = Audit(str(project))
    if in_place:
        odd = [(live[k], why) for k in keys for why in [_allowed(project, sid, k, live[k])] if why]
        if odd:
            print("REFUSED: {} is {}, so it is not written back in place. Nothing was restored.".format(*odd[0]),
                  file=out)
            return 2
        # Final review B-M1: a live file that already holds the snapshot's bytes is NOT rewritten. Replacing it would
        # cut a mod manager's hard link (the part file + os.replace below makes a new file) and back it up for nothing.
        same = [k for k in keys if _unchanged(live[k], chosen[k])]
        pairs = [(live[k], chosen[k]) for k in keys if k not in same]
        unchanged = [live[k] for k in same]
        dest = "the live files"
        for target, _sha in pairs:
            if target.is_file() and backup.take(str(project), "skyguard snapshot restore", str(target), audit) is None:
                print("REFUSED: could not back up {} before replacing it. Nothing was restored.".format(target),
                      file=out)
                return 2
    else:
        base = Path(to) if to else _new_folder(project, sid)
        pairs = [(base.joinpath(*k.split("/")), chosen[k]) for k in keys]
        clash = [str(t) for t, _s in pairs if t.exists()]
        if clash:
            print("REFUSED: {} file(s) already exist under {} (first: {}). Pick a new folder. Nothing was "
                  "restored.".format(len(clash), base, clash[0]), file=out)
            return 2
        dest = str(base)
        unchanged = []
    try:
        n = snapshot.write_blobs(store, pairs)
    except OSError as e:
        print("FAILED part-way restoring snapshot {} to {}: {}".format(sid, dest, e), file=out)
        audit.log(HOOK, "FAILED", "restore of {} to {} stopped: {}".format(sid, dest, e))
        return 1
    audit.log(HOOK, "RESTORED", "{} ({} file(s), {} unchanged) -> {}".format(sid, n, len(unchanged), dest))
    for target, _sha in pairs[:SHOW]:
        print("  restored {}".format(target), file=out)
    if len(pairs) > SHOW:
        print("  ... and {} more".format(len(pairs) - SHOW), file=out)
    for target in unchanged[:SHOW]:
        print("  unchanged {} (already identical to the snapshot: not rewritten)".format(target), file=out)
    if len(unchanged) > SHOW:
        print("  ... and {} more unchanged".format(len(unchanged) - SHOW), file=out)
    print("Restored {} file(s) from snapshot {} to {}{}{}".format(
        n, sid, dest, " (each replaced file was backed up first)" if in_place and n else "",
        "; {} already matched and were left as they are".format(len(unchanged)) if unchanged else ""), file=out)
    return 0


def main(argv, out=None):
    out = sys.stdout if out is None else out
    # Fix round 2 (N1): no abbreviated options -- `--in` must not run as --in-place unseen by the guard's rule.
    ap = argparse.ArgumentParser(prog="skyguard snapshot", add_help=False, allow_abbrev=False)
    ap.add_argument("action", nargs="?", choices=("list", "restore"))
    ap.add_argument("id", nargs="?")
    ap.add_argument("--kind", choices=_KINDS)
    ap.add_argument("--path", action="append", default=[])
    ap.add_argument("--to", default="")
    ap.add_argument("--in-place", action="store_true")
    ap.add_argument("--project", "--root", dest="project", default=None)
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        print(USAGE, file=out)
        return 2
    project = _project(a.project)
    if a.action == "list" and not (a.id or a.path or a.to or a.in_place):
        return cmd_list(project, a.kind, out)
    if a.action == "restore" and a.id and not a.kind and not (a.to and a.in_place):
        return cmd_restore(project, a.id, a.path, a.to, a.in_place, out)
    print(USAGE, file=out)
    return 2
