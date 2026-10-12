"""Plan 6 final fix pass (lane A C1): a NEVER_SHADOW refusal holds whatever else is in the call.

`guards off` (an agent may take it, D6) shadows the ordinary refusals. Before this fix only the FIRST refusal a call
produced reached engine.shadow, so a decoy refusal evaluated earlier (a delete inside the install, a ReSaver --apply)
hid a lift or self-state refusal behind it, and shadow mode then allowed the whole command -- the `lock disable`,
`attrib -R` or `.skyguard/state.json` write included. Each probe below pairs a NEVER_SHADOW rule with such a decoy
and asserts: under an HONOURED guards lift the call is refused by the NEVER_SHADOW rule (and the audit names it);
without a lift the call is refused exactly as strictly as before (the decoy's deny is not loosened into an ask).
"""
import json
import sys
import tempfile

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, codes, engine, paths, shell_rules, state  # noqa: E402
from skyguard.adapters import codex  # noqa: E402
from skyguard.verdict import Verdict  # noqa: E402

GAME = "C:/GOG Games/The Elder Scrolls V Skyrim VR"
NOHOME = tempfile.mkdtemp(prefix="p6fixA-nohome-")
ENV0 = {"USERPROFILE": NOHOME}
H = 3600.0
NOW = 1_800_000_000.0
SID = "sess-p6fixA"
DATA = GAME + "/Data"

# (id, shell, command, the NEVER_SHADOW code that must stand under guards off, its decision)
PROBES = [
    ("disable-after-decoy-delete", "bash",
     'python tools/skyguard lock disable; rm -f "' + DATA + '/nonexistent.txt"', "shell.S-LIFT-PERMANENT", "ask"),
    ("decoy-delete-then-disable", "bash",
     'rm -f "' + DATA + '/nonexistent.txt"; bash tools/skyguard.sh lock disable', "shell.S-LIFT-PERMANENT", "ask"),
    ("attrib-after-decoy-delete", "bash",
     'attrib -R "' + DATA + '/Skyrim.esm"; rm -f "' + DATA + '/x.txt"', "shell.S-LIFT-RAW", "deny"),
    ("unlockcmd-after-decoy-delete", "bash",
     'rm -f "' + DATA + '/x.txt"; cmd /c tools\\\\skyguard-unlock.cmd', "shell.S-LIFT-UNLOCKCMD", "deny"),
    ("sip-readonly-after-decoy-remove", "powershell",
     "Set-ItemProperty '" + DATA + "/Skyrim.esm' -Name IsReadOnly -Value $false; Remove-Item '" + DATA + "/x.txt'",
     "shell.S-LIFT-RAW", "deny"),
    ("disable-after-decoy-ask", "bash",
     "bash tools/resaver-cli.sh clean a.ess b.ess --apply; python tools/skyguard lock disable",
     "shell.S-LIFT-PERMANENT", "ask"),
    ("disable-after-decoy-plugin-write", "bash",
     'echo x > "' + DATA + '/Foo.esp"; python tools/skyguard lock disable', "shell.S-LIFT-PERMANENT", "ask"),
]


def _audit(project=None):
    if project is not None:
        return audit.Audit(str(project))
    a = audit.Audit("")
    a.raw = lambda line: None
    return a


def _roots():
    return paths.roots("C:/p6fixA/Skyrim VR", "", {"SKYRIM_GAME_ROOT": GAME})


def _decide(command, shell_name, a=None):
    return codes.classify(shell_rules.decide(command, _roots(), a or _audit(), cwd=GAME, shell=shell_name, env=ENV0))


def _env(tmp_path, lift=True):
    project, hb = tmp_path / "proj", tmp_path / "hb"
    project.mkdir(exist_ok=True)
    lifts = [{"kind": "guards", "reason": "t", "session_id": SID, "agent": "claude", "created": NOW - H,
              "expires_at": NOW + H}] if lift else []
    state.write(str(project / ".skyguard" / "state.json"), {"v": 1, "persistent": {}, "lifts": lifts})
    f = hb / "sessions" / (SID + ".log")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("{:.6f} start startup t.jsonl\n".format(NOW - 2 * H), encoding="utf-8")
    return project, hb


def _log(project):
    p = project / ".claude" / "backups" / "AUDIT_LOG.txt"
    return p.read_text(encoding="utf-8") if p.exists() else ""


@pytest.mark.parametrize("cid,shell_name,command,code,decision", PROBES, ids=[p[0] for p in PROBES])
def test_a_never_shadow_refusal_stands_under_guards_off_beside_a_decoy(tmp_path, cid, shell_name, command, code,
                                                                         decision):
    project, hb = _env(tmp_path)
    v = engine.shadow(_decide(command, shell_name), str(project), SID, str(hb), _audit(project), now=NOW)
    assert (v.decision, v.code) == (decision, code), (v.decision, v.code, v.context)
    assert "SHADOW: {} kept".format(code) in _log(project)


@pytest.mark.parametrize("cid,shell_name,command,code,decision", PROBES, ids=[p[0] for p in PROBES])
def test_without_a_lift_the_decoy_call_is_refused_no_less_strictly(tmp_path, cid, shell_name, command, code, decision):
    """The twin: the fix must not trade the decoy's deny for the lift rule's ask when nothing is shadowed."""
    project, hb = _env(tmp_path, lift=False)
    plain = _decide(command, shell_name)
    v = engine.shadow(plain, str(project), SID, str(hb), _audit(project), now=NOW)
    assert v.decision == plain.decision
    assert v.decision == "deny" or (v.decision == "ask" and decision == "ask")
    if cid in ("disable-after-decoy-delete", "decoy-delete-then-disable", "disable-after-decoy-plugin-write"):
        assert v.decision == "deny" and v.code != "shell.S-LIFT-PERMANENT"


def test_an_ordinary_decoy_alone_is_still_shadowed(tmp_path):
    """The control: the same decoy with no lift rule beside it IS converted (so the tests above are about the mix)."""
    project, hb = _env(tmp_path)
    v = engine.shadow(_decide('rm -f "' + DATA + '/nonexistent.txt"', "bash"), str(project), SID, str(hb),
                      _audit(project), now=NOW)
    assert v.decision == "allow" and v.context.startswith("Guards OFF")


# ---- engine.combine: the Codex apply_patch and shell+paths folds ----------------------------------------------------

def test_combine_puts_a_never_shadow_deny_above_an_earlier_shadowable_deny():
    dele = Verdict("deny", "x", code="shell.S-DEL-INSTALL")
    own = Verdict("deny", "y", code="write.W-SELF-STATE")
    assert engine.combine([dele, own]).code == "write.W-SELF-STATE"


def test_combine_keeps_a_deny_and_carries_the_never_shadow_ask_beside_it():
    dele = Verdict("deny", "x", code="shell.S-DEL-INSTALL")
    perm = Verdict("ask", "y", code="shell.S-LIFT-PERMANENT")
    v = engine.combine([perm, dele])
    assert (v.decision, v.code) == ("deny", "shell.S-DEL-INSTALL")
    assert v.never is not None and v.never.code == "shell.S-LIFT-PERMANENT"


def _patch(body):
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "cwd": "C:/G", "session_id": SID,
                       "tool_input": {"command": "*** Begin Patch\n" + body + "\n*** End Patch"}})


def _pre_tool(project, hb, raw):
    req = codex.to_request(raw, sys.platform)  # host platform: on Linux CI an absolute POSIX path under a Windows adapter is a mix no host makes (py3.13 joins it onto C:)
    return engine.pre_tool(req, str(project), lambda: paths.roots(str(project), "", {}), audit.Audit(str(project)),
                           telemetry=str(hb), now=NOW)


def test_a_codex_patch_deleting_a_file_then_adding_the_state_is_refused_under_guards_off(tmp_path):
    project, hb = _env(tmp_path)
    p = str(project).replace("\\", "/")
    body = "*** Delete File: {}/Data/x.txt\n*** Add File: {}/.skyguard/state.json\n+{{}}".format(p, p)
    v = _pre_tool(project, hb, _patch(body))
    assert (v.decision, v.code) == ("deny", "write.W-SELF-STATE"), (v.decision, v.code)
    assert "SHADOW: write.W-SELF-STATE kept" in _log(project)


def test_the_same_patch_without_a_lift_is_denied(tmp_path):
    project, hb = _env(tmp_path, lift=False)
    p = str(project).replace("\\", "/")
    body = "*** Delete File: {}/Data/x.txt\n*** Add File: {}/.skyguard/state.json\n+{{}}".format(p, p)
    assert _pre_tool(project, hb, _patch(body)).decision == "deny"
