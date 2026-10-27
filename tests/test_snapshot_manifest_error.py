"""Unreadable recovery metadata is a CLI refusal; synthetic manifest only."""
from pathlib import Path

import pytest

from test_snapshot_restore_cli import inst, cli_run, put
from skyguard import recovery


@pytest.mark.parametrize("action", ["list", "restore"])
@pytest.mark.parametrize("error_type", [PermissionError, OSError])
def test_snapshot_manifest_read_error_is_one_line_without_a_traceback(inst, monkeypatch, action, error_type):
    manifest = inst.store / recovery.MANIFEST
    put(manifest, b"synthetic unreadable metadata\n")
    original = Path.read_bytes

    def unreadable(path):
        if path == manifest:
            raise error_type("synthetic failure\ndetail")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    args = ("list", "--kind", "config") if action == "list" else ("restore", "20261001_000000-config")
    rc, output = cli_run(inst, *args)
    assert rc == 2
    assert "Traceback" not in output
    assert output.splitlines()[-1].endswith("synthetic failure detail")
    assert not (inst.game / ".claude/backups/restored").exists()
