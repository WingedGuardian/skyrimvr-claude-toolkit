"""Fixtures captured from REAL sessions (spec §7.3) -- never hand-written from docs."""
import json
from pathlib import Path

import pytest

from conftest import REPO, login_name

FIX = REPO / "tests" / "fixtures" / "payloads"
REQUIRED = {
    "claude": ["PreToolUse-Bash", "PreToolUse-PowerShell", "PreToolUse-Edit", "PreToolUse-Write", "SessionStart-none"],
    "codex": ["PreToolUse-Bash", "PreToolUse-apply_patch", "SessionStart-none"],
}


def _load(agent):
    """Every payload under the agent's folder, version subfolders included (keyed by path under it)."""
    return {f.relative_to(FIX / agent).as_posix(): json.loads(f.read_text(encoding="utf-8"))
            for f in sorted((FIX / agent).rglob("*.json")) if f.name != "EXPECTED.json"}


def test_every_required_shape_was_captured():
    for agent, keys in REQUIRED.items():
        names = [n.rsplit("/", 1)[-1] for n in _load(agent)]
        for k in keys:
            assert any(n.startswith(f"{agent}-{k}-") for n in names), f"{agent}: no {k} fixture"


def test_codex_patches_cover_add_update_and_delete():
    cmds = [p["tool_input"]["command"] for n, p in _load("codex").items() if "apply_patch" in n.rsplit("/", 1)[-1]]
    for verb in ("*** Add File:", "*** Update File:", "*** Delete File:"):
        assert any(verb in c for c in cmds), verb


def test_no_fixture_carries_the_capturing_users_home():
    """Runs on whoever captures, so it checks THEIR user name, case-insensitively."""
    user = Path.home().name.lower()
    assert len(user) >= 3, f"user name {user!r} too short to search for meaningfully"
    for agent in REQUIRED:
        loaded = _load(agent)
        assert loaded, f"{agent}: no fixtures -- an empty set would pass this check vacuously"
        for name, p in loaded.items():
            assert user not in json.dumps(p).lower(), name


SERVICE_ACCOUNTS = frozenset(("runner", "runneradmin"))     # GitHub-hosted runner logins (Linux / Windows)


def test_no_file_under_fixtures_carries_the_capturing_users_home():
    """Final review (privacy): the check above read only the captured payloads, and the routes harness committed
    beside them carried the author's name and home paths. Every file under tests/fixtures/ is read, as bytes, in
    UTF-8 and UTF-16 spellings, so no fixture of any kind ships the home of whoever runs this.

    Which name: the harness launcher points HOME at a fake `home` folder and drops USERNAME, so the names come from
    the login variables, the account the OS says this runs as, the home folder when it is not that fake, and the
    user folder this checkout sits in (`C:/Users/<name>/...`, `/home/<name>/...`). windows-latest has only the
    second: its checkout is D:/a/..., and without it this skipped and its mutation twin survived."""
    import os
    import re
    names = {os.environ.get(k, "") for k in ("USERNAME", "USER", "LOGNAME")} | {login_name()}
    if Path.home().name.lower() != "home":
        names.add(Path.home().name)
    m = re.search(r"(?:^|/)(?:Users|home)/([^/]+)/", REPO.resolve().as_posix(), re.IGNORECASE)
    if m:
        names.add(m.group(1))
    names = {n.lower() for n in names if len(n) >= 3}
    if not names:
        pytest.skip("no user name to search for: no login variable, and the checkout is not under a user folder")
    root = REPO / "tests" / "fixtures"
    files = [f for f in sorted(root.rglob("*")) if f.is_file() and "__pycache__" not in f.parts]
    assert len(files) > 10, f"only {len(files)} fixture files -- the scan would be near-vacuous"

    def pattern(n):
        # Linux CI runs 38187755448 / 38201545601: GitHub's hosted runners log in as `runner` / `runneradmin`, and
        # `runner` is an ordinary word the fixtures use (sessionstart_runner.py, "test runner"). A service account
        # names no person, so for those only a HOME PATH counts (Users/<n>, home/<n>, any separator spelling,
        # escaped or not); every other name still counts anywhere. The launcher drops GITHUB_ACTIONS, so this is
        # decided by the name, not the environment.
        e = re.escape(n.encode("ascii", "ignore")) if n.isascii() else None
        if n in SERVICE_ACCOUNTS and e is not None:
            return re.compile(rb"(?:users|home)[/\\]+" + e + rb"(?![a-z0-9_])")
        return None

    hits = []
    for f in files:
        data = f.read_bytes().lower()
        for n in sorted(names):
            rx = pattern(n)
            if rx is not None:
                found = rx.search(data) or rx.search(data.replace(b"\x00", b""))   # utf-16-le spelled too
            else:
                found = any(n.encode(enc) in data for enc in ("utf-8", "utf-16-le"))
            if found:
                hits.append(f.relative_to(root).as_posix())
                break
    assert hits == [], hits
