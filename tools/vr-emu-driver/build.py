#!/usr/bin/env python3
"""Build the pose-emulating SteamVR null driver on THIS machine, from pinned upstream source. Nothing is shipped.

    python tools/vr-emu-driver/build.py [--work DIR]

Why it is built here and not shipped: the driver's source repository grants no licence (see README.md beside
this file), so the toolkit may not redistribute its source or a binary. This script fetches exactly one pinned
commit for your own use, applies a one-line fix, and compiles it.

Steps, each checked:
  1. git and the MSVC build tools (Visual Studio 2022 Build Tools, "Desktop development with C++") are present.
  2. The driver source is fetched at DRIVER_COMMIT, and Valve's OpenVR headers at OPENVR_COMMIT (tag v1.26.7);
     each checkout's HEAD is verified against its pin before anything is built.
  3. PATCH is applied: the upstream GetSerialNumber() falls off the end without a return for an unknown index
     (undefined behaviour in C++); it gets a default.
  4. The same cl command line the dev install used builds driver_null.dll into tools/vr-emu-driver/build/, with
     BUILD-INFO.json (the pins, the patch, the SHA-256 of the result). MSVC output is not byte-reproducible, so the
     hash identifies YOUR build, not a reference one.

Then: bash tools/vr-headless.sh on, and bash tools/vr-headless.sh emu on.

Exit: 0 built | 2 a requirement is missing, a pin did not verify, or the build failed (the message says which).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
DRIVER_REPO = "https://github.com/DubiousDuo/VR-Emulator-Driver---SkyrimVR-Devkit"
DRIVER_COMMIT = "59be19d13287e2088c515b696a964381784a3356"
OPENVR_REPO = "https://github.com/ValveSoftware/openvr"
OPENVR_COMMIT = "751538d6cbde9d060e5906c1d45c8cedeaf0ee18"     # tag v1.26.7
SOURCES = ("basics.cpp", "driverlog.cpp", "driver_sample.cpp", "csampledevicedriver.cpp",
           "cserverdriver_sample.cpp", "csamplecontrollerdriver.cpp", "cwatchdogdriver_sample.cpp")
PATCH = {
    "file": "csamplecontrollerdriver.cpp",
    "find": '    case 2:\n        return "CTRL2Serial";\n        break;\n    }\n}',
    "replace": '    case 2:\n        return "CTRL2Serial";\n        break;\n    }\n    return "CTRLUnknownSerial";\n}',
}


class BuildError(Exception):
    pass


def run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise BuildError("{} failed: {}".format(" ".join(map(str, cmd[:3])), (r.stderr or r.stdout).strip()[-400:]))
    return r.stdout.strip()


def fetch(repo, commit, dest: Path, sparse=None):
    """A checkout of exactly `commit`, verified."""
    if not (dest / ".git").is_dir():
        run(["git", "clone", "--no-checkout", "--filter=blob:none", repo, str(dest)])
    if sparse:
        run(["git", "-C", str(dest), "sparse-checkout", "set", *sparse])
    run(["git", "-C", str(dest), "fetch", "--depth", "1", "origin", commit])
    run(["git", "-C", str(dest), "-c", "advice.detachedHead=false", "checkout", "--force", commit])
    verify_head(run(["git", "-C", str(dest), "rev-parse", "HEAD"]), commit, repo)


def verify_head(head: str, commit: str, repo: str) -> None:
    if head.strip().lower() != commit.lower():
        raise BuildError("{} is at {}, not the pinned {}: refusing to build unpinned source".format(repo, head, commit))


def apply_patch(text: str) -> str:
    if text.count(PATCH["find"].replace("\n", "\r\n")) == 1:
        return text.replace(PATCH["find"].replace("\n", "\r\n"), PATCH["replace"].replace("\n", "\r\n"))
    if text.count(PATCH["find"]) != 1:
        raise BuildError("the patch target is not in {} exactly once: the pinned source is not what this script "
                         "expects".format(PATCH["file"]))
    return text.replace(PATCH["find"], PATCH["replace"])


def vcvars() -> Path:
    vswhere = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / \
        "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.is_file():
        raise BuildError("the MSVC build tools were not found (no vswhere.exe). Install Visual Studio 2022 Build Tools "
                         "with 'Desktop development with C++' -- yourself; nothing here installs it.")
    path = run([str(vswhere), "-latest", "-products", "*", "-requires",
                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"])
    bat = Path(path.splitlines()[0]) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat" if path else None
    if not bat or not bat.is_file():
        raise BuildError("Visual Studio was found but not its x64 C++ tools (vcvars64.bat)")
    return bat


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(argv) -> int:
    if os.name != "nt":
        print("ERROR: the driver builds with MSVC on Windows only", file=sys.stderr)
        return 2
    work = Path(argv[argv.index("--work") + 1]) if "--work" in argv else HERE / "work"
    try:
        if not shutil.which("git"):
            raise BuildError("git not found on PATH")
        bat = vcvars()
        work.mkdir(parents=True, exist_ok=True)
        fetch(DRIVER_REPO, DRIVER_COMMIT, work / "driver")
        fetch(OPENVR_REPO, OPENVR_COMMIT, work / "openvr", sparse=["headers"])
        src = work / "src"
        if src.exists():
            shutil.rmtree(str(src))                  # the script's own staging copy, rebuilt every run
        shutil.copytree(str(work / "driver"), str(src), ignore=shutil.ignore_patterns(".git"))
        target = src / PATCH["file"]
        target.write_bytes(apply_patch(target.read_bytes().decode("utf-8")).encode("utf-8"))
        out = src / "out"
        out.mkdir(exist_ok=True)
        script = work / "build.bat"
        script.write_text("@echo off\r\ncall \"{}\" >nul\r\ncd /d \"{}\"\r\ncl /nologo /LD /O2 /MT /EHsc /std:c++17 "
                          "/D_WINDOWS /DWIN32 /I\"{}\" {} /Foout\\ /Feout\\driver_null.dll /link user32.lib\r\n"
                          .format(bat, src, work / "openvr" / "headers", " ".join(SOURCES)), encoding="utf-8")
        run(["cmd", "/c", str(script)])
        dll = out / "driver_null.dll"
        if not dll.is_file():
            raise BuildError("cl reported success but produced no driver_null.dll")
        BUILD.mkdir(exist_ok=True)
        shutil.copy2(str(dll), str(BUILD / "driver_null.dll"))
        info = {"driver_repo": DRIVER_REPO, "driver_commit": DRIVER_COMMIT, "openvr_repo": OPENVR_REPO,
                "openvr_commit": OPENVR_COMMIT, "patch": PATCH, "sha256": sha(BUILD / "driver_null.dll")}
        (BUILD / "BUILD-INFO.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    except (BuildError, OSError) as e:
        print("ERROR: {}".format(e), file=sys.stderr)
        return 2
    print("built {}  sha256 {}".format(BUILD / "driver_null.dll", info["sha256"]))
    print("next: bash tools/vr-headless.sh on   then   bash tools/vr-headless.sh emu on")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
