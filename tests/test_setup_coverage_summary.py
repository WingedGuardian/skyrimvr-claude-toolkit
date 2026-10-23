"""The actual setup summary must state the guard's scope and activation prerequisites."""
import pytest

from conftest import run_setup


@pytest.mark.parametrize("agent", ["claude", "all"])
def test_setup_summary_does_not_promise_active_hooks_or_universal_permission_prompts(game_dir, agent):
    result = run_setup(game_dir, args=("--agent", agent))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "The safety hooks are now active" not in result.stdout
    assert "Ask permission before editing any game file" not in result.stdout
    assert "Advise on ordinary game-file edits" in result.stdout
    assert "once your agent loads them" in result.stdout


def test_setup_summary_scopes_its_backups_and_script_snapshots(game_dir):
    result = run_setup(game_dir, args=("--agent", "claude"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Auto-backups (Edit/Write/NotebookEdit)" in result.stdout
    assert "shell/script writes are not backed up" in result.stdout
    assert "ordinary shell/script writes are not backed up by the edit hook" in result.stdout
    assert "one or two saves with verified permanent backups" in result.stdout
    assert "bound human approval for bulk save access" in result.stdout
    assert "Snapshot Papyrus files before non-read-only shell commands" in result.stdout
    assert "recognized raw writes to ESP/ESM/ESL/BSA/BA2" in result.stdout
