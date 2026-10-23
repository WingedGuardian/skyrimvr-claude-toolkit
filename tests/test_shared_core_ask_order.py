"""The Skyrim profile must preserve saves before a shared-core permission prompt."""
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from modding_harness import profiles
from modding_harness.contracts import Action, Result
from skyguard import portable, save_store


@pytest.mark.parametrize("count", [1, 3])
@pytest.mark.parametrize("origin", ["local", "profile"])
def test_ask_cannot_skip_skyrim_pair_preservation_or_bulk_approval(tmp_path, monkeypatch, count, origin):
    root = tmp_path / "toolkit"
    root.mkdir()
    saves = tmp_path / "profile" / "Saves"
    saves.mkdir(parents=True)
    (root / "skyrim-paths.env").write_text(
        "SKYRIM_CONFIG_DIR='" + saves.parent.as_posix() + "'\n")
    pairs = []
    for index in range(count):
        pair = [saves / (str(index) + suffix) for suffix in (".ess", ".skse")]
        for member in pair:
            member.write_bytes(member.name.encode())
        pairs.append(pair)
    action = Action("claude", "edit", "files", cwd=str(root), session_id="fixture",
                    paths=tuple(str(pair[0]) for pair in pairs), operations=("write",) * count)
    reason = "fixture confirmation"
    restrictions = ()
    if origin == "local":
        restrictions = (lambda _: Result("ask", "evaluated", reason=reason),)
    else:
        # Supply the profile's other-rule verdict at its existing shadow boundary.
        monkeypatch.setattr(portable.engine, "shadow", lambda value, *a, **kw:
                            value._replace(decision="ask", reason=reason))
    outcome, _ = portable.check(action, root, local_restrictions=restrictions)
    if count == 3:
        assert outcome.result.decision == "deny"
        assert outcome.result.preservation == "required"
        assert "typed canonical human approval required" in outcome.result.reason
        assert not outcome.transactions
    else:
        assert (outcome.result.decision, outcome.result.preservation) == ("ask", "verified")
        assert outcome.result.reason == reason
        assert len(outcome.transactions) == 1
        transaction = save_store.store(root).load(outcome.transactions[0])
        assert len(transaction["groups"]) == 1
        assert {profiles.canonical(member["path"]) for member in transaction["groups"][0]["members"]} == {
            profiles.canonical(member) for member in pairs[0]}


@pytest.mark.parametrize("kind", ["single", "bulk", "unresolved", "wildcard", "helper", "writer", "recheck"])
@pytest.mark.parametrize("local_ask", [False, True])
def test_unguarded_save_obligation_refuses(tmp_path, kind, local_ask):
    from modding_harness.dispatch import Dispatcher, Evaluation, Mutation, SavePlan
    plans = {
        "single": SavePlan((Mutation(str(tmp_path / "one.ess"), "write"),)),
        "bulk": SavePlan(tuple(Mutation(str(tmp_path / (str(i) + ".ess")), "write") for i in range(3))),
        "unresolved": SavePlan(unresolved="opaque helper"), "wildcard": SavePlan(wildcard=True),
        "helper": SavePlan(helper=True), "writer": SavePlan(known_writer_active=True),
        "recheck": SavePlan(recheck=lambda: ()),
    }
    dispatcher = Dispatcher(tmp_path, profiles.toy(), lambda _: Evaluation(Result(), plans[kind]),
        (lambda _: Result("ask", "evaluated"),) if local_ask else ())
    out = dispatcher.pre(Action("claude", "unknown", "native"))
    assert (out.result.decision, out.result.evaluation, out.result.preservation) == ("deny", "partial", "failed")
    assert "save obligations" in out.result.reason and not out.transactions
    assert not (tmp_path / ".modding-agent-harness/backups").exists()


@pytest.mark.parametrize("status", ["partial", "failed"])
def test_incomplete_local_ask_refuses(tmp_path, status):
    from modding_harness.dispatch import Dispatcher, Evaluation
    dispatcher = Dispatcher(tmp_path, profiles.toy(), lambda _: Evaluation(Result(evaluation="evaluated")),
        (lambda _: Result("ask", status),))
    out = dispatcher.pre(Action("claude", "fixture", "native"))
    assert (out.result.decision, out.result.evaluation) == ("deny", "failed") and not out.transactions


def test_unknown_local_ask_does_not_claim_profile_evaluation(tmp_path):
    from modding_harness.dispatch import Dispatcher, Evaluation
    dispatcher = Dispatcher(tmp_path, profiles.toy(), lambda _: Evaluation(Result()),
        (lambda _: Result("ask", "evaluated", reason="confirmation", warnings=("local warning",)),))
    out = dispatcher.pre(Action("claude", "unknown", "native"))
    assert (out.result.decision, out.result.evaluation) == ("ask", "unguarded")
    assert "local warning" in out.result.warnings
    assert any("WARN AND ALLOW" in warning for warning in out.result.warnings)
