import json
import os
from pathlib import Path
import subprocess

import pytest

from conftest import BASH, REPO
from harness_isolation import active

pytestmark = pytest.mark.skipif(not active(), reason="COULD NOT CHECK: run tests/run_harness_tests.py for contained fixtures")


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


@pytest.mark.parametrize("fail_start", [False, True])
def test_docker_helper_mount_arguments_close_workspace_alias(tmp_path, fail_start):
    (tmp_path / ".devcontainer").mkdir()
    template = (REPO / ".devcontainer/devcontainer.json").read_text()
    for key in ("MODS", "PROFILE", "OVERWRITE"):
        template = template.replace("{{DEVCONTAINER_" + key + "_MOUNT}}", tmp_path.as_posix())
    (tmp_path / ".devcontainer/devcontainer.json").write_text(template)
    (tmp_path / ".devcontainer/install-dependencies.sh").write_bytes(
        (REPO / ".devcontainer/install-dependencies.sh").read_bytes())
    (tmp_path / "devshell-docker.sh").write_bytes((REPO / "devshell-docker.sh").read_bytes())
    binary = tmp_path / "bin"; binary.mkdir()
    executable(binary / "docker", 'if [ "$1" = run ]; then printf "%s\\n" "$@" > "$MOUNT_LOG"; '
               '[ "$FAIL_START" = 1 ] && exit 19; fi\n'
               'if [ "$1" = rm ]; then echo "RM CALLED" >> "$MOUNT_LOG"; fi\nexit 0\n')
    log = tmp_path / "mounts.log"
    result = subprocess.run([BASH, str(tmp_path / "devshell-docker.sh")], cwd=tmp_path,
                            env=dict(os.environ, PATH=str(binary) + os.pathsep + os.environ["PATH"],
                                     MOUNT_LOG=str(log), FAIL_START="1" if fail_start else "0"),
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == (19 if fail_start else 0), result.stderr
    args = log.read_text()
    if fail_start:
        assert "RM CALLED" not in args
    assert ":/workspace:ro" in args
    for target in ("mods", "profile", "overwrite"):
        assert ":/skyrim/" + target + ":ro" in args
    # A mountpoint nested in the read-only workspace bind cannot be created on a fresh
    # checkout (node_modules is gitignored), so startup would fail before provisioning.
    lines = args.splitlines()
    specs = [lines[i + 1] for i, flag in enumerate(lines[:-1]) if flag in ("-v", "--volume", "--mount")]
    assert len(specs) == 4, specs
    nested = [spec for spec in specs if "target=/workspace/" in spec or ":/workspace/" in spec]
    assert not nested, nested


def test_mo2_config_and_explicit_override(tmp_path):
    root = tmp_path / "toolkit"
    (root / "tools/xelib").mkdir(parents=True)
    (root / ".claude").mkdir()
    helper = REPO / "tools/xelib/active-plugins.js"
    for file in helper.parent.glob("*.js"):
        (root / "tools/xelib" / file.name).write_bytes(file.read_bytes())
    profile = tmp_path / "MO2's profile"
    profile.mkdir()
    (profile / "plugins.txt").write_text("*Correct.esp\n")
    local = tmp_path / "local/Skyrim VR"
    local.mkdir(parents=True)
    (local / "plugins.txt").write_text("*Wrong.esp\n")
    config = root / ".claude/skyrim-paths.env"
    config.write_text("SKYRIM_LOADORDER_DIR='" + profile.as_posix().replace("'", "'\\''") + "'\n")
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
    env.pop("PLUGINS_TXT")
    env["SKYRIM_LOADORDER_DIR"] = str(local)
    result = subprocess.run(["node", "-e", script], cwd=root, env=env,
                            capture_output=True, text=True, timeout=20)
    assert json.loads(result.stdout) == ["Wrong.esp"]
    env.pop("SKYRIM_LOADORDER_DIR")
    config.write_text("SKYRIM_LOADORDER_DIR='unterminated\n")
    result = subprocess.run(["node", "-e", script], cwd=root, env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert "Malformed SKYRIM_LOADORDER_DIR" in result.stderr


def store_stubs(tmp_path):
    stubs = tmp_path / "Microsoft" / "WindowsApps"
    stubs.mkdir(parents=True)
    for name in ("python", "python3"):
        executable(stubs / name, 'echo STUB-RAN; exit 9009\n')
    return stubs


def test_spriggit_skips_windows_store_python_stub(tmp_path):
    stubs = store_stubs(tmp_path)
    binary = tmp_path / "bin"
    binary.mkdir()
    executable(binary / "spriggit", '''while [ "$#" -gt 0 ]; do
 if [ "$1" = --OutputPath ]; then out="$2"; shift 2; else shift; fi
done
mkdir -p "$out"; printf 'ModKey: Example.esp\n' > "$out/Record.yaml"
''')
    source = tmp_path / "Example.esp"
    source.write_text("FAKE INPUT")
    target = tmp_path / "result"
    env = dict(os.environ, PATH=os.pathsep.join([str(binary), str(stubs), os.environ["PATH"]]),
               SPRIGGIT_WORK_ROOT=str(tmp_path / "scratch"))
    env.pop("SKYRIM_PYTHON", None)
    result = subprocess.run([BASH, "-c", 'export PATH="$STUBS:$PATH"; exec bash "$@"', "_",
                             str(fixture_wrapper(tmp_path)), "serialize",
                             "--InputPath", str(source), "--OutputPath", str(target)],
                            env=dict(env, STUBS=stubs.as_posix()), cwd=tmp_path,
                            text=True, capture_output=True, timeout=30)
    assert "STUB-RAN" not in result.stdout + result.stderr
    assert result.returncode == 0, result.stderr
    assert (target / "Record.yaml").exists()


def test_spriggit_only_store_stub_is_a_clear_error(tmp_path):
    stubs = store_stubs(tmp_path)
    wrapper = fixture_wrapper(tmp_path)
    env = {k: v for k, v in os.environ.items() if k not in ("SKYRIM_PYTHON", "BASH_ENV")}
    result = subprocess.run([BASH, "-c", 'S=$STUBS; if command -v cygpath >/dev/null; then S=$(cygpath -u "$S"); fi; '
                             'PATH="$S"; . "$WRAPPER" serialize --InputPath a --OutputPath b',
                             ], env=dict(env, STUBS=stubs.as_posix(), WRAPPER=wrapper.as_posix()),
                            cwd=tmp_path, text=True, capture_output=True, timeout=30)
    assert result.returncode != 0
    assert "STUB-RAN" not in result.stdout + result.stderr
    assert "Microsoft Store" in result.stderr, result.stderr


def test_spriggit_refuses_destination_with_foreign_entries(tmp_path):
    """The pre-repair wrapper merged with `cp -r` and deleted nothing; a whole-directory
    replace would silently lose a git repository kept beside the YAML."""
    target = tmp_path / "result"
    (target / ".git").mkdir(parents=True)
    (target / ".git" / "HEAD").write_text("ref: refs/heads/master\n")
    (target / "Weapons").mkdir()
    (target / "Weapons" / "Old.yaml").write_text("old")
    (target / "spriggit-meta.json").write_text("{}")
    result, target = spriggit_case(tmp_path, "yaml", existing=False)
    assert result.returncode != 0
    assert (target / ".git" / "HEAD").read_text() == "ref: refs/heads/master\n"
    assert (target / "Weapons" / "Old.yaml").read_text() == "old"
    message = [line for line in result.stderr.splitlines() if ".git" in line]
    assert message and "Spriggit" in message[0], result.stderr


def test_spriggit_replaces_spriggit_shaped_destination(tmp_path):
    target = tmp_path / "result"
    (target / "Weapons").mkdir(parents=True)
    (target / "Weapons" / "Old.yaml").write_text("old")
    (target / "spriggit-meta.json").write_text("{}")
    (target / "RecordData.yaml").write_text("old")
    result, target = spriggit_case(tmp_path, "yaml", existing=False)
    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in target.iterdir()) == ["Record.yaml"]
