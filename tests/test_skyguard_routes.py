"""Claude-side routes the routes red team (2026-10-04) found weaker than intended, in shapes the guard corpus cannot
express (a git worktree layout, non-string tool inputs). Each test fires the real engine entry (run.py) or the rule
module, from the rule table, never recorded from the engine."""
import json
import os
import subprocess
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import paths  # noqa: E402

RUN = REPO / "tools" / "skyguard" / "run.py"
DEL_INSTALL = ("Blocked: this deletes {} inside the Skyrim install.\n"
               "A mod manager cannot restore your own files there; if you mean it, run it yourself.")
DEL_STORE = ("Blocked: {} is a permanent save backup store or its ancestor.\n"
             "Recovery data cannot be overwritten, deleted, moved or aliased by this call.")
DEL_UNRESOLVED = ("Blocked: this deletes a path the guard cannot work out, in a command that names a protected Skyrim "
                  "folder.\nWrite the full path, or run it yourself.")
DEL_CONFIG = ("Blocked: this deletes {} in the Skyrim settings folder.\n"
              "Your INIs and controlmap there cannot be restored by a mod manager.")


def _fire(root, role, payload):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    for k in paths.ENV_KEYS:
        env.pop(k, None)
    r = subprocess.run([sys.executable, str(RUN), "claude", role, "--root", root, "--root-win", root,
                        "--project", root], input=json.dumps(payload).encode(), capture_output=True, env=env,
                       timeout=120)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.decode() or "{}").get("hookSpecificOutput", {})
    return out.get("permissionDecision", "advise" if out.get("additionalContext") else "allow"), \
        out.get("permissionDecisionReason", "")


@pytest.fixture()
def worktree(tmp_path):
    """A main install that is a git repo, its paths file declaring a config dir, and a linked worktree inside it
    where Claude Code's EnterWorktree puts one (.claude/worktrees/<name>), with its own copy of the hooks."""
    main = tmp_path / "Game"
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    (main / "Data").mkdir()
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (main / ".claude").mkdir()
    (main / ".claude" / "skyrim-paths.env").write_text('SKYRIM_CONFIG_DIR="{}"\n'.format(cfg.as_posix()))
    wt = main / ".claude" / "worktrees" / "wt"
    wt.mkdir(parents=True)
    (wt / ".git").write_text("gitdir: {}/.git/worktrees/wt\n".format(main.as_posix()))
    return main.as_posix(), wt.as_posix(), cfg.as_posix()


def test_hooks_run_from_a_worktree_still_guard_the_main_install(worktree):
    main, wt, _ = worktree
    got = _fire(wt, "protect-bash", {"tool_name": "Bash", "cwd": wt,
                                      "tool_input": {"command": "rm -rf '{}/Data'".format(main)}})
    assert got == ("deny", DEL_INSTALL.format("Data"))


def test_hooks_run_from_a_worktree_name_the_main_install_as_a_root(worktree):
    # The main tree is a GAME root too (not only an install dir): an unresolvable delete in a command that names it
    # is S-DEL-UNRESOLVED, as it is for the hooks' own root.
    main, wt, _ = worktree
    got = _fire(wt, "protect-bash", {"tool_name": "Bash", "cwd": wt,
                                      "tool_input": {"command": "cd '{}' && rm -rf \"$X\"".format(main)}})
    assert got == ("deny", DEL_UNRESOLVED)


def test_hooks_run_from_a_worktree_read_the_main_trees_paths_file(worktree):
    main, wt, cfg = worktree
    got = _fire(wt, "protect-bash", {"tool_name": "Bash", "cwd": wt,
                                      "tool_input": {"command": "rm -f '{}/Skyrim.ini'".format(cfg)}})
    assert got == ("deny", DEL_CONFIG.format("Skyrim.ini"))


def test_a_worktree_outside_the_install_still_guards_it(worktree, tmp_path):
    main, _, _ = worktree
    (tmp_path / "Game" / ".git" / "worktrees" / "out").mkdir()
    out = tmp_path / "elsewhere" / "out"
    out.mkdir(parents=True)
    (out / ".git").write_text("gitdir: {}/.git/worktrees/out\n".format(main))
    assert paths.main_worktree(out.as_posix()) == main
    got = _fire(out.as_posix(), "protect-bash", {"tool_name": "Bash", "cwd": out.as_posix(),
                                                 "tool_input": {"command": "rm -rf '{}'".format(main)}})
    # The whole install also contains its permanent recovery store; that
    # refusal deliberately takes precedence over ordinary install deletion.
    assert got == ("deny", DEL_STORE.format("Game"))


@pytest.mark.parametrize("dotgit", ["gitdir: ../somewhere/else\n", "not a gitdir line\n", ""])
def test_a_dot_git_file_that_is_not_a_linked_worktree_adds_nothing(tmp_path, dotgit):
    (tmp_path / ".git").write_text(dotgit)
    assert paths.main_worktree(tmp_path.as_posix()) == ""
    assert paths.main_worktree((tmp_path / "missing").as_posix()) == ""
