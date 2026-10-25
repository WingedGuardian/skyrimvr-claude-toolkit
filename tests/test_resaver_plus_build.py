"""tools/resaver-plus/build.py: the pinned, patched, hash-checked, tested local build of ReSaver Plus (port plan task 3).

git, CMake and CTest are replaced by a recording fake (no network, no compiler, any platform). Each refusal has its
own test, and each test asserts the step the refusal must PREVENT never ran: a refused build installs nothing.
The real patch set is checked too: SOURCE-SHA256 must cover exactly what the 27 patches change and the 15 additions
add (the bytes themselves are reproduced against upstream by the Windows CI job, which builds for real).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from conftest import REPO

HERE = REPO / "tools" / "resaver-plus"


def _load():
    spec = importlib.util.spec_from_file_location("resaver_plus_build", HERE / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


GOOD_CTEST = "\n".join(["Test project x"] + [
    "  {}/7 Test #{}: {} ....................   Passed    0.05 sec".format(i + 1, i + 1, n)
    for i, n in enumerate(("unit", "decoder_port", "topic113", "expected_result", "structured_queries",
                           "analysis_contract"))] + [
    "  7/7 Test #7: fixtures .....................***Skipped   0.01 sec", "100% tests passed"])
UPSTREAM = {"src/a.cpp": b"int a;\n", "CMakeLists.txt": b"project(x)\n"}
PATCHED = b"int a; // patched\n"
ADDED = b"// added\n"


class Fake:
    """Stands in for every external program build.py runs. `calls` records them in order."""

    def __init__(self, mod, tmp):
        self.mod, self.tmp, self.calls = mod, tmp, []
        self.head = mod.UPSTREAM_COMMIT
        self.dirty = ""
        self.crlf = "false"
        self.check_fails = False
        self.patched = PATCHED
        self.ctest = (0, GOOD_CTEST)
        self.exe = True
        self.upstream = dict(UPSTREAM)

    def run(self, cmd, cwd=None, env=None, timeout=3600):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        if cmd[-1] == "--version":
            return "cmake version 3.31.6-msvc6"
        if cmd[0].endswith("MSBuild.exe"):
            return "17.14.51.1"
        if "clone" in cmd:
            dest = Path(cmd[-1])
            (dest / ".git").mkdir(parents=True)
            for rel, data in self.upstream.items():
                (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                (dest / rel).write_bytes(data)
            return ""
        if "rev-parse" in cmd:
            return self.head
        if "status" in cmd:
            return self.dirty
        if "apply" in cmd:
            if "--check" in cmd:
                if self.check_fails:
                    raise self.mod.BuildError("git apply --check failed: patch does not apply")
                return ""
            (Path(cwd) / "src" / "a.cpp").write_bytes(self.patched)
            return ""
        if "-S" in cmd:
            out = Path(cmd[cmd.index("-B") + 1])
            (out / "CMakeFiles" / "3.31.6").mkdir(parents=True)
            (out / "CMakeFiles" / "3.31.6" / "CMakeCXXCompiler.cmake").write_text(
                'set(CMAKE_CXX_COMPILER_VERSION "19.44.35228.0")\n', encoding="utf-8")
            return "-- Selecting Windows SDK version 10.0.26100.0 to target Windows 10.0.26300."
        if "--build" in cmd:
            if self.exe:
                out = Path(cmd[cmd.index("--build") + 1]) / "Release"
                out.mkdir(parents=True)
                (out / "resaverpp-cli.exe").write_bytes(b"MZ fake build")
            return ""
        return ""

    def run_result(self, cmd, cwd=None, timeout=3600):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        if "config" in cmd:
            return 0, self.crlf + "\n"
        return self.ctest

    def ran(self, word):
        return [c for c in self.calls if word in c]


@pytest.fixture
def env(tmp_path, monkeypatch):
    mod = _load()
    patches, additions = tmp_path / "patches", tmp_path / "additions"
    patches.mkdir()
    (patches / "0001-a.patch").write_text("--- a/src/a.cpp\n+++ b/src/a.cpp\n@@ -1 +1 @@\n-int a;\n+int a; // patched\n",
                                         encoding="utf-8")
    (additions / "src").mkdir(parents=True)
    (additions / "src" / "new.cpp").write_bytes(ADDED)
    manifest = tmp_path / "SOURCE-SHA256"
    manifest.write_text("# test\n{} patched src/a.cpp\n{} added src/new.cpp\n".format(_sha(PATCHED), _sha(ADDED)),
                        encoding="utf-8")
    fake = Fake(mod, tmp_path)
    for name, value in {"PATCHES": patches, "ADDITIONS": additions, "MANIFEST": manifest, "BUILD": tmp_path / "build",
                        "EXPECTED_PATCHES": 1, "EXPECTED_ADDITIONS": 1, "MAX_WORK_PATH": 10 ** 4, "run": fake.run, "run_result": fake.run_result,
                        "windows": lambda: True, "vs_install": lambda: tmp_path / "vs",
                        "find_cmake": lambda vs: tmp_path / "cmake" / "cmake.exe"}.items():
        monkeypatch.setattr(mod, name, value)
    monkeypatch.setattr(mod.shutil, "which", lambda name: "git" if name == "git" else None)
    fake.work = tmp_path / "work"
    return mod, fake


def _installed(mod):
    return (mod.BUILD / "resaverpp-cli.exe").exists() or (mod.BUILD / "BUILD-INFO.json").exists()


def test_a_good_build_installs_the_tested_executable_with_its_build_info(env):
    mod, fake = env
    info = mod.build(fake.work)
    data = json.loads((mod.BUILD / "BUILD-INFO.json").read_text(encoding="utf-8"))
    assert data == info
    assert data["sha256"] == _sha((mod.BUILD / "resaverpp-cli.exe").read_bytes()) == _sha(b"MZ fake build")
    assert data["upstream_commit"] == mod.UPSTREAM_COMMIT and data["gui"] is False
    assert data["ctest"]["passed"] == list(mod.REQUIRED_TESTS) and data["ctest"]["skipped"] == ["fixtures"]
    assert data["tools"]["msvc"] == "19.44.35228.0" and data["tools"]["windows_sdk"] == "10.0.26100.0"
    assert data["source_sha256"] == {"src/a.cpp": _sha(PATCHED), "src/new.cpp": _sha(ADDED)}
    configure = fake.ran("-S")[0]
    assert "-DRESAVERPP_GUI=OFF" in configure and "-DRESAVERPP_TESTS=ON" in configure
    build = fake.ran("--build")[0]
    assert build[build.index("--parallel") + 1] == "1" and "/p:CL_MPCount=1" in build
    # Conversion is off on every git call: a CRLF checkout would make every hash differ.
    assert all("core.autocrlf=false" in c for c in fake.calls if c[0] == "git")


def test_a_checkout_at_another_commit_is_refused_before_anything_builds(env):
    mod, fake = env
    fake.head = "0" * 40
    with pytest.raises(mod.BuildError, match="not the pinned"):
        mod.build(fake.work)
    assert not fake.ran("apply") and not fake.ran("-S") and not _installed(mod)


def test_a_dirty_source_cache_is_refused_and_left_alone(env):
    mod, fake = env
    cache = fake.work / "upstream"
    (cache / ".git").mkdir(parents=True)
    (cache / "mine.txt").write_text("local work", encoding="utf-8")
    fake.dirty = "?? mine.txt"
    with pytest.raises(mod.BuildError, match="local changes"):
        mod.build(fake.work)
    assert (cache / "mine.txt").read_text(encoding="utf-8") == "local work"
    assert not fake.ran("clone") and not fake.ran("-S") and not _installed(mod)


def test_a_cache_that_converts_line_endings_is_refused(env):
    mod, fake = env
    (fake.work / "upstream" / ".git").mkdir(parents=True)
    fake.crlf = "true"
    with pytest.raises(mod.BuildError, match="converts line endings"):
        mod.build(fake.work)
    assert not fake.ran("-S") and not _installed(mod)


def test_a_patch_that_does_not_apply_stops_before_the_build(env):
    mod, fake = env
    fake.check_fails = True
    with pytest.raises(mod.BuildError, match="apply"):
        mod.build(fake.work)
    assert not [c for c in fake.ran("apply") if "--check" not in c]
    assert not fake.ran("-S") and not fake.ran("--build") and not _installed(mod)


def test_one_byte_off_in_a_patched_file_fails_the_source_check(env):
    mod, fake = env
    fake.patched = PATCHED.replace(b"patched", b"patcheD")
    with pytest.raises(mod.BuildError, match="SOURCE-SHA256"):
        mod.build(fake.work)
    assert not fake.ran("-S") and not fake.ran("--build") and not _installed(mod)


def test_an_addition_may_not_replace_an_upstream_file(env):
    mod, fake = env
    fake.upstream["src/new.cpp"] = b"upstream's own file\n"
    with pytest.raises(mod.BuildError, match="would replace an upstream file"):
        mod.build(fake.work)
    assert not fake.ran("-S") and not _installed(mod)


@pytest.mark.parametrize("ctest", [
    (8, GOOD_CTEST.replace("topic113 ....................   Passed", "topic113 ....................***Failed")),
    (0, "\n".join(l for l in GOOD_CTEST.splitlines() if "analysis_contract" not in l)),        # a target missing
    (0, GOOD_CTEST + "\n  8/8 Test #8: extra ....   Passed    0.01 sec"),                   # an unexpected one
    (0, GOOD_CTEST.replace("***Skipped", "   Passed")),                                    # fixtures ran
    (1, GOOD_CTEST),                                                                       # ctest itself failed
], ids=["a-target-fails", "a-target-missing", "unexpected-target", "fixtures-not-skipped", "ctest-exit"])
def test_a_test_failure_installs_nothing(env, ctest):
    mod, fake = env
    fake.ctest = ctest
    with pytest.raises(mod.BuildError, match="nothing installed|ctest exited"):
        mod.build(fake.work)
    assert not _installed(mod)


def test_a_rebuild_keeps_the_previous_build(env):
    mod, fake = env
    mod.BUILD.mkdir()
    (mod.BUILD / "resaverpp-cli.exe").write_bytes(b"older build")
    (mod.BUILD / "BUILD-INFO.json").write_text("{}", encoding="utf-8")
    mod.build(fake.work)
    kept = list((mod.BUILD / "previous").glob("*/resaverpp-cli.exe"))
    assert len(kept) == 1 and kept[0].read_bytes() == b"older build"


def test_jobs_are_capped_at_two(env):
    mod, fake = env
    with pytest.raises(mod.BuildError, match="--jobs"):
        mod.build(fake.work, jobs=4)
    assert not fake.calls


def test_a_work_folder_too_deep_for_msvc_is_refused_up_front(env, monkeypatch):
    mod, fake = env
    monkeypatch.setattr(mod, "MAX_WORK_PATH", 100)
    with pytest.raises(mod.BuildError, match="shorter --work"):
        mod.build(Path("C:/" + "d" * 120))
    assert not fake.calls


def test_off_windows_it_says_so_and_runs_nothing(env, monkeypatch):
    mod, fake = env
    monkeypatch.setattr(mod, "windows", lambda: False)
    with pytest.raises(mod.BuildError, match="Windows only"):
        mod.build(fake.work)
    assert not fake.calls


# ------------------------------------------------------------------------------------------------ the real patch set

def test_the_shipped_patch_set_is_complete_and_the_manifest_covers_exactly_it():
    mod = _load()
    patches, additions, manifest = mod.patch_files(), mod.addition_files(), mod.read_manifest()
    assert len(patches) == mod.EXPECTED_PATCHES == 27
    assert len(additions) == mod.EXPECTED_ADDITIONS == 15
    mod.check_inputs(patches, additions, manifest)
    assert sorted(r for r, (_h, k) in manifest.items() if k == "patched") == sorted(mod.patched_paths(patches))
    assert len([1 for _h, k in manifest.values() if k == "patched"]) == 10


def test_the_shipped_patches_are_lf_only_and_hold_no_contribution_drafts():
    files = [p for p in HERE.rglob("*") if p.is_file()]
    assert not [p for p in files if p.name.endswith("-PR.md") or "contributions" in p.relative_to(HERE).parts]
    shipped = [p for p in files if p.relative_to(HERE).parts[0] in ("patches", "additions")]
    assert len(shipped) == 42
    assert not [p.name for p in shipped if b"\r\n" in p.read_bytes()]


def test_the_manifest_rejects_a_damaged_line(tmp_path):
    mod = _load()
    bad = tmp_path / "m"
    bad.write_text("abc patched x\n", encoding="utf-8")
    with pytest.raises(mod.BuildError, match="unreadable"):
        mod.read_manifest(bad)
