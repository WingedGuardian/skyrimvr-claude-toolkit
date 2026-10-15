"""Finite legacy requirements supplement rather than replace game policy."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

from conftest import REPO
sys.path.insert(0, str(REPO / "tools"))
from modding_harness import contracts, workspace
from skyguard import save_store
from test_controlled_policy import controlled
from test_local_policy import register


@pytest.fixture
def finite(controlled):
    root, source, _test, _helper, _request, policy, run = controlled
    backup = source.with_name("Source.ess.bak")
    backup.write_bytes(b"backup ESS")
    backup.with_name("Source.skse.bak").write_bytes(b"backup SKSE")
    old_hash = hashlib.sha256(policy.read_bytes()).hexdigest()
    decision = root / "finite.json"
    register(root, "import json\nfrom pathlib import Path\n"
             "def decide(payload): return json.loads((Path(__file__).parents[2]/'finite.json').read_text())\n"
             "def transfer_form(text,args): return {}\n"
             "def resolve_transfer(form):\n"
             " data=json.loads((Path(__file__).parents[2]/'finite.json').read_text())\n"
             " return ([data['affected'][0]],data['affected'][1:])\n"
             "def is_save(path): return True\n")
    module = root / "tools/skyguard/local_policy.py"
    module.write_text(module.read_text().replace(old_hash, hashlib.sha256(policy.read_bytes()).hexdigest()))
    return root, source, backup, decision, run


@pytest.mark.parametrize("shape", ["suffix-write", "restore", "suffix-move"])
def test_finite_legacy_scope_preserves_all_required_members_once(finite, shape):
    root, source, backup, decision, run = finite
    path = source if shape == "restore" else backup
    operation = {"suffix-delete": "delete", "suffix-write": "write", "restore": "restore", "suffix-move": "move"}[shape]
    affected = [str(path)]
    command = ('python "' + (root / '.claude/hooks/restore-save-backup.py').as_posix() + '" restore fixture' if shape == "restore" else
               'rm "' + path.as_posix() + '"' if shape == "suffix-delete" else
               'Set-Content -LiteralPath "' + path.as_posix() + '" -Value fixture')
    if shape == "suffix-move":
        destination = path.with_name("Destination.ess.bak")
        destination.write_bytes(b"destination ESS")
        destination.with_name("Destination.skse.bak").write_bytes(b"destination SKSE")
        affected.append(str(destination))
        command = 'mv "' + path.as_posix() + '" "' + destination.as_posix() + '"'
    decision.write_text(json.dumps({"kind": "backup", "op": operation, "affected": affected, "count": len(affected)}))
    before = {path: path.read_bytes() for path in source.parent.iterdir()}
    action = contracts.Action("future", "PowerShell" if shape == "suffix-write" else "Bash", "shell", cwd=str(root),
                              shell="powershell" if shape == "suffix-write" else "bash", command=command)
    outcome = run(action=action)
    assert outcome["result"]["decision"] == "allow", outcome
    assert outcome["result"]["preservation"] == "verified" and len(outcome["transactions"]) == 1
    manifest = save_store.store(root).load(outcome["transactions"][0])
    assert len(manifest["groups"]) == len(affected)
    assert sum(len(group["members"]) for group in manifest["groups"]) == 2 * len(affected)
    assert {path: path.read_bytes() for path in before} == before


def test_finite_requirement_change_after_backup_refuses_and_retains_original_bytes(finite):
    root, source, backup, decision, run = finite
    decision.write_text(json.dumps({"kind": "backup", "op": "write", "affected": [str(backup)], "count": 1}))
    driver = root / "driver.py"
    text = driver.read_text().replace("'grant':'human.jsonl'", "'grant':'finite.json'")
    driver.write_text(text)
    outcome = run(action=contracts.Action("future", "Bash", "shell", cwd=str(root), shell="bash",
                  command='python restore-helper.py'), mode="grant")
    assert outcome["result"]["decision"] == "deny" and not outcome["transactions"]
    rows, unreadable = workspace.summaries(save_store.store(root))
    assert len(rows) == 1 and not unreadable
    assert save_store.store(root).load(rows[0]["id"])["groups"]
    assert backup.read_bytes() == b"backup ESS"


@pytest.mark.parametrize("operation", ["delete", "move-out"])
def test_finite_preservation_scope_cannot_relax_game_configuration_delete_policy(finite, operation):
    root, source, backup, decision, run = finite
    decision.write_text(json.dumps({"kind": "backup", "op": "delete", "affected": [str(backup)], "count": 1}))
    command = ('rm "' + backup.as_posix() + '"' if operation == "delete" else
               'mv "' + backup.as_posix() + '" "' + (root / 'export.ess.bak').as_posix() + '"')
    outcome = run(action=contracts.Action("future", "Bash", "shell", cwd=str(root), shell="bash", command=command))
    assert outcome["result"]["decision"] == "deny" and "settings folder" in outcome["result"]["reason"]
    assert not outcome["transactions"] and not save_store.rows(root)[0]
    assert source.read_bytes() == b"source ESS" and backup.read_bytes() == b"backup ESS"


def test_unqualified_policy_cannot_add_finite_shell_scope(finite):
    root, _source, backup, decision, run = finite
    decision.write_text(json.dumps({"kind": "backup", "op": "write", "affected": [str(backup)], "count": 1}))
    policy = root / '.claude/hooks/protect-save-access.py'
    register(root, policy.read_text() + '\n')
    outcome = run(action=contracts.Action("future", "PowerShell", "shell", cwd=str(root), shell="powershell",
                    command='Set-Content -LiteralPath "' + backup.as_posix() + '" -Value fixture'))
    assert outcome["result"]["decision"] == "deny" and not save_store.rows(root)[0]
