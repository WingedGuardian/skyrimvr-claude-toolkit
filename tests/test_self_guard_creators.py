"""Plan 8 T8 fix round 1: self-protection routes the T8 review found open (MEASURED there, allow on every route).

  I1  a create-without-content program whose target is a guard file: `touch`, `install`'s destination, cmd `mklink`'s
      link name. Before T8 a touched paths file declared nothing new; with two locations an empty root file used to hide
      the legacy file's roots (paths.read_install now unions them -- tests/test_paths_location.py), and an empty hook
      still fails open (red team F1).
  P1  an NTFS stream spelling of a guard file (`x::$DATA` is x's own data): resolved the way the plugin rules read it
      (targets.STREAM), so `$DATA` no longer makes the operand unresolvable.
  P2  a LINK whose SOURCE is a guard file (`ln`, `ln -s`, `mklink [/H]`, New-Item -ItemType HardLink|SymbolicLink): the
      alias outside the install then edits the guard with no protected path in any later command. Priced as the
      plugin rules price a link: they do not refuse a plugin link, so this is the GUARD class's note (and STATE's deny).
Fired in-process through shell_scan / write_rules with the rule table's roots, never recorded from the engine."""
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, paths, self_rules, shellparse, write_rules  # noqa: E402

INST = "C:/p8t8/Skyrim VR"
ENV0 = {"USERPROFILE": "C:/p8t8-nohome", "HOME": "C:/p8t8-nohome"}


def _roots():
    return paths.roots(INST, "", {})


def _full(command, shell="bash", cwd=INST):
    return self_rules.shell_scan(command, shellparse.parse(command, shell), _roots(), cwd, ENV0)


def _scan(command, shell="bash", cwd=INST):
    """(class, basename): what the route reaches. The verb is _full(...)[2]."""
    got = _full(command, shell, cwd)
    return got[:2] if got else None


def _audit():
    a = audit.Audit("")
    a.raw = lambda line: None
    return a


# ---- I1: create-without-content programs ----------------------------------------------------------------------------

@pytest.mark.parametrize("command,name", [
    ("touch skyrim-paths.env", "skyrim-paths.env"),
    ("touch '{}/SKYRIM-PATHS.ENV'".format(INST), "SKYRIM-PATHS.ENV"),
    ("touch -d yesterday -- .claude/hooks/protect-bash.sh", "protect-bash.sh"),
    ("touch notes.txt .claude/skyrim-paths.env", "skyrim-paths.env"),
    ("install -m644 /dev/null skyrim-paths.env", "skyrim-paths.env"),
    ("install -m 644 -o me /dev/null '{}/skyrim-paths.env'".format(INST), "skyrim-paths.env"),
    ("install -t .claude/hooks /tmp/protect-bash.sh", "protect-bash.sh"),
    ("install -d skyrim-paths.env", "skyrim-paths.env"),
    ("cmd //c mklink skyrim-paths.env C:\\\\x", "skyrim-paths.env"),
    ("cmd //c mklink /D .claude\\\\hooks C:\\\\x", "hooks"),
], ids=["touch", "touch-abs-upper", "touch-valued-option", "touch-second-operand", "install", "install-valued",
        "install-target-dir", "install-d", "mklink", "mklink-dir"])
def test_a_creator_aimed_at_a_guard_file_is_a_guard_write(command, name):
    assert _scan(command) == (self_rules.GUARD, name)


@pytest.mark.parametrize("command", ["touch notes.txt", "touch -r skyrim-paths.env notes.txt",
                                     "install -m644 skyrim-paths.env /tmp/copy.env",
                                     "cmd //c mklink C:\\\\Temp\\\\a C:\\\\Temp\\\\b",
                                     "touch -d skyrim-paths.env notes.txt"],
                         ids=["plain", "reference-is-read", "install-source-is-read", "mklink-outside",
                              "date-value-is-no-operand"])
def test_a_creator_elsewhere_is_not(command):
    assert _scan(command) is None


@pytest.mark.parametrize("tool", ["Bash", "exec_command"])
def test_codex_touch_of_the_root_file_gets_the_note(tool, tmp_path):
    """Both agents: the Codex pre-tool route reaches the same shell rules."""
    import json
    from skyguard import engine
    from skyguard.adapters import codex
    raw = json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": INST,
                      "tool_input": {"command": "touch skyrim-paths.env"}})
    v = engine.pre_tool(codex.to_request(raw, "win32"), str(tmp_path), _roots, audit.Audit(str(tmp_path)))
    assert "skyrim-paths.env" in (v.context or v.reason) and "safety guards" in (v.context or v.reason), v


def test_touching_the_state_is_a_state_write():
    assert _scan("touch .skyguard/lock.json") == (self_rules.STATE, "lock.json")


def test_powershell_touch_alias_free_new_item_is_already_covered():
    assert _scan("New-Item -ItemType File -Path skyrim-paths.env", "powershell") == (self_rules.GUARD,
                                                                                      "skyrim-paths.env")


# ---- P1: NTFS stream spellings ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("word", ["skyrim-paths.env::$DATA", ".claude/hooks/protect-bash.sh::$DATA",
                                  ".claude/skyrim-paths.env:s", "skyrim-paths.env:$x:$DATA"],
                         ids=["root-data", "hook-data", "legacy-named-stream", "stream-with-dollar"])
def test_a_stream_of_a_guard_file_is_the_guard_file(word):
    got = _scan("echo x > '{}/{}'".format(INST, word))
    assert got is not None and got[0] == self_rules.GUARD, got


def test_a_stream_of_the_state_is_the_state():
    assert _scan("echo x > '{}/.skyguard/state.json::$DATA'".format(INST)) == (self_rules.STATE, "state.json")


def test_a_variable_before_the_stream_stays_unresolved():
    """Only the stream suffix is dropped: `$X/...` is still a word the resolver cannot place (GUARD: no note)."""
    assert _scan("echo x > \"$X/skyrim-paths.env::\\$DATA\"") is None


def test_the_write_tool_on_a_guard_file_stream_gets_the_note():
    v = write_rules.decide(INST + "/skyrim-paths.env::$DATA", _audit(), roots=_roots(), cwd=INST, env=ENV0)
    assert v.decision == "allow" and "skyrim-paths.env" in v.context and "safety guards" in v.context, v


def test_the_write_tool_on_a_state_stream_is_refused():
    v = write_rules.decide(INST + "/.skyguard/state.json::$DATA", _audit(), roots=_roots(), cwd=INST, env=ENV0)
    assert v.decision == "deny", v


def test_a_drive_relative_word_is_not_read_as_a_stream():
    assert self_rules.strip_stream("C:skyrim-paths.env") is None
    assert self_rules.strip_stream("C:") is None
    assert self_rules.strip_stream("a/b") is None
    assert self_rules.strip_stream("C:/x/skyrim-paths.env::$DATA") == "C:/x/skyrim-paths.env"


# ---- P2: a link whose source is a guard file --------------------------------------------------------------------------

@pytest.mark.parametrize("command,shell", [
    ("ln skyrim-paths.env /tmp/alias", "bash"),
    ("ln -s '{}/.claude/hooks/protect-bash.sh' /tmp/alias".format(INST), "bash"),
    ("ln -f .claude/settings.json /tmp/a /tmp/b", "bash"),
    ("cmd //c mklink /H C:\\\\Temp\\\\alias skyrim-paths.env", "bash"),
    ("cmd //c mklink C:\\\\Temp\\\\alias .claude\\\\skyrim-paths.env", "bash"),
    ("New-Item -ItemType HardLink -Path C:/Temp/alias -Target skyrim-paths.env", "powershell"),
    ("New-Item -ItemType SymbolicLink -Path C:/Temp/alias -Value .claude/hooks/protect-bash.sh", "powershell"),
], ids=["ln-hard", "ln-symbolic", "ln-forced", "mklink-hard", "mklink-symbolic", "new-item-hardlink",
        "new-item-symlink-value"])
def test_a_link_to_a_guard_file_is_a_guard_write(command, shell):
    got = _scan(command, shell)
    assert got is not None and got[0] == self_rules.GUARD, got


def test_a_hard_link_to_the_state_is_refused_as_state():
    assert _scan("ln .skyguard/state.json /tmp/alias") == (self_rules.STATE, "state.json")


@pytest.mark.parametrize("command,shell", [
    ("ln -s /tmp/a /tmp/b", "bash"),
    ("ln Data/x.esp /tmp/alias", "bash"),
    ("New-Item -ItemType File -Path C:/Temp/x -Value skyrim-paths.env", "powershell"),
    ("cmd //c mklink /J C:\\\\Temp\\\\j C:\\\\Temp\\\\k", "bash"),
], ids=["outside", "not-a-guard-file", "new-item-file-value-is-content", "junction-outside"])
def test_a_link_elsewhere_is_not(command, shell):
    assert _scan(command, shell) is None


# ---- fix round 2 (re-review N2): more link and creator spellings -------------------------------------------------

@pytest.mark.parametrize("command,shell", [
    ("cp -l skyrim-paths.env /tmp/alias", "bash"),
    ("cp --link .claude/hooks/protect-bash.sh /tmp/alias", "bash"),
    ("cp -s '{}/skyrim-paths.env' /tmp/alias".format(INST), "bash"),
    ("cp --symbolic-link .claude/settings.json /tmp/alias", "bash"),
    ("cp -al .claude /tmp/aliasdir", "bash"),
    ("fsutil hardlink create 'C:\\Temp\\alias' skyrim-paths.env", "bash"),
    ("fsutil hardlink create C:\\Temp\\alias skyrim-paths.env", "powershell"),
    ("New-Item -Type HardLink -Path C:/Temp/alias -Target skyrim-paths.env", "powershell"),
    ("[IO.File]::CreateSymbolicLink('C:\\Temp\\alias', '{}/skyrim-paths.env')".format(INST), "powershell"),
    ("[System.IO.File]::CreateHardLink('C:\\Temp\\alias', '{}/.claude/skyrim-paths.env')".format(INST), "powershell"),
    ("[System.IO.Directory]::CreateSymbolicLink('C:\\Temp\\d', '{}/.claude/hooks')".format(INST), "powershell"),
], ids=["cp-l", "cp-link", "cp-s", "cp-symbolic-link", "cp-al-folder", "fsutil-bash", "fsutil-powershell",
        "new-item-type-alias", "dotnet-file-symlink", "dotnet-file-hardlink", "dotnet-directory-symlink"])
def test_more_link_spellings_to_a_guard_file_get_the_note(command, shell):
    got = _full(command, shell)
    assert got is not None and got[0] == self_rules.GUARD and got[2] == self_rules.LINKS, got


@pytest.mark.parametrize("command,shell", [
    ("cp -l .skyguard/state.json /tmp/alias", "bash"),
    ("fsutil hardlink create C:\\Temp\\alias .skyguard\\state.json", "powershell"),
], ids=["cp-l", "fsutil"])
def test_a_link_to_the_state_by_any_spelling_is_refused(command, shell):
    assert _scan(command, shell) == (self_rules.STATE, "state.json")


@pytest.mark.parametrize("command,shell", [
    ("cp skyrim-paths.env /tmp/backup.env", "bash"),
    ("cp -r Data/Scripts /tmp/scripts", "bash"),
    ("cp -l /tmp/a /tmp/b", "bash"),
    ("fsutil hardlink list skyrim-paths.env", "powershell"),
    ("New-Item -Type File -Path C:/Temp/x -Value skyrim-paths.env", "powershell"),
    ("[IO.File]::CreateSymbolicLink('C:\\Temp\\a', 'C:\\Temp\\b')", "powershell"),
], ids=["plain-copy-reads-it", "plain-recursive-copy", "link-outside", "fsutil-list", "new-item-type-file",
        "dotnet-outside"])
def test_a_plain_copy_or_an_outside_link_is_not(command, shell):
    assert _scan(command, shell) is None


def test_fsutil_creating_the_link_name_at_a_guard_path_creates_it():
    assert _full("fsutil hardlink create skyrim-paths.env C:\\Temp\\x", "powershell")[1:] == ("skyrim-paths.env",
                                                                                               self_rules.CREATES)


# ---- fix round 2 (re-review N4): the note's verb says what the command does --------------------------------------

@pytest.mark.parametrize("command,shell,op", [
    ("echo x > skyrim-paths.env", "bash", "edits"),
    ("touch skyrim-paths.env", "bash", "creates"),
    ("install -m644 /dev/null skyrim-paths.env", "bash", "edits"),
    ("cmd //c mklink skyrim-paths.env C:\\\\x", "bash", "creates"),
    ("ln skyrim-paths.env /tmp/alias", "bash", "makes a link to"),
    ("cmd //c mklink /H C:\\\\Temp\\\\alias skyrim-paths.env", "bash", "makes a link to"),
], ids=["redirect", "touch", "install", "mklink-name", "ln-source", "mklink-target"])
def test_the_note_says_what_the_command_does(command, shell, op):
    from skyguard import shell_rules
    v = shell_rules.decide(command, _roots(), _audit(), cwd=INST, shell=shell, env=ENV0)
    assert v.context == "Note: this command {} skyrim-paths.env, part of the safety guards themselves.".format(op), v
