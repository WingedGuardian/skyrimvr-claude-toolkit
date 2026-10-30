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

from modding_harness import sentinel
from . import mcp_rules, paths, save_profile

MARKER = ".protect-saves"
MARKED_DEPTH = 4          # <project>/<a>/<b>/<c>/.protect-saves at most
MARKED_REFRESH = 3600     # seconds between searches for marked folders
GAMES = ("SkyrimVR.exe", "SkyrimSE.exe", "Skyrim.exe")
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
        if f.is_dir():
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
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(["tasklist", "/NH", "/FO", "CSV"], capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return False
    return any('"' + g.lower() + '"' in out for g in GAMES)


class _NoStore:
    root = Path(os.devnull)


def state_path(project):
    return Path(project) / ".claude" / "backups" / ".save-sentinel.json"


def report_text(project, roots, env, now=None):
    """(text for the agent, raise to the user?) -- ('', False) when nothing changed without a backup."""
    from . import save_store
    now = time.time() if now is None else now
    path = state_path(project)
    state = sentinel.load_state(path)
    if state.get("marked") is None or now - state.get("marked_time", 0) > MARKED_REFRESH:
        state["marked"] = marked_folders(project)
        state["marked_time"] = now
    profile = save_profile.skyrim(save_folders(roots, env))
    try:
        store = save_store.store(project)
    except (OSError, ValueError):
        store = _NoStore()        # no readable store: every change is reported, none is "covered"
    report = sentinel.check(profile, store, state, watch=state["marked"], now=now)
    sentinel.save_state(path, state)
    text = sentinel.describe(report)
    if not report.unbacked:
        return text, False
    running = game_running()
    text += ("\nThe game is running, so it may be the game itself saving." if running else
             "\nThe save rules did not back these up. If a command or script you ran did this, tell the user now; "
             "a removed file may be in the Recycle Bin. Restore points: tools/skyguard.sh snapshot list --kind save")
    return text, not running


def render(text, loud):
    """The PostToolUse answer: context for the agent, and a system message when the user should see it."""
    if not text:
        return ""
    out = {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}
    if loud:
        out["systemMessage"] = text
    return json.dumps(out)
