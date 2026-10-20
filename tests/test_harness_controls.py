import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from conftest import BASH, REPO
from harness_isolation import active

pytestmark = pytest.mark.skipif(not active(), reason="COULD NOT CHECK: test isolation barrier not installed")


@pytest.mark.tripwire_control
def test_package_manager_tripwire_can_go_red(tmp_path):
    outer = tmp_path / "outer"
    outer.mkdir()
    fake = outer / "npm"
    fake.write_text('#!/bin/bash\necho OUTER-FAKE\n', newline="\n")
    fake.chmod(0o755)
    env = dict(os.environ, PATH=os.environ["HARNESS_TRIPWIRE_BIN"] + os.pathsep +
               str(outer) + os.pathsep + os.environ["PATH"])
    result = subprocess.run([BASH, "-c", "npm install -g TEST-DECOY"], env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 97
    assert "OUTER-FAKE" not in result.stdout
    # Remove the barrier only for a CONTROLLED fake path, never the real outer PATH.
    env.pop("BASH_ENV")
    script = ('if command -v cygpath >/dev/null 2>&1; then CONTROL_PATH=$(cygpath -u "$CONTROL_PATH"); fi; '
              'PATH="$CONTROL_PATH:/usr/bin:/bin"; npm install -g TEST-DECOY')
    result = subprocess.run([BASH, "-c", script], env=dict(env, CONTROL_PATH=outer.as_posix()),
                            capture_output=True, text=True, timeout=15)
    isolation_check_passes = result.returncode == 97 and "OUTER-FAKE" not in result.stdout
    assert not isolation_check_passes
    assert "OUTER-FAKE" in result.stdout


@pytest.mark.tripwire_control
def test_native_and_module_pip_are_blocked():
    with pytest.raises(PermissionError, match="TRIPWIRE"):
        subprocess.run([sys.executable, "-m", "pip", "install", "TEST-DECOY"])
    with pytest.raises(PermissionError, match="TRIPWIRE"):
        subprocess.run(["npm.cmd", "install", "-g", "TEST-DECOY"])


@pytest.mark.tripwire_control
def test_shell_python_module_pip_is_blocked():
    result = subprocess.run([BASH, "-c", 'python -m pip install TEST-DECOY'],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 97
    assert "TRIPWIRE" in result.stderr


@pytest.mark.tripwire_control
def test_shell_node_npm_cli_is_blocked():
    result = subprocess.run([BASH, "-c", 'node /TEST-DECOY/npm-cli.js install -g TEST-DECOY'],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 97
    assert "TRIPWIRE" in result.stderr


# Mutation children inherit the launcher's PYTHONPATH, so the barrier installed in this process is the PARENT
# checkout's. The pure checks below are read from this test's own sibling file, so a mutated copy is judged.
# Never inside a fixture root, even in a mutation child (whose REPO copy lives under the parent's basetemp).
OUTSIDE_DECOY = Path(sys.executable).resolve().parent / "HARNESS-NONEXISTENT-DECOY"
_iso_spec = importlib.util.spec_from_file_location("_isolation_under_test",
                                                   Path(__file__).resolve().with_name("harness_isolation.py"))
iso = importlib.util.module_from_spec(_iso_spec)
_iso_spec.loader.exec_module(iso)


@pytest.mark.parametrize("event", ["_winapi.CopyFile2", "shutil.copyfile", "os.truncate"])
def test_barrier_refuses_a_copy_or_truncate_outside_the_fixture(tmp_path, event):
    """Review L7 MEDIUM: Windows Python 3.12+ copies through _winapi.CopyFile2 and os.truncate empties a file by
    name; neither is an `open`, and both walked past the barrier. Twin: the same event inside the fixture passes."""
    roots = [tmp_path.resolve()]
    outside, inside = str(OUTSIDE_DECOY), str(tmp_path / "f")
    args = {"_winapi.CopyFile2": lambda dst: (inside, dst, 8), "shutil.copyfile": lambda dst: (inside, dst),
            "os.truncate": lambda dst: (dst, 0)}[event]
    with pytest.raises(PermissionError, match="outside test fixture"):
        iso.check_write(roots, event, args(outside))
    iso.check_write(roots, event, args(inside))
    # ... and the barrier actually installed in this run refuses it (no file is touched either way: it is a decoy).
    with pytest.raises(PermissionError, match="outside test fixture"):
        sys.audit(event, *args(outside))


def test_barrier_refuses_a_real_truncate_outside_the_fixture(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me", encoding="utf-8")
    os.truncate(victim, 0)                                  # inside: allowed
    with pytest.raises(PermissionError, match="outside test fixture"):
        os.truncate(OUTSIDE_DECOY, 0)                        # refused before the OS is asked (it does not exist)


@pytest.mark.parametrize("argv", [
    ["python", "-mpip", "--version"], ["python", "-I", "-m", "pip", "--version"], ["python", "-Impip"],
    ["python", "-m", "pip3", "list"], ["npx", "TEST-DECOY"], ["corepack", "enable"], ["uv", "pip", "install"],
    ["node", "/x/npx-cli.js", "TEST-DECOY"], ["bash", "-c", "python -I -m pip --version"],
    ["bash", "-c", "cd x && python -E -mpip install TEST-DECOY"]])
def test_every_package_manager_spelling_trips(argv):
    """Review L7 LOW: `-mpip`, an isolated interpreter inside a shell string, npx and the other managers."""
    assert iso.package_manager(None, argv), argv


@pytest.mark.parametrize("argv", [
    ["python", "-m", "pytest", "-k", "pip"], ["git", "commit", "-m", "pipe fix"], ["bash", "-c", "python -m pip"],
    ["python", "-mypy"], ["node", "/x/server.js"]])
def test_ordinary_commands_do_not_trip(argv):
    """Twins: an ordinary -m, a commit message, and a plain `-m pip` in a shell string (sitecustomize's job there)."""
    assert not iso.package_manager(None, argv), argv


@pytest.mark.tripwire_control
def test_native_mpip_and_npx_are_blocked():
    with pytest.raises(PermissionError, match="TRIPWIRE"):
        subprocess.run([sys.executable, "-mpip", "--version"])
    with pytest.raises(PermissionError, match="TRIPWIRE"):
        subprocess.run([BASH, "-c", "python -I -m pip --version"])


@pytest.mark.tripwire_control
def test_shell_mpip_and_npx_are_blocked():
    for cmd in ("python -mpip --version", "npx --version"):
        result = subprocess.run([BASH, "-c", cmd], capture_output=True, text=True, timeout=60)
        assert result.returncode == 97 and "TRIPWIRE" in result.stderr, (cmd, result.returncode, result.stderr)


def test_publication_failure_rolls_back(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("publisher", REPO / "tools/spriggit_publish.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source, target = tmp_path / "new", tmp_path / "old"
    source.mkdir(); target.mkdir()
    (source / "fresh.yaml").write_text("fresh")
    (target / "old.yaml").write_text("original")
    replace = module._rename_new

    def fail_once(src, dst):
        if Path(src).parent.name.startswith(".spriggit-stage-"):
            raise OSError("controlled publication failure")
        return replace(src, dst)

    monkeypatch.setattr(module, "_rename_new", fail_once)
    with pytest.raises(OSError, match="controlled publication failure"):
        module.publish(source, target)
    assert (target / "old.yaml").read_text() == "original"
    assert not list(tmp_path.glob(".spriggit-*"))


def test_concurrent_publication_rejected(tmp_path):
    spec = importlib.util.spec_from_file_location("publisher", REPO / "tools/spriggit_publish.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    source, target = tmp_path / "new", tmp_path / "old"
    source.mkdir(); target.mkdir(); (source / "fresh.yaml").write_text("fresh")
    (target / "original.yaml").write_text("old")
    key = hashlib.sha256(os.path.normcase(str(target.resolve())).encode()).hexdigest()[:16]
    (tmp_path / (".spriggit-lock-" + key)).mkdir()
    with pytest.raises(ValueError, match="in progress"):
        module.publish(source, target)
    assert (target / "original.yaml").read_text() == "old"


def test_committed_publication_never_deletes_previous_destination(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("publisher", REPO / "tools/spriggit_publish.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    source, target = tmp_path / "new", tmp_path / "old"
    source.mkdir(); target.mkdir()
    (source / "fresh.yaml").write_text("fresh"); (target / "old.yaml").write_text("old")
    remove = module.shutil.rmtree

    def failure(path, *args, **kwargs):
        if Path(path).name.startswith(".spriggit-previous-"):
            pytest.fail("previous destination must not be deleted")
        return remove(path, *args, **kwargs)

    monkeypatch.setattr(module.shutil, "rmtree", failure)
    module.publish(source, target)
    assert (target / "fresh.yaml").read_text() == "fresh"
    assert len(list(tmp_path.glob(".spriggit-previous-*"))) == 1
    assert "previous output retained" in capsys.readouterr().err.lower()


def test_lock_refresh_merges_target_reports_and_rejects_missing_extras():
    spec = importlib.util.spec_from_file_location("locker", REPO / "scripts/lock-python-dependencies.py")
    locker = importlib.util.module_from_spec(spec); spec.loader.exec_module(locker)
    def report(names):
        return {"install": [{"metadata": {"name": name, "version": version}} for name, version in names]}
    linux = report([("pytest", "9.0.3")])
    windows310 = report([("pytest", "9.0.3"), ("colorama", "0.4.6"),
                         ("exceptiongroup", "1.3.1"), ("tomli", "2.4.1")])
    with pytest.raises(ValueError, match="target"):
        locker.merge_reports([linux])
    merged = locker.merge_reports([linux, windows310])
    assert len(merged["install"]) == 4
    with pytest.raises(ValueError, match="conflicting"):
        locker.merge_reports([windows310, report([("tomli", "2.0.0")])])


def _skip_unless_linux_daemon(docker):
    """windows-latest has a Docker daemon, but it runs WINDOWS containers: no Linux image (busybox) can run there,
    pulled or not. That is the expected "no Linux daemon on the runner" absence ci.yml allows, so it is named as
    such -- reported as a missing image it read as an unexpected gap (CI run 38178385999)."""
    r = subprocess.run([docker, "info", "--format", "{{.OSType}}"], capture_output=True, text=True, timeout=10)
    os_type = r.stdout.strip().lower()
    if r.returncode == 0 and os_type and os_type != "linux":
        pytest.skip("COULD NOT CHECK: Docker daemon runs %s containers, not Linux" % os_type)


def test_live_container_readonly_sentinels(tmp_path):
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("COULD NOT CHECK: Docker executable unavailable in test runtime")
    try:
        probe = subprocess.run([docker, "info"], capture_output=True, timeout=10)
    except subprocess.TimeoutExpired:
        pytest.skip("COULD NOT CHECK: Docker daemon timeout")
    if probe.returncode:
        pytest.skip("COULD NOT CHECK: Docker daemon unavailable")
    _skip_unless_linux_daemon(docker)
    image = "busybox:1.37"
    probe = subprocess.run([docker, "image", "inspect", image], capture_output=True, timeout=10)
    if probe.returncode:
        pytest.skip("COULD NOT CHECK: existing busybox:1.37 image unavailable; test never pulls")
    data, profile = tmp_path / "Data", tmp_path / "profile"
    data.mkdir(); profile.mkdir()
    (data / "sentinel").write_text("game")
    (profile / "sentinel").write_text("profile")
    result = subprocess.run([docker, "run", "--rm", "--pull=never", "--network=none",
                            "--mount", f"type=bind,source={tmp_path},target=/workspace,readonly",
                            "--mount", f"type=bind,source={data},target=/skyrim/mods,readonly",
                            "--mount", f"type=bind,source={profile},target=/skyrim/profile,readonly",
                            image, "sh", "-c",
                            'set -eu; for p in /workspace/Data/sentinel /skyrim/mods/sentinel /skyrim/profile/sentinel; do '
                            'test -r "$p"; if echo changed > "$p"; then exit 42; fi; done'],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert (data / "sentinel").read_text() == "game"
    assert (profile / "sentinel").read_text() == "profile"


def _live_docker():
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("COULD NOT CHECK: Docker executable unavailable in test runtime")
    try:
        probe = subprocess.run([docker, "info"], capture_output=True, timeout=10)
    except subprocess.TimeoutExpired:
        pytest.skip("COULD NOT CHECK: Docker daemon timeout")
    if probe.returncode:
        pytest.skip("COULD NOT CHECK: Docker daemon unavailable")
    _skip_unless_linux_daemon(docker)
    if subprocess.run([docker, "image", "inspect", "busybox:1.37"], capture_output=True, timeout=10).returncode:
        pytest.skip("COULD NOT CHECK: existing busybox:1.37 image unavailable; test never pulls")
    return docker


def test_live_cold_start_mounts_without_node_modules(tmp_path):
    """Replay the helper's REAL mount arguments against real Docker on a fresh workspace
    that has no node_modules/ -- the directory is deliberately NOT pre-created."""
    docker = _live_docker()
    work = tmp_path / "checkout"
    (work / ".devcontainer").mkdir(parents=True)
    sources = {}
    for key in ("MODS", "PROFILE", "OVERWRITE"):
        sources[key] = tmp_path / key.lower()
        sources[key].mkdir()
        (sources[key] / "sentinel").write_text(key)
    template = (REPO / ".devcontainer/devcontainer.json").read_text()
    for key, path in sources.items():  # placeholder or an already-filled source
        template = re.sub(r'source=[^,"]*,target=/skyrim/' + key.lower() + ',',
                          lambda m, path=path, key=key: "source=" + path.as_posix() + ",target=/skyrim/" + key.lower() + ",",
                          template)
    (work / ".devcontainer/devcontainer.json").write_text(template)
    (work / "devshell-docker.sh").write_bytes((REPO / "devshell-docker.sh").read_bytes())
    (work / "package.json").write_text("{}")
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "docker").write_text('#!/bin/bash\nif [ "$1" = run ]; then printf "%s\n" "$@" > "$RUN_LOG"; exit 0; fi\n'
                                 'if [ "$1" = exec ]; then exit 0; fi\nexit 0\n', newline="\n")
    (fake / "docker").chmod(0o755)
    log = tmp_path / "run.log"
    result = subprocess.run([BASH, str(work / "devshell-docker.sh")], cwd=work, capture_output=True, text=True,
                            env=dict(os.environ, PATH=str(fake) + os.pathsep + os.environ["PATH"], RUN_LOG=str(log)),
                            timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (work / "node_modules").exists()
    args = log.read_text().splitlines()[1:]
    image = args.index("skyrim-toolkit")
    replay, i = [], 0
    while i < image:
        if args[i] == "-d":
            i += 1
        elif args[i] == "--name":
            i += 2
        else:
            replay.append(args[i]); i += 1
    probe = ('set -eu; test -r /workspace/package.json; test -r /skyrim/mods/sentinel; '
             'for p in /workspace/package.json /skyrim/mods/sentinel /skyrim/profile/sentinel '
             '/skyrim/overwrite/sentinel; do if echo changed > "$p"; then exit 42; fi; done')
    live = subprocess.run([docker, "run", "--rm", "--pull=never", "--network=none", *replay,
                           "busybox:1.37", "sh", "-c", probe], capture_output=True, text=True, timeout=60)
    assert live.returncode == 0, live.stderr
    assert not (work / "node_modules").exists()
    for key, path in sources.items():
        assert (path / "sentinel").read_text() == key


def test_harness_evidence_falls_back_when_workspace_readonly(tmp_path):
    """Inside the container /workspace is a read-only bind: evidence must go to scratch."""
    spec = importlib.util.spec_from_file_location("launcher", REPO / "tests/run_harness_tests.py")
    launcher = importlib.util.module_from_spec(spec); spec.loader.exec_module(launcher)
    repo = tmp_path / "repo"
    repo.mkdir()
    assert launcher.evidence_root(repo) == repo / ".pytest_cache"
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (blocked / ".pytest_cache").write_text("not a directory")  # mkdir fails like EROFS would
    root = launcher.evidence_root(blocked)
    assert not root.is_relative_to(blocked)
    assert root.is_dir()


def test_spriggit_unwritable_destination_fails_before_serializing(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("publisher", REPO / "tools/spriggit_publish.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    source = tmp_path / "Mod.esp"
    source.write_text("x")
    monkeypatch.setattr(module.os, "access", lambda path, mode: False)
    with pytest.raises(ValueError, match="not writable"):
        module.check_paths(source, tmp_path / "out" / "yaml")


def test_held_lock_error_names_crash_debris(tmp_path):
    spec = importlib.util.spec_from_file_location("publisher", REPO / "tools/spriggit_publish.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    source, target = tmp_path / "new.esp", tmp_path / "Mod.esp"
    source.write_text("fresh"); target.write_text("old")
    key = hashlib.sha256(os.path.normcase(str(target.resolve())).encode()).hexdigest()[:16]
    (tmp_path / (".spriggit-lock-" + key)).mkdir()
    debris = tmp_path / (".spriggit-previous-" + key + "-0123abcd")
    debris.write_text("the previous plugin")
    with pytest.raises(ValueError) as error:
        module.publish(source, target)
    assert str(debris) in str(error.value)
    assert "\n" not in str(error.value)
    assert target.read_text() == "old"


@pytest.mark.skipif(os.unlink not in os.supports_dir_fd, reason="COULD NOT CHECK: platform has no dir_fd removal")
def test_barrier_resolves_dir_fd_relative_removal(tmp_path):
    """POSIX rmtree removes relative names under dir_fd: fixture cleanup must work, and the
    same relative name under a directory OUTSIDE the fixture must still be refused."""
    inside = tmp_path / "tree"
    (inside / "sub").mkdir(parents=True)
    (inside / "sub" / "f").write_text("x")
    shutil.rmtree(inside)
    assert not inside.exists()
    fd = os.open(REPO, os.O_RDONLY)
    try:
        with pytest.raises(PermissionError, match="outside test fixture"):
            os.unlink("HARNESS-NONEXISTENT-DECOY", dir_fd=fd)
    finally:
        os.close(fd)


def test_barrier_reads_a_relative_symlink_target_against_the_links_folder(tmp_path):
    """Linux CI run 38187755448: venv makes `lib64 -> lib` (relative) inside the fixture; the barrier judged `lib`
    against the cwd (the repository) and refused it. The same relative target climbing OUT must still be refused."""
    d = tmp_path / "venv"
    (d / "lib").mkdir(parents=True)
    out = os.path.relpath(REPO, d)                 # from the link's folder up and over into the repository
    with pytest.raises(PermissionError, match="outside test fixture"):
        os.symlink(out, d / "escape", target_is_directory=True)     # the barrier refuses before the OS is asked
    try:
        os.symlink("lib", d / "lib64", target_is_directory=True)
    except OSError as e:                           # the OS may refuse (no symlink privilege); the barrier must not
        assert "outside test fixture" not in str(e), e
