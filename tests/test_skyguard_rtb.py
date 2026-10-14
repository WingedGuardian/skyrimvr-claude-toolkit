"""rt-fix-b (routes red team, 2026-10-04): every hand-authored RTB intended row, fired IN-PROCESS.

The same rows are fired through the real wrapper by test_guard_corpus.py::test_intended_rows_reproduce; this module
runs them through the engine mirror (record.engine_verdict) parametrized by row id, so a mutation in
tests/test_mutations.py can name the one row it must turn red. Rows were authored from the rule table (rules.py
templates) with inputs taken from the routes harness (tests/fixtures/routes-harness), never recorded from the engine.
"""
import importlib.util
import json
import tempfile
from pathlib import Path

import pytest

from conftest import REPO


def _rec():
    spec = importlib.util.spec_from_file_location("record", REPO / "scripts" / "guard-corpus" / "record.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _rows():
    text = (REPO / "tests" / "guard_corpus" / "intended.jsonl").read_text(encoding="utf-8")
    return [r for r in (json.loads(x) for x in text.split("\n") if x.strip()) if r["task"] == "RTB"]


ROWS = _rows()


@pytest.fixture(scope="module")
def installs():
    rec = _rec()
    with tempfile.TemporaryDirectory() as td:
        layouts = {r["layout"] for r in ROWS}
        yield rec, {name: rec.make_install(Path(td), REPO / ".claude" / "hooks", name) for name in layouts}


def test_there_are_rtb_rows():
    assert len(ROWS) >= 100, len(ROWS)
    # The save policy (2026-10-13): the save rows now advise (backed up first) instead of asking.
    assert {r["decision"] for r in ROWS} == {"allow", "advise", "ask", "deny"}
    for rid in ("S-SAVE-BACKUP", "S-MOVE-ROOT", "S-GIT-DISCARD", "W-INERT-NOPATH", "S-WRITE-PLUGIN",
                "S-DEVBENCH-CRASH", "S-DEVBENCH-SAVE"):
        assert any(rid in r["rules"] for r in ROWS), rid


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_rtb_row(installs, row):
    rec, inst = installs
    install = inst[row["layout"]]
    got = rec.engine_verdict(install, row["hook"], rec.row_payload(row, install))
    assert rec.intended_problems(row, got) == []
