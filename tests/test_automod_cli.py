"""tools/automod-cli.sh -- AutoMod runs on the .NET 10 SDK, built from exactly the commit the toolkit tested.

Upstream AutoMod (v1.11.2) targets net8.0 in Directory.Build.props and ships no global.json. v4 does not
ask users for a second SDK, so `--rebuild` pins SDK 10 in tools/automod/global.json, retargets the clone
to net10.0 (a recorded local patch), and builds the Cli project only (Setup is a WPF net8.0-windows app).
MEASURED 2026-10-05 (KNOWLEDGEBASE.md, "AutoMod on .NET 10"): a net8.0 build on a 10-only machine does not
start without DOTNET_ROLL_FORWARD, which is why the wrapper retargets instead of rolling forward.

Pins mean exactly what was tested, so `--rebuild` first puts the clone on the pinned commit: it checks it
out when the clone is clean (undoing only its OWN one-line patch), and stops -- touching nothing -- when the
user has local changes. Every v3 user cloned unpinned, so "an existing clone at an older commit" is the
normal upgrade case, not an edge. `--built` reports the commit the DLL was built from (toolchain-check uses it).

These tests build a real git clone in tmp (two commits: an "old" one and the "pinned" one, named to the
wrapper through its AUTOMOD_TEST_PIN_COMMIT seam, which it honours only with TOOLKIT_TEST_MODE=1) and a FAKE `dotnet` (AUTOMOD_DOTNET seam: the test
harness shadows `dotnet` by name) that records its argv and creates the DLL a real build would.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from conftest import BASH, REPO

WRAPPER = REPO / "tools" / "automod-cli.sh"
PROPS_NET8 = ("<Project>\n  <PropertyGroup>\n    <TargetFramework>net8.0</TargetFramework>\n"
              "    <Nullable>enable</Nullable>\n  </PropertyGroup>\n</Project>\n")
DLL_REL = Path("src") / "SpookysAutomod.Cli" / "bin" / "Release" / "net10.0" / "spookys-automod.dll"


def _git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "core.autocrlf=false",
                        "-C", str(repo), *args], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def _layout(tmp_path: Path, props: str = PROPS_NET8, at: str = "pinned"):
    """A toolkit with a git clone of 'AutoMod' holding commits old -> pinned. Returns (tools, pinned_sha, old_sha)."""
    tools = tmp_path / "tk" / "tools"
    am = tools / "automod"
    cli = am / "src" / "SpookysAutomod.Cli"
    cli.mkdir(parents=True)
    (cli / "SpookysAutomod.Cli.csproj").write_text("<Project/>\n", encoding="utf-8", newline="\n")
    (am / "Directory.Build.props").write_text(props, encoding="utf-8", newline="\n")
    (am / "README.md").write_text("old\n", encoding="utf-8", newline="\n")
    _git(am, "init", "-q")
    _git(am, "add", "-A")
    _git(am, "commit", "-q", "-m", "old")
    old = _git(am, "rev-parse", "HEAD")
    (am / "README.md").write_text("pinned\n", encoding="utf-8", newline="\n")
    _git(am, "commit", "-q", "-am", "pinned")
    pinned = _git(am, "rev-parse", "HEAD")
    if at == "old":
        _git(am, "checkout", "-q", "--detach", old)
    (tools / "automod-cli.sh").write_bytes(WRAPPER.read_bytes())
    log = tmp_path / "dotnet-argv.txt"
    dll = (am / DLL_REL).as_posix()
    (tmp_path / "dotnet").write_text(
        '#!/bin/bash\nprintf "%s\\n" "$*" >> "{}"\n'
        'if [ "$1" = build ]; then mkdir -p "$(dirname "{}")"; printf MZ > "{}"; fi\n'
        'echo "fake-dotnet $*"\n'.format(log.as_posix(), dll, dll), encoding="utf-8", newline="\n")
    return tools, pinned, old


def _run(tmp_path: Path, tools: Path, pinned: str, *args: str):
    env = dict(os.environ)
    env["AUTOMOD_DOTNET"] = (tmp_path / "dotnet").as_posix()
    env["AUTOMOD_TEST_PIN_COMMIT"] = pinned
    env["TOOLKIT_TEST_MODE"] = "1"  # the pin override is honoured only with this (re-review N1)
    return subprocess.run([BASH, str(tools / "automod-cli.sh"), *args], capture_output=True, text=True,
                          env=env, timeout=120)


def _calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "dotnet-argv.txt"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _builds(tmp_path: Path) -> list[str]:
    return [c for c in _calls(tmp_path) if c.startswith("build ")]


# --- .NET 10 build -------------------------------------------------------------------------------

def test_rebuild_pins_the_net10_sdk_not_8(tmp_path):
    tools, pin, _ = _layout(tmp_path)
    p = _run(tmp_path, tools, pin, "--rebuild")
    assert p.returncode == 0, p.stdout + p.stderr
    gj = (tools / "automod" / "global.json").read_text(encoding="utf-8")
    assert '"10.0.100"' in gj and '"latestFeature"' in gj, gj
    assert "8.0" not in gj, gj


def test_rebuild_retargets_the_clone_to_net10(tmp_path):
    tools, pin, _ = _layout(tmp_path)
    p = _run(tmp_path, tools, pin, "--rebuild")
    assert p.returncode == 0, p.stdout + p.stderr
    props = (tools / "automod" / "Directory.Build.props").read_text(encoding="utf-8")
    assert "<TargetFramework>net10.0</TargetFramework>" in props and "net8.0" not in props, props
    assert "<Nullable>enable</Nullable>" in props, "the rest of upstream's props must survive"


def test_rebuild_builds_the_cli_project_only(tmp_path):
    tools, pin, _ = _layout(tmp_path)
    _run(tmp_path, tools, pin, "--rebuild")
    builds = _builds(tmp_path)
    assert len(builds) == 1, _calls(tmp_path)
    assert "SpookysAutomod.Cli" in builds[0] and "-c Release" in builds[0], builds
    assert ".sln" not in builds[0] and "Setup" not in builds[0], builds


def test_rebuild_twice_is_idempotent(tmp_path):
    tools, pin, _ = _layout(tmp_path)
    assert _run(tmp_path, tools, pin, "--rebuild").returncode == 0
    assert _run(tmp_path, tools, pin, "--rebuild").returncode == 0
    props = (tools / "automod" / "Directory.Build.props").read_text(encoding="utf-8")
    assert props.count("<TargetFramework>net10.0</TargetFramework>") == 1, props


def test_rebuild_refuses_a_target_it_does_not_recognise_and_writes_nothing(tmp_path):
    """Upstream moved to something we never tested: say so, build nothing, and leave global.json alone."""
    tools, pin, _ = _layout(tmp_path, PROPS_NET8.replace("net8.0", "net9.0"))
    p = _run(tmp_path, tools, pin, "--rebuild")
    assert p.returncode != 0, p.stdout
    assert "net9.0" in p.stderr or "TargetFramework" in p.stderr, p.stderr
    assert not _builds(tmp_path), _calls(tmp_path)
    assert not (tools / "automod" / "global.json").exists(), "validate first, then write"


def test_a_failed_build_says_the_first_build_needs_internet(tmp_path):
    tools, pin, _ = _layout(tmp_path)
    (tmp_path / "dotnet").write_text('#!/bin/bash\necho "error NU1301: Unable to load the service index"\nexit 1\n',
                                     encoding="utf-8", newline="\n")
    p = _run(tmp_path, tools, pin, "--rebuild")
    assert p.returncode != 0
    assert "internet" in p.stderr, p.stderr


def test_rebuild_without_a_clone_says_how_to_get_one(tmp_path):
    tools = tmp_path / "tk" / "tools"
    tools.mkdir(parents=True)
    (tools / "automod-cli.sh").write_bytes(WRAPPER.read_bytes())
    p = _run(tmp_path, tools, "0" * 40, "--rebuild")
    assert p.returncode != 0
    assert "git clone" in p.stderr and "v1.11.2" in p.stderr, p.stderr


def test_a_run_uses_the_net10_build(tmp_path):
    tools, pin, _ = _layout(tmp_path)
    dll = tools / "automod" / DLL_REL
    dll.parent.mkdir(parents=True)
    dll.write_bytes(b"MZ")
    p = _run(tmp_path, tools, pin, "esp", "info", "x.esp", "--json")
    assert p.returncode == 0, p.stdout + p.stderr
    call = _calls(tmp_path)[-1]
    assert "/net10.0/spookys-automod.dll esp info x.esp --json" in call, call


def test_an_old_net8_build_is_reported_stale_not_run(tmp_path):
    tools, pin, _ = _layout(tmp_path)
    dll = tools / "automod" / "src" / "SpookysAutomod.Cli" / "bin" / "Release" / "net8.0" / "spookys-automod.dll"
    dll.parent.mkdir(parents=True)
    dll.write_bytes(b"MZ")
    p = _run(tmp_path, tools, pin, "esp", "info", "x.esp")
    assert p.returncode != 0
    assert "net8.0" in p.stderr and "--rebuild" in p.stderr, p.stderr
    assert not _calls(tmp_path), "a net8.0 build cannot start on a .NET 10-only machine; do not launch it"


# --- the pinned commit ---------------------------------------------------------------------------

def test_upgrade_a_clean_clone_at_an_older_commit_is_moved_to_the_pin(tmp_path):
    tools, pin, old = _layout(tmp_path, at="old")
    p = _run(tmp_path, tools, pin, "--rebuild")
    assert p.returncode == 0, p.stdout + p.stderr
    assert _git(tools / "automod", "rev-parse", "HEAD") == pin
    assert old[:7] in p.stdout + p.stderr, "say what it moved FROM"
    assert len(_builds(tmp_path)) == 1


def test_upgrade_undoes_only_its_own_patch_before_moving(tmp_path):
    """A v4 rebuild at an old commit left our one-line TFM patch behind; it must not block the move (M6)."""
    tools, pin, _ = _layout(tmp_path, at="old")
    props = tools / "automod" / "Directory.Build.props"
    props.write_text(PROPS_NET8.replace("net8.0", "net10.0"), encoding="utf-8", newline="\n")
    p = _run(tmp_path, tools, pin, "--rebuild")
    assert p.returncode == 0, p.stdout + p.stderr
    assert _git(tools / "automod", "rev-parse", "HEAD") == pin
    assert "<TargetFramework>net10.0</TargetFramework>" in props.read_text(encoding="utf-8")


def test_a_clone_with_user_changes_is_never_touched(tmp_path):
    tools, pin, old = _layout(tmp_path, at="old")
    readme = tools / "automod" / "README.md"
    readme.write_text("my own edit\n", encoding="utf-8", newline="\n")
    p = _run(tmp_path, tools, pin, "--rebuild")
    assert p.returncode != 0, p.stdout
    assert "README.md" in p.stderr and "v1.11.2" in p.stderr, p.stderr
    assert readme.read_text(encoding="utf-8") == "my own edit\n", "user changes must survive"
    assert _git(tools / "automod", "rev-parse", "HEAD") == old
    assert not _builds(tmp_path)
    assert not (tools / "automod" / "global.json").exists()


def test_a_user_edit_to_the_props_file_is_not_mistaken_for_our_patch(tmp_path):
    tools, pin, old = _layout(tmp_path, at="old")
    props = tools / "automod" / "Directory.Build.props"
    edited = PROPS_NET8.replace("net8.0", "net10.0").replace("enable", "disable")
    props.write_text(edited, encoding="utf-8", newline="\n")
    p = _run(tmp_path, tools, pin, "--rebuild")
    assert p.returncode != 0, p.stdout
    assert props.read_text(encoding="utf-8") == edited
    assert _git(tools / "automod", "rev-parse", "HEAD") == old


def test_built_reports_the_pinned_commit_after_a_rebuild(tmp_path):
    tools, pin, _ = _layout(tmp_path)
    assert _run(tmp_path, tools, pin, "--rebuild").returncode == 0
    p = _run(tmp_path, tools, pin, "--built")
    assert p.returncode == 0, p.stdout + p.stderr
    assert pin[:12] in p.stdout and "v1.11.2" in p.stdout, p.stdout


def test_built_without_a_marker_is_a_mismatch(tmp_path):
    """A DLL nobody built through --rebuild (a manual dotnet build, a v3 leftover) is of unknown commit."""
    tools, pin, _ = _layout(tmp_path)
    dll = tools / "automod" / DLL_REL
    dll.parent.mkdir(parents=True)
    dll.write_bytes(b"MZ")
    p = _run(tmp_path, tools, pin, "--built")
    assert p.returncode != 0
    assert "--rebuild" in p.stdout + p.stderr


def test_built_from_another_commit_is_a_mismatch(tmp_path):
    tools, pin, old = _layout(tmp_path)
    dll = tools / "automod" / DLL_REL
    dll.parent.mkdir(parents=True)
    dll.write_bytes(b"MZ")
    (dll.parent / "toolkit-built-from.txt").write_text(old + "\n", encoding="utf-8")
    p = _run(tmp_path, tools, pin, "--built")
    assert p.returncode != 0
    assert old[:12] in p.stdout + p.stderr and pin[:12] in p.stdout + p.stderr


def test_local_changes_stop_the_move_even_when_git_could_carry_them(tmp_path):
    """An edit to a file both commits share would ride along a checkout silently: a 'pinned' build that is not."""
    tools, pin, old = _layout(tmp_path, at="old")
    csproj = tools / "automod" / "src" / "SpookysAutomod.Cli" / "SpookysAutomod.Cli.csproj"
    csproj.write_text("<Project><!-- mine --></Project>\n", encoding="utf-8", newline="\n")
    p = _run(tmp_path, tools, pin, "--rebuild")
    assert p.returncode != 0, p.stdout
    assert _git(tools / "automod", "rev-parse", "HEAD") == old
    assert "mine" in csproj.read_text(encoding="utf-8")
    assert not _builds(tmp_path)


# --- fix round 2 (re-review N1-N4) -----------------------------------------------------------------
SHIPPED_PIN = "e1c34ea34cae4b9a1a547ea6a90c8a91412d689e"


def _built_dll(tools: Path, marker):
    dll = tools / "automod" / DLL_REL
    dll.parent.mkdir(parents=True, exist_ok=True)
    dll.write_bytes(b"MZ")
    if marker is not None:
        m = dll.parent / "toolkit-built-from.txt"
        m.write_text(marker + "\n", encoding="utf-8")
        os.utime(m, (dll.stat().st_mtime + 5,) * 2)
    return dll


def test_the_pin_override_is_ignored_without_the_test_flag(tmp_path):
    """N1: an AUTOMOD_TEST_PIN_COMMIT a user or agent happens to have set must not move the shipped pin."""
    tools, pin, _ = _layout(tmp_path)
    _built_dll(tools, pin)
    env = dict(os.environ)
    env.pop("TOOLKIT_TEST_MODE", None)
    env["AUTOMOD_TEST_PIN_COMMIT"] = pin
    p = subprocess.run([BASH, str(tools / "automod-cli.sh"), "--built"], capture_output=True, text=True,
                       env=env, timeout=120)
    assert p.returncode != 0, "the fixture commit must not count as pinned outside test mode:\n" + p.stdout
    assert SHIPPED_PIN[:12] in p.stdout, p.stdout
    assert "AUTOMOD_TEST_PIN_COMMIT" in p.stderr and "ignored" in p.stderr, p.stderr


def _detached_own_commit(tools: Path) -> str:
    am = tools / "automod"
    (am / "mine.txt").write_text("my work\n", encoding="utf-8", newline="\n")
    _git(am, "add", "mine.txt")
    _git(am, "commit", "-q", "-m", "my own commit on a detached HEAD")
    return _git(am, "rev-parse", "HEAD")


def test_a_detached_commit_of_the_users_own_is_never_moved(tmp_path):
    """N2: moving off a detached commit no branch or tag holds would leave it only in the reflog."""
    tools, pin, _ = _layout(tmp_path, at="old")
    mine = _detached_own_commit(tools)
    p = _run(tmp_path, tools, pin, "--rebuild")
    assert p.returncode != 0, p.stdout
    assert mine[:7] in p.stderr and "branch" in p.stderr, p.stderr
    assert _git(tools / "automod", "rev-parse", "HEAD") == mine
    assert not _builds(tmp_path)


def test_a_detached_commit_kept_on_a_branch_may_be_moved(tmp_path):
    tools, pin, _ = _layout(tmp_path, at="old")
    _detached_own_commit(tools)
    _git(tools / "automod", "branch", "keep-mine")
    p = _run(tmp_path, tools, pin, "--rebuild")
    assert p.returncode == 0, p.stdout + p.stderr
    assert _git(tools / "automod", "rev-parse", "HEAD") == pin


def test_a_dirty_build_marks_itself_and_is_not_the_pin(tmp_path):
    """N3: at the pinned commit with a local edit the build goes ahead, and says it is not what was tested."""
    tools, pin, _ = _layout(tmp_path)
    (tools / "automod" / "README.md").write_text("my local edit\n", encoding="utf-8", newline="\n")
    assert _run(tmp_path, tools, pin, "--rebuild").returncode == 0
    marker = (tools / "automod" / DLL_REL).parent / "toolkit-built-from.txt"
    assert "dirty" in marker.read_text(encoding="utf-8")
    p = _run(tmp_path, tools, pin, "--built")
    assert p.returncode != 0 and "local changes" in p.stdout, p.stdout


def test_our_own_patch_does_not_count_as_dirty(tmp_path):
    tools, pin, _ = _layout(tmp_path)
    assert _run(tmp_path, tools, pin, "--rebuild").returncode == 0
    marker = (tools / "automod" / DLL_REL).parent / "toolkit-built-from.txt"
    assert marker.read_text(encoding="utf-8").strip() == pin


def test_a_dll_newer_than_its_build_record_is_of_unknown_origin(tmp_path):
    """N3: a later manual `dotnet build` replaces the DLL and leaves the record behind."""
    tools, pin, _ = _layout(tmp_path)
    dll = _built_dll(tools, pin)
    os.utime(dll, (dll.stat().st_mtime + 60,) * 2)
    p = _run(tmp_path, tools, pin, "--built")
    assert p.returncode != 0 and "--rebuild" in p.stdout, p.stdout


def test_the_mismatch_message_names_the_commit_once_and_is_clean(tmp_path):
    """N4: the commit was printed twice, and a missing record leaked a `tr` error."""
    tools, pin, old = _layout(tmp_path)
    _built_dll(tools, old)
    p = _run(tmp_path, tools, pin, "--built")
    assert p.stdout.count(old[:12]) == 1 and old not in p.stdout, p.stdout
    ((tools / "automod" / DLL_REL).parent / "toolkit-built-from.txt").unlink()
    p = _run(tmp_path, tools, pin, "--built")
    assert p.returncode != 0 and p.stderr == "", p.stderr
