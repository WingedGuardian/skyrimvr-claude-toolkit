"""The retained edit-backup API enforces its own save reserve before publishing bytes.

Snapshot save restores use the neutral preservation store now. These owned byte
fixtures exercise backup.take directly, without relying on that separate route.
"""
import hashlib
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, backup, recovery  # noqa: E402


def _free(monkeypatch, room):
    real = backup.shutil.disk_usage
    monkeypatch.setattr(backup.shutil, "disk_usage",
                        lambda path: real(path)._replace(free=recovery.SAVE_RESERVE_BYTES + room))


@pytest.mark.parametrize("suffix", ["ess", "skse"])
def test_edit_backup_refuses_a_save_below_reserve(tmp_path, monkeypatch, suffix):
    source = tmp_path / ("Owned." + suffix)
    data = b"owned synthetic save member"
    source.write_bytes(data)
    _free(monkeypatch, len(data) - 1)
    project = tmp_path / "toolkit"
    assert backup.take(str(project), "Edit", str(source), audit.Audit(str(project))) is None
    folder = project / ".claude" / "backups"
    assert not (folder / backup.MANIFEST).exists()
    assert not any("__" in path.name or ".part-" in path.name for path in folder.iterdir())
    assert "would leave under" in (folder / "AUDIT_LOG.txt").read_text(encoding="utf-8")
    assert source.read_bytes() == data


@pytest.mark.parametrize("suffix", ["ess", "skse"])
def test_edit_backup_accepts_a_save_at_the_reserve_boundary(tmp_path, monkeypatch, suffix):
    source = tmp_path / ("Owned." + suffix)
    data = b"owned synthetic save member"
    source.write_bytes(data)
    _free(monkeypatch, len(data))
    project = tmp_path / "toolkit"
    saved = backup.take(str(project), "Edit", str(source), audit.Audit(str(project)))
    assert saved is not None and saved.read_bytes() == data
    entries, bad = backup.read_manifest(saved.parent)
    assert bad == 0 and len(entries) == 1
    assert entries[saved.name]["sha256"] == hashlib.sha256(data).hexdigest()
    assert entries[saved.name]["src"] == str(source)
    assert source.read_bytes() == data


def test_edit_backup_keeps_ordinary_files_below_the_save_reserve(tmp_path, monkeypatch):
    source = tmp_path / "Owned.txt"
    data = b"ordinary configuration fixture"
    source.write_bytes(data)
    _free(monkeypatch, -1)
    project = tmp_path / "toolkit"
    saved = backup.take(str(project), "Edit", str(source), audit.Audit(str(project)))
    assert saved is not None and saved.read_bytes() == data
    assert source.read_bytes() == data
