"""Manifest byte and tracked-inventory contracts without relying on the mutation copy's .git.

The existing real-checkout drift/inventory tests remain unchanged. These anchors
also run in gitless mutation copies: verification reads the actual guard bytes,
and inventory selection uses a separate, genuinely tracked, operation-owned tree.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess

from conftest import REPO


def _generator():
    spec = importlib.util.spec_from_file_location(
        "manifest_component_generator", REPO / "scripts/gen-guard-hashes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_matches_actual_guard_bytes():
    assert _generator().verify(REPO) == []


def test_guard_inventory_excludes_setup_writes_from_owned_tracked_tree(tmp_path, monkeypatch):
    # No inherited Git directory/index/config may redirect a fixture command to
    # the actual checkout. This fixture has an index, no commits or release tags.
    external_index = tmp_path / "external-index"
    sentinel = b"not a fixture index; preserve these bytes\n"
    external_index.write_bytes(sentinel)
    monkeypatch.setenv("GIT_INDEX_FILE", str(external_index))
    for key in list(os.environ):
        if key.upper().startswith("GIT_"):
            monkeypatch.delenv(key)
    empty_config = tmp_path / "git-global-empty"
    empty_config.write_bytes(b"")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    template = tmp_path / "empty-template"
    template.mkdir()
    root = tmp_path / "tracked-tree"
    root.mkdir()
    guards = ["tools/skyguard/engine.py", ".claude/hooks/protect-bash.sh",
              ".claude/settings.json", ".codex/hooks/skyguard-entry.ps1"]
    excluded = [".codex/hooks.json", "notes.txt", ".claude/skills/example/SKILL.md"]
    for name in guards + excluded:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"owned inventory fixture\n")
    for args in (["init", "--quiet", "--template", str(template)],
                 ["add", "--intent-to-add", "--force", "--", *guards, *excluded]):
        subprocess.run(["git", "-C", str(root), *args], check=True,
                       capture_output=True, timeout=10)
    assert _generator().guard_files(root) == sorted(guards)
    assert external_index.read_bytes() == sentinel
