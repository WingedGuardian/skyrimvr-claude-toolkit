"""Timing a static parser refusal must retain the deciding fail-closed contract."""
import sys
import time

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from modding_harness import contracts
from skyguard import portable, shell_rules
from test_skyguard_timing import LARGE, SMALL, _anchored_shapes, _delete_shapes, _timed_shell_decision


def _assert_normal_dispatcher_refuses(tmp_path, shell, command):
    root = tmp_path / "toolkit"
    root.mkdir()
    profile = tmp_path / "profile"
    saves = profile / "Saves"
    saves.mkdir(parents=True)
    members = [saves / ("Owned" + ext) for ext in (".ess", ".skse")]
    for member in members:
        member.write_bytes(b"owned unchanged fixture " + member.suffix.encode())
    before = {str(member): member.read_bytes() for member in members}
    quoted = "'" + str(profile).replace("'", "'\\''") + "'"
    (root / "skyrim-paths.env").write_text("SKYRIM_CONFIG_DIR=" + quoted + "\n", encoding="utf-8")
    action = contracts.Action("generic", "shell", "shell", cwd=str(root), shell=shell,
        command=command,
        session_id="owned-timing-refusal", tool_call_id="invalid-python")
    outcome, _ = portable.check(action, root, deadline=time.monotonic() + 40)
    assert outcome.result.decision == "deny", outcome.result.as_dict()
    assert outcome.result.evaluation == "failed", outcome.result.as_dict()
    assert "Python literal write scope could not be parsed" in outcome.result.reason
    assert not outcome.transactions
    assert before == {str(member): member.read_bytes() for member in members}


@pytest.mark.parametrize("shell", ["bash", "powershell"])
@pytest.mark.parametrize("size", [SMALL, LARGE])
def test_invalid_python_timing_shape_is_refused_by_normal_dispatcher(tmp_path, shell, size):
    _assert_normal_dispatcher_refuses(tmp_path, shell, _delete_shapes(size)["interp-unlink-run"])


@pytest.mark.parametrize("name", ["reg-code-run", "reg-code-unterminated"])
@pytest.mark.parametrize("shell", ["bash", "powershell"])
@pytest.mark.parametrize("size", [SMALL, LARGE])
def test_invalid_anchored_python_timing_shape_is_refused_by_normal_dispatcher(tmp_path, name, shell, size):
    _assert_normal_dispatcher_refuses(tmp_path, shell, _anchored_shapes(size)[name])


@pytest.mark.parametrize("name,error", [
    ("interp-unlink-run", ValueError("unrelated failure")),
    ("rm-deep-relative", ValueError("Python literal write scope could not be parsed")),
    ("interp-unlink-run", RuntimeError("Python literal write scope could not be parsed")),
    ("reg-code-run", ValueError("unrelated anchored failure")),
    ("reg-code-unterminated", RuntimeError("Python literal write scope could not be parsed")),
])
def test_timing_adapter_does_not_hide_an_unexpected_failure(monkeypatch, name, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(shell_rules, "decide", fail)
    with pytest.raises(type(error), match=str(error)) as raised:
        _timed_shell_decision("owned input is never executed", name, "bash")
    assert raised.value is error
