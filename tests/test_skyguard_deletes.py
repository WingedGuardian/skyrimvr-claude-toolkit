"""skyguard.deletes (Plan 5 Task 7): delete rules decided on executed commands and resolved operands.

Two halves:
  - every hand-authored t07 intended row, fired IN-PROCESS (decision, rule trace, exact text), parametrized by row
    id so a mutation can name the one row it must turn red (the corpus test fires the same rows through the real
    wrapper, but as one test);
  - the helpers, one behaviour per test: which programs delete, which words are their operands, how an unresolved
    word is still matched against the protected roots.
"""
import json
import os
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, deletes, paths, shell_rules, shellparse  # noqa: E402

GAME = "C:/GOG Games/The Elder Scrolls V Skyrim VR"
INSTALL = "C:/t7/Skyrim VR"           # stands in for the corpus's temp install (`<install>`)
LAYOUT_ENV = {
    "declared": {"SKYRIM_GAME_ROOT": GAME},
    "config": {"SKYRIM_GAME_ROOT": GAME, "SKYRIM_CONFIG_DIR": "D:/Profiles/MO2/profiles/Default"},
    "mo2": {"SKYRIM_GAME_ROOT": GAME, "SKYRIM_MO2_MODS": "D:/MO2/mods", "SKYRIM_MO2_OVERWRITE": "D:/MO2/overwrite"},
}
SHELL = {"Bash": "bash", "PowerShell": "powershell"}


def _rows():
    text = (REPO / "tests" / "guard_corpus" / "intended.jsonl").read_text(encoding="utf-8")
    return [r for r in (json.loads(x) for x in text.split("\n") if x.strip()) if r["task"] == "T7"]


ROWS = _rows()


def decide(command, layout="declared", cwd="", shell="bash", env=None):
    r = paths.roots(INSTALL, "", LAYOUT_ENV[layout])
    a = audit.Audit("")
    a.raw = lambda line: None                             # no audit log on disk from a unit test
    v = shell_rules.decide(command, r, a, cwd=cwd, shell=shell, env=env if env is not None else os.environ)
    if v.decision == "allow":
        got = ("advise", v.context) if v.context else ("allow", "")
    else:
        got = (v.decision, v.reason)
    return got, {rid for rid, _ in a.fired}


def test_there_are_t07_rows():
    assert len(ROWS) >= 45, len(ROWS)


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_t07_row(row):
    p = row["payload"]
    fill = (lambda s: s.replace("<install>", INSTALL)) if row.get("payload_note") == "install-relative" else (lambda s: s)
    got, fired = decide(fill(p["tool_input"]["command"]), row["layout"], fill(p.get("cwd", "")),
                        SHELL[p["tool_name"]])
    # The corpus install is named "Skyrim VR", as INSTALL is, so texts that name it are the same in-process.
    assert got == (row["decision"], row["text"])
    assert fired == set(row["rules"])


# ------------------------------------------------------------------------------------------- operands
def _execs(cmd, shell="bash"):
    return shellparse.parse(cmd, shell).execs


def _ops(cmd, shell="bash"):
    return [deletes.operands(e) for e in _execs(cmd, shell)]


@pytest.mark.parametrize("cmd,want", [
    ("rm -rf -- -x y", [["-x", "y"]]),
    ("rm -r -f a b", [["a", "b"]]),
    ("rmdir --ignore-fail-on-non-empty d", [["d"]]),
    ("unlink f", [["f"]]),
    ("ls a", [None]),
])
def test_plain_operands_skip_options_until_double_dash(cmd, want):
    assert _ops(cmd) == want


def test_cmd_switches_are_not_operands():
    assert _ops("del /f /s /q a", "cmd") == [["a"]]


@pytest.mark.parametrize("cmd,want", [
    ("Remove-Item a, b -Recurse", ["a", "b"]),
    ("Remove-Item -Path a -Force", ["a"]),
    ("Remove-Item -LiteralPath a", ["a"]),
    ("Remove-Item -lp a", ["a"]),
    ("Remove-Item -Path:a", ["a"]),
    ("Remove-Item -Path C:/x/* -Include *.tmp", ["C:/x/*"]),
    ("Remove-Item x -ErrorAction SilentlyContinue", ["x"]),
    ("ri -r Data", ["Data"]),
])
def test_remove_item_operands(cmd, want):
    assert _ops(cmd, "powershell") == [want]


def test_pipeline_input_is_an_unresolved_operand():
    assert _ops("ls | xargs rm")[-1] == [deletes.UNRESOLVED]
    assert _ops("Get-ChildItem x | Remove-Item", "powershell")[-1] == [deletes.UNRESOLVED]


@pytest.mark.parametrize("cmd,want", [
    ("git clean -fdx", ["."]),
    ("git clean -fd -- a b", ["./a", "./b"]),
    ("git -C sub clean -fdx", ["./sub"]),
    ("git -C C:/r clean -f", ["C:/r"]),
    ("git --work-tree=w clean -f", ["./w"]),
    ("git clean -fe pat x", ["./x"]),
    ("git clean --exclude pat", ["."]),
    ("git clean -n", None),
    ("git clean -fdn", None),
    ("git clean --dry-run -f", None),
    ("git rm -r tools/x", ["./tools/x"]),
    ("git rm C:/abs/x", ["C:/abs/x"]),
    ("git rm --cached x", None),
    ("git rm -n x", None),
    ("git rm", None),
    ("git status", None),
    ("git log --grep=rm", None),
])
def test_git_targets(cmd, want):
    assert _ops(cmd) == [want]


@pytest.mark.parametrize("cmd,want", [
    ("robocopy a b /MIR", ["b"]),
    ("robocopy a b /PURGE", ["b"]),
    ("robocopy a b /MOVE", ["a"]),
    ("robocopy a b //MIR", ["b"]),
    ("robocopy a b /E", None),
    # fix-limits 14: exclusion lists are not the source or the destination
    ("robocopy a b /XF x.esp /MIR", ["b"]),
    ("robocopy a b /XD keep /MIR", ["b"]),
    ("robocopy a b /XF x.esp y.esp /MOVE", ["a"]),
])
def test_robocopy_targets(cmd, want):
    assert _ops(cmd) == [want]


@pytest.mark.parametrize("cmd,want", [
    ("rimraf a b", ["a", "b"]),
    ("npx rimraf a", ["a"]),
    ("npx -y rimraf@5 a", ["a"]),
    ("pnpm dlx rimraf a", ["a"]),
    ("npx prettier a", None),
    ("npm install rimraf", None),
])
def test_rimraf(cmd, want):
    assert _ops(cmd) == [want]


@pytest.mark.parametrize("cmd,has_code", [
    ("python -c 'import shutil'", True),
    ("python3 -cimport", True),
    ("node -e 'x'", True),
    ("perl -pe 's/a/b/' f", True),
    ("python script.py", False),
])
def test_interpreter_code(cmd, has_code):
    assert bool(deletes.interpreter_code(_execs(cmd)[0])) == has_code


def test_interpreter_code_includes_a_heredoc_body():
    e = _execs("python - <<'EOF'\nimport shutil\nEOF")[0]
    assert "import shutil" in deletes.interpreter_code(e)


# ------------------------------------------------------------------------------------------- classification
R = paths.roots(INSTALL, "", LAYOUT_ENV["mo2"])


@pytest.mark.parametrize("p,kind", [
    ("c:/GOG Games/The Elder Scrolls V Skyrim VR/Data", "install"),
    ("c:/GOG Games", "install"),                           # an ancestor (covers_protected)
    ("c:/GOG*", "install"),
    ("d:/MO2/mods/X", "mo2"),
    ("d:/MO2", "mo2"),
    ("c:/Users/u/Documents/My Games/Skyrim VR/x.ini", "config"),
    ("c:/Users/u/Documents", "config"),
    ("c:/t7/Skyrim VR/__pycache__/x.pyc", ""),             # scratch (R8)
    ("c:/Temp/x", ""),
])
def test_protected_kind(p, kind):
    assert deletes.protected_kind(p, R) == kind


@pytest.mark.parametrize("word,base,reaches", [
    ("/c/*/The*/Data", None, True),
    ("C:/GOGGAM~1/THEELD~1/Data", None, True),
    ("C:/GOGGAM~1", None, True),
    ("*/Data", "c:/GOG Games", True),
    ("C:/*/Other*/Data", None, False),
    ("C:/PROGRA~1/x", None, False),
    ("$G/Data", None, False),
    ("rel/x", None, False),
])
def test_raw_reaches(word, base, reaches):
    assert deletes.raw_reaches(word, base, "bash", R) is reaches


def test_literal_abs_keeps_glob_characters_and_literal_dollars():
    assert deletes.literal_abs("C:/a [x]$/b", None, "bash") == "c:/a [x]$/b"
    assert deletes.literal_abs("$G/b", None, "bash") is None
    assert deletes.literal_abs("%X%/b", None, "cmd") is None
    assert deletes.literal_abs("b", "c:/base", "bash") == "c:/base/b"


def test_is_opaque():
    assert deletes.is_opaque(_execs("$R -rf x")[0])
    assert deletes.is_opaque(_execs("git -c alias.x='!rm -rf y' x")[0])
    assert not deletes.is_opaque(_execs("git -c core.x=y status")[0])


# ------------------------------------------------------------------------------------------- whole-rule behaviour
def test_unknown_dialect_without_cwd_is_stricter(monkeypatch):
    """R5: with no dialect and no cwd, PowerShell's reading of `/tmp/x` (the current drive's root) cannot be placed
    ON WINDOWS, so a command that also names the install denies. Claude always sends the dialect; this pins the cost.
    Both host readings run on every OS (paths.POSIX_HOST), so neither branch goes untested on either CI leg."""
    monkeypatch.setattr(paths, "POSIX_HOST", False)
    got, fired = decide('rm -rf /tmp/x && ls "' + GAME + '"', shell="")
    assert fired == {"S-DEL-UNRESOLVED"}
    got, fired = decide('rm -rf /tmp/x && ls "' + GAME + '"', shell="bash")
    assert got == ("allow", "")


def test_unknown_dialect_on_a_posix_host_places_slash_paths(monkeypatch):
    """Linux CI run 38201545601: on a POSIX host `/` is the only root, so PowerShell's `/tmp/x` IS /tmp/x -- outside
    the install, and the Windows-only cost above does not apply. The same shape with the `/x` INSIDE the install
    still denies, in the unknown dialect and in each named one."""
    monkeypatch.setattr(paths, "POSIX_HOST", True)
    got, fired = decide('rm -rf /tmp/x && ls "' + GAME + '"', shell="")
    assert (got, fired) == (("allow", ""), set())
    posix_game = "/home/u/Games/Skyrim VR"
    r = paths.roots(posix_game, "", {})
    for shell in ("", "bash", "powershell"):
        a = audit.Audit("")
        a.raw = lambda line: None
        v = shell_rules.decide('rm -rf "' + posix_game + '/Data" && ls "' + posix_game + '"', r, a, cwd="",
                               shell=shell, env={})
        assert (v.decision, {rid for rid, _ in a.fired}) == ("deny", {"S-DEL-INSTALL"}), shell


def test_home_resolves_from_the_env_argument():
    env = {"HOME": GAME, "USERPROFILE": GAME}
    got, fired = decide("rm -rf ~/Data", env=env)
    assert fired == {"S-DEL-INSTALL"}
    got, fired = decide("rm -rf ~/Data", env={"HOME": "C:/Users/u"})
    assert got == ("allow", "")


def test_first_resolved_deny_wins_over_an_unresolved_one():
    got, fired = decide('rm -rf "$X"; rm -rf "' + GAME + '/Data"')
    assert fired == {"S-DEL-INSTALL"}


def test_parse_failure_without_a_root_is_not_a_deny():
    got, fired = decide('rm -rf "/tmp/x')
    assert got == ("allow", "")


# ------------------------------------------------------------------------------------------- root MENTIONS (clause b)
# A resolved operand no longer needs the legacy root regexes, but clause (b) still asks "does the command NAME a
# protected root" with them. These pin the three properties those regexes must keep (paths.path_to_regex).
def _unresolved_with_mention(root, mention, env_root=None):
    r = paths.roots(root, "", {"SKYRIM_GAME_ROOT": env_root} if env_root else {})
    a = audit.Audit("")
    a.raw = lambda line: None
    v = shell_rules.decide('rm -rf "$X"; ls "' + mention + '"', r, a, cwd="C:/Users/u", shell="bash", env={})
    return v.decision, {rid for rid, _ in a.fired}


def test_mention_with_doubled_separators_still_names_the_root():
    assert _unresolved_with_mention(GAME, GAME.replace("/", "//")) == ("deny", {"S-DEL-UNRESOLVED"})


def test_mention_of_a_root_with_regex_metacharacters_still_names_it():
    root = "C:/Program Files (x86)/Steam/steamapps/common/Skyrim VR"
    assert _unresolved_with_mention(root, root) == ("deny", {"S-DEL-UNRESOLVED"})


def test_a_path_that_merely_contains_skyrim_is_not_a_root_mention():
    scratch = "C:/Users/x/AppData/Local/Temp/claude/C--GOG-Games-The-Elder-Scrolls-V-Skyrim-VR/s/scratchpad"
    assert _unresolved_with_mention(INSTALL, scratch, env_root=GAME) == ("allow", set())


# ------------------------------------------------------------------------------------------- fix round 1
@pytest.mark.parametrize("cmd,shell", [
    ("ls | xargs rm", "bash"),
    ('rm -rf "$X"', "bash"),
    ("rm -rf $(cat list.txt)", "bash"),
    ("Get-ChildItem *.bak | Remove-Item", "powershell"),
    ("Remove-Item $p -Recurse", "powershell"),
])
def test_known_limit_unresolved_delete_from_inside_the_install_is_not_flagged(cmd, shell):
    """KNOWN LIMIT (fix round 1 ruling): an advisory here priced at 75 occurrences, 0 real, in 61,935 items, so it
    was removed as noise. Pinned so a change is deliberate."""
    assert decide(cmd, cwd=INSTALL, shell=shell) == (("allow", ""), set())


@pytest.mark.parametrize("cmd,cwd", [
    ('rm -rf "$X"', "C:/Users/u/proj"),                   # cwd outside every root
    ('rm -rf "$X"', INSTALL + "/__pycache__"),            # a scratch cwd is not protected (R8)
    ('cd "$D" && rm -rf x', INSTALL),                     # the cwd itself is unknowable: no claim about it
    ("rm -rf /tmp/x", INSTALL),                           # resolved, outside: nothing to say
])
def test_unresolved_operand_with_no_root_named_is_silent(cmd, cwd):
    assert decide(cmd, cwd=cwd) == (("allow", ""), set())


def test_a_deny_still_wins_over_the_cwd_advice():
    got, fired = decide('rm -rf "$X"; ls "' + GAME + '"', cwd=INSTALL)
    assert got[0] == "deny" and fired == {"S-DEL-UNRESOLVED"}


@pytest.mark.parametrize("p,kind", [
    (INSTALL.lower().replace("c:/t7", "c:/t7") + "/tools/__pycache__", ""),          # ruling: scratch
    ("c:/t7/Skyrim VR/tools/xelib/__pycache__/x.pyc", ""),
    ("c:/t7/Skyrim VR/tools/xelib/node_modules/koffi", "install"),                    # tool runtime, protected
])
def test_tools_pycache_is_scratch_but_tools_node_modules_is_not(p, kind):
    assert deletes.protected_kind(p, R) == kind


def test_unresolved_operand_from_above_the_root_is_silent():
    assert decide('rm -rf "$X"', cwd="C:/") == (("allow", ""), set())
    assert decide('rm -rf "$X"', cwd="C:/t7") == (("allow", ""), set())


# ---- G1 (RT-A): interpreter inline code that launches a process / removes, naming a protected root -> deny
_ROOT_IN_CODE = INSTALL + "/Data"


@pytest.mark.parametrize("cmd", [
    f"python -c \"import os; os.system('del /s /q \\\"{_ROOT_IN_CODE}\\\"')\"",
    f"python -c \"import subprocess; subprocess.run(['cmd','/c','rd','/s','/q','{_ROOT_IN_CODE}'])\"",
    f"python -Bc \"import shutil; shutil.rmtree('{_ROOT_IN_CODE}')\"",
    f"node -e \"require('child_process').execSync('rd /s /q \\\"{_ROOT_IN_CODE}\\\"')\"",
    f"node -pe \"require('fs').rmSync('{_ROOT_IN_CODE}',{{recursive:true}})\"",
    f"php -r \"system('rd /s /q \\\"{_ROOT_IN_CODE}\\\"');\"",
    f"ruby -e \"require 'fileutils'; FileUtils.rm_rf('{_ROOT_IN_CODE}')\"",
], ids=["py-os-system", "py-subprocess", "py-Bc-rmtree", "node-execSync", "node-pe-rmSync", "php-system",
        "ruby-fileutils"])
def test_g1_interpreter_code_naming_a_root_denies(cmd):
    (decision, _), fired = decide(cmd)
    assert decision == "deny", (cmd, decision, fired)


@pytest.mark.parametrize("cmd", [
    "python -c \"import os; print(os.listdir('C:/Temp'))\"",
    f"python -c \"print(open('{_ROOT_IN_CODE}/x.txt').read())\"",
    "node -e \"console.log(1)\"",
    "python -c \"import subprocess; subprocess.run(['git','status'])\"",
    f"python -Bc \"import os; os.system('dir C:/Temp')\"",
    # priced false positive (6 history items): a read-only tool run per archive while naming the install
    f"python -c \"import subprocess,glob; [subprocess.run(['bash','tools/automod-cli.sh','archive','list',b,'--json'])"
    f" for b in glob.glob('{_ROOT_IN_CODE}/*.bsa')]\"",
], ids=["py-listdir-scratch", "py-read-in-install", "node-log", "py-subprocess-no-root", "py-Bc-system-no-root",
        "py-subprocess-readonly-tool-in-install"])
def test_g1_ordinary_interpreter_code_is_not_refused(cmd):
    (decision, _), fired = decide(cmd)
    assert decision != "deny", (cmd, fired)


# ---- G2 (RT-A): command-running wrappers are opaque; delete word + protected root in their own words -> deny
@pytest.mark.parametrize("cmd,shell", [
    (f"schtasks /create /tn x /sc once /st 00:00 /tr \"cmd /c rd /s /q \\\"{_ROOT_IN_CODE}\\\"\"", "bash"),
    (f"wmic process call create \"cmd /c rd /s /q \\\"{_ROOT_IN_CODE}\\\"\"", "bash"),
    (f"docker run --rm -v \"{INSTALL}:/g\" alpine rm -rf /g/Data \"{INSTALL}\"", "bash"),
    (f"Start-Process cmd -ArgumentList '/c rd /s /q \"{_ROOT_IN_CODE}\"'", "powershell"),
    (f"Invoke-Command -ScriptBlock {{ Remove-Item -Recurse '{_ROOT_IN_CODE}' }}", "powershell"),
], ids=["schtasks-rd", "wmic-rd", "docker-rm", "start-process-rd", "invoke-command-remove-item"])
def test_g2_wrapper_with_delete_and_root_denies(cmd, shell):
    (decision, _), fired = decide(cmd, shell=shell)
    assert decision == "deny", (cmd, decision, fired)


@pytest.mark.parametrize("cmd,shell", [
    ("docker run hello-world", "bash"),
    (f"docker run --rm -v \"{INSTALL}:/g\" alpine ls /g/Data", "bash"),
    ("schtasks /query /fo LIST", "bash"),
    ("Start-Process notepad", "powershell"),
], ids=["docker-hello", "docker-ls-install", "schtasks-query", "start-process-notepad"])
def test_g2_wrapper_without_a_delete_is_not_refused(cmd, shell):
    (decision, _), fired = decide(cmd, shell=shell)
    assert decision != "deny", (cmd, fired)


# ---- Merge of rt-fix-a + rt-fix-b (2026-10-05): a move out of a protected folder is a delete of the source (rt-fix-a);
# a move that takes a protected ROOT folder away is S-MOVE-ROOT (rt-fix-b), whose text says what happens.
@pytest.mark.parametrize("cmd,shell", [
    (f'mv "{INSTALL}/Data/meshes/a.nif" C:/Temp/a.nif', "bash"),
    (f'Move-Item "{INSTALL}/Data/meshes/a.nif" C:/Temp/a.nif', "powershell"),
], ids=["mv", "move-item"])
def test_moving_a_file_out_of_the_install_is_a_delete(cmd, shell):
    (decision, reason), fired = decide(cmd, shell=shell)
    assert (decision, fired) == ("deny", {"S-DEL-INSTALL"}), (cmd, reason, fired)
    assert "a.nif" in reason


@pytest.mark.parametrize("cmd,shell", [
    (f'mv "{INSTALL}" C:/Temp/Game.old', "bash"),
    (f'Move-Item "{INSTALL}/Data" C:/Temp/Data.old', "powershell"),
], ids=["mv-install", "move-item-data"])
def test_moving_a_root_folder_away_names_the_move_not_a_delete(cmd, shell):
    (decision, reason), fired = decide(cmd, shell=shell)
    if cmd.startswith(f'mv "{INSTALL}"'):
        # The install itself contains permanent recovery; moving its ancestor is forbidden first.
        assert (decision, fired) == ("deny", {"S-SAVE-STORE"})
        assert "permanent save backup store" in reason
        return
    assert (decision, fired) == ("deny", {"S-MOVE-ROOT"}), (cmd, reason, fired)
    assert "moves" in reason and "deletes" not in reason


# ---- Plan 5 final review D1: the Recycle-Bin delete core.md recommends is a delete to every detector.
_VB = "Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::"
_RB = ", 'OnlyErrorDialogs', 'SendToRecycleBin')"


@pytest.mark.parametrize("shell", ["powershell", "bash", ""])
def test_recycle_bin_delete_into_the_install_denies_in_every_dialect(shell):
    (decision, reason), fired = decide(_VB + "DeleteDirectory('" + INSTALL + "/Data/meshes'" + _RB, shell=shell,
                                       cwd="C:/Temp")
    assert (decision, fired) == ("deny", {"S-DEL-INSTALL"}) and "meshes" in reason, (decision, fired, reason)


def test_recycle_bin_delete_of_an_unresolved_root_variable_is_unresolved():
    got = decide(_VB + 'DeleteDirectory("$env:SKYRIM_GAME_ROOT/Data"' + _RB, shell="powershell", cwd="C:/Temp",
                 env={})
    assert got[1] == {"S-DEL-UNRESOLVED"}, got


def test_recycle_bin_delete_of_a_variable_in_a_command_naming_the_install_is_unresolved():
    got = decide("$d = '" + INSTALL + "/Data'; " + _VB + "DeleteDirectory($d" + _RB, shell="powershell", cwd="C:/Temp")
    assert got[1] == {"S-DEL-UNRESOLVED"}, got


def test_recycle_bin_words_reach_the_parse_failure_fallback():
    assert deletes.recycle_targets(_VB + "DeleteFile('x'" + _RB) == [("x", "single")]
    assert deletes.recycle_targets("$o.Namespace(10).MoveHere($p)") == [("$p", "bare")]
    from skyguard.grep import grep
    assert grep(deletes.DESTROYER, "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory(")
    assert grep(deletes.DESTROYER_LOOSE, "(New-Object -ComObject Shell.Application).Namespace(10).MoveHere(")


def test_recycle_bin_delete_with_a_double_quoted_path_resolves():
    (decision, reason), fired = decide(_VB + 'DeleteFile("' + INSTALL + '/Data/a.esp"' + _RB, shell="powershell",
                                       cwd="C:/Temp")
    assert (decision, fired) == ("deny", {"S-DEL-INSTALL"}) and "a.esp" in reason, (decision, fired, reason)


# ---- Plan 5 final review B-I1: moves with pipeline / unresolved sources and variable destinations.
def test_a_protected_file_moved_to_a_variable_destination_that_reaches_a_root_may_stay():
    got = decide('mv Data/a.esp "$SKYRIM_GAME_ROOT/Data/b.esp"', cwd=INSTALL, env={})
    assert got[0][0] != "deny", got


def test_xargs_mv_with_a_target_reads_its_sources_from_the_pipe():
    got = decide('ls "' + INSTALL + '/Data" | xargs mv -t /tmp', cwd="C:/work")
    assert got[1] == {"S-DEL-UNRESOLVED"}, got


def test_an_unresolved_move_into_a_protected_folder_is_not_a_delete():
    got = decide('ls x | xargs mv -t "' + INSTALL + '/Data"', cwd="C:/work")
    assert got[0][0] != "deny", got


def test_pipeline_move_of_an_ancestor_of_the_install_is_unresolved():
    got = decide("Get-ChildItem 'C:/t7' | Move-Item -Destination C:/Temp", shell="powershell", cwd="C:/work")
    assert got[1] == {"S-DEL-UNRESOLVED"}, got


@pytest.mark.parametrize("word,expands", [("$TMP", True), ("$env:TEMP", True), ("%TEMP%/x", True),
                                          ("`pwd`/x", True), ("C:/Temp", False), ("50%", False)])
def test_expands(word, expands):
    assert deletes._expands(word) is expands


def test_an_unresolved_delete_in_a_command_naming_the_configured_config_dir_is_unresolved():
    """The configured settings folder's MENTION arm (paths.roots: `_add(config, v)`): an unresolved operand in a
    command that names it. Resolved deletes use Roots.config_dirs instead, which this does not exercise."""
    got = decide('ls "D:/Profiles/MO2/profiles/Default"; rm -rf "$X"', layout="config", cwd="C:/work")
    assert got[1] == {"S-DEL-UNRESOLVED"}, got


# ---- B-I1 pricing (base 300d235, N = 70,710): the newly denied moves, read one by one, were mostly moves INTO the
# install's own backups through a variable the command assigns; a delete-rule twin of each shape is pinned here.
def test_a_move_into_the_installs_backups_through_an_assigned_variable_stays():
    cmd = ('P="' + INSTALL + '"\nB="$P/.claude/backups/x"\nmkdir -p "$B"\nmv "$P/openvr_api.dll" "$B/openvr_api.dll"')
    got = decide(cmd, cwd=INSTALL)
    assert got[0][0] != "deny", got


def test_a_move_out_through_an_assigned_variable_still_leaves():
    cmd = 'ROOT="' + INSTALL + '"; SCR="C:/Temp/scratch"; echo "ROOT=$ROOT"; mv "$ROOT/x.psc" "$SCR/"'
    (decision, reason), fired = decide(cmd, cwd="C:/work")
    assert (decision, fired) == ("deny", {"S-DEL-INSTALL"}) and "x.psc" in reason, (decision, fired, reason)


def test_a_move_into_a_literal_data_folder_with_a_loop_variable_stays():
    cmd = 'cd "' + INSTALL + '" && SRC=".claude/backups/b" && for f in a.dds b.dds; do mv "$SRC/$f" "Data/textures/$f"; done'
    got = decide(cmd, cwd=INSTALL)
    assert got[0][0] != "deny", got


def test_a_heredoc_that_quotes_the_recycle_bin_call_is_not_a_delete():
    cmd = ("cat >> notes.py <<'EOF'\nX = \"[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory('" + INSTALL
           + "/Data', 'OnlyErrorDialogs', 'SendToRecycleBin')\"\nEOF")
    got = decide(cmd, cwd="C:/work")
    assert got[0][0] != "deny", got


def test_echoing_the_recycle_bin_call_is_not_a_delete():
    cmd = "echo \"[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile('" + INSTALL + "/Data/a.esp')\""
    got = decide(cmd, cwd="C:/work")
    assert got[0][0] != "deny", got


@pytest.mark.parametrize("word,want", [("$B/x", "C:/G/.claude/backups/x"), ("$T/x", "$T/x"), ("$D/x", "$D/x")])
def test_expand_assigned(word, want):
    cmd = 'P="C:/G"; B="$P/.claude/backups"; D=a; D=b'
    assert deletes._expand_assigned(word, cmd) == want


# Plan 8 T6 (T4 claims audit K6): `find` is ambiguous on Windows -- System32's find.exe searches text, Git's usr/bin
# find deletes with -delete / -exec rm, and which one runs depends on PATH order. MEASURED before the fix: in the
# PowerShell and cmd dialects (Codex's shell is PowerShell) `find <Data> -delete` was allowed. Ruling: an ambiguous
# program is judged as its destructive binary, on every dialect.
_FIND_DEL = ("Blocked: this deletes {} inside the Skyrim install.\n"
             "A mod manager cannot restore your own files there; if you mean it, run it yourself.")


@pytest.mark.parametrize("shell", ["bash", "powershell", "cmd"])
@pytest.mark.parametrize("command,name", [
    ('find "<i>/Data" -delete', "Data"),
    ('find "<i>/Data" -name "*.bak" -delete', "*.bak"),
    ('find "<i>/Data" -exec rm -f {} +', "Data"),
], ids=["delete", "name-delete", "exec-rm"])
def test_gnu_find_deletes_are_judged_on_every_dialect(shell, command, name):
    got, fired = decide(command.replace("<i>", INSTALL), shell=shell)
    assert (got, fired) == (("deny", _FIND_DEL.format(name)), {"S-DEL-INSTALL"})


@pytest.mark.parametrize("shell", ["bash", "powershell", "cmd"])
@pytest.mark.parametrize("command", ['find /i "x" "<i>/Data/a.txt"', 'find "<i>/Data" -name x.txt',
                                     "find C:/Temp/x -delete"], ids=["find-exe-search", "gnu-search", "outside"])
def test_a_find_that_deletes_nothing_protected_stays_allowed(shell, command):
    """The twins: find.exe's own text search, a GNU search, and a delete outside the protected folders."""
    assert decide(command.replace("<i>", INSTALL), shell=shell) == (("allow", ""), set())


# ---- fix-unclear: a recursive search started ABOVE a protected root reaches its whole subtree; a name filter only
# narrows which files match, so the verdict is the filterless control's.
_ABOVE = "C:/GOG Games"                  # GAME's parent
_FIND_ABOVE = [   # (id, shell, command, filterless control)
    ("find-name-delete", "bash", "find '" + _ABOVE + "' -name Skyrim.esm -delete", "find '" + _ABOVE + "' -delete"),
    ("find-iname-delete", "bash", "find '" + _ABOVE + "' -iname '*.esm' -delete", "find '" + _ABOVE + "' -delete"),
    ("find-maxdepth2-delete", "bash", "find '" + _ABOVE + "' -maxdepth 2 -name Skyrim.esm -delete",
     "find '" + _ABOVE + "' -delete"),
    ("find-exec-rm", "bash", "find '" + _ABOVE + "' -name Skyrim.esm -exec rm {} +",
     "find '" + _ABOVE + "' -exec rm {} +"),
    ("find-exec-mv-away", "bash", "find '" + _ABOVE + "' -name Skyrim.esm -exec mv {} /tmp \\;",
     "find '" + _ABOVE + "' -exec mv {} /tmp \\;"),
    ("fd-exec-batch-rm", "bash", "fd Skyrim.esm '" + _ABOVE + "' -X rm", "find '" + _ABOVE + "' -exec rm {} +"),
    ("fd-exec-mv-away", "bash", "fd Skyrim.esm '" + _ABOVE + "' -x mv {} /tmp",
     "find '" + _ABOVE + "' -exec mv {} /tmp \\;"),
    ("fd-search-path-rm", "bash", "fd -e esm --search-path '" + _ABOVE + "' -x rm {}",
     "find '" + _ABOVE + "' -exec rm {} +"),
    ("find-after-literal-cd-above", "bash", "cd '" + _ABOVE + "' && find . -name Skyrim.esm -delete",
     "cd '" + _ABOVE + "' && find . -delete"),
    # fix-limits 14: a cd whose folder is a same-command literal is followed like the literal cd
    ("find-after-variable-cd-above", "bash", 'D="' + _ABOVE + '"; cd "$D" && find . -name Skyrim.esm -delete',
     "cd '" + _ABOVE + "' && find . -delete"),
    ("find-after-variable-subdir-cd-above", "bash", 'D="C:/"; cd "${D}GOG Games" && find . -name Skyrim.esm -delete',
     "cd '" + _ABOVE + "' && find . -delete"),
    ("ps-foreach-after-variable-set-location", "powershell",
     "$d = '" + _ABOVE + "'; Set-Location $d; Get-ChildItem . -Recurse -Filter Skyrim.esm | ForEach-Object { Remove-Item $_ }",
     "Get-ChildItem '" + _ABOVE + "' -Recurse -Filter Skyrim.esm | Remove-Item"),
    ("find-abs-start-after-unknown-cd", "bash", 'cd "$X" && find \'' + _ABOVE + "' -name Skyrim.esm -delete",
     'cd "$X" && find \'' + _ABOVE + "' -delete"),
    ("ps-foreach-remove", "powershell",
     "Get-ChildItem '" + _ABOVE + "' -Recurse -Filter Skyrim.esm | ForEach-Object { Remove-Item $_ }",
     "Get-ChildItem '" + _ABOVE + "' -Recurse -Filter Skyrim.esm | Remove-Item"),
    ("ps-foreach-psitem-fullname", "powershell",
     "Get-ChildItem '" + _ABOVE + "' -Recurse | % { Remove-Item $PSItem.FullName }",
     "Get-ChildItem '" + _ABOVE + "' -Recurse | Remove-Item"),
]


@pytest.mark.parametrize("shell,command,control", [pytest.param(*r[1:], id=r[0]) for r in _FIND_ABOVE])
def test_a_filtered_search_above_the_install_is_judged_as_its_filterless_control(shell, command, control):
    want = decide(control, cwd="C:/w", shell=shell)
    assert want[0][0] == "deny", ("the control must be refused, or this pair proves nothing", control)
    assert decide(command, cwd="C:/w", shell=shell)[0][0] == "deny", command


@pytest.mark.parametrize("shell,command,cwd", [
    pytest.param("bash", "find . -name '*.pyc' -delete", "C:/w", id="find-outside"),   # holds no protected root
    pytest.param("bash", "find /tmp -name x.esm -delete", "C:/w", id="find-tmp"),
    pytest.param("bash", "find '" + _ABOVE + "' -maxdepth 1 -name Skyrim.esm -delete", "C:/w",
                 id="find-above-maxdepth1"),                                     # provably direct children only
    pytest.param("bash", "find '" + _ABOVE + "' -name Skyrim.esm -exec cat {} +", "C:/w",
                 id="find-above-exec-cat"),                                      # not a delete or a move
    pytest.param("bash", "fd -e pyc . -x rm", "C:/w", id="fd-outside-rm"),
    # priced (1 real item): after a cd the parser cannot follow, `find . -name X -delete` is `rm X` there, not a wipe
    # of an unknown folder (S-DEL-UNKNOWN-DIR)
    pytest.param("bash", 'S="C:/scratch"; cd "$S/tools" && find . -name "*.rej" -delete', "C:/w",
                 id="find-after-unknown-cd"),
    # fix-limits 14 resolves the cd above (a same-command literal); a cd through a variable the command never assigns
    # is still one the parser cannot follow
    pytest.param("bash", 'cd "$OUTER/tools" && find . -name "*.rej" -delete', "C:/w", id="find-after-unresolvable-cd"),
    pytest.param("bash", "fd Skyrim.esm '" + _ABOVE + "' -x cat", "C:/w", id="fd-above-exec-cat"),
    # fix-limits 14: a variable cd into a folder that holds no protected root stays allowed
    pytest.param("bash", 'S="C:/scratch"; cd "$S" && find . -name "*.esp" -delete', "C:/w",
                 id="find-after-variable-cd-outside"),
    # an assignment in a branch does not certainly reach the cd: not followed, as before
    pytest.param("bash", 'if x; then D="C:/scratch"; fi; cd "$D" && find . -name "*.rej" -delete', "C:/w",
                 id="find-after-branch-assigned-cd"),
    pytest.param("powershell", "Get-ChildItem . -Recurse -Filter *.pyc | ForEach-Object { Remove-Item $_ }", "C:/w",
                 id="ps-foreach-outside"),
])
def test_a_filtered_search_that_cannot_reach_a_root_stays_allowed(shell, command, cwd):
    assert decide(command, cwd=cwd, shell=shell)[0][0] == "allow", command


def test_the_find_parse_keeps_the_start_beside_the_narrowed_name():
    ex = shellparse.parse("find /a -name x.esm -delete", "bash").execs
    assert [e.argv for e in ex if e.program == "find.delete"] == [("/a/x.esm", "/a")]
    ex = shellparse.parse("find /a -maxdepth 1 -name x.esm -delete", "bash").execs
    assert [e.argv for e in ex if e.program == "find.delete"] == [("/a/x.esm",)]
    ex = shellparse.parse("find /a -name x.esm -exec cp {} /b \\;", "bash").execs
    assert {e.argv for e in ex if e.program == "cp"} == {("/a/x.esm", "/b")}     # a copy still names the file
