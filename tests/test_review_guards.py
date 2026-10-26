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
