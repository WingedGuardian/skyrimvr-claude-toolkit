"""engine.py: the one place requests meet rules."""
import sys

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import engine, write_rules  # noqa: E402
from skyguard.request import Request  # noqa: E402
from skyguard.verdict import Verdict  # noqa: E402


class _Audit:
    def __init__(self):
        self.lines = []

    def log(self, hook, level, msg):
        self.lines.append((hook, level, msg))

    def raw(self, line):
        self.lines.append(line)

    def rule(self, hook, rule_id, decision, msg, level=None):     # Plan 5: rules record their id (Audit.rule)
        self.log(hook, level or decision.upper(), msg)

    @staticmethod
    def stamp():
        return "20261002_000000"


def test_first_deny_wins_and_keeps_its_reason():
    v = engine.combine([Verdict("allow", "", "a"), Verdict("deny", "first"), Verdict("deny", "second")])
    assert (v.decision, v.reason) == ("deny", "first")


def test_ask_beats_advice_but_not_deny():
    assert engine.combine([Verdict("allow", "", "a"), Verdict("ask", "q")]).decision == "ask"
    assert engine.combine([Verdict("ask", "q"), Verdict("deny", "d")]).decision == "deny"


def test_advice_accumulates_in_order_without_duplicates():
    v = engine.combine([Verdict("allow", "", "a"), Verdict("allow", "", "b"), Verdict("allow", "", "a")])
    assert v.context == "a | b"


def test_worst_status_survives():
    v = engine.combine([Verdict(status="evaluated"), Verdict(status="partial"), Verdict(status="evaluated")])
    assert v.status == "partial"


def test_a_multi_path_write_is_denied_by_any_plugin_path():
    req = Request(kind="write", paths=("C:/G/Data/a.ini", "C:/G/Data/b.esp"), ops=("update", "add"))
    v = engine.evaluate_writes(req, _Audit())
    assert (v.decision, v.code) == ("deny", "write.DENY_PLUGIN")


def test_a_write_request_with_no_paths_refuses():
    v = engine.evaluate_writes(Request(kind="write"), _Audit())
    assert (v.decision, v.status) == ("deny", "failed")


def test_claude_to_request_carries_cwd_and_kind():
    from skyguard.adapters import claude
    p = claude.extract('{"tool_name":"Bash","cwd":"C:/x","session_id":"s1","tool_input":{"command":"ls"}}')
    r = claude.to_request(p, "shell")
    assert (r.kind, r.shell, r.command, r.cwd, r.session_id) == ("shell", "bash", "ls", "C:/x", "s1")


def test_combine_of_nothing_is_a_failed_deny_not_an_allow():
    v = engine.combine([])
    assert (v.decision, v.status, v.code) == ("deny", "failed", "engine.EMPTY_COMBINE")
