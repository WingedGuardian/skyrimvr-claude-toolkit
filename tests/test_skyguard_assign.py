"""Same-command literal assignments (Plan 5 Task 7b): `D=/tmp/x; rm -rf "$D"` resolves; anything less than certain
stays unresolved (the deny it was).

Three halves:
  - every hand-authored t07b intended row fired IN-PROCESS, parametrized by row id so a mutation can name the one
    row it must turn red (the corpus test fires the same rows through the real wrapper, as one test);
  - assign.py, one behaviour per test: what counts as a write, what is a literal value, how a use expands;
  - the parser's flow, one clause per test: which assignment reaches which use (Exec.subst).
"""
import json
import os
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import anchored, assign, audit, deletes, paths, shell_rules, shellparse  # noqa: E402

GAME = "C:/GOG Games/The Elder Scrolls V Skyrim VR"
INSTALL = "C:/t7/Skyrim VR"
SHELL = {"Bash": "bash", "PowerShell": "powershell"}
BS = chr(92)


def _rows():
    text = (REPO / "tests" / "guard_corpus" / "intended.jsonl").read_text(encoding="utf-8")
    return [r for r in (json.loads(x) for x in text.split("\n") if x.strip()) if r["task"] == "T7b"]


ROWS = _rows()


def decide(command, cwd="", shell="bash"):
    r = paths.roots(INSTALL, "", {"SKYRIM_GAME_ROOT": GAME})
    a = audit.Audit("")
    a.raw = lambda line: None
    v = shell_rules.decide(command, r, a, cwd=cwd, shell=shell, env=os.environ)
    if v.decision == "allow":
        got = ("advise", v.context) if v.context else ("allow", "")
    else:
        got = (v.decision, v.reason)
    return got, {rid for rid, _ in a.fired}


def test_there_are_t07b_rows():
    assert len(ROWS) >= 50, len(ROWS)


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_t07b_row(row):
    p = row["payload"]
    fill = (lambda s: s.replace("<install>", INSTALL)) if row.get("payload_note") == "install-relative" else (lambda s: s)
    got, fired = decide(fill(p["tool_input"]["command"]), fill(p.get("cwd", "")), SHELL[p["tool_name"]])
    assert got == (row["decision"], row["text"])
    assert fired == set(row["rules"])


# ------------------------------------------------------------------------------------------- assign.write_counts
@pytest.mark.parametrize("text,name,n", [
    ("D=/x", "d", 1),
    ("D=/x; D=/y", "d", 2),
    ("D=/x; d=/y", "d", 2),                               # case-folded: over-counting is safe
    ("D+=/x", "d", 1),
    ("D[0]=x", "d", 1),
    ("(( D = 1 ))", "d", 1),
    ("echo $D=x", "d", 1),                               # a read in bash, PowerShell's form: over-counted
    ("$p = 'x'", "p", 1),                                # ... and PowerShell's assignment form
    ("$script:p = 'x'", "p", 1),
    ("${p} += 1", "p", 1),
    (": ${D:=x}", "d", 1),
    (": ${D=x}", "d", 1),
    (": ${D:-x}", "d", 0),                               # a default that does not assign
    ("((D++))", "d", 1),
    ("((--D))", "d", 1),
    ("for D in a b; do :; done", "d", 1),
    ('for f in "$D"/*; do :; done', "d", 0),             # bash: $D in a for list is a read
    ("read -r D < f", "d", 1),
    ("export D", "d", 1),
    ("unset D", "d", 1),
    ("local D", "d", 1),
    ("declare -n R=D", "d", 1),
    ("printf -v D '%s' x", "d", 1),
    ("printf '%s' D", "d", 0),                           # printf without -v writes nothing
    ("wait -n -p D", "d", 1),
    ("foreach ($p in $a) { }", "p", 1),
    ("Set-Variable -Name p -Value x", "p", 1),
    ("Get-Item x -OutVariable p", "p", 1),
    ("function f($p) { }", "p", 1),
    ("exec {D}>/dev/null", "d", 1),                      # fd allocation stores a number in D
    ("Get-Item x | Tee-Object -Variable p", "p", 1),
    ("set p x", "p", 1),                                 # PowerShell's `set` is Set-Variable
])
def test_write_counts(text, name, n):
    """n = 0: never counted as a write; n >= 1: counted at least n times (over-counting is safe, under is not)."""
    got = assign.write_counts(text).get(name, 0)
    assert got == 0 if n == 0 else got >= n


# ------------------------------------------------------------------------------------------- assign values
@pytest.mark.parametrize("raw,want", [
    ("/tmp/x", "/tmp/x"),
    ("'/tmp/a b'", "/tmp/a b"),
    ('"/tmp/a b"', "/tmp/a b"),
    ('"C:' + BS + 'Users' + BS + 'x"', "C:" + BS + "Users" + BS + "x"),   # a dq backslash before a letter stays
    ('"a' + BS + '"b"', 'a"b'),
    ("/tmp/*", "/tmp/*"),                                 # no globbing in an assignment
    ("", ""),
    ("$X/y", None),
    ('"$X/y"', None),
    ("$(mktemp)", None),
    ("`mktemp`", None),
    ("~/x", None),                                        # tilde expansion
    ("C:" + BS + "x", None),                              # unquoted backslash: bash would store C:x
    ("$'a'", None),
    ("(a b)", None),
    ("'unterminated", None),
])
def test_bash_value(raw, want):
    assert assign.bash_value(raw) == want


@pytest.mark.parametrize("seg,want", [
    ("$p = 'C:" + BS + "x'", ("p", "C:" + BS + "x")),
    ("$p='it''s'", ("p", "it's")),
    ('$p = "C:' + BS + 'x"', ("p", "C:" + BS + "x")),
    ('$p = "C:' + BS + '$x"', None),
    ('$p = "a`nb"', None),
    ("$p = Get-Item x", None),
    ("$p = 'a' + 'b'", None),
    ("[string]$p = 'x'", None),
    ("$env:p = 'x'", None),
])
def test_ps_assignment(seg, want):
    assert assign.ps_assignment(seg) == want


def _look(d):
    return lambda name: d.get(name)


@pytest.mark.parametrize("raw,want", [
    ('"$D"', "/t"),
    ("$D/x", "/t/x"),
    ("${D}y", "/ty"),
    ('"$D"/"x y"', "/t/x y"),
    ("'$D'", "$D"),                                       # single quotes: literal
    ("$E", None),                                         # not assigned
    ("$1", None),
    ('"${D:-x}"', None),
    ('"$(echo)"', None),
    ("~/$D", None),
    ("$S", None),                                         # unquoted, a value with a space would split
    ('"$S"', "/a b"),
    ("$G", None),                                         # unquoted, a glob value would glob
    ("$Z", None),                                         # unquoted, an empty value would vanish
    (BS + "$D", None),                                    # an unquoted backslash
])
def test_expand_bash_word(raw, want):
    assert assign.expand_word(raw, "bash", _look({"D": "/t", "S": "/a b", "G": "/a*", "Z": ""})) == want


@pytest.mark.parametrize("raw,want", [
    ("$p", "C:" + BS + "t"),
    ('"$p' + BS + 'x"', "C:" + BS + "t" + BS + "x"),
    ("'$p'", "$p"),
    ("$env:p", None),
    ('"$p:x"', None),                                     # a drive-qualified name
    ('"$($p)"', None),
    ("$p`n", None),
    ("@p", None),
])
def test_expand_ps_word(raw, want):
    assert assign.expand_word(raw, "powershell", _look({"p": "C:" + BS + "t"})) == want


def test_cmd_never_expands():
    assert assign.expand_word("%D%", "cmd", _look({"D": "x"})) is None


# ------------------------------------------------------------------------------------------- the parser's flow
def _subst(cmd, shell="bash", program="rm"):
    for e in shellparse.parse(cmd, shell).execs:
        if e.program == program:
            return dict(e.subst)
    raise AssertionError("no " + program)


@pytest.mark.parametrize("cmd,resolved", [
    ('D=/x; rm "$D"', True),
    ('D=/x\nrm "$D"', True),
    ('x && D=/x && rm "$D"', True),
    ('D=/x && x; rm "$D"', True),                         # first in its list: it always ran
    ('D=/x || x; rm "$D"', True),
    ('D=/x; if y; then rm "$D"; fi', True),
    ('D=/x; f() { rm "$D"; }; f', False),            # fix round 1: a function definition poisons
    ('D=/x; echo "$(rm "$D")"', True),
    ('rm "$D"; D=/x', False),
    ('rm "$D" && D=/x', False),
    ('x && D=/x; rm "$D"', False),
    ('x || D=/x; rm "$D"', False),
    ('x || D=/x && rm "$D"', False),
    ('D=/x || rm "$D"', False),
    ('D=/x & rm "$D"', False),
    ('D=/x | y; rm "$D"', False),
    ('y | D=/x; rm "$D"', False),
    ('if y; then D=/x; fi; rm "$D"', False),
    ('while y; do D=/x; done; rm "$D"', False),
    ('until y; do D=/x; done; rm "$D"', False),
    ('for i in 1; do D=/x; done; rm "$D"', False),
    ('select i in 1; do D=/x; done; rm "$D"', False),
    ('case y in y) D=/x;; esac; rm "$D"', False),
    ('{ D=/x; }; rm "$D"', False),
    ('( D=/x ); rm "$D"', False),
    ('f() { rm "$D"; }; D=/x; f', False),                  # the body was read before the assignment
    ('D=/x y; rm "$D"', False),
    ('D+=/x; rm "$D"', False),
    ('D=/x; source s; rm "$D"', False),
    ('D=/x; . s; rm "$D"', False),
    ('D=/x; eval y; rm "$D"', False),
    ("bash -c 'D=/x; rm \"$D\"'", False),
    ('D=/x; D=/x; rm "$D"', False),
    # a closed compound statement ends its control depth: the next top-level assignment counts again
    ('if y; then :; fi; D=/x; rm "$D"', True),
    ('while y; do :; done; D=/x; rm "$D"', True),
    ('{ :; }; D=/x; rm "$D"', True),
    # a separator INSIDE [[ ]] does not end the top-level list: `D` is still after a `||`
    ('false || [[ -n y\n ]] && D=/x; rm "$D"', False),
    # the same && position in a DIFFERENT list is not the same chain
    ('x && D=/x & y && z && rm "$D"', False),
])
def test_bash_flow(cmd, resolved):
    assert (_subst(cmd).get("$D") == "/x") is resolved


@pytest.mark.parametrize("cmd,resolved", [
    ("$p = 'C:" + BS + "x'; Remove-Item $p", True),
    ("$p = 'C:" + BS + "x'\nRemove-Item $p", True),
    ("$p = 'C:" + BS + "x'; if ($y) { Remove-Item $p }", True),
    ("$p = 'C:" + BS + "x' && Remove-Item $p", True),
    ("Remove-Item $p; $p = 'C:" + BS + "x'", False),
    ("if ($y) { $p = 'C:" + BS + "x' }; Remove-Item $p", False),
    ("y || $p = 'C:" + BS + "x'; Remove-Item $p", False),
    ("$p = 'C:" + BS + "x' | y; Remove-Item $p", False),
    ("$p = 'C:" + BS + "x'; . .\\s.ps1; Remove-Item $p", False),
    ("$p = 'C:" + BS + "x'; iex $c; Remove-Item $p", False),
    ("$p = 'C:" + BS + "x'; Invoke-Expression $c; Remove-Item $p", False),
    ("& { $p = 'C:" + BS + "x' }; Remove-Item $p", False),
])
def test_powershell_flow(cmd, resolved):
    assert (_subst(cmd, "powershell", "remove-item").get("$p") == "C:" + BS + "x") is resolved


def test_two_readings_of_one_word_value_are_not_substituted():
    assert "$D" not in _subst("D=/x; rm '$D' \"$D\"")


def test_unknown_dialect_and_cmd_never_resolve():
    for e in shellparse.parse("set D=C:" + BS + "x & rd /s /q %D%", "cmd").execs:
        assert e.subst == ()
    assert decide('cmd /c "set D=C:' + BS + 'Temp & rd /s /q %D%"; ls "' + GAME + '"')[1] == {"S-DEL-UNRESOLVED"}


def test_substituted_replaces_only_mapped_words():
    e = shellparse.parse('D=/x; rm -rf "$D" "$E"', "bash").execs[0]
    assert deletes.substituted(e).argv == ("-rf", "/x", "$E")
    assert deletes.substituted(e._replace(subst=())) == e._replace(subst=())


# ------------------------------------------------------------------------------------------- registry: one resolver
def test_registry_uses_the_exec_substitution():
    key = "HKLM" + BS + "SOFTWARE" + BS + "Bethesda Softworks"
    assert anchored.bethesda_key("$K", {"$K": key}) is True
    assert anchored.bethesda_key("$K", {"$K": "HKCU" + BS + "Software" + BS + "X"}) is False
    assert anchored.bethesda_key("$K", {}) is True                       # unknown: not safe by default
    assert anchored.bethesda_key("$K", {"$K": "$OTHER"}) is True          # a literal holding a `$`


# ------------------------------------------------------------------------------------------- single-purpose tests a
# mutation in tests/test_mutations.py names (a parametrized id with brackets or quotes cannot be named reliably)
def test_flow_dbr_separator_keeps_the_list():
    assert "$D" not in _subst('false || [[ -n y\n ]] && D=/x; rm "$D"')


def test_flow_cross_list_same_position():
    assert "$D" not in _subst('x && D=/x & y && z && rm "$D"')


def test_flow_use_before_in_same_chain():
    assert "$D" not in _subst('rm "$D" && D=/x')


@pytest.mark.parametrize("opener", ["if y; then :; fi", "while y; do :; done", "{ :; }"])
def test_flow_closers_reset_control_depth(opener):
    assert _subst(opener + '; D=/x; rm "$D"').get("$D") == "/x"


def test_increment_is_a_write():
    assert assign.write_counts("((D++))").get("d", 0) >= 1
    assert assign.write_counts("((--D))").get("d", 0) >= 1


def test_for_list_dollar_is_a_read():
    assert assign.write_counts('for f in "$SP"/* "${SP}"; do :; done').get("sp", 0) == 0


def test_plain_assignment_is_counted():
    # without this form the recorded assignment itself reads as unwritten (count 0) and nothing ever resolves
    assert assign.write_counts("D=/x; if y; then D=/z; fi").get("d") == 2


def test_dollar_assignment_is_counted():
    assert assign.write_counts("$p = 'x'; if ($y) { $p = 'z' }").get("p") == 2


# ------------------------------------------------------------------------------------------- fix round 1: poisoning
# Broad, conservative: any of these anywhere in the command turns ALL resolution off (controller ruling). One case
# per poison class, with an explicit id so a mutation can name it.
NL = chr(10)
POISON = [
    ("function-parens", "f() { :; }", "bash", "function"),
    ("function-keyword", "function f { :; }", "bash", "function"),
    ("default-assign", ": ${E:=y}", "bash", "default-assign"),
    ("array-element", "A[0]=x", "bash", "array"),
    ("array-list", "A=(a b)", "bash", "array"),
    ("indirect-name", "$N=x", "bash", "indirect"),
    ("arithmetic", "(( E = 1 ))", "bash", "arithmetic"),
    ("dot", ". ./e.sh", "bash", "dot-source"),
    ("dot-builtin-options", "builtin -- . ./e.sh", "bash", "dot-source"),
    ("dot-command-p", "command -p . ./e.sh", "bash", "dot-source"),
    ("dot-after-then", "if y; then . ./e.sh; fi", "bash", "dot-source"),
    ("trap", "trap 'x' DEBUG", "bash", "trap"),
    ("eval", "eval x", "bash", "eval"),
    ("source", "source ./e.sh", "bash", "source"),
    ("source-quoted", '"source" ./e.sh', "bash", "source"),
    ("source-split-quotes", "so''urce ./e.sh", "bash", "source"),
    ("source-backslash", BS + "source ./e.sh", "bash", "source"),
    ("read", "read -r E < f", "bash", "read"),
    ("read-continuation", "rea" + BS + NL + "d E < f", "bash", "read"),
    ("read-after-assignment", "IFS=/ read -r E < f", "bash", "read"),
    ("readarray", "readarray E < f", "bash", "readarray"),
    ("mapfile", "mapfile E < f", "bash", "mapfile"),
    ("getopts", "getopts ab E", "bash", "getopts"),
    ("declare", "declare E=x", "bash", "declare"),
    ("typeset", "typeset E=x", "bash", "typeset"),
    ("local", "local E=x", "bash", "local"),
    ("readonly", "readonly E=x", "bash", "readonly"),
    ("unset", "unset E", "bash", "unset"),
    ("let", "let E=1", "bash", "let"),
    ("export-dollar", 'export "$N"', "bash", "export"),
    ("printf-v", "printf -v E %s x", "bash", "printf-v"),
    ("printf-v-attached", "printf -vE %s x", "bash", "printf-v"),
    ("wait-p", "wait -n -p E", "bash", "wait-p"),
    ("ps-set-variable", "Set-Variable -Name $n -Value x", "powershell", "variable-cmdlet"),
    ("ps-sv", "sv $n x", "powershell", "variable-cmdlet"),
    ("ps-gv-value", "(gv d).Value = 1", "powershell", "variable-cmdlet"),
    ("ps-variable-drive", "Set-Item variable:d x", "powershell", "powershell-scope"),
    ("ps-ref", "$r = [ref]$d", "powershell", "powershell-scope"),
    ("ps-value-assign", "$x.Value = 1", "powershell", "powershell-scope"),
    ("ps-psvariable", "$ExecutionContext.SessionState.PSVariable.Set('d', 1)", "powershell", "powershell-scope"),
    ("ps-invokescript", "$ExecutionContext.InvokeCommand.InvokeScript('x')", "powershell", "powershell-scope"),
    ("ps-iex", "iex $c", "powershell", "powershell-scope"),
    ("ps-scope", "$script:d = 1", "powershell", "powershell-scope"),
    ("ps-call-variable", "& $sb", "powershell", "powershell-scope"),
    ("ps-call-block", ". { $d = 1 }", "powershell", "powershell-scope"),
    ("ps-block-assigned", "$sb = { $d = 1 }", "powershell", "powershell-scope"),
    ("ps-quoted-call", "& 'C:" + BS + "x.ps1'", "powershell", "quoted-call"),
    ("ps-outvariable-prefix", "Get-Item x -outv d", "powershell", "variable-parameter"),
    ("ps-ov-alias", "Get-Item x -ov d", "powershell", "variable-parameter"),
    ("ps-pipelinevariable", "Get-Item x -PipelineVar d", "powershell", "variable-parameter"),
    ("ps-dot-source", ". ./s.ps1", "powershell", "dot-source"),
    ("cmd-never", "set D=x", "cmd", "cmd"),
]
CLEAN = [
    ("bash-plain", 'D=/tmp/x; rm -rf "$D"; find . -name x; ls .', "bash"),
    ("bash-export-static", "export PATH=/x; D=/tmp/x", "bash"),
    ("bash-printf", "printf '%s' x", "bash"),
    ("bash-time-not", "time D=/x; ! D=/y", "bash"),
    ("ps-plain", "$p = 'C:" + BS + "x'; Remove-Item -Path $p -Recurse -Force -ErrorAction Stop -Include *.x",
     "powershell"),
    ("ps-if-block", "if (Test-Path $p) { Remove-Item $p }", "powershell"),
]


@pytest.mark.parametrize("text,dialect,want", [p[1:] for p in POISON], ids=[p[0] for p in POISON])
def test_poison_class(text, dialect, want):
    assert assign.poisoned(text, dialect) == want


@pytest.mark.parametrize("text,dialect", [c[1:] for c in CLEAN], ids=[c[0] for c in CLEAN])
def test_not_poisoned(text, dialect):
    assert assign.poisoned(text, dialect) == ""


def test_poison_turns_off_every_variable_in_the_command():
    assert _subst('D=/x; E=/y; rm "$D" "$E"') == {"$D": "/x", "$E": "/y"}
    assert _subst('D=/x; E=/y; rm "$D" "$E"; read -r Z < f') == {}


def test_a_stray_closer_turns_resolution_off():
    assert _subst('fi; if y; then D=/x; fi; rm "$D"') == {}
    assert _subst('}; D=/x; rm "$D"') == {}
