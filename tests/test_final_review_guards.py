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
