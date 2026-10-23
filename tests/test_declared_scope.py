"""Unknown helper scope comes only from canonical qualified human evidence."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys

import pytest

from conftest import REPO
sys.path.insert(0, str(REPO / "tools"))
from modding_harness import approval, contracts, dispatch, workspace
from skyguard import local_policy, save_profile, save_store
from test_controlled_policy import controlled
from test_local_policy import register


@pytest.fixture
def declared(controlled):
    root, source, _test, _helper, _request, policy, run = controlled
    old_hash = hashlib.sha256(policy.read_bytes()).hexdigest()
    register(root, "def decide(payload): return {'kind':'needs_set','op':'write','set':None}\n"
                   "if __name__=='__main__': raise RuntimeError('legacy backup main must never execute')\n")
    module = root / "tools/skyguard/local_policy.py"
    module.write_text(module.read_text().replace(old_hash, hashlib.sha256(policy.read_bytes()).hexdigest()))
    (root / "mutate.py").write_text("from pathlib import Path\nPath('Source.ess').write_bytes(b'change')\n")
    action = contracts.Action("claude", "Bash", "shell", cwd=str(root), shell="bash", session_id="scope",
                              command="python mutate.py")
    members = save_profile.skyrim().members(source)
    def grant(*, host="claude", text=None, origin="human"):
        text = text or approval.example("scope", "write", members, action.command, True, 1)
        rows = ([{"type": "session_meta", "payload": {"originator": "codex-tui", "id": "scope"}},
                 {"type": "event_msg", "payload": {"type": "item_completed", "item":
                    {"type": "UserMessage", "content": [{"type": "text", "text": text}]}}}] if host == "codex" else
                [{"type": "user", "origin": {"kind": origin}, "message": {"role": "user", "content": text}}])
        (root / "human.jsonl").write_text("\n".join(map(json.dumps, rows)) + "\n")
    return root, source, action, members, grant, run


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_declared_unknown_helper_requires_and_retains_one_complete_human_scope(declared, host):
    root, source, action, members, grant, run = declared
    action = replace(action, host=host)
    denied = run(action=action)
    assert denied["result"]["decision"] == "deny" and not denied["transactions"]
    grant(host=host)
    before = {path: path.read_bytes() for path in members}
    allowed = run(action=action)
    assert allowed["result"]["decision"] == "allow", allowed
    assert allowed["result"]["preservation"] == "verified" and len(allowed["transactions"]) == 1
    manifest = save_store.store(root).load(allowed["transactions"][0])
    assert len(manifest["groups"]) == 1 and len(manifest["groups"][0]["members"]) == 2
    assert {path: path.read_bytes() for path in members} == before


@pytest.mark.parametrize("fault", ["agent-origin", "unknown-host", "unbound", "short-hash", "command", "session",
                                  "missing-counterpart", "count", "boolean-count", "non-save", "forged-provenance"])
def test_declared_helper_rejects_unqualified_or_incomplete_scope_before_backup(declared, fault):
    root, source, action, members, grant, run = declared
    text = approval.example("scope", "write", members, action.command, True, 1)
    data = json.loads(text.split(" ", 1)[1])
    if fault == "agent-origin":
        grant(origin="agent")
    elif fault == "forged-provenance":
        action = replace(action, provenance={"declared_members": list(map(str, members)),
            "save_plan": {"mutations": [{"path": str(source), "operation": "write"}], "wildcard": True,
                          "helper": True, "recheck": True, "declared_members": list(map(str, members))}})
    else:
        if fault == "unknown-host":
            action = replace(action, host="future")
        elif fault == "unbound":
            data.pop("command_sha256")
        elif fault == "short-hash":
            data["command_sha256"] = data["command_sha256"][:16]
        elif fault == "command":
            data["command_sha256"] = hashlib.sha256(b"different command").hexdigest()
        elif fault == "session":
            data["session_id"] = "other"
        elif fault == "missing-counterpart":
            data["paths"] = data["paths"][:1]
        elif fault == "count":
            data["count"] = 2
        elif fault == "boolean-count":
            data["count"] = True
        elif fault == "non-save":
            data["paths"] = [str(root / "notes.txt")]
        grant(text=approval.KEY + " " + json.dumps(data))
    outcome = run(action=action)
    assert outcome["result"]["decision"] == "deny" and not outcome["transactions"], outcome
    assert not save_store.rows(root)[0] and source.read_bytes() == b"source ESS"


def test_declared_helper_rechecks_human_evidence_after_preserving_bytes(declared):
    root, source, action, _members, grant, run = declared
    grant()
    outcome = run(action=action, mode="grant")
    assert outcome["result"]["decision"] == "deny" and not outcome["transactions"]
    rows, unreadable = workspace.summaries(save_store.store(root))
    assert len(rows) == 1 and not unreadable
    assert save_store.store(root).load(rows[0]["id"])["groups"]
    assert source.read_bytes() == b"source ESS"


def test_different_complete_declarations_for_the_same_opaque_command_are_ambiguous(declared):
    root, source, action, members, grant, run = declared
    other = source.with_name("Other.ess")
    other.write_bytes(b"other ESS")
    grant()
    text = approval.example("scope", "write", save_profile.skyrim().members(other), action.command, True, 1)
    with (root / "human.jsonl").open("a") as stream:
        stream.write(json.dumps({"type": "user", "origin": {"kind": "human"},
                                "message": {"role": "user", "content": text}}) + "\n")
    outcome = run(action=action)
    assert outcome["result"]["decision"] == "deny" and not save_store.rows(root)[0]
    assert source.read_bytes() == b"source ESS" and other.read_bytes() == b"other ESS"


def test_declared_scope_preserves_two_pairs_with_an_explicit_missing_member(declared):
    root, source, action, members, grant, run = declared
    other = source.with_name("Other.ess")
    other.write_bytes(b"other ESS")
    all_members = (*members, *save_profile.skyrim().members(other))
    grant(text=approval.example("scope", "write", all_members, action.command, True, 2))
    outcome = run(action=action)
    assert outcome["result"]["decision"] == "allow", outcome
    manifest = save_store.store(root).load(outcome["transactions"][0])
    assert len(manifest["groups"]) == 2
    assert sum(row["missing"] for group in manifest["groups"] for row in group["members"]) == 1
    assert not other.with_suffix(".skse").exists()


def test_unqualified_policy_cannot_use_a_declared_game_plan(tmp_path):
    source = tmp_path / "Source.ess"
    source.write_bytes(b"unchanged")
    register(tmp_path, "def decide(payload): return {'kind':'needs_set','op':'write','set':None}\n")
    plan = local_policy.DeclaredSavePlan((dispatch.Mutation(str(source), "write"),), wildcard=True, helper=True,
             recheck=lambda: (), declared_members=tuple(map(str, save_profile.skyrim().members(source))))
    result = local_policy.evaluate(contracts.Action("future", "Bash", "shell", shell="bash"), tmp_path,
                                   local_policy.plan_facts(plan))
    assert result.decision == "deny" and "membership is unresolved" in result.reason


def test_unbound_grant_cannot_create_a_declared_plan(declared):
    _root, _source, action, members, grant, _run = declared
    grant(text=approval.example("scope", "write", members))
    assert local_policy._declared_choice(action, str(_root / "human.jsonl"), "write", save_profile.skyrim()) is None
