"""skyguard.write_rules: protect-files.sh's rules, one fire and one no-fire per rule, plus ordering and
an equivalence check against every recorded protect-files verdict in the guard corpus."""
import json
import re
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, write_rules  # noqa: E402

BASH = (REPO / "agent" / "guards" / "legacy-bash" / "protect-files.sh").read_text(encoding="utf-8")
LINES = BASH.splitlines()


def msg(line_no):
    """The quoted message of a deny/advise call on a protect-files.sh line (1-based)."""
    return re.search(r'(?:deny|advise) "(.*)"', LINES[line_no - 1]).group(1)


def inert(line_no):
    return re.search(r'permissionDecisionReason:"(.*?)"', LINES[line_no - 1]).group(1)


def sub(line_no, path):
    return msg(line_no).replace("$FILE_PATH", path)


def run(path, tmp_path):
    return write_rules.decide(path, audit.Audit(str(tmp_path)))


def log_text(tmp_path):
    return (tmp_path / ".claude/backups/AUDIT_LOG.txt").read_text(encoding="utf-8")


def advice(path, tmp_path):
    v = run(path, tmp_path)
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
        assert run("C:/x/Data/.claude/" + sub_dir + "/a.psc", tmp_path) == ("allow", "", "")
    assert run(".claude\\hooks\\a.ini", tmp_path) == ("allow", "", "")
    assert not (tmp_path / ".claude/backups/AUDIT_LOG.txt").exists()  # silent: no log either
    # another .claude subdirectory is not whitelisted (falls through to the catch-all)
    assert advice("C:/x/Data/.claude/other/a.txt", tmp_path) == sub(121, "C:/x/Data/.claude/other/a.txt")


def test_rule2_beats_advise_rules(tmp_path):
    assert run("C:/x/.claude/memory/plugins.txt", tmp_path) == ("allow", "", "")


def test_rule3_node_modules_silent_allow(tmp_path):
    assert run("C:/x/Data/node_modules/p/Skyrim.ini", tmp_path) == ("allow", "", "")
    assert advice("C:/x/Data/node_modulesx/a.txt", tmp_path) == sub(121, "C:/x/Data/node_modulesx/a.txt")


def test_rule4_skyrim_ini(tmp_path):
    for name in ("Skyrim.ini", "SkyrimVR.ini", "SkyrimPrefs.ini", "SkyrimCustom.ini", "skyrim.INI"):
        p = "C:/Users/x/Docs/" + name
        assert advice(p, tmp_path) == sub(106, p), name
    assert advice("C:/tmp/Skyrim.ini.bak", tmp_path) == ""


def test_rule5_skse_plugin_ini(tmp_path):
    p = "C:/g/Data/SKSE/Plugins/foo/bar.ini"
    assert advice(p, tmp_path) == sub(108, p)
    assert advice("C:/g/Data/SKSE/Plugins/foo.json", tmp_path) == sub(121, "C:/g/Data/SKSE/Plugins/foo.json")
    assert advice("C:/g/SKSE/Plugins/foo.ini", tmp_path) == ""


def test_rule6_load_order(tmp_path):
    for name in ("loadorder.txt", "plugins.txt", "Plugins.TXT"):
        p = "C:/AppData/" + name
        assert advice(p, tmp_path) == sub(110, p), name
    assert advice("C:/AppData/plugins.json", tmp_path) == ""


def test_rule7_papyrus(tmp_path):
    for ext in ("pex", "psc", "PSC"):
        p = "C:/work/Foo." + ext
        assert advice(p, tmp_path) == sub(112, p), ext
    assert advice("C:/work/Foo.pexx", tmp_path) == ""


def test_rule8_catch_all(tmp_path):
    p = "C:/GOG Games/Skyrim VR/Data/Textures/a.dds"
    assert advice(p, tmp_path) == sub(121, p)
    p2 = "C:/Users/x/Documents/My Games/Skyrim VR/foo.cfg"
    assert advice(p2, tmp_path) == sub(121, p2)
    p3 = "Data/meshes/a.nif"
    assert advice(p3, tmp_path) == sub(121, p3)
    # a bare "Skyrim" in a directory name must NOT fire (the documented false positive)
    assert advice("C:/Projects/Skyrim-toolkit/README.md", tmp_path) == ""
    assert advice("C:/Projects/mydata/x.txt", tmp_path) == ""


def test_rule8_not_fired_when_specific_advice_present(tmp_path):
    p = "C:/GOG Games/Skyrim VR/Data/Skyrim.ini"
    assert advice(p, tmp_path) == sub(106, p)
    p = "C:/g/Data/SKSE/Plugins/x.ini"
    assert advice(p, tmp_path) == sub(108, p)


def test_advice_joined_in_rule_order(tmp_path):
    # .psc under Data/SKSE/Plugins? rule 5 needs .ini, so use plugins.txt-ending ini impossible; combine 4+... :
    p = "C:/g/Data/SKSE/Plugins/Skyrim.ini"
    assert advice(p, tmp_path) == sub(106, p) + " | " + sub(108, p)


def test_audit_lines(tmp_path):
    run("C:/x/Data/My.esp", tmp_path)
    run("C:/x/plugins.txt", tmp_path)
    log = log_text(tmp_path)
    assert "protect-files DENY: " + msg(98) in log
    assert "protect-files ADVISE: " + sub(110, "C:/x/plugins.txt") in log


def test_every_message_constant_equals_the_bash_text():
    expected = {
        "NO_PATH_DENY": inert(84),
        "NO_PATH_LOG": re.search(r'hooklog REFUSE "(.*)"', LINES[82]).group(1),
        "DENY_PLUGIN": msg(98), "ADV_SKYRIM_CONFIG": msg(106), "ADV_SKSE_CONFIG": msg(108),
        "ADV_LOADORDER": msg(110), "ADV_PAPYRUS": msg(112), "ADV_INSTALL": msg(121),
    }
    for name, text in expected.items():
        assert getattr(write_rules, name) == text.replace("$FILE_PATH", "{p}"), name
    consts = {n for n in dir(write_rules) if n.startswith(("DENY_", "ADV_", "NO_"))}
    assert consts == set(expected), consts ^ set(expected)


def _corpus_rows():
    rows = []
    for line in (REPO / "tests" / "guard_corpus" / "expected.jsonl").read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        r = json.loads(line)
        p = r["payload"]
        if r["hook"] != "protect-files.sh" or not isinstance(p, dict):
            continue
        ti = p.get("tool_input")
        fp = ti.get("file_path") if isinstance(ti, dict) else None
        if isinstance(fp, str):
            rows.append((r["id"], fp, r["decision"], r["text"]))
    return rows


CORPUS = _corpus_rows()


def test_corpus_subset_is_non_empty_and_varied():
    assert len(CORPUS) >= 20, len(CORPUS)
    assert {c[2] for c in CORPUS} >= {"deny", "advise", "allow"}


@pytest.mark.parametrize("cid,path,decision,text", CORPUS, ids=[c[0] for c in CORPUS])
def test_port_matches_recorded_bash_verdict(tmp_path, cid, path, decision, text):
    v = run(path, tmp_path)
    if v.decision == "allow" and v.context:
        got = ("advise", v.context)
    elif v.decision == "allow":
        got = ("allow", "")
    else:
        got = (v.decision, v.reason)
    assert got == (decision, text)
