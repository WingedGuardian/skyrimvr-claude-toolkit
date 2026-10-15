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


@pytest.fixture
def recovery_legacy(tmp_path, monkeypatch):
    from skyguard import audit, recovery
    project = tmp_path / "game"
    project.mkdir()
    config = tmp_path / "config"
    saves = config / "Saves"
    saves.mkdir(parents=True)
    (project / "skyrim-paths.env").write_text("SKYRIM_CONFIG_DIR='" + str(config).replace('\\', '/') + "'\n")
    monkeypatch.setenv("SKYRIM_CONFIG_DIR", str(config))
    ess, skse = saves / "A.ess", saves / "A.skse"
    ess.write_bytes(b"legacy ESS")
    skse.write_bytes(b"legacy SKSE")
    sid, = recovery.backup_saves(project, audit.Audit(str(project)), [(str(ess), str(skse))], "private fixture")
    return project, recovery.store_dir(project), sid, ess, skse


def test_recovery_format_selecting_cosave_imports_and_restores_complete_pair(recovery_legacy, tmp_path):
    project, root, sid, ess, skse = recovery_legacy
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert snapcli.cmd_restore(project, sid, [skse.name], str(tmp_path / "out"), False, io.StringIO()) == 0
    assert {p.name: p.read_bytes() for p in (tmp_path / "out").rglob('*') if p.is_file()} == {
        "A.ess": b"legacy ESS", "A.skse": b"legacy SKSE"}
    assert {p: p.read_bytes() for p in root.rglob('*') if p.is_file()} == before


def test_legacy_writer_retains_both_pair_members(recovery_legacy):
    from skyguard import recovery
    project, root, sid, ess, skse = recovery_legacy
    assert set(recovery.resolve(root, sid)) == {str(ess), str(skse)}


def test_legacy_writer_copy_deadline_cleans_partial_candidate(recovery_legacy, monkeypatch):
    from skyguard import backup, recovery
    project, root, sid, ess, skse = recovery_legacy
    ess.write_bytes(b"changed save bytes over several chunks")
    ticks = iter(range(1000))
    monkeypatch.setattr(backup, "_clock", lambda: float(next(ticks)))
    monkeypatch.setattr(backup, "CHUNK", 4)
    with pytest.raises(recovery.SaveBackupFailed, match="did not finish"):
        recovery._store_verified(root / "blobs", str(ess), deadline=2.0)
    assert not list((root / "blobs").glob('.part-save-*'))


def test_recovery_format_in_place_preserves_complete_current_pair(recovery_legacy):
    from modding_harness import workspace
    project, root, sid, ess, skse = recovery_legacy
    ess.write_bytes(b"current ESS")
    skse.write_bytes(b"current SKSE")
    out = io.StringIO()
    assert snapcli.cmd_restore(project, sid, [ess.name], None, True, out) == 0, out.getvalue()
    assert ess.read_bytes() == b"legacy ESS" and skse.read_bytes() == b"legacy SKSE"
    store = save_store.store(project)
    manifests = workspace.summaries(store)[0]
    prebackup = next(m for m in manifests if m["purpose"] == "pre-mutation")
    assert {Path(r["path"]).name: (store.root / "blobs" / r["sha256"]).read_bytes()
            for group in prebackup["groups"] for r in group["members"]} == {
                "A.ess": b"current ESS", "A.skse": b"current SKSE"}


def test_recovery_format_in_place_obeys_local_restriction_before_current_preservation(recovery_legacy, monkeypatch):
    from modding_harness import contracts, workspace
    from skyguard import local_policy
    project, root, sid, ess, skse = recovery_legacy
    ess.write_bytes(b"current ESS")
    monkeypatch.setattr(local_policy, "restriction", lambda *args: lambda action:
                        contracts.Result("deny", "evaluated", reason="private local restriction"))
    out = io.StringIO()
    assert snapcli.cmd_restore(project, sid, [], None, True, out) == 2
    assert "private local restriction" in out.getvalue() and ess.read_bytes() == b"current ESS"
    assert all(m["purpose"] == "recovery-import" for m in workspace.summaries(save_store.store(project))[0])


def test_recovery_format_missing_counterpart_is_reported_and_new_live_counterpart_refuses(recovery_legacy, tmp_path):
    from skyguard import recovery
    project, root, sid, ess, skse = recovery_legacy
    row = next(row for row in recovery.read_manifest(root)[0] if row["src"] == str(ess))
    row.update(lines=1, state=[str(ess)], missing=[str(skse)])
    (root / recovery.MANIFEST).write_text(json.dumps(row) + '\n')
    out = io.StringIO()
    assert snapcli.cmd_restore(project, sid, [], str(tmp_path / "out"), False, out) == 0
    assert "A.skse did not exist" in out.getvalue() and not list((tmp_path / "out").rglob('*.skse'))
    ess.write_bytes(b"current ESS")
    refused = io.StringIO()
    assert snapcli.cmd_restore(project, sid, [], None, True, refused) == 2
    assert "unexpected counterpart" in refused.getvalue()
    assert ess.read_bytes() == b"current ESS" and skse.read_bytes() == b"legacy SKSE"


@pytest.mark.parametrize("defect", ["duplicate", "corrupt", "omitted-counterpart", "contradictory-missing"])
def test_recovery_format_bad_authority_writes_no_restored_output(recovery_legacy, tmp_path, defect):
    from skyguard import recovery
    project, root, sid, ess, skse = recovery_legacy
    rows = recovery.read_manifest(root)[0]
    if defect == "duplicate":
        rows.append(rows[0])
    elif defect == "corrupt":
        (root / "blobs" / rows[0]["sha256"]).write_bytes(b"rot")
    elif defect == "omitted-counterpart":
        rows = rows[:1]
        rows[0].update(lines=1, state=[rows[0]["src"]], missing=[])
    else:
        rows[0]["missing"] = [rows[1]["src"]]
    (root / recovery.MANIFEST).write_text(''.join(json.dumps(row) + '\n' for row in rows))
    assert snapcli.cmd_restore(project, sid, [], str(tmp_path / "out"), False, io.StringIO()) == 2
    assert not (tmp_path / "out").exists()
