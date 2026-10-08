"""Tripwire evidence belongs to the test that caused it, including intentional controls.

Exercise overlapping fixture lifetimes deterministically, without scheduling races or a nested full suite.
Every manager probe is intercepted by the existing native audit or shell stub; nothing installs software.
"""
import os
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from conftest import BASH
from harness_isolation import active

# The launcher preloads its pytest plugin through PYTHONPATH. Mutation children
# inherit that path, so a plain import silently tests the parent checkout.
# Judge this test's own sibling implementation, including in a copied tree.
_fixture_path = Path(__file__).resolve().with_name("harness_pytest.py")
_fixture_spec = importlib.util.spec_from_file_location("_tripwire_fixture_under_test", _fixture_path)
harness_pytest = importlib.util.module_from_spec(_fixture_spec)
_fixture_spec.loader.exec_module(harness_pytest)

pytestmark = [pytest.mark.skipif(not active(), reason="COULD NOT CHECK: contained harness required"),
              pytest.mark.tripwire_control]


def _scope(label, control=False):
    node = SimpleNamespace(nodeid="tripwire-overlap::" + label,
                           get_closest_marker=lambda name: object() if control else None)
    return harness_pytest.package_manager_attempts.__wrapped__(SimpleNamespace(node=node))


def _blocked_probe(kind):
    if kind == "native":
        with pytest.raises(PermissionError, match="PACKAGE-MANAGER TRIPWIRE"):
            subprocess.run([sys.executable, "-m", "pip", "--version"])
    else:
        result = subprocess.run([BASH, "-c", "npm --version"], capture_output=True, text=True, timeout=15)
        assert result.returncode == 97 and "PACKAGE-MANAGER TRIPWIRE" in result.stderr


@pytest.mark.parametrize("kind", ["native", "shell"])
def test_overlapping_control_attempt_does_not_accuse_the_innocent_test(tmp_path, monkeypatch, kind):
    baseline = tmp_path / "launcher-tripwire.log"
    monkeypatch.setenv("HARNESS_TRIPWIRE_LOG", str(baseline))
    innocent, control = _scope("innocent"), _scope("other-worker-control", control=True)
    try:
        next(innocent)
        innocent_log = Path(os.environ["HARNESS_TRIPWIRE_LOG"])
        next(control)
        control_log = Path(os.environ["HARNESS_TRIPWIRE_LOG"])
        _blocked_probe(kind)
        with pytest.raises(StopIteration):
            next(control)
        # The old shared-log fixture fails HERE: foreign native pip / shell npm appears in its slice.
        with pytest.raises(StopIteration):
            next(innocent)
        assert innocent_log != control_log and innocent_log != baseline
        assert control_log.is_file() and ("native " if kind == "native" else "npm --version") in control_log.read_text()
        assert innocent_log.read_text() == "", "control evidence must not be charged to the innocent test"
        assert os.environ["HARNESS_TRIPWIRE_LOG"] == str(baseline)
    finally:
        control.close()
        innocent.close()


@pytest.mark.parametrize("kind", ["native", "shell"])
def test_an_unmarked_tests_own_attempt_still_fails_and_keeps_its_evidence(tmp_path, monkeypatch, kind):
    baseline = tmp_path / "launcher-tripwire.log"
    monkeypatch.setenv("HARNESS_TRIPWIRE_LOG", str(baseline))
    scope = _scope("actual-offender")
    try:
        next(scope)
        scoped_log = Path(os.environ["HARNESS_TRIPWIRE_LOG"])
        _blocked_probe(kind)
        with pytest.raises(AssertionError, match="PACKAGE-MANAGER TRIPWIRE"):
            next(scope)
        assert scoped_log != baseline, "the offender needs a distinct retained evidence file"
        assert scoped_log.is_file() and ("native " if kind == "native" else "npm --version") in scoped_log.read_text()
        assert os.environ["HARNESS_TRIPWIRE_LOG"] == str(baseline), "restore the parent log even on assertion failure"
    finally:
        scope.close()


def test_aborted_scope_restores_the_parent_log(tmp_path, monkeypatch):
    baseline = tmp_path / "launcher-tripwire.log"
    monkeypatch.setenv("HARNESS_TRIPWIRE_LOG", str(baseline))
    scope = _scope("aborted-control", control=True)
    try:
        next(scope)
        scoped_log = Path(os.environ["HARNESS_TRIPWIRE_LOG"])
        scope.close()  # e.g. interrupted test: the fixture's finally must restore the previous scope
        assert scoped_log != baseline, "each test scope must have its own evidence file"
        assert scoped_log.is_file(), "retain even an empty scoped log so coverage is reviewable"
        assert os.environ["HARNESS_TRIPWIRE_LOG"] == str(baseline)
    finally:
        scope.close()
