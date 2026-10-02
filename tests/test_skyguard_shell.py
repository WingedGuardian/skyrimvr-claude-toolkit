"""skyguard.shell_rules: protect-bash.sh's rules, one fire and one no-fire per rule, plus ordering and
an equivalence check against every recorded protect-bash verdict in the guard corpus."""
import json
import re
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, paths, shell_rules  # noqa: E402

BASH = (REPO / "agent" / "guards" / "legacy-bash" / "protect-bash.sh").read_text(encoding="utf-8")
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


def _corpus_rows():
    rows = []
    for line in (REPO / "tests" / "guard_corpus" / "expected.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        p = r["payload"]
        if (r["hook"] != "protect-bash.sh" or r["layout"] != "declared" or r.get("payload_note")
                or r["id"].startswith(("root-", "payload-")) or not isinstance(p, dict)):
            continue
        cmd = (p.get("tool_input") or {}).get("command")
        if isinstance(cmd, str):
            rows.append((r["id"], cmd, r["decision"], r["text"]))
    return rows


CORPUS = _corpus_rows()


def test_corpus_has_rows():
    assert len(CORPUS) >= 60, len(CORPUS)


@pytest.mark.parametrize("cid,cmd,decision,text", CORPUS, ids=[c[0] for c in CORPUS])
def test_port_matches_recorded_bash_verdict(tmp_path, cid, cmd, decision, text):
    v = run(cmd, tmp_path)
    if v.decision == "allow" and v.context:
        got = ("advise", v.context)
    elif v.decision == "allow":
        got = ("allow", "")
    else:
        got = (v.decision, v.reason)
    assert got == (decision, text)
