"""tools/resaver-cli.sh: the classpath separator is the platform's, and a JDK change recompiles the driver.

Review P1 (Plan 8 T6d fix round 1): the wrapper joined its classpath with ';', the Windows separator, so on Linux
(the devcontainer) java read the whole string as one path. The platform comes from `uname -s`; the test seam
RESAVER_CLI_UNAME replaces it (honoured only under TOOLKIT_TEST_MODE=1). A fake `java` prints the -cp it was given.
"""
from __future__ import annotations

import os
import hashlib
import re
import shutil
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
    (tools / "resaver_cli_cache.py").write_bytes((REPO / "tools" / "resaver_cli_cache.py").read_bytes())
    shutil.copytree(REPO / "tools/modding_harness", tools / "modding_harness",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (rs / "ResaverCLI.java").write_bytes((REPO / "tools" / "resaver-cli" / "ResaverCLI.java").read_bytes())
    (rs / "ReSaver.jar").write_bytes(b"PK")
    for name in ("annotations-16.0.2.jar", "j2html-1.5.0.jar", "juniversalchardet-1.0.3.jar", "lz4-pure-java-1.7.0.jar"):
        (rs / "lib").mkdir(exist_ok=True)
        (rs / "lib" / name).write_bytes(b"fixture jar")
    # Real verification still runs; the copied fixture declares its own fake
    # files' hashes. No production test-mode bypass is introduced.
    wrapper = tools / "resaver-cli.sh"
    wrapper.write_text(re.sub(r"[0-9a-f]{64} required (\S+)",
        lambda m: hashlib.sha256((rs / m.group(1)).read_bytes()).hexdigest() + " required " + m.group(1),
        wrapper.read_text()), encoding="utf-8", newline="\n")
    cls = rs / "ResaverCLI.class"
    cls.write_bytes(b"\xca\xfe")
    os.utime(cls, (os.stat(rs / "ResaverCLI.java").st_mtime + 60,) * 2)
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    _script(b / "java", 'if [ "$1" = "-version" ]; then echo \'openjdk version "%s"\' >&2; exit 0; fi\n'
                        'while [ $# -gt 0 ]; do [ "$1" = "-cp" ] && { echo "CP=$2"; exit 0; }; shift; done\n' % jdk)
    _script(b / "javac", 'if [ "$1" = "-version" ]; then echo "javac 21 fixture"; exit 0; fi\n'
            'echo javac >> "%s"\n' % (tmp_path / "javac.log").as_posix() +
            'while [ $# -gt 0 ]; do if [ "$1" = "-d" ]; then mkdir -p "$2"; '
            'printf "fixture-class" > "$2/ResaverCLI.class"; exit 0; fi; shift; done\nexit 2\n')
    return tools


def _run(tmp_path: Path, tools: Path, uname: str, op: str = "dump") -> str:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(tmp_path / "bin").replace("\\", "/"), env.get("PATH", "")])
    env["TOOLKIT_TEST_MODE"] = "1"
    env["RESAVER_CLI_UNAME"] = uname
    p = subprocess.run([BASH, str(tools / "resaver-cli.sh"), op, "x.ess"], capture_output=True, text=True,
                       encoding="utf-8", env=env, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    line = [ln for ln in p.stdout.splitlines() if ln.startswith("CP=")]
    assert line, p.stdout + p.stderr
    return line[0][3:]


@pytest.mark.parametrize("uname,sep,other", [("Linux", ":", ";"), ("Darwin", ":", ";"),
                                             ("MINGW64_NT-10.0-26200", ";", ":"), ("MSYS_NT-10.0", ";", ":")])
def test_the_classpath_separator_is_the_platforms(tmp_path, uname, sep, other):
    cp = _run(tmp_path, _setup(tmp_path), uname)
    # (the test host is Windows, so the paths themselves may hold ':' -- check the joins, not a blind split)
    assert ("ReSaver.jar" + sep) in cp and cp.endswith("lib/*") and ("driver" + sep) in cp, cp
    assert ("ReSaver.jar" + other) not in cp and ("driver" + other) not in cp, cp


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


def test_source_and_jar_content_changes_recompile_despite_old_mtime(tmp_path):
    tools = _setup(tmp_path)
    _run(tmp_path, tools, "Linux")
    source = tools / "resaver-cli/ResaverCLI.java"
    before = source.stat()
    source.write_bytes(source.read_bytes() + b"\n// regression fixture\n")
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    _run(tmp_path, tools, "Linux")
    assert (tmp_path / "javac.log").read_text().count("javac") == 2
    jar = tools / "resaver-cli/ReSaver.jar"
    before = jar.stat()
    jar.write_bytes(b"CHANGED JAR")
    os.utime(jar, ns=(before.st_atime_ns, before.st_mtime_ns))
    _run(tmp_path, tools, "Linux")
    assert (tmp_path / "javac.log").read_text().count("javac") == 3


def test_failed_write_rebuild_never_executes_legacy_or_cached_class(tmp_path):
    tools = _setup(tmp_path)
    _run(tmp_path, tools, "Linux", op="clean")
    source = tools / "resaver-cli/ResaverCLI.java"
    source.write_bytes(source.read_bytes() + b"\n// changed\n")
    _script(tmp_path / "bin/javac", 'if [ "$1" = "-version" ]; then echo "javac 21 fixture"; exit 0; fi\nexit 7\n')
    env = dict(os.environ, PATH=os.pathsep.join([str(tmp_path / "bin"), os.environ.get("PATH", "")]))
    result = subprocess.run([BASH, str(tools / "resaver-cli.sh"), "clean", "x.ess", "out.ess", "--apply"],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0 and "CP=" not in result.stdout
    assert "no cached-class fallback" in result.stdout + result.stderr


def test_corrupt_class_cache_is_not_executed(tmp_path):
    tools = _setup(tmp_path)
    _run(tmp_path, tools, "Linux")
    compiled = next((tools / "resaver-cli/.compiled").glob("*/driver/ResaverCLI.class"))
    compiled.write_bytes(b"corrupted class")
    _run(tmp_path, tools, "Linux")
    assert (tmp_path / "javac.log").read_text().count("javac") == 2
    assert list((tools / "resaver-cli/.compiled").glob("invalid-*"))


def test_nonascii_cache_and_source_paths_survive_transport(tmp_path):
    root = tmp_path / "雪 toolkit"
    tools = _setup(root)
    cp = _run(root, tools, "Linux")
    assert cp.startswith(".compiled/") and "雪 toolkit" not in cp
    assert list((tools / "resaver-cli/.compiled").glob("*/driver/ResaverCLI.class"))
    assert not list((tools / "resaver-cli/.compiled").glob("arguments-*"))
