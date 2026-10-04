"""Plan 5 Task 9: every advice fires on the executed program; 2-line messages; the write-guard notes by zone.

Two halves, as in test_skyguard_writes:
  - every hand-authored t09 intended row, fired IN-PROCESS (decision, rule trace, exact text), parametrized by row id
    so a mutation can name the one row it must turn red;
  - the helpers, one behaviour per test.
"""
import json
import os
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import anchored, audit, paths, shell_rules, shellparse, write_rules, writes  # noqa: E402

GAME = "C:/GOG Games/The Elder Scrolls V Skyrim VR"
INSTALL = "C:/t7/Skyrim VR"           # stands in for the corpus's temp install (`<install>`)
LAYOUT_ENV = {
    "declared": {"SKYRIM_GAME_ROOT": GAME},
    "config": {"SKYRIM_GAME_ROOT": GAME, "SKYRIM_CONFIG_DIR": "D:/Profiles/MO2/profiles/Default"},
    "mo2": {"SKYRIM_GAME_ROOT": GAME, "SKYRIM_MO2_MODS": "D:/MO2/mods", "SKYRIM_MO2_OVERWRITE": "D:/MO2/overwrite"},
}
SHELL = {"Bash": "bash", "PowerShell": "powershell"}
BS = chr(92)


def _rows():
    text = (REPO / "tests" / "guard_corpus" / "intended.jsonl").read_text(encoding="utf-8")
    return [r for r in (json.loads(x) for x in text.split("\n") if x.strip()) if r["task"] == "T9"]


ROWS = _rows()


def _audit():
    a = audit.Audit("")
    a.raw = lambda line: None                             # no audit log on disk from a unit test
    return a


def _got(v):
    if v.decision == "allow":
        return ("advise", v.context) if v.context else ("allow", "")
    return (v.decision, v.reason)


def decide(command, layout="declared", cwd="", shell="bash"):
    a = _audit()
    v = shell_rules.decide(command, paths.roots(INSTALL, "", LAYOUT_ENV[layout]), a, cwd=cwd, shell=shell,
                           env=os.environ)
    return _got(v), {rid for rid, _ in a.fired}


def decide_file(path, layout="declared", cwd=""):
    a = _audit()
    v = write_rules.decide(path, a, roots=paths.roots(INSTALL, "", LAYOUT_ENV[layout]), cwd=cwd, env=os.environ)
    return _got(v), {rid for rid, _ in a.fired}


def test_there_are_t09_rows():
    assert len(ROWS) >= 6, len(ROWS)


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_t09_row(row):
    p = row["payload"]
    fill = (lambda s: s.replace("<install>", INSTALL)) if row.get("payload_note") == "install-relative" else (lambda s: s)
    if row["hook"] == "protect-files.sh":
        got, fired = decide_file(fill(p["tool_input"]["file_path"]), row["layout"], fill(p.get("cwd", "")))
    else:
        got, fired = decide(fill(p["tool_input"]["command"]), row["layout"], fill(p.get("cwd", "")),
                            SHELL[p["tool_name"]])
    assert got == (row["decision"], row["text"])
    assert fired == set(row["rules"])


# ------------------------------------------------------------------------------------------- item 0: writes.py minors
@pytest.mark.parametrize("text,want", [
    ("print(1 if a>b else 'x.esp')", []),
    ("x > X.esp", [("plugin", "X.esp")]),
    ("x >> 'C:/a b/X.esp'", [("plugin", "C:/a b/X.esp")]),
    ('x >"Data/X.esp"', [("plugin", "Data/X.esp")]),
    ("x 2>&1 X.esp", []),
    ("x | tee -a X.esp", [("plugin", "X.esp")]),
    ("x | tee out.txt X.esp", [("plugin", "X.esp")]),       # tee writes every operand it is given
    ("xs.map(x => 'a.esp')", []),                            # an arrow, not a redirect
    ("$a -> 'a.esp'", []),
    ("Set-Content -Value 1 -Path X.esp", [("plugin", "X.esp")]),
    ("a>b; c > X.esp", [("plugin", "X.esp")]),
    ("a>b c > X.esp", [("plugin", "X.esp")]),
])
def test_segment_hits_take_the_first_word_after_a_redirect_or_tee(text, want):
    assert writes._segment_hits(text) == want


def test_a_copy_from_the_null_device_is_a_write():
    e = shellparse.parse("cp /dev/null Data/X.esp", "bash").execs[0]
    assert writes.copy_targets(e) == [("Data/X.esp", ("/dev/null",))]
    assert decide("cp /dev/null Data/X.esp", cwd="C:/work")[1] == {"S-WRITE-PLUGIN"}
    assert decide("Copy-Item NUL X.esp", cwd="C:/work", shell="powershell")[1] == {"S-WRITE-PLUGIN"}
