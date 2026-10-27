"""Task 7 P4/P8/P7 retained probes: every command here is parser input, never executed."""
import sys
from pathlib import PureWindowsPath, PurePosixPath

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import deletes, paths, shell_rules, shellparse


class Audit:
    def rule(self, *args, **kwargs):
        pass


ROOT = "C:/Review/Game"
ROOTS = paths.Roots(paths.path_to_regex(ROOT), "", install_dirs=(ROOT.lower(),))


def decide(command, dialect="bash"):
    return shell_rules.decide(command, ROOTS, Audit(), cwd="C:/Review/Scratch", shell=dialect, env={})


@pytest.mark.parametrize("parts", [("/tmp", "C:/", "Review", "Game", "Data/x.txt"),
                                  ("C:/outside", "/Review/Game/Data/x.txt")])
def test_windows_path_constructor_resets_earlier_parts(monkeypatch, parts):
    monkeypatch.setattr(paths, "POSIX_HOST", False)
    code = "from pathlib import Path; Path" + repr(parts) + ".unlink()"
    assert deletes.interpreter_delete_targets(code, True) == [PureWindowsPath(*parts).as_posix()]
    assert decide('python -c "' + code + '"').decision == "deny"


@pytest.mark.parametrize("parts", [("/outside", "/tmp", "x"), ("/outside", "C:/", "x")])
def test_posix_path_constructor_resets_to_a_later_absolute_part(monkeypatch, parts):
    monkeypatch.setattr(paths, "POSIX_HOST", True)
    code = "from pathlib import Path; Path" + repr(parts) + ".unlink()"
    assert deletes.interpreter_delete_targets(code, True) == [PurePosixPath(*parts).as_posix()]


@pytest.mark.parametrize("arg", ["%FLAGS%", "!FLAGS!", "$FLAGS", "`pick`"])
def test_unproved_native_cli_flags_are_writes(arg):
    command = "resaverpp-cli x.ess " + arg
    assert shell_rules.resaver_native_direct(command, shellparse.parse(command, "cmd")) == "resaverpp-cli.exe"
    assert decide(command, "cmd").decision == "deny"


@pytest.mark.parametrize("command", ["resaverpp-cli x.ess --top 20", "resaverpp-cli --json info x.ess"])
def test_literal_native_reads_stay_read_only(command):
    assert not shell_rules.resaver_native_direct(command, shellparse.parse(command, "cmd"))
    assert decide(command, "cmd").decision == "allow"


@pytest.mark.parametrize("command", [
    'c=\'cd "C:/Review/Game"\'; eval "$c"; rm -rf Data',
    'eval \'cd "C:/Review/Game"\'; rm -rf Data',
    'eval \'cd "C:/Review/Game"\'; cd Data; rm x.txt',
    'eval \'cd "C:/Review/Game"\'; (rm -rf Data)',
    'cd "C:/Review"; eval \'cd Game\'; rm -rf Data',
    'eval \'eval "cd C:/Review/Game"\'; rm -rf Data',
    'eval \'cd "C:/Review/Game"\'; ( (rm -rf Data) )',
    'eval \'cd "C:/Review/Game"\'; echo `rm -rf Data`',
    'eval \'cd "C:/Review/Game"\'; echo $(( $(rm -rf Data) ))',
])
def test_eval_directory_change_reaches_later_caller_commands(command):
    parsed = shellparse.parse(command, "bash")
    assert parsed.ok, parsed.why
    assert decide(command).decision == "deny"


@pytest.mark.parametrize("command", [
    'bash -c \'cd "C:/Review/Game"\'; rm -rf Data',
    'sh -c \'cd "C:/Review/Game"\'; rm -rf Data',
    '(eval \'cd "C:/Review/Game"\'); rm -rf Data',
    'eval \'cd "C:/Review/Game"\' | cat; rm -rf Data',
    'eval \'cd "C:/Review/Game"\' |& cat; rm -rf Data',
    'echo x | eval \'cd "C:/Review/Game"\'; rm -rf Data',
    'eval \'cd "C:/Review/Game"\' & rm -rf Data',
    'eval \'cd "C:/Review/Game"\'; cd "C:/Review/Scratch"; rm -rf Data',
])
def test_directory_changes_respect_scope_and_later_absolute_cd(command):
    parsed = shellparse.parse(command, "bash")
    assert parsed.ok, parsed.why
    assert decide(command).decision == "allow"


def test_unknown_eval_location_is_unknown_for_later_commands():
    parsed = shellparse.parse('eval "$UNKNOWN"; rm -rf Data', "bash")
    assert parsed.ok, parsed.why
    assert next(e for e in parsed.execs if e.program == "rm").cd_before == "?"


def test_inline_substitution_before_a_syntax_error_keeps_its_delete_evidence():
    command = "eval 'cd Data'; echo `rm -rf x`; echo '"
    parsed = shellparse.parse(command, "bash")
    assert not parsed.ok
    verdict = shell_rules.decide(command, ROOTS, Audit(), cwd=ROOT, shell="bash", env={})
    assert verdict.decision == "deny", "a later syntax error erased the deferred substitution"


@pytest.mark.parametrize("command", [
    "eval 'cd C:/Review/Scratch' <<EOF\n$(rm -rf Data)\nEOF\n",
    "eval 'cd C:/Review/Game'; eval 'cd C:/Review/Scratch' <<EOF\n`rm -rf Data`\nEOF\n",
    "eval 'cd C:/Review/Scratch' <<EOF; echo `rm -rf Data`\n$(rm -rf Data)\nEOF\n",
])
def test_heredoc_substitutions_run_before_the_receiving_eval_changes_directory(command):
    parsed = shellparse.parse(command, "bash")
    assert parsed.ok, parsed.why
    verdict = shell_rules.decide(command, ROOTS, Audit(), cwd=ROOT, shell="bash", env={})
    assert verdict.decision == "deny", "heredoc expansion used the receiver's later directory"


def test_heredoc_expansion_uses_its_receiving_subshell_scope():
    command = "(eval 'cd C:/Review/Game'; eval 'cd C:/Review/Scratch' <<EOF\n$(rm -rf Data)\nEOF\n)"
    parsed = shellparse.parse(command, "bash")
    assert parsed.ok, parsed.why
    assert decide(command).decision == "deny"


@pytest.mark.parametrize("command", [
    "f() { eval 'cd C:/Review/Scratch'; }; rm -rf Data",
    "f () { eval 'cd C:/Review/Scratch'; }; rm -rf Data",
    "function f { eval 'cd C:/Review/Scratch'; }; rm -rf Data",
    "if false; then eval 'cd C:/Review/Scratch'; fi; rm -rf Data",
    "while false; do eval 'cd C:/Review/Scratch'; done; rm -rf Data",
    "for x in; do eval 'cd C:/Review/Scratch'; done; rm -rf Data",
    "case x in y) eval 'cd C:/Review/Scratch';; esac; rm -rf Data",
    "false && eval 'cd C:/Review/Scratch'; rm -rf Data",
    "true || eval 'cd C:/Review/Scratch'; rm -rf Data",
    "eval 'if false; then cd C:/Review/Scratch; fi'; rm -rf Data",
    "if false; then cd C:/Review/Other; fi; eval 'if false; then cd C:/Review/Scratch; fi'; rm -rf Data",
])
def test_unexecuted_control_body_cannot_move_a_later_protected_delete(command):
    verdict = shell_rules.decide(command, ROOTS, Audit(), cwd=ROOT, shell="bash", env={})
    assert verdict.decision == "deny"


@pytest.mark.parametrize("tail", ["(rm -rf Data)", "echo `rm -rf Data`", "echo $(rm -rf Data)"])
def test_control_location_alternatives_reach_nested_commands(tail):
    command = "if false; then eval 'cd C:/Review/Scratch'; fi; " + tail
    verdict = shell_rules.decide(command, ROOTS, Audit(), cwd=ROOT, shell="bash", env={})
    assert verdict.decision == "deny"


@pytest.mark.parametrize("command", [
    "{ eval 'cd C:/Review/Scratch'; }; rm -rf Data",
    "if false; then eval 'cd C:/Review/Game'; fi; cd C:/Review/Scratch; rm -rf Data",
    "for x in; do eval 'cd C:/Review/Game'; done; cd C:/Review/Scratch; rm -rf Data",
    "case x in y) eval 'cd C:/Review/Game';; esac; cd C:/Review/Scratch; rm -rf Data",
    "f() { eval 'cd C:/Review/Game'; }; { eval 'cd C:/Review/Scratch'; }; rm -rf Data",
    "f() (echo unused); { eval 'cd C:/Review/Scratch'; }; rm -rf Data",
])
def test_proved_brace_or_later_absolute_cd_resolves_location_alternatives(command):
    parsed = shellparse.parse(command, "bash")
    assert parsed.ok, parsed.why
    verdict = shell_rules.decide(command, ROOTS, Audit(), cwd=ROOT, shell="bash", env={})
    assert verdict.decision == "allow"


def test_location_alternatives_apply_to_the_bash_literal_backslash_reading():
    command = "if false; then eval 'cd C:/Outside'; fi; rm Game\\Data\\x"
    verdict = shell_rules.decide(command, ROOTS, Audit(), cwd="C:/Review", shell="bash", env={})
    assert verdict.decision == "deny"


def test_location_alternatives_are_bounded_and_overflow_refuses(monkeypatch):
    monkeypatch.setattr(shellparse, "MAX_LOCATION_STATES", 2)
    command = "if x; then cd /first; fi; if y; then cd /second; fi; rm harmless.txt"
    parsed = shellparse.parse(command, "bash")
    assert parsed.ok, parsed.why
    assert parsed.unread, "location overflow must refuse instead of choosing one path"
    assert len([e for e in parsed.execs if e.program == "rm"]) <= 2
    assert decide(command).decision == "deny"


def test_partial_parse_after_location_ambiguity_refuses(monkeypatch):
    monkeypatch.setattr(shellparse, "MAX_TOKENS", 30)
    command = "if x; then cd /first; fi; " + "echo x; " * 50
    parsed = shellparse.parse(command, "bash")
    assert not parsed.ok
    assert parsed.unread, "a partial result lost an alternative location"
    assert decide(command).decision == "deny"


def test_repeated_equal_location_alternatives_do_not_exhaust_the_bound():
    command = "if x; then cd /first; fi; " * 10 + "echo x"
    parsed = shellparse.parse(command, "bash")
    assert parsed.ok, parsed.why
    assert not parsed.unread
    assert decide(command).decision == "allow"


@pytest.mark.parametrize("arg", ["%OUT%", "!OUT!", "$OUT", "`pick`"])
def test_unproved_adapter_output_cannot_preserve_only_the_input(arg):
    calls = shell_rules.resaver_write_calls("resaver-cli.sh clean x.ess " + arg + " --apply", "cmd")
    assert calls and calls[0][1] == []


@pytest.mark.parametrize("arg", ["%FLAGS%", "!FLAGS!"])
def test_unparsed_native_fallback_keeps_unknown_flags_conservative(arg):
    command = "resaverpp-cli x.ess " + arg
    parsed = shellparse.Parsed((), False, "synthetic parse failure")
    assert shell_rules.resaver_native_direct(command, parsed) == "resaverpp-cli.exe"


@pytest.mark.parametrize("arg", ["%OP%", "!OP!"])
def test_adapter_dynamic_operation_keeps_the_text_fallback(arg):
    command = "resaver-cli.sh " + arg + " x.ess --apply; echo clean"
    assert shell_rules.resaver_target(command, "cmd") == ("clean", "x.ess")


def test_repeated_fed_construct_scope_is_read_once(monkeypatch):
    from skyguard import lift_rules

    original = lift_rules._text_hit
    reads = []

    def counted(text, *args):
        reads.append(text)
        return original(text, *args)

    monkeypatch.setattr(lift_rules, "_text_hit", counted)
    command = "$x = Get-Item C:/Review/Scratch/a;$x.IsReadOnly = $false;" * 8
    assert decide(command, "powershell").decision == "allow"
    assert len(reads) == 1, "identical construct/feeder scope was resolved repeatedly"


def test_repeated_construct_keeps_different_feeder_targets():
    command = ("$x = Get-Item C:/Review/Scratch/a; $x.IsReadOnly = $false; "
               "$x = Get-Item C:/Review/Game/Data/Skyrim.esm; $x.IsReadOnly = $false")
    assert decide(command, "powershell").decision == "deny"


def test_construct_scope_reads_are_local_to_each_decision():
    command = "$x = Get-Item C:/Review/Game/Data/Skyrim.esm; $x.IsReadOnly = $false"
    elsewhere = paths.Roots(paths.path_to_regex("C:/Review/Other"), "",
                            install_dirs=("c:/review/other",))
    assert shell_rules.decide(command, elsewhere, Audit(), cwd="C:/Review/Scratch",
                              shell="powershell", env={}).decision == "allow"
    assert decide(command, "powershell").decision == "deny"
