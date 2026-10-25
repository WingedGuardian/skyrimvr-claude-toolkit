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

PINNED = "bc7a0c464c0236fbb84977bc56aaf2d5dd7b64f61aa9b2b771cf491634106381"
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


def test_the_classes_follow_the_retention_rulings_and_the_code():
    """Review C-docs F3/F4 (user rulings 2026-10-20/24): the tool snapshot stores rotate although they sit under
    `.claude/backups/` (snapshot.py / recovery.py KEEP_DAYS = 14, health.py SESSIONS_KEEP = 20, kb-guard.sh
    KB_GUARD_KEEP 30); save backups and every other backup store stay Forever; the temp sweep takes only the
    toolkit's own prefixes and `.claude/retired`, never pytest's shared runs."""
    text = DOC.read_text(encoding="utf-8")
    rows = {m.group(1): m.group(2) for m in re.finditer(r"^\| \*\*(.+?)\*\* \| (.*)$", text, re.M)}
    assert "knowledgebase snapshots" not in rows["Forever"] and "save backups included" in rows["Forever"]
    assert "except the rolling tool stores" in rows["Forever"] and "Rolling tool store" in rows
    assert "pytest's old runs" not in rows["Toolkit temp"] and ".claude/retired/" in rows["Toolkit temp"]
    import sys
    sys.path.insert(0, str(REPO / "tools"))
    from skyguard import health, recovery, snapshot
    assert snapshot.KEEP_DAYS == recovery.KEEP_DAYS == 14 and health.SESSIONS_KEEP == 20
    assert 'KEEP="${KB_GUARD_KEEP:-30}"' in (REPO / "tools" / "kb-guard.sh").read_text(encoding="utf-8")
    checklist = " ".join(text[text.index("## Skyrim checklist"):].split())
    assert "younger than 14 days" in checklist and "older than 14 days" in checklist
    assert "Session logs keep the 20 newest" in checklist and "`KB_GUARD_KEEP` (default 30)" in checklist
    assert "past the 20 newest per agent are renamed" not in checklist
