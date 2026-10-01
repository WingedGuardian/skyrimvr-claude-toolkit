"""Fixtures captured from REAL sessions (spec §7.3) -- never hand-written from docs."""
import json

from conftest import REPO

FIX = REPO / "tests" / "fixtures" / "payloads"
REQUIRED = {
    "claude": ["PreToolUse-Bash", "PreToolUse-PowerShell", "PreToolUse-Edit", "PreToolUse-Write", "SessionStart-none"],
    "codex": ["PreToolUse-Bash", "PreToolUse-apply_patch", "SessionStart-none"],
}


def _load(agent):
    return {f.name: json.loads(f.read_text(encoding="utf-8")) for f in sorted((FIX / agent).glob("*.json"))}


def test_every_required_shape_was_captured():
    for agent, keys in REQUIRED.items():
        names = list(_load(agent))
        for k in keys:
            assert any(n.startswith(f"{agent}-{k}-") for n in names), f"{agent}: no {k} fixture"


def test_codex_patches_cover_add_update_and_delete():
    cmds = [p["tool_input"]["command"] for n, p in _load("codex").items() if "apply_patch" in n]
    for verb in ("*** Add File:", "*** Update File:", "*** Delete File:"):
        assert any(verb in c for c in cmds), verb


def test_no_fixture_carries_the_capturing_users_home():
    for agent in REQUIRED:
        loaded = _load(agent)
        assert loaded, f"{agent}: no fixtures -- an empty set would pass this check vacuously"
        for name, p in loaded.items():
            assert "Moona" not in json.dumps(p), name
