"""Final review tools probes; all payloads and manifests are invented."""
import json
import os
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_housekeep import hk, cfg_for, write, age, planned, key, DAY
from test_review_install import load as install_load


def test_fresh_7z_extraction_with_old_mtimes_is_kept(tmp_path):
    seven = shutil.which("7z") or ("C:/Program Files/7-Zip/7z.exe" if os.path.isfile("C:/Program Files/7-Zip/7z.exe") else None)
    if seven is None:
        pytest.skip("7z required for the reviewer's extraction probe")
    staging = tmp_path / "staging"
    staging.mkdir()
    for ver in ("1.0.0", "2.0.0"):
        archive = tmp_path / (ver + ".zip")
        with zipfile.ZipFile(archive, "w") as z:
            for suffix in ("/", "/payload.txt"):
                z.writestr(zipfile.ZipInfo("My Mod-" + ver + suffix, (2024, 1, 2, 3, 4, 5)), b"payload")
        subprocess.run([seven, "x", "-y", "-o" + str(staging), str(archive)], check=True, capture_output=True)
    assert (staging / "My Mod-1.0.0").stat().st_mtime < time.time() - 60 * DAY
    assert not planned(hk.plan(cfg_for(tmp_path, staging_roots=["staging"]), only={"superseded"}))


@pytest.mark.parametrize("entry", ["folder", "payload"])
def test_staging_creation_time_is_activity(tmp_path, monkeypatch, entry):
    old = tmp_path / "staging/Mod-1.0"
    payload = write(old / "payload.txt", b"old", days=60)
    write(tmp_path / "staging/Mod-2.0/payload.txt", b"new", days=60)
    age(old, 60)
    original = Path.stat
    fresh = old if entry == "folder" else payload
    def stat(path, *a, **k):
        result = original(path, *a, **k)
        if path == old or path == payload:
            values = {name: getattr(result, name) for name in dir(result) if name.startswith("st_")}
            values["st_ctime"] = time.time() if path == fresh else result.st_mtime
            return SimpleNamespace(**values)
        return result
    monkeypatch.setattr(Path, "stat", stat)
    cfg = cfg_for(tmp_path, staging_roots=["staging"])
    assert "recent" in hk.superseded_reason(old, old.parent, cfg, hk.Guard(cfg), time.time())


def test_recycle_keeps_etl_measurements(tmp_path):
    trace = write(tmp_path / "_CLAUDE_OUTPUT/task/trace.etl", b"measurement")
    cfg = cfg_for(tmp_path)
    assert "ETL" in hk.recycle_refusal(cfg, trace.parent)
    assert "ETL" in hk.recycle_refusal(cfg, trace)


@pytest.mark.parametrize("backup", [False, True])
@pytest.mark.parametrize("store", ["manual-installs", ".claude/backups/manual-installs"])
def test_manifests_follow_toolkit_and_parse_json_in_backups(tmp_path, monkeypatch, backup, store):
    toolkit = tmp_path / "toolkit"
    toolkit.mkdir()
    monkeypatch.setattr(hk, "TOOLKIT_ROOT", toolkit)
    source = tmp_path / "staging/Mod-1.0"
    manifest = toolkit / store / ("backups/rollback.json" if backup else "rollback.json")
    write(manifest, json.dumps({"rollback_sources": [str(source)]}).encode())
    refs = hk.manifest_references(cfg_for(tmp_path))
    assert hk._key(hk._real(source)) in refs


@pytest.mark.parametrize("kind", ["root", "directory", "file"])
def test_manifest_link_refusals_have_independent_witnesses(tmp_path, monkeypatch, kind):
    toolkit = tmp_path / "toolkit"
    root = toolkit / "manual-installs"
    leaf = write(root / "nested/rollback.json", b'{"rollback_sources":[]}')
    monkeypatch.setattr(hk, "TOOLKIT_ROOT", toolkit)
    blocked = {"root": root, "directory": leaf.parent, "file": leaf}[kind]
    monkeypatch.setattr(hk, "plain_path", lambda p: Path(p) != blocked)
    with pytest.raises(ValueError, match={"root": "plain directory", "directory": "directory is a link", "file": "manifest is a link"}[kind]):
        hk.manifest_references(cfg_for(tmp_path))


def staging_old(tmp_path, monkeypatch):
    old = tmp_path / "staging/Mod-1.0"
    write(old / "payload.txt", b"old", days=60)
    write(tmp_path / "staging/Mod-2.0/payload.txt", b"new", days=60)
    age(old, 60)
    # Historical fixture: birth and modification times both precede retention.
    if hasattr(hk, "staging_activity"):
        monkeypatch.setattr(hk, "staging_activity", lambda p: p.stat().st_mtime)
    cfg = cfg_for(tmp_path, staging_roots=["staging"])
    return old, cfg


def test_staging_link_clause_is_not_shadowed(tmp_path, monkeypatch):
    old, cfg = staging_old(tmp_path, monkeypatch)
    monkeypatch.setattr(hk, "plain_path", lambda p: Path(p) != old)
    assert hk.superseded_reason(old, old.parent, cfg, hk.Guard(cfg), time.time()) == "staging path is a link"


def test_unreadable_superseded_marker_refuses(tmp_path, monkeypatch):
    old, cfg = staging_old(tmp_path, monkeypatch)
    (old.parent / "_superseded").mkdir()
    assert hk.superseded_reason(old, old.parent, cfg, hk.Guard(cfg), time.time()) == "unreadable _superseded list"


@pytest.mark.parametrize("kind", ["drive", "home", "install", "toolkit", "mods"])
def test_staging_roots_refuse_unsafe_scopes(tmp_path, monkeypatch, kind):
    old, cfg = staging_old(tmp_path, monkeypatch)
    root = {"drive": Path(tmp_path.anchor), "home": Path.home(), "install": tmp_path,
            "toolkit": tmp_path / "toolkit", "mods": tmp_path / "MO2/mods"}[kind]
    monkeypatch.setattr(hk, "TOOLKIT_ROOT", tmp_path / "toolkit")
    cfg["staging_roots"] = [str(root)]
    candidate = root / "Mod-1.0"
    # Must refuse before inspecting any candidate in these real/unsafe locations.
    reason = hk.superseded_reason(candidate, root, cfg, hk.Guard(cfg), time.time())
    expected = {"drive": "drive or filesystem root", "home": "home folder",
                "install": "toolkit/install root or ancestor", "toolkit": "toolkit/install root or ancestor",
                "mods": "mod-manager mods folder"}[kind]
    assert reason == "unsafe staging root: " + expected


def test_derived_apply_oserror_is_a_refused_line(tmp_path, monkeypatch, capsys):
    trace = write(tmp_path / "_CLAUDE_OUTPUT/task/events.xml", b"decoded")
    write(trace.parent / "source.etl", b"measurement")
    cfg = cfg_for(tmp_path)
    doc = hk.plan(cfg, only={"derived"})
    assert planned(doc) == {key(trace)}
    def denied(*a):
        raise PermissionError("synthetic derived recheck failure")
    monkeypatch.setattr(hk, "derived_source", denied)
    recycled = []
    assert hk.apply(doc, cfg, recycler=lambda p: recycled.extend(p) or {}) == 1
    assert "REFUSED" in capsys.readouterr().out and recycled == []


def test_config_refuses_live_staging_root_before_any_scan(tmp_path):
    root = tmp_path / "MO2/mods"
    root.mkdir(parents=True)
    cfg = cfg_for(tmp_path, staging_roots=[str(root)])
    assert not planned(hk.plan(cfg, only={"superseded"}))
    assert "unsafe staging root" in hk.staging_root_refusal(root, cfg)


def test_relative_data_writes_absolute_manifest_rows(tmp_path, monkeypatch):
    mod = install_load()
    monkeypatch.chdir(tmp_path)
    data, records = tmp_path / "relative-data", tmp_path / "records"
    data.mkdir()
    archive = write(tmp_path / "mod.7z", b"synthetic archive")
    observed = []
    original = mod.plan_files
    def plan(entries, destination, *a, **k):
        observed.append(Path(destination))
        return original(entries, destination, *a, **k)
    monkeypatch.setattr(mod, "plan_files", plan)
    monkeypatch.setattr(mod.triage, "triage", lambda *a: {"error": None})
    monkeypatch.setattr(mod.triage, "show", lambda *a: None)
    monkeypatch.setattr(mod.triage, "listing", lambda *a: ([{"Path": "meshes/payload.nif"}], None))
    monkeypatch.setattr(mod.skyrim_paths, "find_7zip", lambda: ("synthetic-7z", "fixture"))
    assert mod.main([str(archive), "--data", "relative-data"]) == 0
    assert observed and observed[0].is_absolute()



def test_ci_docs_state_mutations_run_once_per_os():
    text = (Path(__file__).resolve().parents[1] / "docs/ci-workflow.md").read_text(encoding="utf-8")
    assert "mutations run once per OS" in text


def test_core_eol_crlf_cache_is_refused(tmp_path, monkeypatch):
    from test_resaver_plus_build import _load
    mod = _load()
    config = tmp_path / "empty-git-config"
    config.write_bytes(b"")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    cache = tmp_path / "cache"
    def git(*args):
        return subprocess.run(["git", *map(str, args)], capture_output=True, text=True, check=True).stdout.strip()
    git("init", cache)
    git("-C", cache, "config", "core.autocrlf", "false")
    (cache / ".gitattributes").write_bytes(b"*.txt text\n")
    (cache / "file.txt").write_bytes(b"original\n")
    git("-C", cache, "add", "--", ".gitattributes", "file.txt")
    git("-C", cache, "-c", "user.name=Synthetic", "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-m", "fixture")
    head = git("-C", cache, "rev-parse", "HEAD")
    git("-C", cache, "config", "core.eol", "crlf")
    (cache / "file.txt").rename(tmp_path / "original-checkout.txt")
    git("-C", cache, "checkout-index", "--force", "--all")
    assert (cache / "file.txt").read_bytes() == b"original\r\n"
    # The suspected bypass does not reproduce: the existing dirty check sees CRLF.
    with pytest.raises(mod.BuildError, match="local changes"):
        mod.fetch("synthetic-no-network", head, cache)
