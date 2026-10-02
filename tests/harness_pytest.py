"""Pytest registration separate from the startup audit hook."""
import os
from pathlib import Path

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "tripwire_control: controlled fake-manager probe")


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
