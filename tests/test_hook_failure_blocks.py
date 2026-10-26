"""v4.0.0 H1 (user ruling 2026-10-25): a Claude guard hook that crashes, is killed, cannot start or times out BLOCKS the
tool call ("onFailure": "block", Claude Code 2.1.295+), and setup.sh refuses an older Claude Code before writing anything.

Without the key, Claude Code treats any exit other than 0 or 2 with empty stdout as a non-blocking error and runs the
command (hooks docs, "Other exit codes"): a guard killed mid-run approved what it never evaluated.
"""
from __future__ import annotations

import hashlib
import json
import os

import pytest

from conftest import REPO, make_game, run_setup

GUARDS = ("protect-bash.sh", "protect-files.sh", "protect-mcp.sh")


def _hooks(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    return [(h["command"].rsplit("/", 1)[-1].rstrip('"'), h)
            for group in data["hooks"]["PreToolUse"] for h in group["hooks"]]


@pytest.mark.parametrize("rel", ["agent/targets/claude/settings.json", ".claude/settings.json"])
def test_every_guard_hook_blocks_on_failure_and_nothing_else_does(rel):
    hooks = _hooks(REPO / rel)
    guards = {name: h for name, h in hooks if name in GUARDS}
    assert set(guards) == set(GUARDS), sorted(guards)
    for name, h in guards.items():
        assert h.get("onFailure") == "block", (rel, name)
    # The backup and snapshot hooks are not guards: a crash there must not stop the user's work.
    others = [name for name, h in hooks if name not in GUARDS and "onFailure" in h]
    assert not others, others


def _stub_claude(tmp_path, out):
    d = tmp_path / "claudebin"
    d.mkdir()
    f = d / "claude"
    f.write_text("#!/bin/bash\n" + ("echo '%s'\n" % out if out is not None else "exit 1\n"), encoding="utf-8",
                 newline="\n")
    f.chmod(0o755)
    return d


def _setup(tmp_path, out, agent="claude"):
    game = make_game(tmp_path)
    env = {"PATH": os.pathsep.join([str(_stub_claude(tmp_path, out)), os.environ["PATH"]])}
    before = {p.relative_to(game).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(game.rglob("*")) if p.is_file()}
    r = run_setup(game, env_overrides=env, shim_path=True, args=("--agent", agent))
    after = {p.relative_to(game).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(game.rglob("*")) if p.is_file()}
    return r, before == after


def test_an_older_claude_code_is_refused_before_anything_is_written(tmp_path):
    r, unchanged = _setup(tmp_path, "2.1.294 (Claude Code)")
    assert r.returncode == 3, r.stdout[-2000:] + r.stderr[-2000:]
    assert "Claude Code 2.1.294 is older than 2.1.295" in r.stderr and "Nothing was changed" in r.stderr
    assert unchanged


@pytest.mark.parametrize("ver", ["2.1.295 (Claude Code)", "2.1.304 (Claude Code)", "2.2.0", "10.0.1"])
def test_the_minimum_and_newer_pass(tmp_path, ver):
    r, _ = _setup(tmp_path, ver)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "(needs 2.1.295 or newer): OK." in r.stdout


def test_a_numerically_older_minor_is_refused_not_compared_as_text(tmp_path):
    """'2.1.30' sorts after '2.1.295' as text; the version compare must be numeric."""
    r, unchanged = _setup(tmp_path, "2.1.30")
    assert r.returncode == 3 and unchanged


def test_an_unreadable_version_is_said_not_refused(tmp_path):
    r, _ = _setup(tmp_path, None)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "Could not read 'claude --version'" in r.stdout


def test_codex_only_setup_never_asks_claude(tmp_path):
    r, _ = _setup(tmp_path, "1.0.0", agent="codex")
    assert "older than 2.1.295" not in r.stderr and "needs 2.1.295" not in r.stdout


def test_ordered_upgrade_uses_the_shipped_guard_failure_policy():
    import sys
    sys.path.insert(0, str(REPO / "tools"))
    from skyguard import ordered_upgrade

    expected = json.loads((REPO / "agent/targets/claude/settings.json").read_text(encoding="utf-8"))
    assert ordered_upgrade._claude_pre() == expected["hooks"]["PreToolUse"]
