"""Owned offline inputs: compact hooks and a read-only, non-authorizing scope report."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from conftest import REPO
from test_save_policy import Inst, live_enforce
from test_local_policy import register
from test_rule_messages import contract_problems

sys.path.insert(0, str(REPO / "tools"))
from modding_harness import contracts
from skyguard import engine, portable, state
from skyguard.verdict import Verdict


def census(root):
    """Reading may change atime; bytes, membership, mtime and mode must not change."""
    return {str(p.relative_to(root)): (p.stat().st_mode, p.stat().st_mtime_ns,
            hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None)
            for p in (root, *root.rglob("*"))}


@pytest.fixture
def installed(tmp_path, monkeypatch):
    inst = Inst(tmp_path)
    for name in ("skyguard", "modding_harness"):
        shutil.copytree(REPO / "tools" / name, inst.game / "tools" / name,
                        ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(REPO / "tools/agent-harness.py", inst.game / "tools/agent-harness.py")
    (inst.game / "skyrim-paths.env").write_text("SKYRIM_CONFIG_DIR='" + inst.cfg.as_posix() + "'\n")
    config = inst.game / ".modding-agent-harness/config.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"save_reserve_bytes": 0}))
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.setenv(key, str(tmp_path / "home"))
    monkeypatch.setenv("SKYRIM_CONFIG_DIR", str(inst.cfg))
    monkeypatch.setenv("SKYRIM_GAME_ROOT", str(inst.game))
    return inst


def action(inst, names=("Save1", "Save2", "Save3"), operations=None, **changes):
    data = dict(host="claude", tool="write", surface="files", cwd=str(inst.game), session_id="preview-session",
                command="opaque fixture operation", paths=tuple(str(inst.saves / (n + ".ess")) for n in names),
                operations=operations or ("write",) * len(names))
    data.update(changes)
    return contracts.Action(**data)


def cli(inst, request, preview=True, transcript=""):
    args = [sys.executable, "-B", str(inst.game / "tools/agent-harness.py"), "check"]
    if preview:
        args.append("--preview")
    if transcript:
        args.extend(("--transcript", transcript))
    proc = subprocess.run(args, input=json.dumps(asdict(request)), capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def grants(report):
    return [json.loads(line.split("SAVE_ACCESS_APPROVAL ", 1)[1])
            for line in report["result"]["reason"].splitlines() if line.startswith("SAVE_ACCESS_APPROVAL ")]


def expected(inst, names, operation):
    # Independent of the production approval builder; include absent counterparts too.
    return {"v": 1, "session_id": "preview-session", "op": operation,
            "paths": sorted(os.path.normcase(os.path.realpath(inst.saves / (n + ext)))
                            for n in names for ext in (".ess", ".skse"))}


@pytest.mark.parametrize("names,operations", [(("Save1",), ("write",)),
    (("Save1", "Save2", "Save3"), ("write", "write", "write")),
    (("Save1", "Save2", "Save3"), ("write", "delete", "move"))])
def test_preview_cli_preserves_exact_scope_without_writes(installed, names, operations):
    inst = installed
    if len(names) > 2:
        (inst.saves / "Save3.skse").unlink()  # absent counterparts remain part of the approval scope
    before = census(inst.game.parent)
    report = cli(inst, action(inst, names, operations))
    assert report["preview"] is True and report["execution_authorized"] is False
    assert report["runtime_writes"] is False and report["transactions"] == []
    assert report["result"]["preservation"] == "required"
    assert census(inst.game.parent) == before
    wanted = [expected(inst, tuple(n for n, op in zip(names, operations) if op == operation), operation)
              for operation in sorted(set(operations))] if len(names) > 2 else []
    assert grants(report) == wanted
    assert report["result"]["decision"] == ("deny" if len(names) > 2 else "allow")


def test_preview_wildcard_grant_binds_command_count_and_all_members(installed):
    inst = installed
    command = 'rm "' + inst.S + '/"Save*.ess'
    request = action(inst, names=(), shell="bash", surface="shell", tool="Bash", command=command)
    before = census(inst.game.parent)
    report = cli(inst, request)
    wanted = expected(inst, ("Save1", "Save2", "Save3"), "delete")
    wanted.update(count=3, command_sha256=hashlib.sha256(command.encode()).hexdigest())
    assert grants(report) == [wanted] and report["result"]["decision"] == "deny"
    assert census(inst.game.parent) == before


def test_normal_check_fallback_exposes_full_scope_with_registered_policy(installed):
    inst = installed
    register(inst.game, "def decide(payload): return {'kind': 'allow'}\n")
    request = action(inst)
    before = census(inst.game.parent)
    preview = cli(inst, request)
    assert preview["result"]["decision"] == "deny" and preview["result"]["evaluation"] == "partial"
    assert "local policy workers" in preview["result"]["reason"] and "normal check" in preview["result"]["reason"]
    assert census(inst.game.parent) == before
    report = cli(inst, request, preview=False)
    assert grants(report) == [expected(inst, ("Save1", "Save2", "Save3"), "write")]
    assert report["result"]["decision"] == "deny" and not report["transactions"]
    grant = "SAVE_ACCESS_APPROVAL " + json.dumps(grants(report)[0])
    transcript = inst.transcript(grant)
    # The transcript session is supplied by the action, not by the transcript writer.
    approved = cli(inst, request, preview=False, transcript=transcript)
    assert approved["result"]["preservation"] == "verified" and len(approved["transactions"]) == 1
    for name in ("Save1", "Save2", "Save3"):
        assert (inst.saves / (name + ".ess")).read_bytes() == name.encode() * 100
        assert (inst.saves / (name + ".skse")).read_bytes() == name.encode() * 7


def test_preview_never_runs_unqualified_workers_or_callbacks(installed, monkeypatch):
    inst = installed
    register(inst.game, "def decide(payload): return {'kind': 'allow'}\n")
    def forbidden(*args, **kwargs):
        pytest.fail("preview executed an unqualified worker/callback or preservation side effect")
    for name in ("supplement_native_backups", "supplement_controlled", "supplement_shell_scope", "restriction"):
        monkeypatch.setattr(portable.local_policy, name, forbidden)
    monkeypatch.setattr(engine, "side_effects", forbidden)
    monkeypatch.setattr(portable.telemetry.Telemetry, "record", forbidden)
    before = census(inst.game.parent)
    outcome, count = portable.check(action(inst), inst.game, preview=True)
    assert outcome.result.decision == "deny" and outcome.result.evaluation == "partial" and count is None
    other, _ = portable.check(action(inst), inst.game, preview=True, local_restrictions=(forbidden,))
    assert other.result.decision == "deny" and "callbacks" in other.result.reason
    assert census(inst.game.parent) == before


def test_preview_with_existing_grant_still_cannot_preserve_or_authorize(installed):
    inst = installed
    grant = "SAVE_ACCESS_APPROVAL " + json.dumps(expected(inst, ("Save1", "Save2", "Save3"), "write"))
    transcript = inst.transcript(grant)
    before = census(inst.game.parent)
    report = cli(inst, action(inst), transcript=transcript)
    assert report["result"]["decision"] == "allow" and report["result"]["preservation"] == "required"
    assert report["execution_authorized"] is False and report["transactions"] == []
    assert census(inst.game.parent) == before


def test_normal_check_fallback_keeps_registered_denial(installed):
    inst = installed
    register(inst.game, "def decide(payload): return {'kind': 'deny', 'reason': 'owned personal refusal'}\n")
    before = census(inst.game.parent)
    report = cli(inst, action(inst))
    assert report["result"]["evaluation"] == "partial" and census(inst.game.parent) == before
    deciding = cli(inst, action(inst), preview=False)
    assert deciding["result"]["decision"] == "deny" and "owned personal refusal" in deciding["result"]["reason"]
    assert grants(deciding) == [] and not deciding["transactions"]


def test_readonly_shadow_does_not_change_normal_relock_path(installed, monkeypatch):
    inst = installed
    state.write(inst.game / ".skyguard/state.json", {"v": 1, "persistent": {}, "lifts": [{"kind": "lock",
                "reason": "owned", "session_id": "preview-session", "agent": "test", "created": 1.0,
                "expires_at": 2.0}]})
    calls = []
    monkeypatch.setattr(engine, "_relock_due", lambda *args: calls.append("normal relock"))
    value = Verdict()
    engine.shadow(value, str(inst.game), "preview-session", "", inst.audit)
    assert calls == ["normal relock"]
    engine.shadow(value, str(inst.game), "preview-session", "", portable._PreviewAudit(str(inst.game)), readonly=True)
    assert calls == ["normal relock"]


@pytest.mark.parametrize("bad_state", [False, True])
def test_preview_shadow_does_not_write_reject_markers_or_relock(installed, monkeypatch, bad_state):
    inst = installed
    sf = inst.game / ".skyguard/state.json"
    if bad_state:
        sf.parent.mkdir()
        sf.write_bytes(b"not a valid state")
    else:
        state.write(sf, {"v": 1, "persistent": {}, "lifts": [{"kind": "lock", "reason": "owned expired fixture",
                    "session_id": "preview-session", "agent": "test", "created": 1.0, "expires_at": 2.0}]})
    def forbidden(*args, **kwargs):
        pytest.fail("preview tried to mutate rejection or relock state")
    monkeypatch.setattr(engine, "_relock_due", forbidden)
    monkeypatch.setattr(engine, "_state_reject_is_new", forbidden)
    monkeypatch.setattr(engine, "_state_reject_clear", forbidden)
    before = census(inst.game.parent)
    outcome, _ = portable.check(action(inst, ("Save1",)), inst.game, preview=True)
    assert outcome.result.preservation == "required" and not outcome.transactions
    assert census(inst.game.parent) == before


def test_readonly_shadow_retains_session_binding_and_never_shadow(installed):
    inst = installed
    now = time.time()
    state.write(inst.game / ".skyguard/state.json", {"v": 1, "persistent": {}, "lifts": [{"kind": "guards",
                "reason": "owned", "session_id": "preview-session", "agent": "test", "created": now - 1,
                "expires_at": now + 60}]})
    hb = inst.game / "heartbeat/sessions"
    hb.mkdir(parents=True)
    (hb / "preview-session.log").write_text(str(now - 2) + " start startup fixture\n")
    audit = portable._PreviewAudit(str(inst.game))
    before = census(inst.game.parent)
    decoy = Verdict("deny", "Blocked: fixture.", code="write.W-PLUGIN")
    assert engine.shadow(decoy, str(inst.game), "preview-session", str(hb.parent), audit, now, readonly=True).decision == "allow"
    assert engine.shadow(decoy, str(inst.game), "other-session", str(hb.parent), audit, now, readonly=True).decision == "deny"
    kept = decoy._replace(code="shell.S-SAVE-APPROVAL")
    assert engine.shadow(kept, str(inst.game), "preview-session", str(hb.parent), audit, now, readonly=True).decision == "deny"
    assert census(inst.game.parent) == before


def test_adapting_shell_example_yields_the_grant_the_refusal_does_not_print(installed):
    """Review C-docs F1: every doc said the bulk-save refusal prints the SAVE_ACCESS_APPROVAL line; it prints only a
    pointer to `check --preview`. ADAPTING's shell-surface example, run as written (fixture paths swapped in), must
    produce the delete grant for all three pairs, so the documented route is one a user can actually follow."""
    import re
    text = (REPO / "ADAPTING.md").read_text(encoding="utf-8")
    blocks = [b for b in re.findall(r"```json\n(.*?)\n```", text, re.S) if '"surface":"shell"' in b]
    assert len(blocks) == 1, "ADAPTING.md must carry exactly one shell-surface preview example"
    inst = installed
    data = json.loads(blocks[0])
    data.update(cwd=str(inst.game), session_id="preview-session")
    data["command"] = data["command"].replace("C:/Profile/Saves", inst.S).replace("/A.ess", "/Save1.ess") \
        .replace("/B.ess", "/Save2.ess").replace("/C.ess", "/Save3.ess")
    data.pop("api_version")
    hook = live_enforce(inst.shell(data["command"]), inst.game, inst.roots, inst.audit, command=data["command"])
    assert (hook.decision, hook.code) == ("deny", "shell.S-SAVE-APPROVAL"), hook
    assert "SAVE_ACCESS_APPROVAL {" not in hook.reason and "check --preview" in hook.reason
    report = cli(inst, contracts.Action(**data))
    assert report["result"]["decision"] == "deny"
    assert grants(report) == [expected(inst, ("Save1", "Save2", "Save3"), "delete")]


def test_preview_unknown_surface_stays_warn_and_allow(installed):
    before = census(installed.game.parent)
    report = cli(installed, action(installed, names=(), surface="unknown", tool="future"))
    assert report["result"]["decision"] == "allow" and report["result"]["evaluation"] == "unguarded"
    assert any("WARN AND ALLOW" in row for row in report["result"]["warnings"])
    assert report["execution_authorized"] is False and census(installed.game.parent) == before


def test_preview_unknown_mcp_does_not_omit_registered_restriction(installed):
    inst = installed
    register(inst.game, "def decide(payload): return {'kind': 'deny', 'reason': 'owned personal restriction'}\n")
    before = census(inst.game.parent)
    report = cli(inst, action(inst, names=(), surface="mcp", tool="mcp__unlisted__operation"))
    assert report["result"]["decision"] == "deny" and report["result"]["evaluation"] == "partial"
    assert "local policy workers" in report["result"]["reason"] and census(inst.game.parent) == before


@pytest.mark.parametrize("row_id", ["sp-bash-three", "sp-bash-three-approval-in-tool-result", "sp-bash-wildcard",
                                    "sp-cmd-del-three", "sp-ps-wildcard"])
def test_compact_bulk_hook_and_preview_scope_oracle(tmp_path, row_id):
    import importlib.util
    spec = importlib.util.spec_from_file_location("approval_preview_record", REPO / "scripts/guard-corpus/record.py")
    record = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(record)
    row, = [json.loads(line) for line in (REPO / "tests/guard_corpus/intended.jsonl").read_text(encoding="utf-8").splitlines()
            if json.loads(line)["id"] == row_id]
    install = record.make_install(tmp_path, REPO / ".claude/hooks", row["layout"])
    payload = record.row_payload(row, install)
    got = record.fire_full(install, row)
    assert contract_problems(row_id, got["text"], payload) == []
    assert record.intended_problems(row, got) == []
    assert "check --preview" in got["text"] and "use check" in got["text"]
    request = contracts.Action("claude", payload["tool_name"], "shell", cwd=str(install),
              shell="powershell" if payload["tool_name"] == "PowerShell" else "bash",
              session_id=payload["session_id"], command=payload["tool_input"]["command"])
    # Engine report keeps exact grants out of the bounded host reason.
    before = census(install)
    outcome, _ = portable.check(request, install, preview=True)
    assert grants(portable.reply(outcome)) == [record.canonical_scope_expected(row, install)]
    assert census(install) == before


def test_compact_bulk_reason_is_session_independent_but_grants_are_not(installed):
    inst = installed
    req = portable.request(action(inst))
    texts, scopes = [], []
    for session in ("session-one", "session-two"):
        altered = req._replace(session_id=session)
        value = portable.legacy_pre_tool(altered, str(inst.game), lambda: inst.roots, inst.audit, side_effects=False)
        texts.append(value.reason)
        request = action(inst, session_id=session)
        outcome, _ = portable.check(request, inst.game, preview=True)
        scopes.append(grants(portable.reply(outcome))[0]["session_id"])
    assert texts[0] == texts[1] and len(texts[0].splitlines()) == 2
    assert scopes == ["session-one", "session-two"]
