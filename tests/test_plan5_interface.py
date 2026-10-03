"""The engine interfaces Plan 5 builds on. Changing one is a cross-plan change: update both plans."""
import inspect
import re
import sys

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import engine  # noqa: E402
from skyguard.request import Request  # noqa: E402
from skyguard.verdict import Verdict  # noqa: E402

SPEC_FIELDS = ("v", "agent", "event", "kind", "shell", "command", "paths", "mcp_tool", "mcp_args", "cwd")


def test_verdict_shape():
    assert Verdict._fields == ("decision", "reason", "context", "status", "code")


def test_request_carries_every_spec_field():
    assert set(SPEC_FIELDS) <= set(Request._fields)
    assert Request().v == 1


def test_evaluate_shell_signature():
    assert list(inspect.signature(engine.evaluate_shell).parameters) == ["req", "roots", "audit"]


def test_shell_rules_decide_has_exactly_one_call_site():
    hits = []
    for f in sorted((REPO / "tools" / "skyguard").rglob("*.py")):
        n = len(re.findall(r"shell_rules\.decide\(", f.read_text(encoding="utf-8")))
        if n:
            hits.append((f.name, n))
    assert hits == [("engine.py", 1)], hits
