"""Installed restrictive policy runs before save preservation for portable hosts."""
import hashlib
import json
from pathlib import Path
import sys
import time

import pytest

from conftest import REPO
sys.path.insert(0, str(REPO / "tools"))
from skyguard import local_policy, portable
from modding_harness import contracts


def register(root, source):
    script = root / local_policy.LEGACY
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(source)
    config = root / local_policy.CONFIG
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"v": 1, "kind": local_policy.KIND, "path": local_policy.LEGACY,
                                  "sha256": hashlib.sha256(script.read_bytes()).hexdigest()}))
    return script


def test_registered_policy_denies_before_any_permanent_save_backup(tmp_path):
    root = tmp_path / "toolkit"
    root.mkdir()
    save = tmp_path / "config/Saves/fixture.ess"
    save.parent.mkdir(parents=True)
    save.write_bytes(b"untouched save")
    (root / "skyrim-paths.env").write_text("SKYRIM_CONFIG_DIR='" + save.parent.parent.as_posix() + "'\n")
    script = register(root, "def decide(payload):\n return {'kind':'deny', 'reason':'installed runtime restriction'}\n"
                      "if __name__ == '__main__':\n raise RuntimeError('backup main must never execute')\n")
    action = contracts.Action("future-agent", "edit", "files", cwd=str(root),
                              paths=(str(save),), operations=("write",))
    outcome, _ = portable.check(action, root)
    assert outcome.result.decision == "deny" and outcome.result.reason == "installed runtime restriction"
    assert not outcome.transactions and not (root / ".modding-agent-harness/backups/saves").exists()
    assert save.read_bytes() == b"untouched save"
    script.write_text("def decide(payload):\n return {'kind':'allow'}\n")
    result = local_policy.restriction(root)(action)
    assert result.decision == "deny" and result.evaluation == "failed"


def test_unregistered_legacy_policy_cannot_be_bypassed_by_new_adapter(tmp_path):
    script = tmp_path / local_policy.LEGACY
    script.parent.mkdir(parents=True)
    script.write_text("def decide(payload): return {'kind':'allow'}\n")
    known = contracts.Action("opencode", "write", "files", paths=(str(tmp_path / "file"),), operations=("write",))
    assert local_policy.restriction(tmp_path)(known).decision == "deny"
    read = contracts.Action("opencode", "read", "files", paths=(str(script),), operations=("read",))
    assert local_policy.restriction(tmp_path)(read).decision == "allow"
    assert local_policy.restriction(tmp_path)(contracts.Action("other", "future", "unknown")).decision == "allow"


@pytest.mark.parametrize("source", ["def decide(payload): raise ValueError('broken')\n",
                                  "def decide(payload): return {'kind':'invented'}\n",
                                  "def decide(payload): print('not a verdict'); return {'kind':'allow'}\n"])
def test_failed_or_noisy_local_policy_refuses(source, tmp_path):
    register(tmp_path, source)
    result = local_policy.restriction(tmp_path)(contracts.Action("codex", "bash", "shell", command="echo fixture"))
    assert result.decision == "deny" and result.evaluation == "failed"


def test_local_policy_deadline_refuses(tmp_path):
    register(tmp_path, "def decide(payload):\n import time; time.sleep(5); return {'kind':'allow'}\n")
    start = time.monotonic()
    result = local_policy.restriction(tmp_path, start + .2)(contracts.Action("future", "bash", "shell", command="echo fixture"))
    assert result.decision == "deny" and time.monotonic() - start < 2


@pytest.mark.parametrize("tool,args", [("game", {"action": "save", "name": "probe"}),
                                      ("game", {"action": "load"}), ("game", {"action": "loadLast"}),
                                      ("console", {"command": "getpos x; save probe"}),
                                      ("papyrus", {"function": "Game.SaveGame", "args": ["probe"]}),
                                      ("papyrus", {"function": "ExecuteCommand", "args": ["load probe"]}),
                                      ("scenario", {"steps": [{"tool": "game", "args": {"action": "save"}}]})])
def test_structured_runtime_calls_cannot_bypass_registered_local_save_load_restriction(tmp_path, tool, args):
    register(tmp_path, "def decide(payload): return {'kind':'allow'}\n")
    request = contracts.Action("future", "mcp__devbench__" + tool, "mcp", provenance={"arguments": args})
    result = local_policy.restriction(tmp_path)(request)
    assert result.decision == "deny" and "runtime save/load" in result.reason
    read = contracts.Action("future", "mcp__devbench__game", "mcp", provenance={"arguments": {"action": "status"}})
    assert local_policy.restriction(tmp_path)(read).decision == "allow"
