"""The save policy (user ruling 2026-10-13): saves.py, recovery.backup_saves, the hooks that call them.

  * 1 or 2 saves changed (a save and its .skse co-save are ONE save): the hook backs up BOTH files of each pair, each
    verified by SHA-256, records them, and only then allows the call.
  * more than 2, or a wildcard: refused until the USER types a SAVE_ACCESS_APPROVAL line (approvals.py); then backed
    up the same way and allowed.
  * reading, hashing, listing, copying out: free. Deleting or moving the Saves FOLDER: still denied.
  * any backup failure: denied (fail closed). Save backups are never pruned; a restore puts both files back, verified.

Every test uses fake saves in tmp_path. No real save is read or written.
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import (audit as audit_mod, backup, codes, engine, mcp_rules, paths, recovery, rules,  # noqa: E402
                      saves, shell_rules, snapcli, snapshot, write_rules)
from skyguard import save_store
from modding_harness import approval as scoped_approval, durable, preservation, workspace
from skyguard.adapters import codex  # noqa: E402
from skyguard.verdict import Verdict  # noqa: E402

NOTE = "Note: the guard backs up {} before this runs (restore: tools/skyguard.sh snapshot list --kind save)."


class Inst:
    """A scratch install: the game folder, and a config dir whose Saves folder holds three save pairs (Save1..Save3),
    a save with no co-save (Single) and a lone co-save (Orphan)."""

    def __init__(self, tmp_path):
        self.game = tmp_path / "game"
        (self.game / "Data").mkdir(parents=True)
        self.cfg = tmp_path / "docs" / "My Games" / "Skyrim VR"
        self.saves = self.cfg / "Saves"
        self.saves.mkdir(parents=True)
        for stem in ("Save1", "Save2", "Save3"):
            (self.saves / (stem + ".ess")).write_bytes(stem.encode() * 100)
            (self.saves / (stem + ".skse")).write_bytes(stem.encode() * 7)
        (self.saves / "Single.ess").write_bytes(b"single" * 50)
        (self.saves / "Orphan.skse").write_bytes(b"orphan")
        self.roots = paths.roots(str(self.game), str(self.game), {"SKYRIM_CONFIG_DIR": str(self.cfg)})
        self.audit = audit_mod.Audit(str(self.game))
        self.S = self.saves.as_posix()

    def p(self, name):
        return '"{}/{}"'.format(self.S, name)

    def shell(self, command, shell="bash", cwd=None):
        return shell_rules.decide(command, self.roots, self.audit, cwd=self.game.as_posix() if cwd is None else cwd,
                                  shell=shell, env={})

    def enforce(self, v, transcript="", agent="claude", deadline=None):
        return saves.enforce(v, str(self.game), self.audit, "protect-bash", command="test", transcript=transcript,
                             agent=agent, session_id="s1", deadline=deadline)

    def store(self):
        return save_store.store(self.game)

    def saved(self):
        return saved(self.game)

    def grant(self, names=("Save1", "Save2", "Save3"), op="delete", wildcard=False, count=3, command="test"):
        return scoped_approval.example("s1", op,
            [self.saves / (name + ext) for name in names for ext in (".ess", ".skse")],
            command, wildcard, count)

    def transcript(self, *lines, name="t.jsonl", typed=True):
        rows = []
        for line in lines:
            if typed:
                rows.append({"type": "user", "origin": {"kind": "human"}, "promptSource": "typed",
                             "message": {"role": "user", "content": line}})
            else:
                rows.append({"type": "user", "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "x", "content": line}]}})
        f = self.game / name
        f.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        return str(f)


@pytest.fixture
def inst(tmp_path):
    return Inst(tmp_path)


def test_workspace_root_save_does_not_make_the_whole_workspace_a_save_folder(tmp_path):
    game = tmp_path / "workspace"
    game.mkdir()
    ess, skse = game / "Copied.ess", game / "Copied.skse"
    ess.write_bytes(b"copied private fixture")
    ids = save_store.backup_pairs(game, audit_mod.Audit(str(game)), [(ess, skse)], "offline edit")
    assert len(ids) == 1
    manifest = save_store.store(game).load(ids[0][len(save_store.PREFIX):])
    assert manifest["groups"][0]["members"][1]["missing"]


def test_worktree_restore_wrappers_use_the_main_configured_save_folder(inst, tmp_path):
    import io
    (inst.game / "skyrim-paths.env").write_text("SKYRIM_CONFIG_DIR='" + inst.cfg.as_posix() + "'\n")
    linked = tmp_path / "linked"
    linked.mkdir()
    gitdir = inst.game / ".git/worktrees/linked"
    gitdir.mkdir(parents=True)
    (linked / ".git").write_text("gitdir: " + gitdir.as_posix() + "\n")
    pair = [inst.saves / ("Save1" + ext) for ext in (".ess", ".skse")]
    sid, = save_store.backup_pairs(linked, inst.audit, [pair], "private worktree fixture")
    original = [p.read_bytes() for p in pair]
    for p in pair:
        p.write_bytes(b"current")
    output = io.StringIO()
    assert save_store.restore(linked, sid, [], None, True, output) == 0, output.getvalue()
    assert [p.read_bytes() for p in pair] == original
    assert snapcli._allowed(linked, "20261015_120000-save", "unused", pair[0]) == ""
    transactions = workspace.summaries(save_store.store(linked))[0]
    assert len(transactions) >= 2


def test_configured_store_cannot_live_in_a_save_root_or_hold_a_source(tmp_path, monkeypatch):
    game = tmp_path / "workspace"
    game.mkdir()
    config = game / ".modding-agent-harness/config.json"
    config.parent.mkdir()
    cfg = tmp_path / "config"
    monkeypatch.setenv("SKYRIM_CONFIG_DIR", str(cfg))
    config.write_text(json.dumps({"save_store": str(cfg / "Saves/history")}))
    with pytest.raises(ValueError, match="separate from save roots"):
        save_store.store(game)
    config.write_text("{}")
    with pytest.raises(ValueError, match="overlaps"):
        save_store.store(game, [game / ".modding-agent-harness/backups/saves/inside.ess"])


def saved(project):
    records = []
    for manifest in workspace.summaries(save_store.store(project))[0]:
        for group in manifest["groups"]:
            missing = [r["path"] for r in group["members"] if r["missing"]]
            for member in group["members"]:
                if not member["missing"]:
                    records.append(dict(src=member["path"], snap="core-" + manifest["id"],
                                        group=group["logical_save"], sha256=member["sha256"], missing=missing))
    return records


def stems(v):
    return sorted({saves._split(t.path)[1] for t in v.saves.change.targets}) if v.saves else []


def ops(v):
    return sorted({t.op for t in v.saves.change.targets}) if v.saves else []


# ================================================================ what counts as a save change (judge)


def test_one_save_is_allowed_with_a_backup_note(inst):
    v = inst.shell("rm " + inst.p("Save1.ess"))
    assert (v.decision, v.context) == ("allow", NOTE.format("Save1"))
    assert stems(v) == ["Save1"] and ops(v) == ["delete"]


def test_a_cosave_named_alone_counts_its_whole_pair(inst):
    v = inst.shell("rm " + inst.p("Save1.skse"))
    assert (v.decision, v.context) == ("allow", NOTE.format("Save1"))
    c = saves.count(v.saves.change)
    assert [(Path(p.ess).name, Path(p.skse).name) for p in c.pairs] == [("Save1.ess", "Save1.skse")]
    assert inst.enforce(v).decision == "allow"
    assert sorted(Path(e["src"]).name for e in inst.saved()) == ["Save1.ess", "Save1.skse"]


def test_a_save_and_its_cosave_are_one_save(inst):
    v = inst.shell("rm " + inst.p("Save1.ess") + " " + inst.p("Save1.skse") + " " + inst.p("Save2.ess"))
    assert v.decision == "allow" and len(saves.count(v.saves.change).pairs) == 2


def test_two_saves_are_allowed_and_three_need_approval(inst):
    two = inst.shell("rm " + inst.p("Save1.ess") + " " + inst.p("Save2.ess"))
    assert (two.decision, two.context) == ("allow", NOTE.format("Save1, Save2"))
    three = inst.shell("rm " + " ".join(inst.p(s + ".ess") for s in ("Save1", "Save2", "Save3")))
    assert three.decision == "deny" and three.reason == rules.message(
        "S-SAVE-APPROVAL", op="deletes", n=3, file="Save1, Save2, Save3",
        save='{"op": "delete", "files": ["Save1", "Save2", "Save3"]}')


def test_the_deciding_hook_names_canonical_scope_instead_of_a_basename_grant(inst):
    v = inst.shell("rm " + " ".join(inst.p(s + ".ess") for s in ("Save1", "Save2", "Save3")))
    assert "check --preview" in v.reason and "only the user types SAVE_ACCESS_APPROVAL" in v.reason
    guarded = inst.enforce(v)
    line = guarded.reason.split("SAVE_ACCESS_APPROVAL ", 1)[1]
    expected = json.loads(inst.grant().split("SAVE_ACCESS_APPROVAL ", 1)[1])
    assert json.loads(line) == expected


def test_a_wildcard_needs_approval_even_when_it_matches_one_save(inst):
    v = inst.shell('rm "' + inst.S + '/"Sing*.ess')
    assert v.decision == "deny"
    scoped = inst.enforce(v)
    grant = json.loads(scoped.reason.split("SAVE_ACCESS_APPROVAL ", 1)[1])
    assert grant["count"] == 1 and len(grant["paths"]) == 2 and len(grant["command_sha256"]) == 64


def test_a_wildcard_in_a_folder_name_cannot_be_listed(inst):
    v = inst.shell('rm "' + inst.S + '"/../Sav*/Save1.ess')
    assert v.decision == "deny" and v.reason == rules.message("S-SAVE-UNKNOWN", op="deletes", file="Save1.ess")


def test_a_wildcard_in_a_folder_that_cannot_be_read_cannot_be_listed(inst):
    v = inst.shell('rm "' + inst.S + '/no such folder/"*.ess')
    assert v.decision == "deny" and v.reason == rules.message("S-SAVE-UNKNOWN", op="deletes", file="*.ess")


def test_a_quoted_wildcard_over_saves_is_a_save_change_not_a_settings_delete(inst):
    """A quoted `*` is literal to bash, so the delete rules read the word literally (deletes.literal_abs); it is still a
    save word, judged by the save policy (it needs approval), never refused as a delete of the settings folder."""
    v = inst.shell('rm "' + inst.S + '/Save*.ess"')
    assert v.decision == "deny" and v.reason.startswith("Blocked: this deletes 3 saves (Save1, Save2, Save3)")


def test_a_save_under_a_folder_with_glob_characters_in_its_name_is_still_one_save(tmp_path):
    """A settings folder spelled with `[x]` makes the delete rules read a quoted word literally (deletes.literal_abs):
    that literal save is a save change too, never refused as a delete of the settings folder."""
    cfg = tmp_path / "My Games [x]" / "Skyrim VR"
    (cfg / "Saves").mkdir(parents=True)
    (cfg / "Saves" / "Save1.ess").write_bytes(b"s")
    (tmp_path / "game").mkdir()
    roots = paths.roots(str(tmp_path / "game"), str(tmp_path / "game"), {"SKYRIM_CONFIG_DIR": str(cfg)})
    v = shell_rules.decide('rm "{}/Saves/Save1.ess"'.format(cfg.as_posix()), roots,
                           audit_mod.Audit(str(tmp_path / "game")), cwd=(tmp_path / "game").as_posix(), shell="bash", env={})
    assert v.decision != "deny" or "settings folder" not in v.reason
    assert v.saves is not None and v.saves.change.targets


def test_a_copy_into_the_saves_folder_that_takes_a_saves_name_is_a_change(inst, tmp_path):
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "Save1.ess").write_bytes(b"incoming")
    v = inst.shell('cp "{}" "{}/"'.format((tmp_path / "in" / "Save1.ess").as_posix(), inst.S))
    assert (v.decision, v.context) == ("allow", NOTE.format("Save1"))


def test_an_existing_save_outside_the_settings_folder_is_free(inst, tmp_path):
    """Only saves in the settings folder are judged (writes.is_save): a copy elsewhere is the user's own scratch."""
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "Out.ess").write_bytes(b"a scratch copy")
    v = inst.shell('cp /tmp/x "{}"'.format((tmp_path / "work" / "Out.ess").as_posix()))
    assert (v.decision, v.context, v.saves) == ("allow", "", None)
    w = write_rules.decide((tmp_path / "work" / "Out.ess").as_posix(), inst.audit, roots=inst.roots)
    assert (w.decision, w.context, w.saves) == ("allow", "", None)


def test_a_new_save_does_not_count_toward_the_limit(inst):
    v = inst.shell("rm " + inst.p("Save1.ess") + " " + inst.p("Save2.ess") + " " + inst.p("New9.ess"))
    assert (v.decision, v.context) == ("allow", NOTE.format("Save1, Save2"))


def test_one_save_spelled_in_two_cases_is_still_one_save(inst):
    v = inst.shell("rm " + inst.p("Save1.ess") + " " + inst.p("SAVE1.SKSE") + " " + inst.p("Save2.ess"))
    assert v.decision == "allow" and len(saves.count(v.saves.change).pairs) == 2


def test_a_new_save_changes_no_save(inst):
    v = inst.shell("cp /tmp/x " + inst.p("Brand New.ess"))
    assert (v.decision, v.context) == ("allow", "") and stems(v) == ["Brand New"]
    assert saves.count(v.saves.change).pairs[0].exists is False


@pytest.mark.parametrize("cmd", ["cp {S1} /tmp/out/", "sha256sum {S1} {S2} {S3}", "cat {S1}", "ls -la {dir}",
                                 "Get-FileHash {S1}", "Copy-Item {S1} C:/Temp/out.ess"],
                         ids=["copy-out", "hash", "read", "list", "ps-hash", "ps-copy-out"])
def test_reading_hashing_listing_and_copying_out_are_free(inst, cmd):
    text = cmd.format(S1=inst.p("Save1.ess"), S2=inst.p("Save2.ess"), S3=inst.p("Save3.ess"), dir='"' + inst.S + '"')
    v = inst.shell(text, shell="powershell" if cmd[0].isupper() else "bash")
    assert (v.decision, v.reason, v.context, v.saves) == ("allow", "", "", None)


@pytest.mark.parametrize("cmd,rid", [('rm -rf "{dir}"', "S-DEL-CONFIG"), ('mv "{dir}" /tmp/old', "S-MOVE-ROOT"),
                                     ('Remove-Item -Recurse "{dir}"', "S-DEL-CONFIG")],
                         ids=["rm-folder", "mv-folder", "ps-remove-folder"])
def test_the_whole_saves_folder_stays_a_hard_deny(inst, cmd, rid):
    v = inst.shell(cmd.format(dir=inst.S), shell="powershell" if cmd[0].isupper() else "bash")
    assert v.decision == "deny" and v.reason == rules.message(rid, file="Saves")


@pytest.mark.parametrize("cmd,shell,want_ops", [
    ("echo x > {S1}", "bash", ["write"]),
    ("cp /tmp/x {S1}", "bash", ["write"]),
    ("mv {S1} {S9}", "bash", ["move", "write"]),
    ("mv {S1} /tmp/", "bash", ["move"]),
    ("Remove-Item {S1}", "powershell", ["delete"]),
    ("Set-Content {S1} x", "powershell", ["write"]),
    ("Move-Item {S1} C:/Temp/", "powershell", ["move"]),
    ("Rename-Item {S1} -NewName Save9.ess", "powershell", ["move", "write"]),
    ("cmd /c del {S1}", "bash", ["delete"]),
    ("python -c \"open('{raw}','wb').write(b'x')\"", "bash", ["write"]),
    ("[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile('{raw}','OnlyErrorDialogs','SendToRecycleBin')",
     "powershell", ["delete"]),
], ids=["redirect", "cp-onto", "mv-rename", "mv-out", "ps-remove", "ps-set-content", "ps-move-out", "ps-rename",
        "cmd-del", "python-literal", "recycle-bin"])
def test_every_change_shape_is_one_backed_up_save(inst, cmd, shell, want_ops):
    text = cmd.format(S1=inst.p("Save1.ess"), S9=inst.p("Save9.ess"), raw=inst.S + "/Save1.ess")
    v = inst.shell(text, shell=shell)
    assert (v.decision, v.context) == ("allow", NOTE.format("Save1")), text
    assert ops(v) == want_ops


@pytest.mark.parametrize("tail", ["", r"\0\uD800", r"\0\Uffffffff", r"\Uffffffff", r"\U80000000"])
@pytest.mark.parametrize("escape", [r"\x65", r"\145", r"\u0065", r"\U00000065"])
def test_ansi_c_save_suffix_keeps_complete_verified_pair(inst, escape, tail):
    command = "printf x > $'" + inst.S + "/Save1." + escape + "ss" + tail + "'"
    verdict = inst.shell(command)
    assert stems(verdict) == ["Save1"]
    result = inst.enforce(verdict)
    assert result.decision == "allow"
    assert len(inst.saved()) == 2
    assert sorted(row["sha256"] for row in inst.saved()) == sorted(
        hashlib.sha256((inst.saves / ("Save1" + suffix)).read_bytes()).hexdigest() for suffix in (".ess", ".skse"))


def test_ansi_c_bulk_save_changes_keep_approval_boundary(inst):
    command = "; ".join("printf x > $'" + inst.S + "/Save" + str(i) + r".\x65ss'" for i in (1, 2, 3))
    verdict = inst.shell(command)
    assert stems(verdict) == ["Save1", "Save2", "Save3"]
    assert inst.enforce(verdict).decision == "deny"
    assert not inst.saved()


@pytest.mark.parametrize("tail", ["", r"\0\uD800", r"\0\Uffffffff", r"\Uffffffff", r"\U80000000"])
@pytest.mark.parametrize("escape", [r"\x65", r"\145", r"\u0065", r"\U00000065"])
def test_ansi_c_plugin_suffix_remains_a_raw_binary_write_refusal(inst, escape, tail):
    verdict = inst.shell("printf x > $'" + inst.game.as_posix() + "/Data/plugin." + escape + "sp" + tail + "'")
    assert verdict.decision == "deny"
    assert "raw" in verdict.reason.lower() or "text" in verdict.reason.lower()


def test_a_save_change_beside_another_rules_deny_still_carries_its_plan(inst):
    """Shadow mode can let a denied call run, so its saves are still backed up (the plan rides on the deny)."""
    v = inst.shell("rm " + inst.p("Save1.ess") + " && rm -rf " + '"' + inst.game.as_posix() + '/Data"')
    assert v.decision == "deny" and stems(v) == ["Save1"]


# ---------------------------------------------------------------- ReSaver and DevBench


def test_resaver_apply_on_a_placed_save_is_backed_up(inst):
    v = inst.shell("bash tools/resaver-cli.sh reset-havok " + inst.p("Save2.ess") + " --apply")
    assert (v.decision, v.context) == ("allow", NOTE.format("Save2"))


def test_resaver_apply_by_bare_name_finds_the_saves_folder(inst):
    v = inst.shell("bash tools/resaver-cli.sh reset-havok Save3.ess --apply")
    assert (v.decision, v.context) == ("allow", NOTE.format("Save3"))


@pytest.mark.parametrize("cmd,save", [("reset-havok '*.ess'", "*.ess"), ("reset-havok", "a save it does not name")],
                         ids=["pattern", "no-save"])
def test_resaver_apply_on_a_save_it_cannot_place_is_refused(inst, cmd, save):
    v = inst.shell("bash tools/resaver-cli.sh " + cmd + " --apply")
    assert v.decision == "deny" and v.reason == rules.message("S-RESAVER-APPLY", op="reset-havok", save=save)


def test_resaver_apply_by_relative_name_with_no_cwd_is_refused(inst):
    v = inst.shell("bash tools/resaver-cli.sh reset-havok Save3.ess --apply", cwd="")
    assert v.decision == "deny" and "writes Save3.ess, which the guard cannot place" in v.reason


def mcp(inst, args):
    return mcp_rules.decide("mcp__devbench-vr__game", args, inst.audit, roots=inst.roots, env={})


def test_a_devbench_save_over_an_existing_save_is_backed_up(inst):
    v = mcp(inst, {"action": "save", "name": "Save1"})
    assert (v.decision, v.context) == ("allow", NOTE.format("Save1"))


def test_a_devbench_scenario_saving_three_existing_saves_needs_approval(inst):
    v = mcp(inst, {"steps": [{"tool": "game", "args": {"action": "save", "name": n}} for n in ("Save1", "Save2", "Save3")]})
    assert v.decision == "deny" and v.reason.startswith("Blocked: this changes 3 saves (Save1, Save2, Save3)")


def test_a_devbench_save_it_cannot_identify_is_refused(inst):
    v = mcp(inst, {"action": "save", "name": "a/b"})
    assert v.decision == "deny" and v.reason == rules.message("S-DEVBENCH-SAVE", op="Cannot tell which save this overwrites.")


def test_a_devbench_save_under_a_new_name_is_free(inst):
    assert mcp(inst, {"action": "save", "name": "Brand New"})[:3] == ("allow", "", "")


@pytest.mark.parametrize("route", ["mcp", "cli", "rest", "papyrus"])
@pytest.mark.parametrize("names", [("New", "Save2"), ("Save1", "Save2"),
                                   ("Save1", "Save2", "Save3")])
def test_every_console_save_statement_is_preserved(inst, route, names):
    command = "; ".join("save " + name for name in names)
    args = {"command": command}
    if route == "mcp":
        v = mcp_rules.decide("mcp__devbench-vr__console", args, inst.audit, roots=inst.roots, env={})
    elif route == "papyrus":
        v = mcp_rules.decide("mcp__devbench-vr__papyrus",
                            {"function": "ExecuteCommand", "args": [command]},
                            inst.audit, roots=inst.roots, env={})
    elif route == "cli":
        v = inst.shell("bash tools/devbench-cli.sh exec '" + command + "'")
    else:
        v = inst.shell("curl http://localhost:8920/api/tool/console -d '" + json.dumps(args) + "'")
    expected = sorted(n for n in names if n != "New")
    assert stems(v) == expected
    guarded = inst.enforce(v)
    assert guarded.decision == ("deny" if len(expected) > 2 else "allow")
    assert sorted(Path(row["src"]).name for row in inst.saved()) == (
        [] if len(expected) > 2 else sorted(n + ext for n in expected for ext in (".ess", ".skse")))


def test_every_console_string_is_collected_after_the_first_hit(inst):
    v = mcp_rules.decide("mcp__devbench-vr__console",
                        {"command": ["save Save1", "save Save2", "save Save3"]},
                        inst.audit, roots=inst.roots, env={})
    assert stems(v) == ["Save1", "Save2", "Save3"]
    assert inst.enforce(v).decision == "deny" and inst.saved() == []


@pytest.mark.parametrize("count", [2, 3])
def test_every_compound_resaver_output_is_preserved(inst, count):
    command = "; ".join("bash tools/resaver-cli.sh reset-havok input.ess " +
                        inst.p("Save%d.ess" % n) + " --apply" for n in range(1, count + 1))
    v = inst.shell(command)
    assert stems(v) == ["Save%d" % n for n in range(1, count + 1)]
    assert inst.enforce(v).decision == ("allow" if count == 2 else "deny")
    assert len(inst.saved()) == (4 if count == 2 else 0)


def test_compound_resaver_outputs_keep_each_executions_directory(inst, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "Save1.ess").write_bytes(b"different private save")
    v = inst.shell('cd "' + other.as_posix() + '"; bash tools/resaver-cli.sh reset-havok input.ess Save1.ess --apply; '
                   'cd "' + inst.S + '"; bash tools/resaver-cli.sh reset-havok input.ess Save2.ess --apply')
    assert sorted(t.path.casefold() for t in v.saves.change.targets) == sorted(
        [str(other / "Save1.ess").replace("\\", "/").casefold(), (inst.S + "/Save2.ess").casefold()])


def test_known_resaver_does_not_hide_later_opaque_resaver_outputs(inst):
    command = ("bash tools/resaver-cli.sh reset-havok input.ess " + inst.p("Save1.ess") + " --apply; "
               "R=$(echo tools/resaver-cli.sh); $R reset-havok input.ess " + inst.p("Save2.ess") + " --apply")
    v = inst.shell(command)
    assert stems(v) == ["Save1", "Save2"]
    assert inst.enforce(v).decision == "allow" and len(inst.saved()) == 4


def test_opaque_resaver_after_unknown_cd_keeps_unresolved_scope(inst):
    command = ("bash tools/resaver-cli.sh reset-havok input.ess " + inst.p("Save1.ess") + " --apply; "
               "cd $UNRESOLVED; R=$(echo tools/resaver-cli.sh); $R reset-havok input.ess Save2.ess --apply")
    v = inst.shell(command)
    assert v.decision == "deny" and v.saves.change.unknown
    assert inst.enforce(v).decision == "deny" and inst.saved() == []


@pytest.mark.parametrize("program", ["cp", "mv"])
def test_copy_folder_collects_every_save_destination(inst, tmp_path, program):
    source = tmp_path / "incoming"
    source.mkdir()
    for n in range(1, 4):
        (source / ("Save%d.ess" % n)).write_bytes(b"incoming")
    command = program + " " + " ".join('"' + (source / ("Save%d.ess" % n)).as_posix() + '"'
                                        for n in range(1, 4)) + ' "' + inst.S + '/"'
    v = inst.shell(command)
    assert stems(v) == ["Save1", "Save2", "Save3"]
    assert inst.enforce(v).decision == "deny" and inst.saved() == []


@pytest.mark.parametrize("first_recycled", [False, True])
def test_recycle_saves_are_collected_beside_another_deny(inst, first_recycled):
    def recycle(path):
        return "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile('" + path + "','OnlyErrorDialogs','SendToRecycleBin')"
    first = recycle(inst.game.as_posix() + "/owned.dll") if first_recycled else (
        'Remove-Item "' + inst.game.as_posix() + '/owned.dll"')
    v = inst.shell(first + "; " + recycle(inst.S + "/Save1.ess") + "; " + recycle(inst.S + "/Save2.ess"),
                   shell="powershell")
    assert v.decision == "deny" and stems(v) == ["Save1", "Save2"]
    # Guards-off can shadow the unrelated denial; the complete preservation plan must survive it.
    lifted = saves.judge(v.saves.change, Verdict(), inst.audit, "protect-bash")
    assert inst.enforce(lifted).decision == "allow"
    assert len(inst.saved()) == 4


# ---------------------------------------------------------------- edit tools and patches


def test_an_edit_tool_write_onto_a_save_is_backed_up(inst):
    v = write_rules.decide(inst.S + "/Single.ess", inst.audit, roots=inst.roots)
    assert (v.decision, v.context) == ("allow", NOTE.format("Single"))


def codex_pre(inst, tool_name, tool_input, transcript="", cwd=None):
    d = {"hook_event_name": "PreToolUse", "tool_name": tool_name, "cwd": cwd or inst.game.as_posix(),
         "tool_input": tool_input, "session_id": "s1"}
    if transcript:
        d["transcript_path"] = transcript
    req = codex.to_request(json.dumps(d), "win32")
    return engine.pre_tool(req, str(inst.game), lambda: inst.roots, inst.audit)


def _patch(*lines):
    return {"command": "\n".join(("*** Begin Patch",) + lines + ("*** End Patch",))}


def test_one_patch_that_changes_three_saves_is_judged_as_three(inst):
    """engine.combine judges the saves of every path in a patch TOGETHER: three one-save edits are three saves."""
    v = codex_pre(inst, "apply_patch", _patch(*["*** Add File: {}/{}.ess\n+x".format(inst.S, s)
                                                  for s in ("Save1", "Save2", "Save3")]))
    assert v.decision == "deny" and v.code == "shell.S-SAVE-APPROVAL"
    assert inst.saved() == []


def test_a_patch_delete_or_move_is_judged_as_that_op(inst):
    """The op decides which approval covers it: a patch Delete is a delete, a Move away is a move."""
    from skyguard.request import Request
    req = Request(kind="write", paths=(inst.S + "/Save1.ess", inst.S + "/Save2.ess", "C:/Temp/x.ess"),
                  ops=("delete", "move_from", "move_to"), cwd=inst.game.as_posix())
    v = engine.evaluate_writes(req, inst.audit, roots=inst.roots)
    assert sorted((Path(t.path).name, t.op) for t in v.saves.change.targets) == [("Save1.ess", "delete"),
                                                                                 ("Save2.ess", "move")]


def test_a_patch_delete_of_one_save_is_backed_up_then_allowed_on_codex(inst):
    v = codex_pre(inst, "apply_patch", _patch("*** Delete File: {}/Save2.skse".format(inst.S)))
    assert (v.decision, v.code) == ("allow", "shell.S-SAVE-BACKUP")
    assert sorted(Path(e["src"]).name for e in inst.saved()) == ["Save2.ess", "Save2.skse"]


# ================================================================ the backup (enforce, recovery.backup_saves)


def test_enforce_backs_up_both_files_verified_before_it_allows(inst):
    v = inst.enforce(inst.shell("rm " + inst.p("Save1.ess")))
    assert (v.decision, v.code) == ("allow", "shell.S-SAVE-BACKUP")
    lines = inst.saved()
    assert sorted(Path(e["src"]).name for e in lines) == ["Save1.ess", "Save1.skse"]
    assert len({e["snap"] for e in lines}) == 1 and all(e["missing"] == [] for e in lines)
    blobs = inst.store().root / "blobs"
    for e in lines:
        assert backup._sha256(blobs / e["sha256"]) == e["sha256"] == backup._sha256(Path(e["src"]))
    assert "SAVE-BACKUP" in (inst.game / ".claude" / "backups" / "AUDIT_LOG.txt").read_text(encoding="utf-8")


def test_two_saves_are_one_transaction_with_two_complete_groups(inst):
    inst.enforce(inst.shell("rm " + inst.p("Save1.ess") + " " + inst.p("Save2.skse")))
    by = {}
    for e in inst.saved():
        by.setdefault((e["snap"], e["group"]), []).append(Path(e["src"]).name)
    assert sorted(sorted(x) for x in by.values()) == [["Save1.ess", "Save1.skse"], ["Save2.ess", "Save2.skse"]]


def test_a_missing_cosave_is_recorded_as_missing_not_an_error(inst):
    v = inst.enforce(inst.shell("rm " + inst.p("Single.ess")))
    assert v.decision == "allow"
    (line,) = inst.saved()
    assert Path(line["src"]).name == "Single.ess" and [Path(m).name for m in line["missing"]] == ["Single.skse"]


def test_a_lone_cosave_is_backed_up_with_its_save_recorded_missing(inst):
    v = inst.enforce(inst.shell("rm " + inst.p("Orphan.skse")))
    assert v.decision == "allow"
    (line,) = inst.saved()
    assert [Path(m).name for m in line["missing"]] == ["Orphan.ess"]


def test_a_new_save_takes_no_backup(inst):
    v = inst.enforce(inst.shell("cp /tmp/x " + inst.p("Brand New.ess")))
    assert v.decision == "allow" and inst.saved() == []


def failed(v):
    return v.decision == "deny" and v.code == "shell.S-SAVE-BACKUP-FAILED"


def test_a_backup_that_would_fill_the_drive_denies(inst, monkeypatch):
    monkeypatch.setattr(workspace, "DEFAULT_RESERVE", 1 << 60)
    v = inst.enforce(inst.shell("rm " + inst.p("Save1.ess")))
    assert failed(v) and "insufficient destination space" in v.reason and inst.saved() == []


def test_an_unreadable_save_denies(inst, monkeypatch):
    real = durable.copy_verified

    def boom(src, part, deadline=None):
        if str(src).endswith(".skse"):
            raise PermissionError("Save1.skse could not be read")
        return real(src, part, deadline)
    monkeypatch.setattr(durable, "copy_verified", boom)
    v = inst.enforce(inst.shell("rm " + inst.p("Save1.ess")))
    assert failed(v) and "Save1.skse could not be read" in v.reason and inst.saved() == []


def test_a_copy_that_does_not_verify_denies(inst, monkeypatch):
    real = durable.digest
    monkeypatch.setattr(durable, "digest", lambda p, deadline=None: "0" * 64 if str(p).endswith(".part") else real(p, deadline))
    v = inst.enforce(inst.shell("rm " + inst.p("Save1.ess")))
    assert failed(v) and "failed size/hash verification" in v.reason and inst.saved() == []


def test_a_damaged_reused_blob_refuses_and_is_retained_for_diagnosis(inst):
    v0 = inst.shell("rm " + inst.p("Save1.ess"))
    blobs = inst.store().root / "blobs"
    blobs.mkdir(parents=True)
    good = backup._sha256(inst.saves / "Save1.ess")
    (blobs / good).write_bytes(b"torn")
    assert failed(inst.enforce(v0))
    assert (blobs / good).read_bytes() == b"torn" and not inst.saved()


def test_a_copy_that_runs_out_of_time_denies(inst, monkeypatch):
    v = inst.enforce(inst.shell("rm " + inst.p("Save1.ess")), deadline=0.0)
    assert failed(v) and inst.saved() == []


def test_a_busy_store_denies(inst, monkeypatch):
    def busy(*a, **k):
        raise OSError("held")
    monkeypatch.setattr(preservation, "exclusive", busy)
    v = inst.enforce(inst.shell("rm " + inst.p("Save1.ess")))
    assert failed(v) and "held" in v.reason


def test_a_manifest_that_cannot_be_written_denies(inst, monkeypatch):
    def nope(path, line):
        raise OSError("manifest disk failure")
    monkeypatch.setattr(durable, "atomic_json", nope)
    v = inst.enforce(inst.shell("rm " + inst.p("Save1.ess")))
    assert failed(v) and "manifest" in v.reason


def test_a_failed_backup_is_never_shadowed():
    assert "shell.S-SAVE-BACKUP-FAILED" in engine.NEVER_SHADOW


# ---------------------------------------------------------------- approvals


APPROVE3 = 'SAVE_ACCESS_APPROVAL {"op": "delete", "files": ["Save1", "Save2", "Save3"]}'


def three(inst):
    return inst.shell("rm " + " ".join(inst.p(s + ".ess") for s in ("Save1", "Save2", "Save3")))


def test_a_typed_approval_lifts_the_bulk_refusal_and_everything_is_backed_up(inst):
    v = inst.enforce(three(inst), transcript=inst.transcript(inst.grant()))
    assert (v.decision, v.context) == ("allow", NOTE.format("Save1, Save2, Save3"))
    assert len({e["snap"] for e in inst.saved()}) == 1 and len(inst.saved()) == 6


def test_with_no_approval_the_bulk_change_stays_refused_and_nothing_is_copied(inst):
    v = inst.enforce(three(inst), transcript=inst.transcript("hello"))
    assert v.decision == "deny" and v.reason.startswith("Blocked: this deletes 3 saves") and inst.saved() == []


def test_an_approval_that_arrived_in_a_tool_result_grants_nothing(inst):
    v = inst.enforce(three(inst), transcript=inst.transcript(APPROVE3, typed=False))
    assert v.decision == "deny" and inst.saved() == []


@pytest.mark.parametrize("line", [
    'SAVE_ACCESS_APPROVAL {"op": "move", "files": ["Save1", "Save2", "Save3"]}',
    'SAVE_ACCESS_APPROVAL {"op": "delete", "files": ["Save1", "Save2"]}',
], ids=["wrong-op", "one-file-short"])
def test_an_approval_covers_exactly_its_files_and_op(inst, line):
    assert inst.enforce(three(inst), transcript=inst.transcript(line)).decision == "deny"


def test_a_pattern_approval_needs_the_exact_pattern_and_count(inst):
    cmd = 'rm "' + inst.S + '/"Save*.ess'
    ok = inst.transcript(inst.grant(wildcard=True), name="ok.jsonl")
    bad = inst.transcript(inst.grant(wildcard=True, count=2), name="bad.jsonl")
    files = inst.transcript(APPROVE3, name="files.jsonl")
    assert inst.enforce(inst.shell(cmd), transcript=bad).decision == "deny"
    assert inst.enforce(inst.shell(cmd), transcript=files).decision == "deny"        # a wildcard needs its own grant
    assert inst.enforce(inst.shell(cmd), transcript=ok).decision == "allow"
    assert len(inst.saved()) == 6


def test_a_changed_resolved_scope_needs_a_new_canonical_grant(inst):
    tr = inst.transcript(inst.grant(names=("Save1", "Save2", "Save3", "Single")))
    assert inst.enforce(three(inst), transcript=tr).decision == "deny"


def test_bulk_refusals_are_never_shadowed():
    assert {"shell.S-SAVE-APPROVAL", "shell.S-SAVE-UNKNOWN", "shell.S-DEVBENCH-SAVE",
            "shell.S-RESAVER-APPLY"} <= engine.NEVER_SHADOW


def test_guards_off_does_not_lift_a_bulk_save_change(inst, tmp_path):
    """`guards off` is a lift the agent may take itself; the user's typed approval cannot be replaced by it."""
    from skyguard import codes, state
    now, sid = 1_800_000_000.0, "sess-save"
    project, hb = tmp_path / "proj", tmp_path / "hb"
    state.write(str(project / ".skyguard" / "state.json"), {"v": 1, "persistent": {}, "lifts": [
        {"kind": "guards", "reason": "t", "session_id": sid, "agent": "claude", "created": now - 60,
         "expires_at": now + 3600}]})
    f = hb / "sessions" / (sid + ".log")
    f.parent.mkdir(parents=True)
    f.write_text("{:.6f} start startup t.jsonl\n".format(now - 120), encoding="utf-8")
    bulk = codes.classify(three(inst))
    kept = engine.shadow(bulk, str(project), sid, str(hb), audit_mod.Audit(str(project)), now=now)
    assert (kept.decision, kept.code) == ("deny", "shell.S-SAVE-APPROVAL")
    other = codes.classify(Verdict("deny", rules.message("S-DEL-INSTALL", file="x.nif")))
    assert engine.shadow(other, str(project), sid, str(hb), audit_mod.Audit(str(project)), now=now).decision == "allow"


def test_an_unnamed_program_naming_a_save_asks_and_a_change_it_hides_is_still_backed_up(inst):
    """User ruling 2026-10-19 ("ask first"): `$R <save>` may write, move or delete it -- the guard cannot tell. When a
    reading of the same command does change the save (`$R cp a.ess <save>`: R empty runs cp), the pair is still backed
    up before the ask goes to the user."""
    v = inst.shell("$R " + inst.p("Save1.ess"))
    assert (v.decision, codes.classify(v).code) == ("ask", "shell.S-SAVE-OPAQUE"), v
    v = inst.enforce(inst.shell("$R cp a.ess " + inst.p("Save1.ess")))
    assert (v.decision, codes.classify(v).code) == ("ask", "shell.S-SAVE-OPAQUE") and len(inst.saved()) == 2, v
    assert inst.shell("echo " + inst.p("Save1.ess")).decision == "allow"       # twin: a NAMED program
    # twin: a word WITH a candidate (`${R:-rm}`: rm) is read as it; that reading's save change is counted and backed up
    v = inst.shell("${R:-rm} " + inst.p("Save1.ess"))
    assert (v.decision, codes.classify(v).code) == ("allow", "shell.S-SAVE-BACKUP"), v


def test_guards_off_does_not_lift_the_unnamed_program_save_ask(inst, tmp_path):
    from skyguard import state
    now, sid = 1_800_000_000.0, "sess-opaque"
    project, hb = tmp_path / "proj", tmp_path / "hb"
    state.write(str(project / ".skyguard" / "state.json"), {"v": 1, "persistent": {}, "lifts": [
        {"kind": "guards", "reason": "t", "session_id": sid, "agent": "claude", "created": now - 60,
         "expires_at": now + 3600}]})
    f = hb / "sessions" / (sid + ".log")
    f.parent.mkdir(parents=True)
    f.write_text("{:.6f} start startup t.jsonl\n".format(now - 120), encoding="utf-8")
    asked = codes.classify(inst.shell("$R " + inst.p("Save1.ess")))
    kept = engine.shadow(asked, str(project), sid, str(hb), audit_mod.Audit(str(project)), now=now)
    assert (kept.decision, kept.code) == ("ask", "shell.S-SAVE-OPAQUE")


def test_codex_bulk_change_reads_only_the_tui_users_approval(inst):
    meta = {"type": "session_meta", "payload": {"originator": "codex-tui", "id": "s1"}}
    item = {"type": "event_msg", "payload": {"type": "item_completed", "item": {
        "type": "UserMessage", "content": [{"type": "text", "text": inst.grant()}]}}}
    tr = inst.game / "rollout.jsonl"
    tr.write_text(json.dumps(meta) + "\n" + json.dumps(item) + "\n", encoding="utf-8")
    cmd = {"command": "rm " + " ".join(inst.p(s + ".ess") for s in ("Save1", "Save2", "Save3"))}
    assert codex_pre(inst, "Bash", cmd).decision == "deny"
    v = codex_pre(inst, "Bash", cmd, transcript=str(tr))
    assert (v.decision, v.code) == ("allow", "shell.S-SAVE-BACKUP") and len(inst.saved()) == 6


# ================================================================ kept forever, and restored as a pair


def test_save_snapshots_are_never_pruned(inst):
    inst.enforce(inst.shell("rm " + inst.p("Save1.ess")))
    store = inst.store().root
    later = recovery.time.time() + 3650 * recovery.DAY
    recovery.prune(store, later, inst.audit)
    assert len(inst.saved()) == 2
    assert all((store / "blobs" / e["sha256"]).is_file() for e in inst.saved())


def test_an_old_config_snapshot_is_still_pruned(inst):
    """Only saves are kept forever: the config pruning still runs on the same store."""
    store = recovery.store_dir(str(inst.game))
    (store / "blobs").mkdir(parents=True)
    old = recovery._line("20200101_000000", 1, "c:/x/Skyrim.ini", "a" * 64, 1, "config", "20200101_000000-config",
                         "x", "", "", 1, ["c:/x/Skyrim.ini"])
    new = dict(old, snap="20200102_000000-config", ts="20200102_000000", t=2, dest="b" * 64, sha256="b" * 64)
    (store / recovery.MANIFEST).write_text(json.dumps(old) + "\n" + json.dumps(new) + "\n", encoding="ascii")
    recovery.prune(store, 10 ** 10, inst.audit)
    assert [e["snap"] for e in recovery.read_manifest(store)[0]] == ["20200102_000000-config"]


def _snap(inst):
    return inst.saved()[0]["snap"]


def test_a_restore_puts_both_files_of_the_pair_back_verified(inst, tmp_path):
    inst.enforce(inst.shell("rm " + inst.p("Save1.ess")))
    sid = _snap(inst)
    (inst.saves / "Save1.ess").unlink()
    (inst.saves / "Save1.skse").write_bytes(b"changed")
    out = tmp_path / "restored"
    import io
    buf = io.StringIO()
    assert snapcli.cmd_restore(str(inst.game), sid, [], str(out), False, buf) == 0
    got = sorted(p.name for p in out.rglob("*") if p.is_file())
    assert got == ["Save1.ess", "Save1.skse"]
    assert next(out.rglob("Save1.ess")).read_bytes() == b"Save1" * 100
    assert next(out.rglob("Save1.skse")).read_bytes() == b"Save1" * 7


def test_a_restore_refuses_a_damaged_backup_and_writes_nothing(inst, tmp_path):
    inst.enforce(inst.shell("rm " + inst.p("Save1.ess")))
    sid, sha = _snap(inst), inst.saved()[0]["sha256"]
    (inst.store().root / "blobs" / sha).write_bytes(b"rot")
    import io
    out = tmp_path / "restored"
    assert snapcli.cmd_restore(str(inst.game), sid, [], str(out), False, io.StringIO()) == 2
    assert not out.exists()


def test_a_restore_that_does_not_verify_at_its_target_writes_nothing(inst, tmp_path, monkeypatch):
    inst.enforce(inst.shell("rm " + inst.p("Save1.ess")))
    store, sha = inst.store().root, inst.saved()[0]["sha256"]
    real = backup._sha256
    monkeypatch.setattr(backup, "_sha256", lambda p: "0" * 64 if ".restore-part-" in str(p) else real(p))
    target = tmp_path / "t" / "Save1.ess"
    with pytest.raises(OSError):
        snapshot.write_blobs(store, [(target, sha)])
    assert not target.exists()


def test_a_restore_of_a_pair_without_its_cosave_says_so(inst, tmp_path):
    inst.enforce(inst.shell("rm " + inst.p("Single.ess")))
    import io
    buf = io.StringIO()
    assert snapcli.cmd_restore(str(inst.game), _snap(inst), [], str(tmp_path / "r"), False, buf) == 0
    assert "Single.skse did not exist when this save was backed up" in buf.getvalue()


def test_the_listing_names_each_pair_and_a_missing_cosave(inst):
    inst.enforce(inst.shell("rm " + inst.p("Single.ess") + " " + inst.p("Save2.ess")))
    import io
    buf = io.StringIO()
    snapcli.cmd_list(str(inst.game), "save", buf)
    text = buf.getvalue()
    assert "Save2.ess + Save2.skse" in text and "Single.ess, no Single.skse" in text and "kept forever" in text


# ================================================================ the hooks: backup happens in the deciding hook, first


def test_claude_protect_bash_backs_up_before_it_answers(tmp_path):
    """Through run.py in process (the real adapter, engine, render): the verdict is printed only after the copy."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("record", REPO / "scripts" / "guard-corpus" / "record.py")
    rec = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rec)
    install = rec.make_install(tmp_path, REPO / ".claude" / "hooks", "saves-bulk")
    S = (install / "cfg" / "Saves").as_posix()
    got = rec.fire_inprocess(install, "protect-bash.sh", {"tool_name": "Bash", "session_id": "s",
                                                          "tool_input": {"command": 'rm "' + S + '/Pair.ess"'}})
    assert got["decision"] == "advise" and "Pair" in got["text"]
    lines = saved(install)
    assert sorted(Path(e["src"]).name for e in lines) == ["Pair.ess", "Pair.skse"]


def test_claude_protect_files_backs_up_before_it_answers(tmp_path):
    rec = _rec()
    install = rec.make_install(tmp_path, REPO / ".claude" / "hooks", "saves-bulk")
    S = (install / "cfg" / "Saves").as_posix()
    got = rec.fire_inprocess(install, "protect-files.sh", {"tool_name": "Write", "session_id": "s",
                                                           "tool_input": {"file_path": S + "/Pair.skse", "content": "x"}})
    assert got["decision"] == "advise" and "Pair" in got["text"]
    lines = saved(install)
    assert sorted(Path(e["src"]).name for e in lines) == ["Pair.ess", "Pair.skse"]


def test_the_neutral_mcp_route_backs_up_before_it_answers(inst):
    from skyguard import neutral
    raw = json.dumps({"v": 1, "event": "pre_tool", "agent": "x", "kind": "mcp", "mcp_tool": "mcp__devbench-vr__game",
                      "mcp_args": json.dumps({"action": "save", "name": "Save3"}), "tool_name": "game"})
    text, v = neutral.handle(raw, str(inst.game), lambda: inst.roots, inst.audit)
    assert (v.decision, v.code) == ("allow", "shell.S-SAVE-BACKUP")
    assert sorted(Path(e["src"]).name for e in inst.saved()) == ["Save3.ess", "Save3.skse"]


def test_a_backup_that_raises_inside_the_hook_denies(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("record", REPO / "scripts" / "guard-corpus" / "record.py")
    rec = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rec)
    install = rec.make_install(tmp_path, REPO / ".claude" / "hooks", "saves-bulk")
    run = rec._run_module()

    def boom(*a, **k):
        raise RuntimeError("x")
    from modding_harness.preservation import SaveStore
    monkeypatch.setattr(SaveStore, "backup", boom)
    S = (install / "cfg" / "Saves").as_posix()
    got = rec.fire_inprocess(install, "protect-bash.sh", {"tool_name": "Bash", "session_id": "s",
                                                          "tool_input": {"command": 'rm "' + S + '/Pair.ess"'}})
    assert got["decision"] == "deny" and got["text"].startswith("Blocked: the backup of the saves failed")


def test_a_dispatcher_that_raises_inside_the_hook_denies(tmp_path, monkeypatch):
    from skyguard import portable
    rec = _rec()
    install = rec.make_install(tmp_path, REPO / ".claude/hooks", "saves-bulk")
    def fail(*args, **kwargs):
        raise RuntimeError("dispatcher failed before returning a verdict")
    monkeypatch.setattr(portable, "legacy_pre_tool", fail)
    folder = (install / "cfg/Saves").as_posix()
    got = rec.fire_inprocess(install, "protect-bash.sh", {"tool_name": "Bash", "session_id": "s",
                             "tool_input": {"command": 'rm "' + folder + '/Pair.ess"'}})
    assert got["decision"] == "deny" and got["text"].startswith("Blocked: the backup of the saves failed")


def test_the_snapshot_hook_no_longer_copies_saves(inst):
    """Saves are backed up by the deciding hook only; the parallel snapshot hook copies the load order and INIs."""
    recovery.run(str(inst.game), "rm " + inst.p("Save1.ess"), False, inst.audit, inst.roots, env={})
    assert inst.saved() == []


# ================================================================ the corpus rows, in process (mutations name them)


def _rec():
    import importlib.util
    spec = importlib.util.spec_from_file_location("record", REPO / "scripts" / "guard-corpus" / "record.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _sp_rows():
    text = (REPO / "tests" / "guard_corpus" / "intended.jsonl").read_text(encoding="utf-8")
    return [r for r in (json.loads(x) for x in text.split("\n") if x.strip()) if r["task"] == "SAVE-POLICY"]


SP_ROWS = _sp_rows()


@pytest.fixture(scope="module")
def sp_installs(tmp_path_factory):
    rec = _rec()
    base = tmp_path_factory.mktemp("sp")
    return rec, {n: rec.make_install(base, REPO / ".claude" / "hooks", n) for n in {r["layout"] for r in SP_ROWS}}


def test_there_are_save_policy_rows():
    assert len(SP_ROWS) >= 25
    assert {r["decision"] for r in SP_ROWS} == {"allow", "advise", "deny"}


@pytest.mark.parametrize("row", SP_ROWS, ids=[r["id"] for r in SP_ROWS])
def test_sp_row(sp_installs, row):
    rec, inst = sp_installs
    install = inst[row["layout"]]
    if rec.windows_only(row["id"]):
        pytest.skip(rec.windows_only(row["id"]))
    got = rec.engine_verdict(install, row["hook"], rec.row_payload(row, install))
    assert rec.intended_problems(row, got) == []


# ================================================================ a save change beside another rule's ASK (L2 HIGH-1)
# Through run.py in process (the real Claude hook entry): another rule's ask must never skip the save policy. More than
# 2 saves with no typed approval: the S-SAVE-APPROVAL refusal wins over the ask. 1 or 2 saves: the pairs are backed up
# and verified BEFORE the ask reaches the user, and a failed backup refuses.


def _ask_beside(tmp_path, command_of, transcript=""):
    rec = _rec()
    install = rec.make_install(tmp_path, REPO / ".claude" / "hooks", "saves-bulk")
    S = (install / "cfg" / "Saves").as_posix()
    payload = {"tool_name": "Bash", "session_id": "corpus-save", "cwd": install.as_posix(),
               "tool_input": {"command": command_of(S, install.as_posix())}}
    if transcript:
        payload["transcript_path"] = str(install / "transcripts" / transcript)
    return install, rec.fire_inprocess(install, "protect-bash.sh", payload)


def _rm(S, *stems):
    return "rm " + " ".join('"{}/{}.ess"'.format(S, s) for s in stems)


ASKERS = {
    "opaque": lambda S, I: '$R "{}/Save1.ess"; '.format(S),
    "git-discard": lambda S, I: "git checkout -- Data/Plugin.esp ; ",
}


@pytest.mark.parametrize("asker", sorted(ASKERS))
def test_three_saves_beside_an_ask_are_refused_without_the_typed_approval(tmp_path, asker):
    install, got = _ask_beside(tmp_path, lambda S, I: ASKERS[asker](S, I) + _rm(S, "Save1", "Save2", "Save3"))
    assert got["decision"] == "deny" and "S-SAVE-APPROVAL" in got["rules"], got
    assert got["text"].startswith("Blocked: this deletes 3 saves"), got
    assert saved(install) == []


@pytest.mark.parametrize("asker", sorted(ASKERS))
def test_one_save_beside_an_ask_is_backed_up_before_the_ask(tmp_path, asker):
    install, got = _ask_beside(tmp_path, lambda S, I: ASKERS[asker](S, I) + _rm(S, "Save1"))
    assert got["decision"] == "ask", got
    assert sorted(Path(e["src"]).name for e in saved(install)) == ["Save1.ess", "Save1.skse"]


def test_three_approved_saves_beside_an_ask_are_backed_up_before_the_ask(tmp_path):
    install, got = _ask_beside(tmp_path, lambda S, I: ASKERS["git-discard"](S, I) + _rm(S, "Save1", "Save2", "Save3"),
                               transcript="approved.jsonl")
    assert got["decision"] == "ask" and "S-GIT-DISCARD" in got["rules"], got
    assert len(saved(install)) == 6


def test_a_failed_backup_beside_an_ask_refuses(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    from modding_harness.preservation import SaveStore
    monkeypatch.setattr(SaveStore, "backup", boom)
    install, got = _ask_beside(tmp_path, lambda S, I: ASKERS["git-discard"](S, I) + _rm(S, "Save1"))
    assert got["decision"] == "deny" and got["text"].startswith("Blocked: the backup of the saves failed"), got


@pytest.mark.parametrize("stems, want", [(("Save1",), "ask"), (("Save1", "Save2", "Save3"), "deny")],
                         ids=["one-save-asks-after-backup", "three-saves-refused"])
def test_codex_save_change_beside_an_ask_keeps_the_save_policy(inst, stems, want):
    """The Codex entry (engine.pre_tool) shares portable.check: the same ordering holds there."""
    cmd = 'git -C "{}" checkout -- Data/Plugin.esp ; '.format(inst.game.as_posix()) + "r" + "m " + " ".join(inst.p(s + ".ess") for s in stems)
    v = codex_pre(inst, "Bash", {"command": cmd})
    assert v.decision == want, v
    if want == "deny":
        assert v.code == "shell.S-SAVE-APPROVAL" and inst.saved() == []
    else:
        assert v.code == "shell.S-GIT-DISCARD" and len(inst.saved()) == 2
