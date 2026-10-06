"""Plan 8 T8 (reader side): skyrim-paths.env moves to the toolkit root.

Contract (shared with setup.sh, which writes the file):
  - `<root>/skyrim-paths.env` is read first; `<root>/.claude/skyrim-paths.env` (3.x / early 4.0) is the fallback, with
    a deprecation notice once per session (the SessionStart hook), never per tool call;
  - both present: the root file WINS whole (the legacy file is not read at all, so a stale or broken leftover cannot
    change the guard); a root file that exists but cannot be read fails closed, never falls back;
  - neither present: nothing is declared, and the no-root-resolvable rule still DENIES;
  - `run.py --root ""` reads no file at all (it used to read a cwd-relative `.claude/skyrim-paths.env`);
  - the new key SKYGUARD_PYTHON is read (optional: empty or absent is "not set") and is no root.
Every test fires the real reader or the real engine entry (run.py)."""
import json
import os
import subprocess
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import paths, self_rules, session  # noqa: E402
from skyguard.audit import Audit  # noqa: E402

RUN = REPO / "tools" / "skyguard" / "run.py"
DEL_CONFIG = ("Blocked: this deletes {} in the Skyrim settings folder.\n"
              "Your INIs and controlmap there cannot be restored by a mod manager.")


def _root_file(root, text):
    root.mkdir(parents=True, exist_ok=True)
    (root / "skyrim-paths.env").write_text(text, encoding="utf-8", newline="\n")


def _legacy_file(root, text):
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "skyrim-paths.env").write_text(text, encoding="utf-8", newline="\n")


def _clean_env():
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    for k in paths.ENV_KEYS + ("SKYGUARD_PYTHON",):
        env.pop(k, None)
    return env


def _fire(root, payload, agent="claude", role="protect-bash", cwd=None):
    r = subprocess.run([sys.executable, str(RUN), agent, role, "--root", root, "--root-win", root,
                        "--project", root or str(cwd)], input=json.dumps(payload).encode(), capture_output=True,
                       env=_clean_env(), timeout=120, cwd=cwd)
    assert r.returncode == 0, r.stderr
    text = r.stdout.decode()
    if agent == "codex":
        text = text.split("\n", 1)[1] if "\n" in text else ""
    out = json.loads(text or "{}").get("hookSpecificOutput", {})
    return out.get("permissionDecision", "advise" if out.get("additionalContext") else "allow"), \
        out.get("permissionDecisionReason", "")


def _rm(path):
    return {"tool_name": "Bash", "tool_input": {"command": "rm -f '{}'".format(path)}}


# ---- the reader ----------------------------------------------------------------------------------------------------

def test_the_root_file_is_read(tmp_path):
    _root_file(tmp_path, "SKYRIM_CONFIG_DIR='C:/new/cfg'\n")
    cfg = paths.read_install(str(tmp_path), {})
    assert cfg.values("SKYRIM_CONFIG_DIR") == ["C:/new/cfg"]
    assert cfg.source == "skyrim-paths.env"


def test_the_legacy_file_is_the_fallback(tmp_path):
    _legacy_file(tmp_path, "SKYRIM_CONFIG_DIR='C:/old/cfg'\n")
    cfg = paths.read_install(str(tmp_path), {})
    assert cfg.values("SKYRIM_CONFIG_DIR") == ["C:/old/cfg"]
    assert cfg.source == ".claude/skyrim-paths.env"


def test_both_present_and_disagreeing_the_root_file_wins_whole(tmp_path):
    _root_file(tmp_path, "SKYRIM_CONFIG_DIR='C:/new/cfg'\n")
    _legacy_file(tmp_path, "SKYRIM_CONFIG_DIR='C:/old/cfg'\nSKYRIM_MO2_MODS='C:/old/mods'\n")
    cfg = paths.read_install(str(tmp_path), {})
    assert cfg.values("SKYRIM_CONFIG_DIR") == ["C:/new/cfg"]
    assert cfg.values("SKYRIM_MO2_MODS") == [], "a key only the legacy file declares must not leak past the root file"
    assert cfg.source == "skyrim-paths.env"


def test_a_broken_legacy_leftover_does_not_matter_once_the_root_file_exists(tmp_path):
    _root_file(tmp_path, "SKYRIM_CONFIG_DIR='C:/new/cfg'\n")
    _legacy_file(tmp_path, 'SKYRIM_MO2_MODS="D:/MO2 $X/mods"\n')
    assert paths.read_install(str(tmp_path), {}).malformed == ()


def test_an_unreadable_root_file_raises_and_never_falls_back(tmp_path):
    tmp_path.joinpath("skyrim-paths.env").write_bytes(b'SKYRIM_GAME_ROOT="\xff\xfe\x80"\n')
    _legacy_file(tmp_path, "SKYRIM_CONFIG_DIR='C:/old/cfg'\n")
    with pytest.raises(ValueError):
        paths.read_install(str(tmp_path), {})


def test_neither_file_declares_nothing(tmp_path):
    cfg = paths.read_install(str(tmp_path), {"SKYRIM_CONFIG_DIR": "C:/env/cfg"})
    assert cfg.file == {} and cfg.source == ""
    assert cfg.values("SKYRIM_CONFIG_DIR") == ["C:/env/cfg"]


def test_no_root_reads_no_file_at_all(tmp_path, monkeypatch):
    """Deferred item 9: an empty root used to be Path("") -> a cwd-relative `.claude/skyrim-paths.env`."""
    monkeypatch.chdir(tmp_path)
    _root_file(tmp_path, "SKYRIM_GAME_ROOT='C:/from/cwd'\n")
    _legacy_file(tmp_path, "SKYRIM_GAME_ROOT='C:/from/cwd/legacy'\n")
    cfg = paths.read_install("", {})
    assert cfg.file == {} and cfg.source == ""


def test_skyguard_python_is_read_and_is_no_root(tmp_path):
    _root_file(tmp_path, "SKYGUARD_PYTHON='C:/Python312/python.exe'\nSKYRIM_MO2_MODS=''\nSKYRIM_MO2_OVERWRITE=''\n")
    cfg = paths.read_install(str(tmp_path), {})
    assert cfg.first("SKYGUARD_PYTHON") == "C:/Python312/python.exe"
    assert cfg.malformed == ()
    r = paths.roots("C:/G", "", cfg)
    assert r.install_dirs == ("c:/G",) and r.mo2_dirs == () and r.config_dirs == ()


@pytest.mark.parametrize("text", ["", "SKYGUARD_PYTHON=''\n", "SKYGUARD_PYTHON=\n"], ids=["absent", "quoted", "bare"])
def test_an_empty_or_absent_skyguard_python_is_not_set(text, tmp_path):
    _root_file(tmp_path, text)
    cfg = paths.read_install(str(tmp_path), {})
    assert cfg.first("SKYGUARD_PYTHON") == "" and cfg.malformed == ()


def test_the_environment_skyguard_python_wins_for_lookup(tmp_path):
    _root_file(tmp_path, "SKYGUARD_PYTHON='C:/file/python.exe'\n")
    cfg = paths.read_install(str(tmp_path), {"SKYGUARD_PYTHON": "C:/env/python.exe"})
    assert cfg.first("SKYGUARD_PYTHON") == "C:/env/python.exe"


# ---- through the engine entry (run.py) -------------------------------------------------------------------------------

def test_run_guards_a_config_dir_declared_in_the_root_file(tmp_path):
    root, cfg = tmp_path / "Game", tmp_path / "cfg"
    cfg.mkdir()
    _root_file(root, "SKYRIM_CONFIG_DIR='{}'\n".format(cfg.as_posix()))
    assert _fire(root.as_posix(), _rm(cfg.as_posix() + "/Skyrim.ini")) == ("deny", DEL_CONFIG.format("Skyrim.ini"))


def test_run_still_guards_a_config_dir_declared_only_in_the_legacy_file(tmp_path):
    root, cfg = tmp_path / "Game", tmp_path / "cfg"
    cfg.mkdir()
    _legacy_file(root, "SKYRIM_CONFIG_DIR='{}'\n".format(cfg.as_posix()))
    assert _fire(root.as_posix(), _rm(cfg.as_posix() + "/Skyrim.ini")) == ("deny", DEL_CONFIG.format("Skyrim.ini"))


@pytest.mark.parametrize("where", ["root", "legacy"])
@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_a_malformed_paths_file_is_refused_from_either_location(where, agent, tmp_path):
    """rt-fix-b's malformed paths-file refusal (both agents), from each location."""
    root = tmp_path / "Game"
    (_root_file if where == "root" else _legacy_file)(root, 'SKYRIM_MO2_MODS="D:/MO2 $X/mods"\n')
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": root.as_posix(),
               "tool_input": {"command": "ls"}}
    got = _fire(root.as_posix(), payload, agent=agent, role="pre-tool" if agent == "codex" else "protect-bash")
    assert got[0] == "deny" and "GUARD INERT" in got[1] and "SKYRIM_MO2_MODS" in got[1], got


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_an_unreadable_root_file_fails_closed_naming_the_file_location_neutrally(agent, tmp_path):
    root = tmp_path / "Game"
    root.mkdir()
    (root / "skyrim-paths.env").write_bytes(b'SKYRIM_GAME_ROOT="\xff\xfe\x80"\n')
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": root.as_posix(),
               "tool_input": {"command": "ls"}}
    got = _fire(root.as_posix(), payload, agent=agent, role="pre-tool" if agent == "codex" else "protect-bash")
    assert got[0] == "deny" and "GUARD INERT" in got[1], got
    assert ".claude/skyrim-paths.env" not in got[1], "the refusal must not send the user to the old location"


def test_run_with_no_root_reads_no_cwd_relative_file_and_denies(tmp_path):
    """Deferred item 9 + 'neither -> DENY': a cwd file declaring a game root must not stand in for the root."""
    _root_file(tmp_path, "SKYRIM_GAME_ROOT='{}'\n".format(tmp_path.as_posix()))
    _legacy_file(tmp_path, "SKYRIM_GAME_ROOT='{}'\n".format(tmp_path.as_posix()))
    got = _fire("", {"tool_name": "Bash", "tool_input": {"command": "ls"}}, cwd=str(tmp_path))
    assert got[0] == "deny" and "GUARD INERT" in got[1], got


def test_a_worktree_reads_the_main_trees_root_file(tmp_path):
    main, cfg = tmp_path / "Game", tmp_path / "cfg"
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    cfg.mkdir()
    _root_file(main, "SKYRIM_CONFIG_DIR='{}'\n".format(cfg.as_posix()))
    wt = main / ".claude" / "worktrees" / "wt"
    wt.mkdir(parents=True)
    (wt / ".git").write_text("gitdir: {}/.git/worktrees/wt\n".format(main.as_posix()))
    assert _fire(wt.as_posix(), _rm(cfg.as_posix() + "/Skyrim.ini")) == ("deny", DEL_CONFIG.format("Skyrim.ini"))


def test_an_in_place_restore_reads_the_root_file(tmp_path):
    """snapcli: --in-place writes only inside the DECLARED roots, so it must find them at the 4.0 location."""
    from test_recovery_snapshots import Install, put
    from test_snapshot_restore_cli import cli_run, ids, take_all
    inst = Install(tmp_path)
    put(inst.game / "Data" / "Scripts" / "a.pex", b"script a v1")
    put(inst.game / "skyrim-paths.env", "SKYRIM_CONFIG_DIR='{}'\nSKYRIM_LOADORDER_DIR='{}'\n".format(
        inst.cfg.as_posix(), inst.lo.as_posix()).encode("utf-8"))
    take_all(inst)
    put(inst.lo / "plugins.txt", b"*Broken.esp\n")
    rc, text = cli_run(inst, "restore", ids(inst, "config")[0], "--path", "plugins.txt", "--in-place")
    assert rc == 0, text
    assert (inst.lo / "plugins.txt").read_bytes() == b"*Skyrim.esm\n*Mine.esp\n"


@pytest.mark.parametrize("value", ["'py -3'", "python3"], ids=["launcher", "bare-name"])
def test_a_non_path_skyguard_python_is_kept(value, tmp_path):
    """SKYGUARD_PYTHON is no root, so the absolute-path rule for roots does not apply (a launcher is legitimate)."""
    _root_file(tmp_path, "SKYGUARD_PYTHON={}\n".format(value))
    assert paths.read_install(str(tmp_path), {}).first("SKYGUARD_PYTHON") == value.strip("'")


# ---- the deprecation notice: SessionStart only -------------------------------------------------------------------

def test_the_legacy_location_gets_a_notice_at_session_start(tmp_path):
    _legacy_file(tmp_path, "SKYRIM_CONFIG_DIR='C:/old/cfg'\n")
    text, _, _ = session.run_status(str(tmp_path), "startup", "bash", Audit(str(tmp_path)))
    assert paths.DEPRECATION in text


def test_no_notice_once_the_root_file_exists(tmp_path):
    _root_file(tmp_path, "SKYRIM_CONFIG_DIR='C:/new/cfg'\n")
    _legacy_file(tmp_path, "SKYRIM_CONFIG_DIR='C:/old/cfg'\n")
    text, _, _ = session.run_status(str(tmp_path), "startup", "bash", Audit(str(tmp_path)))
    assert paths.DEPRECATION not in text


def test_the_notice_is_not_repeated_on_compact(tmp_path):
    _legacy_file(tmp_path, "SKYRIM_CONFIG_DIR='C:/old/cfg'\n")
    text, _, _ = session.run_status(str(tmp_path), "compact", "bash", Audit(str(tmp_path)))
    assert paths.DEPRECATION not in text


def test_a_tool_call_never_carries_the_notice(tmp_path):
    root = tmp_path / "Game"
    _legacy_file(root, "SKYRIM_CONFIG_DIR='C:/old/cfg'\n")
    r = subprocess.run([sys.executable, str(RUN), "claude", "protect-bash", "--root", root.as_posix(),
                        "--root-win", root.as_posix(), "--project", root.as_posix()],
                       input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}).encode(),
                       capture_output=True, env=_clean_env(), timeout=120)
    assert r.returncode == 0 and paths.DEPRECATION.encode() not in r.stdout + r.stderr


def test_the_notice_names_both_locations_and_the_fix():
    assert "skyrim-paths.env" in paths.DEPRECATION and ".claude/skyrim-paths.env" in paths.DEPRECATION
    assert "setup.sh" in paths.DEPRECATION


# ---- never tracked, never shipped ----------------------------------------------------------------------------------

@pytest.mark.parametrize("rel", ["skyrim-paths.env", ".claude/skyrim-paths.env"], ids=["root", "legacy"])
def test_the_paths_file_is_never_tracked_at_either_location(rel):
    """The release is built from committed objects: an ignored file cannot reach the zip by `git add .`.
    (tools/check-release-payload.py refuses ANY file of that name in the payload: tests/test_release_payload.py.)"""
    r = subprocess.run(["git", "check-ignore", "-q", "--no-index", rel], cwd=str(REPO), capture_output=True)
    assert r.returncode == 0, rel + " is not ignored by .gitignore"


def test_a_lookalike_deeper_in_the_tree_is_not_swallowed_by_the_root_ignore():
    r = subprocess.run(["git", "check-ignore", "-q", "--no-index", "docs/skyrim-paths.env"], cwd=str(REPO),
                       capture_output=True)
    assert r.returncode == 1


# ---- self-protection covers the new location ----------------------------------------------------------------------

@pytest.mark.parametrize("rel", [["skyrim-paths.env"], [".claude", "skyrim-paths.env"]], ids=["root", "legacy"])
def test_both_locations_are_guard_files(rel):
    assert self_rules.install_kind(rel) == self_rules.GUARD


def test_only_the_root_level_name_is_a_guard_file():
    assert self_rules.install_kind(["docs", "skyrim-paths.env"]) == ""
    assert self_rules.install_kind(["skyrim-paths.env.example"]) == ""
