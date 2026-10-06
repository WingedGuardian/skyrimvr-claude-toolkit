"""tools/automod-cli.sh -- AutoMod runs on the .NET 10 SDK, the toolkit's only .NET requirement.

Upstream AutoMod (v1.11.2) targets net8.0 in Directory.Build.props and ships no global.json. v4 does not
ask users for a second SDK, so `--rebuild` pins SDK 10 in tools/automod/global.json, retargets the clone
to net10.0 (a recorded local patch), and builds the Cli project only (Setup is a WPF net8.0-windows app).
MEASURED 2026-10-05 (KNOWLEDGEBASE.md, "AutoMod on .NET 10"): a net8.0 build on a 10-only machine does not
start without DOTNET_ROLL_FORWARD, which is why the wrapper retargets instead of rolling forward.

These tests copy the wrapper next to a FAKE clone and a FAKE `dotnet` (AUTOMOD_DOTNET seam: the test
harness shadows `dotnet` by name) that records its argv.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from conftest import BASH, REPO

WRAPPER = REPO / "tools" / "automod-cli.sh"
PROPS_NET8 = ("<Project>\n  <PropertyGroup>\n    <TargetFramework>net8.0</TargetFramework>\n"
              "    <Nullable>enable</Nullable>\n  </PropertyGroup>\n</Project>\n")


def _layout(tmp_path: Path, props: str = PROPS_NET8) -> Path:
    tools = tmp_path / "tk" / "tools"
    cli = tools / "automod" / "src" / "SpookysAutomod.Cli"
    cli.mkdir(parents=True)
    (cli / "SpookysAutomod.Cli.csproj").write_text("<Project/>\n", encoding="utf-8")
    (tools / "automod" / "Directory.Build.props").write_text(props, encoding="utf-8", newline="\n")
    (tools / "automod-cli.sh").write_bytes(WRAPPER.read_bytes())
    log = tmp_path / "dotnet-argv.txt"
    fake = tmp_path / "dotnet"
    fake.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "{}"\necho "fake-dotnet $*"\n'.format(
        str(log).replace("\\", "/")), encoding="utf-8", newline="\n")
    return tools


def _run(tmp_path: Path, tools: Path, *args: str):
    env = dict(os.environ)
    env["AUTOMOD_DOTNET"] = str(tmp_path / "dotnet").replace("\\", "/")
    return subprocess.run([BASH, str(tools / "automod-cli.sh"), *args], capture_output=True, text=True,
                          env=env, timeout=120)


def _calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "dotnet-argv.txt"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_rebuild_pins_the_net10_sdk_not_8(tmp_path):
    tools = _layout(tmp_path)
    p = _run(tmp_path, tools, "--rebuild")
    assert p.returncode == 0, p.stdout + p.stderr
    gj = (tools / "automod" / "global.json").read_text(encoding="utf-8")
    assert '"10.0.100"' in gj and '"latestFeature"' in gj, gj
    assert "8.0" not in gj, gj


def test_rebuild_retargets_the_clone_to_net10(tmp_path):
    tools = _layout(tmp_path)
    p = _run(tmp_path, tools, "--rebuild")
    assert p.returncode == 0, p.stdout + p.stderr
    props = (tools / "automod" / "Directory.Build.props").read_text(encoding="utf-8")
    assert "<TargetFramework>net10.0</TargetFramework>" in props and "net8.0" not in props, props
    assert "<Nullable>enable</Nullable>" in props, "the rest of upstream's props must survive"


def test_rebuild_builds_the_cli_project_only(tmp_path):
    tools = _layout(tmp_path)
    _run(tmp_path, tools, "--rebuild")
    builds = [c for c in _calls(tmp_path) if c.startswith("build ")]
    assert len(builds) == 1, _calls(tmp_path)
    assert "SpookysAutomod.Cli" in builds[0] and "-c Release" in builds[0], builds
    assert ".sln" not in builds[0] and "Setup" not in builds[0], builds


def test_rebuild_twice_is_idempotent(tmp_path):
    tools = _layout(tmp_path)
    assert _run(tmp_path, tools, "--rebuild").returncode == 0
    assert _run(tmp_path, tools, "--rebuild").returncode == 0
    props = (tools / "automod" / "Directory.Build.props").read_text(encoding="utf-8")
    assert props.count("<TargetFramework>net10.0</TargetFramework>") == 1, props


def test_rebuild_refuses_a_target_it_does_not_recognise(tmp_path):
    """Upstream moved to something we never tested: say so, do not build a guess."""
    tools = _layout(tmp_path, PROPS_NET8.replace("net8.0", "net9.0"))
    p = _run(tmp_path, tools, "--rebuild")
    assert p.returncode != 0, p.stdout
    assert "net9.0" in p.stderr or "TargetFramework" in p.stderr, p.stderr
    assert not [c for c in _calls(tmp_path) if c.startswith("build ")], _calls(tmp_path)


def test_rebuild_without_a_clone_says_how_to_get_one(tmp_path):
    tools = tmp_path / "tk" / "tools"
    tools.mkdir(parents=True)
    (tools / "automod-cli.sh").write_bytes(WRAPPER.read_bytes())
    p = _run(tmp_path, tools, "--rebuild")
    assert p.returncode != 0
    assert "git clone" in p.stderr and "v1.11.2" in p.stderr, p.stderr


def test_a_run_uses_the_net10_build(tmp_path):
    tools = _layout(tmp_path)
    dll = tools / "automod" / "src" / "SpookysAutomod.Cli" / "bin" / "Release" / "net10.0" / "spookys-automod.dll"
    dll.parent.mkdir(parents=True)
    dll.write_bytes(b"MZ")
    p = _run(tmp_path, tools, "esp", "info", "x.esp", "--json")
    assert p.returncode == 0, p.stdout + p.stderr
    call = _calls(tmp_path)[-1]
    assert "/net10.0/spookys-automod.dll esp info x.esp --json" in call, call


def test_an_old_net8_build_is_reported_stale_not_run(tmp_path):
    tools = _layout(tmp_path)
    dll = tools / "automod" / "src" / "SpookysAutomod.Cli" / "bin" / "Release" / "net8.0" / "spookys-automod.dll"
    dll.parent.mkdir(parents=True)
    dll.write_bytes(b"MZ")
    p = _run(tmp_path, tools, "esp", "info", "x.esp")
    assert p.returncode != 0
    assert "net8.0" in p.stderr and "--rebuild" in p.stderr, p.stderr
    assert not _calls(tmp_path), "a net8.0 build cannot start on a .NET 10-only machine; do not launch it"
