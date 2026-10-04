"""Conformance of the generated Codex entry to REAL captured payloads (Plan 4 Task 12; spec 7.3-7.4).

Every file under tests/fixtures/payloads/codex/ (both capture folders) is fed, byte-for-byte except `cwd`,
through the configured entry scripts of a fixture install -- `skyguard-entry.ps1` under pwsh and Windows
PowerShell 5.1 where present, `skyguard-entry.sh` under Git Bash -- and four things are asserted:

  1. exit 0;
  2. stdout is empty, or one JSON object of an allowed shape (no `ask`, no `allow`, no unknown key), or
     (SessionStart) the SKYGUARD banner text;
  3. the stage telemetry holds a matching invoked / evaluated / emitted triple;
  4. the verdict equals the row in tests/fixtures/payloads/codex/EXPECTED.json (allow | advise | deny + code).

DERIVED variants take a captured shape and replace ONLY its path (or, for a shell shape, its command
string): the shape comes from a real capture, the path comes from the test (spec 7.3). They are generated
here, never hand-written, and are named `derived:<fixture>:<path>`.

Nothing here lists the shell verdict constants: a new DENY_/ASK_/NO_ constant (Plan 5 adds one) trips
test_codex_rules.py::test_every_shell_verdict_is_classified, which is the intended place and says what to do.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from conftest import REPO
import test_codex_entry as E

CAP = REPO / "tests" / "fixtures" / "payloads" / "codex"
EXPECTED_FILE = CAP / "EXPECTED.json"
ALLOWED_TOP = {"hookSpecificOutput"}
ALLOWED_BODY = {"hookEventName", "permissionDecision", "permissionDecisionReason", "additionalContext"}
VERDICTS = ("allow", "advise", "deny")


def _entries():
    out = []
    if E._ps_exe("pwsh"):
        out.append("pwsh")
    if E._ps_exe("powershell"):
        out.append("powershell")
    out.append("bash")
    return out


def _fixtures():
    return sorted((p for p in CAP.rglob("*.json") if p.name != EXPECTED_FILE.name), key=lambda p: p.relative_to(CAP).as_posix())


def _rel(p: Path) -> str:
    return p.relative_to(CAP).as_posix()


def _label(p: Path) -> str:
    """Test-id label: the plain name for the top-level folder, `<version>/<name>` for a versioned one."""
    r = _rel(p)
    return r[:-5] if r.endswith(".json") else r


def _expected_table() -> dict:
    return {k: v for k, v in json.loads(EXPECTED_FILE.read_text(encoding="utf-8")).items() if not k.startswith("_")}


# --------------------------------------------------------------------------------------------------
# derived variants: a captured shape, with only its path / command replaced
# --------------------------------------------------------------------------------------------------
def _patch_paths(command: str) -> list:
    """The distinct paths a captured patch names, in order (read off the captured text)."""
    out = []
    for line in command.replace("\r\n", "\n").split("\n"):
        m = re.match(r"\s*\*\*\* (?:Add File|Update File|Delete File|Move to):\s*(.+?)\s*$", line)
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


def _with_paths(command: str, target: str) -> str:
    """Replace each distinct captured path: the first becomes `target`, later ones `target` + an index."""
    olds = _patch_paths(command)
    assert olds, "no path to replace in the captured patch"
    new = {}
    for i, old in enumerate(olds):
        stem, dot, ext = target.rpartition(".")
        new[old] = target if i == 0 else "{}{}{}{}".format(stem, i, dot, ext)
    # longest first so a path that prefixes another cannot be replaced twice
    for old in sorted(new, key=len, reverse=True):
        command = command.replace(old, "\x00{}\x00".format(olds.index(old)))
    for old in olds:
        command = command.replace("\x00{}\x00".format(olds.index(old)), new[old])
    return command


def _variant(fixture: Path, vid: str, edit, expect: dict, setup=None, cwd_sub: str = "") -> pytest.param:
    return pytest.param(fixture, edit, expect, setup, cwd_sub, id=vid)


def _derived():
    out = []
    for f in _fixtures():
        d = json.loads(f.read_text(encoding="utf-8"))
        name, label = d.get("tool_name"), _label(f)
        if name == "apply_patch":
            cmd = d["tool_input"]["command"]
            for target, cwd_sub, expect, setup in (
                ("Data/Decoy.esp", "", {"verdict": "deny", "code": "write.DENY_PLUGIN"}, None),
                ("../x.esp", "sub", {"verdict": "deny", "code": "write.DENY_PLUGIN"}, None),
                ("Data/SKSE/Plugins/x.ini", "",
                 {"verdict": "advise", "code": "write.ADV_SKSE_CONFIG",
                  "backup": any(o in cmd for o in ("*** Update File:", "*** Delete File:"))}, _seed_ini),
            ):
                def edit(p, t=target, c=cmd):
                    p["tool_input"]["command"] = _with_paths(c, t)
                out.append(_variant(f, "derived:{}:{}".format(label, target), edit, expect, setup, cwd_sub))
        elif name == "Bash":
            cmd = d["tool_input"]["command"]
            if "apply_patch" in cmd:
                def edit(p, c=cmd):
                    p["tool_input"]["command"] = _with_paths(c, "Data/Skyrim.esm")
                out.append(_variant(f, "derived:{}:Data/Skyrim.esm".format(label), edit,
                                    {"verdict": "deny", "code": "write.DENY_PLUGIN"}))
                continue

            def rm(p):
                p["tool_input"]["command"] = 'Remove-Item -Recurse -Force "{}/Data/Skyrim.esm"'.format(p["cwd"])
            out.append(_variant(f, "derived:{}:Remove-Item".format(label), rm,
                                {"verdict": "deny", "code": "shell.DENY_INSTALL"}))

            def resaver(p):
                p["tool_input"]["command"] = "bash tools/resaver-cli.sh reset-havok save.ess --apply"
            out.append(_variant(f, "derived:{}:resaver-apply".format(label), resaver,
                                {"verdict": "deny", "code": "shell.ASK_RESAVER",
                                 "reason_prefix": "NEEDS YOUR CONFIRMATION"}))
    return out


def _seed_ini(root: Path) -> None:
    ini = root / "Data" / "SKSE" / "Plugins" / "x.ini"
    ini.parent.mkdir(parents=True, exist_ok=True)
    ini.write_text("[General]\nA=1\n", encoding="ascii")


# --------------------------------------------------------------------------------------------------
# running and judging one payload
# --------------------------------------------------------------------------------------------------
def _check_shape(role: str, out: bytes, where: str) -> str:
    """Return the observed verdict word: allow | advise | deny | session."""
    text = out.decode("utf-8")
    if role == "session-start":
        assert text.startswith("SKYGUARD: Codex hooks live"), (where, text)
        return "session"
    if text == "":
        return "allow"
    obj = json.loads(text)                                   # exactly one JSON object
    assert isinstance(obj, dict) and set(obj) == ALLOWED_TOP, (where, obj)
    body = obj["hookSpecificOutput"]
    assert set(body) <= ALLOWED_BODY, (where, "unknown key", sorted(set(body) - ALLOWED_BODY))
    assert body.get("hookEventName") == "PreToolUse", (where, body)
    assert text.endswith("\n") and "\n" not in text[:-1], (where, "not exactly one line")
    if "permissionDecision" in body:
        assert body["permissionDecision"] == "deny", (where, "only deny blocks on Codex", body)
        assert body.get("permissionDecisionReason"), (where, "a deny without a reason")
        return "deny"
    assert body.get("additionalContext"), (where, "an empty body")
    return "advise"


def _telemetry_triple(root: Path, role: str, where: str):
    inv = E._rec(root, role, "invoked").split()
    ev = E._rec(root, role, "evaluated").split()
    em = E._rec(root, role, "emitted").split()
    assert inv and ev and em, (where, "missing stage", inv, ev, em)
    assert ev[1] == inv[1] == em[1], (where, "the three stages name different calls", inv, ev, em)
    assert not E._rec(root, role, "failed"), (where, "a failure was recorded", E._rec(root, role, "failed"))
    return ev[2:]                                            # status decision code


def _run_one(entry, tmp_path, fixture: Path, edit, expect: dict, setup, cwd_sub: str, where: str):
    root = E._install(tmp_path / entry)
    if setup:
        setup(root)
    d = json.loads(fixture.read_text(encoding="utf-8"))
    cwd = root / cwd_sub if cwd_sub else root
    cwd.mkdir(parents=True, exist_ok=True)
    d["cwd"] = cwd.as_posix()
    if edit:
        edit(d)
    role = "session-start" if d["hook_event_name"] == "SessionStart" else "pre-tool"
    r = E._run(entry, root, role, json.dumps(d).encode("utf-8"))
    where = "{} [{}]".format(where, entry)
    assert r.returncode == 0, (where, r.returncode, r.stdout, r.stderr)
    verdict = _check_shape(role, r.stdout, where)
    status, decision, code = (_telemetry_triple(root, role, where) + ["", "", ""])[:3]
    if role == "session-start":
        got = {"verdict": "allow", "code": code}
        assert verdict == "session"
    else:
        got = {"verdict": verdict, "code": code}
    want = {"verdict": expect["verdict"], "code": expect["code"]}
    assert got == want, (where, got, want, r.stdout)
    if role == "pre-tool":
        if want["verdict"] == "deny":
            body = json.loads(r.stdout)["hookSpecificOutput"]
            assert "GUARD INERT" not in body["permissionDecisionReason"], (where, "a refusal, not the rule", body)
            if expect.get("reason_prefix"):
                assert body["permissionDecisionReason"].startswith(expect["reason_prefix"]), (where, body)
        if want["verdict"] == "advise":
            assert decision == "allow", (where, decision)
    if "backup" in expect:
        made = sorted((root / ".claude" / "backups").glob("*__*Data_SKSE_Plugins_x.ini"))
        assert bool(made) == expect["backup"], (where, "backup file", made)
    if expect.get("recorded"):
        log = root / ".claude" / "backups" / "codex-unhooked-tools.log"
        assert log.is_file() and expect["recorded"] in log.read_text(encoding="utf-8"), (where, "not recorded")
    return r


# --------------------------------------------------------------------------------------------------
# the tests
# --------------------------------------------------------------------------------------------------
def test_every_captured_fixture_has_an_expectation_and_no_stale_row():
    table = _expected_table()
    on_disk = [_rel(p) for p in _fixtures()]
    assert on_disk, "no captured Codex payloads -- an empty set would pass everything vacuously"
    assert [k for k in on_disk if k not in table] == [], "a captured payload with no EXPECTED.json row"
    assert [k for k in table if k not in on_disk] == [], "an EXPECTED.json row naming no payload"
    for k, v in table.items():
        assert v["verdict"] in VERDICTS and v["code"], k


def test_both_capture_folders_are_covered():
    rels = [_rel(p) for p in _fixtures()]
    assert any("/" not in r for r in rels) and any(r.startswith("0.160.0/") for r in rels), rels


@pytest.mark.parametrize("fixture", _fixtures(), ids=_label)
def test_captured_payload(tmp_path, fixture):
    exp = _expected_table()[_rel(fixture)]
    d = json.loads(fixture.read_text(encoding="utf-8"))
    expect = dict(exp)
    if d.get("tool_name", "").startswith("mcp__"):
        expect["recorded"] = d["tool_name"]
    for entry in _entries():
        _run_one(entry, tmp_path, fixture, None, expect, None, "", _label(fixture))


@pytest.mark.parametrize("fixture,edit,expect,setup,cwd_sub", _derived())
def test_conformance(tmp_path, fixture, edit, expect, setup, cwd_sub, request):
    for entry in _entries():
        _run_one(entry, tmp_path, fixture, edit, expect, setup, cwd_sub, request.node.callspec.id)


def test_the_derived_set_spans_every_op_and_both_shell_shapes():
    ids = [p.id for p in _derived()]
    assert len(ids) == len(set(ids)), "duplicate derived ids"
    for needle in (":Data/Decoy.esp", ":../x.esp", ":Data/SKSE/Plugins/x.ini", ":Data/Skyrim.esm",
                   ":Remove-Item", ":resaver-apply"):
        assert any(i.endswith(needle) for i in ids), needle
    ops = {"*** Add File:": 0, "*** Update File:": 0, "*** Delete File:": 0, "*** Move to:": 0}
    for f in _fixtures():
        c = json.loads(f.read_text(encoding="utf-8")).get("tool_input", {}).get("command", "")
        if json.loads(f.read_text(encoding="utf-8")).get("tool_name") == "apply_patch":
            for k in ops:
                ops[k] += k in c
    assert all(ops.values()), ops


def test_derived_payloads_differ_from_their_capture_only_in_the_replaced_text():
    """A derived payload is the captured one with the path/command edited: every other key is untouched."""
    n = 0
    for p in _derived():
        fixture, edit = p.values[0], p.values[1]
        orig = json.loads(fixture.read_text(encoding="utf-8"))
        orig["cwd"] = "/x"
        mod = json.loads(json.dumps(orig))
        edit(mod)
        assert set(mod) == set(orig) and set(mod["tool_input"]) == set(orig["tool_input"]), p.id
        assert {k: v for k, v in mod.items() if k != "tool_input"} == {k: v for k, v in orig.items() if k != "tool_input"}
        assert mod["tool_input"] != orig["tool_input"], p.id
        n += 1
    assert n >= 30, n


def test_the_shape_check_can_fail():
    """The shape gate refuses what Codex would let run: an ask, an allow, an unknown key, two lines."""
    ok = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                 "permissionDecisionReason": "r"}}
    assert _check_shape("pre-tool", json.dumps(ok).encode() + b"\n", "x") == "deny"
    for bad in (
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                "permissionDecisionReason": "r"}},
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                "permissionDecisionReason": "r"}},
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                "permissionDecisionReason": "r", "extra": 1}},
        {"decision": "block"},
    ):
        with pytest.raises(AssertionError):
            _check_shape("pre-tool", json.dumps(bad).encode() + b"\n", "x")
