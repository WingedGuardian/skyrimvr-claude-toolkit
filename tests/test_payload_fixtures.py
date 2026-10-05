"""Fixtures captured from REAL sessions (spec §7.3) -- never hand-written from docs."""
import json
from pathlib import Path

from conftest import REPO

FIX = REPO / "tests" / "fixtures" / "payloads"
REQUIRED = {
    "claude": ["PreToolUse-Bash", "PreToolUse-PowerShell", "PreToolUse-Edit", "PreToolUse-Write", "SessionStart-none"],
    "codex": ["PreToolUse-Bash", "PreToolUse-apply_patch", "SessionStart-none"],
}


def _load(agent):
    """Every payload under the agent's folder, version subfolders included (keyed by path under it)."""
    return {f.relative_to(FIX / agent).as_posix(): json.loads(f.read_text(encoding="utf-8"))
            for f in sorted((FIX / agent).rglob("*.json")) if f.name != "EXPECTED.json"}


def test_every_required_shape_was_captured():
    for agent, keys in REQUIRED.items():
        names = [n.rsplit("/", 1)[-1] for n in _load(agent)]
        for k in keys:
            assert any(n.startswith(f"{agent}-{k}-") for n in names), f"{agent}: no {k} fixture"


def test_codex_patches_cover_add_update_and_delete():
    cmds = [p["tool_input"]["command"] for n, p in _load("codex").items() if "apply_patch" in n.rsplit("/", 1)[-1]]
    for verb in ("*** Add File:", "*** Update File:", "*** Delete File:"):
        assert any(verb in c for c in cmds), verb


def test_no_fixture_carries_the_capturing_users_home():
    """Runs on whoever captures, so it checks THEIR user name, case-insensitively."""
    user = Path.home().name.lower()
    assert len(user) >= 3, f"user name {user!r} too short to search for meaningfully"
    for agent in REQUIRED:
        loaded = _load(agent)
        assert loaded, f"{agent}: no fixtures -- an empty set would pass this check vacuously"
        for name, p in loaded.items():
            assert user not in json.dumps(p).lower(), name
