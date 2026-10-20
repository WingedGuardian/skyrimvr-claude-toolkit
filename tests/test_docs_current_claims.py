"""User docs may not describe behaviour the v4 code no longer has (release review L6, 2026-10-20).

Each test pins one claim that went stale inside the v4 arc: the stale wording must stay gone, and where the doc
states the current behaviour, that statement must be present. The code side of each claim is tested elsewhere
(named in each test); this file only keeps the prose from drifting back.
"""
from __future__ import annotations

from conftest import REPO


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_saves_are_backed_up_not_confirmed():
    """W-SAVE / S-WRITE-SAVE went with the 2026-10-13 save policy: a write onto 1-2 saves is backed up and allowed
    (tests/test_save_policy.py::test_an_edit_tool_write_onto_a_save_is_backed_up and
    ::test_a_devbench_save_over_an_existing_save_is_backed_up)."""
    readme = _read("README.md")
    assert "its one prompt is a write onto a save" not in readme
    assert "existing save is refused with `NEEDS YOUR CONFIRMATION`" not in readme
    assert "A write onto one or two saves in `Saves/` is backed up first, then runs" in readme
    assert "existing save is backed up first, then runs" in readme
    assert "save-over-a-save confirmation" not in _read("ADAPTING.md")
