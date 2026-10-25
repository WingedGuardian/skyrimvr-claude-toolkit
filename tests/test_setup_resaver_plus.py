"""setup.sh's ReSaver Plus build-tools report (port task 10): detected versus tested, never an install.

Both twins the plan names: Build Tools present (at the tested versions, and at others) and absent; plus the non-Windows
answer. vswhere, the Visual Studio folder and cmake are stubs in tmp_path, reached through the TOOLKIT_TEST_MODE=1 seams
(SETUP_UNAME, SETUP_VSWHERE), so the real machine's toolchain never decides the result.
"""
from __future__ import annotations

import os
from pathlib import Path

from conftest import BIN, run_setup

SECTION = "Checking for the ReSaver Plus build tools"


def _posix(p: Path) -> str:
    s = str(p).replace("\\", "/")
    return "/" + s[0].lower() + s[2:] if len(s) > 1 and s[1] == ":" else s


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/bash\n" + body + "\n", encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return path


def _run(game_dir, tmp_path, vs=True, toolset="14.44.35207", cmake="3.31.6-msvc6", uname="MINGW64_NT-10.0"):
    stubs = tmp_path / "stubbin"
    stubs.mkdir(exist_ok=True)
    _script(stubs / "cmake", 'echo "cmake version %s"' % cmake)
    vsdir = tmp_path / "VS" / "BuildTools"
    if vs:
        (vsdir / "VC" / "Auxiliary" / "Build").mkdir(parents=True)
        (vsdir / "VC" / "Auxiliary" / "Build" / "Microsoft.VCToolsVersion.default.txt").write_text(toolset + "\n")
    answer = _posix(vsdir) if vs else ""
    vswhere = _script(tmp_path / "vswhere", 'case "$*" in *installationPath*) echo "%s" ;; '
                                            '*catalog_productDisplayVersion*) echo "17.14.39 (August 2026)" ;; esac'
                      % answer)
    env = {"PATH": os.pathsep.join([str(stubs), str(BIN), os.environ["PATH"]]), "TOOLKIT_TEST_MODE": "1",
           "SETUP_UNAME": uname, "SETUP_VSWHERE": _posix(vswhere)}
    trip = os.environ.get("HARNESS_TRIPWIRE_BIN")
    if trip:
        env["HARNESS_TRIPWIRE_BIN"] = _posix(stubs) + ":" + trip
    r = run_setup(game_dir, shim_path=True, env_overrides=env)
    assert SECTION in r.stdout, r.stdout[-3000:]
    return r.stdout.split(SECTION)[1].split("Checking for a 3.x")[0]


def test_tested_build_tools_are_reported_as_tested(game_dir, tmp_path):
    out = _run(game_dir, tmp_path)
    assert "Found Visual Studio Build Tools 17.14.39 with the C++ tools: MSVC toolset 14.44.35207, CMake 3.31.6-msvc6" in out
    assert "Tested with: Build Tools 17.14.39, MSVC toolset 14.44.35207, CMake 3.31.6." in out
    assert "differ from the tested" not in out and "python tools/resaver-plus/build.py" in out


def test_other_build_tools_versions_are_named_as_untested(game_dir, tmp_path):
    out = _run(game_dir, tmp_path, toolset="14.50.1", cmake="4.0.0")
    assert "MSVC toolset 14.50.1, CMake 4.0.0" in out and "differ from the tested versions" in out


def test_absent_build_tools_are_optional_and_named_with_the_tested_install(game_dir, tmp_path):
    out = _run(game_dir, tmp_path, vs=False)
    assert "Not found. That is fine" in out and "the default (Java) does" in out
    assert "winget install --id Microsoft.VisualStudio.2022.BuildTools --exact --version 17.14.39" in out
    assert "Found Visual Studio" not in out


def test_off_windows_it_points_at_the_java_backend(game_dir, tmp_path):
    out = _run(game_dir, tmp_path, uname="Linux")
    assert "Not Windows" in out and "Found Visual Studio" not in out and "winget" not in out
