"""tools/resaver-cli.sh backend selection (port task 5; user ruling 2026-10-25: Java is the DEFAULT, native is opt-in,
and a failing backend never silently falls back to the other).

The wrapper and the adapter are copied into tmp_path (the backend file is written there, never in the repo). No
ReSaver.jar is present, so reaching the Java backend is visible as its own "ReSaver.jar not found" refusal; reaching
the native backend is visible as the adapter's JSON (backend "ReSaver Plus native"), run against a stub executable
pinned by a synthetic BUILD-INFO.json.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import BASH, REPO

STUB = 'import json, sys\nprint(json.dumps({"ok": True, "op": sys.argv[2], "stub": True}))\n'
JAVA_MISSING = "ReSaver.jar not found"


@pytest.fixture
def kit(tmp_path):
    tools = tmp_path / "tools"
    (tools / "resaver-cli").mkdir(parents=True)
    for name in ("resaver-cli.sh", "resaver_native.py", "resaver_cli_cache.py"):
        shutil.copy2(REPO / "tools" / name, tools / name)
    (tools / "resaver-plus").mkdir()
    build = tmp_path / "build"
    build.mkdir()
    (build / "resaverpp-cli.exe").write_text(STUB, encoding="utf-8")
    sha = hashlib.sha256((build / "resaverpp-cli.exe").read_bytes()).hexdigest()
    (build / "BUILD-INFO.json").write_text(json.dumps({"upstream_commit": "0d48f60cb8a69b367abfbd78d58418e64c507da5",
                                                       "executable": "resaverpp-cli.exe", "sha256": sha}),
                                           encoding="utf-8")
    save = tmp_path / "Synthetic.ess"
    save.write_bytes(b"synthetic")
    return {"tools": tools, "build": build, "save": save, "tmp": tmp_path}


def wrapper(kit, *args, backend=None, extra=None):
    env = {k: v for k, v in os.environ.items() if k not in ("RESAVER_BACKEND", "RESAVER_NATIVE_BUILD")}
    env.update({"TOOLKIT_TEST_MODE": "1", "RESAVER_NATIVE_BUILD": str(kit["build"]),
                "RESAVER_NATIVE_INTERPRETER": sys.executable})
    if backend is not None:
        env["RESAVER_BACKEND"] = backend
    env.update(extra or {})
    p = subprocess.run([BASH, str(kit["tools"] / "resaver-cli.sh"), *map(str, args)], capture_output=True, text=True,
                       timeout=120, env=env)
    last = (p.stdout.strip().splitlines() or ["{}"])[-1]
    try:
        out = json.loads(last)
    except ValueError:
        out = {"raw": p.stdout + p.stderr}
    return p.returncode, out, p.stdout + p.stderr


def ran_native(out):
    return out.get("backend") == "ReSaver Plus native"


def test_the_default_is_java(kit):
    code, out, text = wrapper(kit, "info", kit["save"])
    assert JAVA_MISSING in text and not ran_native(out) and code != 0
    assert wrapper(kit, "backend")[1]["backend"] == "java"


def test_native_is_opt_in_by_variable(kit):
    code, out, _ = wrapper(kit, "info", kit["save"], backend="native")
    assert code == 0 and out["stub"] and ran_native(out), out


def test_native_is_opt_in_by_the_backend_file(kit):
    (kit["tools"] / "resaver-plus" / "backend").write_text("native\n", encoding="utf-8")
    code, out, _ = wrapper(kit, "info", kit["save"])
    assert code == 0 and ran_native(out)
    # The variable wins over the file.
    code, out, text = wrapper(kit, "info", kit["save"], backend="java")
    assert JAVA_MISSING in text and not ran_native(out)


def test_explicit_native_without_a_build_is_an_error_and_java_does_not_run(kit):
    (kit["build"] / "BUILD-INFO.json").unlink()
    code, out, text = wrapper(kit, "info", kit["save"], backend="native")
    assert code == 2 and not out["ok"] and "build.py" in out["error"]
    assert JAVA_MISSING not in text


def test_explicit_native_with_a_changed_executable_is_an_error_and_java_does_not_run(kit):
    (kit["build"] / "resaverpp-cli.exe").write_text(STUB + "# edited\n", encoding="utf-8")
    code, out, text = wrapper(kit, "info", kit["save"], backend="native")
    assert code == 2 and "SHA-256 mismatch" in out["error"] and JAVA_MISSING not in text


def test_explicit_java_that_cannot_run_is_an_error_and_native_does_not_run(kit):
    code, out, text = wrapper(kit, "info", kit["save"], backend="java")
    assert code != 0 and JAVA_MISSING in text and not ran_native(out) and "stub" not in text


def test_an_unknown_backend_is_refused(kit):
    code, out, _ = wrapper(kit, "info", kit["save"], backend="auto")
    assert code == 2 and "must be java or native" in out["error"]


def test_writer_scan_on_java_says_native_only_and_runs_nothing(kit):
    code, out, text = wrapper(kit, "writer-scan", kit["save"])
    assert code == 2 and "native backend" in out["error"] and JAVA_MISSING not in text


def test_verify_follows_the_selected_backend(kit):
    code, out, _ = wrapper(kit, "verify", backend="native")
    assert code == 0 and out["backend"] == "native" and out["sha256"]
    code, out, _ = wrapper(kit, "verify-native")
    assert code == 0 and out["backend"] == "native"
    (kit["build"] / "BUILD-INFO.json").unlink()
    code, out, _ = wrapper(kit, "verify-native")
    assert code == 1 and not out["ok"]
