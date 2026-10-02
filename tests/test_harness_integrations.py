import json
import os
from pathlib import Path
import subprocess

import pytest

from conftest import BASH, REPO


def executable(path, text):
    path.write_text("#!/bin/bash\n" + text, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def fixture_wrapper(tmp_path):
    text = (REPO / "tools/spriggit-cli.sh").read_text()
    # Keep the vulnerable baseline's hardcoded scratch path inside the fixture too.
    text = text.replace('WORK="C:/Temp/spriggit-work/$RANDOM$RANDOM$$"',
                        'WORK="' + (tmp_path / "scratch").as_posix() + '/$RANDOM$RANDOM$$"')
    wrapper = tmp_path / "spriggit-cli.sh"
    wrapper.write_text(text, newline="\n")
    publisher = REPO / "tools/spriggit_publish.py"
    if publisher.exists():
        (tmp_path / publisher.name).write_bytes(publisher.read_bytes())
    return wrapper


def spriggit_case(tmp_path, mode, existing=True):
    binary = tmp_path / "bin"
    binary.mkdir()
    executable(binary / "spriggit", '''while [ "$#" -gt 0 ]; do
 if [ "$1" = --OutputPath ]; then out="$2"; shift 2; else shift; fi
done
case "$FAKE_OUTPUT" in
 empty) mkdir -p "$out" ;;
 yaml) mkdir -p "$out"; printf 'ModKey: Example.esp\n' > "$out/Record.yaml" ;;
 fail) exit 23 ;;
esac
''')
    source = tmp_path / "Example.esp"
    source.write_text("FAKE INPUT")
    target = tmp_path / "result"
    if existing:
        target.mkdir()
        (target / "stale.yaml").write_text("old")
    env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ["PATH"],
               FAKE_OUTPUT=mode, SPRIGGIT_WORK_ROOT=str(tmp_path / "scratch"))
    result = subprocess.run([BASH, str(fixture_wrapper(tmp_path)), "serialize",
                             "--InputPath", str(source), "--OutputPath", str(target)],
                            env=env, cwd=tmp_path, text=True, capture_output=True, timeout=30)
    return result, target


def test_spriggit_replaces_stale_output(tmp_path):
    result, target = spriggit_case(tmp_path, "yaml")
    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in target.iterdir()) == ["Record.yaml"]


@pytest.mark.parametrize("mode", ["empty", "none", "fail"])
def test_spriggit_invalid_output_preserves_destination(tmp_path, mode):
    result, target = spriggit_case(tmp_path, mode)
    assert result.returncode != 0
    assert (target / "stale.yaml").read_text() == "old"


def test_spriggit_overlapping_paths_rejected(tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "Record.yaml").write_text("original")
    result = subprocess.run([BASH, str(fixture_wrapper(tmp_path)), "serialize",
                             "--InputPath", str(source), "--OutputPath", str(source / "child")],
                            env=dict(os.environ, SPRIGGIT_WORK_ROOT=str(tmp_path / "scratch")),
                            capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert "overlap" in result.stderr.lower()


def test_devcontainer_uses_readonly_mount_strings():
    text = (REPO / ".devcontainer/devcontainer.json").read_text()
    config = json.loads("\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//")))
    assert isinstance(config["workspaceMount"], str)
    for mount in [config["workspaceMount"], *config["mounts"]]:
        assert isinstance(mount, str)
        if "type=bind" in mount:
            assert "readonly" in mount.split(",")
    assert "install-dependencies.sh" in config["postCreateCommand"]
    assert "npm ci" in (REPO / ".devcontainer/install-dependencies.sh").read_text()


@pytest.mark.tripwire_control
def test_docker_dependency_failure_propagates(tmp_path):
    (tmp_path / ".devcontainer").mkdir()
    template = (REPO / ".devcontainer/devcontainer.json").read_text()
    for key in ("MODS", "PROFILE", "OVERWRITE"):
        template = template.replace("{{DEVCONTAINER_" + key + "_MOUNT}}", tmp_path.as_posix())
    (tmp_path / ".devcontainer/devcontainer.json").write_text(template)
    install = REPO / ".devcontainer/install-dependencies.sh"
    if install.exists():
        (tmp_path / ".devcontainer/install-dependencies.sh").write_bytes(install.read_bytes())
    (tmp_path / "devshell-docker.sh").write_bytes((REPO / "devshell-docker.sh").read_bytes())
    (tmp_path / "requirements.lock").write_text("fake")
    (tmp_path / "requirements.txt").write_text("fake")
    binary = tmp_path / "bin"
    binary.mkdir()
    executable(binary / "docker", '''case "$1" in
 exec) shift 2; if [ "$1" = bash ] && [ "$2" = -c ]; then shift 2; exec bash -c "$1"; fi ;;
esac
exit 0
''')
    result = subprocess.run([BASH, str(tmp_path / "devshell-docker.sh")], cwd=tmp_path,
                            env=dict(os.environ, PATH=str(binary) + os.pathsep + os.environ["PATH"]),
                            text=True, capture_output=True, timeout=30)
    assert result.returncode != 0, result.stdout
    assert "Done. Opening shell" not in result.stdout


def test_mo2_config_and_explicit_override(tmp_path):
    root = tmp_path / "toolkit"
    (root / "tools/xelib").mkdir(parents=True)
    (root / ".claude").mkdir()
    helper = REPO / "tools/xelib/active-plugins.js"
    for file in helper.parent.glob("*.js"):
        (root / "tools/xelib" / file.name).write_bytes(file.read_bytes())
    profile = tmp_path / "MO2 profile"
    profile.mkdir()
    (profile / "plugins.txt").write_text("*Correct.esp\n")
    local = tmp_path / "local/Skyrim VR"
    local.mkdir(parents=True)
    (local / "plugins.txt").write_text("*Wrong.esp\n")
    (root / ".claude/skyrim-paths.env").write_text("SKYRIM_LOADORDER_DIR='" + profile.as_posix() + "'\n")
    script = "console.log(JSON.stringify(require('./tools/xelib/active-plugins').readActivePlugins()))"
    env = dict(os.environ, LOCALAPPDATA=str(local.parent))
    env.pop("PLUGINS_TXT", None)
    env.pop("SKYRIM_LOADORDER_DIR", None)
    result = subprocess.run(["node", "-e", script], cwd=root, env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["Correct.esp"]
    env["PLUGINS_TXT"] = str(local / "plugins.txt")
    result = subprocess.run(["node", "-e", script], cwd=root, env=env,
                            capture_output=True, text=True, timeout=20)
    assert json.loads(result.stdout) == ["Wrong.esp"]
