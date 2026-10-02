import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from conftest import BASH, REPO


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


def test_publication_failure_rolls_back(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("publisher", REPO / "tools/spriggit_publish.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source, target = tmp_path / "new", tmp_path / "old"
    source.mkdir(); target.mkdir()
    (source / "fresh.yaml").write_text("fresh")
    (target / "old.yaml").write_text("original")
    replace = module.os.replace

    def fail_once(src, dst):
        if Path(src).parent.name.startswith(".spriggit-stage-"):
            raise OSError("controlled publication failure")
        return replace(src, dst)

    monkeypatch.setattr(module.os, "replace", fail_once)
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


@pytest.mark.parametrize("input_file,lock_file", [("requirements.txt", "requirements.lock"),
                                                   ("requirements-windows.txt", "requirements-windows.lock")])
def test_python_lock_tracks_input_and_hashes(input_file, lock_file):
    digest = hashlib.sha256((REPO / input_file).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    text = (REPO / lock_file).read_text()
    assert "# input-sha256: " + digest in text
    assert "--hash=sha256:" in text
    for line in (REPO / input_file).read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            assert line.strip() in text


def test_node_lock_tracks_declared_dependencies():
    package = json.loads((REPO / "package.json").read_text())
    lock = json.loads((REPO / "package-lock.json").read_text())
    assert lock["packages"][""]["dependencies"] == package["dependencies"]
    for name, value in lock["packages"].items():
        if name:
            assert value["integrity"].startswith("sha512-")


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
