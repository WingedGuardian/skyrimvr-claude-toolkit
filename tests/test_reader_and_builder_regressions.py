"""Malformed-reader and source-ownership failures from the 4.0 review."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, REPO / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.mark.parametrize("command", ["info", "plugins", "search"])
def test_invalid_save_cli_exits_nonzero_with_json(tmp_path, command):
    path = tmp_path / "invalid.ess"
    path.write_bytes(b"TESV_SAVEGAME" + b"\x00" * 4)
    args = [sys.executable, "-B", str(REPO / "scripts/read-save.py"), command, str(path)]
    if command == "search":
        args += ["--string", "needle"]
    result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    assert result.returncode != 0
    assert "error" in json.loads(result.stdout)


def test_truncated_plugin_list_never_returns_partial_counts():
    reader = module("save_reader_regression", "scripts/read-save.py")
    # One announced name, with fewer bytes than its declared length.
    with pytest.raises(ValueError):
        reader.read_plugins(b"", b"\x00" * 5 + b"\x01\x09\x00A")
    assert reader.read_plugins(b"", b"\x00" * 5 + b"\x01\x01\x00A\x00\x00") == (["A"], [])


def test_raw_hit_contexts_are_bounded_but_counts_are_complete():
    reader = module("save_reader_hit_regression", "scripts/read-save.py")
    result = reader.search_string(b"probe " * 1000, "probe")
    assert result.total == 1000 and len(result) == 50


def test_builder_refuses_dirty_existing_source_without_discarding_edits(tmp_path):
    builder = module("driver_build_regression", "tools/vr-emu-driver/build.py")
    source = tmp_path / "driver"
    subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
    path = source / "source.cpp"
    path.write_text("original")
    subprocess.run(["git", "-C", str(source), "add", "source.cpp"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "-m", "fixture"], check=True, capture_output=True)
    head = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    path.write_text("valuable uncommitted edit")
    with pytest.raises(builder.BuildError, match="local changes"):
        builder.fetch("unused.invalid", head, source)
    assert path.read_text() == "valuable uncommitted edit"


@pytest.mark.skipif(os.name != "nt", reason="MSVC builder entry point is Windows only")
def test_builder_preserves_unowned_src_and_build_script(tmp_path, monkeypatch):
    builder = module("driver_build_main_regression", "tools/vr-emu-driver/build.py")
    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    (work / "src/valuable.txt").write_text("unowned")
    (work / "build.bat").write_text("user script")
    monkeypatch.setattr(builder, "vcvars", lambda: tmp_path / "vcvars.bat")
    monkeypatch.setattr(builder, "BUILD", tmp_path / "output")

    def fetch(repo, commit, dest, sparse=None):
        dest.mkdir()
        (dest / builder.PATCH["file"]).write_text(builder.PATCH["find"])

    monkeypatch.setattr(builder, "fetch", fetch)
    monkeypatch.setattr(builder, "run", lambda *args, **kwargs: (_ for _ in ()).throw(builder.BuildError("fake compiler")))
    assert builder.main(["--work", str(work)]) == 2
    assert (work / "src/valuable.txt").read_text() == "unowned"
    assert (work / "build.bat").read_text() == "user script"
    assert len(list(work.glob("owned-build-*/.toolkit-build-owner.json"))) == 1
