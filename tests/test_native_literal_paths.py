"""A native edit/patch path is a literal filename, not a shell expression."""
import json
import sys
from pathlib import Path

import pytest

from conftest import REPO
sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, engine, paths, save_store
from skyguard.adapters import codex


@pytest.mark.parametrize("name", [" Space", "$SAVE_ALIAS"])
def test_patch_preserves_the_exact_physical_pair_including_space_and_dollar(tmp_path, monkeypatch, name):
    monkeypatch.setenv("SAVE_ALIAS", "Ordinary")
    game = tmp_path / "game"
    game.mkdir()
    saves = tmp_path / "config/Saves"
    saves.mkdir(parents=True)
    for stem in (name, "Ordinary", "Space"):
        for ext in (".ess", ".skse"):
            (saves / (stem + ext)).write_bytes((stem + ext).encode())
    patch = "*** Begin Patch\n*** Update File: " + name + ".ess\n@@\n-old\n+new\n*** End Patch"
    req = codex.to_request(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "apply_patch",
        "tool_input": {"command": patch}, "cwd": str(saves), "session_id": "s"}))
    assert Path(req.paths[0]).name == name + ".ess"
    roots = paths.roots(str(game), str(game), {"SKYRIM_CONFIG_DIR": str(saves.parent)})
    result = engine.pre_tool(req, str(game), lambda: roots, audit.Audit(str(game)))
    assert result.decision == "allow", result
    store = save_store.store(game)
    manifests = list((store.root / "transactions").glob("*.json"))
    assert len(manifests) == 1
    transaction = store.load(manifests[0].stem)
    assert {Path(member["path"]).name for group in transaction["groups"] for member in group["members"]} == {
        name + ".ess", name + ".skse"}
