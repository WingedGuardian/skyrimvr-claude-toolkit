"""scripts/lock-python-dependencies.py refuses an incomplete common lock, whatever pytest version it resolves.

pytest's closure has three conditional dependencies (exceptiongroup, tomli: Python < 3.11; colorama: Windows). A
single-platform pip report omits them, and a lock built from it would fail to install on the other platforms. The
guard used to be keyed on `pytest == "8.0.0"`, so the first pytest bump would have silently stopped checking.
"""
import importlib.util

import pytest
from conftest import REPO

_spec = importlib.util.spec_from_file_location("lock_python_dependencies", REPO / "scripts" / "lock-python-dependencies.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


def _report(**versions):
    return {"install": [{"metadata": {"name": n, "version": v}} for n, v in versions.items()]}


COMPLETE = dict(pytest="9.0.3", exceptiongroup="1.3.1", tomli="2.4.1", colorama="0.4.6")


@pytest.mark.parametrize("version", ["8.0.0", "8.4.2", "9.0.3", "10.2.1"])
@pytest.mark.parametrize("missing", ["exceptiongroup", "tomli", "colorama"])
def test_an_incomplete_pytest_closure_is_refused_for_any_pytest_version(version, missing):
    versions = dict(COMPLETE, pytest=version)
    del versions[missing]
    with pytest.raises(ValueError, match="missing supported-target dependencies: " + missing):
        gen.merge_reports([_report(**versions)])


@pytest.mark.parametrize("version", ["8.0.0", "9.0.3"])
def test_a_complete_closure_merges_for_any_pytest_version(version):
    merged = gen.merge_reports([_report(**dict(COMPLETE, pytest=version))])
    assert {i["metadata"]["name"] for i in merged["install"]} == set(COMPLETE)


def test_reports_from_two_platforms_merge_into_the_complete_closure():
    linux = _report(pytest="9.0.3", tomli="2.4.1")
    windows = _report(pytest="9.0.3", exceptiongroup="1.3.1", colorama="0.4.6")
    assert len(gen.merge_reports([linux, windows])["install"]) == 4


def test_conflicting_versions_across_reports_are_refused():
    with pytest.raises(ValueError, match="conflicting target versions for pytest"):
        gen.merge_reports([_report(**COMPLETE), _report(**dict(COMPLETE, pytest="8.4.2"))])
