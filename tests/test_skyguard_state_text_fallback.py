"""Plan 6 final fix pass (lane A I1): S-SELF-STATE reads the TEXT when the parser cannot read the command.

bash runs the lines before a syntax error (MEASURED by the reviewer: line 1 wrote its file, then bash exited 2 on an
unclosed quote in line 2). The plugin-write and lift rules already fall back to the raw text when the parse fails or a
program is opaque; the state rule did not, so `echo '<forged json>' > .skyguard/state.json` followed by a line with an
unclosed quote was ALLOWED -- a forged persistent disable with no prompt and no `guards off` needed. The fallback's
only cost is a false positive on an unreadable command that mentions `.skyguard` beside a write construct.
"""
import sys
import tempfile

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, codes, engine, paths, shell_rules, shellparse, state  # noqa: E402

GAME = "C:/GOG Games/The Elder Scrolls V Skyrim VR"
NOHOME = tempfile.mkdtemp(prefix="p6fixA-i1-")
ENV0 = {"USERPROFILE": NOHOME, "HOME": NOHOME}
SG = GAME + "/.skyguard"
H = 3600.0
NOW = 1_800_000_000.0
SID = "sess-p6fixA-i1"


def _decide(command, shell_name="bash"):
    a = audit.Audit("")
    a.raw = lambda line: None
    r = paths.roots("C:/p6fixA/Skyrim VR", "", {"SKYRIM_GAME_ROOT": GAME})
    v = codes.classify(shell_rules.decide(command, r, a, cwd=GAME, shell=shell_name, env=ENV0))
    return v, {rid for rid, _ in a.fired}


REFUSED = [
    ("unclosed-quote-redirect", "echo '{\"v\":1}' > \"" + SG + "/state.json\"\necho \"x"),
    ("unterminated-heredoc-cp", "cp /tmp/f.json \"" + SG + "/state.json\"\ncat <<EOF\nnever closed"),
    ("relative-tee-unclosed", "printf x | tee .skyguard/lock.json\necho 'x"),
    ("delete-lock-unclosed", "rm -f \"" + SG + "/lock.json\"\necho \"x"),
    ("powershell-set-content-unclosed", "Set-Content -Path '" + SG + "/state.json' -Value x\nWrite-Host \"x"),
]


@pytest.mark.parametrize("cid,command", REFUSED, ids=[c[0] for c in REFUSED])
def test_an_unparseable_write_into_the_state_is_s_self_state(cid, command):
    assert not shellparse.parse(command, "bash").ok, "the probe must be one the parser cannot read"
    v, fired = _decide(command)
    assert (v.decision, v.code) == ("deny", "shell.S-SELF-STATE"), (v.decision, v.code, v.reason)
    assert "S-SELF-STATE" in fired


def test_the_same_write_that_parses_is_still_s_self_state():
    """The control: the parsed route already refused it (so the tests above are about the fallback)."""
    v, _ = _decide("echo x > \"" + SG + "/state.json\"")
    assert (v.decision, v.code) == ("deny", "shell.S-SELF-STATE")


ALLOWED = [
    ("read-only-unclosed", "cat \"" + SG + "/state.json\"\necho \"x"),
    ("write-elsewhere-unclosed", "echo x > C:/work/out.txt\necho \"x"),
    ("write-and-mention-in-another-line", "ls .skyguard\necho x > C:/work/out.txt\necho \"x"),
    ("skyguard-cli-unclosed", "bash tools/skyguard.sh guards status\necho \"x"),
]


@pytest.mark.parametrize("cid,command", ALLOWED, ids=[c[0] for c in ALLOWED])
def test_an_unparseable_command_that_writes_no_state_is_not_s_self_state(cid, command):
    """The twin: the fallback reads one statement at a time, and needs BOTH a `.skyguard` path and a write construct."""
    v, fired = _decide(command)
    assert "S-SELF-STATE" not in fired, (cid, v.decision, v.code)


def test_the_fallback_is_never_shadowed(tmp_path):
    project, hb = tmp_path / "proj", tmp_path / "hb"
    project.mkdir()
    state.write(str(project / ".skyguard" / "state.json"), {"v": 1, "persistent": {}, "lifts": [
        {"kind": "guards", "reason": "t", "session_id": SID, "agent": "claude", "created": NOW - H,
         "expires_at": NOW + H}]})
    f = hb / "sessions" / (SID + ".log")
    f.parent.mkdir(parents=True)
    f.write_text("{:.6f} start startup t.jsonl\n".format(NOW - 2 * H), encoding="utf-8")
    v, _ = _decide(REFUSED[0][1])
    out = engine.shadow(v, str(project), SID, str(hb), audit.Audit(str(project)), now=NOW)
    assert (out.decision, out.code) == ("deny", "shell.S-SELF-STATE")
