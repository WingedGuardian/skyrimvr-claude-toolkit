"""Pytest registration separate from the startup audit hook."""
import os
import hashlib
from pathlib import Path
import tempfile

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "tripwire_control: controlled fake-manager probe")


@pytest.fixture(autouse=True)
def package_manager_attempts(request):
    previous = os.environ["HARNESS_TRIPWIRE_LOG"]
    label = hashlib.sha256(request.node.nodeid.encode("utf-8")).hexdigest()[:12]
    fd, name = tempfile.mkstemp(prefix="tripwire-{}-{}-".format(os.getpid(), label), suffix=".log",
                                dir=Path(previous).parent)
    os.close(fd)
    log = Path(name)
    os.environ["HARNESS_TRIPWIRE_LOG"] = str(log)
    try:
        yield
        attempts = [s for s in log.read_text(encoding="utf-8").splitlines()
                    if s not in ("dotnet --version", "dotnet --list-sdks")]
        if not request.node.get_closest_marker("tripwire_control"):
            assert not attempts, "PACKAGE-MANAGER TRIPWIRE: " + repr(attempts)
    finally:
        os.environ["HARNESS_TRIPWIRE_LOG"] = previous
