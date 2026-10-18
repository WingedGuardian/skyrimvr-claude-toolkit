"""docs/RETENTION.md's shared section is the same text in the Skyrim and X4 toolkits.

The two repos have disjoint histories, so nothing can import one from the other. Instead each pins the hash of the
section between the shared-policy markers. Change the text in one repo and this goes red until the new hash is
pinned -- in BOTH repos (x4-claude-toolkit: tools/x4validate/tests/test_retention_policy.py), with the same text.
"""
from __future__ import annotations

import hashlib
import json
import re

from conftest import REPO

PINNED = "e89302148024fd266cbd0a5075fea84c6d1f705c49d835c7f7c91e7198cdf3ef"
DOC = REPO / "docs" / "RETENTION.md"


def shared_section(text: bytes) -> bytes:
    m = re.search(rb"<!-- shared-policy:begin -->\n(.*?)<!-- shared-policy:end -->",
                  text.replace(b"\r\n", b"\n"), re.S)
    assert m, "docs/RETENTION.md lost its shared-policy markers"
    return m.group(1)


def test_the_shared_policy_matches_the_pinned_text_shared_with_the_x4_toolkit():
    got = hashlib.sha256(shared_section(DOC.read_bytes())).hexdigest()
    assert got == PINNED, ("the shared retention policy changed ({}). Make the same edit in x4-claude-toolkit "
                           "docs/RETENTION.md, then pin the new hash in both repos.".format(got))


def test_a_one_character_edit_changes_the_hash():
    text = DOC.read_bytes()
    edited = text.replace(b"Forever", b"Forevr", 1)
    assert hashlib.sha256(shared_section(edited)).hexdigest() != PINNED


def test_the_example_config_loads_with_no_unknown_keys():
    import importlib.util
    spec = importlib.util.spec_from_file_location("housekeep", REPO / "tools" / "housekeep.py")
    hk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hk)
    example = REPO / ".claude" / "housekeep.json.example"
    cfg = hk.load_config(example)
    assert set(json.loads(example.read_text(encoding="utf-8"))) - {"_comment"} <= set(hk.DEFAULTS)
    assert cfg["active_hours"] == 48
