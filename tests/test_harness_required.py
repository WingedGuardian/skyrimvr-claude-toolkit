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


def test_probe_the_launcher_forwarded_the_flag():
    """Only meaningful as the CHILD of the test below: it is skipped unless named on the command
    line, so a normal run never sees it fail."""
    import pytest
    from conftest import harness_active
    if not any("test_probe_the_launcher_forwarded_the_flag" in a for a in sys.argv):
        pytest.skip("probe: runs only when targeted by the launcher test")
    assert harness_active()
    assert os.environ.get("TOOLKIT_REQUIRE_HARNESS") == "1"


def test_the_launcher_forwards_the_require_flag_to_pytest():
    # The launcher scrubs its environment. If the flag were not on its allow-list, the
    # "barriers are not active" check above could never fire under it, and CI would carry a
    # gate that cannot fail. The probe asserts the flag is visible INSIDE the contained run.
    env = {k: v for k, v in os.environ.items() if not k.startswith(STRIP)}
    env["TOOLKIT_REQUIRE_HARNESS"] = "1"
    result = subprocess.run(
        [sys.executable, "-B", "tests/run_harness_tests.py",
         "tests/test_harness_required.py::test_probe_the_launcher_forwarded_the_flag", "-q"],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout[-1500:] + result.stderr[-800:]
    assert "1 passed" in result.stdout, result.stdout[-800:]


def test_not_requiring_the_harness_collects_normally():
    # The twin: the same child without the variable must succeed, or the red above is
    # an unrelated collection failure.
    result = _child(None)
    assert result.returncode == 0, result.stdout[-800:] + result.stderr[-800:]
