r"""shellparse: what a command line EXECUTES (bash, PowerShell, cmd). Plan 5 Task 5.

A misparse is a silent allow once rules sit on this parser (Review Focus 1), so every executed-code path the plan
names has a row here, and test_mutations.py deletes each branch and names the row that must go red.
"""
import base64
import json
import random
import os
import subprocess
import sys

import pytest

from conftest import BASH, REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import shellparse as sp  # noqa: E402
from skyguard.shellparse import parse  # noqa: E402

BS = chr(92)


@pytest.mark.parametrize("quoted", [
    r"Data/test.\x65sp", r"Data/test.\145sp", r"Data/test.\u0065sp",
    r"Data/test.\U00000065sp", r"Saves/test.\x65ss", r"Saves/test.\145ss",
    r"\x6\x65\x123", r"\1\12\123\777", r"a\q\z\8\9b",
    r"a\cA\ca\c?\c_b", r"a\0ignored", r"a\x00ignored",
    r"a\u0000ignored", r"\xC3\xA9", r"\u00e9\U0001f600",
    r"a\n\r\t\a\b\e\E\f\v\\\'\"\?b", r"a\x\u\Uend",
])
def test_ansi_c_arguments_match_stdout_only_bash_oracle(quoted):
    # Hardcoded literals only. The oracle prints an argument; it writes no files.
    command = "printf '%s' $'" + quoted + "'"
    result = subprocess.run([BASH, "--noprofile", "--norc", "-s"], input=command.encode("utf-8"),
                            capture_output=True, timeout=10,
                            env=dict(os.environ, LC_ALL="C.UTF-8"))
    assert result.returncode == 0, result.stderr
    assert find(command, "printf").argv[1] == result.stdout.decode("utf-8", "surrogateescape")


def progs(cmd, shell="bash"):
    p = parse(cmd, shell)
    assert p.ok, (cmd, p.why)
    return [e.program for e in p.execs]


def find(cmd, program, shell="bash"):
    p = parse(cmd, shell)
    assert p.ok, (cmd, p.why)
    hits = [e for e in p.execs if e.program == program]
    assert hits, (cmd, [(e.program, e.argv) for e in p.execs])
    return hits[0]


# ------------------------------------------------------------------------------------------------ bash
@pytest.mark.parametrize("cmd,expected", [
    ("a; b && c || d | e & f\ng", ["a", "b", "c", "d", "e", "f", "g"]),
    ("a |& b", ["a", "b"]),
    ("( a; b ) && { c; d; }", ["a", "b", "c", "d"]),
    ("a &&\n b", ["a", "b"]),
    ("a |\n b", ["a", "b"]),
])
def test_bash_separators_and_groups(cmd, expected):
    assert progs(cmd) == expected


@pytest.mark.parametrize("cmd", [
    "echo $(rm -rf x)",
    "echo \"$(rm -rf x)\"",
    "x=$(rm -rf x)",
    "echo `rm -rf x`",
    "echo \"`rm -rf x`\"",
    "cat <(rm -rf x)",
    "tee >(rm -rf x)",
    "echo $(echo $(echo $(rm -rf x)))",
    "echo `echo " + BS + "`rm -rf x" + BS + "``",
])
def test_bash_substitutions_are_parsed(cmd):
    assert find(cmd, "rm").argv == ("-rf", "x")


def test_pipeline_stage_reads_the_pipe():
    p = parse("ls | rm -rf x; rm y", "bash")
    assert [(e.program, e.stdin_from) for e in p.execs] == [("ls", ""), ("rm", "pipe"), ("rm", "")]
    assert find("gci | Remove-Item", "remove-item", "powershell").stdin_from == "pipe"


def test_bash_arithmetic_is_not_a_command():
    assert progs("echo $((1 + 2)); (( i++ ))") == ["echo"]


def test_bash_assignment_prefixes_skipped():
    e = find("FOO=1 BAR='a b' rm -rf x", "rm")
    assert e.argv == ("-rf", "x")
    assert progs("A=1") == []


@pytest.mark.parametrize("cmd", [
    "sudo rm -rf x", "sudo -u root rm -rf x", "env rm -rf x", "env -i A=1 B=2 rm -rf x", "command rm -rf x",
    "builtin rm -rf x", "exec rm -rf x", "nohup rm -rf x &", "time rm -rf x", "time -p rm -rf x",
    "nice rm -rf x", "nice -n 5 rm -rf x", "timeout 5 rm -rf x", "timeout -s 9 5m rm -rf x",
    "sudo nohup timeout 5 env X=1 nice rm -rf x", "winpty rm -rf x",
])
def test_bash_wrappers_unwrapped(cmd):
    assert find(cmd, "rm").argv == ("-rf", "x")


@pytest.mark.parametrize("cmd", ["ls | xargs rm -rf", "ls | xargs -n 1 -I {} rm -rf {}", "xargs -0 rm -rf < l"])
def test_bash_xargs_runs_its_command_on_stdin(cmd):
    e = find(cmd, "rm")
    assert e.stdin_from == "pipe"


def test_bash_command_v_is_a_lookup_not_an_execution():
    assert "rm" not in progs("command -v rm")


@pytest.mark.parametrize("cmd,program", [
    ("bash -c 'rm -rf x'", "rm"), ('sh -c "rm -rf x"', "rm"), ("bash -lc 'rm -rf x'", "rm"),
    ("/bin/bash -e -c 'rm -rf x'", "rm"), ("zsh -c 'rm -rf x'", "rm"), ("eval 'rm -rf x'", "rm"),
    ("eval rm -rf x", "rm"),
    ("pwsh -Command \"Remove-Item x -Recurse\"", "remove-item"),
    ("pwsh -NoProfile -c 'Remove-Item x'", "remove-item"),
    ("powershell.exe -NoProfile -ExecutionPolicy Bypass -Command \"ri x\"", "remove-item"),
    ("powershell \"ri x\"", "remove-item"),
    ("cmd /c del x", "del"), ("cmd //c \"del /s /q x\"", "del"), ("cmd.exe /C rd /s /q x", "rmdir"),
    ("sudo bash -c 'sh -c \"rm -rf x\"'", "rm"),
])
def test_bash_shell_strings_reparsed_in_their_dialect(cmd, program):
    e = find(cmd, program)
    assert e.dialect == sp._shell_dialect({"remove-item": "pwsh", "del": "cmd", "rmdir": "cmd"}.get(program, "bash"))


def test_pwsh_encoded_command_is_decoded():
    enc = base64.b64encode("Remove-Item C:/x -Recurse".encode("utf-16-le")).decode()
    assert find("pwsh -EncodedCommand " + enc, "remove-item").argv == ("C:/x", "-Recurse")
    assert not parse("pwsh -enc !!notbase64!!", "bash").ok


def test_bash_script_file_runs_the_script():
    assert find("bash tools/resaver-cli.sh reset-havok s.ess --apply", "resaver-cli").argv[-1] == "--apply"
    assert progs("source x.sh a") == ["x"] and progs(". ./y.sh") == ["y"]


@pytest.mark.parametrize("cmd,starts", [
    # RT-A: a -name filter names start/NAME; fix-unclear: a recursive find still reaches the start's whole subtree,
    # so the start follows it (only -maxdepth 0|1 provably stops at start/NAME)
    ("find Data -name '*.tmp' -delete", ("Data/*.tmp", "Data")),
    ("find Data -maxdepth 1 -name '*.tmp' -delete", ("Data/*.tmp",)),
    ("find Data -type f -delete", ("Data",)),
    ("find a b -type f -delete", ("a", "b")),
    ("find -L Data -delete", ("Data",)),
    ("find -delete", (".",)),
])
def test_find_delete_is_a_delete_on_the_start_paths(cmd, starts):
    assert find(cmd, "find.delete").argv == starts


@pytest.mark.parametrize("cmd", [
    "find Data -name x -exec rm -rf {} " + BS + ";",
    "find Data -name x -exec rm -rf {} ';'",
    "find Data -execdir rm -rf {} +",
    "find Data -ok rm -rf {} " + BS + ";",
])
def test_find_exec_runs_its_command_on_the_start_paths(cmd):
    # RT-A: under a -name filter {} is start/NAME, otherwise the start itself; fix-unclear: for a delete (or a move)
    # the start follows start/NAME, since the recursive search reaches its whole subtree
    assert find(cmd, "rm").argv == (("-rf", "Data/x", "Data") if "-name" in cmd else ("-rf", "Data"))


def test_find_exec_shell_string_is_reparsed():
    assert find("find . -exec sh -c 'rm -rf \"$1\"' _ {} " + BS + ";", "rm").argv == ("-rf", "$1")


# heredocs (R7)
def test_heredoc_fed_to_a_shell_is_parsed():
    e = find("bash <<'EOF'\nrm -rf x\nEOF", "bash")
    assert e.stdin_from == "heredoc:shell"
    assert find("bash <<'EOF'\nrm -rf x\nEOF", "rm").argv == ("-rf", "x")
    assert "rm" in progs("sh -s <<EOF\nrm -rf x\nEOF")
    assert "rm" in progs("sudo /bin/bash <<EOF\nrm -rf x\nEOF")
    assert "remove-item" in progs("pwsh -Command - <<'EOF'\nRemove-Item x\nEOF")


def test_heredoc_to_an_interpreter_is_code_not_commands():
    e = find("python - <<'EOF'\nimport shutil; shutil.rmtree('x')\nEOF", "python")
    assert e.stdin_from == "heredoc:interpreter" and "rmtree" in e.stdin_body
    assert progs("python3 - <<'EOF'\nrm -rf x\nEOF") == ["python3"]
    assert find("node <<EOF\nx\nEOF", "node").stdin_from == "heredoc:interpreter"


def test_heredoc_to_anything_else_is_data():
    e = find("cat > notes.txt <<'EOF'\nrm -rf x\nEOF", "cat")
    assert e.stdin_from == "heredoc:data" and e.redirects == ((">", "notes.txt"),)
    assert "rm" not in progs("cat > notes.txt <<'EOF'\nrm -rf x\nEOF")


def test_heredoc_piped_into_a_shell_is_parsed():
    assert find("cat <<EOF | bash\nrm -rf x\nEOF", "rm").argv == ("-rf", "x")
    assert find("echo 'rm -rf x' | sh", "rm").argv == ("-rf", "x")
    assert find("bash <<< 'rm -rf x'", "rm").argv == ("-rf", "x")


def test_heredoc_variants():
    assert progs("cat <<-EOF\n\trm x\n\tEOF\nls") == ["cat", "ls"]
    assert progs("cat <<\"E-O\"\nrm x\nE-O\nls") == ["cat", "ls"]
    assert progs("cat <<EOF; ls\nrm x\nEOF\npwd") == ["cat", "ls", "pwd"]
    assert progs("cat <<A <<B\na\nA\nb\nB\nls") == ["cat", "ls"]
    assert progs("cat <<< hello\nls") == ["cat", "ls"]
    assert progs("x=$(cat <<'EOF'\nrm y\nEOF\n)\nls") == ["cat", "ls"]


def test_unquoted_heredoc_body_substitutions_execute():
    assert "rm" in progs("cat > f <<EOF\n$(rm -rf x)\nEOF")
    assert "rm" not in progs("cat > f <<'EOF'\n$(rm -rf x)\nEOF")


# quotes
@pytest.mark.parametrize("cmd,argv", [
    ("rm 'a b'", ("a b",)),
    ('rm "a ' + BS + '"b' + BS + '" c"', ('a "b" c',)),
    ("rm $'a" + BS + "tb'", ("a\tb",)),
    ("rm a\" b\"'c d'", ("a bc d",)),
    ("r''m x", ("x",)),
    ("rm --ap\"\"ply", ("--apply",)),
    ("rm \"$HOME/x\"", ("$HOME/x",)),
])
def test_bash_quotes(cmd, argv):
    assert find(cmd, "rm").argv == argv


def test_quoted_flags():
    e = find("rm 'a' b", "rm")
    assert e.quoted == (True, False)


def test_unquoted_backslash_is_read_both_ways_r6():
    p = parse("rm -rf C:" + BS + "GOG" + BS + "x", "bash")
    readings = sorted(e.argv[1] for e in p.execs if e.program == "rm")
    assert readings == ["C:GOGx", "C:" + BS + "GOG" + BS + "x"]
    assert [e.argv for e in parse("rm 'C:" + BS + "x'", "bash").execs] == [("C:" + BS + "x",)]


def test_comments_dropped():
    assert progs("ls # rm -rf x\n# rm y\npwd") == ["ls", "pwd"]
    assert progs("echo a#b") == ["echo"]


@pytest.mark.parametrize("cmd", [
    "if true; then rm -rf x; fi", "if false; then :; else rm -rf x; fi", "if a; then :; elif rm -rf x; then :; fi",
    "while true; do rm -rf x; done", "for f in a b; do rm -rf x; done", "! rm -rf x", "true || { rm -rf x; }",
    "case $a in x|y) rm -rf x;; *) :;; esac", "[[ -f a && -d b ]] && rm -rf x", "f() { rm -rf x; }",
    "function f { rm -rf x; }", "f () ( rm -rf x )", "time ( rm -rf x )", "until rm -rf x; do :; done",
    "a=(1 2 3); rm -rf x", "shopt -s extglob; ls !(y); rm -rf x",
])
def test_bash_compound_commands(cmd):
    assert find(cmd, "rm").argv == ("-rf", "x")


def test_for_header_words_are_not_a_command():
    assert progs("for rm in a b; do echo $rm; done") == ["echo"]


def test_bash_redirects():
    e = find("rm x > out 2>> err &> both 2>&1 < in", "rm")
    assert e.redirects == ((">", "out"), ("2>>", "err"), ("&>", "both"), ("2>&", "1"), ("<", "in"))
    assert parse("> f", "bash").execs[0].program == ""


@pytest.mark.parametrize("word,program", [
    ("/usr/bin/rm", "rm"), ("rm.exe", "rm"), ('"C:/x/RM.EXE"', "rm"), ("./tool.sh", "tool"), ("x.bat", "x"),
    ("$R", "?"), ("\"$R\"", "?"), ("$(which rm)", "?"), ("$HOME/bin/rm", "rm"),
])
def test_program_normalisation(word, program):
    p = parse(word + " a", "bash")
    assert p.ok and program in [e.program for e in p.execs]


# cd_before
@pytest.mark.parametrize("cmd,cd", [
    ("cd /c/x && rm a", "/c/x"),
    ("cd /c/x; cd Data; rm a", "/c/x/Data"),
    ("cd 'C:/GOG Games/x' && rm a", "C:/GOG Games/x"),
    ("cd && rm a", "~"),
    ("cd - && rm a", "?"),
    ("cd $X && rm a", "?"),
    ("cd $HOME/x && rm a", "$HOME/x"),
    ("cd /c/x && (cd y; true); rm a", "/c/x"),
    ("cd /c/x | true; rm a", ""),
    ("rm a", ""),
    ("cd /c/x && bash -c 'rm a'", "/c/x"),
    ("pushd /c/x && rm a", "/c/x"),
])
def test_bash_cd_before(cmd, cd):
    assert find(cmd, "rm").cd_before == cd


def test_cd_inside_a_subshell_applies_inside_it():
    assert find("(cd /c/x; rm a)", "rm").cd_before == "/c/x"


# ------------------------------------------------------------------------------------------------ PowerShell
@pytest.mark.parametrize("cmd,expected", [
    ("a; b | c && d || e\nf", ["a", "b", "c", "d", "e", "f"]),
    ("Get-ChildItem |\n Remove-Item", ["get-childitem", "remove-item"]),
    ("& 'C:/tools/x.exe' a", ["x"]),
    ("& $exe a", ["?"]),
    (". ./setup.ps1", ["setup"]),
])
def test_pwsh_separators_and_call(cmd, expected):
    assert progs(cmd, "powershell") == expected


@pytest.mark.parametrize("alias,canonical", [
    ("ri", "remove-item"), ("rm", "remove-item"), ("del", "remove-item"), ("erase", "remove-item"),
    ("rd", "remove-item"), ("rmdir", "remove-item"), ("mi", "move-item"), ("mv", "move-item"),
    ("move", "move-item"), ("cpi", "copy-item"), ("cp", "copy-item"), ("copy", "copy-item"),
    ("sc", "set-content"), ("ac", "add-content"), ("clc", "clear-content"), ("ni", "new-item"),
    ("sl", "set-location"), ("cd", "set-location"), ("chdir", "set-location"), ("iex", "invoke-expression"),
    ("RI", "remove-item"), ("Remove-Item", "remove-item"),
])
def test_pwsh_aliases(alias, canonical):
    assert progs(alias + " x", "powershell")[0] == canonical


@pytest.mark.parametrize("cmd,name,value", [
    ("Remove-Item -Path C:/x", "Path", "C:/x"),
    ("Remove-Item -Pa C:/x", "Path", "C:/x"),
    ("Remove-Item -path:C:/x", "Path", "C:/x"),
    ("Remove-Item -LiteralPath 'C:/x y'", "LiteralPath", "C:/x y"),
    ("Remove-Item -Li C:/x", "LiteralPath", "C:/x"),
    ("Move-Item a -Destination C:/x", "Destination", "C:/x"),
    ("Move-Item a -Dest C:/x", "Destination", "C:/x"),
    ("Out-File -FilePath C:/x", "FilePath", "C:/x"),
    ("Set-Content -Path a -Value C:/x", "Value", "C:/x"),
    ("Set-Content a -Va C:/x", "Value", "C:/x"),
])
def test_pwsh_named_parameters(cmd, name, value):
    e = parse(cmd, "powershell").execs[0]
    assert value in sp.ps_param(e, name)


def test_pwsh_positional_skips_switches_and_named_values():
    e = parse("Remove-Item -Recurse -Force -ErrorAction SilentlyContinue C:/x", "powershell").execs[0]
    assert sp.ps_param(e, "Path", 0) == ("C:/x",)


@pytest.mark.parametrize("cmd,argv", [
    ("ri 'a''b'", ("a'b",)),
    ('ri "a`"b"', ('a"b',)),
    ('ri "a""b"', ('a"b',)),
    ("ri a,b", ("a", "b")),
    ("ri `$x", ("$x",)),
    ('ri "C:\\x\\$env:USERNAME"', ("C:\\x\\$env:USERNAME",)),
])
def test_pwsh_quotes(cmd, argv):
    assert find(cmd, "remove-item", "powershell").argv == argv


@pytest.mark.parametrize("cmd", [
    "Get-ChildItem x | ForEach-Object { Remove-Item $_ }",
    "gci x | % { ri $_ }",
    "if (Test-Path x) { Remove-Item x } else { Write-Host no }",
    "foreach ($f in $files) { Remove-Item $f }",
    "Invoke-Command -ScriptBlock { Remove-Item x }",
    "$r = $(Remove-Item x)",
    "Write-Host \"$(Remove-Item x)\"",
    "$items = @(Remove-Item x)",
    "try { Remove-Item x } catch { }",
    "function F { Remove-Item x }",
    "iex 'Remove-Item x'",
    "Invoke-Expression -Command \"Remove-Item x\"",
    "Start-Process pwsh -ArgumentList '-c','x'; Remove-Item x",
])
def test_pwsh_blocks_and_subexpressions_parsed(cmd):
    assert "remove-item" in progs(cmd, "powershell")


@pytest.mark.parametrize("call,program", [
    ("[IO.File]::WriteAllText('C:/a.esp', 'x')", "io.file.write"),
    ("[System.IO.File]::WriteAllBytes('C:/a.esp', $b)", "io.file.write"),
    ("[IO.File]::AppendAllText('C:/a.esp', 'x')", "io.file.append"),
    ("[IO.File]::Delete('C:/a.esp')", "io.file.delete"),
    ("[io.file]::Move('C:/a.esp', 'C:/b.esp')", "io.file.move"),
    ("[IO.File]::Copy('C:/a.esp', 'C:/b.esp', $true)", "io.file.copy"),
    ("[IO.Directory]::Delete('C:/a', $true)", "io.directory.delete"),
])
def test_pwsh_io_file_calls_are_synthetic_execs(call, program):
    e = find("$null = " + call, program, "powershell")
    assert e.argv[0].startswith("C:/a")


def test_pwsh_redirects():
    e = find("Get-Content a > C:/x 2>> C:/y", "get-content", "powershell")
    assert e.redirects == ((">", "C:/x"), ("2>>", "C:/y"))
    assert [e.redirects for e in parse("'x' >> C:/z", "powershell").execs] == [((">>", "C:/z"),)]


def test_pwsh_expressions_and_here_strings_are_not_commands():
    assert progs("'rm -rf x' | Out-File a", "powershell") == ["out-file"]
    assert progs("@'\nRemove-Item x\n'@ | Out-File a", "powershell") == ["out-file"]
    assert progs("$x = 'a'; $y.Substring(1)", "powershell") == []
    # fix round 1: opaque, not nothing; final review I7: and `.Delete()` deletes its receiver ($y, unresolved)
    assert progs("$y.Delete()", "powershell") == ["?", "io.directory.delete"]
    assert progs("$h = @{ rm = 1 }", "powershell") == []
    assert progs("<# Remove-Item x #> ls # ri y", "powershell") == ["get-childitem"]


def test_pwsh_assignment_of_a_command():
    assert progs("$out = Remove-Item x", "powershell") == ["remove-item"]


def test_pwsh_cd_before():
    assert find("Set-Location C:/GOG; ri x", "remove-item", "powershell").cd_before == "C:/GOG"
    assert find("sl -Path C:/GOG; cd Data; ri x", "remove-item", "powershell").cd_before == "C:/GOG/Data"
    assert find("cd $env:TEMP; ri x", "remove-item", "powershell").cd_before == "?"


def test_pwsh_calls_bash_and_cmd_in_their_dialect():
    assert find("bash -c 'rm -rf x'", "rm", "powershell").dialect == "bash"
    assert find("cmd /c del /q x", "del", "powershell").dialect == "cmd"


# ------------------------------------------------------------------------------------------------ cmd
@pytest.mark.parametrize("cmd,expected", [
    ("a & b && c || d | e", ["a", "b", "c", "d", "e"]),
    ("del x & erase y & rd z & rmdir w & copy a b & move a b", ["del", "del", "rmdir", "rmdir", "copy", "move"]),
    ("@echo off", ["echo"]),
    ("if exist x (del x) else (del y)", ["del", "del"]),
    ("if not exist x del y", ["del"]),
    ("if a==b del y", ["del"]),
    ('if "%x%"=="" rd /s /q y', ["rmdir"]),
    ("if /i not a==b del y", ["del"]),
    ("for %f in (a b) do del %f", ["del"]),
    ("rem del x\ndir", ["dir"]),
    ("call del x", ["del"]),
    ("start \"\" /b del x", ["del"]),
])
def test_cmd_commands(cmd, expected):
    assert progs(cmd, "cmd") == expected


def test_cmd_flags_and_quotes():
    e = find('del /s /q "Data\\x y" ^& z', "del", "cmd")
    assert e.argv == ("/s", "/q", "Data\\x y", "&", "z")
    assert find("rd /s /q Data", "rmdir", "cmd").argv == ("/s", "/q", "Data")
    assert find("echo x > C:/f", "echo", "cmd").redirects == ((">", "C:/f"),)
    assert find("cd /d C:/GOG & del x", "del", "cmd").cd_before == "C:/GOG"


# ------------------------------------------------------------------------------------------------ R5
def test_unknown_dialect_unions_bash_and_powershell():
    p = parse("rm x", "")
    assert {(e.program, e.dialect) for e in p.execs} == {("rm", "bash"), ("remove-item", "powershell")}
    assert not parse("echo 'x", "").ok


# ------------------------------------------------------------------------------------------------ failure
@pytest.mark.parametrize("shell,cmd", [
    ("bash", "echo 'x"), ("bash", 'echo "x'), ("bash", "echo $'x"), ("bash", "echo `x"),
    ("bash", "cat <<EOF\nx"), ("bash", "echo $(x"), ("bash", "echo $(x)  )"), ("bash", "( x"),
    ("bash", "echo ${x"), ("bash", "x >"), ("bash", "a ;; b"), ("bash", "case x in a) b;;"),
    ("powershell", "ri 'x"), ("powershell", 'ri "x'), ("powershell", "ri $(x"), ("powershell", "{ x"),
    ("powershell", "x }"), ("powershell", "@'\nx"), ("powershell", "<# x"),
    ("cmd", 'del "x'), ("cmd", "(del x"),
])
def test_unparseable_is_ok_false_with_a_reason(shell, cmd):
    p = parse(cmd, shell)
    assert not p.ok and p.why and not p.why.startswith("internal error")


def test_too_many_tokens_fails_closed():
    p = parse("a " * (sp.MAX_TOKENS + 1), "bash")
    assert not p.ok and "tokens" in p.why
    assert parse("a " * (sp.MAX_TOKENS - 5), "bash").ok


@pytest.mark.parametrize("shell,opener,closer", [
    ("bash", "$(", ")"), ("bash", "( ", " )"), ("powershell", "$(", ")"), ("powershell", "{", "}"), ("cmd", "(", ")"),
])
def test_nesting_deeper_than_eight_fails_closed(shell, opener, closer):
    ok = opener * sp.MAX_DEPTH + "x" + closer * sp.MAX_DEPTH
    deep = opener * (sp.MAX_DEPTH + 1) + "x" + closer * (sp.MAX_DEPTH + 1)
    assert parse(ok, shell).ok
    p = parse(deep, shell)
    assert not p.ok and "nesting" in p.why


def test_nested_reparse_counts_toward_the_depth():
    cmd = "rm x"
    for _ in range(sp.MAX_DEPTH + 1):
        cmd = "bash -c " + "'" + cmd.replace("'", "'" + BS + "''") + "'"
    p = parse(cmd, "bash")
    assert not p.ok and "nesting" in p.why


def test_partial_execs_survive_a_failure():
    p = parse("rm -rf x; echo 'oops", "bash")
    assert not p.ok and [e.program for e in p.execs] == []          # the scan failed before phase 2
    p = parse("bash -c 'rm -rf x'; bash -c 'echo $('", "bash")
    assert not p.ok and "rm" in [e.program for e in p.execs]


def test_an_internal_error_is_reported_never_raised(monkeypatch):
    def boom(*a, **k):
        raise KeyError("bug")
    monkeypatch.setattr(sp, "_sub", boom)
    p = parse("rm x", "bash")
    assert not p.ok and p.why.startswith("internal error")
    assert not parse(None, "bash").ok


def _corpus_commands():
    out = []
    for name in ("expected.jsonl", "intended.jsonl"):
        for line in (REPO / "tests" / "guard_corpus" / name).read_text(encoding="utf-8").split(chr(10)):
            if not line.strip():
                continue
            pl = json.loads(line).get("payload")
            if isinstance(pl, dict) and isinstance((pl.get("tool_input") or {}).get("command"), str):
                out.append(pl["tool_input"]["command"])
            elif isinstance(pl, str):
                out.append(pl)
    return out


ALPHABET = ["a", "rm", " ", "  ", "\t", "\n", ";", "&", "|", "(", ")", "{", "}", "$", "$(", "`", "'", '"', BS,
            "<", ">", "<<", "<<<", "EOF", "#", "-c", "bash", "pwsh", "cmd", "/c", "-Command", "@", "@'", "'@",
            ",", "^", "%", "x=", "[[", "]]", "case", "in", "esac", "find", "-exec", "-delete", "{}", "xargs",
            "[IO.File]::Delete(", "ri", "<#", "#>", "\u2019", "\u201c", "*", "!", "=", ":", "$'", "${", "$((", "))"]


FRAGMENTS = ["rm -rf x", "bash -c 'rm x'", "cat <<EOF\nrm x\nEOF\n", "echo $(rm x)", "find . -delete",
             "find . -exec rm {} \\;", "xargs rm", "sudo env A=1 rm x", "cd /c/x", "pwsh -c 'ri x'", "cmd /c del x",
             "Remove-Item x -Recurse", "ri x | % { ri $_ }", "[IO.File]::Delete('x')", "@'\nx\n'@", "del /s /q x",
             "if true; then rm x; fi", "case a in b) rm x;; esac", "f() { rm x; }", "python - <<'P'\nx\nP\n",
             "a=(1 2)", "echo \"$(rm x)\"", "`rm x`", "<(rm x)", "rm C:\\x", "$R x", "(( i++ ))", "[[ -f x ]]"]
SEPARATORS = ["X; ", "X && ", "X | ", "X\n", "$(X) ", "( X ) ", "{ X; } ", "bash -c 'X'; ", "X & "]


def test_never_raises_on_random_text_or_corpus_commands():
    """10,000 seeded random strings plus every corpus command, in every dialect: always a Parsed, never an
    internal error (an internal error is a parser bug that the fail-closed net would hide)."""
    rng = random.Random(5)
    texts = ["".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, 40))) for _ in range(10000)]
    texts += ["".join(rng.choice(SEPARATORS).replace("X", rng.choice(FRAGMENTS)) for _ in range(rng.randint(1, 8)))
              for _ in range(3000)]                       # well-formed-ish: reaches the deeper branches
    texts += _corpus_commands()
    for text in texts:
        for shell in ("bash", "powershell", "cmd", ""):
            p = parse(text, shell)
            assert isinstance(p, sp.Parsed), (shell, text)
            assert not p.why.startswith("internal error"), (shell, text, p.why)
            assert "internal error" not in p.why, (shell, text, p.why)


# ------------------------------------------------------------------------------------------------ Task 6 contract
RESAVER_OPS = {"set-global", "set-var", "clean", "reset-havok", "cleanse-formlists", "remove-created"}


def _resaver_from_parser(cmd):
    """What S-RESAVER-APPLY becomes on the parser (Task 6): ReSaver is the PROGRAM run, with --apply and a write
    operation among ITS arguments."""
    for e in parse(cmd, "bash").execs:
        runs = e.program in ("resaver-cli", "resavercli") or (
            e.program == "java" and any("resaver" in a.lower() for a in e.argv))
        if runs and "--apply" in e.argv and RESAVER_OPS & {a.lower() for a in e.argv}:
            return True
    return False


def _t4_rows():
    rows = [json.loads(x) for x in (REPO / "tests/guard_corpus/intended.jsonl").read_text(encoding="utf-8")
            .splitlines() if x.strip()]
    return [r for r in rows if r["task"] == "T4"]


@pytest.mark.parametrize("row", _t4_rows(), ids=[r["id"] for r in _t4_rows()])
def test_t04_rows_come_out_as_executed_commands(row):
    """Every T4 intended row's decision, reproduced from execs alone: the shapes Task 6 re-bases ReSaver onto. Since
    the save policy (2026-10-13) a detected --apply is refused when the save cannot be placed and allowed when
    a new output resolves through an absolute path or a known preceding cd. Policy and detection are separate."""
    detected = row["decision"] == "deny" or row["id"] in ("t04-quoted-spaces", "t04-heredoc-sh-s")
    assert _resaver_from_parser(row["payload"]["tool_input"]["command"]) is detected


def test_t04_rows_parse():
    for row in _t4_rows():
        assert parse(row["payload"]["tool_input"]["command"], "bash").ok, row["id"]


# ------------------------------------------------------------------------------------------------ fix round 1
# The review's rule: when the parser cannot be sure what runs, it says so (ok=False, or an opaque "?" exec) --
# never ok=True with the executed command missing. Every row below was ok=True with the delete missing.
DELETES = {"rm", "rmdir", "del", "remove-item", "find.delete", "io.file.delete", "io.directory.delete"}


def deletes_x(cmd, shell):
    """ok=True and a delete whose LAST argument is X, or ok=False (said so)."""
    p = parse(cmd, shell)
    if not p.ok:
        return "unsure"
    return any(e.program in DELETES and e.argv and e.argv[-1].rstrip("/") == "X" for e in p.execs)


def opaque(cmd, shell):
    p = parse(cmd, shell)
    return (not p.ok) or "?" in [e.program for e in p.execs]


@pytest.mark.parametrize("cmd", [
    "if a==b rd /s /q X", 'if "%x%"=="" rd /s /q X', "if %x%==1 del /q X", "if not a==b rd /s /q X",
    "if /i a==a rd /s /q X", "if a == b rd /s /q X", "if exist X rd /s /q X", "if not exist Y rd /s /q X",
    "if defined FOO rd /s /q X", "if errorlevel 1 rd /s /q X", "if a equ b rd /s /q X",
    "if 1==1 (rd /s /q X) else (echo)",
])
def test_r1_cmd_if_condition_is_not_the_program(cmd):
    assert deletes_x(cmd, "cmd") is True


@pytest.mark.parametrize("cmd", [
    "rd/s/q X", "del/f/q X", "rd/q X", "del.  /q X", "rmdir/s/q X",
    "for %i in (X) do rd /s /q X", "for %i in (X) do (rd /s /q X)", "for %i in (X) do @rd /s /q X",
    "for /f %i in ('rd /s /q X') do echo", "for /f \"usebackq\" %i in (`rd /s /q X`) do echo",
    "rem x & rd /s /q X", ":: c & rd /s /q X", "echo hi & rem & rd /s /q X",
    "%COMSPEC% /c del X", "%SystemRoot%\\System32\\cmd.exe /c rd /s /q X",
])
def test_r1_cmd_forms(cmd):
    assert deletes_x(cmd, "cmd") is True


@pytest.mark.parametrize("cmd", [
    "time { rm -rf X; }", "time -p { rm -rf X; }", "time ( rm -rf X )", "time rm -rf X", "time -p rm -rf X",
    "coproc { rm -rf X; }", "coproc foo { rm -rf X; }", "coproc foo ( rm -rf X )", "coproc rm -rf X",
])
def test_r1_bash_time_and_coproc(cmd):
    assert deletes_x(cmd, "bash") is True


@pytest.mark.parametrize("cmd", [
    "files=( $(rm -rf X) )", 'files=("$(rm -rf X)")', "x+=( $(rm -rf X) )", "x=(`rm -rf X`)",
    "declare -a x=( $(rm -rf X) )", "x=( a $(rm -rf X) )", "shopt -s extglob; ls @(a|$(rm -rf X))",
])
def test_r1_array_literals_are_scanned_for_substitutions(cmd):
    assert deletes_x(cmd, "bash") is True


@pytest.mark.parametrize("cmd", [
    "echo ${x:-$(rm -rf X)}", "echo ${x:-`rm -rf X`}", 'echo "${x:-$(rm -rf X)}"', "echo $(( $(rm -rf X) + 1 ))",
    "(( $(rm -rf X) ))", "echo $(( `rm -rf X` ))",
])
def test_r1_skipped_spans_with_substitutions_are_parsed(cmd):
    assert deletes_x(cmd, "bash") in (True, "unsure")
    assert deletes_x(cmd, "bash") is True


@pytest.mark.parametrize("cmd", ["((rm -rf X))", "(((rm -rf X)))", "( (( (rm -rf X) )) )", "$((rm -rf X))"])
def test_r1_double_paren_that_reads_as_a_command_is_unsure(cmd):
    assert not parse(cmd, "bash").ok


@pytest.mark.parametrize("cmd", ["(( i++ ))", "(( a > b ))", "(( x -= 1 ))", "echo $(( n % 2 ))", "((count++))",
                                 "(( a && b ))", "echo $((16#ff))"])
def test_r1_real_arithmetic_still_parses(cmd):
    assert parse(cmd, "bash").ok


def test_r1_pwsh_here_string_with_a_subexpression_is_unsure():
    assert not parse('Write-Output @"\n$(Remove-Item X)\n"@', "powershell").ok
    assert parse("Write-Output @'\n$(Remove-Item X)\n'@", "powershell").ok       # single-quoted: literal


@pytest.mark.parametrize("cmd", [
    "Start-Process cmd -ArgumentList '/c rd /s /q X'",
    "Start-Process -FilePath cmd -ArgumentList '/c','rd /s /q X'",
    "Start-Process cmd -Args '/c rd /s /q X'",
    "Start-Process cmd -Arg '/c','rd','/s','/q','X'",
    "saps cmd '/c rd /s /q X'",
    "Start-Process -FilePath 'cmd.exe' -ArgumentList '/c rd /s /q X' -Wait",
    "Start-Process powershell -ArgumentList '-c Remove-Item X'",
    'Start-Process powershell -ArgumentList "-Command","Remove-Item X"',
    "Start-Process pwsh -ArgumentList '-NoProfile','-Command','Remove-Item X'",
])
def test_r1_start_process_runs_its_argument_list(cmd):
    assert deletes_x(cmd, "powershell") is True


@pytest.mark.parametrize("shell,cmd", [
    ("powershell", "& $env:ComSpec /c del X"), ("powershell", "cmd --% /c rd /s /q X"),
    ("bash", "env -S 'rm -rf X'"), ("bash", "env --split-string='rm -rf X'"), ("bash", "env -S'rm -rf X'"),
    ("bash", "wsl rm -rf X"), ("bash", "wsl -d Ubuntu -u root -- rm -rf X"), ("bash", "wsl -e rm -rf X"),
    ("powershell", "wsl rm -rf X"), ("bash", "busybox rm -rf X"), ("bash", "su -c 'rm -rf X'"),
    ("bash", "su root -c 'rm -rf X'"), ("bash", "trap 'rm -rf X' EXIT"), ("bash", "trap -- 'rm -rf X' EXIT"),
    ("bash", "ionice -c3 rm -rf X"), ("bash", "ionice -c 3 -n 7 rm -rf X"), ("bash", "chroot / rm -rf X"),
    ("bash", "strace -o log rm -rf X"), ("bash", "flock /tmp/l rm -rf X"), ("bash", "flock /tmp/l -c 'rm -rf X'"),
    ("bash", "unbuffer rm -rf X"), ("bash", "watch -n 5 rm -rf X"), ("bash", "script -q -c 'rm -rf X'"),
    ("bash", "alias ll='rm -rf X'; ll"), ("bash", "parallel rm -rf ::: X"), ("bash", "setsid rm -rf X"),
    ("bash", "taskset 0x1 rm -rf X"),
])
def test_r1_more_wrappers(shell, cmd):
    assert deletes_x(cmd, shell) is True


# Merge of rt-fix-a into plan5 (2026-10-05): an alias is judged twice, where it is DEFINED (R1: its use may be in a later
# tool call the guard never sees beside this one) and where it is USED (RT-A: the use adds its own arguments). Each half
# alone passes `alias ll='rm -rf X'; ll`, so each gets its own pin.
def test_r1_an_alias_body_is_judged_where_it_is_defined():
    assert deletes_x("alias ll='rm -rf X'", "bash") is True


def test_ra_an_alias_use_runs_its_body_with_the_uses_arguments():
    assert deletes_x("alias r='rm -rf'; r X", "bash") is True


@pytest.mark.parametrize("shell", ["bash", "powershell", "cmd"])
def test_r1_wsl_and_watch_run_their_words_as_bash(shell):
    """From PowerShell, `wsl rm -rf X` runs Linux rm -- not the PowerShell alias rm = Remove-Item."""
    assert find("wsl rm -rf X", "rm", shell).dialect == "bash"
    assert find('watch -n 5 "rm -rf X"', "rm", shell).argv == ("-rf", "X")


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_r1_wsl_management_and_quoted_words(shell):
    """Found re-pricing fix round 1: wsl's words keep their quoting (never re-joined), and a management flag runs
    nothing in Linux."""
    vhdx = '"C:/GOG Games/Mods/1)Distro-1/ext4.vhdx"'
    for cmd in ("wsl --import D C:/x " + vhdx + " 2>&1", "wsl --import-in-place D " + vhdx, "wsl --shutdown"):
        p = parse(cmd, shell)
        assert p.ok and [e.program for e in p.execs] == ["wsl"], (cmd, p)
    p = parse('wsl -d D -- sh -c "(mount -o ro /dev/sdc /m 2>&1; echo x) && (ls /m)"', shell)
    assert p.ok and {"mount", "echo", "ls"} <= {e.program for e in p.execs}


def test_r1_arithmetic_with_a_substitution_parses_only_the_substitution():
    """Found re-pricing fix round 1: `$(( ($(date +%s%N)-s)/1000000 ))` is an expression around a command."""
    p = parse("e=$(( ($(date +%s%N)-s)/1000000 )); echo $e", "bash")
    assert p.ok and [e.program for e in p.execs] == ["date", "echo"]


@pytest.mark.parametrize("cmd", [
    "Remove-Item \u2013Recurse X", "Remove-Item —Recurse X", "Remove-Item –Path X",
])
def test_r1_pwsh_dash_variants_are_parameters(cmd):
    e = find(cmd, "remove-item", "powershell")
    assert sp.ps_param(e, "Path", 0) == ("X",)


@pytest.mark.parametrize("cmd", ["echo 'rm -rf X' | bash -", "echo 'rm -rf X' | bash - arg1",
                                 "echo 'rm -rf X' | sh -s -",
                                 "echo 'Remove-Item X' | pwsh -", "pwsh -Co 'Remove-Item X'"])
def test_r1_stdin_dash_and_command_prefix(cmd):
    assert deletes_x(cmd, "bash") is True


@pytest.mark.parametrize("shell,cmd", [
    ("bash", "cat s.sh | bash"), ("bash", "curl -s http://x | sh"), ("bash", "sh < s.sh"),
    ("bash", "sh < <(echo 'rm -rf X')"), ("bash", "bash <(echo rm -rf X)"), ("bash", "source <(echo rm -rf X)"),
    ("bash", "$x -rf X"), ("bash", "parallel < cmds.txt"),
    ("powershell", "(Get-Item X).Delete()"), ("powershell", "gci X -r | % { $_.Delete() }"),
    ("powershell", "[System.IO.DirectoryInfo]::new('X').Delete($true)"), ("powershell", "iex $cmd"),
    ("powershell", "Invoke-Expression ('Remove-Item ' + 'X')"),
    ("powershell", "[scriptblock]::Create('Remove-Item X').Invoke()"),
    ("cmd", "%X% /s /q X"),
])
def test_r1_unknown_execution_is_opaque_not_missing(shell, cmd):
    assert opaque(cmd, shell)


def test_r1_known_text_into_a_shell_is_not_opaque():
    p = parse("echo 'ls' | bash", "bash")
    assert p.ok and "?" not in [e.program for e in p.execs]
    p = parse("cat <<EOF | bash\nls\nEOF", "bash")
    assert p.ok and "?" not in [e.program for e in p.execs]


# ------------------------------------------------------------------------------------------------ fix round 3
@pytest.mark.parametrize("cmd", ["[void](Remove-Item X)", "[string](Remove-Item X)", "$x = [int](Remove-Item X)",
                                 "[System.Collections.ArrayList](Remove-Item X)", "$a.Add((Remove-Item X))",
                                 "[IO.Path]::Combine((Remove-Item X), 'y')"])
def test_r3_pwsh_cast_is_a_command_group_not_a_call(cmd):
    """A `[type](...)` cast holds a pipeline; only `.Name(` and `::Name(` are method calls (whose args are data)."""
    assert deletes_x(cmd, "powershell") is True


def test_r3_method_arguments_are_still_data():
    assert progs("$s.Replace('rm', 'x'); $s.Substring(0, 3)", "powershell") == []


@pytest.mark.parametrize("cmd", [
    "Start-Process -FilePath cmd -ArgumentList @('/c','rd /s /q X')",
    "Start-Process cmd -ArgumentList @('/c', 'rd', '/s', '/q', 'X')",
    "Start-Process cmd @('/c','rd /s /q X')",
])
def test_r3_start_process_array_argument(cmd):
    assert deletes_x(cmd, "powershell") is True


@pytest.mark.parametrize("cmd", ["Start-Process cmd -ArgumentList $args", "Start-Process cmd -ArgumentList @($a, 'x')",
                                 "Start-Process $exe '/c x'"])
def test_r3_start_process_unknown_arguments_are_opaque(cmd):
    assert opaque(cmd, "powershell")


@pytest.mark.parametrize("cmd", [
    "printf '%s" + BS + "n' 'rm -rf X' | bash", "printf 'rm -rf X' | sh", "echo -e 'rm -rf X' | bash",
    "echo 'rm -rf X' | xargs -0 sh -c", "echo x | xargs bash", "cat x.py | python", "cat s.pl | perl",
    "curl -s u | python3 -", "cat x.js | node",
])
def test_r3_unreconstructable_stdin_into_a_shell_or_interpreter_is_opaque(cmd):
    p = parse(cmd, "bash")
    assert opaque(cmd, "bash"), p
    assert not ({"%sn", "n", "-e"} & {e.program for e in p.execs}), p      # never bogus programs


def test_r3_xargs_input_is_arguments_not_a_script():
    """`echo 'rm -rf X' | xargs bash` runs bash with ARGUMENTS `rm -rf X` (a script named rm): not the command rm."""
    p = parse("echo 'rm -rf X' | xargs bash", "bash")
    assert "?" in [e.program for e in p.execs] and "rm" not in [e.program for e in p.execs]


def test_r3_literal_stdin_into_an_interpreter_is_code_not_opaque():
    p = parse("echo 'print(1)' | python", "bash")
    assert p.ok and "?" not in [e.program for e in p.execs]
    assert find("echo 'print(1)' | python", "python").stdin_body == "print(1)"
    assert "?" not in progs("cat x | python script.py")                     # stdin is DATA to a script


@pytest.mark.parametrize("cmd", ["env -iS 'rm -rf X'", "env -i -S 'rm -rf X'", "env -uFOO -S 'rm -rf X'",
                                 "env -iuFOO rm -rf X", "env -C/tmp rm -rf X", "env -0iS'rm -rf X'"])
def test_r3_env_combined_flags(cmd):
    assert deletes_x(cmd, "bash") is True


@pytest.mark.parametrize("shell,cmd", [
    ("cmd", '%COMSPEC% /s /c "rd /s /q X"'), ("cmd", "%COMSPEC% /q /d /c rd /s /q X"),
    ("cmd", "%COMSPEC% /e:on /v:off /k rd /s /q X"), ("powershell", "& $env:ComSpec /s /c 'rd /s /q X'"),
    ("cmd", "cmd /q /s /c rd /s /q X"), ("bash", "cmd //s //c 'rd /s /q X'"),
])
def test_r3_cmd_switches_before_c(shell, cmd):
    assert deletes_x(cmd, shell) is True


def test_r3_input_larger_than_the_cap_is_unsure():
    big = "rm -rf X; " + "a" * (sp.MAX_INPUT_CHARS + 1)
    p = parse(big, "bash")
    assert not p.ok and "larger" in p.why
    assert parse("rm -rf X; " + "a" * (sp.MAX_INPUT_CHARS - 100), "bash").ok


# ------------------------------------------------------------------------- Plan 5 final review, lane A (fix pass)
# Each finding was a parse that returned ok=True with the executed command, or its directory, dropped.
_OPTION_VALUES = {"pipefail", "errexit", "posix", "extglob", "nounset"}


@pytest.mark.parametrize("cmd", [
    "bash -eo pipefail -c 'rm -rf X'", "bash -euo pipefail -c 'rm -rf X'", "sh -o errexit -c 'rm -rf X'",
    "bash +o posix -c 'rm -rf X'", "bash -O extglob -c 'rm -rf X'", "bash -oO pipefail extglob -c 'rm -rf X'",
    "bash -c -o pipefail 'rm -rf X'", "bash -ec -o pipefail 'rm -rf X'", "dash -eo errexit -c 'rm -rf X'",
    "bash -eo pipefail +O extglob -c 'rm -rf X'", "zsh -oerrexit -c 'rm -rf X'", "zsh -eo errexit -c 'rm -rf X'",
    "bash +c 'rm -rf X'",                       # MEASURED in Git Bash: `bash +c 'echo x'` runs the string
])
def test_c1_a_shell_option_value_is_not_the_command(cmd):
    """C1: `-eo pipefail` took `pipefail` as the script FILE and never reached the -c string."""
    assert deletes_x(cmd, "bash") is True
    assert not _OPTION_VALUES & set(progs(cmd))


@pytest.mark.parametrize("cmd", ["echo 'rm -rf X' | bash -eo pipefail", "echo 'rm -rf X' | sh -euo nounset",
                                 "cat <<'EOF' | bash -o pipefail -e\nrm -rf X\nEOF"])
def test_c1_a_shell_with_option_values_still_reads_the_pipe(cmd):
    assert deletes_x(cmd, "bash") is True


def test_c1_a_script_after_option_values_is_the_script():
    assert progs("bash -eo pipefail scripts/build.sh a") == ["build"]
    assert progs("zsh -oerrexit scripts/build.sh") == ["build"]
    assert progs("zsh -oshwordsplit scripts/build.sh") == ["build"]     # zsh: the value is the rest of the word


@pytest.mark.parametrize("cmd", ["bash /dev/stdin <<'EOF'\nrm -rf X\nEOF", "sh /proc/self/fd/0 <<< 'rm -rf X'",
                                 "source /dev/stdin <<'EOF'\nrm -rf X\nEOF", "echo 'rm -rf X' | bash /dev/stdin"])
def test_m4_a_script_that_is_stdin_reads_the_heredoc_or_pipe(cmd):
    assert deletes_x(cmd, "bash") is True


@pytest.mark.parametrize("cmd,cd", [
    ("Set-Location C:/x; Remove-Item a", "C:/x"),
    ("Set-Location C:/x | Out-Null; Remove-Item a", "C:/x"),
    ("Push-Location C:/x | Out-Null; Remove-Item a", "C:/x"),
    ("if ($true) { Set-Location C:/x }; Remove-Item a", "?"),
    ("try { Set-Location C:/x } catch {}; Remove-Item a", "?"),
    ("& { Set-Location C:/x }; Remove-Item a", "?"),
    (". { Set-Location C:/x }; Remove-Item a", "?"),
    ("Get-Item C:/x | Set-Location; Remove-Item a", "?"),
    ("Set-Location C:/x; if ($true) { Get-Date }; Remove-Item a", "C:/x"),
    ("if ($true) { Set-Location C:/x; Remove-Item a }", "C:/x"),
])
def test_i1_powershell_location_is_session_wide(cmd, cd):
    """I1: a Set-Location in a pipeline stage or a block moves the CALLER (bash's subshell rule does not apply)."""
    assert find(cmd, "remove-item", "powershell").cd_before == cd


def test_i1_bash_pipeline_and_subshell_cd_still_do_not_carry():
    assert find("cd /c/x | true; rm a", "rm").cd_before == ""
    assert find("(cd /c/x); rm a", "rm").cd_before == ""


@pytest.mark.parametrize("cmd,cd", [
    ("cd C:" + BS + "t7" + BS + "Skyrim VR && rd /s /q Data", "C:" + BS + "t7" + BS + "Skyrim VR"),
    ("cd /d C:" + BS + "a b" + BS + "c d & rd Data", "C:" + BS + "a b" + BS + "c d"),
    ("chdir C:" + BS + "a b & rd Data", "C:" + BS + "a b"),
    ('cd/d "C:' + BS + 't7' + BS + 'Skyrim VR" && rd /s /q Data', "C:" + BS + "t7" + BS + "Skyrim VR"),
    ("cd/D C:" + BS + "x && rd Data", "C:" + BS + "x"),
    ("cd" + BS + "t7" + BS + "Skyrim VR && rd /s /q Data", BS + "t7" + BS + "Skyrim VR"),
    ("cd.. && rd Data", ".."),
    ("cd C:" + BS + "a  b && rd Data", "?"),
    ("pushd C:" + BS + "a b && rd Data", "?"),
    ("cd C:" + BS + "a %X% && rd Data", "?"),
    ("cd/x C:" + BS + "a && rd Data", "?"),
    ('cd "C:' + BS + 'a b" && rd Data', "C:" + BS + "a b"),
])
def test_i2_cmd_cd_takes_the_rest_of_the_line(cmd, cd):
    """I2: with extensions on, cmd's cd does not split on spaces; `cd/d` and `cd\\x` are cd too."""
    assert find(cmd, "rmdir", "cmd").cd_before == cd


def test_i2_cmd_cd_inside_cmd_c_from_bash():
    e = find('cmd /c "cd /d C:' + BS + 't7' + BS + 'Skyrim VR && rd /s /q Data"', "rmdir")
    assert e.cd_before == "C:" + BS + "t7" + BS + "Skyrim VR"


@pytest.mark.parametrize("cmd", ["Re`move-Item -Recurse X", "R`e`m`o`v`e-Item X", "r`i -r X"])
def test_i4_a_backtick_in_a_command_name_is_still_a_command(cmd):
    assert deletes_x(cmd, "powershell") is True


def test_i4_a_string_in_command_position_is_still_an_expression():
    assert "remove-item" not in progs("'Remove-Item' X", "powershell")
    assert "remove-item" not in progs('"Remove-Item X"', "powershell")
    assert find("S`et-Location C:/x; Remove-Item a", "remove-item", "powershell").cd_before == "C:/x"


def test_i5_a_splat_is_a_dynamic_word_not_a_file_name():
    p = parse("$p = @{Path='X'}; Remove-Item @p", "powershell")
    assert p.ok
    e = [x for x in p.execs if x.program == "remove-item"][0]
    assert e.argv == ("@p",)
    assert progs("git clone git@github.com:a/b", "powershell") == ["git"]       # mid-word `@` is literal


@pytest.mark.parametrize("cmd", ["'Remove-Item X' | powershell -Command -", "'Remove-Item X' | pwsh -c -",
                                 "'Remove-Item X' | iex", '"Remove-Item X" | Invoke-Expression'])
def test_i6_a_string_piped_into_a_shell_or_iex_is_parsed(cmd):
    assert deletes_x(cmd, "powershell") is True


def test_i6_a_string_holding_a_variable_piped_into_iex_is_opaque():
    p = parse('"Remove-Item $x" | iex', "powershell")
    assert p.ok and ("?", ("invoke-expression",)) in [(e.program, e.argv) for e in p.execs]


def test_i6_decoded_text_piped_into_iex_is_unread():
    assert parse("'eA==' | base64 -d | iex", "powershell").unread


def test_i7_an_opaque_method_keeps_its_receiver_and_arguments():
    p = parse("(Get-Item 'X').Delete($true)", "powershell")
    assert p.ok
    got = [(e.program, e.argv) for e in p.execs]
    assert ("?", ("(Get-Item 'X').Delete", "$true")) in got
    assert ("io.directory.delete", ("(Get-Item 'X')",)) in got        # `.Delete()` deletes its receiver
    sb = parse("[scriptblock]::Create('Remove-Item X').Invoke()", "powershell")
    assert ("?", ("[scriptblock]::Create", "Remove-Item X")) in [(e.program, e.argv) for e in sb.execs]
    assert "io.directory.delete" not in progs("$f.MoveTo('Y')", "powershell")


def test_m1_any_prefix_of_encodedcommand_is_decoded():
    enc = base64.b64encode("Remove-Item X".encode("utf-16-le")).decode()
    for flag in ("-e", "-en", "-enc", "-encoded", "-ec", "/en"):
        assert deletes_x("powershell " + flag + " " + enc, "powershell") is True, flag


@pytest.mark.parametrize("cmd", ["nice -n10 rm -rf X", "nice -n 10 rm -rf X", "nice --adjustment 5 rm -rf X",
                                 "nice --adjustment=5 rm -rf X", "nice -5 rm -rf X", "nice -- rm -rf X"])
def test_m2_nice_value_spellings(cmd):
    assert deletes_x(cmd, "bash") is True


# Coordinator ruling on the lane-A probe: cmd's /c quote rule, and nested quotes split by the caller fail closed.
@pytest.mark.parametrize("cmd", ["cmd //s //c '\"rd /s /q X\"'", "cmd //c '\"rd /s /q X\"'",
                                 "cmd /s /c '\"rd /s /q\" X'"])
def test_cmd_c_strips_its_outer_quotes(cmd):
    assert deletes_x(cmd, "bash") is True


def test_cmd_c_two_quotes_around_a_spaced_name_reads_both_ways():
    assert {"x", "program"} <= set(progs("cmd //c '\"C:" + BS + "Program Files" + BS + "x.exe\" a'"))


@pytest.mark.parametrize("cmd,shell", [
    ('cmd //s //c "rd /s /q "C:' + BS + 'a b' + BS + 'Data""', "bash"),
    ('cmd /s /c "rd /s /q "C:' + BS + 'a b' + BS + 'Data""', "powershell"),
    ('cmd /s /c "rd /s /q "C:' + BS + 'a b' + BS + 'Data""', "cmd"),
])
def test_cmd_c_nested_quotes_split_by_the_caller_are_opaque(cmd, shell):
    p = parse(cmd, shell)
    assert p.ok
    assert ("?", ("rd /s /q C:" + BS + "a b" + BS + "Data",)) in [(e.program, e.argv) for e in p.execs]


def test_cmd_c_a_single_word_line_is_not_opaque():
    assert "?" not in progs('cmd //c "rd /s /q X"')
    assert "?" not in progs("cmd //c dir " + '"C:' + BS + 'a b"')


@pytest.mark.parametrize("line,s_flag,want", [
    ('"a b" c', False, ["a b c", '"a b" c']),        # two quotes, whitespace, nothing special: both readings
    ('"a "b c""', False, ['a "b c"']),               # more than two quotes: stripped
    ('"a & b"', False, ["a & b"]),                   # a special character between them: stripped
    ('"ab" c', False, ["ab c"]),                     # no whitespace between them: stripped
    ('"a b" c', True, ["a b c"]),                    # /S: always stripped
    ('a "b c"', False, ['a "b c"']),                 # the line does not start with a quote: as written
])
def test_cmd_c_quote_rule(line, s_flag, want):
    assert sp._cmd_c_lines(line, s_flag) == want


def test_cmd_c_s_switch_always_strips():
    assert "q x" not in progs("cmd //s //c '\"rd /s /q X\"'")
    assert "q x" in progs("cmd //c '\"rd /s /q X\"'")         # without /S cmd may keep the quotes: both readings


# fix-limits 14: a cd whose folder is a same-command literal (Exec.subst's resolution) moves the cwd the parser models.
@pytest.mark.parametrize("dialect,command,want", [
    ("bash", 'D=/a/b; cd "$D" && rm x', "/a/b"),
    ("bash", 'D=/a; cd "$D/b" && rm x', "/a/b"),
    ("bash", "D=/a/b; cd $D; rm x", "/a/b"),
    ("bash", 'cd "$UNSET" && rm x', "?"),
    ("bash", 'if y; then D=/a; fi; cd "$D" && rm x', "?"),
    ("bash", 'D=/a; eval "D=/b"; cd "$D" && rm x', "?"),
    ("bash", 'D=/a; source x.sh; cd "$D" && rm x', "?"),          # poisoned: sourced code may reassign D
    ("bash", 'D=/a; done; cd "$D" && rm x', "?"),                 # a stray closer: the statement list is unknown
    ("powershell", "$d = '/a/b'; Set-Location $d; Remove-Item x", "/a/b"),
    ("powershell", "Set-Location $d; Remove-Item x", "?"),
])
def test_a_cd_through_a_same_command_literal_is_followed(dialect, command, want):
    p = parse(command, dialect)
    (rm,) = [e for e in p.execs if e.program in ("rm", "remove-item")]
    assert rm.cd_before == want, (command, rm.cd_before)
