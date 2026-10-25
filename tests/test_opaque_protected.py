"""User ruling 2026-10-20 (v4.0.0 review L1 MEDIUM-1/2, the LOWs and the INFO): when the guard cannot name the program
that will run AND the command names a path the other rules protect, the user is asked (S-OPAQUE-PROTECTED). Saves keep
S-SAVE-OPAQUE. Ordinary commands, and an unnamed program on anything else, stay as they were.

Through run.py in process (record.fire_inprocess) on a scratch install; nothing is executed."""
import importlib.util
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))

BS = chr(92)


def _rec():
    spec = importlib.util.spec_from_file_location("record", REPO / "scripts" / "guard-corpus" / "record.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def inst(tmp_path_factory):
    rec = _rec()
    return rec, rec.make_install(tmp_path_factory.mktemp("op"), REPO / ".claude" / "hooks", "saves")


def fire(inst, command, tool="Bash", shell=None, cwd=None):
    rec, install = inst
    I = install.as_posix()
    command = command.replace("<I>", I).replace("<IB>", I.replace("/", BS))
    tool_input = {"command": command}
    if shell:
        tool_input["shell"] = shell
    return rec.fire_inprocess(install, "protect-bash.sh", {"tool_name": tool, "session_id": "s",
                                                           "cwd": (cwd or "<I>").replace("<I>", I),
                                                           "tool_input": tool_input})


ASKS = {
    "var-holds-command-line": ('R="rm -rf"; $R "<I>/Data"', "Bash"),
    # guard3: a value assigned in a branch is not proven, so the eval-like call runs text the guard cannot read.
    "eval-of-variable": ('if [ -n "$X" ]; then c="rm -rf <I>/Data"; fi; eval "$c"', "Bash"),
    "bash-c-of-variable": ('if [ -n "$X" ]; then c="rm -rf <I>/Data"; fi; bash -c "$c"', "Bash"),
    "indirect-expansion": ('R=rm; ${!R} -rf "<I>/Data"', "Bash"),
    "array-program": ('A=(rm -rf); "${A[@]}" "<I>/Data"', "Bash"),
    "printf-v": ('printf -v R rm; $R -rf "<I>/Data"', "Bash"),
    "read-variable": ('read R <<< rm; $R -rf "<I>/Data"', "Bash"),
    "awk-system": ("awk 'BEGIN{system(\"rm -rf <I>/Data\")}'", "Bash"),
    "cmd-percent-var": ('cmd /c "set R=del&& %R% /s /q <IB>' + BS + 'Data"', "Bash"),
    "cmd-delayed-var": ('cmd /v:on /c "set R=del&& !R! /s /q <IB>' + BS + 'Data"', "Bash"),
    "pwsh-iex-variable": ("if ($x) { $c = \"Remove-Item -Recurse '<IB>" + BS + "Data'\" }; iex $c", "PowerShell"),
    "pwsh-get-command-call": ("& (Get-Command Remove-Item) -Recurse '<IB>" + BS + "Data'", "PowerShell"),
    "pwsh-here-string-iex": ("$s = @'\nRemove-Item -Recurse '<IB>" + BS + "Data'\n'@\nInvoke-Expression $s",
                             "PowerShell"),
    "opaque-on-data-no-verb": ('$R "<I>/Data"', "Bash"),
    "opaque-on-install-no-verb": ('$R "<I>"', "Bash"),
    "opaque-on-config-no-verb": ('$R "<I>/cfg/Skyrim.ini"', "Bash"),
    "opaque-relative-plugin-no-verb": ('$R custom.esp', "Bash"),
    "opaque-redirect-no-verb": ('$R > "<I>/Data/result.txt"', "Bash"),
    "opaque-on-a-plugin": ('$R "<I>/Data/Skyrim.esm"', "Bash"),
}


@pytest.mark.parametrize("case", sorted(ASKS))
def test_an_unnamed_program_on_a_protected_path_asks(inst, case):
    command, tool = ASKS[case]
    got = fire(inst, command, tool)
    assert got["decision"] == "ask" and "S-OPAQUE-PROTECTED" in got["rules"], got
    assert len(got["text"].split("\n")) <= 2 and "rm -rf" not in got["text"] and "Remove-Item" not in got["text"]


STAYS = {
    "here-string-data-only": ("$s = @'\nRemove-Item -Recurse '<IB>" + BS + "Data'\n'@\nWrite-Output $s", "PowerShell"),
    "heredoc-to-a-file": ("cat <<EOF > /tmp/x.txt\nrm -rf <I>/Data\nEOF", "Bash"),
    "echo-mentions-it": ('echo "do not run rm -rf <I>/Data"', "Bash"),
    "opaque-on-a-tool-script": ('"$PY" tools/x.py --out notes.txt', "Bash"),
    "opaque-on-scratch": ('$EDITOR /tmp/notes.txt', "Bash"),
    "named-program-reads-data": ('ls "<I>/Data"', "Bash"),
    "eval-of-literal-elsewhere": ('eval "echo hi"', "Bash"),
    # guard3 precision (v4.0.0 review LOW-5, user-approved): a same-command literal names what runs.
    "program-path-with-spaces": ('CAP="<I>/tools/Caprica/Caprica.exe"; "$CAP" -f "TESV_Papyrus_Flags.flg" '
                                 '"<I>/Data/Scripts/Source/x.psc"', "Bash"),
    "eval-of-a-harmless-literal": ('c="echo hi"; eval "$c"', "Bash"),
}


@pytest.mark.parametrize("case", sorted(STAYS))
def test_ordinary_commands_and_unprotected_targets_do_not_ask(inst, case):
    command, tool = STAYS[case]
    got = fire(inst, command, tool)
    assert "S-OPAQUE-PROTECTED" not in got["rules"] and got["decision"] in ("allow", "advise"), got


def test_a_save_keeps_its_own_ask(inst):
    got = fire(inst, '$R "<I>/cfg/Saves/Existing.ess"')
    assert got["decision"] == "ask" and "S-SAVE-OPAQUE" in got["rules"] and "S-OPAQUE-PROTECTED" not in got["rules"]


def test_option_and_bare_words_are_not_paths_even_inside_data(inst):
    """Priced: with the cwd inside Data, `list`, `-r`, `l` are not Data files the unnamed program is given."""
    got = fire(inst, '"$TOOL" list -r l', cwd="<I>/Data")
    assert "S-OPAQUE-PROTECTED" not in got["rules"] and got["decision"] in ("allow", "advise"), got


def test_entering_the_install_folder_does_not_ask(inst):
    """Priced: `cd <install> && "$PY" ...` spells the install root itself, which is the toolkit's own folder."""
    got = fire(inst, 'cd "<I>" && "$PY" -c "print(1)"')
    assert "S-OPAQUE-PROTECTED" not in got["rules"] and got["decision"] in ("allow", "advise"), got


def test_changed_opaque_corpus_rows_match_their_recorded_contract(tmp_path):
    import json
    rec = _rec()
    install = rec.make_install(tmp_path, REPO / ".claude/hooks", "declared")
    rows = [json.loads(line) for line in (REPO / "tests/guard_corpus/intended.jsonl").read_text(encoding="utf-8").splitlines()]
    rows = [row for row in rows if row.get("task") == "OPAQUE-PROTECTED" or row["id"] == "t07-opaque-elsewhere"]
    assert len(rows) == 17
    failures = []
    for row in rows:
        got = rec.fire_full_inprocess(install, row)
        failures.extend(row["id"] + ": " + problem for problem in rec.intended_problems(row, got))
    assert not failures, "\n".join(failures)


# guard3 precision (v4.0.0 review LOW-5, user-approved direction): a variable assigned ONE literal, unconditionally, in
# the same command is read as that literal when it is the program word or the text an eval-like call runs. The eval is
# then the command it runs, and the delete rule refuses it. Anything less stays unreadable (the asks above).
LITERALS = {
    "eval-literal": ("c='rm -rf \"<I>/Data\"'; eval \"$c\"", "Bash"),
    "bash-c-literal": ("c='rm -rf \"<I>/Data\"'; bash -c \"$c\"", "Bash"),
    "pwsh-iex-literal": ("$c = \"Remove-Item -Recurse '<IB>" + BS + "Data'\"; iex $c", "PowerShell"),
    "pwsh-iex-command-literal": ("$c = \"Remove-Item -Recurse '<IB>" + BS + "Data'\"; Invoke-Expression -Command $c",
                                 "PowerShell"),
    "pwsh-pipe-iex-literal": ("$c = \"Remove-Item -Recurse '<IB>" + BS + "Data'\"; $c | iex", "PowerShell"),
}


@pytest.mark.parametrize("case", sorted(LITERALS))
def test_an_eval_of_a_same_command_literal_is_the_command_it_runs(inst, case):
    command, tool = LITERALS[case]
    got = fire(inst, command, tool)
    assert got["decision"] == "deny" and got["rules"] == {"S-DEL-INSTALL"}, got


# The eval of a literal lifts no resolution it could falsify: an eval the guard cannot prove still turns every
# variable in the command unresolved, as an eval always did. /tmp/safe here would otherwise be read as the target.
POISONED = {
    "value-unproven": ('D=/tmp/safe; if [ -n "$X" ]; then k="echo hi"; fi; eval "$k"; rm -rf "$D"; ls "<I>"', "deny"),
    "value-expands": ("D=/tmp/safe; k='$R D'; eval \"$k\"; rm -rf \"$D\"; ls \"<I>\"", "deny"),
    # `source` may set D without spelling it (a `read D` would already count D as written twice).
    "value-poisons": ("D=/tmp/safe; k='source /tmp/x.sh'; eval \"$k\"; rm -rf \"$D\"; ls \"<I>\"", "deny"),
    "eval-not-a-plain-statement": ('D=/tmp/safe; X=1 eval "$k"; rm -rf "$D"; ls "<I>"', "deny"),
    "value-writes-the-variable": ("D=/tmp/safe; k='D=\"<I>/Data\"'; eval \"$k\"; rm -rf \"$D\"", "deny"),
    "cd-before-the-eval-settles": ('D=/tmp/safe; X=1 eval "$k"; cd "$D" && rm -rf Data', "advise"),
}


@pytest.mark.parametrize("case", sorted(POISONED))
def test_an_eval_the_guard_cannot_prove_still_turns_resolution_off(inst, case):
    command, decision = POISONED[case]
    got = fire(inst, command)
    rule = "S-RELATIVE-UNKNOWN" if decision == "advise" else "S-DEL-UNRESOLVED"
    assert got["decision"] == decision and rule in got["rules"], got


def test_a_resolved_eval_frees_the_rest_of_the_command(inst):
    """The point of the precision: `k="echo hi"; eval "$k"` no longer hides /tmp/safe behind an eval poison."""
    got = fire(inst, 'D=/tmp/safe; k="echo hi"; eval "$k"; rm -rf "$D"; ls "<I>"')
    assert got["decision"] == "allow", got
