#!/usr/bin/env python3
"""Build ReSaver Plus (ReSaver++) on THIS machine, from pinned upstream source plus this folder's patches. Nothing
binary is shipped.

    python tools/resaver-plus/build.py [--work DIR] [--jobs 1|2]

Steps, each checked; the first failure stops the build and nothing is installed:
  1. Windows, git, the MSVC build tools (Visual Studio 2022 Build Tools, "Desktop development with C++") and CMake
     3.21 or newer (on PATH, or the copy Visual Studio bundles) are present.
  2. Tonycubed2/ResaverPP is fetched at UPSTREAM_COMMIT (19.6) with line-ending conversion OFF; the checkout's HEAD is
     verified against the pin. An existing cache must be clean, at the pin, and conversion-free, or it is refused.
  3. The upstream tree is copied into fresh owned staging. Each of the 27 patches is checked (`git apply --check`) and
     applied, in order; the 15 additions are copied in (none may replace an upstream file).
  4. Every changed and added file is compared with SOURCE-SHA256. One byte different and the build stops.
  5. CMake configures the command-line tool and the tests with the GUI OFF, and builds them with one MSBuild job and
     one compiler worker by default (--jobs 2 at most).
  6. CTest runs. The six runnable targets (unit, decoder_port, topic113, expected_result, structured_queries,
     analysis_contract) must all pass; the real-save `fixtures` target is skipped by design. Anything else -- a
     failure, a missing target, an unexpected one -- refuses the install.
  7. Only then: resaverpp-cli.exe is installed into tools/resaver-plus/build/ with BUILD-INFO.json (pins, patch-set
     hash, per-file source hashes, the tool versions found, the executable's SHA-256 and the CTest summary). A previous
     build is moved to build/previous/<stamp>/, never deleted. MSVC output is not byte-reproducible, so the hash
     identifies YOUR build; the adapter (tools/resaver_native.py) pins to it.

Then: bash tools/resaver-cli.sh verify-native, and opt in with RESAVER_BACKEND=native (README.md).

Exit: 0 built and installed | 2 a requirement is missing, a pin or hash did not verify, the build or a test failed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
PATCHES = HERE / "patches"
ADDITIONS = HERE / "additions"
MANIFEST = HERE / "SOURCE-SHA256"
UPSTREAM_REPO = "https://github.com/Tonycubed2/ResaverPP"
UPSTREAM_VERSION = "19.6"
UPSTREAM_COMMIT = "0d48f60cb8a69b367abfbd78d58418e64c507da5"
EXPECTED_PATCHES = 27
EXPECTED_ADDITIONS = 15
REQUIRED_TESTS = ("unit", "decoder_port", "topic113", "expected_result", "structured_queries", "analysis_contract")
SKIPPED_TESTS = ("fixtures",)        # real saves; never configured here
EXECUTABLE = "resaverpp-cli.exe"
CMAKE_MINIMUM = (3, 21)
MAX_WORK_PATH = 100
TESTED = {"msvc": "19.44.35228", "cmake": "3.31.6-msvc6", "msbuild": "17.14.51"}
GIT = ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf"]
# `  1/7 Test #1: unit ......................   Passed    0.05 sec` / `... fixtures .....***Skipped   0.01 sec`
CTEST_LINE = re.compile(r"Test\s+#\d+:\s+(\S+)\s+\.*\s*\**\s*(Passed|Failed|Skipped|Not Run|Exception|Timeout)", re.I)


class BuildError(Exception):
    pass


def run(cmd, cwd=None, env=None, timeout=3600):
    """One checked step. Every external program goes through here (the tests replace it)."""
    r = subprocess.run([str(c) for c in cmd], cwd=None if cwd is None else str(cwd), env=env, capture_output=True,
                       text=True, timeout=timeout)
    if r.returncode != 0:
        raise BuildError("{} failed: {}".format(" ".join(map(str, cmd[:4])), (r.stderr or r.stdout).strip()[-600:]))
    return r.stdout.strip()


def run_result(cmd, cwd=None, timeout=3600):
    """A step whose failure is decided by the caller (CTest's summary)."""
    r = subprocess.run([str(c) for c in cmd], cwd=None if cwd is None else str(cwd), capture_output=True, text=True,
                       timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def windows() -> bool:
    return os.name == "nt"


# ------------------------------------------------------------------------------------------------ the shipped inputs

def read_manifest(path: Path = None):
    """{relative path: (sha256, kind)} from SOURCE-SHA256 (kind: patched | added)."""
    rows = {}
    for line in Path(path or MANIFEST).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split(" ", 2)
        if len(parts) != 3 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]) or parts[1] not in ("patched", "added"):
            raise BuildError("SOURCE-SHA256 has an unreadable line: {!r}".format(line[:80]))
        if parts[2] in rows:
            raise BuildError("SOURCE-SHA256 lists {} twice".format(parts[2]))
        rows[parts[2]] = (parts[0], parts[1])
    if not rows:
        raise BuildError("SOURCE-SHA256 lists no files")
    return rows


def patch_files(patches_dir: Path = None):
    return sorted(Path(patches_dir or PATCHES).glob("*.patch"))


def addition_files(additions_dir: Path = None):
    root = Path(additions_dir or ADDITIONS)
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def patched_paths(patches):
    out = set()
    for p in patches:
        text = p.read_text(encoding="utf-8")
        for target in re.findall(r"^\+\+\+ b/(.+)$", text, re.M):
            out.add(target.strip())
    return out


def check_inputs(patches, additions, manifest, expect=None):
    """The shipped patch set is complete, and SOURCE-SHA256 covers exactly what it changes and adds."""
    expect = (EXPECTED_PATCHES, EXPECTED_ADDITIONS) if expect is None else expect
    if expect and (len(patches), len(additions)) != tuple(expect):
        raise BuildError("expected {} patches and {} additions, found {} and {}: the patch set is incomplete".format(
            expect[0], expect[1], len(patches), len(additions)))
    want_patched = {r for r, (_h, k) in manifest.items() if k == "patched"}
    want_added = {r for r, (_h, k) in manifest.items() if k == "added"}
    if patched_paths(patches) != want_patched:
        raise BuildError("the patches change files SOURCE-SHA256 does not list (or the reverse)")
    if set(additions) != want_added:
        raise BuildError("the additions differ from the files SOURCE-SHA256 lists as added")


def patch_set_hash(patches, additions_dir: Path = None) -> str:
    h = hashlib.sha256()
    for p in patches:
        h.update(("patch " + p.name + " " + sha(p) + "\n").encode())
    root = Path(additions_dir or ADDITIONS)
    for rel in addition_files(root):
        h.update(("added " + rel + " " + sha(root / rel) + "\n").encode())
    return h.hexdigest()


# ------------------------------------------------------------------------------------------------ toolchain

def vs_install() -> Path:
    vswhere = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / \
        "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.is_file():
        raise BuildError("the MSVC build tools were not found (no vswhere.exe). Install Visual Studio 2022 Build Tools "
                         "with 'Desktop development with C++' -- yourself; nothing here installs it.")
    path = run([vswhere, "-latest", "-products", "*", "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-property", "installationPath"])
    if not path:
        raise BuildError("Visual Studio was found but not its x64 C++ tools (Desktop development with C++)")
    return Path(path.splitlines()[0])


def find_cmake(vs: Path) -> Path:
    found = shutil.which("cmake")
    if found:
        return Path(found)
    bundled = vs / "Common7" / "IDE" / "CommonExtensions" / "Microsoft" / "CMake" / "CMake" / "bin" / "cmake.exe"
    if bundled.is_file():
        return bundled
    raise BuildError("CMake was not found on PATH or inside Visual Studio (its 'C++ CMake tools' component)")


def cmake_version(cmake: Path) -> str:
    text = run([cmake, "--version"])
    m = re.search(r"cmake version (\S+)", text)
    if not m:
        raise BuildError("could not read the CMake version")
    nums = tuple(int(x) for x in re.findall(r"\d+", m.group(1))[:2])
    if nums < CMAKE_MINIMUM:
        raise BuildError("CMake {} is older than the {}.{} upstream requires".format(m.group(1), *CMAKE_MINIMUM))
    return m.group(1)


def msbuild_version(vs: Path) -> str:
    exe = vs / "MSBuild" / "Current" / "Bin" / "MSBuild.exe"
    if not exe.is_file():
        return "unknown (MSBuild.exe not found)"
    try:
        return run([exe, "-version", "-nologo"]).splitlines()[-1].strip()
    except BuildError:
        return "unknown"


# ------------------------------------------------------------------------------------------------ source

def verify_head(head: str, commit: str, repo: str) -> None:
    if head.strip().lower() != commit.lower():
        raise BuildError("{} is at {}, not the pinned {}: refusing to build unpinned source".format(repo, head, commit))


def fetch(repo: str, commit: str, dest: Path) -> None:
    """A conversion-free checkout of exactly `commit`, verified. An existing cache is reused only when it is clean,
    at the pin and conversion-free; it is never reset, switched or discarded."""
    if dest.is_symlink():
        raise BuildError("refusing a linked source cache: {}".format(dest))
    if dest.exists():
        if not (dest / ".git").is_dir():
            raise BuildError("existing source directory is not this builder's Git cache: {}".format(dest))
        _code, conversion = run_result([*GIT, "-C", dest, "config", "--get", "core.autocrlf"])
        if conversion.strip().lower() != "false":
            raise BuildError("source cache converts line endings (core.autocrlf is not false); its bytes are not the "
                             "pinned ones. Use a fresh --work directory: {}".format(dest))
        dirty = run([*GIT, "-C", dest, "status", "--porcelain", "--untracked-files=all"])
        if dirty:
            raise BuildError("source cache has local changes; preserve them before building: {}".format(dest))
        head = run([*GIT, "-C", dest, "rev-parse", "HEAD"])
        verify_head(head, commit, repo)
        return
    run([*GIT, "clone", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "--no-checkout", "--filter=blob:none",
         repo, dest])
    run([*GIT, "-C", dest, "fetch", "--depth", "1", "origin", commit])
    run([*GIT, "-C", dest, "-c", "advice.detachedHead=false", "checkout", "--detach", commit])
    verify_head(run([*GIT, "-C", dest, "rev-parse", "HEAD"]), commit, repo)


def stage(work: Path, upstream: Path) -> Path:
    """Fresh owned staging under `work`, holding a copy of the upstream tree in staging/source."""
    owned = Path(tempfile.mkdtemp(prefix="owned-build-", dir=str(work))).resolve()
    if owned.parent != work:
        raise BuildError("staging resolved outside the selected work directory")
    (owned / ".toolkit-build-owner.json").write_text(json.dumps({"v": 1, "builder": "tools/resaver-plus/build.py",
                                                                 "upstream_commit": UPSTREAM_COMMIT}), encoding="utf-8")
    shutil.copytree(str(upstream), str(owned / "source"), ignore=shutil.ignore_patterns(".git"))
    return owned


def apply_patches(source: Path, patches) -> None:
    # Applied as a standalone tree: the work folder may sit inside the toolkit's own repository, and `git apply` in a
    # repository subfolder would resolve the patch paths against that repository's root.
    env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(source.parent))
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    for p in patches:
        run([*GIT, "apply", "--check", p], cwd=source, env=env)
        run([*GIT, "apply", p], cwd=source, env=env)


def overlay(source: Path, additions_dir: Path, additions) -> None:
    for rel in additions:
        target = source / rel
        if target.exists():
            raise BuildError("addition {} would replace an upstream file".format(rel))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(additions_dir / rel), str(target))


def verify_sources(source: Path, manifest) -> dict:
    bad = []
    for rel, (want, _kind) in sorted(manifest.items()):
        p = source / rel
        got = sha(p) if p.is_file() else None
        if got != want:
            bad.append("{} ({})".format(rel, "missing" if got is None else "SHA-256 " + got[:16]))
    if bad:
        raise BuildError("patched source differs from SOURCE-SHA256 -- refusing to build: " + "; ".join(bad))
    return {rel: want for rel, (want, _k) in sorted(manifest.items())}


# ------------------------------------------------------------------------------------------------ build and test

def configure(cmake: Path, source: Path, out: Path) -> str:
    return run([cmake, "-S", source, "-B", out, "-G", "Visual Studio 17 2022", "-A", "x64",
                "-DRESAVERPP_TESTS=ON", "-DRESAVERPP_GUI=OFF"])


def compile_tree(cmake: Path, out: Path, jobs: int) -> None:
    run([cmake, "--build", out, "--config", "Release", "--parallel", str(jobs), "--", "/p:CL_MPCount={}".format(jobs)])


def parse_ctest(text: str) -> dict:
    """{test name: Passed | Failed | Skipped | Not Run | ...} from CTest's console summary."""
    out = {}
    for name, status in CTEST_LINE.findall(text):
        out[name] = status.lstrip("*").title()
    return out


def judge_ctest(code: int, text: str) -> dict:
    results = parse_ctest(text)
    problems = []
    for name in REQUIRED_TESTS:
        if results.get(name) != "Passed":
            problems.append("{} {}".format(name, (results.get(name) or "did not run").lower()))
    for name, status in results.items():
        if name in REQUIRED_TESTS:
            continue
        if name in SKIPPED_TESTS:
            if status not in ("Skipped", "Not Run"):
                problems.append("{} was expected to be skipped but {}".format(name, status.lower()))
            continue
        problems.append("unexpected test {} ({})".format(name, status.lower()))
    if code != 0 and not problems:
        problems.append("ctest exited {}".format(code))
    if problems:
        raise BuildError("tests did not all pass -- nothing installed: " + "; ".join(problems))
    return {"passed": list(REQUIRED_TESTS), "skipped": sorted(n for n in results if n in SKIPPED_TESTS)}


def compiler_info(out: Path, configure_text: str) -> dict:
    info = {}
    for f in sorted((out / "CMakeFiles").glob("*/CMakeCXXCompiler.cmake")):
        m = re.search(r'set\(CMAKE_CXX_COMPILER_VERSION "([^"]+)"\)', f.read_text(encoding="utf-8", errors="replace"))
        if m:
            info["msvc"] = m.group(1)
    m = re.search(r"Selecting Windows SDK version (\S+)", configure_text)
    info["windows_sdk"] = m.group(1) if m else "unknown"
    info.setdefault("msvc", "unknown")
    return info


def install(executable: Path, info: dict, dest: Path = None) -> Path:
    """Rotate any previous build aside (never delete it), then install the executable and, LAST, BUILD-INFO.json: a
    crash between the two leaves no BUILD-INFO, and the adapter refuses to run without one."""
    dest = Path(dest or BUILD)
    dest.mkdir(parents=True, exist_ok=True)
    old = [dest / EXECUTABLE, dest / "BUILD-INFO.json"]
    if any(p.exists() for p in old):
        keep = dest / "previous" / "{}-{}".format(time.strftime("%Y%m%d-%H%M%S"), os.getpid())
        keep.mkdir(parents=True, exist_ok=False)
        for p in old:
            if p.exists():
                os.replace(str(p), str(keep / p.name))
    tmp = dest / (EXECUTABLE + ".installing")
    shutil.copyfile(str(executable), str(tmp))
    if sha(tmp) != info["sha256"]:
        raise BuildError("the installed copy differs from the tested executable")
    os.replace(str(tmp), str(dest / EXECUTABLE))
    meta = dest / "BUILD-INFO.json.installing"
    meta.write_text(json.dumps(info, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(str(meta), str(dest / "BUILD-INFO.json"))
    return dest / EXECUTABLE


def build(work: Path, jobs: int = 1, dest: Path = None) -> dict:
    if not windows():
        raise BuildError("ReSaver Plus builds with MSVC on Windows only; the Java backend is the cross-platform path")
    if jobs not in (1, 2):
        raise BuildError("--jobs must be 1 or 2 (the build shares the machine with the game)")
    if len(str(work)) > MAX_WORK_PATH:
        # MEASURED 2026-10-25: from a 160-character work folder MSBuild's FileTracker could not create its logs
        # (FTK1011, the 260-character path limit) and CMake reported "No CMAKE_CXX_COMPILER could be found".
        raise BuildError("the work folder path is {} characters; MSVC's build trackers fail past about {}. Use a "
                         "shorter --work folder (for example C:/Temp/rspp)".format(len(str(work)), MAX_WORK_PATH))
    if not shutil.which("git"):
        raise BuildError("git not found on PATH")
    patches, additions = patch_files(), addition_files()
    manifest = read_manifest()
    check_inputs(patches, additions, manifest)
    vs = vs_install()
    cmake = find_cmake(vs)
    versions = {"cmake": cmake_version(cmake), "msbuild": msbuild_version(vs)}
    work.mkdir(parents=True, exist_ok=True)
    fetch(UPSTREAM_REPO, UPSTREAM_COMMIT, work / "upstream")
    owned = stage(work, work / "upstream")
    source = owned / "source"
    apply_patches(source, patches)
    overlay(source, ADDITIONS, additions)
    hashes = verify_sources(source, manifest)
    out = owned / "cmake-build"
    configured = configure(cmake, source, out)
    versions.update(compiler_info(out, configured))
    compile_tree(cmake, out, jobs)
    ctest = Path(cmake).with_name("ctest.exe" if Path(cmake).suffix.lower() == ".exe" else "ctest")
    code, text = run_result([ctest, "--test-dir", out, "-C", "Release", "--output-on-failure", "--timeout", "600"])
    summary = judge_ctest(code, text)
    exe = out / "Release" / EXECUTABLE
    if not exe.is_file():
        raise BuildError("the build reported success but produced no " + EXECUTABLE)
    info = {"schema": 1, "upstream_repo": UPSTREAM_REPO, "upstream_version": UPSTREAM_VERSION,
            "upstream_commit": UPSTREAM_COMMIT, "patches": len(patches), "additions": len(additions),
            "patch_set_sha256": patch_set_hash(patches), "source_sha256": hashes, "gui": False,
            "tools": versions, "tested_tools": TESTED, "ctest": summary, "executable": EXECUTABLE,
            "sha256": sha(exe), "staging": owned.name, "built": time.strftime("%Y-%m-%dT%H:%M:%S")}
    install(exe, info, dest)
    return info


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work", type=Path, default=HERE / "work")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--install", type=Path, default=BUILD,
                        help="where the tested executable and BUILD-INFO.json go (default tools/resaver-plus/build; "
                             "the adapter reads only that one)")
    args = parser.parse_args(argv)
    try:
        info = build(args.work.resolve(), args.jobs, args.install.resolve())
    except (BuildError, OSError, subprocess.SubprocessError) as e:
        print("ERROR: {}".format(e), file=sys.stderr)
        return 2
    differs = [k for k in ("msvc", "cmake") if not str(info["tools"].get(k, "")).startswith(TESTED[k].split("-")[0])]
    print("built {}  sha256 {}".format(args.install.resolve() / EXECUTABLE, info["sha256"]))
    print("tests passed: {}; skipped: {}".format(", ".join(info["ctest"]["passed"]),
                                                 ", ".join(info["ctest"]["skipped"]) or "none"))
    if differs:
        print("note: built with {} -- tested with MSVC {} / CMake {}".format(
            ", ".join("{} {}".format(k, info["tools"].get(k)) for k in differs), TESTED["msvc"], TESTED["cmake"]))
    print("next: bash tools/resaver-cli.sh verify-native   then opt in: RESAVER_BACKEND=native (tools/resaver-plus/README.md)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
