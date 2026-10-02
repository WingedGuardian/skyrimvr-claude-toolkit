"""skyguard.shell_rules: protect-bash.sh's rules, one fire and one no-fire per rule, plus ordering and
an equivalence check against every recorded protect-bash verdict in the guard corpus."""
import json
import re
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, paths, shell_rules  # noqa: E402

BASH = (REPO / "tests" / "guard_corpus" / "reference" / "protect-bash.sh").read_text(encoding="utf-8")
GAME = "C:/GOG Games/The Elder Scrolls V Skyrim VR"
LINES = BASH.splitlines()


def msg(line_no):
    """The quoted message of a deny/advise/ask call on a protect-bash.sh line (1-based)."""
    return re.search(r'(?:deny|advise|ask) "(.*)"', LINES[line_no - 1]).group(1)


def inert(line_no):
    return re.search(r'permissionDecisionReason:"(.*?)"', LINES[line_no - 1]).group(1)


def run(cmd, tmp_path, game=GAME):
    r = paths.roots("/x/install", "", {"SKYRIM_GAME_ROOT": game})
    return shell_rules.decide(cmd, r, audit.Audit(str(tmp_path)))


def advice(cmd, tmp_path):
    v = run(cmd, tmp_path)
    assert v.decision == "allow" and v.reason == ""
    return v.context


def sub(line_no, cmd):
    return msg(line_no).replace("$COMMAND", cmd)


def test_rule0_empty_command(tmp_path):
    v = run("", tmp_path)
    assert (v.decision, v.reason) == ("deny", inert(59))
    log = (tmp_path / ".claude/backups/AUDIT_LOG.txt").read_text(encoding="utf-8")
    assert "protect-bash REFUSE: " + re.search(r'hooklog REFUSE "(.*)"', LINES[57]).group(1) in log


def test_rule0b_no_root(tmp_path):
    v = shell_rules.decide("ls", paths.Roots("", "x"), audit.Audit(str(tmp_path)))
    assert (v.decision, v.reason) == ("deny", inert(216))
    log = (tmp_path / ".claude/backups/AUDIT_LOG.txt").read_text(encoding="utf-8")
    assert "protect-bash REFUSE: " + re.search(r'hooklog REFUSE "(.*)"', LINES[214]).group(1) in log


def test_rule0_checked_before_0b(tmp_path):
    v = shell_rules.decide("", paths.Roots("", ""), audit.Audit(str(tmp_path)))
    assert v.reason == inert(59)


def test_rule1_config_delete_denied(tmp_path):
    cmd = 'rm "C:/Users/x/Documents/My Games/Skyrim VR/SkyrimPrefs.ini"'
    assert (run(cmd, tmp_path).decision, run(cmd, tmp_path).reason) == ("deny", msg(252))


def test_rule1_no_destroyer_no_fire(tmp_path):
    assert run('ls "C:/Users/x/Documents/My Games/Skyrim VR"', tmp_path).decision == "allow"


def test_rule2_install_delete_denied_with_exact_text(tmp_path):
    v = run(f'rm -rf "{GAME}"', tmp_path)
    assert (v.decision, v.reason) == ("deny", msg(253))


def test_rule2_destroyer_elsewhere_allowed(tmp_path):
    assert run("rm -rf /tmp/scratch", tmp_path) == ("allow", "", "")


def test_rule2_conjunction_is_per_grep_not_per_line(tmp_path):
    v = run(f"rm x\nls {GAME}", tmp_path)
    assert v.decision == "deny" and v.reason == msg(253)


def test_rule3_registry(tmp_path):
    v = run("reg.exe delete HKLM\\SOFTWARE\\Bethesda /f", tmp_path)
    assert (v.decision, v.reason) == ("deny", msg(258))
    assert run("reg query HKLM\\SOFTWARE", tmp_path).decision == "allow"


def test_rule4_psc_output(tmp_path):
    v = run("Champollion.exe a.pex -o out/Foo.psc", tmp_path)
    assert (v.decision, v.reason) == ("deny", msg(263))
    assert run("Champollion.exe a.pex -o out/dir", tmp_path).decision == "allow"


def test_rule5_champollion_pex_in_scripts(tmp_path):
    v = run(f'Champollion.exe "{GAME}/Data/Scripts/Foo.pex"', tmp_path)
    assert (v.decision, v.reason) == ("deny", msg(271))
    assert run(f'Champollion.exe "C:/Temp/Foo.pex" "{GAME}"', tmp_path).decision == "allow"


def test_rule6_delete_advice(tmp_path):
    # Dead in the bash too: its prefix is DESTROYER's first alternative, so any command it matches was
    # already denied by rule 2/1. Pinned as the deny it really is.
    v = run("rm /x/install/Data/a.dds", tmp_path)
    assert (v.decision, v.reason) == ("deny", msg(253))
    assert advice("ls /x/install", tmp_path) == ""


def test_rule7_relative_data_delete(tmp_path):
    cmd = "rm -rf Data/Textures/x.dds"
    assert advice(cmd, tmp_path) == sub(282, cmd)
    assert advice("rm mydata/cache.bin", tmp_path) == ""


def test_rule8_move_copy(tmp_path):
    cmd = "cp a.txt /x/install/Data/a.txt"
    assert advice(cmd, tmp_path) == sub(283, cmd)
    assert advice("cp a.txt b.txt", tmp_path) == ""


def test_rule9_redirect(tmp_path):
    cmd = f'echo hi > "{GAME}/notes.txt"'
    assert advice(cmd, tmp_path) == sub(284, cmd)
    assert advice("echo hi > /tmp/n.txt", tmp_path) == ""


def test_rule10_sed(tmp_path):
    cmd = f"sed -i s/a/b/ {GAME}/x.txt"
    assert advice(cmd, tmp_path) == sub(285, cmd)
    assert advice("sed -i s/a/b/ /tmp/x.txt", tmp_path) == ""


def test_rule11_plugin_files(tmp_path):
    cmd = "cat My.esp"
    assert advice(cmd, tmp_path) == sub(288, cmd)
    assert advice("cat espresso.txt", tmp_path) == ""


def test_rule12_load_order(tmp_path):
    cmd = "cat plugins.txt"
    assert advice(cmd, tmp_path) == sub(289, cmd)
    assert advice("cat plugins.json", tmp_path) == ""


def test_rule13_automod_esp_write(tmp_path):
    cmd = "automod esp add-weapon My.esp x"
    assert sub(293, cmd) in advice(cmd, tmp_path)
    assert sub(293, cmd) not in advice(cmd + " --dry-run", tmp_path)
    assert advice("automod esp info x", tmp_path) == ""


def test_rule14_automod_nif_write(tmp_path):
    cmd = "automod nif scale a.nif 2"
    assert advice(cmd, tmp_path) == sub(295, cmd)
    assert advice("automod nif info a.nif", tmp_path) == ""


def test_rule15_automod_archive_write(tmp_path):
    cmd = "automod archive create a.bsa dir"
    assert advice(cmd, tmp_path).endswith(sub(296, cmd))
    assert sub(296, cmd) not in advice("automod archive list a.bsa", tmp_path)


def test_rule16_resaver_apply_asks(tmp_path):
    cmd = "bash tools/resaver-cli.sh reset-havok s.ess --apply"
    v = run(cmd, tmp_path)
    assert (v.decision, v.reason, v.context) == ("ask", sub(303, cmd), "")
    assert run(cmd.replace(" --apply", ""), tmp_path) == ("allow", "", "")


def test_rule16_ask_drops_earlier_advice(tmp_path):
    v = run("cat plugins.txt; bash tools/resaver-cli.sh reset-havok s.ess --apply", tmp_path)
    assert v.decision == "ask" and v.context == ""
    assert v.reason == sub(303, "cat plugins.txt; bash tools/resaver-cli.sh reset-havok s.ess --apply")


def test_rule17_external_tool_output_into_game(tmp_path):
    cmd = f'Caprica.exe -o "{GAME}/Data/Scripts" in.psc'
    assert advice(cmd, tmp_path) == sub(309, cmd)
    assert advice("Caprica.exe -o C:/Temp/out in.psc", tmp_path) == ""


def test_advice_joined_in_rule_order(tmp_path):
    cmd = "rm Data/a.esp"
    assert advice(cmd, tmp_path) == sub(282, cmd) + " | " + sub(288, cmd)


def test_audit_lines_logged(tmp_path):
    run("cat plugins.txt", tmp_path)
    run(f'rm -rf "{GAME}"', tmp_path)
    log = (tmp_path / ".claude/backups/AUDIT_LOG.txt").read_text(encoding="utf-8")
    assert "protect-bash ADVISE: " + sub(289, "cat plugins.txt") in log
    assert "protect-bash DENY: " + msg(253) in log


LAYOUT_ENV = {
    "declared": {"SKYRIM_GAME_ROOT": GAME},
    "config": {"SKYRIM_GAME_ROOT": GAME, "SKYRIM_CONFIG_DIR": "D:/Profiles/MO2/profiles/Default"},
}


def _corpus_rows():
    rows = []
    for line in (REPO / "tests" / "guard_corpus" / "expected.jsonl").read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        r = json.loads(line)
        p = r["payload"]
        if (r["hook"] != "protect-bash.sh" or r["layout"] not in LAYOUT_ENV or r.get("payload_note")
                or r["id"].startswith(("root-", "payload-")) or not isinstance(p, dict)):
            continue
        cmd = (p.get("tool_input") or {}).get("command")
        if isinstance(cmd, str):
            rows.append((r["id"], r["layout"], cmd, r["decision"], r["text"]))
    return rows


CORPUS = _corpus_rows()


def test_corpus_has_rows_in_both_layouts():
    assert len(CORPUS) >= 100, len(CORPUS)
    assert {c[1] for c in CORPUS} == {"declared", "config"}
    assert sum(c[0].startswith("shell-unicode-") for c in CORPUS) >= 30


@pytest.mark.parametrize("cid,layout,cmd,decision,text", CORPUS, ids=[c[0] for c in CORPUS])
def test_port_matches_recorded_bash_verdict(tmp_path, cid, layout, cmd, decision, text):
    r = paths.roots("/x/install", "", LAYOUT_ENV[layout])
    v = shell_rules.decide(cmd, r, audit.Audit(str(tmp_path)))
    if v.decision == "allow" and v.context:
        got = ("advise", v.context)
    elif v.decision == "allow":
        got = ("allow", "")
    else:
        got = (v.decision, v.reason)
    assert got == (decision, text)


# Accepted divergence (controller ruling, Task 3 fix round 1): next to a NON-ASCII letter, GNU grep in
# a UTF-8 locale sees no word boundary while the port (re.ASCII) sees one. The port is therefore
# STRICTER than bash here, never looser. Measured against the real hook on this machine: all four are allowed.
RESIDUE = {   # input -> the decision the port gives today (bash allows every one of them)
    f'Champollioné "{GAME}/Data/Scripts/Foo.pex"': "deny",
    "cat My.espé": "advise",
    "cat a.esp b": "advise",  # bash on this Windows build sees U+2003 as a word char; UTF-8 Linux does not
    "cat X.ESPİ": "advise",
}
STRICTNESS = {"allow": 0, "advise": 1, "ask": 2, "deny": 3}


@pytest.mark.parametrize("cmd,expected", sorted(RESIDUE.items()))
def test_unicode_word_boundary_residue_is_never_looser(tmp_path, cmd, expected):
    """Pins the port's actual decision; bash allows all of these, so the port may only be stricter."""
    v = run(cmd, tmp_path)
    py = "advise" if v.decision == "allow" and v.context else v.decision
    assert py == expected
    assert STRICTNESS[py] > STRICTNESS["allow"]


def test_every_message_constant_equals_the_bash_text():
    expected = {
        "NO_COMMAND_DENY": inert(59), "NO_ROOT_DENY": inert(216),
        "NO_COMMAND_LOG": re.search(r'hooklog REFUSE "(.*)"', LINES[57]).group(1),
        "NO_ROOT_LOG": re.search(r'hooklog REFUSE "(.*)"', LINES[214]).group(1),
        "DENY_CONFIG": msg(252), "DENY_INSTALL": msg(253), "DENY_REGISTRY": msg(258), "DENY_PSC": msg(263),
        "DENY_CHAMPOLLION": msg(271),
        "ADV_DELETE": msg(281), "ADV_RELATIVE": msg(282), "ADV_MOVE": msg(283), "ADV_REDIRECT": msg(284),
        "ADV_SED": msg(285), "ADV_PLUGIN": msg(288), "ADV_LOADORDER": msg(289), "ADV_AUTOMOD_ESP": msg(293),
        "ADV_AUTOMOD_NIF": msg(295), "ADV_AUTOMOD_ARCHIVE": msg(296), "ASK_RESAVER": msg(303),
        "ADV_EXTERNAL": msg(309),
    }
    for name, text in expected.items():
        assert getattr(shell_rules, name) == text.replace("$COMMAND", "{c}"), name
    consts = {n for n in dir(shell_rules) if n.startswith(("DENY_", "ADV_", "ASK_", "NO_"))}
    assert consts == set(expected), consts ^ set(expected)


def test_ask_path_writes_the_ask_audit_line(tmp_path):
    cmd = "bash tools/resaver-cli.sh reset-havok s.ess --apply"
    run(cmd, tmp_path)
    log = (tmp_path / ".claude/backups/AUDIT_LOG.txt").read_text(encoding="utf-8")
    assert "protect-bash ASK: " + sub(303, cmd) in log
