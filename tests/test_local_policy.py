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


@pytest.mark.parametrize("kind", ["backup", "needs_files", "needs_set"])
def test_legacy_preservation_requirements_cannot_disappear_in_unrecognized_helpers(tmp_path, kind):
    save = tmp_path / "config/Saves/fixture.ess"
    save.parent.mkdir(parents=True)
    save.write_bytes(b"unchanged")
    (tmp_path / "skyrim-paths.env").write_text("SKYRIM_CONFIG_DIR='" + save.parent.parent.as_posix() + "'\n")
    decision = {"kind": kind, "op": "write", "affected": [str(save)], "count": 3, "set": None}
    register(tmp_path, "def decide(payload): return " + repr(decision) + "\n")
    action = contracts.Action("future-agent", "shell", "shell", cwd=str(tmp_path), shell="bash", command="python ordinary-helper.py",
                              provenance={"save_plan": {"mutations": [{"path": str(save), "operation": "write"}]}})
    outcome, _ = portable.check(action, tmp_path)
    assert outcome.result.decision == "deny"
    assert "local policy" in outcome.result.reason.lower()
    assert not outcome.transactions and save.read_bytes() == b"unchanged"
    assert not (tmp_path / ".modding-agent-harness/backups/saves").exists()


@pytest.mark.parametrize("kind", ["allow", "backup"])
def test_registered_allowed_file_edit_preserves_pair_once(tmp_path, kind):
    from skyguard import save_store
    save = tmp_path / "config/Saves/fixture.ess"
    save.parent.mkdir(parents=True)
    save.write_bytes(b"ess")
    save.with_suffix(".skse").write_bytes(b"skse")
    (tmp_path / "skyrim-paths.env").write_text("SKYRIM_CONFIG_DIR='" + save.parent.parent.as_posix() + "'\n")
    decision = {"kind": kind, "op": "write", "affected": [str(save)], "count": 1}
    register(tmp_path, "def decide(payload): return " + repr(decision) + "\n")
    action = contracts.Action("future", "edit", "files", cwd=str(tmp_path), paths=(str(save),), operations=("write",))
    outcome, _ = portable.check(action, tmp_path)
    assert outcome.result.decision == "allow", outcome.result.reason
    assert outcome.result.preservation == "verified" and len(outcome.transactions) == 1
    transaction = save_store.store(tmp_path).load(outcome.transactions[0])
    assert len(transaction["groups"]) == 1
    assert {Path(row["path"]).suffix for row in transaction["groups"][0]["members"]} == {".ess", ".skse"}


@pytest.mark.parametrize("defect", ["other_save", "wrong_operation", "bulk_threshold", "unresolved_set", "unbound_set", "no_recheck"])
def test_legacy_scope_and_approval_requirements_must_match_computed_plan(tmp_path, defect):
    from modding_harness import dispatch
    save, other = tmp_path / "first.ess", tmp_path / "other.ess"
    save.write_bytes(b"first")
    other.write_bytes(b"other")
    decision = {"kind": "backup", "op": "write", "affected": [str(save)], "count": 1}
    plan = dispatch.SavePlan((dispatch.Mutation(str(save), "write"),))
    if defect == "other_save":
        decision["affected"] = [str(other)]
    elif defect == "wrong_operation":
        decision["op"] = "delete"
    elif defect == "bulk_threshold":
        decision.update(kind="needs_files", count=3)
    else:
        decision.update(kind="needs_set", set=None if defect == "unresolved_set" else [str(save)])
        plan = dispatch.SavePlan(plan.mutations, wildcard=defect != "unbound_set",
                                 recheck=None if defect == "no_recheck" else lambda: plan.mutations)
    register(tmp_path, "def decide(payload): return " + repr(decision) + "\n")
    result = local_policy.restriction(tmp_path, plan_provider=lambda: plan)(
        contracts.Action("future", "bash", "shell", shell="bash", command="fixture"))
    assert result.decision == "deny" and result.evaluation == "evaluated", result


@pytest.mark.parametrize("kind", ["backup", "needs_files", "needs_set"])
def test_qualified_scope_reaches_dispatcher_without_granting_approval(tmp_path, kind):
    from modding_harness import dispatch
    saves = [tmp_path / (str(i) + ".ess") for i in range(3 if kind == "needs_files" else 1)]
    for path in saves:
        path.write_bytes(path.name.encode())
    decision = {"kind": kind, "op": "write", "affected": [str(p) for p in saves], "count": len(saves),
                "set": [str(p) for p in saves]}
    register(tmp_path, "def decide(payload): return " + repr(decision) + "\n")
    plan = dispatch.SavePlan(tuple(dispatch.Mutation(str(p), "write") for p in saves),
                             wildcard=kind == "needs_set", recheck=lambda: plan.mutations)
    result = local_policy.restriction(tmp_path, plan_provider=lambda: plan)(
        contracts.Action("future", "bash", "shell", shell="bash", command="fixture"))
    assert result.decision == "allow" and result.evaluation == "evaluated", result
    assert isinstance(result.warnings, tuple) and result.preservation == "not_required"


@pytest.mark.parametrize("wildcard", [False, True])
def test_registered_policy_retains_canonical_bulk_and_wildcard_approval(tmp_path, wildcard):
    from modding_harness import approval
    folder = tmp_path / "config/Saves"
    folder.mkdir(parents=True)
    members = [folder / (str(i) + suffix) for i in range(1 if wildcard else 3) for suffix in (".ess", ".skse")]
    for path in members:
        path.write_bytes(path.name.encode())
    (tmp_path / "skyrim-paths.env").write_text("SKYRIM_CONFIG_DIR='" + folder.parent.as_posix() + "'\n")
    command = 'rm "' + folder.as_posix() + '/"*.ess' if wildcard else ""
    decision = {"kind": "needs_set" if wildcard else "needs_files", "op": "delete" if wildcard else "write",
                "set": [str(p) for p in members], "affected": [str(p) for p in members], "count": len(members) // 2}
    register(tmp_path, "def decide(payload): return " + repr(decision) + "\n")
    action = contracts.Action("claude", "Bash" if wildcard else "Write", "shell" if wildcard else "files",
        cwd=str(tmp_path), shell="bash", session_id="s", command=command,
        paths=() if wildcard else tuple(map(str, members)), operations=() if wildcard else ("write",) * len(members))
    denied, _ = portable.check(action, tmp_path)
    assert denied.result.decision == "deny" and not denied.transactions
    transcript = tmp_path / "human.jsonl"
    text = approval.example("s", decision["op"], members, command, wildcard, len(members) // 2)
    transcript.write_text(json.dumps({"type": "user", "origin": {"kind": "human"},
                                     "message": {"role": "user", "content": text}}))
    allowed, _ = portable.check(action, tmp_path, transcript=str(transcript))
    assert allowed.result.decision == "allow", allowed.result.reason
    assert allowed.result.preservation == "verified" and len(allowed.transactions) == 1


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


@pytest.mark.parametrize("host,role", [("codex", "pre-tool"), ("claude", "protect-mcp")])
def test_named_host_entrypoints_apply_local_restrictions_before_permission_and_keep_call_identity(tmp_path, host, role):
    import subprocess
    from modding_harness.telemetry import Telemetry
    register(tmp_path, "def decide(payload): return {'kind':'allow'}\n")
    saves = tmp_path / "config/Saves"
    saves.mkdir(parents=True)
    (tmp_path / "skyrim-paths.env").write_text("SKYRIM_CONFIG_DIR='" + saves.parent.as_posix() + "'\n")
    payload = {"hook_event_name": "PreToolUse", "tool_name": "mcp__devbench__game",
               "tool_input": {"action": "save", "name": "new-fixture"}, "cwd": str(tmp_path),
               "session_id": "s", "tool_use_id": "c", "host_version": "fixture-declared-version"}
    command = [sys.executable, "-B", str(REPO / "tools/skyguard/run.py"), host, role,
               "--project", str(tmp_path), "--root", str(tmp_path)]
    completed = subprocess.run(command, input=json.dumps(payload), capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    body = completed.stdout.removeprefix("SKYGUARD-OK\n")
    result = json.loads(body)["hookSpecificOutput"]
    assert result["permissionDecision"] == "deny" and "runtime save/load" in result["permissionDecisionReason"]
    assert not (tmp_path / ".modding-agent-harness/backups/saves").exists()
    report = Telemetry(tmp_path).report("s")
    assert report["receipts"] == 1 and report["uncorrelated_receipts"] == 0
    assert report["caller_declarations"][0]["version"] == "fixture-declared-version"
