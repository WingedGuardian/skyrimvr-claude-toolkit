"""Every hook verdict in tests/guard_corpus/expected.jsonl, re-checked against the shipped hooks.

Recorded from the bash hooks before the skyguard port (Plan 2, Task 1). After the port, the new
wrappers must reproduce every decision AND every reason byte for byte (spec §7.2).
"""
import importlib.util
import json
import tempfile
from pathlib import Path

import pytest

from conftest import REPO

EXPECTED = REPO / "tests" / "guard_corpus" / "expected.jsonl"


def _rec():
    spec = importlib.util.spec_from_file_location("record", REPO / "scripts" / "guard-corpus" / "record.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ROWS = [json.loads(line) for line in EXPECTED.read_text(encoding="utf-8").split("\n") if line.strip()]


@pytest.fixture(scope="module")
def installs():
    rec = _rec()
    if rec.needs_missing_jq(REPO / ".claude" / "hooks"):
        pytest.skip("the shipped hooks still need jq, and it is not installed")
    with tempfile.TemporaryDirectory() as td:
        yield rec, {name: rec.make_install(Path(td), REPO / ".claude" / "hooks", name) for name in rec.LAYOUTS}


def test_corpus_is_not_trivially_small():
    assert len(ROWS) >= 150, len(ROWS)
    assert {r["decision"] for r in ROWS} >= {"allow", "advise", "ask", "deny"}


def test_corpus_inputs_are_exactly_what_the_recorder_generates():
    """Dropping, adding or altering a row (id, layout, hook, payload) must fail, not just shrink coverage."""
    want = [(r["id"], r["layout"], r["hook"], r["payload"]) for r in _rec().inputs()]
    got = [(r["id"], r["layout"], r["hook"], None if r.get("payload_note") else r["payload"]) for r in ROWS]
    assert got == want


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_hook_reproduces_the_recorded_verdict(installs, row):
    rec, inst = installs
    install = inst[row["layout"]]
    payload = row["payload"]
    if row.get("payload_note") == "install-relative":
        payload = rec.install_relative(payload, install)
    got = rec.fire(install, row["hook"], payload)
    assert (got["decision"], got["text"]) == (row["decision"], row["text"])
