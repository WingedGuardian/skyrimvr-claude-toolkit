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
def test_unknown_dialect_without_cwd_is_stricter():
    """R5: with no dialect and no cwd, PowerShell's reading of `/tmp/x` (the current drive's root) cannot be placed,
    so a command that also names the install denies. Claude always sends the dialect; this pins the cost."""
    got, fired = decide('rm -rf /tmp/x && ls "' + GAME + '"', shell="")
    assert fired == {"S-DEL-UNRESOLVED"}
    got, fired = decide('rm -rf /tmp/x && ls "' + GAME + '"', shell="bash")
    assert got == ("allow", "")


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
