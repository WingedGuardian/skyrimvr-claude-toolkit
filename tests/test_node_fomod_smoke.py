"""The FOMOD smoke (tests/fomod-xmlbuilder.test.js) runs against the node_modules a CI step installed with `npm ci`.

This test runs a package MANAGER never: the contained launcher's tripwire blocks npm/pip/winget, so the locked
install must already exist (ci.yml `npm ci` step before the pytest step; locally, run `npm ci` once). Without it the
test skips as COULD NOT CHECK (counted by CI's skip ceiling), unless TOOLKIT_REQUIRE_NODE_DEPS=1, which turns the
missing install into a failure. The smoke's control (it must go RED on injectable fast-xml-parser 4.3.6) needs a
package manager, so it is the `FOMOD smoke control` step of the ci.yml `node` job.
"""
import json
import os
import re
import shutil
import subprocess

import pytest
from conftest import REPO

SMOKE = REPO / "tests" / "fomod-xmlbuilder.test.js"
INSTALLED = REPO / "node_modules" / "fast-xml-parser" / "package.json"


def _unavailable(reason):
    if os.environ.get("TOOLKIT_REQUIRE_NODE_DEPS") == "1":
        pytest.fail("TOOLKIT_REQUIRE_NODE_DEPS=1 but " + reason)
    pytest.skip("COULD NOT CHECK: " + reason)


def test_the_smoke_passes_against_the_installed_locked_fast_xml_parser():
    if not INSTALLED.is_file():
        _unavailable("node_modules/fast-xml-parser is not installed (run `npm ci` outside the launcher first)")
    node = shutil.which("node")
    if not node:
        _unavailable("node is not installed in the test runtime")
    pinned = json.loads((REPO / "package.json").read_text())["dependencies"]["fast-xml-parser"]
    assert json.loads(INSTALLED.read_text())["version"] == pinned, "node_modules is not the locked install"
    r = subprocess.run([node, "--test", str(SMOKE)], cwd=str(REPO), capture_output=True, text=True, timeout=120,
                       env=dict(os.environ, TOOLKIT_REQUIRE_NODE_DEPS="1"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert re.search(r"pass 3$", r.stdout, re.M) and re.search(r"skipped 0$", r.stdout, re.M), r.stdout   # ran


def _outcome():
    try:
        _unavailable("x")
    except pytest.fail.Exception:
        return "fail"
    except pytest.skip.Exception:
        return "skip"


def test_a_missing_install_fails_when_required_and_skips_otherwise(monkeypatch):
    """The decision itself, with no node_modules needed: required means failure, never a quiet pass."""
    monkeypatch.setenv("TOOLKIT_REQUIRE_NODE_DEPS", "1")
    assert _outcome() == "fail"
    monkeypatch.delenv("TOOLKIT_REQUIRE_NODE_DEPS")
    assert _outcome() == "skip"
