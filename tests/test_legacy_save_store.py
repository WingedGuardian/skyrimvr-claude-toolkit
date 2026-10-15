import hashlib
import io
import json
from pathlib import Path

import pytest

from conftest import REPO
import sys
sys.path.insert(0, str(REPO / "tools"))
from skyguard import legacy_saves, save_store, snapcli


@pytest.fixture
def legacy(tmp_path):
    project = tmp_path / "game"
    root = legacy_saves.root(project)
    files = root / "2026-10-15" / "operation" / "0"
    files.mkdir(parents=True)
    originals = [str(tmp_path / "live" / ("A" + ext)) for ext in (".ess", ".skse")]
    row = {"id": "20261015-120000-abcd01-0", "originals": originals}
    for kind, original, data in zip(("ess", "skse"), originals, (b"old ESS", b"old SKSE")):
        stored = files / Path(original).name
        stored.write_bytes(data)
        row[kind] = {"original": original, "backup": str(stored), "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    (root / "manifest.jsonl").write_text(json.dumps(row) + "\n")
    return project, root, row


def test_legacy_pair_import_restore_keeps_old_store_and_both_members(legacy, tmp_path):
    project, root, row = legacy
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    output = io.StringIO()
    assert snapcli.cmd_restore(project, "local-" + row["id"], ["A.skse"], str(tmp_path / "out"), False, output) == 0
    assert sorted(p.name for p in (tmp_path / "out").rglob("*") if p.is_file()) == ["A.ess", "A.skse"]
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    assert save_store.rows(project)[0]


@pytest.mark.parametrize("defect", ["corrupt", "outside", "missing-authority", "duplicate"])
def test_invalid_legacy_evidence_never_writes_restored_output(legacy, tmp_path, defect):
    project, root, row = legacy
    if defect == "corrupt":
        Path(row["ess"]["backup"]).write_bytes(b"rot")
    elif defect == "outside":
        outside = tmp_path / "outside.ess"
        outside.write_bytes(b"old ESS")
        row["ess"]["backup"] = str(outside)
    elif defect == "missing-authority":
        row.pop("skse")
    text = json.dumps(row) + "\n"
    (root / "manifest.jsonl").write_text(text * (2 if defect == "duplicate" else 1))
    output = tmp_path / "out"
    assert snapcli.cmd_restore(project, "local-" + row["id"], [], str(output), False, io.StringIO()) == 2
    assert not output.exists()


def test_legacy_missing_counterpart_stays_explicit(legacy, tmp_path):
    project, root, row = legacy
    row["skse"] = None
    (root / "manifest.jsonl").write_text(json.dumps(row) + "\n")
    result = io.StringIO()
    assert snapcli.cmd_restore(project, "local-" + row["id"], [], str(tmp_path / "out"), False, result) == 0
    assert "A.skse did not exist" in result.getvalue()
    assert not list((tmp_path / "out").rglob("*.skse"))
