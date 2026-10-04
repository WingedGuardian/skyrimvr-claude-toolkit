"""skyguard.shell_rules: protect-bash.sh's rules, one fire and one no-fire per rule, plus ordering and
an equivalence check against every recorded protect-bash verdict in the guard corpus."""
import json
import re
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, paths, rules, shell_rules  # noqa: E402

BASH = (REPO / "tests" / "guard_corpus" / "reference" / "protect-bash.sh").read_text(encoding="utf-8")
GAME = "C:/GOG Games/The Elder Scrolls V Skyrim VR"
LINES = BASH.splitlines()


def msg(line_no):
    """The quoted message of a deny/advise/ask call on a protect-bash.sh line (1-based)."""
    return re.search(r'(?:deny|advise|ask) "(.*)"', LINES[line_no - 1]).group(1)


def inert(line_no):
    return re.search(r'permissionDecisionReason:"(.*?)"', LINES[line_no - 1]).group(1)


def run(cmd, tmp_path, game=GAME):
    # shell="bash": these pin the bash hook's behaviour (the Bash tool). Unknown dialect (R5) is tested on its own.
    r = paths.roots("/x/install", "", {"SKYRIM_GAME_ROOT": game})
    return shell_rules.decide(cmd, r, audit.Audit(str(tmp_path)), shell="bash")


def advice(cmd, tmp_path):
    v = run(cmd, tmp_path)
    assert v.decision == "allow" and v.reason == ""
    return v.context


def sub(line_no, cmd):
    return msg(line_no).replace("$COMMAND", cmd)


def del_install(f):
    return rules.message("S-DEL-INSTALL", file=f)


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
    # Task 7: the delete rules are rewritten (non-legacy text, decided on the executed rm and its resolved operand).
    cmd = 'rm "C:/Users/x/Documents/My Games/Skyrim VR/SkyrimPrefs.ini"'
    v = run(cmd, tmp_path)
    assert (v.decision, v.reason) == ("deny", rules.message("S-DEL-CONFIG", file="SkyrimPrefs.ini"))


def test_rule1_no_destroyer_no_fire(tmp_path):
    assert run('ls "C:/Users/x/Documents/My Games/Skyrim VR"', tmp_path).decision == "allow"


def test_rule2_install_delete_denied_with_exact_text(tmp_path):
    v = run(f'rm -rf "{GAME}"', tmp_path)
    assert (v.decision, v.reason) == ("deny", del_install("The Elder Scrolls V Skyrim VR"))


def test_rule2_destroyer_elsewhere_allowed(tmp_path):
    assert run("rm -rf /tmp/scratch", tmp_path) == ("allow", "", "")


def test_rule2_unresolved_operand_with_the_root_named_still_denies(tmp_path):
    # No cwd in the payload: `x` cannot be placed, and the command names the install -> S-DEL-UNRESOLVED (Task 7).
    v = run(f"rm x\nls {GAME}", tmp_path)
    assert v.decision == "deny" and v.reason == rules.message("S-DEL-UNRESOLVED")


def test_rule2_mention_alone_is_not_a_delete(tmp_path):
    # E16: the old rule denied any text holding a delete word and the install path.
    assert run(f'grep -rn "rm -rf" "{GAME}/tools"', tmp_path) == ("allow", "", "")
    assert run(f'rm -rf /tmp/x && ls "{GAME}"', tmp_path) == ("allow", "", "")


def test_rule3_registry(tmp_path):
    # Task 9: decided on the EXECUTED program (reg delete / the PowerShell registry cmdlets) and its key.
    v = run("reg.exe delete HKLM\\SOFTWARE\\Bethesda /f", tmp_path)
    assert (v.decision, v.reason) == ("deny", rules.message("S-REGISTRY"))
    assert run("reg query HKLM\\SOFTWARE", tmp_path).decision == "allow"
    assert run("printf 'reg delete HKLM\\SOFTWARE\\Bethesda' >> notes.md", tmp_path).decision == "allow"


def test_rule4_psc_output(tmp_path):
    v = run("Champollion.exe a.pex -o out/Foo.psc", tmp_path)
    assert (v.decision, v.reason) == ("deny", rules.message("S-PSC-OUT", file="Foo.psc"))
    assert run('echo "Champollion.exe a.pex -o out/Foo.psc"', tmp_path).decision == "allow"
    assert run("Champollion.exe a.pex -o out/dir", tmp_path).decision == "allow"


def test_rule5_champollion_pex_in_scripts(tmp_path):
    v = run(f'Champollion.exe "{GAME}/Data/Scripts/Foo.pex"', tmp_path)
    assert (v.decision, v.reason) == ("deny", rules.message("S-CHAMPOLLION", file="Foo.pex"))
    assert run(f'Champollion.exe "C:/Temp/Foo.pex" "{GAME}"', tmp_path).decision == "allow"


def test_rule6_delete_advice_is_gone(tmp_path):
    # R10 (Task 7): S-DEL-ADVISE is deleted; a delete inside the install denies.
    v = run("rm /x/install/Data/a.dds", tmp_path)
    assert (v.decision, v.reason) == ("deny", del_install("a.dds"))
    assert advice("ls /x/install", tmp_path) == ""
    assert "S-DEL-ADVISE" not in rules.RULES and "S-DEL-ADVISE" in rules.REMOVED


def test_rule7_relative_data_delete(tmp_path):
    # No cwd: R9 keeps a relative Data delete as advice (S-RELATIVE-UNKNOWN, rewritten in Task 7).
    cmd = "rm -rf Data/Textures/x.dds"
    assert advice(cmd, tmp_path) == rules.message("S-RELATIVE-UNKNOWN", file="x.dds")
    assert advice("rm mydata/cache.bin", tmp_path) == ""


def test_rule7_relative_delete_resolves_from_the_payload_cwd(tmp_path):
    r = paths.roots("/x/install", "", {"SKYRIM_GAME_ROOT": GAME})
    v = shell_rules.decide("rm -rf Data", r, audit.Audit(str(tmp_path)), cwd="/x/install", shell="bash")
    assert (v.decision, v.reason) == ("deny", del_install("Data"))
    v = shell_rules.decide("rm -rf Data", r, audit.Audit(str(tmp_path)), cwd="C:/Users/u/proj", shell="bash")
    assert v == ("allow", "", "")


def test_rule8_move_copy(tmp_path):
    # Task 9: the DESTINATION resolves into the install's Data/ (or config, MO2); the source does not count.
    cmd = "cp a.txt /x/install/Data/a.txt"
    assert advice(cmd, tmp_path) == rules.message("S-MOVE-INSTALL", file="a.txt")
    assert advice("cp a.txt b.txt", tmp_path) == ""
    assert advice("cp /x/install/Data/a.txt /tmp/a.txt", tmp_path) == ""


def test_rule9_redirect(tmp_path):
    # Task 8: the text-matched redirect note (S-REDIRECT-INSTALL) is gone; a redirect into the install's Data/ folder
    # is S-WRITE-INSTALL, decided on the redirect's resolved target.
    assert advice(f'echo hi > "{GAME}/Data/notes.txt"', tmp_path) == rules.message("S-WRITE-INSTALL", file="notes.txt")
    assert advice(f'echo hi > "{GAME}/notes.txt"', tmp_path) == ""         # the install root is not Data/
    assert advice("echo hi > /tmp/n.txt", tmp_path) == ""
    assert "S-REDIRECT-INSTALL" in rules.REMOVED


def test_rule10_sed(tmp_path):
    # Task 9: only a backslash path in the REPLACEMENT, on an install/config file; it replaces S-WRITE-INSTALL.
    cmd = f"sed -i 's/a/C:\\x\\y/' \"{GAME}/Data/x.txt\""
    assert advice(cmd, tmp_path) == rules.message("S-SED-WINPATH")
    assert advice(f'sed -i s/a/b/ "{GAME}/Data/x.txt"', tmp_path) == rules.message("S-WRITE-INSTALL", file="x.txt")
    assert advice("sed -i s/a/b/ /tmp/x.txt", tmp_path) == ""


def test_rule11_plugin_files(tmp_path):
    # R11 (Task 8): reading a plugin is silent; writing one by a shell primitive is S-WRITE-PLUGIN (deny).
    assert advice("cat My.esp", tmp_path) == ""
    v = run("echo x > My.esp", tmp_path)
    assert (v.decision, v.reason) == ("deny", rules.message("S-WRITE-PLUGIN", file="My.esp"))
    assert "S-PLUGIN-REF" in rules.REMOVED


def test_rule12_load_order(tmp_path):
    # Task 8: reading plugins.txt is silent; a write is S-WRITE-LOADORDER.
    assert advice("cat plugins.txt", tmp_path) == ""
    assert advice("echo x >> plugins.txt", tmp_path) == rules.message("S-WRITE-LOADORDER", file="plugins.txt")
    assert advice("echo x >> plugins.json", tmp_path) == ""
    assert "S-LOADORDER-REF" in rules.REMOVED


def test_rule13_automod_esp_write(tmp_path):
    cmd = "automod esp add-weapon My.esp x"
    assert advice(cmd, tmp_path) == rules.message("S-AUTOMOD-ESP", file="My.esp")
    assert advice(cmd + " --dry-run", tmp_path) == ""
    assert advice("automod esp info x", tmp_path) == ""
    assert advice("grep add-weapon docs/automod.md", tmp_path) == ""


def test_rule14_automod_nif_write(tmp_path):
    cmd = "automod nif scale a.nif 2"
    assert advice(cmd, tmp_path) == rules.message("S-AUTOMOD-NIF", file="a.nif")
    assert advice("automod nif info a.nif", tmp_path) == ""


def test_rule15_automod_archive_write(tmp_path):
    cmd = "automod archive create a.bsa dir"
    assert advice(cmd, tmp_path) == rules.message("S-AUTOMOD-ARCHIVE", file="a.bsa")
    assert advice("automod archive list a.bsa", tmp_path) == ""


NEW_RESAVER = ("ReSaver will MODIFY a save file (reset-havok on s.ess). "
               "Approve only if you asked for this save to be changed.")


def test_rule16_resaver_apply_asks(tmp_path):
    cmd = "bash tools/resaver-cli.sh reset-havok s.ess --apply"
    v = run(cmd, tmp_path)
    assert (v.decision, v.reason, v.context) == ("ask", NEW_RESAVER, "")
    assert run(cmd.replace(" --apply", ""), tmp_path) == ("allow", "", "")


def test_rule16_ask_drops_earlier_advice(tmp_path):
    v = run("echo x >> plugins.txt; bash tools/resaver-cli.sh reset-havok s.ess --apply", tmp_path)
    assert v.decision == "ask" and v.context == ""
    assert v.reason == NEW_RESAVER


def _t4_rows():
    path = REPO / "tests" / "guard_corpus" / "intended.jsonl"
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    # T4's rows plus Task 6's ReSaver rows (the forms the parser re-base closed).
    return [r for r in rows if r["task"] == "T4" or r["id"].startswith("t06-resaver-")]


@pytest.mark.parametrize("row", _t4_rows(), ids=[r["id"] for r in _t4_rows()])
def test_resaver_rows(tmp_path, row):
    """Every T4 intended row, in-process: decision, rule trace and exact text. Mutations name a row id here."""
    r = paths.roots("/x/install", "", {"SKYRIM_GAME_ROOT": GAME})
    a = audit.Audit(str(tmp_path))
    shell = {"Bash": "bash", "PowerShell": "powershell"}.get(row["payload"].get("tool_name"), "")
    v = shell_rules.decide(row["payload"]["tool_input"]["command"], r, a, shell=shell)
    assert v.decision == ("ask" if row["decision"] == "ask" else "allow")
    assert v.reason == row["text"]
    assert {rid for rid, _ in a.fired if rid == "S-RESAVER-APPLY"} == set(row["rules"])


def test_resaver_without_a_save_name_still_asks(tmp_path):
    v = run("bash tools/resaver-cli.sh reset-havok --apply", tmp_path)
    assert v.decision == "ask" and "(reset-havok on the save)" in v.reason


def test_resaver_save_is_reduced_to_its_basename(tmp_path):
    v = run("bash tools/resaver-cli.sh reset-havok 'C:/My Saves/Save 12.ess' --apply", tmp_path)
    assert "(reset-havok on Save 12.ess)" in v.reason


def test_rule17_external_tool_output_into_game(tmp_path):
    # Task 9: decompilers only; a compiler writing .pex into Data/Scripts is its job (priced Ruling).
    cmd = f'Champollion.exe C:/Temp/a.pex -p "{GAME}/Data/Scripts/Source"'
    assert advice(cmd, tmp_path) == rules.message("S-EXTERNAL-OUT", op="Champollion")
    assert advice(f'Caprica.exe -o "{GAME}/Data/Scripts" in.psc', tmp_path) == ""
    assert advice("Champollion.exe C:/Temp/a.pex -p C:/Temp/out", tmp_path) == ""


def test_advice_joined_in_rule_order(tmp_path):
    cmd = "rm Data/a.esp; echo x >> plugins.txt"
    assert advice(cmd, tmp_path) == (rules.message("S-RELATIVE-UNKNOWN", file="a.esp") + " | "
                                     + rules.message("S-WRITE-LOADORDER", file="plugins.txt"))


def test_audit_lines_logged(tmp_path):
    run("echo x >> plugins.txt", tmp_path)
    run(f'rm -rf "{GAME}"', tmp_path)
    log = (tmp_path / ".claude/backups/AUDIT_LOG.txt").read_text(encoding="utf-8")
    assert "protect-bash ADVISE: " + rules.message("S-WRITE-LOADORDER", file="plugins.txt") in log
    assert "protect-bash DENY: " + del_install("The Elder Scrolls V Skyrim VR") in log


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
SUPERSEDED = {r.get("supersedes") for r in (json.loads(x) for x in (REPO / "tests" / "guard_corpus" / "intended.jsonl")
              .read_text(encoding="utf-8").split("\n") if x.strip())}


def test_corpus_has_rows_in_both_layouts():
    assert len(CORPUS) >= 100, len(CORPUS)
    assert {c[1] for c in CORPUS} == {"declared", "config"}
    assert sum(c[0].startswith("shell-unicode-") for c in CORPUS) >= 30


@pytest.mark.parametrize("cid,layout,cmd,decision,text", CORPUS, ids=[c[0] for c in CORPUS])
def test_port_matches_recorded_bash_verdict(tmp_path, cid, layout, cmd, decision, text):
    r = paths.roots("/x/install", "", LAYOUT_ENV[layout])
    v = shell_rules.decide(cmd, r, audit.Audit(str(tmp_path)), shell="bash")      # every row is a Bash tool payload
    if v.decision == "allow" and v.context:
        got = ("advise", v.context)
    elif v.decision == "allow":
        got = ("allow", "")
    else:
        got = (v.decision, v.reason)
    if cid in SUPERSEDED:
        pytest.skip("deliberately re-specified by an intended row (tests/guard_corpus/intended.jsonl)")
    # A rule a later Plan 5 task REWROTE or REMOVED keeps its frozen row's decision, not its text.
    frozen = json.loads((REPO / "tests" / "guard_corpus" / "legacy_rules.json").read_text(encoding="utf-8"))
    rewritten = [t for t, rid in frozen.items() if rid in rules.REMOVED or not rules.RULES[rid]["legacy"]]
    if text and any(text == t.replace("{c}", cmd) for t in rewritten):
        assert got[0] == decision
        return
    assert got == (decision, text)


# Accepted divergence (controller ruling, Task 3 fix round 1): next to a NON-ASCII letter, GNU grep in
# a UTF-8 locale sees no word boundary while the port (re.ASCII) sees one. The port is therefore
# STRICTER than bash here, never looser. Measured against the real hook on this machine: all four are allowed.
RESIDUE = {   # input -> the decision the port gives today (bash allows every one of them)
}
# Task 8 (R11): the plugin-reference note that made these three stricter than bash is gone; a READ is silent,
# so the port now agrees with bash on them.
RESIDUE_RESOLVED = (
    "cat My.espé",
    "cat a.esp b",
    "cat X.ESPİ",
    f'Champollioné "{GAME}/Data/Scripts/Foo.pex"',   # Task 9: not Champollion
)
STRICTNESS = {"allow": 0, "advise": 1, "ask": 2, "deny": 3}


@pytest.mark.parametrize("cmd", RESIDUE_RESOLVED)
def test_unicode_residue_on_a_plugin_read_now_matches_bash(tmp_path, cmd):
    assert run(cmd, tmp_path) == ("allow", "", "")


def test_unicode_word_boundary_residue_is_never_looser(tmp_path):
    """Pins the port's actual decision; bash allows all of these, so the port may only be stricter. Task 9 emptied
    the set (every residue now agrees with bash); the check stays so a new residue is pinned, not silent."""
    for cmd, expected in sorted(RESIDUE.items()):
        v = run(cmd, tmp_path)
        py = "advise" if v.decision == "allow" and v.context else v.decision
        assert py == expected
        assert STRICTNESS[py] > STRICTNESS["allow"]


def test_every_message_constant_equals_the_bash_text():
    expected = {
        "NO_COMMAND_DENY": inert(59), "NO_ROOT_DENY": inert(216),
        "NO_COMMAND_LOG": re.search(r'hooklog REFUSE "(.*)"', LINES[57]).group(1),
        "NO_ROOT_LOG": re.search(r'hooklog REFUSE "(.*)"', LINES[214]).group(1),
    }
    for name, text in expected.items():
        assert getattr(shell_rules, name) == text.replace("$COMMAND", "{c}"), name
    # Constants added by Plan 5 have no bash original; each is its registry template (Plan 4's Codex trip-wire keys
    # on these names, so a new deny is visible at the merge).
    # Task 9 rewrote the registry, .psc-output and Champollion denies and every advise: same names, registry text.
    plan5 = {"DENY_WRITE_PLUGIN": rules.RULES["S-WRITE-PLUGIN"]["template"],
             "DENY_REGISTRY": rules.RULES["S-REGISTRY"]["template"], "DENY_PSC": rules.RULES["S-PSC-OUT"]["template"],
             "DENY_CHAMPOLLION": rules.RULES["S-CHAMPOLLION"]["template"],
             "ADV_MOVE": rules.RULES["S-MOVE-INSTALL"]["template"], "ADV_SED": rules.RULES["S-SED-WINPATH"]["template"],
             "ADV_AUTOMOD_ESP": rules.RULES["S-AUTOMOD-ESP"]["template"],
             "ADV_AUTOMOD_NIF": rules.RULES["S-AUTOMOD-NIF"]["template"],
             "ADV_AUTOMOD_ARCHIVE": rules.RULES["S-AUTOMOD-ARCHIVE"]["template"],
             "ADV_EXTERNAL": rules.RULES["S-EXTERNAL-OUT"]["template"]}
    for name, text in plan5.items():
        assert getattr(shell_rules, name) == text, name
    consts = {n for n in dir(shell_rules) if n.startswith(("DENY_", "ADV_", "ASK_", "NO_"))}
    assert consts == set(expected) | set(plan5), consts ^ (set(expected) | set(plan5))


def test_ask_path_writes_the_ask_audit_line(tmp_path):
    cmd = "bash tools/resaver-cli.sh reset-havok s.ess --apply"
    run(cmd, tmp_path)
    log = (tmp_path / ".claude/backups/AUDIT_LOG.txt").read_text(encoding="utf-8")
    assert "protect-bash ASK: " + NEW_RESAVER in log


def test_rule_trace_records_the_rule_id(tmp_path):
    a = audit.Audit(str(tmp_path))
    r = paths.roots("/x/install", "", {"SKYRIM_GAME_ROOT": GAME})
    shell_rules.decide(f'rm -rf "{GAME}/Data"', r, a)
    assert a.fired == [("S-DEL-INSTALL", "deny")]
    a = audit.Audit(str(tmp_path))
    shell_rules.decide("echo x >> plugins.txt", r, a)
    assert a.fired == [("S-WRITE-LOADORDER", "advise")]
    a = audit.Audit(str(tmp_path))
    shell_rules.decide("bash tools/resaver-cli.sh reset-havok s.ess --apply", r, a)
    assert a.fired == [("S-RESAVER-APPLY", "ask")]
    a = audit.Audit(str(tmp_path))
    shell_rules.decide("", r, a)
    assert a.fired == [("S-INERT-NOCMD", "deny")]
