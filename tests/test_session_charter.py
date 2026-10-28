"""Session charter and ledger at SessionStart (2026-10-27): the shared core's charter (the session's origin) and its
open ledger are appended after the knowledgebase guard and preferences at every source, within the hosts' budgets."""
from __future__ import annotations

import json
import sys
from unittest import mock

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import session  # noqa: E402
from modding_harness import charter, ledger  # noqa: E402

SID = "0b7c6733-5bc4-4b6f-af5e-8c64ed2ce4fe"


def _seed(project):
    charter.capture_origin(json.dumps({"session_id": SID, "prompt": "Port the mod to VR"}).encode(), str(project))
    ledger.main(["--workspace", str(project), "--session", SID, "add", "--", "Rebuild the ESP"])


def test_the_charter_and_open_ledger_follow_the_guard_text(tmp_path, capsys):
    _seed(tmp_path)
    capsys.readouterr()
    out = session.with_charter("KB GUARD: ok\nUSER PREFERENCES: x", str(tmp_path), SID)
    assert out.startswith("KB GUARD: ok\nUSER PREFERENCES: x\nSESSION CHARTER")
    assert "> Port the mod to VR" in out
    assert "[Ledger open: 1" in out and "Rebuild the ESP" in out


def test_no_session_id_leaves_the_text_alone(tmp_path):
    assert session.with_charter("KB GUARD: ok", str(tmp_path), "") == "KB GUARD: ok"


def test_a_charter_that_does_not_fit_becomes_a_pointer(tmp_path, capsys):
    _seed(tmp_path)
    capsys.readouterr()
    out = session.with_charter("P" * 7000, str(tmp_path), SID)
    assert "list the open ledger yourself. Ledger commands, run as" in out
    assert "--session={}".format(SID) in out
    assert ".agent-sessions/{}.md".format(SID) in out
    assert "Port the mod to VR" not in out
    assert len(out.encode("utf-8")) < 8000


def test_a_broken_core_is_reported_in_one_line(tmp_path):
    with mock.patch.object(charter, "render", side_effect=RuntimeError("boom")):
        out = session.with_charter("KB GUARD: ok", str(tmp_path), SID)
    assert out.splitlines()[-1].startswith("SESSION CHARTER: NOT INJECTED")
