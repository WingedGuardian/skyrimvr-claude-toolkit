"""CLI selection skips a simulated old interpreter; probes never install one."""
import os
import shlex
import subprocess
import sys

import pytest

from conftest import BASH
from test_wrapper_python import _install


@pytest.mark.parametrize("name", ["skydoctor", "skyguard.sh"])
@pytest.mark.parametrize("version", [(3, 9), (3, 10)])
def test_bash_cli_checks_version_and_disables_manager_installation(tmp_path, name, version):
    probe = tmp_path / "simulate39.py"
    probe.write_text(f"import sys\nsys.version_info={version!r}\nexec(sys.argv[1])\n")
    old = tmp_path / "old python"
    old.write_text("#!/bin/bash\nif [ \"$1\" = -c ]; then\n" +
                   shlex.quote(sys.executable.replace("\\", "/")) + " " + shlex.quote(probe.as_posix()) +
                   ' "$2"\nexit $?\nfi\necho "CONFIGURED ${PYTHON_MANAGER_AUTOMATIC_INSTALL:-unset}"\n', newline="\n")
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
    assert result.returncode == 0
    wanted = "COMPATIBLE false" if version < (3, 10) else "CONFIGURED false"
    assert wanted in result.stdout, result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt", reason="native CMD wrappers")
@pytest.mark.parametrize("name", ["skydoctor.cmd", "skyguard.cmd"])
@pytest.mark.parametrize("version", [(3, 9), (3, 10)])
def test_cmd_cli_checks_version_and_disables_manager_installation(tmp_path, name, version):
    probe = tmp_path / "simulate39.py"
    probe.write_text(f"import sys\nsys.version_info={version!r}\nexec(sys.argv[1])\n")
    old = tmp_path / "old python.cmd"
    old.write_text('@echo off\nif "%~1"=="-c" goto probe\necho CONFIGURED %PYTHON_MANAGER_AUTOMATIC_INSTALL%\nexit /b 0\n:probe\n'
                   f'"{sys.executable}" "{probe}" %2\nexit /b %errorlevel%\n')
    directory = tmp_path / "fallback"
    directory.mkdir()
    (directory / "py.cmd").write_text('@echo off\nif "%~2"=="-c" exit /b 0\n'
                                      'echo COMPATIBLE %PYTHON_MANAGER_AUTOMATIC_INSTALL%\nexit /b 0\n')
    install = _install(tmp_path, None)
    env = dict(os.environ, SKYGUARD_PYTHON=str(old), PYTHON_MANAGER_AUTOMATIC_INSTALL="true", PATH=str(directory))
    result = subprocess.run([env["COMSPEC"], "/d", "/c", str(install / "tools" / name)],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    wanted = "COMPATIBLE false" if version < (3, 10) else "CONFIGURED false"
    assert wanted in result.stdout, result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt", reason="native CMD wrappers")
@pytest.mark.parametrize("name", ["skydoctor.cmd", "skyguard.cmd"])
def test_cmd_cli_accepts_actual_python_exe_for_a_private_standin(tmp_path, name):
    install = _install(tmp_path, None)
    package = install / "tools/skyguard"
    package.mkdir()
    body = "import os\nprint('ACTUAL-EXE ' + os.environ['PYTHON_MANAGER_AUTOMATIC_INSTALL'])\n"
    (package / "doctor.py").write_text(body)
    (package / "__main__.py").write_text(body)
    env = dict(os.environ, SKYGUARD_PYTHON=sys.executable, PYTHON_MANAGER_AUTOMATIC_INSTALL="true")
    result = subprocess.run([env["COMSPEC"], "/d", "/c", str(install / "tools" / name)],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and "ACTUAL-EXE false" in result.stdout, result.stdout + result.stderr
