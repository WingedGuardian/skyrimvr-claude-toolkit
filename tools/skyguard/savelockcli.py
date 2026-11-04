"""`skyguard save-lock`: optional OS save locks (modding_harness/save_lock.py). Off unless the user switches it on.

    tools/skyguard.sh save-lock status [--project P]
    tools/skyguard.sh save-lock on     [--project P]   lock every save folder now; the save sentinel then locks each
                                                       new save once it settles (a few minutes after it is written)
    tools/skyguard.sh save-lock off    [--project P] [--forget-missing]
                                                       elevated prompt only; lifts EVERY lock (run it before you
                                                       remove the toolkit: the locks outlast it, and ordinary
                                                       Windows tools cannot lift them). --forget-missing drops the
                                                       record of a locked folder that is gone for good
    tools/skyguard.sh save-lock unlock <save.ess|folder> [--project P]
                                                       elevated prompt only; a save is backed up (with its co-save)
                                                       into the permanent store first, and nothing is unlocked if
                                                       that backup fails. It stays unlocked for an hour, then the
                                                       sentinel locks it again

While on, Windows itself refuses any delete, overwrite, rename or recycle of a locked save, from any program running
as the user -- an agent, a script, and the game too (saving over an old slot from the menu writes a new file; the old
one stays), and a mod or console command saving to one fixed name fails once that file is locked. Autosave and
quicksave slots stay unlocked. `on` is refused from an elevated (administrator) prompt: the locks do not hold an agent
running elevated. Windows/NTFS only. New saves are locked by the save sentinel, a Claude Code hook: on another agent,
only the saves present at `on` are locked. An agent's own `on`, `off` or `unlock` is refused by the guard (S-SAVE-LOCK).

Exit codes (the X4 contract): 0 done, 1 partial, 2 refused or usage, 3 unsupported.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from modding_harness import save_lock

EXTS = (".ess", ".skse")


def folders(project, env=None):
    """Every folder the save sentinel watches that holds saves: the game's save folders, and each folder under a
    .protect-saves marker that holds saves (a nested folder needs its own folder rule)."""
    from . import paths, save_sentinel
    env = os.environ if env is None else env
    roots = paths.roots(str(project), str(project), paths.read_install(str(project), env))
    found = [str(f) for f in save_sentinel.save_folders(roots, env)]
    for top in save_sentinel.marked_folders(project):
        for folder, _dirs, names in os.walk(top):
            if any(save_lock.is_member(n, EXTS) for n in names):
                found.append(folder)
    return list(dict.fromkeys(found))


def _unlock(project, target, out):
    from . import audit, save_store
    sid = save_lock.user_sid()
    if os.path.isdir(target):
        # A whole folder: lift the folder rule and every save's. Nothing changes yet, and the save rules back up any
        # later 1-2 save change; elevated only, like every unlock.
        paths = [target] + [e.path for e in os.scandir(target) if e.is_file() and save_lock.is_member(e.name, EXTS)]
        for p in paths:
            save_lock.unlock(p, sid)
        _spare(project, [target], out)
        print("unlocked {} path(s)".format(len(paths)), file=out)
        return 0
    stem, ext = os.path.splitext(target)
    if ext.lower() not in EXTS:
        print("not a save: " + target, file=out)
        return 2
    ess, skse = stem + ".ess", stem + ".skse"

    def backup(paths):
        if not os.path.exists(ess):
            raise OSError("no .ess beside " + target + "; nothing to back up as a pair")
        ids = save_store.backup_pairs(project, audit.Audit(str(project)), [(ess, skse)],
                                      "skyguard save-lock unlock " + target, agent="user-unlock")
        if not ids:
            raise OSError("the backup recorded nothing")
        print("backed up: " + ", ".join(ids), file=out)

    done = save_lock.backup_then_unlock(ess, [skse], sid, backup)
    _spare(project, [str(p) for p in done], out)
    print("unlocked: " + ", ".join(os.path.basename(str(p)) for p in done), file=out)
    return 0


def _spare(project, paths, out):
    from . import save_sentinel
    try:
        save_sentinel.record_unlocked(project, paths)
        print("It stays unlocked for an hour; after that the save sentinel locks it again.", file=out)
    except OSError as e:
        print("Could not record the unlock ({}): the save sentinel may lock it again at its next look.".format(e), file=out)


def main(argv=None, out=None) -> int:
    from . import save_sentinel
    out = sys.stdout if out is None else out
    ap = argparse.ArgumentParser(prog="skyguard save-lock", allow_abbrev=False)
    ap.add_argument("action", choices=("status", "on", "off", "unlock"))
    ap.add_argument("target", nargs="?")
    ap.add_argument("--project", default=None)
    ap.add_argument("--forget-missing", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 2
    from . import lock as _lock
    project = Path(a.project or _lock.default_root())
    switch = save_sentinel.lock_switch(project)
    if (a.action == "unlock") != bool(a.target):
        print("usage: save-lock unlock <save.ess|folder>; the other actions take no target", file=out)
        return 2
    try:
        if a.action == "status":
            print("save locking: " + ("ON" if save_lock.read_config(switch).get("enabled") else "OFF"), file=out)
            if save_lock.is_elevated():
                print("WARNING: " + save_lock.ELEVATED_WARNING, file=out)
            for f in folders(project):
                m = save_lock.status_map(f, EXTS)
                print("{}: folder {}, {} of {} save file(s) locked".format(
                    f, "locked" if save_lock.is_locked(f) else "unlocked", sum(m.values()), len(m)), file=out)
            return 0
        if a.action == "on":
            for f, n, skipped in save_lock.turn_on(switch, folders(project), EXTS):
                print("{}: locked {} new, left unlocked {} (autosave/quicksave slots, or just written)"
                      .format(f, n, skipped), file=out)
            print("save locking is ON: on Claude Code new saves are locked once they settle; on another agent only the "
                  "saves present now are locked", file=out)
            return 0
        if a.action == "off":
            kwargs = {"forget_missing": True} if a.forget_missing else {}
            n = save_lock.turn_off(switch, folders(project), EXTS, **kwargs)
            print("save locking is OFF; {} lock(s) lifted, so the saves are plain files again".format(n), file=out)
            return 0
        return _unlock(project, a.target, out)
    except save_lock.Unsupported as e:
        print("unsupported: {}".format(e), file=out)
        return 3
    except (PermissionError, OSError, ValueError) as e:     # ValueError: a malformed paths file
        print(str(e), file=out)
        return 2
