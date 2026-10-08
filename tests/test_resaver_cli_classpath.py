"""tools/resaver-cli.sh: the classpath separator is the platform's, and a JDK change recompiles the driver.

Review P1 (Plan 8 T6d fix round 1): the wrapper joined its classpath with ';', the Windows separator, so on Linux
(the devcontainer) java read the whole string as one path. The platform comes from `uname -s`; the test seam
RESAVER_CLI_UNAME replaces it (honoured only under TOOLKIT_TEST_MODE=1). A fake `java` prints the -cp it was given.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from conftest import BASH, REPO


def _script(path: Path, body: str) -> None:
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _setup(tmp_path: Path, jdk: str = "21.0.12") -> Path:
    tools = tmp_path / "tk" / "tools"
    rs = tools / "resaver-cli"
    rs.mkdir(parents=True)
    (tools / "resaver-cli.sh").write_bytes((REPO / "tools" / "resaver-cli.sh").read_bytes())
    (rs / "ResaverCLI.java").write_bytes((REPO / "tools" / "resaver-cli" / "ResaverCLI.java").read_bytes())
    (rs / "ReSaver.jar").write_bytes(b"PK")
    cls = rs / "ResaverCLI.class"
    cls.write_bytes(b"\xca\xfe")
    os.utime(cls, (os.stat(rs / "ResaverCLI.java").st_mtime + 60,) * 2)
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    _script(b / "java", 'if [ "$1" = "-version" ]; then echo \'openjdk version "%s"\' >&2; exit 0; fi\n'
                        'while [ $# -gt 0 ]; do [ "$1" = "-cp" ] && { echo "CP=$2"; exit 0; }; shift; done\n' % jdk)
    _script(b / "javac", 'echo javac >> "%s"\n' % (tmp_path / "javac.log").as_posix())
    return tools


def _run(tmp_path: Path, tools: Path, uname: str, op: str = "dump") -> str:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(tmp_path / "bin").replace("\\", "/"), env.get("PATH", "")])
    env["TOOLKIT_TEST_MODE"] = "1"
    env["RESAVER_CLI_UNAME"] = uname
    p = subprocess.run([BASH, str(tools / "resaver-cli.sh"), op, "x.ess"], capture_output=True, text=True,
                       env=env, timeout=60)
    line = [ln for ln in p.stdout.splitlines() if ln.startswith("CP=")]
    assert line, p.stdout + p.stderr
    return line[0][3:]


@pytest.mark.parametrize("uname,sep,other", [("Linux", ":", ";"), ("Darwin", ":", ";"),
                                             ("MINGW64_NT-10.0-26200", ";", ":"), ("MSYS_NT-10.0", ";", ":")])
def test_the_classpath_separator_is_the_platforms(tmp_path, uname, sep, other):
    cp = _run(tmp_path, _setup(tmp_path), uname)
    # (the test host is Windows, so the paths themselves may hold ':' -- check the joins, not a blind split)
    assert ("ReSaver.jar" + sep) in cp and cp.endswith("lib/*") and ("resaver-cli" + sep) in cp, cp
    assert ("ReSaver.jar" + other) not in cp and ("resaver-cli" + other) not in cp, cp


def test_a_jdk_change_recompiles_the_driver(tmp_path):
    tools = _setup(tmp_path, jdk="24.0.1")
    _run(tmp_path, tools, "Linux")                       # no stamp yet: compiled once, stamp written (24)
    log = tmp_path / "javac.log"
    first = log.read_text().count("javac") if log.exists() else 0
    _run(tmp_path, tools, "Linux")                       # same JDK: no recompile
    assert (log.read_text().count("javac") if log.exists() else 0) == first
    (tmp_path / "bin" / "java").write_text((tmp_path / "bin" / "java").read_text().replace("24.0.1", "21.0.12"),
                                           encoding="utf-8", newline="\n")
    _run(tmp_path, tools, "Linux")                       # JDK 24 -> 21: the class must be rebuilt
    assert log.read_text().count("javac") == first + 1


def test_the_platform_seam_is_ignored_outside_test_mode(tmp_path):
    """A stray RESAVER_CLI_UNAME in a user's environment must not change the real separator."""
    real = subprocess.run([BASH, "-c", "uname -s"], capture_output=True, text=True, timeout=30).stdout.strip()
    windows = real.startswith(("MINGW", "MSYS", "CYGWIN"))
    tools = _setup(tmp_path)
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(tmp_path / "bin").replace("\\", "/"), env.get("PATH", "")])
    env.pop("TOOLKIT_TEST_MODE", None)
    env["RESAVER_CLI_UNAME"] = "Linux" if windows else "MINGW64_NT-10.0"
    p = subprocess.run([BASH, str(tools / "resaver-cli.sh"), "dump", "x.ess"], capture_output=True, text=True,
                       env=env, timeout=60)
    cp = [ln for ln in p.stdout.splitlines() if ln.startswith("CP=")][0]
    assert ("ReSaver.jar" + (";" if windows else ":")) in cp, cp
