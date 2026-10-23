"""Canonical typed grants must reach the live matcher and retain operation scope."""
import pytest

from test_save_policy import Inst, three
from modding_harness import approval


@pytest.mark.parametrize("op", ["write", "move"])
def test_canonical_grant_rejects_another_operation(tmp_path, op):
    inst = Inst(tmp_path)
    before = {path: path.read_bytes() for path in inst.saves.iterdir()}
    transcript = inst.transcript(inst.grant(op=op), name="wrong-operation.jsonl")
    grants, why = approval.read(transcript, "claude", "s1")
    assert why == "" and len(grants) == 1 and grants[0].operation == op
    assert inst.enforce(three(inst), transcript=transcript).decision == "deny"
    assert inst.saved() == []
    correct = inst.transcript(inst.grant(op="delete"), name="correct-operation.jsonl")
    assert inst.enforce(three(inst), transcript=correct).decision == "allow"
    assert len(inst.saved()) == 6
    assert {path: path.read_bytes() for path in inst.saves.iterdir()} == before
