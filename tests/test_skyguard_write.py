"""skyguard.write_rules: protect-files.sh's rules, one fire and one no-fire per rule, plus ordering and
an equivalence check against every recorded protect-files verdict in the guard corpus.

Plan 5 Task 9: every advise has a new 1-line template naming the file by its basename, and W-PAPYRUS / W-INSTALL
decide on where the path RESOLVES (the install's Data/Scripts, Data/, the config folder, MO2), so `run` passes the
roots the hook gets; `run_legacy` pins the no-roots fallback (the legacy text tests)."""
import json
import re
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, paths, rules, write_rules  # noqa: E402

BASH = (REPO / "tests" / "guard_corpus" / "reference" / "protect-files.sh").read_text(encoding="utf-8")
LINES = BASH.splitlines()
GAME = "C:/GOG Games/The Elder Scrolls V Skyrim VR"
ROOTS = paths.roots("C:/g", "", {"SKYRIM_GAME_ROOT": GAME})
BS = chr(92)


def msg(line_no):
    """The quoted message of a deny/advise call on a protect-files.sh line (1-based)."""
    return re.search(r'(?:deny|advise) "(.*)"', LINES[line_no - 1]).group(1)


def inert(line_no):
    return re.search(r'permissionDecisionReason:"(.*?)"', LINES[line_no - 1]).group(1)


def note(rule_id, path):
    return rules.message(rule_id, file=path)


def run(path, tmp_path, cwd=""):
    return write_rules.decide(path, audit.Audit(str(tmp_path)), roots=ROOTS, cwd=cwd)


def run_legacy(path, tmp_path):
    return write_rules.decide(path, audit.Audit(str(tmp_path)))


def log_text(tmp_path):
    return (tmp_path / ".claude/backups/AUDIT_LOG.txt").read_text(encoding="utf-8")


def advice(path, tmp_path, cwd=""):
    v = run(path, tmp_path, cwd)
    assert v.decision == "allow" and v.reason == ""
    return v.context


def test_rule0_empty_path(tmp_path):
    v = run("", tmp_path)
    assert (v.decision, v.reason, v.context) == ("deny", inert(84), "")
    assert "protect-files REFUSE: " + re.search(r'hooklog REFUSE "(.*)"', LINES[82]).group(1) in log_text(tmp_path)


def test_rule1_plugin_write_denied(tmp_path):
    for ext in ("esp", "esm", "esl", "bsa", "ba2", "ESP"):
        v = run("C:/x/Data/My." + ext, tmp_path)
        assert (v.decision, v.reason) == ("deny", msg(98)), ext
    # trailing dots/space still count; a longer extension or a bare word does not
    assert run("C:/x/Data/My.esp. ", tmp_path).decision == "deny"
    assert run("C:/x/notes/My.espx", tmp_path) == ("allow", "", "")
    assert run("C:/x/notes/esp", tmp_path) == ("allow", "", "")


def test_rule1_beats_whitelist(tmp_path):
    assert run("C:/x/.claude/plans/a.esp", tmp_path).decision == "deny"


def test_rule2_claude_workspace_silent_allow(tmp_path):
    for sub_dir in ("hooks", "plans", "backups", "memory", "projects"):
        assert run(GAME + "/Data/.claude/" + sub_dir + "/a.psc", tmp_path) == ("allow", "", "")
    assert run(".claude" + BS + "hooks" + BS + "a.ini", tmp_path) == ("allow", "", "")
    assert not (tmp_path / ".claude/backups/AUDIT_LOG.txt").exists()  # silent: no log either
    # another .claude subdirectory is not whitelisted (falls through to the catch-all)
    assert advice(GAME + "/Data/.claude/other/a.txt", tmp_path) == note("W-INSTALL", "a.txt")


def test_rule2_beats_advise_rules(tmp_path):
    assert run("C:/x/.claude/memory/plugins.txt", tmp_path) == ("allow", "", "")


def test_rule3_node_modules_silent_allow(tmp_path):
    assert run(GAME + "/Data/node_modules/p/Skyrim.ini", tmp_path) == ("allow", "", "")
    assert advice(GAME + "/Data/node_modulesx/a.txt", tmp_path) == note("W-INSTALL", "a.txt")


def test_rule4_skyrim_ini(tmp_path):
    for name in ("Skyrim.ini", "SkyrimVR.ini", "SkyrimPrefs.ini", "SkyrimCustom.ini", "skyrim.INI"):
        p = "C:/Users/x/Docs/" + name
        assert advice(p, tmp_path) == note("W-INI", name), name
    assert advice("C:/tmp/Skyrim.ini.bak", tmp_path) == ""


def test_rule5_skse_plugin_ini(tmp_path):
    p = "C:/g/Data/SKSE/Plugins/foo/bar.ini"
    assert advice(p, tmp_path) == note("W-SKSE-INI", "bar.ini")
    assert advice("C:/g/Data/SKSE/Plugins/foo.json", tmp_path) == note("W-INSTALL", "foo.json")
    assert advice("C:/g/SKSE/Plugins/foo.ini", tmp_path) == ""


def test_rule6_load_order(tmp_path):
    for name in ("loadorder.txt", "plugins.txt", "Plugins.TXT"):
        p = "C:/AppData/" + name
        assert advice(p, tmp_path) == note("W-LOADORDER", name), name
    assert advice("C:/AppData/plugins.json", tmp_path) == ""


def test_rule7_papyrus(tmp_path):
    # Task 9: only a script the game loads: under the install's Data/Scripts (any depth) or in MO2.
    for ext in ("pex", "psc", "PSC"):
        p = GAME + "/Data/Scripts/Source/Foo." + ext
        assert advice(p, tmp_path) == note("W-PAPYRUS", "Foo." + ext), ext
    assert advice("C:/work/Foo.psc", tmp_path) == ""                   # a toolkit checkout, a temp decompile
    assert advice(GAME + "/tools/Foo.psc", tmp_path) == ""             # the install, but not Data/Scripts
    assert advice("Data/Scripts/Foo.pex", tmp_path, cwd=GAME) == note("W-PAPYRUS", "Foo.pex")
    assert advice(GAME + "/Data/Scripts/Foo.pexx", tmp_path) == note("W-INSTALL", "Foo.pexx")
    mo2 = paths.roots("C:/g", "", {"SKYRIM_GAME_ROOT": GAME, "SKYRIM_MO2_MODS": "D:/MO2/mods"})
    v = write_rules.decide("D:/MO2/mods/M/Scripts/A.pex", audit.Audit(str(tmp_path)), roots=mo2)
    assert v.context == note("W-PAPYRUS", "A.pex")


def test_rule8_catch_all(tmp_path):
    # Task 9: resolved into the install's Data/, the config folder or MO2 -- not a WORD in the path.
    assert advice(GAME + "/Data/Textures/a.dds", tmp_path) == note("W-INSTALL", "a.dds")
    assert advice("C:/Users/x/Documents/My Games/Skyrim VR/foo.cfg", tmp_path) == note("W-INSTALL", "foo.cfg")
    assert advice("Data/meshes/a.nif", tmp_path, cwd=GAME) == note("W-INSTALL", "a.nif")
    assert advice("Data/meshes/a.nif", tmp_path, cwd="C:/work") == ""   # someone else's Data/
    assert advice("Data/meshes/a.nif", tmp_path) == ""                  # no cwd: cannot be placed
    assert advice("C:/Games/Skyrim VR/Data/Textures/a.dds", tmp_path) == ""   # not this install
    assert advice(GAME + "/tools/x.py", tmp_path) == ""                # the install's tools/ is not the game
    # a bare "Skyrim" in a directory name must NOT fire (the documented false positive)
    assert advice("C:/Projects/Skyrim-toolkit/README.md", tmp_path) == ""
    assert advice("C:/Projects/mydata/x.txt", tmp_path) == ""


def test_without_roots_the_zone_notes_fall_back_to_the_legacy_text_tests(tmp_path):
    a = run_legacy("C:/Games/Skyrim VR/Data/Textures/a.dds", tmp_path)
    assert a.context == note("W-INSTALL", "a.dds")
    assert run_legacy("C:/work/Foo.psc", tmp_path).context == note("W-PAPYRUS", "Foo.psc")
    assert run_legacy("C:/Projects/mydata/x.txt", tmp_path).context == ""


def test_rule8_not_fired_when_specific_advice_present(tmp_path):
    p = GAME + "/Data/Skyrim.ini"
    assert advice(p, tmp_path) == note("W-INI", "Skyrim.ini")
    p = GAME + "/Data/SKSE/Plugins/x.ini"
    assert advice(p, tmp_path) == note("W-SKSE-INI", "x.ini")


def test_advice_joined_in_rule_order(tmp_path):
    # Only rules 4 and 5 can both fire on one path: a Skyrim*.ini under Data/SKSE/Plugins.
    p = "C:/g/Data/SKSE/Plugins/Skyrim.ini"
    assert advice(p, tmp_path) == note("W-INI", "Skyrim.ini") + " | " + note("W-SKSE-INI", "Skyrim.ini")


def test_audit_lines(tmp_path):
    run("C:/x/Data/My.esp", tmp_path)
    run("C:/x/plugins.txt", tmp_path)
    log = log_text(tmp_path)
    assert "protect-files DENY: " + msg(98) in log
    assert "protect-files ADVISE: " + note("W-LOADORDER", "plugins.txt") in log


def test_every_message_constant_equals_the_bash_text():
    expected = {
        "NO_PATH_DENY": inert(84),
        "NO_PATH_LOG": re.search(r'hooklog REFUSE "(.*)"', LINES[82]).group(1),
        "DENY_PLUGIN": msg(98),
    }
    for name, text in expected.items():
        assert getattr(write_rules, name) == text, name
    # Task 9 rewrote every advise: same constant names, each its registry template.
    plan5 = {"ADV_SKYRIM_CONFIG": "W-INI", "ADV_SKSE_CONFIG": "W-SKSE-INI", "ADV_LOADORDER": "W-LOADORDER",
             "ADV_PAPYRUS": "W-PAPYRUS", "ADV_INSTALL": "W-INSTALL"}
    for name, rid in plan5.items():
        assert getattr(write_rules, name) == rules.RULES[rid]["template"], name
    consts = {n for n in dir(write_rules) if n.startswith(("DENY_", "ADV_", "NO_"))}
    assert consts == set(expected) | set(plan5), consts ^ (set(expected) | set(plan5))


def _corpus_rows():
    rows, dropped = [], []
    for line in (REPO / "tests" / "guard_corpus" / "expected.jsonl").read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        r = json.loads(line)
        p = r["payload"]
        if r["hook"] != "protect-files.sh":
            continue
        ti = p.get("tool_input") if isinstance(p, dict) else None
        fp = ti.get("file_path") if isinstance(ti, dict) else None
        if isinstance(fp, str):
            rows.append((r["id"], fp, r["decision"], r["text"]))
        else:
            dropped.append(r["id"])
    return rows, dropped


CORPUS, DROPPED = _corpus_rows()
SUPERSEDED = {r.get("supersedes") for r in (json.loads(x) for x in (REPO / "tests" / "guard_corpus" / "intended.jsonl")
              .read_text(encoding="utf-8").split("\n") if x.strip())}


def test_corpus_filter_drops_only_the_payload_shape_rows():
    assert sorted(DROPPED) == [
        "payload-array-protect-files.sh", "payload-empty-protect-files.sh",
        "payload-invalid-protect-files.sh", "payload-notool-protect-files.sh",
    ], DROPPED


def test_corpus_subset_is_non_empty_and_varied():
    assert len(CORPUS) >= 20, len(CORPUS)
    assert {c[2] for c in CORPUS} >= {"deny", "advise", "allow"}


@pytest.mark.parametrize("cid,path,decision,text", CORPUS, ids=[c[0] for c in CORPUS])
def test_port_matches_recorded_bash_verdict(tmp_path, cid, path, decision, text):
    """Every corpus row keeps its recorded DECISION (the rewritten advise texts are compared by the corpus test, by rule
    id); a row an intended row deliberately re-specifies is skipped. The roots are the corpus's declared layout."""
    if cid in SUPERSEDED:
        pytest.skip("deliberately re-specified by an intended row (tests/guard_corpus/intended.jsonl)")
    v = write_rules.decide(path, audit.Audit(str(tmp_path)), roots=paths.roots("C:/t7/x", "", {"SKYRIM_GAME_ROOT": GAME}))
    if v.decision == "allow" and v.context:
        got = ("advise", v.context)
    elif v.decision == "allow":
        got = ("allow", "")
    else:
        got = (v.decision, v.reason)
    assert got[0] == decision
    if decision == "deny":
        assert got[1] == text


def test_rule_trace_records_the_rule_id(tmp_path):
    a = audit.Audit(str(tmp_path))
    write_rules.decide("C:/x/Data/My.esp", a)
    write_rules.decide("C:/x/plugins.txt", a)
    assert a.fired == [("W-PLUGIN", "deny"), ("W-LOADORDER", "advise")]
