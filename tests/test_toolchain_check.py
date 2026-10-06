"""tools/toolchain-check.sh -- the Spriggit clause: installed version must equal the pin.

The pin is `.config/dotnet-tools.json` (0.41.0, the version every YAML/serialize command in the
toolkit was written against; it ships only tools/net10.0, so it needs the .NET 10 SDK). On SDK 9 it
starts, prints a version, and cannot serialize -- the exact false green this script exists to catch.
An OLDER Spriggit (0.40.0, net9.0) against the 0.41.0 pin is a mismatch too: pins mean exactly the
tested version, not older and not newer.
These tests put a FAKE `spriggit` and a FAKE `dotnet` first on PATH and read the two spriggit lines.
Other tools in the script (python/node/java...) are real, so the overall RESULT is not asserted.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from conftest import BASH, REPO

TOOL = REPO / "tools" / "toolchain-check.sh"
PIN = json.loads((REPO / ".config" / "dotnet-tools.json").read_text(encoding="utf-8"))["tools"]["spriggit.cli"]["version"]


def _fake(tmp_path: Path, spriggit_version: str, sdks: list[str]) -> Path:
    b = tmp_path / "bin"
    b.mkdir()
    (b / "spriggit").write_text("#!/usr/bin/env bash\necho {}\n".format(spriggit_version), encoding="utf-8", newline="\n")
    sdk_lines = "\\n".join("{} [C:/fake/sdk]".format(s) for s in sdks)
    (b / "dotnet").write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "--list-sdks" ]; then printf "{}\\n"; exit 0; fi\n'
        'echo "{}"\n'.format(sdk_lines, sdks[-1] if sdks else "0.0.0"),
        encoding="utf-8", newline="\n")
    return b


def _run(tmp_path: Path, spriggit_version: str, sdks: list[str]):
    b = _fake(tmp_path, spriggit_version, sdks)
    env = dict(os.environ)
    env["PATH"] = str(b).replace("\\", "/") + os.pathsep + env.get("PATH", "")
    env["TOOLCHAIN_DOTNET"] = str(b / "dotnet").replace("\\", "/")  # the harness shadows `dotnet` by name
    p = subprocess.run([BASH, str(TOOL)], capture_output=True, text=True, env=env, timeout=120)
    return p.stdout


def _line(out: str, label: str) -> str:
    return next((ln for ln in out.splitlines() if label in ln), "")


def test_the_pin_is_0_41_0():
    assert PIN == "0.41.0"


def test_pinned_version_with_sdk_10_is_ok(tmp_path):
    out = _run(tmp_path, PIN, ["10.0.100"])
    assert " ok " in _line(out, "spriggit version"), out
    assert " ok " in _line(out, "spriggit entry point"), out
    assert "spriggit-version" not in out and "spriggit-entry-point" not in out


def test_pinned_version_on_sdk_9_cannot_work(tmp_path):
    out = _run(tmp_path, PIN, ["9.0.100"])
    assert " ok " in _line(out, "spriggit version"), out
    assert "CANNOT WORK" in _line(out, "spriggit entry point"), out
    assert "SDK 10+" in out and "Microsoft.DotNet.SDK.10" in out, out
    assert "RESULT: DEGRADED" in out
    assert "spriggit-entry-point" in out.split("RESULT:")[1]


def test_older_spriggit_against_the_pin_is_a_mismatch(tmp_path):
    out = _run(tmp_path, "0.40.0", ["9.0.100", "10.0.100"])
    assert "MISMATCH" in _line(out, "spriggit version"), out
    assert "0.40.0" in out and "0.41.0" in out
    assert " ok " in _line(out, "spriggit entry point"), out
    assert "RESULT: DEGRADED" in out
    assert "spriggit-version" in out.split("RESULT:")[1]


def test_newer_spriggit_with_sdk_10_still_fails_the_version_pin(tmp_path):
    out = _run(tmp_path, "0.42.0", ["10.0.100"])
    assert "MISMATCH" in _line(out, "spriggit version"), out
    assert " ok " in _line(out, "spriggit entry point"), out
    assert "spriggit-version" in out.split("RESULT:")[1]


def test_pinned_version_with_only_sdk_8_cannot_work(tmp_path):
    out = _run(tmp_path, PIN, ["8.0.400"])
    assert " ok " in _line(out, "spriggit version"), out
    assert "CANNOT WORK" in _line(out, "spriggit entry point"), out
    assert "spriggit-entry-point" in out.split("RESULT:")[1]


def test_a_version_suffix_does_not_hide_a_mismatch(tmp_path):
    out = _run(tmp_path, "0.41.1+abc123", ["10.0.100"])
    assert "MISMATCH" in _line(out, "spriggit version"), out


# --- Our wrappers: "it started" is not "it works" -------------------------------------------------
# A wrapper that prints "Built DLL not found" or "ReSaver.jar not found" used to be reported "ok":
# the check confirmed the WRAPPER started, never that it reached its dependency. Each wrapper now
# has to print what a working one prints (AutoMod's help banner; our ReSaver driver's own reply).
# These run the script against a FAKE toolkit root (CLAUDE_PROJECT_DIR) holding real copies of the
# wrappers, with fakes for dotnet/java. Fakes use #!/bin/bash so a shadowed `bash` cannot hijack them.

def _toolkit(tmp_path: Path) -> Path:
    root = tmp_path / "tk"
    (root / "tools").mkdir(parents=True)
    (root / ".config").mkdir()
    (root / ".config" / "dotnet-tools.json").write_bytes((REPO / ".config" / "dotnet-tools.json").read_bytes())
    for name in ("automod-cli.sh", "resaver-cli.sh", "hook-canary.sh"):
        (root / "tools" / name).write_bytes((REPO / "tools" / name).read_bytes())
    (root / "tools" / "resaver-cli").mkdir()
    (root / "tools" / "resaver-cli" / "ResaverCLI.java").write_bytes(
        (REPO / "tools" / "resaver-cli" / "ResaverCLI.java").read_bytes())
    return root


def _script(path: Path, body: str) -> None:
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8", newline="\n")


def _bins(tmp_path: Path) -> Path:
    b = tmp_path / "wbin"
    b.mkdir(exist_ok=True)
    # what AutoMod's real CLI prints for --help (RootCommand description at v1.11.2)
    _script(b / "dotnet", 'if [ "$1" = "--list-sdks" ]; then echo "10.0.100 [C:/fake]"; exit 0; fi\n'
                          'if [ "$1" = "--version" ]; then echo 10.0.100; exit 0; fi\n'
                          'echo "Description:"; echo "  Spooky\'s AutoMod Toolkit - LLM-friendly Skyrim mod creation"\n')
    # what our ResaverCLI driver prints for an op it was not given (tools/resaver-cli/ResaverCLI.java)
    _script(b / "java", 'if [ "$1" = "-version" ]; then echo \'openjdk version "21.0.4"\' >&2; exit 0; fi\n'
                        'echo \'{"ok":false,"error":"need <op> <save> [...]"}\'\n')
    _script(b / "javac", "exit 1\n")
    return b


def _run_wrappers(tmp_path: Path, root: Path, shadow_bash: bool = False):
    b = _bins(tmp_path)
    path = [str(b).replace("\\", "/")]
    if shadow_bash:  # a bare `bash` that is not Git's -- WSL's launcher on a stock Windows box
        s = tmp_path / "shadow"
        s.mkdir()
        _script(s / "bash", 'echo "WSL: no installed distributions"; exit 1\n')
        path.insert(0, str(s).replace("\\", "/"))
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(path + [env.get("PATH", "")])
    env["CLAUDE_PROJECT_DIR"] = str(root).replace("\\", "/")
    env["TOOLCHAIN_DOTNET"] = str(b / "dotnet").replace("\\", "/")
    env["AUTOMOD_DOTNET"] = str(b / "dotnet").replace("\\", "/")
    env["AUTOMOD_TEST_PIN_COMMIT"] = PIN_SHA
    env["TOOLKIT_TEST_MODE"] = "1"
    p = subprocess.run([BASH, str(TOOL)], capture_output=True, text=True, env=env, timeout=120)
    return p.stdout


def _failed(out: str) -> str:
    return out.split("RESULT:")[1] if "RESULT:" in out else ""


PIN_SHA = "a" * 40  # the wrapper's pinned commit, named through its AUTOMOD_TEST_PIN_COMMIT seam


def _automod_built(root: Path, tfm: str = "net10.0", commit: str = PIN_SHA) -> None:
    d = root / "tools" / "automod" / "src" / "SpookysAutomod.Cli" / "bin" / "Release" / tfm
    d.mkdir(parents=True)
    (d / "spookys-automod.dll").write_bytes(b"MZ")
    if commit:
        (d / "toolkit-built-from.txt").write_text(commit + "\n", encoding="utf-8")


def _resaver_installed(root: Path, compiled: bool = True, lib: bool = True) -> None:
    rs = root / "tools" / "resaver-cli"
    (rs / "ReSaver.jar").write_bytes(b"PK")
    if lib:
        (rs / "lib").mkdir()
        (rs / "lib" / "lz4-java.jar").write_bytes(b"PK")
    if compiled:
        cls = rs / "ResaverCLI.class"
        cls.write_bytes(b"\xca\xfe")
        os.utime(cls, (os.stat(rs / "ResaverCLI.java").st_mtime + 60,) * 2)


def test_automod_cloned_but_not_built_is_not_ok(tmp_path):
    root = _toolkit(tmp_path)
    (root / "tools" / "automod" / "src" / "SpookysAutomod.Cli").mkdir(parents=True)
    out = _run_wrappers(tmp_path, root)
    line = _line(out, "automod-cli")
    assert " ok " not in line and "Built DLL not found" in line, out
    assert "automod-cli" in _failed(out), out


def test_automod_left_on_an_old_net8_build_is_not_ok(tmp_path):
    root = _toolkit(tmp_path)
    _automod_built(root, "net8.0")
    out = _run_wrappers(tmp_path, root)
    assert " ok " not in _line(out, "automod-cli"), out
    assert "automod-cli" in _failed(out), out


def test_automod_not_cloned_is_optional_not_ok(tmp_path):
    root = _toolkit(tmp_path)
    out = _run_wrappers(tmp_path, root)
    line = _line(out, "automod-cli")
    assert "not installed (optional)" in line and " ok " not in line, out
    assert "automod-cli" not in _failed(out), out


def test_automod_built_is_ok(tmp_path):
    root = _toolkit(tmp_path)
    _automod_built(root)
    out = _run_wrappers(tmp_path, root)
    assert " ok " in _line(out, "automod-cli"), out
    assert "automod-cli" not in _failed(out), out


def test_resaver_without_its_jar_is_optional_not_ok(tmp_path):
    root = _toolkit(tmp_path)
    out = _run_wrappers(tmp_path, root)
    line = _line(out, "resaver-cli")
    assert "not installed (optional)" in line and " ok " not in line, out
    assert "resaver-cli" not in _failed(out), out


def test_resaver_whose_driver_cannot_compile_is_not_ok(tmp_path):
    root = _toolkit(tmp_path)
    _resaver_installed(root, compiled=False)
    out = _run_wrappers(tmp_path, root)
    line = _line(out, "resaver-cli")
    assert " ok " not in line and "compile failed" in line, out
    assert "resaver-cli" in _failed(out), out


def test_resaver_reaching_its_driver_is_ok(tmp_path):
    root = _toolkit(tmp_path)
    _resaver_installed(root)
    out = _run_wrappers(tmp_path, root)
    assert " ok " in _line(out, "resaver-cli"), out
    assert "resaver-cli" not in _failed(out), out


def test_wrappers_run_under_this_bash_not_a_bare_bash_on_path(tmp_path):
    """On Windows a bare `bash` can be WSL's launcher; the wrappers must run under the bash running the check."""
    root = _toolkit(tmp_path)
    _automod_built(root)
    _resaver_installed(root)
    out = _run_wrappers(tmp_path, root, shadow_bash=True)
    assert " ok " in _line(out, "automod-cli"), out
    assert " ok " in _line(out, "resaver-cli"), out


def test_automod_built_from_the_pinned_commit_says_which(tmp_path):
    root = _toolkit(tmp_path)
    _automod_built(root)
    out = _run_wrappers(tmp_path, root)
    line = _line(out, "automod version")
    assert " ok " in line and PIN_SHA[:12] in line and "v1.11.2" in line, out
    assert "automod-version" not in _failed(out), out


def test_automod_built_from_another_commit_is_degraded(tmp_path):
    """Every v3 user cloned unpinned: a DLL from another commit runs, and is not what the toolkit tested."""
    root = _toolkit(tmp_path)
    _automod_built(root, commit="b" * 40)
    out = _run_wrappers(tmp_path, root)
    assert " ok " in _line(out, "automod-cli"), out
    line = _line(out, "automod version")
    assert "MISMATCH" in line and "b" * 12 in line, out
    assert "automod-version" in _failed(out), out


def test_automod_built_without_a_marker_is_degraded(tmp_path):
    root = _toolkit(tmp_path)
    _automod_built(root, commit="")
    out = _run_wrappers(tmp_path, root)
    assert "MISMATCH" in _line(out, "automod version"), out
    assert "automod-version" in _failed(out), out


def test_resaver_whose_lib_was_removed_is_not_ok(tmp_path):
    """--help exits before ReSaver loads a class, so a compiled driver answers even with lib/ gone (review M1)."""
    root = _toolkit(tmp_path)
    _resaver_installed(root, lib=False)
    out = _run_wrappers(tmp_path, root)
    line = _line(out, "resaver-cli")
    assert " ok " not in line and "lib/" in line, out
    assert "resaver-cli" in _failed(out), out


# --- Spriggit installed from the repo's tool manifest (`dotnet tool restore`) -----------------------
# installing-tools documents `dotnet tool restore`, which installs exactly the pinned version but puts no
# `spriggit` on PATH. Review I3: toolchain-check read that healthy install as "not installed".

def _path_without_spriggit() -> str:
    import shutil
    keep = [d for d in os.environ.get("PATH", "").split(os.pathsep)
            if d and not (shutil.which("spriggit", path=d) or shutil.which("spriggit.exe", path=d))]
    return os.pathsep.join(keep)


def _run_local_spriggit(tmp_path: Path, local_version: str):
    b = tmp_path / "lbin"
    b.mkdir()
    # `dotnet tool run spriggit -- ...` answers only where the manifest is (the toolkit root), like the real one.
    body = ('if [ "$1" = "--list-sdks" ]; then echo "10.0.100 [C:/fake]"; exit 0; fi\n'
            'if [ "$1" = "--version" ]; then echo 10.0.100; exit 0; fi\n'
            'if [ "$1 $2 $3" = "tool run spriggit" ]; then\n'
            '  [ -f .config/dotnet-tools.json ] || { echo "Cannot find a manifest file."; exit 1; }\n')
    # MEASURED 2026-10-05: the real `spriggit --version` prints three lines and EXITS 1, global or local.
    body += ('  printf "Spriggit version {0}+Branch.main\n--version\nSpriggit.CLI {0}+Branch.main\n"; exit 1\n'.format(local_version)
             if local_version
             else '  echo "Run dotnet tool restore to make the spriggit command available."; exit 1\n')
    body += "fi\nexit 1\n"
    _script(b / "dotnet", body)
    env = dict(os.environ)
    env["PATH"] = str(b).replace("\\", "/") + os.pathsep + _path_without_spriggit()
    env["TOOLCHAIN_DOTNET"] = str(b / "dotnet").replace("\\", "/")
    p = subprocess.run([BASH, str(TOOL)], capture_output=True, text=True, env=env, timeout=120, cwd=str(tmp_path))
    return p.stdout


def test_a_spriggit_restored_from_the_manifest_is_found(tmp_path):
    out = _run_local_spriggit(tmp_path, PIN)
    assert "not installed" not in _line(out, "spriggit "), out
    assert " ok " in _line(out, "spriggit version"), out
    assert " ok " in _line(out, "spriggit entry point"), out


def test_a_manifest_spriggit_at_another_version_is_a_mismatch(tmp_path):
    out = _run_local_spriggit(tmp_path, "0.40.0")
    assert "MISMATCH" in _line(out, "spriggit version"), out


def test_no_spriggit_anywhere_is_optional(tmp_path):
    out = _run_local_spriggit(tmp_path, "")
    assert "not installed (optional)" in _line(out, "spriggit "), out



def test_automod_built_from_a_dirty_tree_is_degraded(tmp_path):
    """Re-review N3: the pinned commit plus local edits is not what the toolkit tested."""
    root = _toolkit(tmp_path)
    _automod_built(root, commit=PIN_SHA + " dirty")
    out = _run_wrappers(tmp_path, root)
    assert "MISMATCH" in _line(out, "automod version") and "local changes" in _line(out, "automod version"), out
    assert "automod-version" in _failed(out), out
