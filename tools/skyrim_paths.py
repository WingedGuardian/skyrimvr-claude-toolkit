"""Where this install keeps its logs, saves and crash dumps.

Shared by the triage tools so the discovery rule lives in exactly one place.
Two things it deliberately does NOT do: bake in a path from whoever wrote the
tool, and pick between candidates silently.

The silent pick is the real hazard. A machine can carry several per-game
folders under Documents/My Games at once -- a VR install and an SE install, or
one left behind by an uninstall -- and they are structurally identical. Reading
the wrong one does not fail. It produces a complete, plausible triage of a log
nobody asked about, and a clean bill of health is exactly what it looks like.
So when there is more than one candidate, say so, and say how to override it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Priority order. VR first: this toolkit is VR-first, and where both exist the
# rival is usually the leftover. The GOG and Epic builds of SE/AE write under
# their own folder names (SKSE64's SAVE_FOLDER_NAME, skse64_common/skse_version.h
# in ianpatt/skse64), so a Steam-only list misses them entirely.
GAME_FOLDERS = ("Skyrim VR", "Skyrim Special Edition", "Skyrim Special Edition GOG",
                "Skyrim Special Edition EPIC", "Skyrim")


def my_games() -> Path:
    """<user profile>/Documents/My Games -- the root Bethesda writes under."""
    profile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return Path(profile) / "Documents" / "My Games"


def find_game_dir(subpath: str, base: Path | None = None,
                  games: tuple[str, ...] = GAME_FOLDERS):
    """Return (chosen, others) for <base>/<game>/<subpath>.

    `others` is every rival that also exists. Callers are expected to surface
    it rather than drop it -- see the module docstring.
    """
    root = Path(base) if base is not None else my_games()
    existing = [root / g / subpath for g in games if (root / g / subpath).is_dir()]
    if not existing:
        # Name a real location even when nothing exists, so the error that
        # follows points somewhere the user can actually go and look.
        return root / games[0] / subpath, []
    return existing[0], existing[1:]


def resolve_dir(env_var: str, subpath: str, base: Path | None = None):
    """find_game_dir, with an environment override and a warning when ambiguous."""
    override = os.environ.get(env_var)
    if override:
        return Path(override), []

    chosen, others = find_game_dir(subpath, base=base)
    if others:
        lines = ["[skyrim_paths] more than one candidate exists; using:",
                 f"  {chosen}"]
        lines += [f"  ignoring: {o}" for o in others]
        lines += ["  They can belong to different installs, and the wrong one",
                  f"  reads perfectly. If this picked wrong, set {env_var}."]
        print("\n".join(lines), file=sys.stderr)
    return chosen, others


# ---------------------------------------------------------------------------
# The game folder, and 7-Zip
# ---------------------------------------------------------------------------
TOOLKIT_ROOT = Path(__file__).resolve().parent.parent


def game_root(environ=None, toolkit: Path | None = None):
    """(game folder, where the answer came from) -- the same precedence the guard's tool lookups use.

    1. $SKYRIM_GAME_ROOT;
    2. SKYRIM_GAME_ROOT in the toolkit's skyrim-paths.env (written by setup.sh; the legacy
       .claude/skyrim-paths.env when the root file is absent), read as DATA by the guard's own reader
       (tools/skyguard/paths.py), never sourced;
    3. the folder the toolkit is installed in -- the toolkit is extracted into the game folder.

    A paths file that cannot be read, or that names SKYRIM_GAME_ROOT in a form the reader refuses, raises
    ValueError rather than falling through to (3): a silent fallback would quietly answer about a different
    folder than the one the user configured.
    """
    from skyguard import paths as sg_paths     # tools/ is on sys.path whenever this module is importable

    env = os.environ if environ is None else environ
    root = Path(toolkit) if toolkit is not None else TOOLKIT_ROOT
    try:
        cfg = sg_paths.read_install(str(root), env)
    except (OSError, ValueError) as e:
        raise ValueError(f"cannot read {sg_paths.PATHS_FILE} in {root}: {e}") from e
    if env.get("SKYRIM_GAME_ROOT"):
        return Path(env["SKYRIM_GAME_ROOT"]), "SKYRIM_GAME_ROOT"
    if any(key == "SKYRIM_GAME_ROOT" for _, key in cfg.malformed):
        raise ValueError(f"{sg_paths.PATHS_FILE} in {root} sets SKYRIM_GAME_ROOT in a form that cannot be read "
                         "(see tools/skyguard/paths.py); fix it or set $SKYRIM_GAME_ROOT")
    value = cfg.first("SKYRIM_GAME_ROOT")
    if value:
        return Path(value), cfg.source or sg_paths.PATHS_FILE
    return root, "the toolkit folder"


# Console builds, in preference order: 7z (Windows 7-Zip, p7zip's full build), 7zz (7-Zip's own Linux/macOS
# build), 7za (the standalone console build).
SEVENZIP_NAMES = ("7z", "7zz", "7za")


def find_7zip(environ=None):
    """(path or None, how it was found -- or, when None, what to do about it).

    $SEVENZIP wins and is never second-guessed: if it names no file the answer is None, not a fallback to
    some other 7-Zip, because the user asked for that one. Then PATH, then the Program Files defaults.
    """
    import shutil

    env = os.environ if environ is None else environ
    override = env.get("SEVENZIP")
    if override:
        if Path(override).is_file():
            return str(override), "SEVENZIP"
        return None, f"SEVENZIP is set to {override}, which is not a file"
    search = env.get("PATH")
    for name in SEVENZIP_NAMES:
        hit = shutil.which(name, path=search)
        if hit:
            return hit, f"PATH ({name})"
    bases = [env.get(v) for v in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")]
    bases = [b for b in bases if b] or ["C:/Program Files"]
    for base in dict.fromkeys(bases):
        exe = Path(base) / "7-Zip" / "7z.exe"
        if exe.is_file():
            return str(exe), "Program Files"
    return None, ("7-Zip not found: install 7-Zip 26.00 (docs/installing-tools.md, Pinned downloads), "
                  "put 7z / 7zz / 7za on PATH, or set SEVENZIP to its full path")
