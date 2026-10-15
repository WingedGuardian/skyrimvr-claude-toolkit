import json
from pathlib import Path
import sys

import pytest

from conftest import REPO
sys.path.insert(0, str(REPO / "tools"))
from modding_harness import approval, contracts
from skyguard import portable, save_store
from skyguard.adapters import opencode


@pytest.fixture
def install(tmp_path):
    root = tmp_path / "toolkit"
    root.mkdir()
    saves = tmp_path / "config/Saves"
    saves.mkdir(parents=True)
    (root / "skyrim-paths.env").write_text("SKYRIM_CONFIG_DIR='" + saves.parent.as_posix() + "'\n")
    files = []
    for index in range(3):
        pair = tuple(saves / (str(index) + extension) for extension in (".ess", ".skse"))
        for member in pair:
            member.write_bytes(member.name.encode())
        files.append(pair)
    return root, files


def action(root, files, host="future-agent"):
    return contracts.Action(host, "edit", "files", cwd=str(root), session_id="s", tool_call_id="c",
                            paths=tuple(str(path) for path in files), operations=("write",) * len(files))


def test_pair_is_one_group_and_local_refusal_precedes_preservation(install):
    root, files = install
    request = action(root, files[0])
    denied, _ = portable.check(request, root, local_restrictions=(
        lambda request: contracts.Result("deny", "evaluated", reason="installed local runtime policy"),))
    assert denied.result.reason == "installed local runtime policy" and not denied.transactions
    assert not (root / ".modding-agent-harness/backups").exists()
    outcome, _ = portable.check(request, root)
    assert outcome.result.decision == "allow" and outcome.result.preservation == "verified"
    assert len(outcome.transactions) == 1
    transaction = save_store.store(root).load(outcome.transactions[0])
    assert len(transaction["groups"]) == 1
    assert {Path(member["path"]).name for member in transaction["groups"][0]["members"]} == {"0.ess", "0.skse"}


@pytest.mark.parametrize('surface', ['files', 'mcp'])
def test_concrete_file_and_api_save_targets_are_literal_with_bracketed_paths(tmp_path, surface):
    root = tmp_path / 'game [VR]'
    root.mkdir()
    config = root / 'profile [one]'
    saves = config / 'Saves'
    saves.mkdir(parents=True)
    (root / 'skyrim-paths.env').write_text("SKYRIM_CONFIG_DIR='" + config.as_posix() + "'\n")
    stem = 'Save[1]' if surface == 'files' else 'Save1'
    pair = [saves / (stem + ext) for ext in ('.ess', '.skse')]
    for member in pair:
        member.write_bytes(member.name.encode())
    request = action(root, pair[:1]) if surface == 'files' else contracts.Action(
        'future-agent', 'mcp__devbench-vr__game', 'mcp', cwd=str(root), session_id='s',
        provenance={'arguments': {'action': 'save', 'name': stem}})
    outcome, _ = portable.check(request, root)
    assert outcome.result.decision == 'allow', outcome.result.reason
    assert outcome.result.preservation == 'verified' and len(outcome.transactions) == 1
    record = save_store.store(root).load(outcome.transactions[0])
    assert {Path(m['path']).name for g in record['groups'] for m in g['members']} == {stem + ext for ext in ('.ess', '.skse')}


def test_literal_save_identity_does_not_relax_unqualified_shell_patterns(tmp_path):
    from skyguard import saves
    folder = tmp_path / 'profile [one]' / 'Saves'
    folder.mkdir(parents=True)
    target = folder / 'Save[1].ess'
    target.write_bytes(b'fixture')
    shell = saves.count(saves.Change((saves.Target(str(target), 'write'),)))
    assert shell.unknown and not shell.pairs
    native = saves.count(saves.Change((saves.Target(str(target), 'write', True),)))
    assert not native.unknown and not native.patterns and len(native.pairs) == 1


def test_generic_upgrade_stage_keeps_private_install_bytes_and_refuses_bundle_reuse(tmp_path):
    import hashlib
    import subprocess
    installed, source, bundle = tmp_path / "installed", tmp_path / "source", tmp_path / "review"
    installed.mkdir()
    source.mkdir()
    (installed / "AGENTS.md").write_bytes(b"private instructions")
    (source / "AGENTS.md").write_bytes(b"candidate instructions")
    inventory = tmp_path / "candidate.json"
    inventory.write_text(json.dumps({"v": 1, "files": {"AGENTS.md": hashlib.sha256(b"candidate instructions").hexdigest()}}))
    args = [sys.executable, "-B", str(REPO / "tools/agent-harness.py"), "upgrade-stage", "--installed-root", str(installed),
            "--source", str(source), "--candidate", str(inventory), "--destination", str(bundle)]
    first = subprocess.run(args, capture_output=True, timeout=45)
    assert first.returncode == 0, first.stderr
    result = json.loads(first.stdout)
    assert result["complete"] and not result["application_authorized"] and result["collisions"] == 1
    assert (installed / "AGENTS.md").read_bytes() == b"private instructions"
    assert (bundle / "rollback/AGENTS.md").read_bytes() == b"private instructions"
    assert subprocess.run(args, capture_output=True, timeout=45).returncode == 2
    choices = tmp_path / 'choices.json'
    choices.write_text(json.dumps({'AGENTS.md': 'candidate'}))
    review_sha = hashlib.sha256((bundle / 'complete.json').read_bytes()).hexdigest()
    applying = [sys.executable, '-B', str(REPO / 'tools/agent-harness.py'), 'upgrade-apply',
                '--installed-root', str(installed), '--bundle', str(bundle), '--review-sha256', review_sha,
                '--choices', str(choices), '--quiescent']
    applied = subprocess.run(applying, capture_output=True, timeout=45)
    assert applied.returncode == 0, applied.stdout
    receipt = json.loads(applied.stdout)
    assert receipt['host_review_required'] and (installed / 'AGENTS.md').read_bytes() == b'candidate instructions'
    rolled = subprocess.run([sys.executable, '-B', str(REPO / 'tools/agent-harness.py'), 'upgrade-rollback',
        receipt['operation'], '--installed-root', str(installed), '--quiescent'], capture_output=True, timeout=45)
    assert rolled.returncode == 0, rolled.stdout
    assert (installed / 'AGENTS.md').read_bytes() == b'private instructions'


@pytest.mark.parametrize("in_place", [False, True])
def test_neutral_restore_carries_one_deadline_through_every_copy(install, tmp_path, monkeypatch, in_place):
    from modding_harness import durable
    root, files = install
    transaction = save_store.store(root).backup([files[0]])["id"]
    for member in files[0]:
        member.write_bytes(b"current")
    real, seen = durable.copy_verified, []
    def observe(source, target, deadline=None):
        seen.append(deadline)
        return real(source, target, deadline)
    monkeypatch.setattr(durable, "copy_verified", observe)
    result = save_store.restore_result(root, transaction, to=None if in_place else tmp_path / "out", in_place=in_place)
    assert len(result["published"]) == 2
    assert seen and all(deadline is not None for deadline in seen)
    assert len(set(seen)) == 1, "pre-preservation and restore must share one total budget"


def test_bulk_is_one_scope_and_only_qualified_human_evidence_can_grant_it(install):
    root, files = install
    all_members = [path for pair in files for path in pair]
    request = action(root, all_members)
    outcome, _ = portable.check(request, root)
    assert outcome.result.decision == "deny" and not outcome.transactions
    transcript = root / "human.jsonl"
    text = approval.example("s", "write", all_members)
    transcript.write_text(json.dumps({"type": "user", "origin": {"kind": "human"},
                                      "message": {"role": "user", "content": text}}))
    outcome, _ = portable.check(request, root, transcript=str(transcript))
    assert outcome.result.decision == "deny"  # An unspecified host has no invented reader.
    outcome, _ = portable.check(action(root, all_members, "claude"), root, transcript=str(transcript))
    assert outcome.result.preservation == "verified", outcome
    assert len(save_store.store(root).load(outcome.transactions[0])["groups"]) == 3


def test_unknown_records_each_gap_and_file_plugin_rule_still_denies(install):
    root, files = install
    request = contracts.Action("future-agent", "future-MCP-code-mode", "unknown", session_id="s")
    outcome, count = portable.check(request, root)
    assert (outcome.result.decision, outcome.result.evaluation, count) == ("allow", "unguarded", 1)
    assert "WARN AND ALLOW" in outcome.result.warnings[0]
    _, count = portable.check(request, root)
    assert count == 2
    outcome, _ = portable.check(action(root, [root / "plugin.esp"]), root)
    assert outcome.result.decision == "deny" and not outcome.transactions


def test_opencode_uses_its_native_grammar_and_refuses_bad_known_payload(install):
    root, files = install
    data = {"v": 1, "tool": "edit", "args": {"filePath": str(files[0][0])}, "directory": str(root)}
    result = opencode.handle(json.dumps(data), root)
    assert result["decision"] in ("allow", "advise") and "backup verified" in result["context"]
    data["args"] = {}
    result = opencode.handle(json.dumps(data), root)
    assert result["decision"] == "deny" and result["inert"]
    data.update(tool="apply_patch", args={"patchText": "*** Begin Patch\n*** Add File: harmless.txt\n+x\n  *** Delete File: plugin.esp\n*** End Patch"})
    result = opencode.handle(json.dumps(data), root)
    assert result["decision"] != "deny", result  # OpenCode skips that indented header.


def test_bulk_recovery_uses_one_command_bound_grant_and_preserves_current_pairs(install):
    root, files = install
    target = save_store.store(root)
    transaction = target.backup(files)["id"]
    for pair in files:
        for member in pair:
            member.write_bytes(b"current " + member.name.encode())
    transcript = root / "human.jsonl"
    options = dict(host="claude", session_id="s", transcript=str(transcript), command="restore fixture three pairs")
    refused = save_store.restore_result(root, transaction, in_place=True, **options)
    assert refused["result"]["decision"] == "deny"
    grant = next(line for line in refused["result"]["reason"].splitlines() if line.startswith(approval.KEY + " "))
    scope = json.loads(grant[len(approval.KEY) + 1:])
    assert scope["count"] == 3 and len(scope["paths"]) == 6 and scope["command_sha256"]
    transcript.write_text(json.dumps({"type": "user", "origin": {"kind": "human"},
                                      "message": {"role": "user", "content": grant}}))
    wrong = save_store.restore_result(root, transaction, in_place=True, **dict(options, command="changed helper"))
    assert wrong["result"]["decision"] == "deny"
    assert save_store.restore_result(root, transaction, in_place=True, **dict(options, host="future-agent"))["result"]["decision"] == "deny"
    result = save_store.restore_result(root, transaction, in_place=True, **options)
    assert len(result["published"]) == 6 and result["pre_restore_transactions"]
    previous = target.load(result["pre_restore_transactions"][0])
    import hashlib
    assert {member["sha256"] for group in previous["groups"] for member in group["members"]} == {
        hashlib.sha256(b"current " + member.name.encode()).hexdigest() for pair in files for member in pair}
    assert all(member.read_bytes() == member.name.encode() for pair in files for member in pair)


def test_recovery_selecting_cosave_restores_pair_and_rejects_unexpected_counterpart(install, tmp_path):
    root, files = install
    target = save_store.store(root)
    record = target.backup(files)
    out = tmp_path / "recovered"
    result = save_store.restore_result(root, record["id"], wanted=[str(files[0][1])], to=out)
    assert {Path(path).suffix for path in result["published"]} == {".ess", ".skse"}
    assert len(result["published"]) == 2
    files[0][1].unlink()
    record = target.backup([files[0]])
    files[0][1].write_bytes(b"unexpected counterpart")
    with pytest.raises(OSError, match="counterpart|unexpected"):
        save_store.restore_result(root, record["id"], in_place=True)
    assert files[0][1].read_bytes() == b"unexpected counterpart"


def test_generic_cli_cold_copy_needs_no_named_agent_files(tmp_path):
    import os
    import shutil
    import subprocess
    root = tmp_path / "toolkit"
    for name in ("skyguard", "modding_harness"):
        shutil.copytree(REPO / "tools" / name, root / "tools" / name,
                        ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(REPO / "tools/agent-harness.py", root / "tools/agent-harness.py")
    saves = tmp_path / "config/Saves"
    saves.mkdir(parents=True)
    (root / "skyrim-paths.env").write_text("SKYRIM_CONFIG_DIR='" + saves.parent.as_posix() + "'\n")
    for name in ("save.ess", "save.skse"):
        (saves / name).write_bytes(name.encode())
    assert not any((root / name).exists() for name in (".claude", ".codex", ".opencode", ".agents"))
    def run(*args, data=None):
        completed = subprocess.run([sys.executable, "-B", str(root / "tools/agent-harness.py"), *args],
                                   input=json.dumps(data) if data is not None else None,
                                   capture_output=True, text=True, timeout=45)
        assert completed.returncode == 0, completed.stderr
        return json.loads(completed.stdout)
    assert run("capabilities")["profile"] == "skyrim"
    from dataclasses import asdict
    data = asdict(action(root, [saves / "save.ess"]))
    checked = run("check", data=data)
    assert checked["result"]["preservation"] == "verified", checked
    assert len(run("save-list")["transactions"]) == 1
    restored = run("save-restore", checked["transactions"][0], "--to", str(tmp_path / "out"))
    assert len(restored["published"]) == 2
