"""State rejection must happen before a copy can fail for another reason."""
import sys

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, backup  # noqa: E402


def test_state_filter_runs_before_backup_copy(tmp_path, monkeypatch):
    project = tmp_path / "owned-project"
    state_file = project / ".skyguard" / "state.json"
    ordinary = project / "Data" / "x.ini"
    for file in (state_file, ordinary):
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"owned fixture")
    calls = []
    def take(*args):
        calls.append(args)
        return None  # An impossible copy must not mask filter acceptance.
    monkeypatch.setattr(backup, "take", take)
    log = audit.Audit(str(project))
    backup.run(str(project), "Edit", str(state_file), False, log)
    assert calls == [], "state must be skipped before the copy layer"
    backup.run(str(project), "Edit", str(ordinary), False, log)
    assert calls == [(str(project), "Edit", str(ordinary), log, "", "")]
    assert state_file.read_bytes() == ordinary.read_bytes() == b"owned fixture"
