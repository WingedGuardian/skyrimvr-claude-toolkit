"""The engine interfaces Plan 5 builds on. Changing one is a cross-plan change: update both plans."""
import inspect
import re
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import engine  # noqa: E402
from skyguard.request import Request  # noqa: E402
from skyguard.verdict import Verdict  # noqa: E402

SPEC_FIELDS = ("v", "agent", "event", "kind", "shell", "command", "paths", "mcp_tool", "mcp_args", "cwd")


def test_verdict_shape():
    # `never` (Plan 6 final review A-C1, commit 3934907): a NEVER_SHADOW refusal carried beside a decision that
    # outranked it, for engine.shadow. It is engine-internal: the adapter wire line stays the five fields below.
    # `saves` (the save policy, 2026-10-13): the call's save plan, read by portable.check in the hook; engine-internal too.
    assert Verdict._fields == ("decision", "reason", "context", "status", "code", "never", "saves")
    assert Verdict().never is None


def test_the_wire_verdict_does_not_carry_never():
    """ADAPTING.md's answer line is v/decision/reason/context/status/code: the internal `never` must not leak."""
    import json
    from skyguard import neutral
    line = neutral.render(Verdict("deny", "r", never=Verdict("ask", "p")))
    assert sorted(json.loads(line)) == ["code", "context", "decision", "reason", "status", "v"]


def test_request_carries_every_spec_field():
    assert set(SPEC_FIELDS) <= set(Request._fields)
    assert Request().v == 1


def test_evaluate_shell_signature():
    assert list(inspect.signature(engine.evaluate_shell).parameters) == ["req", "roots", "audit"]


@pytest.mark.parametrize("call", ["shell_rules", "write_rules"])
def test_rules_decide_has_exactly_one_call_site(call):
    hits = []
    for f in sorted((REPO / "tools" / "skyguard").rglob("*.py")):
        n = len(re.findall(call + r"\.decide\(", f.read_text(encoding="utf-8")))
        if n:
            hits.append((f.name, n))
    assert hits == [("engine.py", 1)], hits
