"""The guard corpus, in two files.

expected.jsonl is FROZEN: what the bash hooks decided before the skyguard port (Plan 2, Task 1). Plan 5
rewrites every message, so it is checked by decision and RULE ID (legacy_rules.json maps each frozen text to
its id), not by text. A row whose verdict a later task deliberately changes is named by `supersedes` in
intended.jsonl and is then skipped here.

intended.jsonl is the spec for the new behaviour, authored by hand: decision, rule ids and exact text, fired
through the real wrapper. It is never recorded from the engine (see tests/guard_corpus/README.md).
"""
import importlib.util
import json
import tempfile
from pathlib import Path

import pytest

from conftest import REPO

EXPECTED = REPO / "tests" / "guard_corpus" / "expected.jsonl"
INTENDED = REPO / "tests" / "guard_corpus" / "intended.jsonl"
LEGACY_RULES = REPO / "tests" / "guard_corpus" / "legacy_rules.json"


def _rec():
    spec = importlib.util.spec_from_file_location("record", REPO / "scripts" / "guard-corpus" / "record.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _load(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]


ROWS = _load(EXPECTED)
INTENDED_ROWS = _load(INTENDED)
TEMPLATES = json.loads(LEGACY_RULES.read_text(encoding="utf-8"))
SUPERSEDED = {r["supersedes"] for r in INTENDED_ROWS if r.get("supersedes")}
LIVE = [r for r in ROWS if r["id"] not in SUPERSEDED]


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


@pytest.mark.parametrize("row", LIVE, ids=[r["id"] for r in LIVE])
def test_hook_reproduces_the_recorded_decision_and_rules(installs, row):
    """A legacy row that is not superseded: same decision, same rule-id set. Never the text."""
    rec, inst = installs
    got = rec.fire_full(inst[row["layout"]], row)
    assert got["decision"] == row["decision"]
    assert got["rules"] == rec.legacy_rule_ids(row["text"], TEMPLATES), (row["text"], got["rules"])


def test_every_legacy_text_resolves_to_a_rule_set():
    """The frozen text must map to ids, or the check above compares nothing: a non-allow row maps to at least
    one rule, an allow row to none."""
    rec = _rec()
    for r in ROWS:
        ids = rec.legacy_rule_ids(r["text"], TEMPLATES)
        if r["decision"] == "allow":
            assert not ids, r["id"]
        elif not ids:
            pytest.fail(f"{r['id']}: no rule id for text {r['text'][:80]!r}")


def test_legacy_rule_ids_reads_a_joined_advise_text():
    rec = _rec()
    t = {"A one: {c}. tail": "S-A", "B two": "S-B", "C three": "S-C"}
    assert rec.legacy_rule_ids("A one: x | y. tail | B two", t) == {"S-A", "S-B"}
    assert rec.legacy_rule_ids("", t) == set()


def test_payload_cwd_install_template_is_filled_in(tmp_path):
    rec = _rec()
    p = {"tool_name": "Bash", "cwd": "<install>/Data", "tool_input": {"command": "rm -rf x"}}
    got = rec.row_payload({"payload": p, "payload_note": "install-relative"}, tmp_path)
    assert got["cwd"] == tmp_path.as_posix() + "/Data"
    assert rec.row_payload({"payload": p}, tmp_path) == p          # no note: left alone


def test_intended_rows_are_well_formed():
    rec = _rec()
    ids = [r["id"] for r in INTENDED_ROWS]
    assert len(ids) == len(set(ids)), "duplicate intended id"
    assert not set(ids) & {r["id"] for r in ROWS}, "an intended id collides with a legacy id"
    for r in INTENDED_ROWS:
        assert rec.intended_row_problems(r) == [], r["id"]


def test_an_intended_row_without_why_or_task_is_rejected():
    rec = _rec()
    row = {"id": "x", "layout": "declared", "hook": "protect-bash.sh", "payload": {}, "decision": "deny",
           "rules": ["S-DEL-INSTALL"], "text": "t", "why": "reason", "task": "T3"}
    assert rec.intended_row_problems(row) == []
    assert rec.intended_row_problems(dict(row, why="  "))
    assert rec.intended_row_problems(dict(row, task=""))
    assert rec.intended_row_problems({k: v for k, v in row.items() if k != "why"})


def test_intended_rows_reproduce(installs):
    """Each hand-authored row: decision + rule ids + EXACT text, through the real wrapper."""
    rec, inst = installs
    # Self-check first: the comparison must be able to fail on each of its three parts. With intended.jsonl
    # empty this is the only thing that proves the check is not decoration.
    good = {"decision": "deny", "rules": ["S-DEL-INSTALL"], "text": "Blocked: exact."}
    assert rec.intended_problems(good, dict(good, rules={"S-DEL-INSTALL"})) == []
    assert rec.intended_problems(good, {"decision": "ask", "rules": {"S-DEL-INSTALL"}, "text": good["text"]})
    assert rec.intended_problems(good, {"decision": "deny", "rules": {"S-REGISTRY"}, "text": good["text"]})
    assert rec.intended_problems(good, {"decision": "deny", "rules": {"S-DEL-INSTALL"}, "text": "Blocked: exact"})
    failures = []
    for row in INTENDED_ROWS:
        got = rec.fire_full(inst[row["layout"]], row)
        failures += [f"{row['id']}: {p}" for p in rec.intended_problems(row, got)]
    assert not failures, "\n".join(failures)


def _current(rec, inst):
    def current(row):
        got = rec.fire_full(inst[row["layout"]], row)
        return {"decision": got["decision"], "rules": got["rules"]}
    return current


def test_supersessions_are_live(installs):
    """A supersession the engine no longer needs fails, so it cannot hide a later regression."""
    rec, inst = installs
    problems = rec.supersession_problems(INTENDED_ROWS, ROWS, TEMPLATES, _current(rec, inst))
    assert problems == []


def _a_denied_legacy_row():
    return next(r for r in ROWS if r["decision"] == "deny" and r["layout"] == "declared"
                and not r.get("payload_note"))


def _legacy_twin(row, **over):
    """An intended row that claims to supersede a legacy row, with the identical case."""
    return dict({"id": "fake-" + row["id"], "layout": row["layout"], "hook": row["hook"],
                 "payload": row["payload"], "decision": row["decision"], "rules": [], "text": "x",
                 "supersedes": row["id"], "task": "T3", "why": "twin"}, **over)


def test_a_stale_supersession_is_caught(installs):
    """The twin of test_supersessions_are_live: the engine still gives the legacy verdict, so it must go red."""
    rec, inst = installs
    legacy = _a_denied_legacy_row()
    problems = rec.supersession_problems([_legacy_twin(legacy)], ROWS, TEMPLATES, _current(rec, inst))
    assert len(problems) == 1 and "stale" in problems[0], problems


def test_a_supersession_must_name_a_real_row_with_the_identical_case(installs):
    rec, inst = installs
    legacy = _a_denied_legacy_row()
    cur = _current(rec, inst)
    ghost = _legacy_twin(legacy, supersedes="no-such-row")
    other = _legacy_twin(legacy, payload={"tool_name": "Bash", "tool_input": {"command": "echo hi"}})
    assert "not a legacy row" in rec.supersession_problems([ghost], ROWS, TEMPLATES, cur)[0]
    assert "differs" in rec.supersession_problems([other], ROWS, TEMPLATES, cur)[0]


def _legacy(i, decision):
    return {"id": i, "layout": "declared", "hook": "protect-bash.sh", "payload": {"c": i}, "decision": decision,
            "text": ""}


def _supersede(old, new_id, decision, **over):
    return dict({"id": new_id, "layout": old["layout"], "hook": old["hook"], "payload": old["payload"],
                 "decision": decision, "rules": [], "text": "x", "supersedes": old["id"], "task": "T4",
                 "why": "w"}, **over)


def _always_changed(row):
    return {"decision": "x", "rules": {"R"}}


def test_a_supersession_that_loosens_the_decision_needs_loosens_and_a_reason():
    rec = _rec()
    old = _legacy("old1", "deny")
    for new in ("ask", "advise", "allow"):
        bare = _supersede(old, "n-" + new, new)
        got = rec.supersession_problems([bare], [old], {}, _always_changed)
        assert len(got) == 1 and "loosens" in got[0], (new, got)
    flag_only = _supersede(old, "n1", "allow", loosens=True)
    no_flag = _supersede(old, "n2", "allow", reason="because")
    blank = _supersede(old, "n3", "allow", loosens=True, reason="  ")
    for row in (flag_only, no_flag, blank):
        assert rec.supersession_problems([row], [old], {}, _always_changed), row["id"]
    ok = _supersede(old, "n4", "allow", loosens=True, reason="the legacy ask fired on embedded text")
    assert rec.supersession_problems([ok], [old], {}, _always_changed) == []


def test_a_supersession_that_keeps_or_tightens_the_decision_needs_no_flag():
    rec = _rec()
    old = _legacy("old2", "ask")
    for new in ("ask", "deny"):
        assert rec.supersession_problems([_supersede(old, "n-" + new, new)], [old], {}, _always_changed) == []


def test_a_legacy_row_is_superseded_by_at_most_one_intended_row():
    rec = _rec()
    old = _legacy("old3", "ask")
    a, b = _supersede(old, "a", "deny"), _supersede(old, "b", "deny")
    got = rec.supersession_problems([a, b], [old], {}, _always_changed)
    assert len(got) == 1 and "exactly one" in got[0], got
    assert rec.supersession_problems([a], [old], {}, _always_changed) == []


def test_intended_supersessions_are_unique_and_loosenings_declared():
    """The real file, no engine needed: the static halves of the supersession rules."""
    rec = _rec()
    problems = rec.supersession_problems(INTENDED_ROWS, ROWS, TEMPLATES, _always_changed)
    assert [p for p in problems if "loosens" in p or "exactly one" in p] == []
