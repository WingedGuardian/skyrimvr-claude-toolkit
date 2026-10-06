"""The FOMOD smoke (tests/fomod-xmlbuilder.test.js) runs against the LOCKED install, and goes red on a vulnerable one.

`npm ci` into a scratch directory from the real package.json + package-lock.json, then `node --test` on the smoke:
that is the container's own provisioning path, so a lock that installs but cannot build a FOMOD is caught here.
The control installs fast-xml-parser 4.3.6 (before the XMLBuilder comment/CDATA escaping fix, GHSA-gh4j-gqv2-49f6)
and requires the same smoke to FAIL there: a smoke that cannot fail proves nothing. Needs node, npm and the registry.
"""
import json
import re
import shutil
import subprocess

import pytest
from conftest import REPO

SMOKE = REPO / "tests" / "fomod-xmlbuilder.test.js"


def _run(args, cwd, timeout=240):
    exe = shutil.which(args[0])
    if not exe:
        pytest.skip("COULD NOT CHECK: %s is not installed in the test runtime" % args[0])
    try:
        return subprocess.run([exe] + args[1:], cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        pytest.skip("COULD NOT CHECK: %s timed out (registry unreachable?)" % args[0])


def _stage(tmp_path, name):
    d = tmp_path / name
    d.mkdir()
    shutil.copy(SMOKE, d / SMOKE.name)
    return d


def _install_or_skip(r):
    if r.returncode != 0:
        if any(w in (r.stdout + r.stderr) for w in ("ENOTFOUND", "EAI_AGAIN", "ETIMEDOUT", "ECONNREFUSED", "network")):
            pytest.skip("COULD NOT CHECK: npm registry unreachable")
        pytest.fail("npm install failed:\n" + r.stdout + r.stderr)


def test_the_smoke_passes_against_the_locked_npm_ci_install(tmp_path):
    d = _stage(tmp_path, "locked")
    shutil.copy(REPO / "package.json", d / "package.json")
    shutil.copy(REPO / "package-lock.json", d / "package-lock.json")
    _install_or_skip(_run(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], d))
    r = _run(["node", "--test", SMOKE.name], d)
    assert r.returncode == 0, r.stdout + r.stderr
    assert re.search(r"pass 3$", r.stdout, re.M) and re.search(r"skipped 0$", r.stdout, re.M), r.stdout   # ran, not skipped


def test_the_smoke_goes_red_on_a_fast_xml_parser_before_the_escaping_fix(tmp_path):
    d = _stage(tmp_path, "control")
    (d / "package.json").write_text(json.dumps({"name": "control", "version": "1.0.0", "private": True}))
    _install_or_skip(_run(["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund",
                           "fast-xml-parser@4.3.6"], d))
    r = _run(["node", "--test", SMOKE.name], d)
    assert r.returncode != 0, "the smoke passed on injectable fast-xml-parser 4.3.6:\n" + r.stdout
    assert "created an element" in r.stdout, r.stdout
