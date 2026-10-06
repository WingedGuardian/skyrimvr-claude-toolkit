"""Deferred row 24 (Plan 8 T6): an environment scrub that keeps SystemRoot must keep SystemDrive too.

Without SystemDrive, py.exe and the python3 Store alias create a literal `%SystemDrive%/ProgramData/...` cache folder in
the current directory -- the repository, under pytest (found in Plan 4 Task 11; fixed site by site in 639c5ac). The
scrubbed install check (T15) relies on the launcher's scrub. Nothing held either to it, so the next minimal env written
would bring the stray folder back. Two checks: the launcher's forwarded names, and every minimal env built in Python
anywhere in the repo's tests, scripts and tools.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import warnings

import pytest

from conftest import REPO

SCANNED = ("tests", "scripts", "tools")


def _launcher():
    spec = importlib.util.spec_from_file_location("run_harness_tests", REPO / "tests" / "run_harness_tests.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_launcher_scrub_keeps_systemroot_and_systemdrive():
    names = {n.upper() for n in _launcher().FORWARDED_ENV}
    assert {"SYSTEMROOT", "SYSTEMDRIVE"} <= names, sorted(names)


@pytest.mark.skipif(os.name != "nt", reason="SystemDrive is a Windows variable")
@pytest.mark.skipif(not os.environ.get("HARNESS_TRIPWIRE_LOG"), reason="only meaningful under tests/run_harness_tests.py")
def test_under_the_launcher_systemdrive_reaches_the_tests():
    """From the outside: the scrubbed environment this very test runs in still carries SystemDrive."""
    assert os.environ.get("SYSTEMDRIVE"), "the launcher's scrub dropped SystemDrive"


def _const(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def scrub_problems(source: str, where: str = "<src>"):
    """Each dict literal keyed, or tuple/list/set literal listing, SystemRoot (any case) without SystemDrive."""
    out = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")            # another file's invalid escapes are not this check's business
        tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            names = [_const(k) for k in node.keys if k is not None]
        elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            names = [_const(e) for e in node.elts]
        else:
            continue
        upper = {n.upper() for n in names if n}
        if "SYSTEMROOT" in upper and "SYSTEMDRIVE" not in upper:
            out.append("{}:{}".format(where, node.lineno))
    return out


def _sources():
    for top in SCANNED:
        for p in sorted((REPO / top).rglob("*.py")):
            if "__pycache__" in p.parts or "node_modules" in p.parts:
                continue
            yield p


def test_every_minimal_env_that_keeps_systemroot_keeps_systemdrive():
    bad = []
    for p in _sources():
        try:
            bad += scrub_problems(p.read_text(encoding="utf-8"), p.relative_to(REPO).as_posix())
        except SyntaxError:
            continue                      # fixtures that are not Python; ruff/compile checks own them
    assert bad == [], "an env scrub keeps SystemRoot but drops SystemDrive (stray %SystemDrive% folder): " + ", ".join(bad)


def test_the_scan_can_fail():
    """The check goes red on the shape it exists for, in each literal form, and stays green on the fixed form."""
    assert scrub_problems('env = {"PATH": p, "SYSTEMROOT": r}\n')
    assert scrub_problems('keep = ("SystemRoot", "COMSPEC")\n')
    assert scrub_problems('keep = ["SYSTEMROOT"]\n')
    assert not scrub_problems('env = {"SystemRoot": r, "SYSTEMDRIVE": d}\n')
    assert not scrub_problems('os.environ.get("SystemRoot", "C:/Windows")\n')
