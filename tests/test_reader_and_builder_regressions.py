"""Malformed-reader and source-ownership failures from the 4.0 review."""
import importlib.util
import json
import subprocess
import sys

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


def _driver_zip(members):
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in members.items():
            z.writestr(name, body)
    return buf.getvalue()


def _fetcher(monkeypatch, members, tmp_path):
    import hashlib
    fetch = module("driver_fetch_regression", "tools/vr-emu-driver/fetch.py")
    data = _driver_zip(members)
    monkeypatch.setattr(fetch, "DRIVER_ZIP_SHA256", hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(fetch, "DRIVER_DLL_SHA256", hashlib.sha256(b"dll").hexdigest())
    monkeypatch.setattr(fetch, "BUILD", tmp_path / "build")
    return fetch, data


GOOD_DRIVER = {"null/bin/win64/driver_null.dll": b"dll", "null/driver.vrdrivermanifest": b"{}",
               "null/resources/settings/default.vrsettings": b"{}"}


def _sidecar(members):
    import hashlib
    return dict(members, **{"null/bin/win64/driver_null.dll.sha256": hashlib.sha256(b"dll").hexdigest().encode()})


def test_fetcher_accepts_only_the_pinned_package(tmp_path, monkeypatch):
    fetch, data = _fetcher(monkeypatch, _sidecar(GOOD_DRIVER), tmp_path)
    files = fetch.verify(data)
    assert set(fetch.SWAPPED) <= set(files)
    with pytest.raises(fetch.FetchError, match="not the pinned"):
        fetch.verify(data + b"tampered")


@pytest.mark.parametrize("members, why", [
    (dict(_sidecar(GOOD_DRIVER), **{"../escape.dll": b"x"}), "unexpected path"),
    (dict(_sidecar(GOOD_DRIVER), **{"elsewhere/file": b"x"}), "unexpected path"),
    (GOOD_DRIVER, "sha256 says"),
    ({k: v for k, v in _sidecar(GOOD_DRIVER).items() if "manifest" not in k}, "lacks"),
    (dict(_sidecar(GOOD_DRIVER), **{"null/bin/win64/driver_null.dll": b"other dll"}), "not the pinned"),
])
def test_fetcher_refuses_a_package_with_a_bad_layout_or_hash(tmp_path, monkeypatch, members, why):
    fetch, data = _fetcher(monkeypatch, members, tmp_path)
    with pytest.raises(fetch.FetchError, match=why):
        fetch.verify(data)


def test_fetcher_never_overwrites_a_different_existing_package(tmp_path, monkeypatch):
    fetch, data = _fetcher(monkeypatch, _sidecar(GOOD_DRIVER), tmp_path)
    files = fetch.verify(data)
    dest = tmp_path / "build" / "null"
    fetch.install(files, dest)
    fetch.install(files, dest)                                  # identical: a no-op
    (dest / "driver.vrdrivermanifest").write_bytes(b"the user's edit")
    with pytest.raises(fetch.FetchError, match="different package"):
        fetch.install(files, dest)
    assert (dest / "driver.vrdrivermanifest").read_bytes() == b"the user's edit"


def test_fetcher_pins_agree_with_the_readme():
    fetch = module("driver_fetch_pins", "tools/vr-emu-driver/fetch.py")
    readme = (REPO / "tools/vr-emu-driver/README.md").read_text(encoding="utf-8")
    for pin in (fetch.DRIVER_COMMIT, fetch.DRIVER_ZIP_SHA256, fetch.DRIVER_DLL_SHA256, fetch.DRIVER_TAG):
        assert pin in readme, pin
    assert fetch.DRIVER_COMMIT in fetch.DRIVER_ZIP_URL and fetch.DRIVER_ZIP_URL.startswith("https://")
