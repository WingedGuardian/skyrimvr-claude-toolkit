"""P6 retained CONFIG-SAME probe, including an actual rollback of owned synthetic files."""
import hashlib
import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import REPO


def load():
    spec = importlib.util.spec_from_file_location("review_install", REPO / "tools/mod-install.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("slot", [0, 1, 2])
def test_kept_identical_config_survives_the_documented_uninstall(tmp_path, monkeypatch, slot):
    mod = load()
    data, records = tmp_path / "invented-data", tmp_path / "records"
    data.mkdir()
    config, replaced, added = data / "Example.ini", data / "old.txt", data / "new.txt"
    original = b"[example]\nvalue=1\n"
    config.write_bytes(original)
    replaced.write_bytes(b"original replaced payload")
    payloads = {"Example.ini": original, "old.txt": b"replacement", "new.txt": b"added payload"}
    plan = [("old.txt", replaced, "OVERWRITE", []), ("new.txt", added, "NEW", [])]
    plan.insert(slot, ("Example.ini", config, "CONFIG-NEW", []))
    def extract(argv, **kwargs):
        out = Path(next(a[2:] for a in argv if a.startswith("-o")))
        out.mkdir(parents=True)
        for name, contents in payloads.items():
            (out / name).write_bytes(contents)
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")
    monkeypatch.setattr(mod, "records_dir", lambda: records)
    monkeypatch.setattr(mod.subprocess, "run", extract)
    recorded, atomic_record = [], mod.atomic_record
    def capture(target, text):
        recorded.append(text)
        return atomic_record(target, text)
    monkeypatch.setattr(mod, "atomic_record", capture)
    assert mod.install("synthetic-extractor", "invented-archive", "Example", "(root)", data, plan) == 0
    assert config.read_bytes() == original and added.read_bytes() == payloads["new.txt"]
    assert replaced.read_bytes() == payloads["old.txt"]
    manifest = (records / "Example.files.txt").read_text(encoding="utf-8")
    operation = next((records / "backups").iterdir())
    assert (operation / "manifest.files.txt").read_text(encoding="utf-8") == manifest
    # Execute the documented rollback: remove the ordinary manifest file rows,
    # then restore backed-up originals. Every path is owned scratch from this test.
    removed = []
    for row in manifest.splitlines():
        if row and not row.startswith("#"):
            _, name = row.split("  ", 1)
            target = Path(name)
            target.relative_to(data)
            target.unlink()
            removed.append(target)
    assert added in removed and replaced in removed
    saved = operation / mod.backup_rel(replaced)
    shutil.copy2(saved, replaced)
    assert replaced.read_bytes() == b"original replaced payload" and not added.exists()
    assert config.is_file(), "rollback removed a config this installation never wrote or backed up"
    assert config.read_bytes() == original
    assert not config.with_suffix(".ini.new").exists()
    assert config not in removed
    assert "# KEPT: " + hashlib.sha256(original).hexdigest() + "  " + str(config) in manifest
    assert "# STATUS: complete, 2 file(s), 0 verify mismatch(es)" in manifest
    for snapshot in recorded:
        if "# KEPT:" not in snapshot:
            continue
        rows = snapshot.splitlines()
        assert "PLANNED  " + str(config) + ".new" not in rows
        written = {row.split("  ", 1)[1] for row in rows if row and not row.startswith(("#", "PLANNED"))}
        pending = {row[9:] for row in rows if row.startswith("PLANNED  ")}
        assert not written & pending, "a completed write remained in the recovery plan"
