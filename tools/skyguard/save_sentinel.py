"""The PostToolUse save sentinel (role `save-sentinel`): a save changed with no backup first, whatever changed it.

The pre-call save rules (saves.py) see what a call NAMES. A save changed by code that builds its path at run time
(`python fix.py`) is invisible to them -- saves.py's KNOWN LIMITS. This role closes that gap after the fact: it hands
the save folders to the shared sentinel (modding_harness/sentinel.py), which compares them with the previous look
and reports every .ess/.skse modified or removed without a committed backup in the permanent store since then.
It lists and stats only, and never blocks (a post-call hook cannot undo the call).

Watched: every existing Saves folder the game may read (each declared config dir, Documents -- from the environment
and, on Windows, as the OS reports it -- / My Games / every game folder, and each game folder's INI sLocalSavePath
override), plus any folder under the project marked with a `.protect-saves` file (kept save copies). While the game
runs it may be the writer, so the report is given to the agent only, not raised to the user.

    report_text(project, roots, env) -> (text, loud)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from modding_harness import save_lock, sentinel
from . import mcp_rules, paths, save_profile

MARKER = ".protect-saves"
ELEVATED_REPEAT = 12 * 3600   # seconds between "this agent runs elevated" warnings
MARKED_DEPTH = 4          # <project>/<a>/<b>/<c>/.protect-saves at most
MARKED_REFRESH = 3600     # seconds between searches for marked folders
GAMES = ("SkyrimVR.exe", "SkyrimSE.exe", "Skyrim.exe")
MOST_A_SESSION_CHANGES = 40   # saves one play session can plausibly change between two looks
FAILED_REPEAT = 3600          # seconds between reports that the sentinel itself is failing
UNLOCK_LASTS = 3600           # seconds a save the user unlocked stays unlocked, for a repair in progress
_INI = re.compile(r"^\s*sLocalSavePath\s*=\s*(.*?)\s*$", re.I | re.M)
SKIP = {".git", "node_modules", "__pycache__", ".claude"}


def _known_documents():
    """Documents as Windows reports it (a OneDrive- or policy-redirected one), or nothing off Windows."""
    if os.name != "nt":
        return []
    try:
        import ctypes
        import uuid
        guid = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID("FDD39AD0-238F-46AF-ADB4-6C85480369C7").bytes_le)
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) != 0:
            return []
        try:
            return [out.value]
        finally:
            ctypes.windll.ole32.CoTaskMemFree(out)
    except (OSError, AttributeError, ValueError):
        return []


def save_folders(roots, env):
    """Every EXISTING folder the game may read saves from (mcp_rules.saves_folder returns only the first)."""
    games = [Path(d) for d in (roots.physical_config_dirs or roots.config_dirs)]
    # SKYGUARD_NO_KNOWN_FOLDERS=1: the environment's Documents only (tests: never the real profile's folders).
    known = [] if env.get("SKYGUARD_NO_KNOWN_FOLDERS") == "1" else _known_documents()
    docs = list(dict.fromkeys(mcp_rules._documents(env) + known))
    games += [Path(d) / "My Games" / g for d in docs for g in paths.GAME_FOLDERS]
    found = []
    for game in games:
        found.append(game / "Saves")
        for ini in ("Skyrim.ini", "SkyrimCustom.ini", "SkyrimVR.ini", "SkyrimPrefs.ini"):
            try:
                text = (game / ini).read_bytes().decode("latin-1")
            except OSError:
                continue
            for value in _INI.findall(text):
                value = value.strip("\"'").rstrip("\\/")
                if value:
                    found.append(Path(value) if os.path.isabs(value) else game / value)
    unique = {}
    for f in found:
        try:
            present = f.is_dir()
        except OSError:                    # an sLocalSavePath under a denied folder must not stop every look
            continue
        if present:
            unique.setdefault(os.path.normcase(os.path.abspath(f)), f)
    return list(unique.values())


def marked_folders(project, depth=MARKED_DEPTH):
    found, stack = [], [(str(project), 0)]
    while stack:
        folder, level = stack.pop()
        try:
            with os.scandir(folder) as it:
                entries = list(it)
        except OSError:
            continue
        if any(e.name == MARKER and e.is_file() for e in entries):
            found.append(folder)
            continue                           # the walk covers everything below
        if level < depth:
            stack.extend((e.path, level + 1) for e in entries
                         if e.name not in SKIP and e.is_dir(follow_symlinks=False))
    return sorted(found)


def game_running():
    command = ["tasklist", "/NH", "/FO", "CSV"] if os.name == "nt" else ["ps", "-eo", "args"]
    try:
        out = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return False
    return any(g.lower() in out for g in GAMES)      # off Windows the game runs under Proton: its name is in the arguments


def session_end(save_folder, since):
    """When the game last ran, by the logs it rewrites beside its saves, if that was since `since`; None when none shows."""
    game = Path(save_folder).parent
    newest = None
    for log in list(game.glob("SKSE/*.log")) + [game / "Logs" / "Script" / "Papyrus.0.log"]:
        try:
            stamp = log.stat().st_mtime
        except OSError:
            continue
        if stamp >= since and (newest is None or stamp > newest):
            newest = stamp
    return newest


def games_doing(changes, game_folders, since):
    """The (kind, path) changes the game itself may have made: in a folder it writes, a few at a time, and dated inside
    the time it was running or the session that ended since the last look. A bulk change is never a play session's."""
    running, quiet = None, []
    counts = {}
    for _kind, path in changes:
        folder = os.path.normcase(os.path.abspath(os.path.dirname(path)))
        counts[folder] = counts.get(folder, 0) + 1
    for kind, path in changes:
        folder = os.path.normcase(os.path.abspath(os.path.dirname(path)))
        if folder not in game_folders or counts[folder] > MOST_A_SESSION_CHANGES:
            continue
        if running is None:
            running = game_running()
        end = None if running else session_end(os.path.dirname(path), since)
        if (running or end is not None) and sentinel.dated_within(kind, path, since, end):
            quiet.append((kind, path))
    return quiet


class _NoStore:
    root = Path(os.devnull)


def state_path(project):
    return Path(project) / ".claude" / "backups" / ".save-sentinel.json"


def lock_switch(project):
    """The optional save-lock switch (modding_harness/save_lock.py); self_rules protects it from agent changes."""
    return Path(project) / ".modding-agent-harness" / "save-lock.json"


def exempt_path(project):
    """What the user unlocked by hand, and until when; the save locks leave those alone for UNLOCK_LASTS."""
    return Path(project) / ".modding-agent-harness" / "save-lock-exempt.json"


def record_unlocked(project, paths_, now=None):
    now = time.time() if now is None else now
    path = exempt_path(project)
    rows = _exempt_rows(path, now)
    rows.update({str(p): now + UNLOCK_LASTS for p in paths_})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows), encoding="utf-8")


def _exempt_rows(path, now):
    try:
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in rows.items() if isinstance(v, (int, float)) and v > now} if isinstance(rows, dict) else {}


def lock_notes(project, state, before, profile, now):
    """(notes, raise to the user?) for save locking: lock new or changed saves once settled while the switch is on,
    and say so loudly when locking was switched off or the agent runs elevated (the locks do not hold it)."""
    enabled = bool(save_lock.read_config(lock_switch(project)).get("enabled"))
    notes, loud = [], False
    if state.get("lock_enabled") and not enabled:
        notes.append("SAVE LOCK: save locking was switched OFF ({} changed or removed). If the user did not do this, "
                     "an agent or script did: tell them now. Switching it back on is the user's decision: "
                     "`tools/skyguard.sh save-lock on`."
                     .format(lock_switch(project)))
        loud = True
    state["lock_enabled"] = enabled
    if not enabled or not save_lock.supported():
        return notes, loud
    files = state.get("files") or {}
    folders = list(dict.fromkeys([str(r) for r in profile.save_roots]
                                 + [os.path.dirname(p) for _, _, p in files.values()]))
    try:
        failed = save_lock.auto_lock(state, before, files, folders, now=now, extensions=profile.extensions,
                                     config=lock_switch(project), exempt=tuple(_exempt_rows(exempt_path(project), now)))
    except Exception as exc:        # noqa: BLE001 -- locks are optional; the look above must still reach the agent
        failed = ["(locking unavailable: {}: {})".format(type(exc).__name__, exc)]
    if failed:                      # the user's business, once per failure: this runs after every tool call
        seen = sorted(failed)
        if state.get("lock_failed") != seen or now - state.get("lock_failed_time", 0) > ELEVATED_REPEAT:
            state["lock_failed"], state["lock_failed_time"] = seen, now
            notes.append("SAVE LOCK: could not lock {} save(s) or folder(s), so they are NOT protected: {}".format(
                len(failed), ", ".join(failed[:5])))
            loud = True
    else:
        state.pop("lock_failed", None)
        state.pop("lock_failed_time", None)
    if save_lock.is_elevated():
        if "elevated_warned" not in state or now - state["elevated_warned"] > ELEVATED_REPEAT:
            state["elevated_warned"] = now
            notes.append("SAVE LOCK: this agent session runs as administrator, so the save locks do NOT protect the "
                         "saves from it. Restart the agent from a normal (non-admin) terminal.")
            loud = True
    else:
        state.pop("elevated_warned", None)
    return notes, loud


def report_text(project, roots, env, now=None):
    """(text for the agent, raise to the user?) -- ('', False) when nothing changed without a backup. A look that fails
    says so, once an hour: the wrapper discards a crash, and the user was told any save change is reported."""
    now = time.time() if now is None else now
    try:
        return _look(project, roots, env, now)
    except Exception as exc:                           # noqa: BLE001 -- silence here would read as "nothing changed"
        mark = state_path(project).with_name(".save-sentinel.failed")
        try:
            if now - mark.stat().st_mtime < FAILED_REPEAT:
                return "", False
        except OSError:
            pass                                       # silent-ok: no mark yet means report it now
        try:
            mark.parent.mkdir(parents=True, exist_ok=True)
            mark.write_bytes(b"")
        except OSError:
            pass                                       # silent-ok: this report does not depend on the mark
        return ("SAVE SENTINEL: this look failed ({}), so changes to saves are NOT being reported right now. Run "
                "tools/hook-canary.sh, and tell the user.".format(type(exc).__name__)), True


def _look(project, roots, env, now):
    from . import save_store
    path = state_path(project)
    state = sentinel.load_state(path, now=now)
    if state.get("busy"):           # the record is locked just now: nothing to compare with, and it stays the baseline
        return sentinel.describe(sentinel.check(None, None, state)), False
    if state.get("marked") is None or now - state.get("marked_time", 0) > MARKED_REFRESH:
        state["marked"] = marked_folders(project)
        state["marked_time"] = now
    profile = save_profile.skyrim(save_folders(roots, env))
    try:
        store = save_store.store(project)
    except (OSError, ValueError):
        store = _NoStore()        # no readable store: every change is reported, none is "covered"
    before = dict(state.get("files") or {})
    since = state.get("time", now)
    report = sentinel.check(profile, store, state, watch=state["marked"], now=now)
    notes, lock_loud = lock_notes(project, state, before, profile, now)
    # Everything that can fail comes before the record: written first, a failure after it lost the finding for good.
    folders = {os.path.normcase(os.path.abspath(f)) for f in profile.save_roots}
    quiet = games_doing(report.unbacked, folders, since)
    raised = tuple(c for c in report.unbacked if c not in quiet)
    # A folder that stays unreadable is said once, not after every tool call.
    standing = sorted(report.truncated + report.unread)
    if state.get("noted") == standing:
        report = report._replace(truncated=(), unread=())
    state["noted"] = standing
    text = sentinel.describe(report._replace(unbacked=raised))
    if raised:
        text += ("\nThe save rules did not back these up. If a command or script you ran did this, tell the user now; "
                 "a removed file may be in the Recycle Bin. Restore points: tools/skyguard.sh snapshot list --kind save")
    if quiet:
        lines = ["SAVE SENTINEL: {} save file(s) changed while the game was running or before it last exited, so it may be "
                 "the game itself saving:".format(len(quiet))]
        lines += ["  {}: {}".format(kind, where) for kind, where in quiet[:15]]
        if len(quiet) > 15:
            lines.append("  ... and {} more".format(len(quiet) - 15))
        text = "\n".join(t for t in [text] + lines if t)
    try:
        sentinel.save_state(path, state)
    except OSError as exc:          # this look's findings still count; the old record stays the baseline, so at worst
        notes.append("SAVE SENTINEL: this look could not be recorded ({}), so the next look may repeat it."    # a repeat
                     .format(type(exc).__name__))
    text = "\n".join([t for t in [text] + notes if t])
    return text, bool(raised) or lock_loud or report.state_lost


def render(text, loud):
    """The PostToolUse answer: context for the agent, and a system message when the user should see it."""
    if not text:
        return ""
    out = {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}
    if loud:
        out["systemMessage"] = text
    return json.dumps(out)
