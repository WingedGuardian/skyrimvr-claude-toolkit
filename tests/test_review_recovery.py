"""A2 retained probe: synthetic manifest/blob only, retirement is recorded but never performed."""
import hashlib
import json
import sys
import time
from pathlib import Path

import pytest
from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import recovery
from test_recovery_snapshots import inst, put


@pytest.fixture
def store(tmp_path, monkeypatch):
    body = b"synthetic bytes, not a save"
    blob = hashlib.sha256(body).hexdigest()
    (tmp_path / "blobs").mkdir()
    (tmp_path / "blobs" / blob).write_bytes(body)
    entry = dict(v=1, ts="20261001_000000", t=1, src="invented-identity", dest=blob, sha256=blob,
                 kind="save", snap="20261001_000000-save", size=len(body), lines=1, state=["invented-identity"])
    manifest = tmp_path / recovery.MANIFEST
    manifest.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    selected, audit = [], []
    class Batch:
        def __init__(self, *args):
            pass
        def retire(self, path):
            selected.append(Path(path).name)
            return True
    class Audit:
        def log(self, *args):
            audit.append(args)
    monkeypatch.setattr(recovery.retire, "Batch", Batch)
    return tmp_path, manifest, blob, selected, audit, Audit()


@pytest.mark.parametrize("failure", ["unreadable", "disappeared", "emptied"])
def test_manifest_authority_must_remain_readable_under_the_prune_lock(store, monkeypatch, failure):
    root, manifest, blob, selected, audit, logger = store
    assert recovery.prune(root, time.time(), logger) == 0 and selected == []
    original = Path.read_bytes
    def read(path):
        if path == manifest:
            if failure == "emptied":
                return b""
            error = PermissionError if failure == "unreadable" else FileNotFoundError
            raise error("synthetic authority read failure")
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", read)
    assert recovery.prune(root, time.time(), logger) == 0
    assert selected == [] and (root / "blobs" / blob).exists()
    assert any(row[1] == "PRUNE-SKIPPED" for row in audit)


def test_unreadable_manifest_is_not_an_empty_snapshot_list(store, monkeypatch):
    root, manifest, *_ = store
    original = Path.read_bytes
    def read(path):
        if path == manifest:
            raise PermissionError("synthetic unreadable manifest")
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(OSError):
        recovery.read_manifest(root)


def test_absent_manifest_is_empty_for_a_new_store(tmp_path):
    assert recovery.read_manifest(tmp_path) == ([], 0)


def test_unreadable_authority_stops_config_snapshot_without_blocking_the_hook(inst, monkeypatch):
    old = time.time() - 3600
    inst.run("first synthetic command", now=old)
    manifest = inst.store / recovery.MANIFEST
    before = manifest.read_bytes()
    put(inst.lo / "plugins.txt", b"*Changed.esp\n")
    original = Path.read_bytes
    def read(path):
        if path == manifest:
            raise PermissionError("synthetic inaccessible recovery authority")
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", read)
    inst.run("second synthetic command", now=time.time())
    assert original(manifest) == before
    assert "could not read the recovery authority" in inst.log()
