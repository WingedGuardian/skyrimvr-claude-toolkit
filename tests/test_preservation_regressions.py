"""Regressions reproduced during the cross-toolkit 4.0 due-diligence review."""
import io
import os
from pathlib import Path

import pytest

from test_recovery_snapshots import Install, put
from skyguard import backup, engine, recovery, snapcli, snapshot
from skyguard import save_store, shell_rules


@pytest.fixture
def inst(tmp_path):
    result = Install(tmp_path)
    put(result.game / "skyrim-paths.env", (
        "SKYRIM_CONFIG_DIR='{}'\nSKYRIM_LOADORDER_DIR='{}'\n".format(result.cfg.as_posix(), result.lo.as_posix())
    ).encode())
    return result


def take(inst):
    return recovery.backup_saves(str(inst.game), inst.audit,
                                 [(str(inst.saves / "Quick.ess"), str(inst.saves / "Quick.skse"))], "test")[0]


def test_complete_pair_revalidated_after_all_members_copied(inst, monkeypatch):
    original = recovery._store_verified
    ess = inst.saves / "Quick.ess"
    before = ess.stat()

    def race(blobs, source, deadline):
        result = original(blobs, source, deadline)
        if source.endswith(".skse"):
            put(ess, b"x" * before.st_size)
            os.utime(ess, ns=(before.st_atime_ns, before.st_mtime_ns))
        return result

    monkeypatch.setattr(recovery, "_store_verified", race)
    with pytest.raises(recovery.SaveBackupFailed, match="changed"):
        take(inst)
    assert not recovery.snapshots(inst.store)


def test_short_manifest_write_is_completed_before_success(inst, monkeypatch):
    original = os.write
    monkeypatch.setattr(os, "write", lambda fd, data: original(fd, data[:max(1, len(data) // 2)]))
    sid = take(inst)
    assert len(recovery.resolve(inst.store, sid)) == 2


def test_index_failure_cannot_reuse_save_id(inst, monkeypatch):
    monkeypatch.setattr(recovery.snapshot, "_write_replace", lambda *args: (_ for _ in ()).throw(OSError("index")))
    monkeypatch.setattr(inst.audit, "stamp", lambda: "20261014_120000")
    first, second = take(inst), take(inst)
    assert first != second
    assert len(recovery.resolve(inst.store, first)) == len(recovery.resolve(inst.store, second)) == 2


@pytest.mark.parametrize("relative", [
    ".claude/backups/recovery_snapshots/blobs/abc", ".claude/backups/save-backups/manifest.jsonl",
    ".modding-agent-harness/backups/saves/transactions/abc.json",
])
def test_backup_store_write_cannot_pass_the_whitelist(inst, relative):
    verdict = engine.evaluate_write(str(inst.game / relative), inst.audit, inst.roots, str(inst.game))
    assert verdict.decision == "deny"


def test_configured_external_save_store_and_its_ancestor_are_protected(inst, tmp_path):
    import json
    destination = tmp_path / "permanent" / "saves"
    put(inst.game / ".modding-agent-harness/config.json",
        json.dumps({"v": 1, "save_store": str(destination)}).encode())
    verdict = engine.evaluate_write(str(destination / "blobs" / "file"), inst.audit, inst.roots, str(inst.game))
    assert verdict.decision == "deny"
    verdict = shell_rules.decide('rm -r "{}"'.format(destination.parent), inst.roots, inst.audit, cwd=str(inst.game))
    assert verdict.decision == "deny"


def test_both_legacy_collectors_leave_neutral_permanent_blobs_alone(inst):
    target = save_store.store(inst.game)
    record = target.backup([[(inst.saves / "Quick.ess"), (inst.saves / "Quick.skse")]])
    for collector in (snapshot.prune, recovery.prune):
        assert collector(target.root, 10**10, inst.audit) == 0
    assert target.load(record["id"])["complete"]


def test_restore_member_selection_never_creates_mixed_pair(inst):
    sid = take(inst)
    put(inst.saves / "Quick.ess", b"ESS-B")
    put(inst.saves / "Quick.skse", b"SKSE-B")
    output = io.StringIO()
    assert snapcli.cmd_restore(str(inst.game), sid, ["Quick.ess"], "", True, output) == 0, output.getvalue()
    assert (inst.saves / "Quick.ess").read_bytes() == b"quick save bytes"
    assert (inst.saves / "Quick.skse").read_bytes() == b"quick co-save"
    prebackup = recovery.snapshots(inst.store)[-1]
    assert {Path(e["src"]).suffix for e in prebackup["files"]} == {".ess", ".skse"}


def test_restore_refuses_a_counterpart_absent_from_backup(inst):
    (inst.saves / "Quick.skse").unlink()
    sid = take(inst)
    put(inst.saves / "Quick.ess", b"ESS-B")
    put(inst.saves / "Quick.skse", b"SKSE-B")
    output = io.StringIO()
    assert snapcli.cmd_restore(str(inst.game), sid, [], "", True, output) == 2
    assert "unexpected counterpart at restore destination" in output.getvalue()
    assert (inst.saves / "Quick.ess").read_bytes() == b"ESS-B"
    assert (inst.saves / "Quick.skse").read_bytes() == b"SKSE-B"


def test_new_directory_restore_never_clobbers_a_concurrent_file(inst, tmp_path, monkeypatch):
    sid = take(inst)
    state = recovery.resolve(inst.store, sid)
    sha = next(iter(state.values()))["sha256"]
    target = tmp_path / "restore/Quick.ess"
    original = backup._sha256

    def race(path):
        result = original(path)
        if ".restore-part-" in str(path):
            put(target, b"another process")
        return result

    monkeypatch.setattr(backup, "_sha256", race)
    with pytest.raises(OSError):
        snapshot.write_blobs(inst.store, [(target, sha)])
    assert target.read_bytes() == b"another process"
