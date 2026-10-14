"""Codex routes the routes red team (2026-10-04) found weaker than the toolkit's intent, as Codex corpus rows.

The guard corpus (tests/guard_corpus) fires only the Claude wrappers, so the Codex rows live here: each row is a
real Codex PreToolUse payload, judged by the engine's Codex role (engine.pre_tool) against a temp install with a
declared config dir, and compared on decision, rule ids (Audit.fired) and exact text -- the same three parts the
intended corpus compares. Rows were written from the rule table (rules.py) and agent/instructions/codex.md, never
recorded from the engine.
"""
import json
import sys
from pathlib import Path

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import engine, paths  # noqa: E402
from skyguard.adapters import codex  # noqa: E402
from skyguard.audit import Audit  # noqa: E402

DEL_INSTALL = ("Blocked: this deletes {} inside the Skyrim install.\n"
               "A mod manager cannot restore your own files there; if you mean it, run it yourself.")
DEL_CONFIG = ("Blocked: this deletes {} in the Skyrim settings folder.\n"
              "Your INIs and controlmap there cannot be restored by a mod manager.")
DEL_UNRESOLVED = ("Blocked: this deletes a path the guard cannot work out, in a command that names a protected Skyrim "
                  "folder.\nWrite the full path, or run it yourself.")
# The save policy (user ruling 2026-10-13): one existing save is backed up by the hook, then the call runs, on Codex too.
SAVE_NOTE = "Note: the guard backs up {} before this runs (restore: tools/skyguard.sh snapshot list --kind save)."
CRASH = ("Blocked: 'smp reset' crashes a large load order in the live game.\n"
         "Use a targeted command instead, or run it yourself.")
INERT = "GUARD INERT: the agent's hook could not read this call ({}), so it checked NOTHING. This is a refusal, not a verdict."


@pytest.fixture()
def inst(tmp_path):
    g = tmp_path / "Game"
    (g / "Data" / "meshes").mkdir(parents=True)
    (g / "Data" / "meshes" / "x.nif").write_bytes(b"n")
    (g / "Data" / "Plugin.esp").write_bytes(b"p")
    (g / "SkyrimVR.exe").write_bytes(b"e")
    cfg = tmp_path / "cfg"
    (cfg / "Saves").mkdir(parents=True)
    (cfg / "Saves" / "Existing.ess").write_bytes(b"s")
    (cfg / "Skyrim.ini").write_text("[x]\n")
    return g.as_posix(), cfg.as_posix()


def _judge(inst, tool, tool_input, cwd=None, platform="win32", payload=None):
    g, cfg = inst
    d = payload if payload is not None else {"hook_event_name": "PreToolUse", "tool_name": tool,
                                             "cwd": g if cwd is None else cwd, "tool_input": tool_input}
    req = codex.to_request(json.dumps(d), platform)
    audit = Audit(g)
    v = engine.pre_tool(req, g, lambda: paths.roots(g, "", {"SKYRIM_CONFIG_DIR": cfg}), audit)
    return v, {rid for rid, _ in audit.fired}


def _patch(*lines):
    return {"command": "\n".join(("*** Begin Patch",) + lines + ("*** End Patch",))}


def _patch_under(key, *lines):
    return {key: _patch(*lines)["command"]}


def _sub(inst, text):
    return text.replace("<install>", inst[0]).replace("<config>", inst[1])


# (id, tool, tool_input, cwd ("<install>" default), decision, rule ids, text) -- the Codex corpus rows.
ROWS = [
    # F2 (codex red team) / routes s147-s190: an apply_patch Delete in the game or config folder is a delete.
    ("rt-codex-patch-delete-install", "apply_patch", _patch("*** Delete File: Data/meshes/x.nif"), None,
     "deny", {"S-DEL-INSTALL"}, DEL_INSTALL.format("x.nif")),
    ("rt-codex-patch-delete-exe", "apply_patch", _patch("*** Delete File: <install>/SkyrimVR.exe"), None,
     "deny", {"S-DEL-INSTALL"}, DEL_INSTALL.format("SkyrimVR.exe")),
    ("rt-codex-patch-delete-backslash", "apply_patch", _patch("*** Delete File: <install>\\SkyrimVR.exe"), None,
     "deny", {"S-DEL-INSTALL"}, DEL_INSTALL.format("SkyrimVR.exe")),
    ("rt-codex-patch-delete-verbatim", "apply_patch", _patch("*** Delete File: //?/<install>/SkyrimVR.exe"), None,
     "deny", {"S-DEL-INSTALL"}, DEL_INSTALL.format("SkyrimVR.exe")),
    ("rt-codex-patch-delete-ini", "apply_patch", _patch("*** Delete File: <config>/Skyrim.ini"), None,
     "deny", {"S-DEL-CONFIG"}, DEL_CONFIG.format("Skyrim.ini")),
    ("rt-codex-patch-delete-save", "apply_patch", _patch("*** Delete File: <config>/Saves/Existing.ess"), None,
     "allow", {"S-SAVE-BACKUP"}, SAVE_NOTE.format("Existing")),
    ("rt-codex-patch-delete-dot", "apply_patch", _patch("*** Delete File: ."), None,
     "deny", {"S-DEL-INSTALL"}, DEL_INSTALL.format("Game")),
    ("rt-codex-patch-delete-dir", "apply_patch", _patch("*** Delete File: Data"), None,
     "deny", {"S-DEL-INSTALL"}, DEL_INSTALL.format("Data")),
    ("rt-codex-patch-delete-plugin", "apply_patch", _patch("*** Delete File: Data/Plugin.esp"), None,
     "deny", {"S-DEL-INSTALL"}, DEL_INSTALL.format("Plugin.esp")),
    # A literal `$HOME` is a folder name to apply_patch (no shell expands it): inside the install here.
    ("rt-codex-patch-delete-literal-var", "apply_patch", _patch("*** Delete File: $HOME/x"), None,
     "deny", {"S-DEL-INSTALL"}, DEL_INSTALL.format("x")),
    # p21: a Move takes the file OUT of the install, deleting it there.
    ("rt-codex-patch-move-out", "apply_patch",
     _patch("*** Update File: Data/meshes/x.nif", "*** Move to: C:/Temp/rtB/stash.nif", "@@", "-n", "+n"), None,
     "deny", {"S-DEL-INSTALL"}, DEL_INSTALL.format("x.nif")),
    # The shell-heredoc form reaches the same judgement (s191).
    ("rt-codex-shell-patch-delete", "Bash",
     {"command": "apply_patch <<'EOF'\n*** Begin Patch\n*** Delete File: <install>/SkyrimVR.exe\n*** End Patch\nEOF"},
     None, "deny", {"S-DEL-INSTALL"}, DEL_INSTALL.format("SkyrimVR.exe")),
    # s197/s199: other spellings of the shell form run the same program (Windows: case-insensitive PATH lookup).
    ("rt-codex-shell-patch-upper", "Bash",
     {"command": "APPLY_PATCH <<'EOF'\n*** Begin Patch\n*** Delete File: Data/Plugin.esp\n*** End Patch\nEOF"},
     None, "deny", {"S-DEL-INSTALL"}, DEL_INSTALL.format("Plugin.esp")),
    ("rt-codex-shell-patch-quoted", "Bash",
     {"command": "'apply_patch' <<'EOF'\n*** Begin Patch\n*** Delete File: Data/Plugin.esp\n*** End Patch\nEOF"},
     None, "deny", {"S-DEL-INSTALL"}, DEL_INSTALL.format("Plugin.esp")),
    # s200/s202: a patch body beside a spelling the parser cannot attribute is refused, never read as plain shell.
    ("rt-codex-shell-patch-variable", "Bash",
     {"command": "a=apply_patch; $a <<'EOF'\n*** Begin Patch\n*** Delete File: Data/Plugin.esp\n*** End Patch\nEOF"},
     None, "deny", set(),
     INERT.format("unreadable patch: a patch body beside apply_patch that this parser cannot attribute")),
    ("rt-codex-shell-patch-spaced-path", "Bash",
     {"command": "\"C:/Program Files/codex/apply_patch.exe\" <<'EOF'\n*** Begin Patch\n*** Delete File: "
                 "Data/Plugin.esp\n*** End Patch\nEOF"},
     None, "deny", set(),
     INERT.format("unreadable patch: a patch body beside apply_patch that this parser cannot attribute")),
    # Codex tool shapes (codex red team F3a/F3b, routes s125-s128): keystrokes typed into a live shell and a command
    # under unified exec's `cmd` key are shell commands.
    ("rt-codex-write-stdin", "write_stdin",
     {"session_id": 1, "chars": "Remove-Item -Recurse -Force \"<install>/Data/meshes\"\n"}, None,
     "deny", {"S-DEL-INSTALL"}, DEL_INSTALL.format("meshes")),
    ("rt-codex-unknown-cmd-key", "exec_command", {"cmd": "rm -rf <install>/Data"}, None,
     "deny", {"S-DEL-INSTALL"}, DEL_INSTALL.format("Data")),
    ("rt-codex-write-stdin-enter", "write_stdin", {"session_id": 1, "chars": "\n"}, None, "allow", set(), ""),
    ("rt-codex-write-stdin-poll", "write_stdin", {"session_id": 1, "chars": ""}, None, "allow", set(), ""),
    ("rt-codex-write-stdin-answer", "write_stdin", {"session_id": 1, "chars": "y\n"}, None, "allow", set(), ""),
    # m35: a case variant of the patch tool is still a patch.
    ("rt-codex-patch-case", "Apply_Patch", _patch("*** Delete File: Data/Plugin.esp"), None,
     "deny", {"S-DEL-INSTALL"}, DEL_INSTALL.format("Plugin.esp")),
    # A patch under an unknown key of an unknown tool.
    ("rt-codex-unknown-patch-key", "edit_files", _patch_under("input", "*** Delete File: Data/Plugin.esp"), None,
     "deny", {"S-DEL-INSTALL"}, DEL_INSTALL.format("Plugin.esp")),
    # s132-s144: the shell call's own `workdir` is where its relative paths land.
    ("rt-codex-workdir", "Bash", {"command": "rm -rf Data", "workdir": "<install>"}, "C:/Temp/rtB",
     "deny", {"S-DEL-INSTALL"}, DEL_INSTALL.format("Data")),
    ("rt-codex-workdir-outside", "Bash", {"command": "rm -rf x", "workdir": "C:/Temp/rtB"}, "<install>/Data",
     "allow", set(), ""),
    # A Delete of a path the guard cannot place (an 8.3 short name) that reaches the install.
    ("rt-codex-patch-delete-unresolved", "apply_patch", _patch("*** Delete File: <install>/DATA~1/x.esp"), None,
     "deny", {"S-DEL-UNRESOLVED"}, DEL_UNRESOLVED),
    # A patch that adds (replaces) a save in the settings folder: backed up first, then allowed (was W-SAVE's ask).
    ("rt-codex-patch-add-save", "apply_patch", _patch("*** Add File: <config>/Saves/Existing.ess", "+x"), None,
     "allow", {"S-SAVE-BACKUP"}, SAVE_NOTE.format("Existing")),
    # rt-fix-b (routes s115/s116): the DevBench judge Claude's protect-mcp uses, on Codex too.
    ("rt-codex-mcp-crash", "mcp__devbench-vr__console", {"command": "smp reset"}, None,
     "deny", {"S-DEVBENCH-CRASH"}, CRASH),
    ("rt-codex-mcp-list-input", "mcp__devbench-vr__console", ["smp reset"], None,
     "deny", {"S-DEVBENCH-CRASH"}, CRASH),
    ("rt-codex-mcp-save", "mcp__devbench-vr__game", {"action": "save", "name": "Existing"}, None,
     "allow", {"S-SAVE-BACKUP"}, SAVE_NOTE.format("Existing")),
    ("rt-codex-mcp-other-server", "mcp__probe__echo", {"text": "smp reset"}, None, "allow", set(), ""),
    ("rt-codex-mcp-help", "mcp__devbench-vr__console", {"command": "help"}, None, "allow", set(), ""),
    # Plan 8 T6 (rt-fix-b concern 4, routes harness s130): a command under `script` was never read, so the call was
    # allowed unexamined. It is command text now; a call carrying two command fields is refused (one would go unread).
    ("rt-codex-script-key", "Bash", {"script": "rm -rf <install>/Data"}, None,
     "deny", {"S-DEL-INSTALL"}, DEL_INSTALL.format("Data")),
    ("rt-codex-script-key-harmless", "Bash", {"script": "Get-Date"}, None, "allow", set(), ""),
    ("rt-codex-two-command-keys", "Bash", {"command": "Get-Date", "script": "rm -rf <install>/Data"}, None,
     "deny", set(), INERT.format("tool call with more than one command field (command, script)")),
    # Plan 8 T6 (T4 audit K6): Codex's shell is PowerShell, where `find` was read as find.exe and allowed.
    ("rt-codex-find-delete", "Bash", {"command": "find <install>/Data -name *.bak -delete"}, None,
     "deny", {"S-DEL-INSTALL"}, DEL_INSTALL.format("*.bak")),
    # Twins that must stay allowed: deleting outside the protected folders, a rename inside one, a scratch dir.
    ("rt-codex-patch-delete-outside", "apply_patch", _patch("*** Delete File: C:/Temp/rtB/x.txt"), None,
     "allow", set(), ""),
    ("rt-codex-patch-delete-pycache", "apply_patch", _patch("*** Delete File: notes/__pycache__/a.pyc"), None,
     "allow", set(), ""),
]


def test_a_move_inside_the_same_folder_is_a_rename_not_a_delete(inst):
    ti = _patch("*** Update File: Data/meshes/x.nif", "*** Move to: Data/meshes/y.nif", "@@", "-n", "+n")
    v, fired = _judge(inst, "apply_patch", ti)
    assert v.decision == "allow" and fired == {"W-INSTALL"}, (v, fired)


def test_a_patch_delete_is_audited_under_its_rules_own_hook(inst):
    """Plan 8 T6 (rt-fix-b concern 6): an apply_patch Delete is judged by the shell delete rules (S-DEL-*, registry
    hook protect-bash, code namespace bash.*); its audit line said protect-files, so the log and the verdict code
    named two different guards."""
    v, fired = _judge(inst, "apply_patch", _patch("*** Delete File: Data/meshes/x.nif"))
    assert fired == {"S-DEL-INSTALL"}
    log = (Path(inst[0]) / ".claude" / "backups" / "AUDIT_LOG.txt").read_text(encoding="utf-8")
    assert " protect-bash DENY: " + DEL_INSTALL.format("x.nif").split("\n")[0] in log, log
    assert " protect-files DENY:" not in log, log


def test_a_patch_delete_refuses_when_the_paths_file_is_unreadable(inst, tmp_path):
    g = inst[0]
    req = codex.to_request(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "cwd": g,
                                       "tool_input": _patch("*** Delete File: Data/meshes/x.nif")}), "win32")

    def unreadable():
        raise engine.RootsUnavailable("GUARD INERT: unreadable paths file")
    v = engine.pre_tool(req, g, unreadable, Audit(g))
    assert (v.decision, v.status) == ("deny", "failed") and "GUARD INERT" in v.reason
    # An Add/Update still only loses its advice (the soft roots), as before.
    req = codex.to_request(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "cwd": g,
                                       "tool_input": _patch("*** Add File: notes/a.txt", "+x")}), "win32")
    assert engine.pre_tool(req, g, unreadable, Audit(g)).decision == "allow"


def test_a_patch_delete_refuses_when_the_paths_file_has_an_unreadable_value(inst):
    """A Delete is judged by a deny rule, so a malformed protection key refuses it, as it refuses a shell delete."""
    g, cfg = inst
    req = codex.to_request(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "cwd": g,
                                       "tool_input": _patch("*** Delete File: C:/Temp/rtB/x.txt")}), "win32")
    roots = paths.roots(g, "", {"SKYRIM_CONFIG_DIR": cfg})._replace(
        malformed=(("skyrim-paths.env", "SKYRIM_MO2_MODS"),))
    v = engine.pre_tool(req, g, lambda: roots, Audit(g))
    assert (v.decision, v.status, v.reason) == (
        "deny", "failed", "GUARD INERT: skyrim-paths.env has an unreadable value for SKYRIM_MO2_MODS, so a protected "
                          "folder is unknown.\nRe-run setup.sh to rewrite it.")


@pytest.mark.parametrize("payload, why", [
    # F4: Codex always sends cwd; without one a relative delete would pass as a note.
    ({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "Remove-Item -Recurse Data"}},
     "payload has no cwd"),
    ({"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": ["C:/x"],
      "tool_input": {"command": "Remove-Item -Recurse Data"}}, "payload has no cwd"),
    # F3b m25: a bare-string tool_input.
    ({"hook_event_name": "PreToolUse", "tool_name": "exec_command", "cwd": "C:/x", "tool_input": "rm -rf Data"},
     "tool_input is not an object"),
])
def test_a_malformed_codex_payload_is_refused_with_its_reason(inst, payload, why):
    v, fired = _judge(inst, None, None, payload=payload)
    assert (v.decision, v.status, v.reason) == ("deny", "failed", INERT.format(why))


def test_deep_nesting_is_refused_by_name_not_by_a_crash(inst):
    raw = '{"hook_event_name": "PreToolUse", "cwd": "C:/x", "tool_name": "Bash", "x": ' + "[" * 100000 + "]" * 100000 + "}"
    assert codex.to_request(raw, "win32").failure == "payload nested too deeply"


# Linux CI run 38187755448: rows whose input only a Windows host can produce. Off Windows the install is a POSIX path,
# and these splice it into a Windows-only spelling. Their POSIX re-spellings are covered by tests/test_posix_host_paths.py.
WINDOWS_ONLY_ROWS = {
    "rt-codex-patch-delete-verbatim": "the //?/ verbatim prefix is a Win32 namespace; on POSIX `//?/` is a folder named ?",
    "rt-codex-workdir": "a POSIX workdir under a drive-letter cwd (C:/Temp/rtB); py3.13 ntpath joins it onto C:",
}


@pytest.mark.parametrize("row", ROWS, ids=[r[0] for r in ROWS])
def test_codex_route_rows(inst, row):
    rid, tool, ti, cwd, decision, rule_ids, text = row
    if sys.platform != "win32" and rid in WINDOWS_ONLY_ROWS:
        pytest.skip(WINDOWS_ONLY_ROWS[rid])
    ti = json.loads(_sub(inst, json.dumps(ti)))
    v, fired = _judge(inst, tool, ti, cwd=None if cwd is None else _sub(inst, cwd))
    shown = v.reason if v.decision in ("deny", "ask") else v.context
    assert (v.decision, fired, shown) == (decision, rule_ids, text)
