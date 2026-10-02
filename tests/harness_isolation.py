"""Test-only barriers. Python writes are constrained; shell effects require reviewed fixtures."""
import os
from pathlib import Path
import re
import sys

MANAGERS = set("winget choco scoop apt apt-get dnf pacman brew npm pip pip3 dotnet".split())


def install():
    root = Path(os.environ["HARNESS_FIXTURE_ROOT"]).resolve()

    def contained(value):
        if isinstance(value, int) or value is None:
            return True
        value = os.fsdecode(value)
        if value.lower() in ("nul", "/dev/null", r"\\.\nul"):
            return True
        if value.startswith("\\\\?\\"):
            value = "\\\\" + value[8:] if value[4:].startswith("UNC\\") else value[4:]
        return Path(value).resolve().is_relative_to(root)

    def audit(event, args):
        if event == "open":
            filename, mode, flags = args
            writing = any(c in (mode or "") for c in "wax+") or flags & (
                os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            if writing and not contained(filename):
                raise PermissionError("outside test fixture: " + str(filename))
        elif event in ("os.mkdir", "os.remove", "os.rmdir", "os.rename", "os.symlink", "os.link"):
            count = 2 if event in ("os.rename", "os.symlink", "os.link") else 1
            if any(not contained(p) for p in args[:count]):
                raise PermissionError("outside test fixture: " + repr(args[:count]))
        elif event == "subprocess.Popen":
            executable, argv, _, env = args
            child_env = env if env is not None else os.environ
            child_env["HARNESS_NATIVE_PATH"] = child_env.get("PATH", "")
            tokens = argv if isinstance(argv, (tuple, list)) else re.findall(r'"([^"]*)"|(\S+)', argv)
            if isinstance(argv, str):
                tokens = [a or b for a, b in tokens]
            first = str(executable or (tokens[0] if tokens else ""))
            name = Path(first).name.lower().removesuffix(".exe").removesuffix(".cmd").removesuffix(".ps1")
            module_pip = "-m" in tokens and any(str(t) in ("pip", "pip3") for t in tokens)
            npm_cli = any(str(t).replace("\\", "/").endswith("/npm-cli.js") for t in tokens)
            if name in MANAGERS or module_pip or npm_cli:
                with open(os.environ["HARNESS_TRIPWIRE_LOG"], "a", encoding="utf-8") as log:
                    log.write("native " + repr(argv) + "\n")
                raise PermissionError("PACKAGE-MANAGER TRIPWIRE: " + repr(argv))
    sys.addaudithook(audit)


def pytest_configure(config):
    config.addinivalue_line("markers", "tripwire_control: controlled fake-manager probe")


import pytest


@pytest.fixture(autouse=True)
def package_manager_attempts(request):
    log = Path(os.environ["HARNESS_TRIPWIRE_LOG"])
    before = log.read_text() if log.exists() else ""
    yield
    after = log.read_text() if log.exists() else ""
    attempts = [s for s in after[len(before):].splitlines()
                if s not in ("dotnet --version", "dotnet --list-sdks")]
    if not request.node.get_closest_marker("tripwire_control"):
        assert not attempts, "PACKAGE-MANAGER TRIPWIRE: " + repr(attempts)
