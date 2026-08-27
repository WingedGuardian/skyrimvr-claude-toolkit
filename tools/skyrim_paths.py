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
# rival is usually the leftover.
GAME_FOLDERS = ("Skyrim VR", "Skyrim Special Edition", "Skyrim")


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
