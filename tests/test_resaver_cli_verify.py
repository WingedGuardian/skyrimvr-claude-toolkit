"""`bash tools/resaver-cli.sh verify`: ReSaver.jar AND its lib/ files against the pinned SHA-256s (fix-limits 5).

Before, only ReSaver.jar had a pinned hash (docs/installing-tools.md) and nothing checked lib/, although every jar
there is on the driver's classpath. The pins are those of the named Nexus archive, MEASURED 2026-10-13 from an
extraction of `Resaver 6.0.643-5031-6-0-643-1694115572.7z` whose ReSaver.jar equals the documented pin; the four
`required` lib jars are byte-identical to the ones the tested install loads.

The wrapper's table and the docs table must agree; the files here are stand-ins whose hashes the test computes, so
the pin values themselves are checked only against the docs, never against real ReSaver bytes (not shipped).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from conftest import BASH, REPO

WRAPPER = REPO / "tools" / "resaver-cli.sh"
DOCS = REPO / "docs" / "installing-tools.md"
ROW = re.compile(r"^([0-9a-f]{64}) (required|optional) (\S+)$")


def _pins():
    text = WRAPPER.read_text(encoding="utf-8")
    block = text.split('PINNED="', 1)[1].split('"', 1)[0]
    rows = [ROW.match(line) for line in block.splitlines()]
    assert all(rows), block
    return [(m.group(1), m.group(2), m.group(3)) for m in rows]


def test_the_wrapper_pins_resaver_jar_and_every_lib_file_of_the_archive():
    pins = _pins()
    names = [n for _h, _k, n in pins]
    assert names[0] == "ReSaver.jar" and len(set(names)) == len(names) == 22
    assert sorted(n for _h, k, n in pins if k == "required") == [
        "ReSaver.jar", "lib/annotations-16.0.2.jar", "lib/j2html-1.5.0.jar", "lib/juniversalchardet-1.0.3.jar",
        "lib/lz4-pure-java-1.7.0.jar"]


def test_the_docs_table_and_the_wrapper_table_agree():
    docs = DOCS.read_text(encoding="utf-8")
    for sha, _kind, name in _pins():
        base = name.split("/")[-1]
        hits = [line for line in docs.splitlines() if ("`" + base + "`") in line and sha in line]
        assert hits, "docs/installing-tools.md has no row naming {} with {}".format(base, sha)


def _install(tmp_path: Path, files: dict) -> Path:
    tools = tmp_path / "tk" / "tools"
    rs = tools / "resaver-cli"
    (rs / "lib").mkdir(parents=True)
    (tools / "resaver-cli.sh").write_bytes(WRAPPER.read_bytes())
    for rel, data in files.items():
        (rs / rel).write_bytes(data)
    return tools


def _pinned_wrapper(tools: Path, files: dict):
    """Rewrite the copied wrapper's table so the stand-in bytes ARE the pinned ones (the real jars are not shipped)."""
    w = tools / "resaver-cli.sh"
    text = w.read_text(encoding="utf-8")
    for sha, kind, name in _pins():
        if name in files:
            text = text.replace("{} {} {}".format(sha, kind, name),
                                "{} {} {}".format(hashlib.sha256(files[name]).hexdigest(), kind, name))
    w.write_text(text, encoding="utf-8", newline="\n")


REQUIRED = {"ReSaver.jar": b"resaver", "lib/annotations-16.0.2.jar": b"a", "lib/j2html-1.5.0.jar": b"j",
            "lib/juniversalchardet-1.0.3.jar": b"u", "lib/lz4-pure-java-1.7.0.jar": b"l"}


def _verify(tools: Path, env=None):
    p = subprocess.run([BASH, str(tools / "resaver-cli.sh"), "verify"], capture_output=True, text=True,
                       env=dict(os.environ, **(env or {})), timeout=60)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def test_the_required_set_verifies(tmp_path):
    tools = _install(tmp_path, REQUIRED)
    _pinned_wrapper(tools, REQUIRED)
    code, out = _verify(tools)
    assert (code, out) == (0, {"ok": True, "checked": 5}), out


def test_a_lib_jar_with_other_bytes_is_named(tmp_path):
    tools = _install(tmp_path, REQUIRED)
    _pinned_wrapper(tools, REQUIRED)
    (tools / "resaver-cli" / "lib" / "lz4-pure-java-1.7.0.jar").write_bytes(b"tampered")
    code, out = _verify(tools)
    assert code == 1 and len(out["problems"]) == 1 and "lib/lz4-pure-java-1.7.0.jar is not the pinned file" in out["problems"][0]


def test_a_changed_resaver_jar_is_named(tmp_path):
    tools = _install(tmp_path, REQUIRED)
    _pinned_wrapper(tools, REQUIRED)
    (tools / "resaver-cli" / "ReSaver.jar").write_bytes(b"other")
    code, out = _verify(tools)
    assert code == 1 and out["problems"][0].startswith("ReSaver.jar is not the pinned file"), out


def test_a_missing_required_jar_is_named_and_a_missing_optional_one_is_not(tmp_path):
    files = dict(REQUIRED)
    del files["lib/j2html-1.5.0.jar"]
    tools = _install(tmp_path, files)
    _pinned_wrapper(tools, files)
    code, out = _verify(tools)
    assert code == 1 and out["problems"] == ["lib/j2html-1.5.0.jar is missing"], out


def test_a_present_optional_file_must_be_the_pinned_one_too(tmp_path):
    files = dict(REQUIRED, **{"lib/picocli-4.6.3.jar": b"not picocli"})
    tools = _install(tmp_path, files)
    _pinned_wrapper(tools, REQUIRED)                  # picocli keeps its real pin; these bytes are not it
    code, out = _verify(tools)
    assert code == 1 and "lib/picocli-4.6.3.jar is not the pinned file" in out["problems"][0], out


def test_an_unknown_jar_on_the_classpath_is_named_but_another_file_is_not(tmp_path):
    files = dict(REQUIRED, **{"lib/evil-1.0.jar": b"x", "lib/README.txt": b"x"})
    tools = _install(tmp_path, files)
    _pinned_wrapper(tools, REQUIRED)
    code, out = _verify(tools)
    assert code == 1 and out["problems"] == ["lib/evil-1.0.jar is not part of ReSaver 6.0.643 (lib/* is on the "
                                             "classpath): remove it"], out


def test_verify_needs_no_jdk(tmp_path):
    """It runs before the JDK probe: a PATH without java still verifies."""
    tools = _install(tmp_path, REQUIRED)
    _pinned_wrapper(tools, REQUIRED)
    code, out = _verify(tools, {"JAVA_HOME": str(tmp_path / "no-jdk-here")})
    assert code == 0 and out["ok"] is True
    src = (tools / "resaver-cli.sh").read_text(encoding="utf-8")
    assert src.index('if [ "${1:-}" = "verify" ]') < src.index("JV=$(java -version")
