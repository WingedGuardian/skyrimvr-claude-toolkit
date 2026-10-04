"""TOOLKIT_REQUIRE_HARNESS=1 makes a run WITHOUT the package-manager barriers an error.

CI sets it so that "the barriers silently were not installed" can never pass as a green run:
the contained launcher is the only thing that installs them, and a plain `pytest` skips the
modules that need them. The twins below run a child pytest with the barrier environment
stripped, so they are meaningful both inside and outside the contained launcher.
"""
import os
import subprocess
import sys

from conftest import REPO

STRIP = ("HARNESS_", "PYTEST_PLUGINS", "PYTHONPATH", "BASH_ENV", "TOOLKIT_REQUIRE_HARNESS")


def _child(require):
    env = {k: v for k, v in os.environ.items() if not k.startswith(STRIP)}
    if require is not None:
        env["TOOLKIT_REQUIRE_HARNESS"] = require
    return subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "tests/test_python_path.py", "--collect-only", "-q",
         "-p", "no:cacheprovider"],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=300)


def test_requiring_the_harness_without_it_is_an_error():
    result = _child("1")
    assert result.returncode != 0, result.stdout[-800:]
    assert "TOOLKIT_REQUIRE_HARNESS" in (result.stdout + result.stderr)


def test_not_requiring_the_harness_collects_normally():
    # The twin: the same child without the variable must succeed, or the red above is
    # an unrelated collection failure.
    result = _child(None)
    assert result.returncode == 0, result.stdout[-800:] + result.stderr[-800:]
