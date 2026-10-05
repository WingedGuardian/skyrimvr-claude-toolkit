"""Which plugins.txt a tool reads: Python (skyguard.paths.plugins_txt) and JS (active-plugins.js) must agree.

The cases live in tests/fixtures/paths_precedence.json, which tools/xelib/paths-precedence.test.js reads too, so
the two implementations are held to ONE table (Codex handoff item 4: carry the precedence into the common path
interface without losing PLUGINS_TXT). test_node_agrees_with_python runs the Node side from here as well, so a
pytest run catches drift even where `npm test` is not run.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import paths  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "paths_precedence.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def build(case, tmp):
    """Lay the case out under tmp; return the environment the lookup sees."""
    t = tmp.as_posix()
    sub = lambda s: s.replace("{tmp}", t)  # noqa: E731
    local = tmp / "local"
    for game in case["probe"]:
        (local / game).mkdir(parents=True, exist_ok=True)
        (local / game / "plugins.txt").write_text("*Skyrim.esm\n", encoding="utf-8")
    local.mkdir(exist_ok=True)
    pf = tmp / "skyrim-paths.env"
    pf.write_text(sub(case["paths_file"]), encoding="utf-8", newline="\n")
    env = {"LOCALAPPDATA": str(local), "SKYRIM_PATHS_FILE": str(pf)}
    env.update({k: sub(v) for k, v in case["env"].items()})
    return env, sub(case["want"])


def same(a, b):
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_python_lookup(case, tmp_path):
    env, want = build(case, tmp_path)
    if want == "error":
        with pytest.raises(ValueError):
            paths.plugins_txt(env)
    else:
        assert same(paths.plugins_txt(env), want)


def test_the_fixture_pins_plugins_txt_winning():
    assert CASES[0]["id"] == "plugins-txt-wins-over-everything" and CASES[0]["env"].get("PLUGINS_TXT")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_node_agrees_with_python(case, tmp_path):
    env, want = build(case, tmp_path)
    script = ("const a = require(process.argv[1]); console.warn = () => {};"
              "try { process.stdout.write('OK:' + a.defaultPluginsTxt()); }"
              "catch (e) { process.stdout.write('ERR:' + e.message); }")
    full = {k: v for k, v in os.environ.items()
            if k not in ("PLUGINS_TXT", "SKYRIM_LOADORDER_DIR", "SKYRIM_PATHS_FILE", "LOCALAPPDATA")}
    full.update(env)
    r = subprocess.run(["node", "-e", script, str(REPO / "tools" / "xelib" / "active-plugins.js")],
                       capture_output=True, text=True, env=full, timeout=60)
    out = r.stdout
    if want == "error":
        assert out.startswith("ERR:"), out
    else:
        assert out.startswith("OK:") and same(out[3:], want), out
