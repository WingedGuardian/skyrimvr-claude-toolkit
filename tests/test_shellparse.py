r"""shellparse: what a command line EXECUTES (bash, PowerShell, cmd). Plan 5 Task 5.

A misparse is a silent allow once rules sit on this parser (Review Focus 1), so every executed-code path the plan
names has a row here, and test_mutations.py deletes each branch and names the row that must go red.
"""
import base64
import json
import random
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import shellparse as sp  # noqa: E402
from skyguard.shellparse import parse  # noqa: E402

BS = chr(92)


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
    ("find Data -name '*.tmp' -delete", ("Data",)),
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
    assert find(cmd, "rm").argv == ("-rf", "Data")


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
    assert progs("$x = 'a'; $y.Delete()", "powershell") == []
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
    """Every T4 intended row's decision, reproduced from execs alone: the shapes Task 6 re-bases ReSaver onto."""
    assert _resaver_from_parser(row["payload"]["tool_input"]["command"]) is (row["decision"] == "ask")


def test_t04_rows_parse():
    for row in _t4_rows():
        assert parse(row["payload"]["tool_input"]["command"], "bash").ok, row["id"]
