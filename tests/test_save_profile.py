"""Legacy backup names retain a complete pair in the neutral store."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

from conftest import REPO
sys.path.insert(0, str(REPO / "tools"))
from modding_harness import approval, contracts, dispatch, profiles, workspace
from modding_harness.preservation import SaveStore
from skyguard import local_policy, portable, save_profile, save_store
from test_local_policy import register


def _qualify(monkeypatch, script):
    """Model the reviewed source: add the fixture's hash to the qualified set. Since 783c5b0 every check reads
    QUALIFIED_POLICY_SHA256 (built once at import), so patching TRANSFER_POLICY_SHA256 alone reaches nothing
    (CI run 39145224156)."""
    monkeypatch.setattr(local_policy, "QUALIFIED_POLICY_SHA256",
                        local_policy.QUALIFIED_POLICY_SHA256 | {hashlib.sha256(script.read_bytes()).hexdigest()})


@pytest.mark.parametrize("suffix", [".bak", ".BAK", ".old.1", ".snapshot-1", ".bäckup"])
def test_backup_suffix_stays_attached_to_both_logical_members(tmp_path, suffix):
    ess, skse = tmp_path / ("Source.ess" + suffix), tmp_path / ("Source.skse" + suffix)
    ess.write_bytes(b"ess")
    skse.write_bytes(b"skse")
    profile = save_profile.skyrim()
    assert profile.members(ess) == (ess, skse)
    assert profile.members(skse) == (ess, skse)
    with pytest.raises(ValueError):
        profiles.skyrim().members(ess)


def test_regular_save_membership_is_unchanged_and_unrelated_suffixes_stay_separate(tmp_path):
    ess = tmp_path / "Source.ess"
    ess.write_bytes(b"ess")
    assert save_profile.skyrim().members(ess) == profiles.skyrim().members(ess)
    backup = tmp_path / "Source.ess.bak"
    backup.write_bytes(b"backup")
    other = tmp_path / "Source.skse.other"
    other.write_bytes(b"different backup")
    assert save_profile.skyrim().members(backup) == (backup, tmp_path / "Source.skse.bak")
    with pytest.raises(ValueError):
        save_profile.skyrim().members(tmp_path / "unrelated.txt")


@pytest.mark.parametrize("cosave", [False, True])
def test_qualified_native_backup_edit_preserves_and_restores_complete_pair(tmp_path, monkeypatch, cosave):
    saves = tmp_path / "profile/Saves"
    saves.mkdir(parents=True)
    (tmp_path / "skyrim-paths.env").write_text("SKYRIM_CONFIG_DIR='" + saves.parent.as_posix() + "'\n")
    ess, skse = saves / "Source.ess.bak", saves / "Source.skse.bak"
    ess.write_bytes(b"original ESS backup")
    if cosave:
        skse.write_bytes(b"original SKSE backup")
    original = {path.name: path.read_bytes() for path in saves.iterdir()}
    script = register(tmp_path, "def decide(payload): return {'kind':'backup','op':'write','affected':[payload['tool_input']['file_path']],'count':1}\n")
    # The public fixture models the reviewed source; real installed-source
    # equivalence is measured separately without shipping private policy code.
    _qualify(monkeypatch, script)
    action = contracts.Action("future", "edit", "files", cwd=str(tmp_path), paths=(str(ess),), operations=("write",))
    outcome, _ = portable.check(action, tmp_path)
    assert outcome.result.decision == "allow" and outcome.result.preservation == "verified", outcome
    assert len(outcome.transactions) == 1
    manifest = save_store.store(tmp_path).load(outcome.transactions[0])
    assert len(manifest["groups"]) == 1 and len(manifest["groups"][0]["members"]) == 2
    assert sum(row["missing"] for row in manifest["groups"][0]["members"]) == (0 if cosave else 1)
    restored = tmp_path / "restored"
    save_store.restore_result(tmp_path, outcome.transactions[0], (skse.name,), restored)
    assert {path.name: path.read_bytes() for path in restored.rglob("*") if path.is_file()} == original
    ess.write_bytes(b"later ESS backup")
    if cosave:
        skse.write_bytes(b"later SKSE backup")
    result = save_store.restore_result(tmp_path, outcome.transactions[0], in_place=True)
    assert result["pre_restore_transactions"]
    assert {path.name: path.read_bytes() for path in saves.iterdir()} == original


def test_unqualified_policy_cannot_enable_additional_backup_membership(tmp_path):
    save = tmp_path / "profile/Saves/Source.ess.bak"
    save.parent.mkdir(parents=True)
    save.write_bytes(b"unchanged")
    (tmp_path / "skyrim-paths.env").write_text("SKYRIM_CONFIG_DIR='" + save.parent.parent.as_posix() + "'\n")
    register(tmp_path, "def decide(payload): return {'kind':'backup','op':'write','affected':[payload['tool_input']['file_path']],'count':1}\n")
    outcome, _ = portable.check(contracts.Action("future", "edit", "files", cwd=str(tmp_path),
                                paths=(str(save),), operations=("write",)), tmp_path)
    assert outcome.result.decision == "deny" and not outcome.transactions
    assert save.read_bytes() == b"unchanged"


def test_backup_supplement_keeps_unresolved_game_scope_and_the_total_deadline(tmp_path, monkeypatch):
    import time
    save = tmp_path / "Source.ess.bak"
    save.write_bytes(b"original")
    script = register(tmp_path, "def decide(payload): return {'kind':'allow'}\n")
    _qualify(monkeypatch, script)
    action = contracts.Action("future", "edit", "files", paths=(str(save),), operations=("write",))
    unresolved = dispatch.SavePlan(unresolved="unknown original game scope")
    assert local_policy.supplement_native_backups(action, tmp_path, unresolved, save_profile.skyrim()) is unresolved
    with pytest.raises(TimeoutError):
        local_policy.supplement_native_backups(action, tmp_path, dispatch.SavePlan(), save_profile.skyrim(),
                                               deadline=time.monotonic() - 1)


def test_policy_change_after_backup_refuses_permission_and_keeps_verified_backup(tmp_path, monkeypatch):
    save = tmp_path / "profile/Saves/Source.ess.bak"
    save.parent.mkdir(parents=True)
    save.write_bytes(b"original")
    (tmp_path / "skyrim-paths.env").write_text("SKYRIM_CONFIG_DIR='" + save.parent.parent.as_posix() + "'\n")
    script = register(tmp_path, "def decide(payload): return {'kind':'backup','op':'write','affected':[payload['tool_input']['file_path']],'count':1}\n")
    _qualify(monkeypatch, script)
    backup = SaveStore.backup
    def changed_after_backup(store, *args, **kwargs):
        manifest = backup(store, *args, **kwargs)
        script.write_text("def decide(payload): return {'kind':'deny'}\n")
        return manifest
    monkeypatch.setattr(SaveStore, "backup", changed_after_backup)
    outcome, _ = portable.check(contracts.Action("future", "edit", "files", cwd=str(tmp_path),
                                paths=(str(save),), operations=("write",)), tmp_path)
    assert outcome.result.decision == "deny" and not outcome.transactions
    assert "policy" in outcome.result.reason
    store = save_store.store(tmp_path)
    manifests, unreadable = workspace.summaries(store)
    assert len(manifests) == 1 and not unreadable
    verified = store.load(manifests[0]["id"])
    assert verified["groups"][0]["members"][0]["sha256"] == hashlib.sha256(b"original").hexdigest()
    assert save.read_bytes() == b"original"


def test_bulk_backup_helper_requires_command_bound_human_scope(tmp_path, monkeypatch):
    saves = tmp_path / "profile/Saves"
    saves.mkdir(parents=True)
    (tmp_path / "skyrim-paths.env").write_text("SKYRIM_CONFIG_DIR='" + saves.parent.as_posix() + "'\n")
    files = [saves / (str(index) + ".ess.bak") for index in range(3)]
    for path in files:
        path.write_bytes(b"original")
    script = register(tmp_path, "def decide(payload): return {'kind':'backup','op':'write','affected':[payload['tool_input']['file_path']],'count':1}\n")
    _qualify(monkeypatch, script)
    action = contracts.Action("claude", "save-restore", "files", cwd=str(tmp_path), session_id="s",
                              command="restore explicitly selected backup copies", paths=tuple(map(str, files)), operations=("write",) * 3)
    members = [path for save in files for path in save_profile.skyrim().members(save)]
    transcript = tmp_path / "human.jsonl"
    def grant(bound):
        text = approval.example("s", "write", members, action.command, bound, 3)
        transcript.write_text(json.dumps({"type": "user", "origin": {"kind": "human"},
                                         "message": {"role": "user", "content": text}}))
    grant(False)
    denied, _ = portable.check(action, tmp_path, transcript=str(transcript), helper=True)
    assert denied.result.decision == "deny" and not denied.transactions
    grant(True)
    allowed, _ = portable.check(action, tmp_path, transcript=str(transcript), helper=True)
    assert allowed.result.decision == "allow" and allowed.result.preservation == "verified", allowed
    assert len(allowed.transactions) == 1
