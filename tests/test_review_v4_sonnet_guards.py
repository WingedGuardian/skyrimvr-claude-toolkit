"""Guard gaps from the v4.0.0 line-by-line review, each reproduced through shell_rules.decide before it was fixed. Each group
pins a control too: the plain spelling that was already caught, or the ordinary command that must stay allowed.
Commands are only judged here, never run."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO

GIT_BASH = next((p for p in (r"C:/Program Files/Git/bin/bash.exe",) if os.path.exists(p)), None)

sys.path.insert(0, str(REPO / "tools"))
from skyguard import anchored, audit, deletes, mcp_rules, paths, shell_rules, shellparse, targets  # noqa: E402

INSTALL = "C:/t7/Skyrim VR"
ENV = {"SKYRIM_GAME_ROOT": "C:/GOG Games/Skyrim Fixture"}
DATA = INSTALL + "/Data"
HOOK = INSTALL + "/.claude/hooks/protect-bash.sh"
URL = "http://127.0.0.1:8921/api/tool/console"
BASH = str(Path(GIT_BASH)) if GIT_BASH else (None if os.name == "nt" else shutil.which("bash"))


def decide(command, shell="bash"):
    a = audit.Audit("")
    a.raw = lambda line: None
    v = shell_rules.decide(command, paths.roots(INSTALL, "", ENV), a, shell=shell, env=os.environ)
    return v.decision, {rid for rid, _ in a.fired}


# ---- 1.1: a ${ } whose end depends on quoting must not hide what follows it
@pytest.mark.parametrize("cmd", [
    "echo ${a:-'{'} ; rm -rf \"" + DATA + "\" ; echo }",
    'echo ${a:-"{"} ; rm -rf "' + DATA + '" ; echo }',
    "echo \"${a:-'}\" ; rm -rf \"" + DATA + "\" ; echo \"'}\"",
])
def test_quoted_braces_in_an_expansion_cannot_hide_a_command(cmd):
    assert decide(cmd)[0] == "deny"


def test_the_parser_declines_rather_than_guesses_where_a_brace_expansion_ends():
    assert not shellparse.parse("echo ${a:-'{'} ; echo x ; echo }", "bash").ok


@pytest.mark.parametrize("cmd", ["echo ${a:-b} ; echo c", "echo ${a:-'b c'} ; echo d", 'echo "${a:-b}" ; echo c',
                                 "echo ${a:-${b:-c}} ; echo d", "echo ${#a}", "echo ${a//x/y}"])
def test_ordinary_expansions_still_parse(cmd):
    assert shellparse.parse(cmd, "bash").ok, cmd


def test_a_command_substitution_inside_an_expansion_is_still_read():
    assert decide('echo ${a:-$(rm -rf "' + DATA + '")}')[0] == "deny"


@pytest.mark.skipif(BASH is None, reason="no bash on this machine")
def test_real_bash_does_end_the_expansion_at_the_first_unquoted_brace():
    """The reason this is refused: if a later bash stopped doing this, the rule would be guarding nothing."""
    out = subprocess.run([BASH, "-c", "echo ${a:-'{'} ; echo RAN ; echo }"], capture_output=True, text=True).stdout
    assert out.split() == ["{", "RAN", "}"]


# ---- 7.1: curl takes the rest of a short-option cluster as the value of its last flag
@pytest.mark.parametrize("cmd", [
    "curl -sd'{\"command\":\"smp reset\"}' " + URL,
    "curl -sSd'{\"command\":\"smp reset\"}' " + URL,
    "curl -d'{\"command\":\"smp reset\"}' " + URL,
    "curl -s -d '{\"command\":\"smp reset\"}' " + URL,
    "curl -sd '{\"command\":\"smp reset\"}' " + URL,
])
def test_a_clustered_curl_body_is_read(cmd):
    assert decide(cmd)[1] & {"S-DEVBENCH-CRASH"} and decide(cmd)[0] == "deny", cmd


def test_a_harmless_clustered_body_is_not_refused():
    assert decide("curl -sd'{\"command\":\"help\"}' " + URL)[0] != "deny"


def test_curl_bodies_reads_a_cluster_and_nothing_from_long_options():
    assert mcp_rules._curl_bodies(["-sdX", URL]) == ["X"]
    assert mcp_rules._curl_bodies(["--silent", URL]) == []


# ---- 5.1: a symbolic mode that starts with a dash is a mode, not an option
@pytest.mark.parametrize("cmd", ['chmod -r "{}"', 'chmod -x "{}"', 'chmod -w "{}"', 'chmod -rwx "{}"', 'chmod -R -r "{}"',
                                 'chmod -v -x "{}"', 'chmod a-r "{}"', 'chmod 000 "{}"', 'chmod --reference=other "{}"',
                                 'chown -R me "{}"', 'chown me "{}"', 'chown --reference=other "{}"'])
def test_a_permission_change_on_a_guard_file_is_seen(cmd):
    assert "S-SELF-GUARD" in decide(cmd.format(HOOK))[1], cmd


def test_a_permission_change_elsewhere_is_not():
    assert "S-SELF-GUARD" not in decide('chmod -x "C:/Temp/tool.sh"')[1]


# ---- 5.2: HKLM:SOFTWARE is the same key as HKLM:\SOFTWARE
@pytest.mark.parametrize("key", ["HKLM:SOFTWARE\\WOW6432Node", "HKLM:\\SOFTWARE\\WOW6432Node", "HKLM:SOFTWARE",
                                 "HKEY_LOCAL_MACHINE:SOFTWARE\\WOW6432Node", "Registry::HKLM:SOFTWARE\\WOW6432Node"])
def test_a_registry_ancestor_of_the_game_key_is_refused_however_it_is_spelled(key):
    assert decide('Remove-Item -Recurse -Path "' + key + '"', "powershell")[0] == "deny", key


def test_the_drive_only_spelling_gets_its_separator():
    assert anchored._reg_key("HKLM:SOFTWARE\\X") == anchored._reg_key("HKLM:\\SOFTWARE\\X") == "hklm\\software\\x"
    assert anchored._reg_key("HKLM:") == "hklm"


def test_an_unrelated_key_is_still_allowed():
    assert decide('Remove-Item -Recurse -Path "HKCU:Software\\SomeOtherApp"', "powershell")[0] != "deny"


# ---- 4.2: every interpreter the parser knows runs code, so its code is read
@pytest.mark.parametrize("cmd", [
    'julia -e \'rm("' + DATA + '", recursive=true)\'',
    'Rscript -e \'unlink("' + DATA + '", recursive=TRUE)\'',
    'Rscript -e \'file.remove("' + DATA + '/x.esp")\'',
    'lua -e \'os.remove("' + DATA + '/x.esp")\'',
    'luajit -e \'os.remove("' + DATA + '/x.esp")\'',
    'groovy -e \'new File("' + DATA + '").deleteDir()\'',
    'osascript -e \'do shell script "rm -rf ' + DATA + '"\'',
    'tsx -e \'require("fs").rmSync("' + DATA + '",{recursive:true})\'',
    'ts-node -e \'require("fs").rmSync("' + DATA + '",{recursive:true})\'',
    'node -e \'fs.rmSync("' + DATA + '",{recursive:true})\'',
    'deno eval \'Deno.removeSync("' + DATA + '",{recursive:true})\'',
])
def test_interpreter_deletes_of_the_install_are_refused(cmd):
    assert decide(cmd)[0] == "deny", cmd


def test_a_computed_name_in_a_julia_delete_is_refused():
    assert decide('julia -e \'rm(joinpath("' + DATA + '", "x.esp"))\'')[0] == "deny"


def test_a_heredoc_to_one_of_them_is_read_too():
    assert decide("julia <<'EOF'\nrm(\"" + DATA + "\", recursive=true)\nEOF\n")[0] == "deny"


@pytest.mark.parametrize("cmd", ['julia -e \'println("' + DATA + '")\'', 'Rscript -e \'print(1)\'', 'lua -e \'print("hi")\'',
                                 'julia -e \'rm("C:/Temp/scratch.txt")\'', 'Rscript analysis.R', 'julia script.jl'])
def test_reading_and_scratch_work_in_them_stay_allowed(cmd):
    assert decide(cmd)[0] != "deny", cmd


# ---- 3.1: a folder merely named documents is not the user's Documents
@pytest.mark.parametrize("path", ["C:/Temp/documents", "C:/Temp/build/documents", "/tmp/x/documents", "D:/work/Documents",
                                  "C:/repo/docs/documents", "C:/Temp/documents/My Games",
                                  "C:/Users/me/AppData/Local/Temp/x/documents"])
def test_a_scratch_folder_named_documents_can_be_removed(path):
    assert decide('rm -rf "' + path + '"')[0] != "deny", path


@pytest.mark.parametrize("path", ["C:/Users/me/Documents", "C:/Users/me/OneDrive/Documents", "/home/me/Documents",
                                  "/mnt/c/Users/me/Documents", "D:/Documents", "C:/Users/me/Documents/My Games",
                                  "C:/Users/me/OneDrive - Acme/Documents"])
def test_a_real_documents_folder_stays_protected(path):
    assert decide('rm -rf "' + path + '"')[0] == "deny", path
    assert targets.covers_protected(path.lower(), paths.roots(INSTALL, "", ENV))


def test_a_scratch_documents_folder_is_not_classed_as_config():
    assert deletes.protected_kind("c:/temp/documents", paths.roots(INSTALL, "", ENV)) == ""
