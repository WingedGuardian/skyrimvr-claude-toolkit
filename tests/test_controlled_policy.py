"""Controlled scope bridge using an inert controller and real worker processes."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from conftest import REPO
sys.path.insert(0, str(REPO / "tools"))
from modding_harness import contracts, dispatch, workspace
from skyguard import local_policy, save_store
from test_local_policy import register


@pytest.fixture
def controlled(tmp_path):
    for name in ("skyguard", "modding_harness"):
        shutil.copytree(REPO / "tools" / name, tmp_path / "tools" / name,
                        ignore=shutil.ignore_patterns("__pycache__"))
    saves = tmp_path / "profile/Saves"
    saves.mkdir(parents=True)
    source = saves / "Source.ess"
    source.write_bytes(b"source ESS")
    source.with_suffix(".skse").write_bytes(b"source SKSE")
    test = saves / "ShinsoObserver_Headless_Fixture.ess"
    (tmp_path / "skyrim-paths.env").write_text("SKYRIM_CONFIG_DIR='" + saves.parent.as_posix() + "'\n")
    helper = tmp_path / ".claude/scripts/guarded-runtime-load.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("raise RuntimeError('fixture controller must never execute')\n")
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"schema": 1, "source": str(source), "test": str(test)}))
    policy = register(tmp_path,
        "import json\nfrom pathlib import Path\n"
        "def decide(payload):\n"
        " text=payload['tool_input']['command']\n"
        " words=text.split(' --request ')\n"
        " data=json.loads(Path(words[1].strip(chr(34))).read_text())\n"
        " op='controlled-load' if ' load ' in text else 'controlled-prepare'\n"
        " targets=[data['test']] if op=='controlled-load' else [data['source'],data['test']]\n"
        " return {'kind':'backup','op':op,'affected':sorted(targets,key=str.casefold),'count':1}\n"
        "if __name__=='__main__': raise RuntimeError('legacy main must never execute')\n")
    module = tmp_path / "tools/skyguard/local_policy.py"
    text = module.read_text().replace(local_policy.TRANSFER_POLICY_SHA256, hashlib.sha256(policy.read_bytes()).hexdigest())
    text = text.replace("22e0b8b1c004bc4ee79946836a6ce0abb60c4ca9b06ded20b8e659e61dc8102b",
                        hashlib.sha256(helper.read_bytes()).hexdigest())
    module.write_text(text)
    driver = tmp_path / "driver.py"
    driver.write_text('''import json, sys, time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'tools'))
from modding_harness import contracts
from modding_harness.preservation import SaveStore
from skyguard import portable
root=Path(__file__).parent
mode=sys.argv[1]
if mode!='none':
 original=SaveStore.backup
 def changed(store,*args,**kwargs):
  result=original(store,*args,**kwargs)
  target=root/({'request':'request.json','controller':'.claude/scripts/guarded-runtime-load.py',
                'policy':'.claude/hooks/protect-save-access.py'}[mode])
  target.write_bytes(target.read_bytes()+b' ')
  return result
 SaveStore.backup=changed
outcome,count=portable.check(contracts.Action(**json.load(sys.stdin)),root,deadline=time.monotonic()+20)
print(json.dumps(portable.reply(outcome,count)))
''')
    def run(operation="prepare", *, mode="none", action=None, shell="bash"):
        command = 'python "' + helper.as_posix() + '" ' + operation + ' --request "' + request.as_posix() + '"'
        action = action or contracts.Action("future", "PowerShell" if shell == "powershell" else "Bash", "shell",
                                             cwd=str(tmp_path), shell=shell, command=command)
        completed = subprocess.run([sys.executable, "-B", str(driver), mode], input=json.dumps(asdict(action)),
                                   capture_output=True, text=True, timeout=30)
        assert completed.returncode == 0, completed.stderr
        return json.loads(completed.stdout)
    return tmp_path, source, test, helper, request, policy, run


@pytest.mark.parametrize("operation", ["prepare", "load"])
@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_qualified_controlled_scope_preserves_exact_pair_without_executing_controller(controlled, operation, shell):
    root, source, test, helper, request, policy, run = controlled
    if operation == "load":
        test.write_bytes(b"test ESS")
        test.with_suffix(".skse").write_bytes(b"test SKSE")
    before = {path: path.read_bytes() for path in (source, source.with_suffix(".skse"), helper, request, policy)}
    outcome = run(operation, shell=shell)
    assert outcome["result"]["decision"] == "allow", outcome
    assert outcome["result"]["preservation"] == "verified" and len(outcome["transactions"]) == 1
    manifest = save_store.store(root).load(outcome["transactions"][0])
    members = manifest["groups"][0]["members"]
    expected = source if operation == "prepare" else test
    assert len(manifest["groups"]) == 1 and len(members) == 2
    assert {Path(row["path"]).name for row in members} == {expected.name, expected.with_suffix(".skse").name}
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("mode", ["request", "controller", "policy"])
def test_controlled_dependencies_changed_after_backup_refuse_permission_and_retain_backup(controlled, mode):
    root, source, _test, _helper, _request, _policy, run = controlled
    outcome = run(mode=mode)
    assert outcome["result"]["decision"] == "deny" and not outcome["transactions"]
    rows, unreadable = workspace.summaries(save_store.store(root))
    assert len(rows) == 1 and not unreadable
    assert save_store.store(root).load(rows[0]["id"])["groups"]
    assert source.read_bytes() == b"source ESS"


@pytest.mark.parametrize("fault", ["controller", "policy", "schema", "extra-argument", "different-controller"])
def test_unqualified_controlled_requests_write_no_backup(controlled, fault):
    root, _source, _test, helper, request, policy, run = controlled
    action = None
    if fault in ("controller", "policy"):
        path = helper if fault == "controller" else policy
        path.write_bytes(path.read_bytes() + b" ")
    elif fault == "schema":
        data = json.loads(request.read_text())
        data["schema"] = True
        request.write_text(json.dumps(data))
    else:
        chosen = helper if fault == "extra-argument" else root / "guarded-runtime-load.py"
        if fault == "different-controller":
            chosen.write_bytes(helper.read_bytes())
        command = 'python "' + chosen.as_posix() + '" prepare --request "' + request.as_posix() + '"'
        if fault == "extra-argument":
            command += " --extra"
        action = contracts.Action("future", "Bash", "shell", cwd=str(root), shell="bash", command=command)
    outcome = run(action=action)
    assert outcome["result"]["decision"] == "deny" and not outcome["transactions"]
    assert not save_store.rows(root)[0]


def test_unqualified_policy_cannot_relabel_controlled_operation(tmp_path, monkeypatch):
    source = tmp_path / "Source.ess"
    source.write_bytes(b"unchanged")
    decision = {"kind": "backup", "op": "controlled-load", "affected": [str(source)], "count": 1}
    register(tmp_path, "def decide(payload): return " + repr(decision) + "\n")
    monkeypatch.setattr(local_policy, "_controlled_scope", lambda *args: ("controlled-load", (str(source),), ()))
    plan = dispatch.SavePlan((dispatch.Mutation(str(source), "write"),), helper=True, recheck=lambda: ())
    result = local_policy.evaluate(contracts.Action("future", "Bash", "shell", shell="bash"), tmp_path,
                                   local_policy.plan_facts(plan))
    assert result.decision == "deny" and "not been qualified" in result.reason
