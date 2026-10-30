"""setup.sh v4 (Plan 8 T7): `--agent claude|codex|all|auto`, `--lock`, the root paths file, the 3.x hook-in, the
runtime messages and the instruction-budget warning -- each asserted on what the REAL setup.sh wrote or said.

Rules under test, from the plan:
  * an unknown value is a usage error (exit 2) and NOTHING is written;
  * each value configures exactly its agent; an unselected agent's files are kept as extracted, and setup says
    plainly that agent is not configured;
  * every value is idempotent: a second run changes no byte;
  * a partial state (a half-filled CLAUDE.md, a lost paths file, a stale hooks.json.new) is repaired by a re-run;
  * Codex is Windows-only and never marked trusted;
  * the Layer 2 lock is applied only with --lock (R3: off by default).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import BASH, BIN, REPO, game_root_win, make_game, run_setup

WIN = sys.platform == "win32"
windows_only = pytest.mark.skipif(not WIN, reason="the toolkit's Codex hooks are rendered on Windows only")
posix_only = pytest.mark.skipif(WIN, reason="needs a non-Windows install root")
DOC_MARKS = ("{{GAME_ROOT}}", "{{CONFIG_DIR}}", "{{LOADORDER_DIR}}", "{{MOD_MANAGER_PATHS}}")
GUARD_FILES = [".claude/hooks/" + n for n in ("protect-bash.sh", "protect-files.sh", "protect-mcp.sh",
                                              "backup-before-edit.sh", "snapshot-before-tool.sh",
                                              "session-kb-guard.sh")] + [
    ".codex/hooks/skyguard-entry.ps1", ".codex/hooks/skyguard-entry.sh"]


def tree(root: Path) -> dict:
    """Every file under the install, by content. The backups folder is excluded only where a test says so."""
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}


def configured(game: Path, rel: str) -> bool:
    text = (game / rel).read_text(encoding="utf-8")
    return not any(m in text for m in DOC_MARKS)


def codex_rendered(game: Path) -> bool:
    return "{{CODEX_ROOT" not in (game / ".codex" / "hooks.json").read_text(encoding="utf-8")


def stub_bin(tmp_path: Path, **scripts) -> Path:
    """A folder of fake commands: name -> bash body. Put first on PATH to stand in for node, dotnet, java ..."""
    d = tmp_path / "stubbin"
    d.mkdir(exist_ok=True)
    for name, body in scripts.items():
        f = d / name
        f.write_text("#!/bin/bash\n" + body + "\n", encoding="utf-8", newline="\n")
        f.chmod(0o755)
    return d


def path_with(*dirs) -> str:
    return os.pathsep.join([str(d) for d in dirs] + [os.environ["PATH"]])


# ------------------------------------------------------------------------------------------ usage errors --

@pytest.mark.parametrize("args", [
    ("--agent", "gpt"), ("--agent", "CLAUDE"), ("--agent", ""), ("--agent=both,codex",), ("--agent",),
    ("--bogus",), ("claude",),
], ids=["unknown-value", "wrong-case", "empty", "list", "no-value", "unknown-option", "positional"])
def test_a_usage_error_exits_2_and_writes_nothing(game_dir, args):
    before = tree(game_dir)
    r = run_setup(game_dir, args=args)
    assert r.returncode == 2, (r.returncode, r.stdout[-800:], r.stderr[-800:])
    assert "Nothing was changed" in r.stderr and "--agent claude|codex|all|auto" in r.stderr, r.stderr
    assert tree(game_dir) == before, "a usage error wrote something"


def test_help_exits_0_and_writes_nothing(game_dir):
    before = tree(game_dir)
    r = run_setup(game_dir, args=("--help",))
    assert r.returncode == 0 and "--agent claude|codex|all|auto" in r.stdout and "--lock" in r.stdout
    assert tree(game_dir) == before


@pytest.mark.parametrize("agent", ["generic", "opencode"])
def test_portable_targets_configure_shared_instructions_without_codex_trust(game_dir, agent):
    before = (game_dir / ".codex/hooks.json").read_bytes()
    result = run_setup(game_dir, shim_path=True, args=("--agent", agent))
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr
    assert configured(game_dir, "AGENTS.md")
    assert (game_dir / ".codex/hooks.json").read_bytes() == before
    assert "does not prove a host intercepted" in result.stdout
    if agent == "opencode":
        assert "Running-host blocking, Desktop and V2 remain unqualified" in result.stdout
    assert not (game_dir / ".codex/trusted.json").exists()


@posix_only
def test_codex_by_name_off_windows_is_refused_before_anything_is_written(game_dir):
    before = tree(game_dir)
    r = run_setup(game_dir, args=("--agent", "codex"))
    assert r.returncode == 3, r.stdout + r.stderr
    assert "Windows-only" in r.stderr and "Nothing was changed" in r.stderr
    assert tree(game_dir) == before


# ------------------------------------------------------------------------------------- each --agent value --

AGENT_CASES = {
    "claude": (True, False),
    "codex": (False, True),
    "all": (True, True),
    "both": (True, True),
}


@pytest.mark.parametrize("value", sorted(AGENT_CASES))
def test_each_value_configures_exactly_its_agents(game_dir, value):
    claude, codex = AGENT_CASES[value]
    if value == "codex" and not WIN:
        pytest.skip("--agent codex off Windows is refused (test_codex_by_name_off_windows_...)")
    r = run_setup(game_dir, args=("--agent", value))
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    out = r.stdout
    assert configured(game_dir, "CLAUDE.md") is claude
    # AGENTS.md serves Codex and every other agent: filled whenever Codex is selected, on any OS (T7 review I1).
    assert configured(game_dir, "AGENTS.md") is codex
    # Only Codex's hooks are Windows-only.
    assert codex_rendered(game_dir) is (codex and WIN)
    codex = codex and WIN
    # Every guard file runs the one engine on the one interpreter, whichever agent was selected.
    for rel in GUARD_FILES:
        assert "{{PYTHON_PATH}}" not in (game_dir / rel).read_text(encoding="utf-8"), rel
    assert ("Claude Code: CONFIGURED" in out) is claude, out[-1500:]
    assert ("Codex:       CONFIGURED" in out) is codex, out[-1500:]
    if not claude:
        assert "Claude Code: NOT CONFIGURED" in out and "bash setup.sh --agent claude" in out
        assert "CLAUDE.md: not configured by this run" in out
        assert not (game_dir / ".claude" / "settings.local.json").exists(), "a Claude-only file was created"
    if not codex:
        assert "Codex:       NOT CONFIGURED" in out
        assert ("AGENTS.md: not configured by this run" in out) is (not AGENT_CASES[value][1])
        if WIN:
            assert "WITHOUT the toolkit's hooks" in out and "bash setup.sh --agent codex" in out
        else:
            assert "Codex support is Windows-only" in out
        assert "Codex users: Codex runs these hooks only after you approve them" not in out
    else:
        assert "/hooks, and approve the 2 Skyrim toolkit hooks" in out


@pytest.mark.parametrize("value", ["claude", "codex", "all", "both", "auto"])
def test_every_value_is_idempotent(game_dir, value):
    if value == "codex" and not WIN:
        pytest.skip("Codex is configured on Windows only")
    assert run_setup(game_dir, args=("--agent", value)).returncode == 0
    first = tree(game_dir)
    r = run_setup(game_dir, args=("--agent", value))
    assert r.returncode == 0, r.stdout[-1500:]
    assert tree(game_dir) == first, "a second run changed: %s" % sorted(
        k for k in set(first) | set(tree(game_dir)) if first.get(k) != tree(game_dir).get(k))
    assert "Configured with your paths" not in r.stdout and "Configured: " not in r.stdout, r.stdout[-1500:]


@pytest.mark.parametrize("hide,stubs,expected", [
    ("claude codex", (), "all"),
    ("codex", ("claude",), "claude"),
    pytest.param("claude", ("codex",), "codex", marks=windows_only),
    pytest.param("", ("claude", "codex"), "all", marks=windows_only),
], ids=["none-found-means-all", "claude-only", "codex-only", "both-found"])
def test_auto_configures_the_agents_found_on_path(game_dir, tmp_path, hide, stubs, expected):
    stubdir = stub_bin(tmp_path, **{s: "echo stub" for s in stubs})
    r = run_setup(game_dir, shim_path=True, env_overrides={
        "PATH": path_with(stubdir, BIN), "FAKE_WHICH_MISSING": hide})
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    assert "-> configuring: %s (--agent auto)" % expected in r.stdout, r.stdout[:800]
    assert configured(game_dir, "CLAUDE.md") is (expected in ("claude", "all"))
    assert configured(game_dir, "AGENTS.md") is (expected in ("codex", "all"))


def test_auto_is_the_default(game_dir, tmp_path):
    r = run_setup(game_dir, shim_path=True, env_overrides={
        "PATH": path_with(stub_bin(tmp_path, claude="echo stub"), BIN), "FAKE_WHICH_MISSING": "codex"})
    assert "-> configuring: claude (--agent auto)" in r.stdout, r.stdout[:800]


@windows_only
def test_a_later_run_for_the_other_agent_keeps_the_first(game_dir):
    assert run_setup(game_dir, args=("--agent", "claude")).returncode == 0
    claude_md = (game_dir / "CLAUDE.md").read_bytes()
    r = run_setup(game_dir, args=("--agent", "codex"))
    assert r.returncode == 0, r.stdout[-1500:]
    assert (game_dir / "CLAUDE.md").read_bytes() == claude_md, "the codex run touched CLAUDE.md"
    assert configured(game_dir, "AGENTS.md") and codex_rendered(game_dir)
    assert "Claude Code: CONFIGURED" in r.stdout and "Codex:       CONFIGURED" in r.stdout


@pytest.mark.parametrize("value", ["claude", pytest.param("codex", marks=windows_only)])
def test_an_unselected_agent_leaves_no_guard_drift(game_dir, value):
    """The session-start drift check compares the interpreter across all nine guard files: one left at its
    placeholder by a one-agent run would read as an edited guard file at every session start."""
    sys.path.insert(0, str(REPO / "tools"))
    from skyguard import session
    assert run_setup(game_dir, args=("--agent", value)).returncode == 0
    _release, differ, missing, unchecked = session._guard_drift(str(game_dir))
    assert (differ, missing, unchecked) == ([], 0, []), (differ, missing, unchecked)


def test_skydoctor_names_codex_as_not_configured_after_a_claude_only_run(game_dir, tmp_path):
    """An unrendered .codex/hooks.json means Codex would run here with no toolkit hook: it must be flagged."""
    assert run_setup(game_dir, args=("--agent", "claude")).returncode == 0
    r = subprocess.run([BASH, "tools/skydoctor", "--root", str(game_dir), "--json", "--no-toolchain",
                        "--codex-config", str(tmp_path / "no-codex-config.toml")],
                       cwd=str(game_dir), capture_output=True, text=True, timeout=180)
    rows = json.loads(r.stdout)["rows"]
    trust = [x for x in rows if x["section"] == "Layer 3 codex" and x["item"] == "trust"]
    assert trust and trust[0]["status"] == "not live" and "not rendered" in trust[0]["reason"], trust or r.stdout[-1500:]


@windows_only
def test_codex_by_name_exits_1_when_codex_cannot_be_configured(tmp_path):
    """Asked for Codex alone, and Codex could not be set up (a root the renderer refuses): not a success."""
    game = make_game(tmp_path, name="Sky!rim")
    r = run_setup(game, args=("--agent", "codex"))
    assert r.returncode == 1, r.stdout[-1500:]
    assert "CODEX HOOKS NOT CONFIGURED" in r.stdout and "--agent codex: Codex is NOT configured" in r.stdout


def test_setup_never_marks_a_codex_project_trusted(game_dir, tmp_path):
    """Trust is the user's, in Codex. A run with CODEX_HOME pointed at an empty folder leaves it empty."""
    home = tmp_path / "codexhome"
    home.mkdir()
    assert run_setup(game_dir, env_overrides={"CODEX_HOME": str(home)}).returncode == 0
    assert list(home.iterdir()) == []
    assert "trust_level" not in (game_dir / ".codex" / "config.toml").read_text(encoding="utf-8")


# -------------------------------------------------------------------------------- partial-state repair --

def test_a_half_filled_claude_md_is_completed_by_a_rerun(game_dir):
    """An interrupted run can leave the root filled and a later placeholder not. The old 'is {{GAME_ROOT}} still
    there?' test called that file configured, forever."""
    assert run_setup(game_dir, args=("--agent", "claude")).returncode == 0
    doc = game_dir / "CLAUDE.md"
    line = next(l for l in doc.read_text(encoding="utf-8").splitlines() if l.startswith("- **User INI configs**"))
    half = doc.read_text(encoding="utf-8").replace(line, "- **User INI configs**: `{{CONFIG_DIR}}/` (Skyrim.ini)")
    doc.write_text(half, encoding="utf-8", newline="\n")
    r = run_setup(game_dir, args=("--agent", "claude"))
    assert r.returncode == 0
    assert configured(game_dir, "CLAUDE.md"), [l for l in doc.read_text(encoding="utf-8").splitlines() if "{{" in l]
    assert "Configured with your paths" in r.stdout


def test_a_lost_paths_file_is_rewritten(game_dir):
    assert run_setup(game_dir).returncode == 0
    want = (game_dir / "skyrim-paths.env").read_bytes()
    (game_dir / "skyrim-paths.env").write_bytes(b"")          # emptied: it would hide a 3.x file and declare nothing
    (game_dir / "skyrim-paths.env.new").write_text("SKYRIM_GAME_ROOT='/x'\n", encoding="utf-8")   # interrupted run
    assert run_setup(game_dir).returncode == 0
    assert (game_dir / "skyrim-paths.env").read_bytes() == want
    assert not (game_dir / "skyrim-paths.env.new").exists()
    (game_dir / "skyrim-paths.env").unlink()
    assert run_setup(game_dir).returncode == 0
    assert (game_dir / "skyrim-paths.env").read_bytes() == want


@windows_only
def test_a_stale_hooks_json_new_is_never_taken_for_the_result(game_dir):
    (game_dir / ".codex" / "hooks.json.new").write_text("{\"garbage\": true}", encoding="utf-8")
    r = run_setup(game_dir, args=("--agent", "codex"))
    assert r.returncode == 0, r.stdout[-1500:]
    assert codex_rendered(game_dir) and not (game_dir / ".codex" / "hooks.json.new").exists()
    assert "garbage" not in (game_dir / ".codex" / "hooks.json").read_text(encoding="utf-8")


def test_a_hook_reverted_to_its_placeholder_is_configured_again(game_dir):
    assert run_setup(game_dir).returncode == 0
    hook = game_dir / ".codex" / "hooks" / "skyguard-entry.sh"
    text = hook.read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if l.startswith("PY="))
    hook.write_text(text.replace(line, "PY='{{PYTHON_PATH}}'"), encoding="utf-8", newline="\n")
    r = run_setup(game_dir)
    assert "Configured: .codex/hooks/skyguard-entry.sh" in r.stdout
    assert line in hook.read_text(encoding="utf-8")


# --------------------------------------------------------------------- the paths file at the toolkit root --

def _sourced(env_file: Path, key: str) -> str:
    return subprocess.run([BASH, "-c", ". '" + env_file.as_posix() + "'; printf '%s' \"$" + key + "\""],
                          capture_output=True, text=True, timeout=60).stdout


def test_the_paths_file_is_written_at_the_root_and_no_3x_copy_is_created(game_dir):
    r = run_setup(game_dir)
    assert r.returncode == 0
    new = game_dir / "skyrim-paths.env"
    assert not (game_dir / ".claude" / "skyrim-paths.env").exists(), "the reader uses the root file; no 3.x copy"
    assert "Wrote: skyrim-paths.env" in r.stdout
    assert _sourced(new, "SKYRIM_GAME_ROOT") == game_root_win(game_dir)
    # the interpreter the hooks got, recorded for the tools that need one
    hook_py = next(l for l in (game_dir / ".claude" / "hooks" / "protect-bash.sh").read_text(encoding="utf-8")
                   .splitlines() if l.startswith("PY="))
    assert "PY='" + _sourced(new, "SKYGUARD_PYTHON") + "'" == hook_py
    sys.path.insert(0, str(REPO / "tools"))
    from skyguard import paths
    cfg = paths.read_config(new, {})
    assert cfg.malformed == () and cfg.values("SKYRIM_GAME_ROOT") == [game_root_win(game_dir)]
    if hasattr(paths, "read_install"):                          # the v4 reader (Plan 8 T8): it reads THIS file
        assert paths.read_install(str(game_dir), {}).source == paths.PATHS_FILE
        assert paths.legacy_notice(str(game_dir)) == ""


def _legacy(game, extra=""):
    """A 3.x .claude/skyrim-paths.env naming this install, plus `extra` lines."""
    p = game / ".claude" / "skyrim-paths.env"
    p.write_text("# 3.9\nSKYRIM_GAME_ROOT='%s'\n%s" % (game_root_win(game), extra), encoding="utf-8", newline="\n")
    return p, p.read_bytes()


def _retired(game):
    return sorted((game / ".claude" / "backups" / "skyrim-paths-3x").glob("*/skyrim-paths.env"))


def test_a_3x_paths_file_that_adds_nothing_is_retired_by_a_move(game_dir):
    """The 4.0 reader UNIONS the protection roots of both files, so a 3.x file left behind would drift silently.
    One that declares no folder the root file lacks adds nothing: it is moved (never deleted) into backups."""
    legacy, before = _legacy(game_dir)
    r = run_setup(game_dir)
    assert r.returncode == 0, r.stdout[-1500:]
    sys.path.insert(0, str(REPO / "tools"))
    from skyguard import paths
    assert paths.DEPRECATION in r.stdout, "setup did not print the session-start notice for the state it found"
    assert not legacy.exists(), r.stdout[-2000:]
    moved = _retired(game_dir)
    assert len(moved) == 1 and moved[0].read_bytes() == before, moved
    assert "Retired .claude/skyrim-paths.env" in r.stdout and "Moved, not deleted" in r.stdout
    again = run_setup(game_dir)
    assert paths.DEPRECATION not in again.stdout and len(_retired(game_dir)) == 1


def test_a_3x_paths_file_that_adds_a_folder_is_kept_and_its_folders_named(game_dir):
    legacy, before = _legacy(game_dir, "SKYRIM_CONFIG_DIR='D:/Old INIs'\n")
    r = run_setup(game_dir)
    assert r.returncode == 0
    assert legacy.read_bytes() == before, "a 3.x file that still adds protection was changed"
    assert not _retired(game_dir)
    assert "still protects them (it combines the two files)" in r.stdout and "SKYRIM_CONFIG_DIR=D:/Old INIs" in r.stdout
    assert "wins" not in r.stdout, "the union reader means the root file does not 'win' for protected folders"


@pytest.mark.parametrize("body", [b"SKYRIM_CONFIG_DIR=$HOME/My INIs\n", b"SKYRIM_CONFIG_DIR='D:/M\x81ller'\n"],
                         ids=["malformed", "not-utf8"])
def test_a_3x_paths_file_the_guard_cannot_read_is_kept_and_said(game_dir, body):
    """The reader adds nothing from such a file, so the folder it meant is NOT protected, and the session start
    says so (paths.LEGACY_BROKEN, T8 fix round 2). Setup says the same and never moves it out of sight."""
    legacy, _ = _legacy(game_dir)
    legacy.write_bytes(legacy.read_bytes() + body)
    before = legacy.read_bytes()
    r = run_setup(game_dir)
    assert r.returncode == 0, r.stdout[-2000:]
    assert legacy.read_bytes() == before and not _retired(game_dir), r.stdout[-2000:]
    sys.path.insert(0, str(REPO / "tools"))
    from skyguard import paths
    assert paths.LEGACY_BROKEN in r.stdout


def test_a_3x_paths_file_differing_only_in_a_folder_the_guard_does_not_protect_is_retired(game_dir):
    """SKYRIM_LOADORDER_DIR is not a protection key: the union reader never adds it, so a 3.x file that differs only
    there adds nothing, and saying "the guard still protects" it would be false (T7 review M1)."""
    legacy, _ = _legacy(game_dir, "SKYRIM_LOADORDER_DIR='C:/Elsewhere/Skyrim VR'\n")
    r = run_setup(game_dir)
    assert not legacy.exists() and len(_retired(game_dir)) == 1, r.stdout[-2000:]
    assert "still protects" not in r.stdout


def test_a_3x_paths_file_is_kept_when_the_migration_tool_is_missing(game_dir):
    """No tools/migrate-3x.py = the migration has NOT run here: the 3.9 record it reads is kept (T7 review M5)."""
    legacy, before = _legacy(game_dir)
    (game_dir / "tools" / "migrate-3x.py").unlink()
    r = run_setup(game_dir)
    assert legacy.read_bytes() == before and not _retired(game_dir), r.stdout[-1500:]
    assert "kept until the 3.x migration has run" in r.stdout


def test_a_3x_paths_file_is_kept_while_the_migration_has_not_finished(game_dir):
    """migrate-3x reads the 3.x file as the 3.9 record: a failed migration keeps it for the retry."""
    legacy, before = _legacy(game_dir)
    (game_dir / "tools" / "migrate-3x.py").write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    r = run_setup(game_dir)
    assert legacy.read_bytes() == before and not _retired(game_dir), r.stdout[-1500:]
    assert "kept until the 3.x migration has run" in r.stdout


def test_no_interpreter_line_when_no_python_was_found(game_dir):
    from conftest import no_python_env, offers_python
    env = no_python_env()
    bash_dir = os.path.dirname(BASH)
    # Linux: bash lives in /usr/bin beside python3, and no_python_env's PATH already carries it (mirrored)
    path = env["PATH"] if offers_python(bash_dir) else env["PATH"] + os.pathsep + bash_dir
    r = run_setup(game_dir, env_overrides={"PATH": path})
    body = (game_dir / "skyrim-paths.env").read_text(encoding="utf-8")
    assert "SKYGUARD_PYTHON=''" in body.splitlines(), body
    assert "Wrote: skyrim-paths.env" in r.stdout, r.stdout[-1500:]


def test_an_exported_value_cannot_stand_in_for_a_missing_line(game_dir):
    """The read-back compares what the FILE says: a value exported by the caller must not mask a line the file lacks.

    v4.0.0 review L4a L4: the old assertion named a message setup.sh never prints, so it could not go red. Now a copy
    of setup.sh that forgets one line runs with no Python (so the guard's own reader cannot catch it) and with that
    very value exported: only the `unset` before the read-back makes the missing line visible."""
    from conftest import no_python_env, offers_python
    env = no_python_env()
    # On Linux bash shares /usr/bin with python3; the no-Python mirror already supplies bash and its tools.
    bash_dir = os.path.dirname(BASH)
    path = {"PATH": env["PATH"] if offers_python(bash_dir) else env["PATH"] + os.pathsep + bash_dir}
    r = run_setup(game_dir, env_overrides=path)
    assert "Wrote: skyrim-paths.env" in r.stdout, r.stdout[-1500:]
    line = next(ln for ln in (game_dir / "skyrim-paths.env").read_text(encoding="utf-8").splitlines()
                if ln.startswith("SKYRIM_CONFIG_DIR="))
    value = line.split("=", 1)[1].strip("'")
    assert value, "the fixture needs a detected config folder to export"
    script = game_dir / "setup.sh"
    text = script.read_bytes()
    forgot = b"    printf 'SKYRIM_CONFIG_DIR=%s\\n' \"$(sq \"$CONFIG_DIR\")\"\n"
    if forgot not in text:                       # a CRLF checkout
        forgot = forgot.replace(b"\n", b"\r\n")
    assert text.count(forgot) == 1
    script.write_bytes(text.replace(forgot, b""))
    r = run_setup(game_dir, env_overrides={**path, "SKYRIM_CONFIG_DIR": value})
    assert "skyrim-paths.env does NOT read back" in r.stdout, r.stdout[-1500:]
    assert "Wrote: skyrim-paths.env" not in r.stdout


def test_the_root_paths_file_is_not_tracked():
    assert "/skyrim-paths.env" in (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()


# ------------------------------------------------------------------------------ 3.x -> 4.0 hook-in (T9) --

def test_setup_runs_the_migration(game_dir):
    r = run_setup(game_dir)
    assert "migrate-3x: no 3.x files found" in r.stdout, r.stdout[:3000]


def _with_3x_snapshot(game: Path, config_value: str) -> None:
    """A pre-update snapshot of a 3.9.1 install whose CLAUDE.md names `config_value` as the INI folder."""
    fx = (REPO / "tests" / "fixtures" / "migrate-3x" / "v3.9.1" / "CLAUDE.stock.fixture").read_text(encoding="utf-8")
    assert "`D:/Docs/My Games/Skyrim VR/`" in fx
    snap = game / ".claude" / "backups" / "pre-update-20261001_120000"
    snap.mkdir(parents=True)
    (snap / "CLAUDE.md").write_text(fx.replace("D:/Docs/My Games/Skyrim VR", config_value),
                                    encoding="utf-8", newline="\n")


def test_a_path_the_3x_install_used_is_kept_when_it_is_a_folder(game_dir, tmp_path):
    mine = tmp_path / "My INIs" / "Skyrim VR"
    mine.mkdir(parents=True)
    value = game_root_win(mine)
    _with_3x_snapshot(game_dir, value)
    r = run_setup(game_dir, args=("--agent", "claude"))
    assert r.returncode == 0, r.stdout[-2000:]
    assert "CONFIG_DIR: your 3.x CLAUDE.md said %s" % value in r.stderr + r.stdout
    assert _sourced(game_dir / "skyrim-paths.env", "SKYRIM_CONFIG_DIR") == value
    line = next(l for l in (game_dir / "CLAUDE.md").read_text(encoding="utf-8").splitlines()
                if l.startswith("- **User INI configs**"))
    assert value in line, line


@pytest.mark.parametrize("why", ["not-a-folder", "prefer-detected"])
def test_a_3x_path_is_not_applied_when_it_is_not_a_folder_or_detection_is_preferred(game_dir, tmp_path, why):
    if why == "not-a-folder":
        value, env = "C:/Nowhere (NOT Default!)/Skyrim VR", {}
    else:
        (tmp_path / "real").mkdir()
        value, env = game_root_win(tmp_path / "real"), {"SKYRIM_PREFER_DETECTED": "1"}
    _with_3x_snapshot(game_dir, value)
    r = run_setup(game_dir, env_overrides=env, args=("--agent", "claude"))
    assert r.returncode == 0
    assert "CONFIG_DIR: your 3.x CLAUDE.md said %s -- not used" % value in r.stderr + r.stdout
    assert _sourced(game_dir / "skyrim-paths.env", "SKYRIM_CONFIG_DIR") != value


def test_a_failed_migration_is_said_and_setup_carries_on(game_dir):
    """Exit 1 from migrate-3x (a copy or write failed): loud, never fatal to the rest of setup."""
    (game_dir / "tools" / "migrate-3x.py").write_text("import sys\nprint('boom')\nsys.exit(1)\n", encoding="utf-8")
    r = run_setup(game_dir)
    assert r.returncode == 0
    assert "The 3.x migration reported a problem (exit 1" in r.stdout and configured(game_dir, "CLAUDE.md")


# --------------------------------------------------------------------------- runtime ranges and messages --

def _posix(p: Path) -> str:
    s = Path(p).as_posix()
    return "/" + s[0].lower() + s[2:] if WIN and s[1:2] == ":" else s


def _runtime_run(game, tmp_path, **stubs):
    """setup.sh with fake runtimes first on PATH. Under the contained launcher every bash puts the package-manager
    tripwires (which include `dotnet` and a `node` wrapper) in front of PATH again (BASH_ENV); the stubs go in
    front of THAT, and the tripwires stay on PATH for everything else."""
    stubs_dir = stub_bin(tmp_path, **stubs)
    env = {"PATH": path_with(stubs_dir, BIN)}
    trip = os.environ.get("HARNESS_TRIPWIRE_BIN")
    if trip:
        env["HARNESS_TRIPWIRE_BIN"] = _posix(stubs_dir) + ":" + trip
    return run_setup(game, shim_path=True, env_overrides=env)


@pytest.mark.parametrize("version,old", [("v20.11.1", True), ("v22.12.0", True), ("v24.20.0", False)])
def test_an_old_node_gets_a_plain_message(game_dir, tmp_path, version, old):
    r = _runtime_run(game_dir, tmp_path, node="echo %s" % version)
    assert "Found Node.js: %s" % version in r.stdout, "the stub was not the node setup ran: " + r.stdout[:2000]
    msg = "Node.js %s found; v4 needs Node 24 LTS for the xelib tools" % version
    assert (msg in r.stdout) is old, r.stdout[:3000]
    assert ("the xelib tools were tested with Node.js 24.19.0" in r.stdout) is (not old)
    if old:
        assert "f0f66c2a80c08a30a5ab5179ee9ea9e45f9b46289436a8cc87ff833b852db351" in r.stdout
        assert "an agent may not install this" in r.stdout


SDKS = {
    # listing (as `dotnet --list-sdks` prints it), whether SDK 10 counts as present, and the line that proves THIS
    # listing was read (without it a case expecting "not present" passes on any dotnet that answers nothing).
    "sdk9": ("9.0.318 [C:/Program Files/dotnet/sdk]", False,
             ".NET SDK 9.0.318 found; Spriggit 0.41.0 and AutoMod need the .NET 10 SDK"),
    "sdk8-and-9": ("8.0.424 [x]\\n9.0.318 [x]", False, ".NET SDK 9.0.318 found"),
    "sdk11-only": ("11.0.100 [x]", False, ".NET SDK 11.0.100 found"),
    "sdk10": ("9.0.318 [x]\\n10.0.401 [x]", True, ".NET 10 SDK present (10.0.401)"),
    "sdk10-other-band": ("10.0.100 [x]", True, "the toolkit was tested with SDK 10.0.401"),
    "runtime-only": ("", False, "dotnet runs but lists no SDK"),
}


@pytest.mark.parametrize("case", sorted(SDKS))
def test_the_dotnet_check_wants_exactly_major_10(game_dir, tmp_path, case):
    listing, ok, proof = SDKS[case]
    body = 'case "$1" in --list-sdks) printf "%b\\n" "' + listing + '" | sed "/^$/d" ;; *) echo 10.0.401 ;; esac'
    r = _runtime_run(game_dir, tmp_path, dotnet=body)
    assert (".NET 10 SDK present" in r.stdout) is ok, r.stdout[:4000]
    if not ok:
        assert "need the .NET 10 SDK" in r.stdout
        assert "winget install --id Microsoft.DotNet.SDK.10 --exact --version 10.0.401 --no-upgrade" in r.stdout
    assert proof in r.stdout, r.stdout[:4000]


@pytest.mark.parametrize("line,javac,expect", [
    ('openjdk version "11.0.2" 2019-01-15', True, "is too old for ReSaver CLI"),
    ('java version "1.8.0_202"', True, "is too old for ReSaver CLI"),
    ('openjdk version "21.0.12" 2026-07-15', False, "No javac"),
    ('openjdk version "21.0.12" 2026-07-15', True, None),
])
def test_the_jdk_check(game_dir, tmp_path, line, javac, expect):
    stubs = {"java": "echo '%s' >&2" % line}
    if javac:
        stubs["javac"] = "echo javac"
    else:
        stubs["javac"] = "exit 127"
    r = run_setup(game_dir, shim_path=True, env_overrides={
        "PATH": path_with(stub_bin(tmp_path, **stubs), BIN), "FAKE_WHICH_MISSING": "" if javac else "javac"})
    jdk = r.stdout.split("Checking for a JDK...")[1].split("Checking for a 3.x")[0]
    assert "Found Java: " + line in jdk, "the stub was not the java setup ran: " + jdk
    if expect:
        assert expect in jdk and "Microsoft.OpenJDK.21 --exact --version 21.0.12.8 --no-upgrade" in jdk, jdk
    else:
        assert "too old" not in jdk and "No javac" not in jdk, jdk


@pytest.mark.parametrize("version,expect", [
    ("3.12.10", "is inside the tested range 3.10-3.13"),
    ("3.13.1", "is inside the tested range 3.10-3.13"),
    ("3.14.0", "outside the tested range 3.10-3.13 (newer than tested)"),
])
def test_the_python_range_message(game_dir, version, expect):
    r = run_setup(game_dir, shim_path=True, env_overrides={
        "FAKE_PYTHON_EXE": "C:/Py/python.exe", "FAKE_PYTHON_VERSION": version})
    assert r.returncode == 0
    lines = [ln for ln in r.stdout.splitlines() if expect in ln]
    assert len(lines) == 1 and ("Python %s " % version) in lines[0], r.stdout[:3000]


def test_python_39_is_refused_before_configuring_shared_harness(game_dir):
    r = run_setup(game_dir, shim_path=True, env_overrides={
        "FAKE_PYTHON_EXE": "C:/Py/python.exe", "FAKE_PYTHON_VERSION": "3.9.18"})
    assert "safety hooks need Python 3.10 or newer" in r.stdout
    assert "Python 3.10+ not found" in r.stdout
    assert "Hooks will use: C:/Py/python.exe" not in r.stdout


# --------------------------------------------------------------------------------- the instruction budget --

def _budget():
    import importlib.util
    spec = importlib.util.spec_from_file_location("gen_agent_trees", REPO / "scripts" / "gen_agent_trees.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.INSTRUCTION_BUDGET


def test_setup_uses_the_generators_budget():
    assert "INSTRUCTION_BUDGET=%d\n" % _budget() in (REPO / "setup.sh").read_text(encoding="utf-8")


@pytest.mark.parametrize("over", [True, False])
def test_an_installed_agents_md_past_the_budget_is_warned_about(game_dir, over):
    """A long root can push the INSTALLED AGENTS.md past what Codex reads. Padded here, so the real expansion
    of this test's own root decides: over = 10 bytes short of the budget before filling, under = 1,500 short."""
    for doc in ("AGENTS.md", "CLAUDE.md"):
        p = game_dir / doc
        data = p.read_bytes()
        target = _budget() - (10 if over else 1500)
        p.write_bytes(data + b"\n<!--" + b"x" * (target - len(data) - 9) + b"-->\n")
        assert len(p.read_bytes()) == target
    r = run_setup(game_dir, args=("--agent", "all") if WIN else ("--agent", "claude"))
    assert r.returncode == 0
    warned_agents = "WARNING: AGENTS.md is " in r.stdout
    warned_claude = "WARNING: CLAUDE.md is " in r.stdout
    assert warned_claude is over, r.stdout[-3000:]
    assert warned_agents is (over and WIN), r.stdout[-3000:]
    if over and WIN:
        assert "Codex reads only the first 32768 bytes of AGENTS.md in a project it does not trust" in r.stdout


# ------------------------------------------------------------------------------------ the lock (opt-in) --

def test_the_lock_is_not_applied_without_lock(game_dir):
    """No masters here on purpose: a regression that applied the lock unasked must fail this test, not lock
    real files in a temp folder nobody unlocks (a lock attempt with no Data/ is refused, and said)."""
    r = run_setup(game_dir)
    assert r.returncode == 0, r.stdout[-1500:]
    assert "Layer 2 lock: OFF (the default)" in r.stdout
    assert "Applying the Layer 2 lock" not in r.stdout
    assert not (game_dir / ".skyguard" / "lock.json").exists()


def test_lock_runs_the_lock_and_a_failure_is_exit_1(game_dir):
    """No Data/ here: the lock's own refusal must reach the user, and the run must not read as a success."""
    r = run_setup(game_dir, args=("--lock",))
    assert r.returncode == 1, r.stdout[-1500:]
    assert "Applying the Layer 2 lock (--lock)" in r.stdout
    assert ("No Data/ folder under the install root" in r.stdout or "unsupported on this platform" in r.stdout), \
        r.stdout[-1500:]
    assert "LOCK NOT (fully) APPLIED" in r.stdout


def _scrub_lock(root: Path):
    sys32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    sid = subprocess.run([str(sys32 / "whoami.exe"), "/user", "/fo", "csv", "/nh"], capture_output=True,
                         text=True).stdout.strip().split(",")[1].strip('"')
    subprocess.run([str(sys32 / "icacls.exe"), str(root), "/remove:d", "*" + sid, "/T", "/C"], capture_output=True)
    subprocess.run([str(sys32 / "attrib.exe"), "-R", os.path.join(str(root), "*"), "/S", "/D"], capture_output=True)


@windows_only
def test_lock_applies_the_lock_to_the_vanilla_masters(tmp_path):
    game = make_game(tmp_path)
    (game / "Data").mkdir()
    for n in ("Skyrim.esm", "Update.esm"):
        (game / "Data" / n).write_bytes(os.urandom(256))
    try:
        r = run_setup(game, args=("--lock",), env_overrides={"SKYGUARD_LOCK_SANDBOX": str(tmp_path)})
        assert r.returncode == 0, r.stdout[-2000:]
        assert "The lock is on." in r.stdout
        assert os.stat(str(game / "Data" / "Skyrim.esm")).st_file_attributes & 1, "Skyrim.esm is not read-only"
        assert (game / ".skyguard" / "lock.json").is_file()
    finally:
        _scrub_lock(game / "Data")
    assert os.access(str(game / "Data" / "Skyrim.esm"), os.W_OK)


# ------------------------------------------------------------------------- non-ASCII and `&` in paths --

UTF8_DOCS = "C:/Users/testuser/Dokumente \u2013 M\u00fcller"


def _powershell(tmp_path, honour_utf8=True):
    """A `powershell` that answers like Windows PowerShell piped: the OEM code page (cp437: u-umlaut = 0x81, no
    en dash), unless the command sets [Console]::OutputEncoding to UTF-8 and `honour_utf8` is on."""
    utf8 = UTF8_DOCS.encode("utf-8")
    oem = "C:/Users/testuser/Dokumente M\u00fcller".encode("cp437")
    def esc(b):
        return "".join("\\x%02x" % c for c in b)
    body = ('case "$*" in *"OutputEncoding=[Text.Encoding]::UTF8"*) %s ;; *) printf \'%s\\r\\n\' ;; esac'
            % (("printf '%s\\r\\n'" % esc(utf8)) if honour_utf8 else ("printf '%s\\r\\n'" % esc(oem)), esc(oem)))
    return stub_bin(tmp_path, powershell=body)


def _cfg(game):
    sys.path.insert(0, str(REPO / "tools"))
    from skyguard import paths
    return paths.read_config(game / "skyrim-paths.env", {})


def test_a_non_ascii_documents_folder_reaches_the_paths_file_as_utf8(game_dir, tmp_path):
    """T7 review C1: piped, PowerShell answered in cp437; the guard's UTF-8 reader then refused EVERY command while
    setup printed "Wrote:". The probe now asks for UTF-8, and the file is read back with the guard's own reader."""
    r = run_setup(game_dir, env_overrides={"PATH": path_with(_powershell(tmp_path))}, args=("--agent", "claude"))
    assert r.returncode == 0, r.stdout[-2500:]
    assert "Wrote: skyrim-paths.env" in r.stdout
    cfg = _cfg(game_dir)
    assert cfg.malformed == () and cfg.values("SKYRIM_CONFIG_DIR") == [UTF8_DOCS + "/My Games/Skyrim VR"]
    assert UTF8_DOCS + "/My Games/Skyrim VR/" in (game_dir / "CLAUDE.md").read_text(encoding="utf-8")


def test_a_paths_file_the_guard_cannot_read_is_a_loud_failure(game_dir, tmp_path):
    """If the bytes are still not UTF-8 (a PowerShell that ignores the request), setup must not print success."""
    r = run_setup(game_dir, env_overrides={"PATH": path_with(_powershell(tmp_path, honour_utf8=False))},
                  args=("--agent", "claude"))
    assert r.returncode == 1, r.stdout[-2500:]
    assert "Wrote: skyrim-paths.env" not in r.stdout
    assert "does NOT read back as written" in r.stdout and "REFUSES EVERY COMMAND" in r.stdout


def test_an_ampersand_in_the_root_is_filled_in_once_and_rerun_changes_nothing(tmp_path):
    """T7 review I2: `&` in sed's replacement re-inserted the placeholder, so CLAUDE.md grew on every run and setup
    called a configured install NOT CONFIGURED."""
    game = make_game(tmp_path, name="Skyrim & Mods VR")
    r = run_setup(game, args=("--agent", "claude"))
    assert r.returncode == 0, r.stdout[-2000:]
    assert configured(game, "CLAUDE.md") and "Claude Code: CONFIGURED" in r.stdout
    line = next(l for l in (game / "CLAUDE.md").read_text(encoding="utf-8").splitlines() if l.startswith("- **Game root"))
    assert line.count("Skyrim & Mods VR") == 1, line
    first = tree(game)
    assert run_setup(game, args=("--agent", "claude")).returncode == 0
    assert tree(game) == first


@windows_only
def test_a_stale_hooks_json_new_is_removed_even_when_nothing_renders(game_dir):
    """No Python: nothing is rendered, and a leftover hooks.json.new from an interrupted run is still not left
    lying beside the definitions (T7 review M6)."""
    from conftest import no_python_env
    (game_dir / ".codex" / "hooks.json.new").write_text("{\"stale\": true}", encoding="utf-8")
    env = no_python_env()
    run_setup(game_dir, env_overrides={"PATH": env["PATH"] + os.pathsep + os.path.dirname(BASH)},
              args=("--agent", "codex"))
    assert not (game_dir / ".codex" / "hooks.json.new").exists()
