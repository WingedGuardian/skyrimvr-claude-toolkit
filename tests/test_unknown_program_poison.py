"""guard3 (coordinator follow-up to the v4.0.0 review): a program the guard cannot name may be `read`, `declare` or
`eval` and set any variable without spelling an assignment, so after it no variable resolves to its earlier literal.
The variable-setting builtins already turn resolution off (assign.poisoned); these rows prove each of them end to
end, on a variable later used as a delete operand in a command that names the install.

Through run.py in process (record.fire_inprocess) on a scratch install; nothing is executed."""
import importlib.util
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))


def _rec():
    spec = importlib.util.spec_from_file_location("record", REPO / "scripts" / "guard-corpus" / "record.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def inst(tmp_path_factory):
    rec = _rec()
    return rec, rec.make_install(tmp_path_factory.mktemp("unk"), REPO / ".claude" / "hooks", "declared")


def fire(inst, command):
    rec, install = inst
    I = install.as_posix()
    return rec.fire_inprocess(install, "protect-bash.sh", {"tool_name": "Bash", "session_id": "s", "cwd": "C:/work",
                                                           "tool_input": {"command": command.replace("<I>", I)}})


TAIL = '; rm -rf "$D"; ls "<I>"'
UNRESOLVED = {
    # an unnamed program word, then a use of an earlier literal
    "unknown-program-reads-a-file": 'D=/tmp/safe; $R D < /tmp/f' + TAIL,
    "substitution-program": 'D=/tmp/safe; $(pick) D < /tmp/f' + TAIL,
    "proven-program-is-read": 'D=/tmp/safe; R=read; $R D < /tmp/f' + TAIL,
    "proven-program-is-declare": 'D=/tmp/safe; R=declare; $R D=x' + TAIL,
    # (printf is no write-counting keyword, so only the program-word rule can catch these two)
    "empty-program-then-printf-v": 'D=/tmp/safe; R=""; $R printf -v D x' + TAIL,
    "proven-program-is-printf": 'D=/tmp/safe; R=printf; $R -v D x' + TAIL,
    # the variable-setting builtins, spelled out
    "read": 'D=/tmp/safe; read D < /tmp/f' + TAIL,
    "mapfile": 'D=/tmp/safe; mapfile -t D < /tmp/f' + TAIL,
    "readarray": 'D=/tmp/safe; readarray -t D < /tmp/f' + TAIL,
    "printf-v": "D=/tmp/safe; printf -v D '%s' x" + TAIL,
    "declare-non-literal": 'D=/tmp/safe; declare D="$X"' + TAIL,
    "typeset-non-literal": 'D=/tmp/safe; typeset D="$X"' + TAIL,
    "local-non-literal": 'D=/tmp/safe; local D="$X"' + TAIL,
    "export-non-literal": 'D=/tmp/safe; export D="$X"' + TAIL,
    "getopts": 'D=/tmp/safe; getopts ab D' + TAIL,
}


@pytest.mark.parametrize("case", sorted(UNRESOLVED))
def test_after_a_possible_variable_setter_the_operand_stays_unresolved(inst, case):
    got = fire(inst, UNRESOLVED[case])
    assert got["decision"] == "deny" and got["rules"] == {"S-DEL-UNRESOLVED"}, got


RESOLVED = {
    "use-before-the-unknown-program": 'D=/tmp/safe; rm -rf "$D"; $R x; ls "<I>"',
    "proven-ordinary-program": 'D=/tmp/safe; R=ls; $R x' + TAIL,
    "proven-program-with-arguments": 'D=/tmp/safe; R="ls -la"; $R x' + TAIL,
    "proven-child-wrapper": 'D=/tmp/safe; S=sudo; $S ls' + TAIL,
}


@pytest.mark.parametrize("case", sorted(RESOLVED))
def test_a_literal_still_resolves_where_nothing_could_have_changed_it(inst, case):
    got = fire(inst, RESOLVED[case])
    assert not any(r.startswith("S-DEL") for r in got["rules"]), got
