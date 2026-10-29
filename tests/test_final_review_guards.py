"""Reviewer p5/p6/p7 shapes are parser input only, never shell execution."""
import pytest
from test_review_guards import ROOT, ROOTS, Audit, shell_rules, shellparse


CASES = [
    ("function", 'function f() { cd /tmp; }; rm -rf Data', ROOT),
    ("conditional-brace", 'false && { echo hi; cd /tmp; }; rm -rf Data', ROOT),
    ("piped-brace", '{ cd /tmp; } | cat; rm -rf Data', ROOT),
    ("background-brace", '{ cd /tmp; } & rm -rf Data', ROOT),
    ("coproc", 'coproc { cd /tmp; }; rm -rf Data', ROOT),
    ("relative-loop", 'for i in 1 2; do cd ..; done; rm -rf Game/Data', 'C:/Review/Scratch/sub'),
    ("failed-cd", 'cd /tmp 2>/dev/null || true; rm -rf Data', ROOT),
    ("pipe-sh", "test -d /tmp && cd /tmp; echo 'rm -rf Data' | sh", ROOT),
    ("pipe-bash", "if x; then cd /tmp; fi; echo 'rm -rf Data' | bash", ROOT),
    ("pipe-heredoc", "test -d /tmp && cd /tmp; cat <<EOF | bash\nrm -rf Data\nEOF", ROOT),
]


@pytest.mark.parametrize("command,cwd", [(c, d) for _, c, d in CASES], ids=[n for n, _, _ in CASES])
def test_possible_caller_locations_protect_install(command, cwd):
    verdict = shell_rules.decide(command, ROOTS, Audit(), cwd=cwd, shell="bash", env={})
    assert verdict.decision == "deny", (verdict, shellparse.parse(command, "bash"))


@pytest.mark.parametrize("command", [
    '{ cd /tmp; }; rm -rf Data',
    'cd /tmp && rm -rf Data',
    'for i in 1 2; do echo hi; done; echo harmless',
])
def test_literal_safe_location_controls_still_allow(command):
    assert shell_rules.decide(command, ROOTS, Audit(), cwd=ROOT, shell="bash", env={}).decision == "allow"


@pytest.mark.parametrize("body", [
    'function f() { cd "C:/Review/Game"; }',
    'coproc { cd "C:/Review/Game"; }',
    '{ cd "C:/Review/Game"; } | cat',
    '{ cd "C:/Review/Game"; } &',
])
def test_isolated_body_location_does_not_leak(body):
    command = body + (' ' if body.endswith('&') else '; ') + 'rm -rf Data'
    assert shell_rules.decide(command, ROOTS, Audit(), cwd="C:/Review/Scratch", shell="bash", env={}).decision == "allow"


@pytest.mark.parametrize("command", [
    'for d in /a /b; do cd "$d" && git status; done',
    'for w in plan4 plan5; do cd /c/Projects/$w && git log --oneline -1; done',
    'cd "C:/Review/Game"; for d in /a /b; do cd "$d"; git status --short; done',
])
def test_a_loop_cd_to_an_unknown_target_is_not_unreadable(command):
    """Priced 2026-10-29: 37 real read-only loops were denied when an unknown cd target counted as relative.
    An unknown target is the ordinary unknown-location case; only a literal relative move is unbounded."""
    assert shell_rules.decide(command, ROOTS, Audit(), cwd="C:/Review/Scratch", shell="bash", env={}).decision == "allow"


@pytest.mark.parametrize("command", [
    'cd "C:/Review/Game"; for d in $X; do cd "$d"; done; rm -rf Data',
    'cd "C:/Review/Game"; for d in /a /b; do cd "$d" && git status; done; rm -rf Data',
])
def test_a_loop_cd_to_an_unknown_target_still_protects_the_install(command):
    assert shell_rules.decide(command, ROOTS, Audit(), cwd="C:/Review/Scratch", shell="bash", env={}).decision == "deny"
