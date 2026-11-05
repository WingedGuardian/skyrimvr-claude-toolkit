#!/usr/bin/env python3
"""The pinned SKSEVR development SDK: fetch, build, install, verify and link-qualify it.

    python native/sdk/sdk.py build   --prefix P --work W [--jobs N]
    python native/sdk/sdk.py verify  --prefix P [--json]
    python native/sdk/sdk.py qualify --prefix P --work W

Maintainer tooling. It never ships, launches a game, or writes into a game folder. A plugin built with this SDK needs
only the requirements `pins.json` lists under runtime_requirements.

Exit codes: 0 verified, 1 failed or drifted, 2 refused before doing anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NATIVE = HERE.parent
PINS = HERE / "pins.json"
RECEIPT = "sdk-receipt.json"
QUALIFIED = "sdk-qualified.json"
FORBIDDEN_IMPORTS = ("fmt", "spdlog", "openvr_api", "directxtk", "minhook")
REQUIRED_EXPORTS = ("SKSEPlugin_Load", "SKSEPlugin_Query")
REQUIRED_FILES = ("lib/CommonLibSSE.lib", "lib/minhook.x64.lib", "lib/cmake/CommonLibSSE/CommonLibSSEConfig.cmake",
                  "lib/cmake/SkyrimNativeSdk/SkyrimNativeSdkConfig.cmake", "include/MinHook.h", "licenses/CommonLibVR-COPYING.txt",
                  "licenses/MinHook-LICENSE.txt")
SUPPORTED_TOOLSETS = ("14.3", "14.4")     # MSVC v143: what CommonLib's C++23 needs and what was tested


class Refused(Exception):
    """Nothing was changed: the inputs cannot produce a qualified SDK."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_pins(path: Path = PINS) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def refuse_game_root(path: Path) -> None:
    """The SDK is development tooling: refuse any path inside a game install."""
    resolved = path.resolve()
    for folder in [resolved, *resolved.parents]:
        if (folder / "SkyrimVR.exe").exists() or (folder / "SkyrimSE.exe").exists():
            raise Refused("{} is inside a game folder ({})".format(path, folder))


def check_packages(installed: dict, pinned: dict) -> list:
    """Every difference between what vcpkg installed and the pins. A hash lock alone does not make a set coherent:
    an unpinned or moved package is the failure that once produced a library expecting fmt v9 beside fmt 12."""
    problems = []
    for name in sorted(set(installed) | set(pinned)):
        have, want = installed.get(name), pinned.get(name)
        if have != want:
            problems.append("{}: installed {} but pinned {}".format(name, have, want))
    return problems


def installed_packages(triplet_dir: Path) -> dict:
    found = {}
    for spdx in sorted((triplet_dir / "share").glob("*/vcpkg.spdx.json")):
        document = json.loads(spdx.read_text(encoding="utf-8"))
        found[spdx.parent.name] = document["packages"][0].get("versionInfo")
    return found


def check_baseline(configuration: dict, pinned: str) -> None:
    """vcpkg-configuration.json's default registry overrides vcpkg.json's builtin-baseline, so it is the one to pin."""
    actual = (configuration.get("default-registry") or {}).get("baseline")
    if actual != pinned:
        raise Refused("vcpkg baseline is {} but pinned {}".format(actual, pinned))


def manifest(prefix: Path) -> dict:
    return {p.relative_to(prefix).as_posix(): sha256(p)
            for p in sorted(prefix.rglob("*")) if p.is_file() and p.name not in (RECEIPT, QUALIFIED)}


def compare_manifest(recorded: dict, current: dict) -> list:
    problems = []
    for name in sorted(set(recorded) | set(current)):
        if name not in current:
            problems.append("missing: " + name)
        elif name not in recorded:
            problems.append("unexpected: " + name)
        elif recorded[name] != current[name]:
            problems.append("changed: " + name)
    return problems


def sources_digest(native: Path = NATIVE) -> str:
    """What the prefix copies from this tree. A changed header or config must make an already-built prefix stale."""
    digest = hashlib.sha256()
    roots = [native / "sdk/SkyrimNativeSdkConfig.cmake", native / "sdk/THIRD-PARTY-NOTICES.md",
             native / "observation/include", native / "observation/asm"]
    for root in roots:
        for path in sorted(root.rglob("*")) if root.is_dir() else [root]:
            if path.is_file():
                digest.update(path.relative_to(native).as_posix().encode("utf-8") + b"\0" + bytes.fromhex(sha256(path)))
    return digest.hexdigest()


def verify(prefix: Path, pins_path: Path = PINS, native: Path = NATIVE) -> list:
    """Problems with the SDK at `prefix`; empty means every installed byte is what the receipt recorded."""
    receipt_path = prefix / RECEIPT
    if not receipt_path.is_file():
        return ["no receipt at " + str(receipt_path)]
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except ValueError as e:
        return ["unreadable receipt: {}".format(e)]
    problems = []
    if receipt.get("pins_sha256") != sha256(pins_path):
        problems.append("stale: the pins changed since this SDK was built")
    if receipt.get("sources_sha256") != sources_digest(native):
        problems.append("stale: the observation sources or SDK config changed since this SDK was built")
    pins = load_pins(pins_path)
    for field, section in (("commonlib_commit", "commonlib"), ("minhook_commit", "minhook")):
        if receipt.get(field) != (pins.get(section) or {}).get("commit"):
            problems.append("{} is not the pinned commit".format(field))
    if receipt.get("packages") != pins.get("packages"):
        problems.append("the recorded packages are not the pinned set")
    recorded = receipt.get("files") or {}
    problems.extend("required file absent: " + name for name in REQUIRED_FILES if name not in recorded)
    problems.extend(compare_manifest(recorded, manifest(prefix)))
    return problems


def qualification_state(prefix: Path) -> bool:
    """True only if a link qualification ran against exactly this receipt."""
    record, receipt = prefix / QUALIFIED, prefix / RECEIPT
    if not record.is_file() or not receipt.is_file():
        return False
    try:
        return json.loads(record.read_text(encoding="utf-8")).get("receipt_sha256") == sha256(receipt)
    except ValueError:
        return False


def check_toolset(version: str) -> None:
    if not version or not version.startswith(SUPPORTED_TOOLSETS):
        raise Refused("MSVC toolset {} is not v143 (14.3x-14.4x)".format(version))


def imported_dlls(dependents: str) -> list:
    """The DLL names in `dumpbin /dependents` output, lowercased, one per import. Exact names, not a substring search."""
    return [line.strip().lower() for line in dependents.splitlines() if line.strip().lower().endswith(".dll") and " " not in line.strip()]


def check_link_output(exports: str, dependents: str) -> list:
    """Problems with a linked consumer: missing SKSE exports, or a dependency that would become a runtime requirement."""
    problems = ["missing export " + name for name in REQUIRED_EXPORTS if name not in exports.split()]
    names = imported_dlls(dependents)
    if not any(name == "kernel32.dll" for name in names):
        problems.append("the import list is unreadable: no KERNEL32.dll in it")
    problems.extend("runtime dependency on {} (must link statically)".format(name)
                    for name in names if name.rsplit(".", 1)[0].startswith(FORBIDDEN_IMPORTS))
    return problems


# ---- everything below needs the Windows toolchain ----

def vs_environment() -> dict:
    vswhere = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / \
        "Microsoft Visual Studio/Installer/vswhere.exe"
    if not vswhere.is_file():
        raise Refused("vswhere.exe not found: install Visual Studio 2022 Build Tools with the C++ workload yourself")
    found = subprocess.run([str(vswhere), "-latest", "-products", "*", "-requires",
                            "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                           capture_output=True, text=True, check=False).stdout.strip()
    if not found:
        raise Refused("Visual Studio was found, but not its x64 C++ tools")
    vcvars = Path(found.splitlines()[0]) / "VC/Auxiliary/Build/vcvars64.bat"
    listing = subprocess.run('cmd /c ""{}" >nul 2>&1 && set"'.format(vcvars), capture_output=True, text=True,
                             shell=True, check=False).stdout
    env = dict(line.split("=", 1) for line in listing.splitlines() if "=" in line and not line.startswith("="))
    if "VCToolsVersion" not in env:
        raise Refused("vcvars64.bat did not load an MSVC environment")
    return env


def run(name, command, log_dir: Path, env=None, cwd=None) -> None:
    """One logged step. CreateProcess searches the parent's PATH, so tools resolve from the given environment."""
    if env:
        resolved = shutil.which(str(command[0]), path=env.get("Path") or env.get("PATH"))
        if resolved:
            command = [resolved, *command[1:]]
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / (name + ".log")
    with open(log, "w", encoding="utf-8", errors="replace") as f:
        code = subprocess.run([str(c) for c in command], stdout=f, stderr=subprocess.STDOUT, env=env, cwd=cwd,
                              check=False).returncode
    print("{}: exit {}".format(name, code), flush=True)
    if code:
        raise RuntimeError("{} failed; see {}".format(name, log))


def git_head(repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()


def fetch(repo: str, commit: str, dest: Path, logs: Path, submodules: bool) -> None:
    if not dest.exists():
        run("clone-" + dest.name, ["git", "clone", "--filter=blob:none", "--no-checkout", repo, dest], logs)
        run("checkout-" + dest.name, ["git", "-C", dest, "checkout", "--detach", commit], logs)
        if submodules:
            run("submodules-" + dest.name, ["git", "-C", dest, "submodule", "update", "--init", "--depth", "1"], logs)
    if git_head(dest) != commit:
        raise Refused("{} is at {}, not the pinned {}".format(dest, git_head(dest), commit))
    dirty = subprocess.run(["git", "-C", str(dest), "status", "--porcelain"], capture_output=True, text=True,
                           check=True).stdout.strip()
    if dirty:
        raise Refused("{} has local changes; use a new --work".format(dest))


def bootstrap_vcpkg(pins: dict, work: Path, logs: Path) -> Path:
    """vcpkg at the pinned baseline commit: its scripts, ports and tool all come from that one commit, and bootstrap
    checks the tool's hash. A shared or runner-provided vcpkg folder would make them mutable inputs."""
    root = work / "vcpkg"
    fetch("https://github.com/microsoft/vcpkg.git", pins["commonlib"]["vcpkg_baseline"], root, logs, submodules=False)
    if not (root / "vcpkg.exe").is_file():
        run("vcpkg-bootstrap", ["cmd", "/c", root / "bootstrap-vcpkg.bat", "-disableMetrics"], logs, cwd=root)
    return root


def build(prefix: Path, work: Path, jobs: int) -> int:
    pins = load_pins()
    prefix, work = prefix.resolve(), work.resolve()    # configure runs elsewhere: a relative prefix would land elsewhere
    refuse_game_root(prefix)
    refuse_game_root(work)
    if prefix.exists() and any(prefix.iterdir()):
        raise Refused("{} is not empty: an SDK prefix is built once and never updated in place".format(prefix))
    env = vs_environment()
    check_toolset(env.get("VCToolsVersion", ""))
    logs = work / "logs"
    vcpkg_root = str(bootstrap_vcpkg(pins, work, logs))
    source, binary = work / "commonlib", work / "b"
    fetch(pins["commonlib"]["repo"], pins["commonlib"]["commit"], source, logs, submodules=True)
    submodule = git_head(source / "extern/openvr")
    if submodule != pins["commonlib"]["openvr_submodule_commit"]:
        raise Refused("OpenVR submodule is at {}".format(submodule))
    check_baseline(json.loads((source / "vcpkg-configuration.json").read_text(encoding="utf-8")),
                   pins["commonlib"]["vcpkg_baseline"])
    for folder in ("vt", "vp", "vd", "vb"):
        (work / folder).mkdir(parents=True, exist_ok=True)
    env.update(VCPKG_ROOT=vcpkg_root, VCPKG_DISABLE_METRICS="1", VCPKG_DEFAULT_BINARY_CACHE=str(work / "vb"))
    # A CMake variable, not an environment one: this keeps vcpkg's working files out of the shared vcpkg folder.
    install_options = ";".join("--{}={}".format(k, (work / v).as_posix()) for k, v in
                               (("x-buildtrees-root", "vt"), ("x-packages-root", "vp"), ("downloads-root", "vd")))
    run("configure", ["cmake", "--preset", pins["commonlib"]["preset"], "-B", binary, *pins["commonlib"]["cmake_args"],
                      "-DVCPKG_INSTALL_OPTIONS={}".format(install_options),
                      "-DCMAKE_INSTALL_PREFIX={}".format(prefix)], logs, env, source)
    triplet_dir = binary / "vcpkg_installed" / pins["triplet"]
    problems = check_packages(installed_packages(triplet_dir), pins["packages"])
    if problems:
        raise Refused("package set differs from the pins, before compiling:\n  " + "\n  ".join(problems))
    run("build", ["cmake", "--build", binary, "--config", "Release", "-j", str(jobs)], logs, env, source)
    run("install", ["cmake", "--install", binary, "--config", "Release"], logs, env, source)

    minhook_source, minhook_binary = work / "minhook", work / "mb"
    fetch(pins["minhook"]["repo"], pins["minhook"]["commit"], minhook_source, logs, submodules=False)
    run("minhook-configure", ["cmake", "-S", minhook_source, "-B", minhook_binary, "-G", "Ninja",
                              "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_POLICY_DEFAULT_CMP0091=NEW",
                              "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreadedDLL",
                              "-DCMAKE_INSTALL_PREFIX={}".format(prefix)], logs, env)
    run("minhook-build", ["cmake", "--build", minhook_binary, "-j", str(jobs)], logs, env)
    run("minhook-install", ["cmake", "--install", minhook_binary], logs, env)

    # The whole tree: vcpkg's exported targets check their debug libraries exist even for a Release consumer.
    shutil.copytree(triplet_dir, prefix / "deps" / pins["triplet"])
    shutil.copytree(NATIVE / "observation/include", prefix / "observation/include")
    shutil.copytree(NATIVE / "observation/asm", prefix / "observation/asm")
    package = prefix / "lib/cmake/SkyrimNativeSdk"
    package.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HERE / "SkyrimNativeSdkConfig.cmake", package / "SkyrimNativeSdkConfig.cmake")
    licenses = prefix / "licenses"
    licenses.mkdir(exist_ok=True)
    for src, name in ((source / "COPYING.txt", "CommonLibVR-COPYING.txt"),
                      (source / "EXCEPTIONS.md", "CommonLibVR-EXCEPTIONS.md"),
                      (source / "extern/openvr/LICENSE", "OpenVR-LICENSE.txt"),
                      (minhook_source / "LICENSE.txt", "MinHook-LICENSE.txt"),
                      (HERE / "THIRD-PARTY-NOTICES.md", "THIRD-PARTY-NOTICES.md")):
        shutil.copy2(src, licenses / name)
    receipt = {
        "schema": 1,
        "pins_sha256": sha256(PINS),
        "vcpkg_commit": git_head(Path(vcpkg_root)),
        "sources_sha256": sources_digest(),
        "commonlib_commit": git_head(source),
        "openvr_commit": submodule,
        "minhook_commit": git_head(minhook_source),
        "packages": installed_packages(triplet_dir),
        "msvc_toolset": env.get("VCToolsVersion"),
        "vcpkg_tool": subprocess.run([str(Path(vcpkg_root, "vcpkg.exe")), "version"], capture_output=True, text=True,
                                     check=False).stdout.splitlines()[0],
        "runtime_requirements": pins["runtime_requirements"],
        "development_requirements": pins["development_requirements"],
        "files": manifest(prefix),
    }
    (prefix / RECEIPT).write_text(json.dumps(receipt, indent=1), encoding="utf-8", newline="\n")
    print("built {} files into {}".format(len(receipt["files"]), prefix))
    return 0


def qualify(prefix: Path, work: Path) -> int:
    """A real link of the example consumer against the installed bytes. Finding the package proves nothing."""
    prefix, work = prefix.resolve(), work.resolve()
    problems = verify(prefix)
    if problems:
        raise Refused("the SDK does not verify:\n  " + "\n  ".join(problems[:20]))
    refuse_game_root(work)
    env = vs_environment()
    example, binary = NATIVE / "examples/consumer", work / "qualify"
    if binary.exists():
        shutil.rmtree(binary)    # regenerated from tracked sources each run
    logs = work / "logs"
    run("qualify-configure", ["cmake", "-S", example, "-B", binary, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
                              "-DCMAKE_PREFIX_PATH={}".format(prefix)], logs, env)
    run("qualify-build", ["cmake", "--build", binary, "-j", "2"], logs, env)
    dll = binary / "SkyrimNativeExample.dll"
    dumpbin = shutil.which("dumpbin", path=env.get("Path") or env.get("PATH"))
    exports = subprocess.run([dumpbin, "/exports", str(dll)], capture_output=True, text=True, check=True).stdout
    dependents = subprocess.run([dumpbin, "/dependents", str(dll)], capture_output=True, text=True, check=True).stdout
    failures = check_link_output(exports, dependents)
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    run("qualify-test", ["ctest", "--test-dir", binary, "--output-on-failure"], logs, env)
    (prefix / QUALIFIED).write_text(json.dumps({"receipt_sha256": sha256(prefix / RECEIPT), "example_dll_sha256": sha256(dll),
                                                "msvc_toolset": env.get("VCToolsVersion")}, indent=1), encoding="utf-8", newline="\n")
    print("link qualified: {} sha256 {}".format(dll.name, sha256(dll)))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("build", "verify", "qualify"))
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--work", type=Path)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            problems = verify(args.prefix)
            qualified = qualification_state(args.prefix)
            print(json.dumps({"verified": not problems, "link_qualified": qualified, "problems": problems}) if args.json
                  else "\n".join(problems) or ("verified, link qualified" if qualified else "verified, NOT link qualified"))
            return 1 if problems else 0
        if not args.work:
            raise Refused("--work is required")
        return build(args.prefix, args.work, args.jobs) if args.command == "build" else qualify(args.prefix, args.work)
    except Refused as e:
        print("REFUSED: {}".format(e), file=sys.stderr)
        return 2
    except RuntimeError as e:
        print("FAILED: {}".format(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
