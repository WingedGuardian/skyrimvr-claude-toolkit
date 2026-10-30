"""Fix-delta review D (7ae4502..b7acc9b) and two shapes found while fixing it: parser input only, never executed."""
import pytest
from test_review_guards import ROOTS, Audit, shell_rules, shellparse

G, S = "C:/Review/Game", "C:/Review/Scratch"


def decide(command, cwd):
    return shell_rules.decide(command, ROOTS, Audit(), cwd=cwd, shell="bash", env={}).decision


# Each id names the finding it reproduces; every one of these was ALLOWED by b7acc9b (P1, P2, P4, P5: and by 7ae4502).
DENY = [
    ("H1-call", S, 'f() { cd "C:/Review/Game"; }; f; rm -rf Data'),
    ("H1-keyword", S, 'function f { cd "C:/Review/Game"; }; f; rm -rf Data'),
    ("H1-argument", S, 'g() { cd "$1"; }; g "C:/Review/Game"; rm -rf Data'),
    # the argument alone carries the location (the text names no root): only the substitution can deny it
    ("H1-relative-argument", "C:/Review", 'g() { cd "$1"; }; g Game; rm -rf Data'),
    ("P3-body-at-call", S, 'f() { rm -rf Data; }; cd C:/Review/Game; f'),
    ("H2-amp-redirect", S, '{ cd "C:/Review/Game"; } &>/dev/null; rm -rf Data'),
    ("H2-amp-append", S, '{ cd "C:/Review/Game"; } &>>log; rm -rf Data'),
    ("H3-eval-wildcard", S, 'eval "$X"; rm -rf *'),
    ("H3-eval-clean", G, 'cd /tmp; eval "$X"; git clean -fdx'),
    ("M1-redirect-then-pipe", G, '{ cd /tmp; } 2>&1 | cat; rm -rf Data'),
    ("M2-brace-or", G, '{ cd /tmp; } || true; rm -rf Data'),
    ("M2-and-or", G, 'cd /tmp && ls || true; rm -rf Data'),
    ("P1-chdir-equals", S, 'env --chdir=C:/Review/Game rm -rf Data'),
    ("P1-pwsh-prefix", S, 'pwsh -wo C:/Review/Game -c "Remove-Item -Recurse Data"'),
    ("P1-pwsh-colon", S, 'pwsh -WorkingDirectory:C:/Review/Game -c "Remove-Item -Recurse Data"'),
    ("P2-printf", G, "printf 'rm -rf Data' | bash"),
    ("P2-subshell-reader", S, "echo 'rm -rf C:/Review/Game/Data' | (bash)"),
    ("P2-exec-reader", S, "echo 'rm -rf C:/Review/Game/Data' | (exec sh)"),
    ("P2-cd-then-reader", S, "echo 'rm -rf Data' | (cd C:/Review/Game && sh)"),
    ("P2-brace-reader", S, "echo 'rm -rf Data' | { cd C:/Review/Game; sh; }"),
    ("P4-piped-brace-cd", S, 'echo hi | { cd C:/Review/Game; rm -rf Data; }'),
    ("piped-brace-isolated", G, 'echo hi | { cd /tmp; }; rm -rf Data'),
    ("L2-ascend-relative", "C:/Review/Scratch/sub", 'for i in 1 2; do cd ..; done; rm -rf Game/Data'),
    ("L2-ascend-absolute", S, 'cd C:/Review/Game/Data/x/y; for i in 1 2 3; do cd ../..; done; rm -rf Data'),
    # one iteration stays outside the install; only a later ancestor (C:/) reaches it
    ("L2-ascend-two-iterations", S, 'cd C:/Review/Scratch/a/b; for i in 1 2; do cd ../..; done; rm -rf Review/Game/Data'),
]
# ...and the ordinary work around them that must keep running.
ALLOW = [
    ("uncalled-harmless", S, 'f() { echo hi; }; f; ls'),
    ("function-argument-elsewhere", "C:/Review", 'g() { cd "$1"; }; g Scratch; rm -rf Data'),
    ("ascend-loop-elsewhere", S, 'cd C:/Review/Scratch/a/b; for i in 1 2; do cd ../..; done; rm -rf Review/Other'),
    ("isolated-pipe-brace", S, '{ cd "C:/Review/Game"; } | cat; rm -rf Data'),
    ("isolated-background-brace", S, '{ cd "C:/Review/Game"; } & rm -rf Data'),
    ("L1-or-exit", G, 'cd /c/Temp/work || exit 1; rm -rf build'),
    ("L1-or-return", G, 'cd /c/Temp/work || return; rm -f build/a.o'),
    ("L1-or-brace-exit", G, 'cd /c/Temp/work || { echo failed; exit 1; }; rm -rf build'),
    ("eval-tool-hook", G, 'eval "$(conda shell.bash hook)"; git log'),
    ("L2-read-only-loop", G, 'for d in a b; do cd "$d"; git status; cd ..; done'),
    ("loop-unknown-target", S, 'for d in /a /b; do cd "$d" && git status; done'),
    ("printf-harmless", G, "printf 'ls\\n' | bash"),
]


@pytest.mark.parametrize("cwd,command", [(c, x) for _, c, x in DENY], ids=[n for n, _, _ in DENY])
def test_review_d_shapes_deny(cwd, command):
    assert decide(command, cwd) == "deny", shellparse.parse(command, "bash")


@pytest.mark.parametrize("cwd,command", [(c, x) for _, c, x in ALLOW], ids=[n for n, _, _ in ALLOW])
def test_review_d_ordinary_work_still_allows(cwd, command):
    assert decide(command, cwd) == "allow", shellparse.parse(command, "bash")


def test_or_with_a_fallthrough_branch_still_leaves_the_prior_location_possible():
    assert decide('cd /c/Temp/work || echo no; rm -rf Data', G) == "deny"


def test_a_parser_failure_inside_the_install_falls_back_to_deny(monkeypatch):
    """P5: an internal parser error left parsed.ok False with no execs, and S-DEL-FALLBACK required the TEXT to name
    a protected root, so a relative `rm -rf Data` run FROM the install was allowed."""
    def boom(self):
        raise AttributeError("forced internal error")
    monkeypatch.setattr(shellparse._Resolver, "run", boom)
    assert not shellparse.parse("rm -rf Data", "bash").ok
    assert decide("rm -rf Data", G) == "deny"
    assert decide("rm -rf Data", S) == "allow"


def test_printf_text_reads_only_plain_formats():
    assert shellparse._printf_text(["rm -rf Data"]) == "rm -rf Data"
    assert shellparse._printf_text(["50%% done\\n"]) == "50% done\n"
    for argv in (["a %s b", "x"], ["\\x72m"], ["-v", "x"], ["\\101"]):
        assert shellparse._printf_text(argv) is None, argv


def test_function_calls_have_their_own_token_allowance():
    """Priced 2026-10-30: a real 44-line script whose `run` wrapper was called ten times went past the 2000-token
    cap once each call re-read the body, and turned unreadable. Re-readings draw on MAX_FUNCTION_TOKENS instead."""
    command = "f() { echo a b c d e f g h i j; }; " + "f; " * 250
    assert shellparse.parse(command, "bash").ok
    assert not shellparse.parse("f() { f; f; }; f", "bash").ok      # recursion still stops at the nesting cap
