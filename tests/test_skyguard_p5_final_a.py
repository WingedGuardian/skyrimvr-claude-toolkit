"""Plan 5 final review, lane A (parser and path layer) fix pass: every P5-FINAL-A intended row, fired IN-PROCESS.

The same rows are fired through the real wrapper by test_guard_corpus.py::test_intended_rows_reproduce; this module
runs them through the engine mirror (record.engine_verdict) parametrized by row id, so a mutation in
tests/test_mutations.py can name the one row it must turn red. Rows were authored from the rule table with the
review's minimal inputs (final-review-lane-A-report.md), each dangerous form beside its ordinary benign twin.
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
    return [r for r in (json.loads(x) for x in text.split("\n") if x.strip()) if r["task"] == "P5-FINAL-A"]


ROWS = _rows()


@pytest.fixture(scope="module")
def installs():
    rec = _rec()
    with tempfile.TemporaryDirectory() as td:
        yield rec, {name: rec.make_install(Path(td), REPO / ".claude" / "hooks", name)
                    for name in {r["layout"] for r in ROWS}}


def test_there_are_rows_in_both_directions():
    assert len(ROWS) >= 5, len(ROWS)
    assert {r["decision"] for r in ROWS} == {"allow", "deny"}


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_p5a_row(installs, row):
    rec, inst = installs
    install = inst[row["layout"]]
    got = rec.engine_verdict(install, row["hook"], rec.row_payload(row, install))
    assert rec.intended_problems(row, got) == []
