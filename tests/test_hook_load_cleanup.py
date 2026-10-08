"""Failure-injection checks for the opt-in load harness; these start no processes."""
import pytest

import test_hook_deadline_load as harness


def test_partial_burner_start_failure_stops_and_waits_for_every_started_child(monkeypatch):
    monkeypatch.setattr(harness, "CORES", 1)
    monkeypatch.setattr(harness, "FACTOR", 2)
    monkeypatch.setattr(harness, "_budget_check", lambda cores: None)
    monkeypatch.setattr(harness, "_get_affinity", lambda: "original")
    affinity, stopped, waited = [], [], []
    monkeypatch.setattr(harness, "_set_affinity", affinity.append)
    monkeypatch.setattr(harness, "_stop", stopped.append)

    class Child:
        def wait(self, timeout):
            waited.append(timeout)

    child = Child()
    starts = []

    def start():
        if starts:
            raise OSError("second child could not start")
        starts.append(child)
        return child

    monkeypatch.setattr(harness, "_start_burner", start)
    with pytest.raises(OSError, match="second child could not start"):
        next(harness.load.__wrapped__())
    assert stopped == [child], "already-started child escaped the fixture's cleanup"
    assert waited == [30]
    assert affinity[-1] == "original"
