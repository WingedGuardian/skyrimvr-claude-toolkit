"""CLI selection skips a simulated old interpreter; probes never install one."""
import os
import shlex
import subprocess
import sys

import pytest

from conftest import BASH
from test_wrapper_python import _install


@pytest.mark.parametrize("name", ["skydoctor", "skyguard.sh"])
def test_bash_cli_skips_python39_and_disables_manager_installation(tmp_path, name):
    probe = tmp_path / "simulate39.py"
    probe.write_text("import sys\nsys.version_info=(3,9)\nexec(sys.argv[1])\n")
    old = tmp_path / "old python"
    old.write_text("#!/bin/bash\nif [ \"$1\" = -c ]; then\n" +
                   shlex.quote(sys.executable.replace("\\", "/")) + " " + shlex.quote(probe.as_posix()) +
                   ' "$2"\nexit $?\nfi\necho OLD-SELECTED\n', newline="\n")
    old.chmod(0o755)
    directory = tmp_path / "fallback"
    directory.mkdir()
    modern = directory / "python3"
    modern.write_text('#!/bin/bash\n[ "$1" = -c ] && exit 0\n'
                      'echo "COMPATIBLE ${PYTHON_MANAGER_AUTOMATIC_INSTALL:-unset}"\n', newline="\n")
    modern.chmod(0o755)
    install = _install(tmp_path, None)
    env = dict(os.environ, SKYGUARD_PYTHON=str(old), PYTHON_MANAGER_AUTOMATIC_INSTALL="true",
               PATH=str(directory) + os.pathsep + os.environ["PATH"])
    result = subprocess.run([BASH, str(install / "tools" / name)], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and "OLD-SELECTED" not in result.stdout
    assert "COMPATIBLE false" in result.stdout, result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt", reason="native CMD wrappers")
@pytest.mark.parametrize("name", ["skydoctor.cmd", "skyguard.cmd"])
def test_cmd_cli_skips_python39_and_disables_manager_installation(tmp_path, name):
    probe = tmp_path / "simulate39.py"
    probe.write_text("import sys\nsys.version_info=(3,9)\nexec(sys.argv[1])\n")
    old = tmp_path / "old python.cmd"
    old.write_text('@echo off\nif "%~1"=="-c" goto probe\necho OLD-SELECTED\nexit /b 0\n:probe\n'
                   f'"{sys.executable}" "{probe}" %2\nexit /b %errorlevel%\n')
    directory = tmp_path / "fallback"
    directory.mkdir()
    (directory / "py.cmd").write_text('@echo off\nif "%~2"=="-c" exit /b 0\n'
                                      'echo COMPATIBLE %PYTHON_MANAGER_AUTOMATIC_INSTALL%\nexit /b 0\n')
    install = _install(tmp_path, None)
    env = dict(os.environ, SKYGUARD_PYTHON=str(old), PYTHON_MANAGER_AUTOMATIC_INSTALL="true", PATH=str(directory))
    result = subprocess.run([env["COMSPEC"], "/d", "/c", str(install / "tools" / name)],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and "OLD-SELECTED" not in result.stdout
    assert "COMPATIBLE false" in result.stdout, result.stdout + result.stderr
