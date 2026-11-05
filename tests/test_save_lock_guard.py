"""User ruling 2026-10-31: save locking is optional and only the USER switches it off. The agent may read the switch
(.modding-agent-harness/save-lock.json) but never write, move or delete it, by any route the guard reads; while
locking is on the OS refuses those too, and `save-lock off` needs an elevated prompt.

Through the real hook entries: run.py in process (record.fire_inprocess; HOME is the scratch install). Nothing is
executed; the switch here is a scratch file."""
import importlib.util
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import engine  # noqa: E402


def _rec():
    spec = importlib.util.spec_from_file_location("record", REPO / "scripts" / "guard-corpus" / "record.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def claude(tmp_path_factory):
    rec = _rec()
    install = rec.make_install(tmp_path_factory.mktemp("sl"), REPO / ".claude" / "hooks", "saves-bulk")
    switch = install / ".modding-agent-harness" / "save-lock.json"
    switch.parent.mkdir(parents=True, exist_ok=True)
    switch.write_text('{"enabled": true}\n', encoding="utf-8")
    return rec, install, switch


def fire(claude, hook, tool_input, tool="Bash"):
    rec, install, _ = claude
    payload = {"tool_name": tool, "session_id": "s", "cwd": install.as_posix(), "tool_input": tool_input}
    return rec.fire_inprocess(install, hook, payload)


SHELL_WRITES = {
    "overwrite": "echo '{{\"enabled\": false}}' > \"{s}\"",
    "copy-onto": "cp /tmp/off.json \"{s}\"",
    "move-away": "mv \"{s}\" /tmp/x.json",
    "delete": "rm \"{s}\"",
    "relative-delete": "rm .modding-agent-harness/save-lock.json",
    "sed-i": "sed -b -i s/true/false/ \"{s}\"",
}


@pytest.mark.parametrize("shape", sorted(SHELL_WRITES))
def test_a_shell_change_to_the_switch_is_refused(claude, shape):
    _, _, switch = claude
    got = fire(claude, "protect-bash.sh", {"command": SHELL_WRITES[shape].format(s=switch.as_posix())})
    assert got["decision"] == "deny" and "S-SAVE-LOCK" in got["rules"], got


def test_a_powershell_change_to_the_switch_is_refused(claude):
    _, _, switch = claude
    got = fire(claude, "protect-bash.sh", {"command": "Set-Content -Path '{}' -Value x".format(switch),
                                           "shell": "powershell"}, tool="PowerShell")
    assert got["decision"] == "deny" and "S-SAVE-LOCK" in got["rules"], got


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_a_file_tool_write_to_the_switch_is_refused(claude, tool):
    _, _, switch = claude
    got = fire(claude, "protect-files.sh", {"file_path": str(switch), "content": "{}", "old_string": "true",
                                            "new_string": "false"}, tool=tool)
    assert got["decision"] == "deny" and "W-SAVE-LOCK" in got["rules"], got


@pytest.mark.parametrize("command", ["cat \"{s}\"", "grep enabled \"{s}\"", "cp \"{s}\" /tmp/copy.json"])
def test_reading_or_copying_the_switch_out_is_free(claude, command):
    _, _, switch = claude
    got = fire(claude, "protect-bash.sh", {"command": command.format(s=switch.as_posix())})
    assert "S-SAVE-LOCK" not in got["rules"], got


def test_a_same_named_file_elsewhere_is_not_the_switch(claude):
    _, install, _ = claude
    got = fire(claude, "protect-bash.sh", {"command": "rm \"{}\"".format((install / "notes" / "save-lock.json")
                                                                        .as_posix())})
    assert "S-SAVE-LOCK" not in got["rules"], got


def test_guards_off_never_lifts_the_switch_refusal():
    assert {"shell.S-SAVE-LOCK", "write.W-SAVE-LOCK"} <= engine.NEVER_SHADOW
