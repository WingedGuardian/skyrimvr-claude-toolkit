"""Plan 6 Task 11 (D6, R7): the lift rules.

  S-LIFT-NOTE       `tools/skyguard lock unlock` / `guards off` run by an agent -> allowed, with the advisory (a D6 lift).
  S-LIFT-PERMANENT  `tools/skyguard lock disable` -> ask on Claude; the Codex adapter turns an ask into a deny (R4).
  S-LIFT-RAW        attrib -R / icacls /remove /reset /grant / Set-Acl / IsReadOnly / Attributes / SetAttributes on a
                    CURRENT LOCK TARGET of the install -> deny, whatever --session says.
  S-LIFT-UNLOCKCMD  tools/skyguard-unlock.cmd run by an agent -> deny (the Python-free unlock is the user's).

Two halves, as in test_skyguard_mcp: every hand-authored P6-T11 intended row fired IN-PROCESS (decision, rule trace,
exact text; parametrized by row id so a mutation names the one row it must turn red), then one behaviour per test.
No ACL or attribute is ever applied here: the rules are pure.
"""
import json
import sys
import tempfile
import time

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, codes, codex_rules, engine, lift_rules, paths, rules, shell_rules, state  # noqa: E402
from skyguard.adapters import codex  # noqa: E402
from skyguard.verdict import Verdict  # noqa: E402

GAME = "C:/GOG Games/The Elder Scrolls V Skyrim VR"
SHELL = {"Bash": "bash", "PowerShell": "powershell"}
NOHOME = tempfile.mkdtemp(prefix="p6t11-nohome-")
ENV0 = {"USERPROFILE": NOHOME}
LIFT_IDS = ("S-LIFT-NOTE", "S-LIFT-RAW", "S-LIFT-PERMANENT", "S-LIFT-UNLOCKCMD")


def _rows():
    text = (REPO / "tests" / "guard_corpus" / "intended.jsonl").read_text(encoding="utf-8")
    return [r for r in (json.loads(x) for x in text.split("\n") if x.strip()) if r["task"] == "P6-T11"]


ROWS = _rows()


def _audit():
    a = audit.Audit("")
    a.raw = lambda line: None
    return a


def _got(v):
    if v.decision == "allow":
        return ("advise", v.context) if v.context else ("allow", "")
    return (v.decision, v.reason)


def shell(command, shell_name="bash", cwd=GAME):
    a = _audit()
    r = paths.roots("C:/p6t11/Skyrim VR", "", {"SKYRIM_GAME_ROOT": GAME})
    v = shell_rules.decide(command, r, a, cwd=cwd, shell=shell_name, env=ENV0)
    return _got(v), {rid for rid, _ in a.fired}


def test_there_are_rows_for_every_rule_and_every_decision():
    assert len(ROWS) >= 40, len(ROWS)
    assert {r["decision"] for r in ROWS} == {"allow", "advise", "ask", "deny"}
    fired = set().union(*(set(r["rules"]) for r in ROWS))
    assert fired == set(LIFT_IDS)


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_p6t11_row(row):
    p = row["payload"]
    cmd = p["tool_input"]["command"].replace("<install>", GAME)
    got, fired = shell(cmd, SHELL[p["tool_name"]], (p.get("cwd") or "").replace("<install>", GAME))
    assert got == (row["decision"], row["text"])
    assert fired == set(row["rules"])


# ---- the registry, the Codex classification and the adapter -------------------------------------------------------

def test_the_four_rules_are_registered_with_their_decisions():
    want = {"S-LIFT-NOTE": "advise", "S-LIFT-RAW": "deny", "S-LIFT-PERMANENT": "ask", "S-LIFT-UNLOCKCMD": "deny"}
    for rid, decision in want.items():
        assert rules.RULES[rid]["decision"] == decision and rules.RULES[rid]["hook"] == "protect-bash", rid


def test_the_decisive_ones_are_hook_only_on_codex():
    """Plan 4 R5: a `forbidden` prefix would also refuse the USER's own `!` command in Codex, and running the .cmd is
    exactly what the README tells a Codex user to do; none of these is a strict-subset prefix, so hook_only."""
    for rid in ("S-LIFT-RAW", "S-LIFT-PERMANENT", "S-LIFT-UNLOCKCMD"):
        assert codex_rules.CLASSIFICATION["shell." + rid][0] == "hook_only", rid
    assert not any(r["code"].startswith("shell.S-LIFT") for r in codex_rules.PREFIX_RULES)


def test_on_codex_the_disable_ask_is_rendered_as_a_deny():
    """R7: asks fail open on Codex, so the adapter must turn the S-LIFT-PERMANENT ask into a deny."""
    (decision, reason), _ = shell("python tools/skyguard lock disable")
    assert decision == "ask"
    body = json.loads(codex.render(codes.classify(Verdict("ask", reason))))["hookSpecificOutput"]
    assert body["permissionDecision"] == "deny"
    assert body["permissionDecisionReason"].endswith(reason)


def test_the_note_on_codex_is_context_not_a_refusal():
    (decision, ctx), _ = shell("python tools/skyguard guards off --session s", "powershell")
    assert decision == "advise"
    body = json.loads(codex.render(Verdict("allow", "", ctx)))["hookSpecificOutput"]
    assert "permissionDecision" not in body and body["additionalContext"] == ctx


# ---- shadow mode cannot lift the lift rules (R8: the Layer 2 lock is a separate lift) -------------------------------

H = 3600.0
NOW = 1_800_000_000.0
SID = "sess-p6t11"


def _shadow_env(tmp_path):
    project, hb = tmp_path / "proj", tmp_path / "hb"
    project.mkdir()
    state.write(str(project / ".skyguard" / "state.json"), {"v": 1, "persistent": {}, "lifts": [
        {"kind": "guards", "reason": "t", "session_id": SID, "agent": "claude", "created": NOW - H,
         "expires_at": NOW + H}]})
    f = hb / "sessions" / (SID + ".log")
    f.parent.mkdir(parents=True)
    f.write_text("{:.6f} start startup t.jsonl\n".format(NOW - 2 * H), encoding="utf-8")
    return project, hb


@pytest.mark.parametrize("command,shell_name", [
    ("python tools/skyguard lock disable", "bash"),
    ("attrib -R Data/Skyrim.esm", "bash"),
    ("cmd /c tools\\\\skyguard-unlock.cmd", "bash"),
], ids=["permanent", "raw", "unlockcmd"])
def test_guards_off_does_not_shadow_a_lift_rule(tmp_path, command, shell_name):
    """guards off (an agent may take it, D6) must not turn `lock disable` into a silent persistent off, nor a raw
    unlock into an allow: the refusal stands, and the SHADOW line says it was kept."""
    project, hb = _shadow_env(tmp_path)
    (decision, reason), _ = shell(command, shell_name)
    assert decision in ("deny", "ask")
    a = audit.Audit(str(project))
    v = engine.shadow(codes.classify(Verdict(decision, reason)), str(project), SID, str(hb), a, now=NOW)
    assert (v.decision, v.reason) == (decision, reason)
    log = (project / ".claude" / "backups" / "AUDIT_LOG.txt").read_text(encoding="utf-8")
    assert "SHADOW" in log and "kept" in log


def test_guards_off_still_shadows_an_ordinary_rule(tmp_path):
    """The control for the test above: the same lift DOES convert a refusal no lift rule made."""
    project, hb = _shadow_env(tmp_path)
    (decision, reason), _ = shell("echo x > Foo.esp", "bash", "C:/work")
    assert decision == "deny"
    v = engine.shadow(codes.classify(Verdict(decision, reason)), str(project), SID, str(hb),
                      audit.Audit(str(project)), now=NOW)
    assert v.decision == "allow" and v.context.startswith("Guards OFF")


# ---- the helpers, one behaviour each --------------------------------------------------------------------------------

def test_the_lock_names_are_the_lock_modules_own_list():
    """One list, not two: the rule's target names are lock._names for every edition (R1), plus Creation Club names."""
    from skyguard import lock
    for ed in ("vr", "se"):
        for n in lock._names(ed):
            assert lift_rules.is_lock_name(n), n
    assert lift_rules.is_lock_name("ccBGSSSE001-Fish.esm") and lift_rules.is_lock_name("ccqdrsse001-survivalmode.bsa")
    assert not lift_rules.is_lock_name("MyMod.esp") and not lift_rules.is_lock_name("Skyrim.esm.bak")


@pytest.mark.parametrize("command", [
    "attrib -R C:/elsewhere/Data/Skyrim.esm",
    "icacls D:/Backup/Skyrim/Data/Skyrim.esm /reset",
], ids=["attrib-other-data", "icacls-other-data"])
def test_a_vanilla_name_in_another_data_folder_is_not_the_install(command):
    assert shell(command, cwd="C:/work") == (("allow", ""), set())


def test_an_unparseable_command_naming_a_target_with_an_unlock_construct_is_denied():
    """R4: when the parser cannot read the command, the construct and a target name in the text decide."""
    got, fired = shell("attrib -R \"" + GAME + "/Data/Skyrim.esm", "bash")       # unterminated quote
    assert got[0] == "deny" and fired == {"S-LIFT-RAW"}


def test_an_unparseable_unlock_cmd_is_still_denied():
    got, fired = shell("tools/skyguard-unlock.cmd \"unterminated", "bash")
    assert got[0] == "deny" and fired == {"S-LIFT-UNLOCKCMD"}


def test_an_unparseable_disable_still_asks():
    got, fired = shell("python tools/skyguard lock disable \"unterminated", "bash")
    assert got[0] == "ask" and fired == {"S-LIFT-PERMANENT"}


def test_a_lift_rule_message_never_echoes_the_install_path():
    for cmd in ("attrib -R \"" + GAME + "/Data/Skyrim.esm\"", "python tools/skyguard lock unlock " + GAME + "/Data/Update.esm"):
        (decision, text), _ = shell(cmd)
        assert "GOG Games" not in text and "C:" not in text, text


def test_many_files_are_summarised():
    (decision, text), _ = shell("python tools/skyguard lock unlock Skyrim.esm Update.esm Dawnguard.esm Dragonborn.esm")
    assert decision == "advise"
    assert text == ("Note: this lifts the lock on Skyrim.esm, Update.esm and 2 more until the next session starts. "
                    "Tell the user why, and restore it when done.")
