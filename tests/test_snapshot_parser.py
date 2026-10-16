"""Parser refusal must be observed before any restore or backup can mask it."""
import io
import sys

import pytest
from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import cli, snapcli  # noqa: E402


@pytest.mark.parametrize("flag", ["--in", "--in-p", "--in-plac"])
def test_snapshot_parser_refuses_abbreviations_before_dispatch(tmp_path, monkeypatch, flag):
    project = str(tmp_path / "owned-project")
    snapshot_id = "20261005_120000-config"
    calls = []
    def restore(*args):
        calls.append(args)
        return 0
    monkeypatch.setattr(snapcli, "cmd_restore", restore)
    output = io.StringIO()
    args = ["snapshot", "restore", snapshot_id, flag, "--project", project]
    assert cli.main(args, out=output) == 2
    assert calls == [], "parser acceptance must not hide behind a later restore refusal"
    assert snapcli.USAGE in output.getvalue()

    # The complete spelling must reach this same dispatch. No filesystem or
    # backup operation runs: those belong to the separate integration checks.
    positive = io.StringIO()
    args[3] = "--in-place"
    assert cli.main(args, out=positive) == 0
    assert calls == [(project, snapshot_id, [], "", True, positive)]
    assert not positive.getvalue()
