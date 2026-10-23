"""Configured permanent stores keep their own rule even outside the install tree."""
import json
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, paths, shell_rules, write_rules  # noqa: E402


@pytest.mark.parametrize("route", ["shell", "files"])
def test_configured_store_mutations_are_denied_before_ordinary_location_rules(tmp_path, route):
    project, store = tmp_path / "toolkit", tmp_path / "permanent-store"
    (project / ".modding-agent-harness").mkdir(parents=True)
    (project / ".modding-agent-harness/config.json").write_text(
        json.dumps({"v": 1, "save_store": str(store)}), encoding="utf-8")
    store.mkdir()
    target = store / "owned-blob"
    target.write_bytes(b"owned permanent recovery bytes")
    roots = paths.roots(str(project), str(project), {"SKYRIM_GAME_ROOT": str(project)})

    def decide(path):
        log = audit.Audit("")
        log.raw = lambda line: None
        if route == "shell":
            value = shell_rules.decide('printf x > "' + path.as_posix() + '"', roots, log,
                                       cwd=project.as_posix(), shell="bash", env={})
        else:
            value = write_rules.decide(str(path), log, roots=roots, cwd=str(project), env={})
        return value, {rid for rid, _ in log.fired}

    value, fired = decide(target)
    rid = "S-SAVE-STORE" if route == "shell" else "W-SAVE-STORE"
    assert value.decision == "deny" and fired == {rid}
    assert "permanent save backup store" in value.reason
    ordinary, _ = decide(tmp_path / "ordinary-output")
    assert ordinary.decision == "allow"
    assert target.read_bytes() == b"owned permanent recovery bytes"
